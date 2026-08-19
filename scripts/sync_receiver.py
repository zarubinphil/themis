#!/usr/bin/env python3
"""sync_receiver.py — приёмник синхронизации «Mac → сервер». Берёт только текст.

Решение владельца 18.08.2026: на сервер уходит ИЗВЛЕЧЁННЫЙ ТЕКСТ дел, оригиналы —
никогда. Значит, у границы нужен сторож, который решает не по обещанию отправителя,
а по самому файлу: имя врёт, расширение врёт, симлинк врёт особенно охотно.

    --queue КАТАЛОГ --accept ФАЙЛ --as ОТНОСИТЕЛЬНЫЙ_ПУТЬ
        код 0 — принято, файл лежит в очереди по указанному пути
        код 1 — отвергнуто, причина названа, в очереди НЕ появилось ничего
    --selftest   проверка без сети

Отвергается:
  · оригинал документа (.pdf .docx .xlsx .pptx .doc .rtf и прочая первичка) —
    даже если он текстовый внутри: это материал дела, его место на Маке;
  · бинарник под любым именем — содержимое не UTF-8 (.txt с байтами PDF тоже);
  · симлинк — за ним может стоять что угодно вне очереди;
  · путь вне очереди — `..`, абсолютный путь, ссылка-каталог.

Fail-closed: непонятный случай — отказ. Пропущенный оригинал уходит с машины
навсегда, отвергнутый текст отправляется повторно.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

# Первичка: расширения, которые на сервере не нужны ни при каком содержимом.
ORIGINAL_EXT = {"pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "rtf", "odt",
                "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "heic",
                "zip", "rar", "7z", "mov", "mp4", "m4a", "mp3", "wav"}
# Что принимаем: результат извлечения и служебные выжимки.
TEXT_EXT = {"md", "txt", "json", "csv", "yaml", "yml"}
MAX_BYTES = 20 * 1024 * 1024      # выжимка дела столько не весит; больше — повод посмотреть
MAX_DEPTH = 12                    # cases/клиент/дело/.agent/context/... — глубже незачем


def _otkaz(why: str) -> int:
    print(f"ОТКАЗ: {why}", file=sys.stderr)
    return 1


def accept(queue: Path, src: Path, rel: str) -> int:
    queue = queue.resolve()
    if not queue.is_dir():
        return _otkaz(f"очереди нет: {queue}")

    # 1. Путь назначения обязан остаться внутри очереди и быть обычным путём.
    if rel.startswith("/") or rel.startswith("~"):
        return _otkaz(f"абсолютный путь назначения: {rel}")
    if "\\" in rel:
        # На сервере обратный слеш — обычный символ имени, и файл остаётся внутри
        # очереди. Но на клиенте другой платформы то же имя читается как каталоги.
        return _otkaz(f"обратные слеши в пути: {rel}")
    if any(ord(c) < 32 for c in rel):
        return _otkaz("управляющие символы в пути назначения")
    if len(Path(rel).parts) > MAX_DEPTH:
        return _otkaz(f"путь в {len(Path(rel).parts)} сегментов — глубже {MAX_DEPTH} "
                      "выжимки не лежат")
    dst = (queue / rel).resolve()
    if queue not in dst.parents:
        return _otkaz(f"путь ведёт вне очереди: {rel}")

    # 2. Источник: симлинк не берём — цель может лежать где угодно.
    if src.is_symlink():
        return _otkaz(f"симлинк не принимается: {src.name}")
    if not src.is_file():
        return _otkaz(f"файла нет: {src}")

    # 3. Оригинал документа не покидает машину — ни под своим именем, ни под чужим.
    for name in (src.name, Path(rel).name):
        ext = Path(name).suffix.lower().lstrip(".")
        if ext in ORIGINAL_EXT:
            return _otkaz(f"оригинал документа (.{ext}) на сервер не уходит — "
                          "туда идёт только извлечённый текст")
        if ext not in TEXT_EXT:
            return _otkaz(f"расширение .{ext or '—'} не в списке текстовых "
                          f"({', '.join(sorted(TEXT_EXT))})")

    size = src.stat().st_size
    if size == 0:
        # Пустая выжимка поверх целой стирает содержимое на сервере, и выглядит
        # это как обычная синхронизация. Обрезанный экстракт отправляют повторно.
        return _otkaz(f"{src.name}: файл пуст — синхронизировать нечего")
    if size > MAX_BYTES:
        return _otkaz(f"{size / 1024 / 1024:.1f} МБ — больше потолка выжимки "
                      f"({MAX_BYTES // 1024 // 1024} МБ)")

    # 4. Содержимое решает последним: имя врёт, байты нет.
    raw = src.read_bytes()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return _otkaz(f"{src.name}: содержимое не текст (не UTF-8) — бинарник "
                      "под текстовым именем")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"принято: {rel} ({size} байт)")
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        q = td / "queue"
        q.mkdir()
        good = td / "karta.md"
        good.write_text("# карта дела\nизвлечённый текст", encoding="utf-8")
        original = td / "isk.pdf"
        original.write_bytes(b"%PDF-1.4\n")
        fake = td / "fake.txt"
        fake.write_bytes(b"\x00\x01\xff\xfe")
        link = td / "link.md"
        link.symlink_to(good)

        assert accept(q, good, "delo/karta.md") == 0, "текст отвергнут"
        assert (q / "delo" / "karta.md").is_file(), "принято, но файла нет"
        assert accept(q, original, "delo/isk.pdf") == 1, "оригинал принят"
        assert accept(q, original, "delo/isk.md") == 1, "оригинал под чужим именем принят"
        assert accept(q, fake, "delo/fake.txt") == 1, "бинарник принят"
        assert accept(q, link, "delo/link.md") == 1, "симлинк принят"
        assert accept(q, good, "../beglec.md") == 1, "выход из очереди принят"
        assert accept(q, good, "/etc/themis.md") == 1, "абсолютный путь принят"
        assert not (td / "beglec.md").exists(), "запись состоялась вне очереди"
        left = sorted(p.relative_to(q).as_posix() for p in q.rglob("*") if p.is_file())
        assert left == ["delo/karta.md"], f"в очереди лишнее: {left}"

        pusto = td / "pusto.md"
        pusto.write_bytes(b"")
        assert accept(q, pusto, "delo/karta.md") == 1, "пустой файл принят поверх целого"
        assert (q / "delo" / "karta.md").read_text(encoding="utf-8").startswith("#"), \
            "принятый ранее текст затёрт пустым"
        assert accept(q, good, "a\\b.md") == 1, "обратные слеши приняты"
        assert accept(q, good, "/".join(["a"] * 300) + "/k.md") == 1, "путь-бомба принят"

        big = td / "big.md"
        big.write_text("я" * (MAX_BYTES + 1), encoding="utf-8")
        assert accept(q, big, "delo/big.md") == 1, "файл сверх потолка принят"
    print("selftest пройден: оригинал, бинарник, симлинк и выход из очереди отвергнуты")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Приёмник синхронизации: только извлечённый текст.")
    ap.add_argument("--queue", help="каталог очереди на сервере")
    ap.add_argument("--accept", help="файл-кандидат")
    ap.add_argument("--as", dest="rel", help="путь внутри очереди")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.queue and a.accept and a.rel:
        return accept(Path(a.queue), Path(a.accept), a.rel)
    ap.error("нужны --queue, --accept и --as, либо --selftest")


if __name__ == "__main__":
    sys.exit(main())
