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
# Потолок страниц. Был 80 — и резал ровно то, ради чего документ и читают:
# в заключении эксперта по делу Раковца (119 стр.) таблицы остатков счетов стоят
# на стр. 82-83, то есть ЗА порогом. С переходом на структурный vision-doc
# (1,33 с/стр) 119 страниц стоят 2,6 минуты — держать низкий потолок незачем.
MAXP = int(os.environ.get("THEMIS_MAX_PAGES", "500"))
OCR_WORKERS = 4         # параллельный OCR (subprocess освобождает GIL)
# Apple Vision OCR — локально, $0, русский точно. НЕ облачный vision, НЕ ollama/llava.
# Путь: env THEMIS_VISION_OCR → repo bin/vision-ocr (собирается install.sh) → fallback.
OCR_BIN = os.environ.get("THEMIS_VISION_OCR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "vision-ocr")
# Структурный движок (macOS 26+): даёт таблицы с ячейками. Основной путь для сканов;
# при его отсутствии роутер молча НЕ деградирует — падает на строковый vision-ocr и
# помечает, что структуры таблиц в артефактах нет.
DOC_BIN = os.environ.get("THEMIS_VISION_DOC") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "vision-doc")
# Сигнал отсутствия движка: отличить «движок не собран» от «скан реально пустой».
# Инвариант CLAUDE.md: движок OCR недоступен → СТОП, не деградировать молча на облако.
OCR_ENGINE_MISSING = (
    "\n⛔ OCR-ДВИЖОК НЕ СОБРАН: bin/vision-ocr отсутствует или не исполняемый.\n"
    "   Собери: swiftc -O bin/vision-ocr.swift -o bin/vision-ocr && chmod +x bin/vision-ocr\n"
    "   OCR НЕ ВЫПОЛНЕН — page_NNN.txt пустые, это НЕ пустой скан. СТОП, не уходить на облачный vision молча.")


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


def _vision_doc(png_path):
    """Структурный OCR (macOS 26+): текст + ТАБЛИЦЫ с ячейками, одним проходом.

    Прежний путь (`VNRecognizeTextRequest`) отдаёт только строки, поэтому таблица
    рассыпалась: заголовки отдельными строками, потом номера колонок. На стр. 82
    заключения эксперта по делу Раковца, где по таблице считались остатки счетов,
    это делало доказательство нечитаемым. `RecognizeDocumentsRequest` даёт сетку
    ячеек нативно, за 1,33 с/стр и $0.

    Пишет рядом `page_NNN.md` (параграфы + таблицы GFM). Возвращает плоский текст
    для `page_NNN.txt` — постраничная адресация, на которую опираются читатели.
    Движка нет (старая macOS / не собран) → None, вызывающий уходит на строковый OCR.
    """
    import subprocess
    if not os.access(DOC_BIN, os.X_OK):
        return None
    try:
        r = subprocess.run([DOC_BIN, png_path, "--json"], capture_output=True,
                           text=True, timeout=120)
        d = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    md = list(d.get("paragraphs", []))
    for i, t in enumerate(d.get("tables", []), 1):
        rows = t.get("rows") or []
        if not rows:
            continue
        w = max(len(x) for x in rows)
        def line(cells):
            cs = [str(c).replace("|", "\\|") for c in cells] + [""] * (w - len(cells))
            return "| " + " | ".join(cs) + " |"
        md += ["", f"<!-- таблица {i}: {len(rows)} строк -->", line(rows[0]),
               "|" + " --- |" * w] + [line(r) for r in rows[1:]]
    if md:
        with open(os.path.splitext(png_path)[0] + ".md", "w", encoding="utf-8") as f:
            f.write("\n".join(md))

    flat = list(d.get("paragraphs", []))
    for t in d.get("tables", []):
        flat += ["\t".join(str(c) for c in row) for row in (t.get("rows") or [])]
    return "\n".join(flat)


def _ocr_one(png_path):
    """OCR с адаптивным ретраем: пустой результат → предобработка и повтор.
    Хорошие сканы не трогаем (ретрай только при пустоте)."""
    t = _vision_doc(png_path)
    if t is not None and len(t.strip()) >= 10:
        return t
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
    """Отрисовать страницы PDF в PNG (для внешнего рендера scan-маршрута).

    Идемпотентна: страница с готовым `page_NNN.txt` не перерисовывается и не
    отдаётся на повторный OCR. Раньше каждый вызов гнал заново 300 DPI и Vision
    по всем ≤80 страницам — при том что проверка уже была написана в
    `render_tail.py:35-36` и просто не перенесена сюда (дефект Д11 аудита).
    """
    import fitz
    os.makedirs(outdir, exist_ok=True)
    d = fitz.open(path)
    n = d.page_count
    names, skipped = [], 0
    for i in range(min(maxp, n)):
        png = os.path.join(outdir, f"page_{i + 1:03d}.png")
        if os.path.exists(os.path.splitext(png)[0] + ".txt"):
            skipped += 1
            continue
        d[i].get_pixmap(dpi=dpi).save(png)
        names.append(f"page_{i + 1:03d}.png")
    d.close()
    if skipped:
        print(f"note: {skipped} стр. уже распознаны — пропущены", file=sys.stderr)
    return names, n


# Unlimited-OCR — основной движок (решение владельца 02.08.2026): разбирает весь
# документ одним проходом и сохраняет структуру таблиц, включая переход таблицы
# со страницы на страницу. Apple Vision остаётся в дополнение: он даёт
# постраничные page_NNN.txt для адресации «стр. 82» и страхует, когда основной
# движок недоступен. Оба нужны, один другого не заменяет.
UNLIMITED_MAX_PAGES = int(os.environ.get("THEMIS_OCR_MAX_PAGES", "40"))


def unlimited_pass(ocr_dir, total_pages):
    """Сквозной разбор → ocr_dir/document.md. Возвращает строку для note.

    Никогда не молчит: недоступен движок или упал — так и пишет, чтобы читатель
    знал, что структуры таблиц у него нет, и не принял постраничный текст за неё.
    """
    import subprocess
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_unlimited.py")
    try:
        chk = subprocess.run([sys.executable, tool, "--selftest"],
                             capture_output=True, text=True, timeout=30)
        if chk.returncode != 0:
            return ("\n⚠ Unlimited-OCR (основной движок) не установлен — структуры таблиц НЕТ, "
                    "есть только постраничный текст Apple Vision. Сложные таблицы читать по PNG.")
    except (OSError, subprocess.SubprocessError):
        return "\n⚠ Unlimited-OCR не запускается — работаем на одном Apple Vision."

    if total_pages > UNLIMITED_MAX_PAGES:
        return (f"\n⚠ Unlimited-OCR пропущен: {total_pages} стр. > порога {UNLIMITED_MAX_PAGES} "
                f"(THEMIS_OCR_MAX_PAGES). Запустить вручную по нужному диапазону: "
                f"python3 scripts/ocr_unlimited.py {ocr_dir} --pages ПЕРВАЯ ПОСЛЕДНЯЯ")

    r = subprocess.run([sys.executable, tool, ocr_dir], capture_output=True, text=True)
    try:
        res = json.loads((r.stdout or "{}").strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {"ok": False, "reason": (r.stderr or "")[-200:]}
    if not res.get("ok"):
        return (f"\n⚠ Unlimited-OCR не отдал результат ({res.get('reason', 'причина не названа')}). "
                f"Структуры таблиц нет — только постраничный Apple Vision.")
    return (f"\nUnlimited-OCR (основной, локально, $0): {res['pages']} стр. одним проходом за "
            f"{res['seconds']}с ({res['sec_per_page']}с/стр) → {os.path.basename(res['md_path'])}. "
            f"ЭТО основной текст со структурой таблиц; page_NNN.txt — постраничная адресация.")


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


LAT_MAX = 0.10   # доля латиницы среди букв страницы; выше — текст-слой битый
RU_MIN = 0.40    # доля кириллицы по ВСЕМУ документу, чтобы считать его русским
ALL_BAD = 0.25   # столько битых страниц — битый весь PDF (один прогон чужого OCR)


def _lat_share(t):
    """Доля латиницы среди букв. None — букв слишком мало, судить не по чему."""
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 100:
        return None
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return (len(letters) - cyr) / len(letters)


def text_layer_ok(t, doc_is_ru=True):
    """Текст-слой русского документа бывает битым: чужой OCR запечен в PDF и дает
    кашу вида «Рссn)'6л~1кс». Такой слой ХУЖЕ, чем его отсутствие — читается как
    настоящий текст, но врет в реквизитах. Признак: латиница лезет в кириллицу.
    Apple Vision по рендеру страницы такой лист берет чисто.
    ponytail: одна метрика вместо словарного анализа; ложное срабатывание стоит
    только времени OCR ($0), пропуск мусора стоит ошибки в судебном документе.
    «Русский ли документ» решается по ВСЕМУ файлу, не по странице: у битой
    страницы кириллицы может почти не остаться, и постраничная проверка
    пропускала бы худшие листы как «иноязычные»."""
    r = _lat_share(t)
    return True if (r is None or not doc_is_ru) else r <= LAT_MAX


def pdf_garbage_pages(path, idx):
    """Из страниц с текстовым слоем вернуть те, чей слой мусорный (до-OCR-ить).
    Если битых листов много — весь PDF прогнан через один чужой OCR, чистых
    страниц в нем не осталось: возвращаем все."""
    import fitz
    d = fitz.open(path)
    texts = {i: d[i].get_text() for i in idx}
    d.close()
    letters = [c for t in texts.values() for c in t if c.isalpha()]
    if not letters:
        return []
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    doc_is_ru = cyr >= len(letters) * RU_MIN
    bad = [i for i in idx if not text_layer_ok(texts[i], doc_is_ru)]
    return list(idx) if len(bad) >= max(2, len(idx) * ALL_BAD) else bad


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
    # сайдкары page_NNN.txt ОБЯЗАТЕЛЬНЫ и здесь: без них манифест полноты слеп,
    # а render_scan теряет идемпотентность (дыры png-без-txt, найдено 30 шт. 02.08.2026)
    for i in scan_idx:
        with open(os.path.join(odir, f"page_{i + 1:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(ocr_texts.get(i, ""))
    parts = []
    for i in pages:
        if per[i] >= TEXT_MIN:
            parts.append(d[i].get_text().strip())
        else:
            t = ocr_texts.get(i, "").strip()
            parts.append(f"[стр. {i + 1} — скан, Apple Vision OCR]\n{t}" if t
                         else f"[стр. {i + 1} — скан, OCR пуст: проверить визуально]")
    d.close()
    write_manifest(odir, len(per), text_pages=[i for i in pages if per[i] >= TEXT_MIN])
    return "\n\n".join(parts), len(scan_idx), truncated


def write_manifest(odir, total_pages, text_pages=(), maxp=MAXP):
    """Манифест полноты: страниц в источнике = отрендерено = артефактов.

    Каждая страница — в ТЕРМИНАЛЬНОМ статусе: text (текст-слой) / ocr /
    ocr_empty (распознан, но пусто — фолбэк человеку) / beyond_maxp (за порогом,
    НЕ извлечена — раньше резалась молча) / missing (png есть, txt нет — сбой).
    complete=true только когда нет missing и нет beyond_maxp. Аудит кеша:
    scripts/extract_manifest.py."""
    st = {}
    tp = {i + 1 for i in text_pages}  # 0-based → 1-based
    for p in range(1, total_pages + 1):
        if p in tp:
            st[p] = "text"
        elif p > maxp:
            st[p] = "beyond_maxp"
        else:
            txt = os.path.join(odir, f"page_{p:03d}.txt")
            if os.path.isfile(txt):
                body = open(txt, encoding="utf-8").read().strip()
                st[p] = "ocr" if len(body) >= 10 else "ocr_empty"
            else:
                st[p] = "missing"
    man = {
        "total_pages": total_pages,
        "pages": {str(k): v for k, v in st.items()},
        "missing": [p for p, v in st.items() if v == "missing"],
        "beyond_maxp": [p for p, v in st.items() if v == "beyond_maxp"],
        "ocr_empty": [p for p, v in st.items() if v == "ocr_empty"],
    }
    man["complete"] = not man["missing"] and not man["beyond_maxp"]
    with open(os.path.join(odir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    return man


# ── Авто-реквизиты: вытащить ключевые юр-данные regex-ом на первом проходе ──
# длинные альтернативы первыми (иначе \d{10} съест часть 12-значного ИНН);
# паспорт — только по контексту (10-значный ИНН иначе ловится как паспорт)
_REQ = {
    "inn": re.compile(r"ИНН[:\s]*?\b(\d{12}|\d{10})\b"),
    "ogrn": re.compile(r"ОГРН(?:ИП)?[:\s]*?\b(\d{15}|\d{13})\b"),
    "case_arb": re.compile(r"\bА\d{2}-\d+/\d{4}\b"),
    "case_soyu": re.compile(r"\b\d+[аА]?-\d+/\d{4}\b"),
    "passport": re.compile(r"паспорт[^\d]{0,15}(\d{4}\s?\d{6})", re.IGNORECASE),
    "snils": re.compile(r"СНИЛС[:\s№-]{0,4}(\d{3}[- ]\d{3}[- ]\d{3}[- ]?\d{2})"),
    # 20-значный счет с кодом валюты 810 (рубли) на позициях 6-8
    "account": re.compile(r"\b\d{5}810\d{12}\b"),
    "bik": re.compile(r"БИК[:\s]*?(0\d{8})\b", re.IGNORECASE),
    "isin": re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b"),
    "date": re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b"),
    # копейки через , или . обязаны войти в захват: раньше «265 000,00 ₽»
    # обрезалось до «00 ₽» (матч рестартовал после запятой) — дефект Д-суммы
    "sum_rub": re.compile(
        r"\b(?:\d{1,3}(?:[\s  ']\d{3})+|\d+)(?:[.,]\d{1,2})?\s*(?:руб|₽)",
        re.IGNORECASE),
}


# первые 2 буквы ISIN — код страны ISO-3166 (+XS еврооблигации): отсеивает
# VIN мотоциклов (VBKJPJ404NC2) и внутренние номера (MB0001749435) от ISIN
_ISIN_CC = frozenset(
    "AD AE AR AT AU BE BG BH BM BR BS CA CH CL CN CO CY CZ DE DK EE EG ES FI FR GB GG "
    "GI GR HK HR HU ID IE IL IM IN IS IT JE JP KR KY KZ LI LT LU LV MC MT MX MY NL NO "
    "NZ PA PE PH PL PT QA RO RS RU SA SE SG SI SK TH TR TW UA US UY VG XS ZA".split())


def extract_requisites(body):
    """Уникальные находки по каждому ключу (cap), чтобы читатель брал готовое."""
    out = {}
    for k, rx in _REQ.items():
        found = []
        for m in rx.finditer(body):
            v = (m.group(1) if m.groups() else m.group(0)).strip()
            v = re.sub(r"\s+", " ", v)
            if k == "isin" and v[:2] not in _ISIN_CC:
                continue
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
    ocr_bin_ok = os.access(OCR_BIN, os.X_OK)

    try:
        if e in IMAGE:
            route = "scan"
            note = "OCR_REQUIRED — изображение."
            if a.render_dir:
                if not ocr_bin_ok:
                    note += OCR_ENGINE_MISSING
                else:
                    res = ocr_image(p, a.render_dir)
                    if res:
                        write_manifest(a.render_dir, 1)
                    if res and res[1] >= 10:
                        note += f"\nApple Vision OCR (локально, $0): {a.render_dir}/page_001.txt — читать ТЕКСТ, облачный vision только фолбэк на спорное."
                    else:
                        note += "\nApple Vision OCR пуст → облачный визуальный читатель (Буринский) точечно на спорное."
        elif e == "pdf":
            per = pdf_perpage_chars(p)
            pages = len(per)
            chars = sum(per)
            text_pages = [i for i, c in enumerate(per[:MAXP]) if c >= TEXT_MIN]
            # битый текст-слой (чужой OCR запечен в PDF) — считать страницу сканом
            garbage = pdf_garbage_pages(p, text_pages) if text_pages else []
            for i in garbage:
                per[i] = 0
            text_pages = [i for i, c in enumerate(per[:MAXP]) if c >= TEXT_MIN]
            scan_pages = [i for i, c in enumerate(per[:MAXP]) if c < TEXT_MIN]
            if not text_pages and not garbage:
                # полностью скан — прежний маршрут (рендерит case-mapper, читатели читают .txt)
                route = "scan"
                note = f"OCR_REQUIRED — скан без текстового слоя. Страниц: {pages}."
                if a.render_dir:
                    imgs, n = render_scan(p, a.render_dir)
                    note += f"\nОтрисовано: {len(imgs)} стр. -> {a.render_dir}"
                    if n > MAXP:
                        note += f" (УСЕЧЕНО: всего {n} стр., обработано {MAXP})"
                    if not ocr_bin_ok:
                        note += OCR_ENGINE_MISSING
                    else:
                        od, oe = ocr_pages(a.render_dir, imgs)
                        ocr_count = od
                        man = write_manifest(a.render_dir, n)
                        note += f"\nApple Vision OCR (локально, $0): {od} стр → page_NNN.txt ({oe} пустых). Постраничная адресация — читать ТЕКСТ (.txt)."
                        if not man["complete"]:
                            note += (f"\n⚠ МАНИФЕСТ НЕПОЛОН: missing={man['missing']}, "
                                     f"за порогом MAXP={man['beyond_maxp']} — эти страницы НЕ извлечены.")
                        if oe:
                            note += f"\n⚠ {oe} стр. пустых после предобработки — возможно рукопись/слабый скан → фолбэк на человека или облачный vision."
                        note += unlimited_pass(a.render_dir, n)
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
                    if garbage:
                        note += (f"\n⚠ БИТЫЙ ТЕКСТ-СЛОЙ на {len(garbage)} стр.: в PDF запечен "
                                 f"чужой OCR с кашей вместо кириллицы. Эти страницы распознаны "
                                 f"заново Apple Vision. Текст-слой из PDF по ним НЕ использовать.")
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

    # На scan-маршруте body=None (текст лежит по страницам в ocr_dir/page_NNN.txt),
    # и раньше реквизиты не извлекались вовсе — модель вычитывала ИНН, суммы и
    # номера дел глазами по сканам, то есть самым дорогим способом (дефект Д11).
    # Собираем текст из готовых сайдкаров: регулярки те же, стоимость нулевая.
    if not body and route == "scan" and a.render_dir and os.path.isdir(a.render_dir):
        pages_txt = sorted(f for f in os.listdir(a.render_dir) if f.endswith(".txt"))
        ocr_body = "\n".join(
            open(os.path.join(a.render_dir, f), encoding="utf-8", errors="ignore").read()
            for f in pages_txt
        ).strip()
        if ocr_body and not os.path.isfile(req_path):
            try:
                open(req_path, "w", encoding="utf-8").write(
                    json.dumps(extract_requisites(ocr_body), ensure_ascii=False))
            except OSError:
                pass

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
            "ocr_dir": ocr_dir, "ocr_pages": ocr_count, "ocr_engine": ocr_bin_ok,
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
