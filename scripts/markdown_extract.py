#!/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
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
  • СМЕШАННЫЙ PDF (часть текст, часть скан-страницы) -> постранично:
        текст где есть + Apple Vision OCR на скан-страницы -> склейка в кеш.
        Маршрут остаётся text-pdf, но ни одна страница не теряется.
  • Полный скан / изображение -> ROUTE=scan, OCR_REQUIRED (визуальный читатель).

Извлечение всегда локальное: текст — markitdown/fitz, скан — Apple Vision OCR ($0).
Никаких облачных/LLM вызовов в роутере.

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
AUDIO = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "aiff", "amr"}
VIDEO = {"mov", "mp4", "m4v", "avi", "mkv", "3gp"}
TEXT_MIN = 40            # символов текста на странице → считаем «текстовой»
CACHE = os.path.expanduser("~/.cache/legal_extract")
SMALL_INLINE = 8000     # подсказка: до стольки символов дешевле --inline, чем срез
DPI = 300               # рендер сканов для OCR (мелкий юр-шрифт читается лучше, чем на 200)
MAXP = 80               # потолок страниц на рендер/OCR; усечение помечается явно
OCR_WORKERS = 4         # параллельный OCR (subprocess освобождает GIL)
# Apple Vision OCR — локально, $0, русский точно. НЕ облачный vision, НЕ ollama/llava.
# Путь: env THEMIS_VISION_OCR → repo bin/vision-ocr (собирается install.sh) → fallback.
OCR_BIN = os.environ.get("THEMIS_VISION_OCR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "vision-ocr")


def ext_of(p):
    b = os.path.basename(p)
    return b.rsplit(".", 1)[-1].lower() if "." in b else ""


def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(ch)
    return h.hexdigest()[:16]


def pdf_perpage_chars(path):
    """Длина текстового слоя ПО КАЖДОЙ странице — основа решения text/scan/mixed."""
    import fitz
    d = fitz.open(path)
    per = [len(d[i].get_text().strip()) for i in range(d.page_count)]
    d.close()
    return per


def _vision(png_path):
    """Сырой Apple Vision OCR одного PNG → текст. Сбой → ''."""
    import subprocess
    try:
        return subprocess.run([OCR_BIN, png_path], capture_output=True,
                              text=True, timeout=60).stdout
    except Exception:
        return ""


def _enhance(png_path):
    """Предобработка кривого/бледного скана: grayscale + autocontrast + sharpen.
    Возвращает путь к улучшенной копии или None. Локально (Pillow)."""
    try:
        from PIL import Image, ImageOps, ImageFilter
        im = Image.open(png_path).convert("L")
        im = ImageOps.autocontrast(im, cutoff=1)
        im = im.filter(ImageFilter.SHARPEN)
        out = png_path + ".enh.png"
        im.save(out)
        return out
    except Exception:
        return None


def _ocr_one(png_path):
    """OCR с адаптивным ретраем: пустой результат → предобработка и повтор.
    Хорошие сканы не трогаем (ретрай только при пустоте)."""
    t = _vision(png_path)
    if len(t.strip()) >= 10:
        return t
    enh = _enhance(png_path)
    if enh:
        t2 = _vision(enh)
        try:
            os.remove(enh)
        except OSError:
            pass
        if len(t2.strip()) > len(t.strip()):
            return t2
    return t


def _ocr_many(png_paths):
    """Параллельный OCR пачки PNG → список текстов в том же порядке."""
    from concurrent.futures import ThreadPoolExecutor
    if not os.access(OCR_BIN, os.X_OK):
        return [""] * len(png_paths)
    with ThreadPoolExecutor(max_workers=OCR_WORKERS) as ex:
        return list(ex.map(_ocr_one, png_paths))


def render_scan(path, outdir, dpi=DPI, maxp=MAXP):
    """Отрисовать страницы PDF в PNG (для внешнего рендера scan-маршрута)."""
    import fitz
    os.makedirs(outdir, exist_ok=True)
    d = fitz.open(path)
    n = d.page_count
    names = []
    for i in range(min(maxp, n)):
        d[i].get_pixmap(dpi=dpi).save(os.path.join(outdir, f"page_{i + 1:03d}.png"))
        names.append(f"page_{i + 1:03d}.png")
    d.close()
    return names, n


def ocr_pages(outdir, png_names):
    """Apple Vision OCR по PNG → сайдкары page_NNN.txt рядом. Параллельно, $0.
    Возвращает (готово, пустых)."""
    paths = [os.path.join(outdir, n) for n in png_names]
    texts = _ocr_many(paths)
    empty = 0
    for png_path, t in zip(paths, texts):
        with open(os.path.splitext(png_path)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write(t)
        if len(t.strip()) < 10:
            empty += 1
    return len(png_names), empty


def ocr_image(path, outdir):
    """OCR одиночной картинки → outdir/page_001.txt. $0. Возвращает (путь, длина)."""
    os.makedirs(outdir, exist_ok=True)
    if not os.access(OCR_BIN, os.X_OK):
        return None
    t = _ocr_one(path)
    txt_path = os.path.join(outdir, "page_001.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(t)
    return txt_path, len(t.strip())


def to_md(path):
    """Текстовый слой через markitdown (без модели, без сети)."""
    from markitdown import MarkItDown
    return MarkItDown().convert(path).text_content or ""


def transcribe(path):
    """Локальная расшифровка аудио/видео через whisper (ru, модель small). $0, без сети.
    Видео whisper берёт через ffmpeg. Возвращает текст или ''."""
    import subprocess, shutil, tempfile
    wbin = shutil.which("whisper") or os.path.expanduser("~/.npm-global/bin/whisper")
    if not (wbin and os.path.exists(wbin)):
        return ""
    outdir = tempfile.mkdtemp()
    try:
        subprocess.run([wbin, path, "--model", "small", "--language", "ru",
                        "--output_format", "txt", "--output_dir", outdir],
                       capture_output=True, text=True, timeout=1800)
        txt = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".txt")
        if os.path.isfile(txt):
            return open(txt, encoding="utf-8").read().strip()
    except Exception:
        pass
    return ""


def pdf_mixed_to_md(path, per, sha):
    """Смешанный PDF: текст где есть, Apple Vision OCR где скан. Склейка по порядку
    страниц. OCR-страницы помечены. Возвращает (body, n_ocr_pages, truncated)."""
    import fitz
    truncated = per[MAXP:] != []
    pages = list(range(min(len(per), MAXP)))
    scan_idx = [i for i in pages if per[i] < TEXT_MIN]
    odir = os.path.join(CACHE, f"{sha}_ocr")
    os.makedirs(odir, exist_ok=True)
    d = fitz.open(path)
    # отрисовать и OCR только скан-страницы (параллельно)
    png_paths = []
    for i in scan_idx:
        pp = os.path.join(odir, f"page_{i + 1:03d}.png")
        d[i].get_pixmap(dpi=DPI).save(pp)
        png_paths.append(pp)
    ocr_texts = dict(zip(scan_idx, _ocr_many(png_paths))) if png_paths else {}
    parts = []
    for i in pages:
        if per[i] >= TEXT_MIN:
            parts.append(d[i].get_text().strip())
        else:
            t = ocr_texts.get(i, "").strip()
            parts.append(f"[стр. {i + 1} — скан, Apple Vision OCR]\n{t}" if t
                         else f"[стр. {i + 1} — скан, OCR пуст: проверить визуально]")
    d.close()
    return "\n\n".join(parts), len(scan_idx), truncated


# ── Авто-реквизиты: вытащить ключевые юр-данные regex-ом на первом проходе ──
# длинные альтернативы первыми (иначе \d{10} съест часть 12-значного ИНН);
# паспорт — только по контексту (10-значный ИНН иначе ловится как паспорт)
_REQ = {
    "inn": re.compile(r"ИНН[:\s]*?\b(\d{12}|\d{10})\b"),
    "ogrn": re.compile(r"ОГРН(?:ИП)?[:\s]*?\b(\d{15}|\d{13})\b"),
    "case_arb": re.compile(r"\bА\d{2}-\d+/\d{4}\b"),
    "case_soyu": re.compile(r"\b\d+[аА]?-\d+/\d{4}\b"),
    "passport": re.compile(r"паспорт[^\d]{0,15}(\d{4}\s?\d{6})", re.IGNORECASE),
    "date": re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b"),
    "sum_rub": re.compile(r"\b\d[\d\s ]{2,}(?:руб|₽|рублей)", re.IGNORECASE),
}


def extract_requisites(body):
    """Уникальные находки по каждому ключу (cap), чтобы читатель брал готовое."""
    out = {}
    for k, rx in _REQ.items():
        found = []
        for m in rx.finditer(body):
            v = (m.group(1) if m.groups() else m.group(0)).strip()
            v = re.sub(r"\s+", " ", v)
            if v and v not in found:
                found.append(v)
            if len(found) >= 50:
                break
        if found:
            out[k] = found
    return out


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
    req_path = os.path.join(CACHE, f"{sha}.requisites.json")

    route = pages = chars = None
    note = ""
    body = None
    cache = "miss"
    ocr_count = 0

    try:
        if e in IMAGE:
            route = "scan"
            note = "OCR_REQUIRED — изображение."
            if a.render_dir:
                res = ocr_image(p, a.render_dir)
                if res and res[1] >= 10:
                    note += f"\nApple Vision OCR (локально, $0): {a.render_dir}/page_001.txt — читать ТЕКСТ, облачный vision только фолбэк на спорное."
                else:
                    note += "\nApple Vision OCR пуст/недоступен → облачный визуальный читатель (Буринский)."
        elif e == "pdf":
            per = pdf_perpage_chars(p)
            pages = len(per)
            chars = sum(per)
            text_pages = [i for i, c in enumerate(per[:MAXP]) if c >= TEXT_MIN]
            scan_pages = [i for i, c in enumerate(per[:MAXP]) if c < TEXT_MIN]
            if not text_pages:
                # полностью скан — прежний маршрут (рендерит case-mapper, читатели читают .txt)
                route = "scan"
                note = f"OCR_REQUIRED — скан без текстового слоя. Страниц: {pages}."
                if a.render_dir:
                    imgs, n = render_scan(p, a.render_dir)
                    note += f"\nОтрисовано: {len(imgs)} стр. -> {a.render_dir}"
                    if n > MAXP:
                        note += f" (УСЕЧЕНО: всего {n} стр., обработано {MAXP})"
                    od, oe = ocr_pages(a.render_dir, imgs)
                    ocr_count = od
                    note += f"\nApple Vision OCR (локально, $0): {od} стр → page_NNN.txt ({oe} пустых). Читать ТЕКСТ (.txt), облачный vision ТОЛЬКО фолбэк на пустые/спорные критичные реквизиты."
                    if oe:
                        note += f"\n⚠ {oe} стр. пустых после предобработки — возможно рукопись/слабый скан → фолбэк на человека или облачный vision."
            elif not scan_pages:
                route = "text-pdf"  # чистый текст — markitdown (лучшая разметка)
            else:
                # СМЕШАННЫЙ: текст + скан-страницы. Не теряем ни одной страницы.
                route = "text-pdf"
                if os.path.isfile(md_path) and os.path.getsize(md_path) > 0:
                    cache = "hit"
                    body = open(md_path, encoding="utf-8").read()
                else:
                    body, ocr_count, trunc = pdf_mixed_to_md(p, per, sha)
                    open(md_path, "w", encoding="utf-8").write(body)
                    note = (f"СМЕШАННЫЙ PDF: {len(text_pages)} текст-стр + {ocr_count} скан-стр "
                            f"(до-OCR-ено Apple Vision, $0). Контент полный."
                            + (f" УСЕЧЕНО: всего {pages} стр., обработано {MAXP}." if trunc else ""))
        elif e in AUDIO or e in VIDEO:
            route = "media"
            if os.path.isfile(md_path) and os.path.getsize(md_path) > 0:
                cache = "hit"
                body = open(md_path, encoding="utf-8").read()
            else:
                body = transcribe(p)
                if body:
                    open(md_path, "w", encoding="utf-8").write(body)
                note = ("Расшифровка whisper (ru, small, локально, $0)." if body
                        else "whisper недоступен или речь не распознана → проверить вручную.")
        elif e in OFFICE:
            route = "office"
        else:
            route = "office"  # пробуем markitdown для незнакомых

        # извлечение текста для текстовых маршрутов (с кешем), если ещё не собрано
        if route in ("text-pdf", "office") and body is None:
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

    # авто-реквизиты в сайдкар (только для текстовых маршрутов)
    requisites = None
    if body:
        if os.path.isfile(req_path):
            try:
                requisites = json.loads(open(req_path, encoding="utf-8").read())
            except ValueError:
                requisites = None
        if requisites is None:
            requisites = extract_requisites(body)
            try:
                open(req_path, "w", encoding="utf-8").write(json.dumps(requisites, ensure_ascii=False))
            except OSError:
                pass

    words = len(body.split()) if body else 0
    lines = body.count("\n") + 1 if body else 0
    nchars = len(body) if body else 0

    ocr_dir = a.render_dir if (route == "scan" and a.render_dir) else None
    if a.json_meta:
        print(json.dumps({
            "route": route, "ext": e, "bytes": size, "sha": sha, "pages": pages,
            "text_chars": chars, "md_path": md_path if body is not None else None,
            "md_chars": nchars, "md_words": words, "cache": cache,
            "small": nchars <= SMALL_INLINE if body is not None else None,
            "ocr_dir": ocr_dir, "ocr_pages": ocr_count,
            "requisites": requisites, "requisites_path": req_path if requisites else None,
            "note": note,
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
    if note:
        print(note)
    if requisites:
        print(f"РЕКВИЗИТЫ ({req_path}): " +
              ", ".join(f"{k}×{len(v)}" for k, v in requisites.items()))

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
