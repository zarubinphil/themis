#!/usr/bin/env python3
"""case_code_gc.py — вывоз файлов кода из-под cases/ в карантин. Обратимо, с манифестом.

Зачем. Под `cases/` исторически лежат 84 файла кода (.py/.sh — генераторы и
служебные скрипты прошлых сессий) и 28 мертвых `practice_context.md` (шаг 4.5
снят). Код в дереве дел — не доказательство: он уезжает в бэкап первички
наравне с ней и путает глаз. Вывоз — владельцу (касание cases/ заморожено
для автономного цикла), прибор лишь делает его безопасным: план → перенос
по манифесту → откат побайтово.

Первичка (`00_intake/`) НЕ ТРОГАЕТСЯ ни в одном режиме. Служебные папки
(`_templates`, `_logs`, `_assets`, прочие `_*`) и `.agent/` — тоже.
Симлинки пропускаются: цель может лежать где угодно.

    --plan                  показать, что уедет; диск не меняется
    --apply                 вывезти в карантин, записать манифест отката
    --practice-context      добавить в план practice_context.md (по умолчанию нет)
    --undo МАНИФЕСТ         вернуть все по манифесту на прежние места
    --selftest              проверка на временном дереве, cases/ не затрагивается

Карантин: `<родитель cases>/cases_quarantine/<путь относительно cases>`.
Манифест: `cases/.agent/archive/case_code_gc_<метка времени>.json`.
Манифест обязателен: перенос без записи отката — необратимое действие над
данными дел, а такие делаются только с возможностью вернуть.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_guard

# Перечень расширений кода — ОДИН на проект. Сторож (claude_guard) не пускает эти
# файлы под cases/, а уборщик обязан видеть уже лежащие в дереве: две копии списка
# круг за кругом давали полузакрытые дыры (.php/.lua/.go/.rs/.bat сторож блокировал,
# уборщик к вывозу не брал). Импортируем эталон, а не переписываем — с ведущей точкой,
# как отдает Path.suffix.
CODE_EXT = {"." + e for e in claude_guard.CODE_EXT}
PRACTICE_CONTEXT = "practice_context.md"
INTAKE = "00_intake"          # неприкосновенная первичка


def _service(p: Path, root: Path) -> bool:
    """cases/_templates, cases/_logs и прочие _* — хозяйство системы, не вывоз."""
    try:
        first = p.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return False
    return first.startswith("_")


def find_targets(root: Path, practice_context: bool = False) -> list[Path]:
    """Файлы кода (и опционально practice_context.md) под корнем, кроме первички,
    служебных _* и .agent/. Симлинки не трогаем."""
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        if INTAKE in p.parts or ".agent" in p.parts or _service(p, root):
            continue
        if p.suffix.lower() in CODE_EXT:
            out.append(p)
        elif practice_context and p.name == PRACTICE_CONTEXT:
            out.append(p)
    return out


def _manifest_dir(root: Path) -> Path:
    return root / ".agent" / "archive"


def do_plan(root: Path, practice_context: bool) -> int:
    files = find_targets(root, practice_context)
    if not files:
        print("вывозить нечего: кода вне первички и служебных папок нет")
        return 0
    total = sum(p.stat().st_size for p in files)
    for p in files:
        print(f"  {p.relative_to(root)}  ({p.stat().st_size} б)")
    print(f"к вывозу: {len(files)} файлов, {total} б. "
          f"Диск не менялся — перенос: --apply")
    return 0


def do_apply(root: Path, practice_context: bool) -> int:
    files = find_targets(root, practice_context)
    if not files:
        print("вывозить нечего: кода вне первички и служебных папок нет")
        return 0
    quarantine = root.parent / "cases_quarantine"
    records = []
    for src in files:
        rel = src.relative_to(root)
        dst = quarantine / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        i = 1
        while dst.exists():                  # тезка из прошлого вывоза не затирается
            dst = dst.with_name(f"{dst.stem}__{i}{dst.suffix}")
            i += 1
        shutil.move(str(src), str(dst))
        records.append({"src": str(src), "dst": str(dst),
                        "bytes": dst.stat().st_size})
    mdir = _manifest_dir(root)
    mdir.mkdir(parents=True, exist_ok=True)
    mpath = mdir / f"case_code_gc_{time.strftime('%Y%m%d-%H%M%S')}.json"
    mpath.write_text(json.dumps(
        {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "root": str(root),
         "quarantine": str(quarantine), "records": records},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"вывезено: {len(records)} файлов → {quarantine}")
    print(f"манифест отката: {mpath}")
    print(f"откат: python3 {Path(__file__).name} --undo {mpath}")
    return 0


def do_undo(manifest: Path) -> int:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    records = data.get("records", [])
    if not records:
        print("манифест пуст — возвращать нечего")
        return 0
    restored, problems = 0, []
    for rec in records:
        src, dst = Path(rec["src"]), Path(rec["dst"])
        if not dst.is_file():
            problems.append(f"нет в карантине: {dst}")
            continue
        if src.exists():
            problems.append(f"место занято, не затираю: {src}")
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        restored += 1
    print(f"возвращено: {restored} из {len(records)}")
    for prob in problems:
        print(f"  ВНИМАНИЕ: {prob}")
    return 1 if problems else 0


def selftest() -> int:
    """Временное дерево вместо cases/: план ничего не меняет, apply вывозит
    только цели, undo возвращает побайтово. Настоящий cases/ не затрагивается."""
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    with tempfile.TemporaryDirectory(prefix="case-code-gc-") as tmp:
        root = Path(tmp) / "cases"
        case = root / "testfam-ab" / "test-delo"
        (case / INTAKE).mkdir(parents=True)
        (root / "_templates").mkdir()
        # Цели: код и practice_context.md
        code = case / "gen_doc.py"
        code.write_text("print('генератор')\n", encoding="utf-8")
        pc = case / ".agent" / "context" / PRACTICE_CONTEXT
        # practice_context в .agent не вывозится никогда — кладем боевой вариант
        pc.parent.mkdir(parents=True)
        pc.write_text("служебный\n", encoding="utf-8")
        pc2 = case / "01_context" / PRACTICE_CONTEXT
        pc2.parent.mkdir(parents=True)
        pc2.write_text("мертвый шаг 4.5\n", encoding="utf-8")
        # Нецели: первичка, шаблоны, документ, .agent
        intake_py = case / INTAKE / "scan_notes.py"
        intake_py.write_text("# заметка клиента\n", encoding="utf-8")
        tpl = root / "_templates" / "build.py"
        tpl.write_text("# шаблон\n", encoding="utf-8")
        docx = case / "02_hearings" / "doc.docx"
        docx.parent.mkdir(parents=True)
        docx.write_bytes(b"PK\x03\x04")
        agent_py = case / ".agent" / "context" / "helper.py"
        agent_py.write_text("# рантайм агента\n", encoding="utf-8")

        before = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                        if p.is_file())

        # 1. --plan ничего не меняет
        targets = find_targets(root, practice_context=False)
        check([p.name for p in targets] == ["gen_doc.py"],
              f"plan без флага: цели {[p.name for p in targets]}, ожидался gen_doc.py")
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                       if p.is_file())
        check(before == after, "план изменил диск")

        # 2. --apply вывозит только цели, пишет манифест
        check(do_apply(root, practice_context=True) == 0, "apply вернул ошибку")
        check(not code.exists(), "код остался на месте после apply")
        check(not pc2.exists(), "practice_context.md остался на месте после apply")
        check((root.parent / "cases_quarantine" / "testfam-ab" / "test-delo"
               / "gen_doc.py").is_file(), "код не в карантине")
        check(intake_py.is_file(), "apply тронул первичку 00_intake")
        check(tpl.is_file(), "apply тронул служебную _templates")
        check(agent_py.is_file(), "apply тронул .agent")
        check(pc.is_file(), "apply тронул practice_context внутри .agent")
        check(docx.is_file(), "apply тронул документ")
        manifests = list(_manifest_dir(root).glob("case_code_gc_*.json"))
        check(len(manifests) == 1, f"манифестов {len(manifests)}, ожидался 1")
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        check(len(data["records"]) == 2,
              f"в манифесте {len(data['records'])} записей, ожидалось 2")

        # 3. --undo возвращает побайтово
        check(do_undo(manifests[0]) == 0, "undo вернул ошибку")
        check(code.is_file() and code.read_text(encoding="utf-8") == "print('генератор')\n",
              "код не восстановлен побайтово")
        check(pc2.is_file(), "practice_context.md не восстановлен")
        after_undo = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                            if p.is_file())
        check(set(after_undo) == set(before) | {
            str(manifests[0].relative_to(root))},
              "после undo дерево отличается от исходного (кроме манифеста)")

    if fails:
        for f in fails:
            print(f"SELTEST FAIL: {f}")
        return 1
    print("selftest: план инертен, apply вывозит только код, undo возвращает "
          "побайтово, первичка/служебные/.agent не тронуты — OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Вывоз файлов кода из-под cases/ в карантин (обратимо).")
    ap.add_argument("--root", default="cases",
                    help="корень дерева дел (по умолчанию cases)")
    ap.add_argument("--plan", action="store_true", help="показать план, диск не меняется")
    ap.add_argument("--apply", action="store_true", help="вывезти по манифесту")
    ap.add_argument("--practice-context", action="store_true",
                    help="добавить practice_context.md в план вывоза")
    ap.add_argument("--undo", metavar="МАНИФЕСТ", help="вернуть все по манифесту")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.undo:
        m = Path(args.undo)
        if not m.is_file():
            print(f"манифест не найден: {m}")
            return 1
        return do_undo(m)
    root = Path(args.root)
    if not root.is_dir():
        print(f"корень не найден: {root}")
        return 1
    if args.apply:
        return do_apply(root, args.practice_context)
    # по умолчанию — план: перенос только явным --apply
    return do_plan(root, args.practice_context)


if __name__ == "__main__":
    sys.exit(main())
