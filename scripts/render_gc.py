#!/usr/bin/env python3
"""render_gc.py — вывоз рендеров из дерева дел в кеш. Обратимо, с манифестом.

Зачем. Страницы, отрисованные из первички под OCR, — производный кеш: они
восстанавливаются из PDF за секунды. Живя внутри `cases/`, они занимают место
в дереве дел, уезжают в бэкап доказательств наравне с ними и путают глаз
(замер 19.08.2026: 487 картинок кухни против 150 картинок первички).

Первичка (`00_intake/`) НЕ ТРОГАЕТСЯ ни в одном режиме: там картинка и есть
доказательство. Это инвариант, а не настройка.

    --dry-run КОРЕНЬ             показать, что будет вывезено; диск не меняется
    --move КОРЕНЬ --manifest F   вывезти в кеш, записать манифест отката
    --restore F                  вернуть по манифесту, побайтово
    --selftest                   проверка без сети

Вывозится в ~/.cache/legal_extract/case_renders/<путь относительно корня>.
Манифест обязателен: перенос без записи отката — необратимое действие над
данными дел, а такие делаются только с возможностью вернуть.

# ponytail: сайдкары .txt остаются на месте — читатели работают с текстом.
# Облачный фолбэк vision по конкретной странице после вывоза требует повторного
# рендера из первички (markdown_extract --render-dir /tmp/...), это дёшево.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

RASTER = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}
INTAKE = "00_intake"          # неприкосновенная первичка
CACHE = Path(os.path.expanduser("~/.cache/legal_extract/case_renders"))


def _service(p: Path, root: Path) -> bool:
    """cases/_assets, cases/_templates, cases/_logs — хозяйство системы, не дело.
    Подпись владельца (cases/_assets/подпись.png) вывозу не подлежит: её читает
    sign_and_pdf.py, и вывоз молча сломал бы подписание документов."""
    try:
        first = p.relative_to(root).parts[0]
    except ValueError:
        return False
    return first.startswith("_")


def find_renders(root: Path) -> list:
    """Растровые файлы под корнем, кроме первички. Симлинки не трогаем: цель
    может лежать где угодно, а перенос ссылки выглядит как перенос файла."""
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        if INTAKE in p.parts or _service(p, root):
            continue
        if p.suffix.lower().lstrip(".") in RASTER:
            out.append(p)
    return out


def do_move(root: Path, manifest: Path, cache: Path = CACHE) -> int:
    files = find_renders(root)
    if not files:
        print("вывозить нечего: растра вне первички нет")
        return 0
    records, moved_bytes = [], 0
    for src in files:
        rel = src.relative_to(root)
        dst = cache / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        i = 1
        while dst.exists():                      # тёзка из прошлого вывоза не затирается
            dst = dst.with_name(f"{dst.stem}__{i}{dst.suffix}")
            i += 1
        size = src.stat().st_size
        shutil.move(str(src), str(dst))
        records.append({"src": str(src), "dst": str(dst), "bytes": size})
        moved_bytes += size
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"root": str(root), "cache": str(cache),
                                    "files": records}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"вывезено файлов: {len(records)} ({moved_bytes / 1024 / 1024:.1f} МБ) → {cache}")
    print(f"манифест отката: {manifest}")
    return 0


def do_restore(manifest: Path) -> int:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"ERROR: манифест не прочитан: {e}", file=sys.stderr)
        return 1
    back, missing, busy = 0, [], []
    for rec in data.get("files", []):
        src, dst = Path(rec["src"]), Path(rec["dst"])
        if not dst.is_file():
            missing.append(str(dst))
            continue
        if src.exists():                          # на месте что-то уже есть — не затирать
            busy.append(str(src))
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        back += 1
    print(f"возвращено файлов: {back}")
    if missing:
        print(f"нет в кеше: {len(missing)} — {missing[:3]}", file=sys.stderr)
    if busy:
        print(f"место занято, пропущено: {len(busy)} — {busy[:3]}", file=sys.stderr)
    return 1 if (missing or busy) else 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        root = td / "cases" / "ivanov-ivan" / "delo-2026"
        work = root / ".agent" / "context" / "_working" / "ocr"
        intake = root / INTAKE
        work.mkdir(parents=True)
        intake.mkdir(parents=True)
        (work / "page_001.png").write_bytes(b"R1")
        (work / "page_002.PNG").write_bytes(b"R2")       # регистр расширения не спасает
        (work / "page_001.txt").write_text("текст", encoding="utf-8")
        (intake / "dokazatelstvo.png").write_bytes(b"PERV")
        assets = td / "cases" / "_assets"
        assets.mkdir(parents=True)
        (assets / "podpis.png").write_bytes(b"PODPIS")
        cache = td / "cache"
        man = td / "man.json"

        found = find_renders(td / "cases")
        assert len(found) == 2, f"найдено {len(found)}, ждали 2 (первичка не в счёт)"
        assert do_move(td / "cases", man, cache) == 0
        assert not (work / "page_001.png").exists(), "рендер остался на месте"
        assert (work / "page_001.txt").is_file(), "сайдкар .txt пострадал"
        assert (intake / "dokazatelstvo.png").read_bytes() == b"PERV", "первичка тронута"
        assert man.is_file(), "манифест не записан"
        assert (assets / "podpis.png").read_bytes() == b"PODPIS", "служебный _assets тронут"

        assert do_restore(man) == 0
        assert (work / "page_001.png").read_bytes() == b"R1", "откат не побайтовый"
        assert (work / "page_002.PNG").read_bytes() == b"R2", "откат не побайтовый"

        # Занятое место не затирается: откат не должен уничтожать новую работу.
        assert do_move(td / "cases", man, cache) == 0
        (work / "page_001.png").write_bytes(b"NOVOE")
        assert do_restore(man) == 1, "занятое место обязано дать ненулевой код"
        assert (work / "page_001.png").read_bytes() == b"NOVOE", "затёрли занятое место"

        # Симлинк не переносится: за ним может стоять что угодно вне дерева.
        (work / "ssylka.png").symlink_to(intake / "dokazatelstvo.png")
        assert all("ssylka" not in str(p) for p in find_renders(td / "cases")), "симлинк взят в вывоз"
    print("selftest пройден: первичка цела, откат побайтовый, симлинк и занятое место не тронуты")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Вывоз рендеров из дерева дел в кеш (обратимо).")
    ap.add_argument("--dry-run", metavar="КОРЕНЬ", help="показать, что будет вывезено")
    ap.add_argument("--move", metavar="КОРЕНЬ", help="вывезти в кеш (нужен --manifest)")
    ap.add_argument("--restore", metavar="МАНИФЕСТ", help="вернуть по манифесту")
    ap.add_argument("--manifest", help="файл манифеста отката")
    ap.add_argument("--cache", default=str(CACHE), help=f"корень кеша (по умолчанию {CACHE})")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.dry_run:
        root = Path(a.dry_run).resolve()
        files = find_renders(root)
        total = sum(p.stat().st_size for p in files)
        print(f"найдено рендеров вне первички: {len(files)} ({total / 1024 / 1024:.1f} МБ)")
        for p in files[:10]:
            print("  " + str(p.relative_to(root)))
        if len(files) > 10:
            print(f"  … ещё {len(files) - 10}")
        print("диск не изменён. Вывезти: --move КОРЕНЬ --manifest ФАЙЛ")
        return 0
    if a.move:
        if not a.manifest:
            print("ERROR: --move без --manifest запрещён: перенос без записи отката "
                  "необратим", file=sys.stderr)
            return 1
        return do_move(Path(a.move).resolve(), Path(a.manifest), Path(a.cache))
    if a.restore:
        return do_restore(Path(a.restore))
    ap.error("нужен --dry-run, --move, --restore или --selftest")


if __name__ == "__main__":
    sys.exit(main())
