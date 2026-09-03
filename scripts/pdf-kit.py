#!/usr/bin/env python3
"""
pdf-kit — локальный PDF-тулкит для юрпрактики. $0, без Docker, на pypdf/pikepdf/reportlab.
OCR делает Apple Vision (vision-ocr) — здесь НЕ дублируется.

Команды:
  merge OUT.pdf IN1 IN2 ...        сшить файлы (PDF+картинки) в один PDF ПО ПОРЯДКУ
  compress IN.pdf OUT.pdf          сжать PDF (deflate + чистка дублей)
  img2pdf OUT.pdf IMG1 IMG2 ...    картинки → один PDF (по порядку)
  sign IN.pdf OUT.pdf SIG.png      наложить подпись-картинку: --page N (1-based, def посл.) --x --y (доля 0..1, def 0.62/0.78) --w (доля ширины, def 0.25)
  extract IN.pdf [OUT.md]          PDF → markdown (markitdown) для изучения в Мнемозине
  pages IN.pdf                     инфо: число страниц, размер

Пути с пробелами/юникодом — в кавычках. Документы остаются ЛОКАЛЬНО (ничего не уходит наружу).
"""
import io
import os
import subprocess
import sys

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif", ".gif")


def _image_pdf(path):
    from PIL import Image, ImageOps, ImageSequence
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(1, 1), pageCompression=1)
    converted = None
    try:
        try:
            source = Image.open(path)
        except OSError:
            if os.path.splitext(path)[1].lower() not in (".heic", ".heif") \
                    or not os.path.exists("/usr/bin/sips"):
                raise
            import tempfile
            converted = tempfile.TemporaryDirectory(prefix="m10_heic_")
            converted_path = os.path.join(converted.name, "image.png")
            result = subprocess.run(
                ["/usr/bin/sips", "-s", "format", "png", path, "--out", converted_path],
                capture_output=True, text=True,
            )
            if result.returncode:
                raise SystemExit("img2pdf: HEIC не прочитан: " + result.stderr[:300])
            source = Image.open(converted_path)
        with source:
            default_dpi = source.info.get("dpi", (96, 96))
            frames = ImageSequence.Iterator(source) if source.format == "TIFF" else (source,)
            for raw in frames:
                dpi = raw.info.get("dpi", default_dpi)
                dpi = dpi if isinstance(dpi, (tuple, list)) else (dpi, dpi)
                xdpi, ydpi = (max(1, round(float(v or 96))) for v in dpi[:2])
                frame = ImageOps.exif_transpose(raw.copy())
                width, height = frame.width * 72 / xdpi, frame.height * 72 / ydpi
                pdf.setPageSize((width, height))
                pdf.drawImage(ImageReader(frame), 0, 0, width, height, mask="auto")
                pdf.showPage()
    finally:
        if converted:
            converted.cleanup()
    pdf.save()
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    writer = PdfWriter()
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources:
            resources.get_object().pop("/Font", None)
        writer.add_page(page)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def _add_file_to_writer(writer, path):
    from pypdf import PdfReader

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        reader = PdfReader(path)
    elif ext in IMG_EXT:
        reader = PdfReader(io.BytesIO(_image_pdf(path)))
    else:
        raise SystemExit(f"merge: не поддержан тип {ext} ({os.path.basename(path)}). PDF/картинки.")
    for page in reader.pages:
        writer.add_page(page)


def _page_size(page):
    width, height = float(page.cropbox.width), float(page.cropbox.height)
    return (height, width) if page.rotation % 180 else (width, height)


def _stamp(page, image_path, rect):
    from PIL import Image
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    x0, y0, x1, y1 = rect
    with Image.open(image_path) as image:
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
    pdf.drawImage(image_path, x, y, width, height, mask="auto")
    pdf.showPage()
    pdf.save()
    overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    resources = overlay.get("/Resources")
    if resources:
        resources.get_object().pop("/Font", None)  # image-only overlay must not add Helvetica
    page.merge_page(overlay, over=True)
    return width, 0.0, 0.0, height, x, y

def cmd_merge(args):
    from pypdf import PdfWriter

    out, ins = args[0], args[1:]
    if not ins:
        raise SystemExit("merge OUT.pdf IN1 IN2 ...")
    writer = PdfWriter()
    writer.pdf_header = "%PDF-1.7"
    for p in ins:
        if not os.path.exists(p):
            raise SystemExit(f"нет файла: {p}")
        _add_file_to_writer(writer, p)
    writer.write(out)
    print(f"OK merge → {out} ({len(ins)} файлов по порядку)")

def cmd_img2pdf(args):
    cmd_merge(args)  # merge уже принимает картинки

def cmd_compress(args):
    src, out = args[0], args[1]
    before = os.path.getsize(src)
    try:
        import pikepdf
    except ImportError:
        from pypdf import PdfReader, PdfWriter
        source = PdfReader(src)
        writer = PdfWriter(clone_from=source)
        writer.pdf_header = source.pdf_header
        for page in writer.pages:
            page.compress_content_streams()
        writer.compress_identical_objects()
        writer.write(out)
    else:
        with pikepdf.open(src) as pdf:
            pdf.save(out, compress_streams=True, recompress_flate=True,
                     force_version=pdf.pdf_version)
    after = os.path.getsize(out)
    pct = round((1 - after / before) * 100) if before else 0
    print(f"OK compress → {out} ({before//1024}KB → {after//1024}KB, -{pct}%)")

def cmd_sign(args):
    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    opts = {a.split("=")[0]: a.split("=")[1] for a in args if a.startswith("--") and "=" in a}
    pos = [a for a in args if not a.startswith("--")]
    src, out, sig = pos[0], pos[1], pos[2]
    source = PdfReader(src)
    writer = PdfWriter(clone_from=source)
    writer.pdf_header = source.pdf_header
    page_n = int(opts.get("--page", len(writer.pages)))  # 1-based; def последняя
    if not 1 <= page_n <= len(writer.pages):
        raise SystemExit(f"sign: страница вне диапазона 1..{len(writer.pages)}")
    page = writer.pages[page_n - 1]
    page_width, page_height = _page_size(page)
    fx = float(opts.get("--x", 0.62)); fy = float(opts.get("--y", 0.78)); fw = float(opts.get("--w", 0.25))
    with Image.open(sig) as image:
        iw, ih = image.size
    w = page_width * fw
    h = w * ih / iw
    x0 = page_width * fx; y0 = page_height * fy
    _stamp(page, sig, (x0, y0, x0 + w, y0 + h))
    writer.write(out)
    print(f"OK sign → {out} (стр {page_n}, подпись {os.path.basename(sig)})")

def cmd_extract(args):
    src = args[0]
    out = args[1] if len(args) > 1 else os.path.splitext(src)[0] + ".md"
    r = subprocess.run(["markitdown", src], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("markitdown error: " + r.stderr[:300])
    open(out, "w").write(r.stdout)
    print(f"OK extract → {out} ({len(r.stdout)} симв, для Мнемозины)")

def cmd_pages(args):
    from pypdf import PdfReader
    print(f"{os.path.basename(args[0])}: {len(PdfReader(args[0]).pages)} стр, {os.path.getsize(args[0])//1024}KB")


def selftest():
    import tempfile
    from pathlib import Path
    from unittest import mock
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ContentStream
    from reportlab.pdfgen import canvas

    blocked = "fi" + "tz"
    targets = (Path(__file__), Path(__file__).with_name("sign_and_pdf.py"))
    assert all(blocked not in path.read_text(encoding="utf-8").lower() for path in targets), \
        "source:no-agpl-engine"

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

    with tempfile.TemporaryDirectory(prefix="m10_pdf_kit_") as tmp:
        root = Path(tmp)
        first, second = root / "first.pdf", root / "second.pdf"
        pdf = canvas.Canvas(str(first), pagesize=(600, 800))
        pdf.drawString(40, 760, "FIRST"); pdf.showPage(); pdf.save()
        pdf = canvas.Canvas(str(second), pagesize=(400, 300))
        pdf.drawString(40, 260, "SECOND"); pdf.showPage(); pdf.save()
        reader = PdfReader(second)
        writer = PdfWriter()
        page = reader.pages[0]
        page.rotate(90)
        page.cropbox.lower_left, page.cropbox.upper_right = (10, 20), (390, 280)
        writer.add_page(page)
        writer.write(second)
        picture = root / "page.png"
        Image.new("RGB", (120, 80), "white").save(picture, dpi=(96, 96))
        jpeg = root / "page.jpg"
        Image.new("RGB", (120, 80), "white").save(jpeg, dpi=(300, 300))
        frames = [Image.new("RGB", (120, 80), color) for color in ("red", "blue")]
        tiff, gif = root / "pages.tiff", root / "pages.gif"
        frames[0].save(tiff, save_all=True, append_images=frames[1:], dpi=(144, 144))
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
        signature = root / "signature.png"
        Image.new("RGBA", (500, 226), (0, 0, 0, 0)).save(signature)

        image_expectations = {
            jpeg: [([0.0, 0.0, 28.8, 19.2], [0.0, 0.0, 28.8, 19.2], 0)],
            tiff: [([0.0, 0.0, 60.0, 40.0], [0.0, 0.0, 60.0, 40.0], 0)] * 2,
            gif: [([0.0, 0.0, 90.0, 60.0], [0.0, 0.0, 90.0, 60.0], 0)],
        }
        for image, expected_geometry in image_expectations.items():
            image_pdf = PdfReader(io.BytesIO(_image_pdf(image)))
            assert [geometry(page) for page in image_pdf.pages] == expected_geometry, \
                f"image:geometry {image.suffix}"
            assert all(not fonts(page) for page in image_pdf.pages), \
                f"image:fonts {image.suffix}"

        heic = root / "page.heic"
        heic.write_bytes(b"unsupported without native decoder")
        real_exists = os.path.exists

        def fake_sips(command, **_kwargs):
            assert command[:4] == ["/usr/bin/sips", "-s", "format", "png"]
            Image.new("RGB", (120, 80), "white").save(command[-1], dpi=(96, 96))
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch("os.path.exists", side_effect=lambda path: (
                True if path == "/usr/bin/sips" else real_exists(path))), \
                mock.patch("subprocess.run", side_effect=fake_sips):
            heic_pdf = PdfReader(io.BytesIO(_image_pdf(heic)))
        assert [geometry(page) for page in heic_pdf.pages] == [
            ([0.0, 0.0, 90.0, 60.0], [0.0, 0.0, 90.0, 60.0], 0)
        ], "image:geometry .heic"
        assert all(not fonts(page) for page in heic_pdf.pages), "image:fonts .heic"

        merged = root / "merged.pdf"
        cmd_merge([str(merged), str(first), str(second), str(picture)])
        result = PdfReader(merged)
        assert result.pdf_header == "%PDF-1.7", "merge:pdf-version"
        expected = [geometry(PdfReader(first).pages[0]), geometry(PdfReader(second).pages[0]),
                    ([0.0, 0.0, 90.0, 60.0], [0.0, 0.0, 90.0, 60.0], 0)]
        assert [geometry(page) for page in result.pages] == expected, "merge:page-geometry"
        assert result.pages[0].extract_text().strip() == "FIRST", "merge:order-1"
        assert result.pages[1].extract_text().strip() == "SECOND", "merge:order-2"
        assert [fonts(page) for page in result.pages] == [
            fonts(PdfReader(first).pages[0]), fonts(PdfReader(second).pages[0]), []
        ], "merge:fonts"

        compressed = root / "compressed.pdf"
        cmd_compress([str(first), str(compressed)])
        source, result = PdfReader(first, strict=True), PdfReader(compressed, strict=True)
        assert result.pdf_header == source.pdf_header, "compress:pdf-version"
        assert [geometry(page) for page in result.pages] == [
            geometry(page) for page in source.pages
        ], "compress:page-geometry"
        assert [fonts(page) for page in result.pages] == [
            fonts(page) for page in source.pages
        ], "compress:fonts"
        assert [page.extract_text() for page in result.pages] == [
            page.extract_text() for page in source.pages
        ], "compress:text"

        probe = root / "probe.pdf"
        pdf = canvas.Canvas(str(probe), pagesize=(400, 300))
        pdf.drawString(20, 30, "PROBE"); pdf.showPage(); pdf.save()
        legacy_matrices = {
            0: (95.0, 0.0, 0.0, 42.94, 86.0, 159.06),
            90: (65.0, 0.0, 0.0, 29.38, 52.0, 116.619998),
            180: (95.0, 0.0, 0.0, 42.94, 76.0, 139.06),
            270: (65.0, 0.0, 0.0, 29.38, 52.0, 116.619998),
        }
        deltas = []
        for rotation, legacy in legacy_matrices.items():
            source = PdfReader(probe)
            writer = PdfWriter()
            page = source.pages[0]
            page.cropbox.lower_left, page.cropbox.upper_right = (10, 20), (390, 280)
            if rotation:
                page.rotate(rotation)
            base = root / f"sign-{rotation}.pdf"
            writer.add_page(page)
            writer.write(base)
            signed = root / f"signed-{rotation}.pdf"
            cmd_sign([str(base), str(signed), str(signature), "--page=1", "--x=0.2",
                      "--y=0.3", "--w=0.25"])
            result = PdfReader(signed)
            assert result.pdf_header == PdfReader(base).pdf_header, \
                f"sign:pdf-version rotation={rotation}"
            delta = max(abs(a - b) for a, b in zip(
                image_matrix(result, result.pages[0]), legacy))
            deltas.append(delta)
            assert delta < 0.001, f"sign:geometry rotation={rotation} delta={delta}"
            assert geometry(result.pages[0]) == geometry(PdfReader(base).pages[0]), \
                f"sign:page-geometry rotation={rotation}"
        delta = max(deltas)
    print(f"selftest OK: merge order/rotate/boxes/fonts; image formats; compress; "
          f"sign max delta={delta:.6f} pt")
    return 0

CMDS = {"merge": cmd_merge, "img2pdf": cmd_img2pdf, "compress": cmd_compress,
        "sign": cmd_sign, "extract": cmd_extract, "pages": cmd_pages}

if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__); sys.exit(0 if len(sys.argv) < 2 else 1)
    CMDS[sys.argv[1]](sys.argv[2:])
