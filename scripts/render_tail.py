#!/usr/bin/env python3
"""render_tail.py — дорендер хвоста скан-PDF сверх потолка MAXP роутера.

markdown_extract.py рендерит/OCR-ит не более MAXP (=500, env THEMIS_MAX_PAGES) страниц;
усечение помечается в note: «УСЕЧЕНО: всего N стр., обработано MAXP». Этот скрипт
отрисовывает и OCR-ит остаток локальным Apple Vision ($0) в те же сайдкары
ocr_dir/page_NNN.{png,txt}. Идемпотентен: страницы с готовым .txt пропускает.

Использование: render_tail.py FILE OCR_DIR START [END]
  START/END — номера страниц от 1 включительно. START=1 безопасен всегда:
  страницы с готовым .txt пропускаются, добираются только дыры и хвост.
  render_tail.py --selftest — проверка без сети и без материалов дел.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import markdown_extract as m  # noqa: E402  (DPI, OCR_BIN, ocr_pages)


def render_pages(path, outdir, start, end=None):
    """Отрисовать страницы [start..end] (1-based, end=None — до конца) в PNG.
    Страницы с готовым .txt пропускаются. Возвращает (имена новых PNG, всего стр.)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    n = len(pdf)
    end = min(end or n, n)
    os.makedirs(outdir, exist_ok=True)
    names = []
    for i in range(start - 1, end):
        png = os.path.join(outdir, f"page_{i + 1:03d}.png")
        if os.path.exists(os.path.splitext(png)[0] + ".txt"):
            continue  # готовый OCR не перераспознавать
        pdf[i].render(scale=m.DPI / 72).to_pil().save(png)
        names.append(os.path.basename(png))
    pdf.close()
    return names, n


def selftest() -> int:
    """Проверка СВОЕГО поведения прибора, без сети и без материалов дел.

    Фикстура — cases/_templates/_selftest_render.pdf (собран reportlab, латиница:
    встроенные шрифты кириллицу не несут). Именно своя фикстура, а не «любой PDF
    из дел»: дела закрываются и архивируются, и ссылка на чужой материал роняет
    селфтест ОКРУЖЕНИЕМ раньше, чем доходит до проверяемого условия (урок R02).
    """
    import subprocess, tempfile, io, contextlib
    checks = []
    here = os.path.abspath(__file__)

    # 1. Разбор аргументов: вызов без аргументов — ненулевой код и usage.
    r = subprocess.run([sys.executable, here], capture_output=True, text=True)
    checks.append(("usage без аргументов: ненулевой код + подсказка",
                   r.returncode != 0 and "usage:" in (r.stderr + r.stdout)))

    fixture = os.path.join(os.path.dirname(os.path.dirname(here)),
                           "cases", "_templates", "_selftest_render.pdf")
    checks.append(("фикстура PDF на месте", os.path.isfile(fixture)))

    # 2. Рендер страницы настоящего PDF (pypdfium2, как в боевом пути).
    with tempfile.TemporaryDirectory() as td:
        names, n = render_pages(fixture, td, 1)
        png = os.path.join(td, "page_001.png")
        checks.append(("страница реального PDF отрисована в PNG",
                       bool(names) and os.path.isfile(png) and os.path.getsize(png) > 0))
        # 3. Идемпотентность: готовый .txt — страница не перерисовывается.
        with open(os.path.join(td, "page_001.txt"), "w") as f:
            f.write("готово")
        names2, _ = render_pages(fixture, td, 1)
        checks.append(("готовый .txt не перерисовывается", names2 == []))

    # 4. fitz напрямую в ЭТОМ приборе не импортируется: запасной путь MuPDF
    #    живет только в markdown_extract и обязан говорить вслух (см. шапку
    #    markdown_extract.py — решение владельца 02.09.2026).
    with open(here, encoding="utf-8") as f:
        src = f.read()
    needle = "import " + "fitz"  # собрано по частям, чтобы не ловить саму проверку
    checks.append(("прямого импорта fitz в render_tail нет", needle not in src))

    # 5. Запасной путь MuPDF СООБЩАЕТ о себе: постранично, с идентификатором
    #    материала, номером страницы и причиной (решение владельца 02.09.2026,
    #    круг 4). fitz стоит нарочно (отложенный блокер AGPL), поэтому замер
    #    исполняется целиком; объявление проверяется без редкой фикстуры —
    #    _announce_mupdf вынесен ровно для этого.
    res = m._mupdf_perpage(fixture)
    checks.append(("запасной замер MuPDF вернул постраничный счет (fitz установлен)",
                   isinstance(res, list) and len(res) >= 1))
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        m._announce_mupdf("fixtureid0", 1, 0, 100, "проверка объявления")
    said = buf.getvalue()
    checks.append(("запасной путь печатает идентификатор, страницу и причину в stderr",
                   "MuPDF" in said and "fixtureid0" in said and "стр. 1" in said
                   and "вслух" in said))
    # 6. Правило минимума: pdf_perpage_chars возвращает счет решения и набор
    #    страниц, чей текст берет MuPDF.
    dec, mupdf_pages = m.pdf_perpage_chars(fixture, "fixtureid0")
    checks.append(("счет решения — минимум движков, страницы MuPDF — множество",
                   len(dec) == len(res) and isinstance(mupdf_pages, set)))

    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main() -> None:
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    if len(sys.argv) < 4:
        sys.exit("usage: render_tail.py FILE OCR_DIR START [END]")
    path, outdir, start = sys.argv[1], sys.argv[2], int(sys.argv[3])
    m.guard_render_dir(outdir)   # рендер внутрь дела запрещен; проверка одна на все приборы
    if not os.path.isfile(path):
        sys.exit(f"файл не найден: {path}")
    # Инвариант local-first: движка OCR нет → СТОП, не деградировать на облако.
    if not os.access(m.OCR_BIN, os.X_OK):
        sys.exit(m.OCR_ENGINE_MISSING)
    end = int(sys.argv[4]) if len(sys.argv) > 4 else None
    names, n = render_pages(path, outdir, start, end)
    end = min(end or n, n)
    done, empty = m.ocr_pages(outdir, names) if names else (0, 0)
    print(f"Дорендер: стр. {start}-{end}, новых OCR: {done}, пустых: {empty}, пропущено готовых: {end - start + 1 - done}")


if __name__ == "__main__":
    main()
