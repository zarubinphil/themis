#!/usr/bin/env python3
"""M09 — прогон корпуса cases/*.pdf: СТАРЫЙ путь MuPDF против шипнутого кода.

База сравнения — то, как PDF читался ДО замены движка: fitz (MuPDF) постранично.
Новый путь — markdown_extract.pdf_perpage_chars как он шипнут: pypdfium2 основной,
ПОСТРАНИЧНЫЙ запасной MuPDF с правилом минимума (решение владельца 02.09.2026,
круг 4): dec = min(PDFium, MuPDF), текст — у движка-победителя, каждый замененный
маршрут объявлен в stderr.

Для каждого материала: симв. ДО/ПОСЛЕ, сторона порога TEXT_MIN документа
(text/scan/mixed) и число страниц, флипнувших сторону. Идентификатор — SHA-256
относительного пути (10 знаков): имена файлов дел в вывод НЕ идут, объявления
запасного пути обезличиваются тем же идентификатором.

Вывод: JSON в файл из argv[1] (без него - stdout), прогресс в stderr.
"""
import os
import sys
import io
import json
import hashlib
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import markdown_extract as m

assert m.TEXT_MIN == 40, "порог TEXT_MIN сдвинут — прогон недействителен"


def side_of(per):
    t = [c >= m.TEXT_MIN for c in per]
    if all(t):
        return "text"
    if not any(t):
        return "scan"
    return "mixed"


def mupdf_perpage(path):
    """СТАРЫЙ путь: как PDF читался до замены — MuPDF (fitz) постранично."""
    import fitz  # noqa: старый движок, снят как база сравнения, не как маршрут
    doc = fitz.open(path)
    per = [len(doc[i].get_text().strip()) for i in range(len(doc))]
    doc.close()
    return per


def main():
    pdfs = []
    for root, _dirs, files in os.walk("cases"):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(root, f))
    pdfs.sort()

    rows, errors = [], []
    for k, path in enumerate(pdfs, 1):
        rel = os.path.relpath(path)
        hid = hashlib.sha256(rel.encode()).hexdigest()[:10]
        try:
            old = mupdf_perpage(path)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                new, mupdf_pages = m.pdf_perpage_chars(path, hid)
            ann = buf.getvalue().strip()
            # обезличивание: полный путь и имя файла -> идентификатор
            # (объявления уже несут только hid — замена страхует, а не спасает)
            ann = ann.replace(path, f"<{hid}>").replace(os.path.basename(path), f"<{hid}>")
        except Exception as ex:  # noqa: BLE001 — фиксируем и идем дальше
            errors.append({"id": hid, "error": str(ex)[:200]})
            continue
        n = max(len(old), len(new))
        old_pad = old + [0] * (n - len(old))
        new_pad = new + [0] * (n - len(new))
        page_flips = sum(1 for a, b in zip(old_pad, new_pad)
                         if (a >= m.TEXT_MIN) != (b >= m.TEXT_MIN))
        # Две стороны потери РАЗДЕЛЕНЫ: критерий приемки (б) требует нуля только
        # в одну сторону. lost_ocr — страница была сканом у старой базы MuPDF, а
        # новый путь считает ее текстовой: OCR бы НЕ запустился, фактура теряется.
        # gained_ocr — обратное: множество сканов расширилось, это разрешено
        # (Apple Vision локальный, тратится время, не фактура).
        lost_ocr = sum(1 for a, b in zip(old_pad, new_pad)
                       if a < m.TEXT_MIN <= b)
        gained_ocr = sum(1 for a, b in zip(old_pad, new_pad)
                         if b < m.TEXT_MIN <= a)
        rows.append({
            "id": hid,
            "pages": len(old),
            "old_chars": sum(old),
            "new_chars": sum(new),
            "old_side": side_of(old) if old else "scan",
            "new_side": side_of(new) if new else "scan",
            "page_flips": page_flips,
            "lost_ocr": lost_ocr,
            "gained_ocr": gained_ocr,
            "old_per": old,
            "new_per": new,
            "mupdf_text_pages": len(mupdf_pages),
            "fallback": bool(ann),
            "announce": ann,
        })
        if k % 50 == 0:
            print(f"  …{k}/{len(pdfs)}", file=sys.stderr)

    flips = [r for r in rows if r["old_side"] != r["new_side"]]
    pflips = [r for r in rows if r["page_flips"]]
    out = json.dumps({
        "total": len(pdfs), "read": len(rows), "errors": errors,
        "text_min": m.TEXT_MIN,
        # свод по двум сторонам потери — то, что требует критерий приемки (б)
        "pages_lost_ocr": sum(r["lost_ocr"] for r in rows),
        "pages_gained_ocr": sum(r["gained_ocr"] for r in rows),
        "materials_lost_ocr": [r["id"] for r in rows if r["lost_ocr"]],
        "pages_text_from_mupdf": sum(r["mupdf_text_pages"] for r in rows),
        "materials_text_from_mupdf": [r["id"] for r in rows if r["mupdf_text_pages"]],
        "doc_side_flips": flips,
        "page_flip_materials": pflips,
        "fallback_used": [r for r in rows if r["fallback"]],
        # сырая улика целиком: каждый материал корпуса, постранично, оба движка
        "rows": rows,
    }, ensure_ascii=False, indent=1)
    # Улика идет В ФАЙЛ, а не в stdout: fitz печатает «warning: The 'fitz' API is
    # deprecated» именно в stdout, и прошлый прогон сохранил в json ровно эту
    # строку (99 байт) вместо результата. Труба тут молча портит улику.
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"улика записана: {sys.argv[1]} ({len(out)} байт)", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
