#!/usr/bin/env python3
"""
pdf-kit — локальный PDF-тулкит для юрпрактики. $0, без Docker, на PyMuPDF (fitz).
OCR делает Apple Vision (vision-ocr) — здесь НЕ дублируется.

Команды:
  merge OUT.pdf IN1 IN2 ...        сшить файлы (PDF+картинки) в один PDF ПО ПОРЯДКУ
  compress IN.pdf OUT.pdf          сжать PDF (deflate + чистка мусора + сабсет шрифтов)
  img2pdf OUT.pdf IMG1 IMG2 ...    картинки → один PDF (по порядку)
  sign IN.pdf OUT.pdf SIG.png      наложить подпись-картинку: --page N (1-based, def посл.) --x --y (доля 0..1, def 0.62/0.78) --w (доля ширины, def 0.25)
  extract IN.pdf [OUT.md]          PDF → markdown (markitdown) для изучения в Мнемозине
  pages IN.pdf                     инфо: число страниц, размер

Пути с пробелами/юникодом — в кавычках. Документы остаются ЛОКАЛЬНО (ничего не уходит наружу).
"""
import sys, os, subprocess

def _fitz():
    import fitz
    return fitz

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif", ".gif")

def _add_file_to_doc(doc, fitz, path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        with fitz.open(path) as src:
            doc.insert_pdf(src)
    elif ext in IMG_EXT:
        img = fitz.open(path)
        rect = img[0].rect
        pdfbytes = img.convert_to_pdf()
        img.close()
        with fitz.open("pdf", pdfbytes) as imgpdf:
            doc.insert_pdf(imgpdf)
    else:
        raise SystemExit(f"merge: не поддержан тип {ext} ({os.path.basename(path)}). PDF/картинки.")

def cmd_merge(args):
    fitz = _fitz()
    out, ins = args[0], args[1:]
    if not ins:
        raise SystemExit("merge OUT.pdf IN1 IN2 ...")
    doc = fitz.open()
    for p in ins:
        if not os.path.exists(p):
            raise SystemExit(f"нет файла: {p}")
        _add_file_to_doc(doc, fitz, p)
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    print(f"OK merge → {out} ({len(ins)} файлов по порядку)")

def cmd_img2pdf(args):
    cmd_merge(args)  # merge уже принимает картинки

def cmd_compress(args):
    fitz = _fitz()
    src, out = args[0], args[1]
    before = os.path.getsize(src)
    doc = fitz.open(src)
    doc.save(out, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
    doc.close()
    after = os.path.getsize(out)
    pct = round((1 - after / before) * 100) if before else 0
    print(f"OK compress → {out} ({before//1024}KB → {after//1024}KB, -{pct}%)")

def cmd_sign(args):
    fitz = _fitz()
    opts = {a.split("=")[0]: a.split("=")[1] for a in args if a.startswith("--") and "=" in a}
    pos = [a for a in args if not a.startswith("--")]
    src, out, sig = pos[0], pos[1], pos[2]
    doc = fitz.open(src)
    page_n = int(opts.get("--page", len(doc)))  # 1-based; def последняя
    page = doc[page_n - 1]
    pr = page.rect
    fx = float(opts.get("--x", 0.62)); fy = float(opts.get("--y", 0.78)); fw = float(opts.get("--w", 0.25))
    from PIL import Image
    iw, ih = Image.open(sig).size
    w = pr.width * fw
    h = w * ih / iw
    x0 = pr.width * fx; y0 = pr.height * fy
    page.insert_image(fitz.Rect(x0, y0, x0 + w, y0 + h), filename=sig, keep_proportion=True, overlay=True)
    doc.save(out, garbage=4, deflate=True)
    doc.close()
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
    fitz = _fitz()
    doc = fitz.open(args[0])
    print(f"{os.path.basename(args[0])}: {len(doc)} стр, {os.path.getsize(args[0])//1024}KB")
    doc.close()

CMDS = {"merge": cmd_merge, "img2pdf": cmd_img2pdf, "compress": cmd_compress,
        "sign": cmd_sign, "extract": cmd_extract, "pages": cmd_pages}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__); sys.exit(0 if len(sys.argv) < 2 else 1)
    CMDS[sys.argv[1]](sys.argv[2:])
