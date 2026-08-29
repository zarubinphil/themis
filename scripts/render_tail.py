#!/usr/bin/env python3
"""render_tail.py — дорендер хвоста скан-PDF сверх потолка MAXP роутера.

markdown_extract.py рендерит/OCR-ит не более MAXP (=500, env THEMIS_MAX_PAGES) страниц;
усечение помечается в note: «УСЕЧЕНО: всего N стр., обработано MAXP». Этот скрипт
отрисовывает и OCR-ит остаток локальным Apple Vision ($0) в те же сайдкары
ocr_dir/page_NNN.{png,txt}. Идемпотентен: страницы с готовым .txt пропускает.

Использование: render_tail.py FILE OCR_DIR START [END]
  START/END — номера страниц от 1 включительно. START=1 безопасен всегда:
  страницы с готовым .txt пропускаются, добираются только дыры и хвост.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import markdown_extract as m  # noqa: E402  (DPI, OCR_BIN, ocr_pages)


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit("usage: render_tail.py FILE OCR_DIR START [END]")
    path, outdir, start = sys.argv[1], sys.argv[2], int(sys.argv[3])
    m.guard_render_dir(outdir)   # рендер внутрь дела запрещён; проверка одна на все приборы
    if not os.path.isfile(path):
        sys.exit(f"файл не найден: {path}")
    # Инвариант local-first: движка OCR нет → СТОП, не деградировать на облако.
    if not os.access(m.OCR_BIN, os.X_OK):
        sys.exit(m.OCR_ENGINE_MISSING)
    import fitz
    d = fitz.open(path)
    end = min(int(sys.argv[4]) if len(sys.argv) > 4 else d.page_count, d.page_count)
    os.makedirs(outdir, exist_ok=True)
    names = []
    for i in range(start - 1, end):
        png = os.path.join(outdir, f"page_{i + 1:03d}.png")
        if os.path.exists(os.path.splitext(png)[0] + ".txt"):
            continue  # готовый OCR не перераспознавать
        d[i].get_pixmap(dpi=m.DPI).save(png)
        names.append(os.path.basename(png))
    d.close()
    done, empty = m.ocr_pages(outdir, names) if names else (0, 0)
    print(f"Дорендер: стр. {start}-{end}, новых OCR: {done}, пустых: {empty}, пропущено готовых: {end - start + 1 - done}")


if __name__ == "__main__":
    main()
