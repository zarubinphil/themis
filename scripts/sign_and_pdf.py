#!/usr/bin/env python3
"""
sign_and_pdf.py — конвертация docx → PDF + наложение подписи.

Использование:
    sign_and_pdf.py {путь_к_docx}                 # конвертировать + подпись
    sign_and_pdf.py {путь_к_docx} --no-sign       # только конвертировать
    sign_and_pdf.py {путь_к_docx} --sign-only     # только наложить подпись на PDF

Подпись: cases/_assets/подпись.png (PNG с прозрачным фоном, ~300x100px)
Выход: рядом с docx, то же имя, расширение .pdf
"""
import sys
import os
import io
import subprocess
import argparse
import shutil
import tempfile

SIGNATURE_PATH = os.path.join(os.path.dirname(__file__), "../cases/_assets/подпись.png")

def docx_to_pdf_via_word(docx_path: str, pdf_path: str) -> bool:
    """Конвертация через Microsoft Word (AppleScript)."""
    docx_abs = os.path.abspath(docx_path)
    pdf_abs = os.path.abspath(pdf_path)
    # Документ, уже открытый владельцем, Word повторно не открывает: `open`
    # возвращает ничто, theDoc остается неопределенной (-2753). Делать `save as`
    # на его открытом окне тоже нельзя — Word переназначит окно на PDF, а у
    # владельца в Word держится два десятка документов. Поэтому конвертируем
    # копию под другим именем: оригинал и открытое окно не трогаются.
    # PDF пишем рядом с копией, а не сразу в папку дела: Word в песочнице на
    # чужой каталог выбрасывает модальный диалог «Предоставить доступ к файлам»
    # и висит до ответа человека. В папку открытого им же файла он пишет молча.
    # Песочница Word не пускает запись ни в папку дела, ни в /var/folders, ни в
    # ~/Documents — на каждую вешает модальный диалог «Предоставить доступ к
    # файлам» и висит до ответа человека. Свой контейнер он пишет молча, поэтому
    # и копия, и PDF живут там, а готовый файл забираем оттуда сами.
    word_docs = os.path.expanduser(
        "~/Library/Containers/com.microsoft.Word/Data/Documents")
    os.makedirs(word_docs, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="themis_pdf_", dir=word_docs)
    tmp_docx = os.path.join(tmp_dir, "_conv_" + os.path.basename(docx_abs))
    tmp_pdf = os.path.splitext(tmp_docx)[0] + ".pdf"
    shutil.copy2(docx_abs, tmp_docx)
    script = f'''
tell application "Microsoft Word"
    open POSIX file "{tmp_docx}"
    -- `open` в Word ничего не возвращает: `set theDoc to open ...` дает -2753.
    -- Документ, присвоенный переменной, не принимает `save as` (-1708).
    -- Работает только адресация «active document» прямо в команде.
    set thePath to POSIX file "{tmp_pdf}" as string
    save as active document file name thePath file format format PDF
    close active document saving no
end tell
'''
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"Word ошибка: {result.stderr.strip()}")
            return False
        if not os.path.exists(tmp_pdf):
            print("Word не создал PDF.")
            return False
        shutil.move(tmp_pdf, pdf_abs)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return os.path.exists(pdf_path)


def find_signature_rect(page):
    """
    Найти блок подписи на странице.
    Ищет текст 'С уважением' или 'Представитель' или '__________' в нижней трети страницы.
    Возвращает прямоугольник для размещения подписи в координатах страницы от левого верхнего угла.
    """
    from pypdf import mult
    from pypdf._font import Font

    crop_width, crop_height = float(page.cropbox.width), float(page.cropbox.height)
    page_width, page_height = ((crop_height, crop_width)
                               if page.rotation % 180 else (crop_width, crop_height))
    lower_third = page_height * 0.65

    # Ищем маркеры подписи. Фамилия подписанта — первый маркер: в судебных
    # документах Фемиды блок подписи это строка таблицы «дата | | Фамилия»,
    # ни «С уважением», ни прочерков в ней нет.
    markers = ["Зарубин", "Зарубина", "С уважением", "Представитель",
               "___________", "Подпись", "подпись"]
    fragments = []

    def visit(text, cm, tm, font, font_size):
        value = text.replace("\r", "").replace("\n", "")
        if not value or not font:
            return
        # ponytail: у pypdf нет публичного API ширины декодированных глифов;
        # перейти на него, когда появится, вместо собственной копии парсера PDF-шрифтов.
        metrics = Font.from_font_resource(font)
        inverse_map = {decoded: raw for raw, decoded in metrics.character_map.items()}
        matrix = mult(tm, cm)
        bottom = float(metrics.font_descriptor.descent) * float(font_size) / 1000
        top = float(metrics.font_descriptor.ascent) * float(font_size) / 1000
        cursor, boxes = 0.0, []
        for char in value:
            raw = inverse_map.get(char, char)
            advance = metrics.character_widths.get(
                raw, metrics.character_widths["default"]) * float(font_size) / 1000
            points = [(matrix[0] * x + matrix[2] * y + matrix[4],
                       matrix[1] * x + matrix[3] * y + matrix[5])
                      for x in (cursor, cursor + advance) for y in (bottom, top)]
            boxes.append((min(point[0] for point in points) - float(page.cropbox.left),
                          float(page.cropbox.top) - max(point[1] for point in points),
                          max(point[0] for point in points) - float(page.cropbox.left),
                          float(page.cropbox.top) - min(point[1] for point in points)))
            cursor += advance
        fragments.append((value, boxes))

    page.extract_text(visitor_text=visit)

    # Поиск старого движка соединял соседние куски одной строки: прочерк подписи
    # в Word PDF бывает разбит между двумя text-run. Восстанавливаем только этот
    # горизонтальный layout, не переписывая PDF-парсер.
    # ponytail: вертикальный/RTL-маркер не поддержан; при его появлении перейти
    # на glyph-level layout API pypdf, когда такой публичный API появится.
    lines = []
    for value, boxes in fragments:
        x0 = min(box[0] for box in boxes)
        y0 = min(box[1] for box in boxes)
        x1 = max(box[2] for box in boxes)
        y1 = max(box[3] for box in boxes)
        if lines:
            line = lines[-1]
            overlap = min(line["y1"], y1) - max(line["y0"], y0)
            tolerance = max(1.0, min(line["y1"] - line["y0"], y1 - y0) * 0.25)
            if overlap > 0 and x0 >= line["x1"] - tolerance:
                gap = x0 - line["x1"]
                separator = ("" if gap <= 1 or line["text"].endswith(" ")
                             or value.startswith(" ") else " ")
                line["text"] += separator + value
                line["boxes"] += [None] * len(separator) + boxes
                line["x1"] = max(line["x1"], x1)
                line["y0"], line["y1"] = min(line["y0"], y0), max(line["y1"], y1)
                continue
        lines.append({"text": value, "boxes": boxes, "x1": x1, "y0": y0, "y1": y1})

    hits = {marker: [] for marker in markers}
    for line in lines:
        for marker in markers:
            start = line["text"].find(marker)
            while start >= 0:
                span = [box for box in line["boxes"][start:start + len(marker)] if box]
                if span:
                    hits[marker].append((min(box[0] for box in span),
                                         min(box[1] for box in span)))
                start = line["text"].find(marker, start + len(marker))

    for marker in markers:
        for hit_x0, hit_y0 in hits[marker]:
            if hit_y0 > lower_third:
                # Размещаем подпись чуть выше текста, справа. Ширину держим в
                # поле: без зажима подпись у фамилии в правой колонке вылезала
                # за обрез листа (правое поле документа — 15 мм ≈ 42 пункта).
                width, right_margin = 160, 42
                x0 = max(hit_x0, page_width * 0.55)
                x0 = min(x0, page_width - right_margin - width)
                y0 = hit_y0 - 35
                return x0, y0, x0 + width, y0 + 50

    # Дефолт: правый нижний угол (последняя страница)
    return page_width * 0.55, page_height * 0.82, page_width * 0.88, page_height * 0.87


def _stamp(page, sign_path, rect):
    from PIL import Image
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    x0, y0, x1, y1 = rect
    with Image.open(sign_path) as image:
        iw, ih = image.size
    scale = min((x1 - x0) / iw, (y1 - y0) / ih)
    width, height = iw * scale, ih * scale
    rotation = page.rotation % 360
    x_origin = float(page.cropbox.left) if rotation == 0 else 0.0
    y_origin = (float(page.cropbox.top) if rotation == 0
                else float(page.cropbox.height))
    x = x_origin + x0 + (x1 - x0 - width) / 2
    y = y_origin - y1 + (y1 - y0 - height) / 2

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(float(page.mediabox.width), float(page.mediabox.height)),
                        pageCompression=1)
    pdf.drawImage(sign_path, x, y, width, height, mask="auto")
    pdf.showPage()
    pdf.save()
    overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    resources = overlay.get("/Resources")
    if resources:
        resources.get_object().pop("/Font", None)  # image-only overlay must not add Helvetica
    page.merge_page(overlay, over=True)
    return width, 0.0, 0.0, height, x, y


def overlay_signature(pdf_path: str, sign_path: str) -> bool:
    """Наложить подпись PNG на последнюю страницу PDF."""
    from pypdf import PdfReader, PdfWriter

    if not os.path.exists(sign_path):
        print(f"Подпись не найдена: {sign_path}")
        print("Положи PNG подписи в: cases/_assets/подпись.png")
        return False

    source = PdfReader(pdf_path)
    doc = PdfWriter(clone_from=source)
    doc.pdf_header = source.pdf_header

    # Word нередко оставляет в хвосте пустую страницу — на ней только колонцифра.
    # Подписывать ее нельзя (подпись уедет с листа с реквизитами), и подавать
    # документ с пустым листом тоже нельзя: удаляем хвост до последней с текстом.
    def is_blank(page):
        words = [w for w in (page.extract_text() or "").split() if not w.isdigit()]
        return not words

    while len(doc.pages) > 1 and is_blank(doc.pages[-1]):
        print(f"Удалена пустая последняя страница ({len(doc.pages)}).")
        doc.remove_page(len(doc.pages) - 1)

    last_page = doc.pages[-1]
    rect = find_signature_rect(last_page)
    _stamp(last_page, sign_path, rect)
    pages = len(doc.pages)
    # Инкрементальная запись поверх себя не переживает удаление страниц —
    # пишем во временный файл рядом и подменяем.
    out_tmp = pdf_path + ".tmp"
    doc.write(out_tmp)
    os.replace(out_tmp, pdf_path)
    print(f"Подпись наложена: стр. {pages} → Rect{rect}")
    return True



def font_pt_serif():
    """Путь к PT Serif: сначала тот, что приехал С ПРИБОРОМ, потом системный.

    Шрифт везется в репозитории (assets/fonts, SIL OFL, файл лицензии рядом) по
    решению владельца 03.09.2026. Причина замером: изолированный прогон гейта
    публикации показал, что на чужой машине шрифта нет вовсе - установщик его не
    ставил, а прибор требовал. Документ на подпись без него не собрать, и дефект
    был не виден только потому, что на машине владельца шрифт лежал с давних пор.

    Порядок намеренный: своя копия важнее системной. Системную кто угодно может
    подменить другой версией, а подпись обязана ложиться в те же координаты, в
    каких ее проверяли.
    """
    from pathlib import Path as _P
    koren = _P(__file__).resolve().parent.parent
    kandidaty = [
        koren / "assets" / "fonts" / "PT_Serif-Web-Regular.ttf",
        _P.home() / "Library/Fonts/PT_Serif-Web-Regular.ttf",
        _P.home() / ".local/share/fonts/PT_Serif-Web-Regular.ttf",
    ]
    for k in kandidaty:
        if k.is_file():
            return k
    return None


def selftest():
    from pathlib import Path
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ContentStream, RectangleObject
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    blocked = "fi" + "tz"
    targets = (Path(__file__), Path(__file__).with_name("pdf-kit.py"))
    assert all(blocked not in path.read_text(encoding="utf-8").lower() for path in targets), \
        "source:no-agpl-engine"

    font_path = font_pt_serif()
    assert font_path is not None, "font:PT Serif"
    pdfmetrics.registerFont(TTFont("M10PTSerif", str(font_path)))

    def geometry(page):
        return ([float(v) for v in page.mediabox], [float(v) for v in page.cropbox],
                page.rotation)

    def fonts(page):
        resources = page.get("/Resources") or {}
        return sorted(str(font.get_object().get("/BaseFont"))
                      for font in (resources.get("/Font") or {}).values())

    def image_matrix(reader, page):
        last = None
        found = []
        for operands, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"cm":
                last = tuple(float(v) for v in operands)
            elif operator == b"Do" and last:
                found.append(last)
        return found[-1]

    with tempfile.TemporaryDirectory(prefix="m10_sign_") as tmp:
        root = Path(tmp)
        base = root / "base.pdf"
        pdf = canvas.Canvas(str(base), pagesize=(600, 800))
        pdf.setFont("M10PTSerif", 12)
        pdf.drawString(50, 750, "DOCUMENT PT Serif")
        pdf.drawString(350, 90, "Представитель")
        pdf.showPage()
        pdf.setFont("M10PTSerif", 12)
        pdf.drawString(300, 30, "2")
        pdf.showPage()
        pdf.save()
        signature = root / "signature.png"
        Image.new("RGBA", (500, 226), (0, 0, 0, 0)).save(signature)

        before = PdfReader(base)
        rect = find_signature_rect(before.pages[0])
        legacy_rect = (350.0, 662.531982421875, 510.0, 712.531982421875)
        rect_delta = max(abs(a - b) for a, b in zip(rect, legacy_rect))
        assert rect_delta < 0.001, f"find:geometry delta={rect_delta}"
        before_geometry, before_fonts = geometry(before.pages[0]), fonts(before.pages[0])
        assert any("PTSerif" in name for name in before_fonts), "font:PT Serif missing"

        signed = root / "signed.pdf"
        shutil.copy2(base, signed)
        assert overlay_signature(str(signed), str(signature)), "overlay:false"
        after = PdfReader(signed)
        assert after.pdf_header == before.pdf_header, "overlay:pdf-version"
        assert len(after.pages) == 1, "blank-tail:not-removed"
        assert geometry(after.pages[0]) == before_geometry, "overlay:page-geometry"
        assert fonts(after.pages[0]) == before_fonts, "overlay:fonts-changed"
        actual = image_matrix(after, after.pages[0])
        legacy_image = (110.61947, 0.0, 0.0, 50.0, 374.69029, 87.46802)
        image_delta = max(abs(a - b) for a, b in zip(actual, legacy_image))
        assert image_delta < 0.001, f"overlay:geometry delta={image_delta}"

        split = root / "split-marker.pdf"
        pdf = canvas.Canvas(str(split), pagesize=(600, 800))
        pdf.setFont("M10PTSerif", 12)
        pdf.drawString(350, 90, "_")
        pdf.drawString(350 + pdfmetrics.stringWidth("_", "M10PTSerif", 12), 90,
                       "_____________ / SIGNER")
        pdf.showPage()
        pdf.save()
        split_rect = find_signature_rect(PdfReader(split).pages[0])
        split_delta = max(abs(a - b) for a, b in zip(split_rect, legacy_rect))
        assert split_delta < 0.001, f"find:split-marker delta={split_delta}"

        legacy_rotations = {
            0: ((340.0, 642.531982421875, 500.0, 692.531982421875),
                (110.61947, 0.0, 0.0, 50.0, 374.69029, 87.46802)),
            90: ((418.0, 642.531982421875, 578.0, 692.531982421875),
                 (110.61947, 0.0, 0.0, 50.0, 442.69029, 67.46802)),
            180: ((340.0, 642.531982421875, 500.0, 692.531982421875),
                  (110.61947, 0.0, 0.0, 50.0, 364.69029, 67.46802)),
            270: ((418.0, 642.531982421875, 578.0, 692.531982421875),
                  (110.61947, 0.0, 0.0, 50.0, 442.69029, 67.46802)),
        }
        rotation_deltas = []
        for rotation, (legacy_rect, legacy_matrix) in legacy_rotations.items():
            source = PdfReader(base)
            writer = PdfWriter()
            page = source.pages[0]
            page.cropbox = RectangleObject((10, 20, 590, 780))
            if rotation:
                page.rotate(rotation)
            variant = root / f"rotation-{rotation}.pdf"
            writer.add_page(page)
            writer.write(variant)
            variant_reader = PdfReader(variant)
            actual_rect = find_signature_rect(variant_reader.pages[0])
            rotation_deltas.append(max(abs(a - b) for a, b in zip(actual_rect, legacy_rect)))
            before_geometry = geometry(variant_reader.pages[0])
            before_fonts = fonts(variant_reader.pages[0])
            assert overlay_signature(str(variant), str(signature)), "rotation:overlay-false"
            result = PdfReader(variant)
            assert result.pdf_header == variant_reader.pdf_header, "rotation:pdf-version"
            rotation_deltas.append(max(abs(a - b) for a, b in zip(
                image_matrix(result, result.pages[0]), legacy_matrix)))
            assert geometry(result.pages[0]) == before_geometry, "rotation:page-geometry"
            assert fonts(result.pages[0]) == before_fonts, "rotation:fonts-changed"
        rotation_delta = max(rotation_deltas)
        assert rotation_delta < 0.001, f"rotation:geometry delta={rotation_delta}"
    print(f"selftest OK: rect delta={rect_delta:.6f} pt; split delta={split_delta:.6f} pt; "
          f"image delta={image_delta:.6f} pt; "
          f"rotation delta={rotation_delta:.6f} pt; PT Serif intact")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--no-sign", action="store_true", help="Только PDF, без подписи")
    ap.add_argument("--sign-only", action="store_true", help="Только наложить подпись (PDF уже есть)")
    a = ap.parse_args()

    docx_path = os.path.abspath(a.docx)
    pdf_path = docx_path.replace(".docx", ".pdf")

    if not a.sign_only:
        if not os.path.exists(docx_path):
            print(f"Файл не найден: {docx_path}")
            sys.exit(1)
        print(f"Конвертирую: {os.path.basename(docx_path)} → PDF...")
        ok = docx_to_pdf_via_word(docx_path, pdf_path)
        if not ok:
            print("Ошибка конвертации.")
            sys.exit(2)
        print(f"PDF создан: {pdf_path}")

    if not a.no_sign:
        sign_path = os.path.abspath(SIGNATURE_PATH)
        print(f"Накладываю подпись...")
        overlay_signature(pdf_path, sign_path)

    print(f"Готово: {pdf_path}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    main()
