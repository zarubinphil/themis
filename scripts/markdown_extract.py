#!/usr/bin/env python3
"""
markdown_extract.py — роутер извлечения текста (экономный по токенам).

Принцип: тяжёлый текст НЕ попадает в результат целиком. По умолчанию роутер
кладёт Markdown в кеш (адресация по хешу содержимого) и печатает только
метаданные + короткое превью. Читатель тянет нужный СРЕЗ из файла кеша
(Grep / Read offset-limit), а не весь документ. Повторный прогон того же
файла (или дубликата) — попадание в кеш, без переконвертации.

Маршруты:
  • Текстовый слой (PDF с текстом, DOCX, XLSX, PPTX, HTML, CSV, RTF)
        -> markitdown -> Markdown в кеш. Дёшево, без модели.
  • Скан / изображение -> ROUTE=scan, OCR_REQUIRED (визуальный читатель).

Использование:
    markdown_extract.py FILE                 # метаданные + превью + путь к MD
    markdown_extract.py FILE --inline        # весь Markdown в stdout (мелкие файлы)
    markdown_extract.py FILE --grep "ИНН|№|руб|договор"   # только строки-совпадения
    markdown_extract.py FILE --json-meta     # одна строка JSON (для триажа)
    markdown_extract.py FILE --render-dir DIR  # скан: отрисовать страницы в PNG

Флаги размера: --preview N (символов превью, 800), --max-chars N (лимит --inline).
"""
import sys, os, argparse, hashlib, json, re

OFFICE = {"docx", "xlsx", "xls", "pptx", "ppt", "html", "htm", "csv", "json", "xml", "rtf", "epub", "odt"}
IMAGE = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "gif", "heic"}
TEXT_MIN = 40
CACHE = os.path.expanduser("~/.cache/legal_extract")
SMALL_INLINE = 8000  # подсказка: до стольки символов дешевле --inline, чем срез


def ext_of(p):
    b = os.path.basename(p)
    return b.rsplit(".", 1)[-1].lower() if "." in b else ""


def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(ch)
    return h.hexdigest()[:16]


def probe_pdf(path, sample=5):
    import fitz
    d = fitz.open(path)
    n = d.page_count
    chars = sum(len(d[i].get_text().strip()) for i in range(min(sample, n)))
    d.close()
    return n, chars


def render_scan(path, outdir, dpi=200, maxp=60):
    import fitz
    os.makedirs(outdir, exist_ok=True)
    d = fitz.open(path)
    paths = []
    for i in range(min(maxp, d.page_count)):
        d[i].get_pixmap(dpi=dpi).save(os.path.join(outdir, f"page_{i + 1:03d}.png"))
        paths.append(f"page_{i + 1:03d}.png")
    d.close()
    return paths


def to_md(path):
    from markitdown import MarkItDown
    return MarkItDown().convert(path).text_content or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--inline", action="store_true", help="весь Markdown в stdout")
    ap.add_argument("--grep", default=None, help="печатать только строки по регэкспу (+ № строки)")
    ap.add_argument("--json-meta", action="store_true")
    ap.add_argument("--render-dir", default=None)
    ap.add_argument("--preview", type=int, default=800)
    ap.add_argument("--max-chars", type=int, default=200000)
    a = ap.parse_args()

    p = os.path.abspath(a.file)
    if not os.path.isfile(p):
        print("ERROR: файл не найден:", p)
        sys.exit(1)

    e = ext_of(p)
    size = os.path.getsize(p)
    sha = sha_of(p)
    os.makedirs(CACHE, exist_ok=True)
    md_path = os.path.join(CACHE, f"{sha}.md")

    route = pages = chars = None
    note = ""
    body = None
    cache = "miss"

    try:
        if e in IMAGE:
            route = "scan"
            note = "OCR_REQUIRED — изображение. Визуальный читатель (Буринский)."
        elif e == "pdf":
            pages, chars = probe_pdf(p)
            if chars >= TEXT_MIN:
                route = "text-pdf"
            else:
                route = "scan"
                note = f"OCR_REQUIRED — скан без текстового слоя. Страниц: {pages}. Визуальный читатель (Буринский)."
                if a.render_dir:
                    imgs = render_scan(p, a.render_dir)
                    note += f"\nОтрисовано: {len(imgs)} стр. -> {a.render_dir}"
        elif e in OFFICE:
            route = "office"
        else:
            route = "office"  # пробуем markitdown для незнакомых

        # извлечение текста только для текстовых маршрутов, с кешем
        if route in ("text-pdf", "office"):
            if os.path.isfile(md_path) and os.path.getsize(md_path) > 0:
                cache = "hit"
                with open(md_path, encoding="utf-8") as f:
                    body = f.read()
            else:
                body = to_md(p)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(body)
    except Exception as ex:
        print("ERROR при извлечении:", ex)
        sys.exit(2)

    words = len(body.split()) if body else 0
    lines = body.count("\n") + 1 if body else 0
    nchars = len(body) if body else 0

    if a.json_meta:
        print(json.dumps({
            "route": route, "ext": e, "bytes": size, "sha": sha, "pages": pages,
            "text_chars": chars, "md_path": md_path if body is not None else None,
            "md_chars": nchars, "md_words": words, "cache": cache,
            "small": nchars <= SMALL_INLINE if body is not None else None,
        }, ensure_ascii=False))
        return

    # человекочитаемая шапка
    print(f"ROUTE: {route}")
    print(f"FILE: {os.path.basename(p)}")
    if pages is not None:
        print(f"PAGES: {pages}  TEXT_CHARS: {chars}")
    if route == "scan":
        print("---")
        print(note)
        return

    print(f"MD: {md_path}  ({nchars} симв. / {words} слов / {lines} строк)  CACHE: {cache}")

    if a.grep:
        rx = re.compile(a.grep, re.IGNORECASE)
        hits = [f"{i}: {ln}" for i, ln in enumerate(body.splitlines(), 1) if rx.search(ln)]
        print(f"--- grep '{a.grep}' ({len(hits)} строк) ---")
        print("\n".join(hits[:400]))
        return

    if a.inline:
        print("---")
        out = body[: a.max_chars]
        print(out)
        if len(body) > a.max_chars:
            print(f"\n[...обрезано, всего {nchars} симв.; остальное в MD...]")
        return

    # дефолт: только превью, остальное — точечно из MD
    print(f"--- превью (первые {a.preview} симв.) ---")
    print(body[: a.preview])
    if nchars > a.preview:
        print(f"\n[...ещё {nchars - a.preview} симв. в MD. Тяни срез: "
              f"Grep по MD или Read с offset/limit. Не читай целиком без нужды.]")
    if nchars <= SMALL_INLINE:
        print("[файл мелкий — можно один раз --inline вместо среза]")


if __name__ == "__main__":
    main()
