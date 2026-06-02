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
import subprocess
import argparse

SIGNATURE_PATH = os.path.join(os.path.dirname(__file__), "../cases/_assets/подпись.png")

def docx_to_pdf_via_word(docx_path: str, pdf_path: str) -> bool:
    """Конвертация через Microsoft Word (AppleScript)."""
    docx_abs = os.path.abspath(docx_path)
    pdf_abs = os.path.abspath(pdf_path)
    script = f'''
tell application "Microsoft Word"
    set theDoc to open POSIX file "{docx_abs}"
    set thePath to POSIX file "{pdf_abs}" as string
    save as theDoc file name thePath file format format PDF
    close theDoc saving no
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"Word ошибка: {result.stderr.strip()}")
        return False
    return os.path.exists(pdf_path)


def find_signature_rect(page):
    """
    Найти блок подписи на странице.
    Ищет текст 'С уважением' или 'Представитель' или '__________' в нижней трети страницы.
    Возвращает fitz.Rect для размещения подписи.
    """
    import fitz
    page_height = page.rect.height
    page_width = page.rect.width
    lower_third = page_height * 0.65

    # Ищем маркеры подписи
    markers = ["С уважением", "Представитель", "___________", "Подпись", "подпись"]
    for marker in markers:
        hits = page.search_for(marker)
        for hit in hits:
            if hit.y0 > lower_third:
                # Размещаем подпись чуть выше текста, справа
                x0 = max(hit.x0, page_width * 0.55)
                y0 = hit.y0 - 35
                x1 = x0 + 160
                y1 = y0 + 50
                return fitz.Rect(x0, y0, x1, y1)

    # Дефолт: правый нижний угол (последняя страница)
    return fitz.Rect(page_width * 0.55, page_height * 0.82, page_width * 0.88, page_height * 0.87)


def overlay_signature(pdf_path: str, sign_path: str) -> bool:
    """Наложить подпись PNG на последнюю страницу PDF."""
    try:
        import fitz
    except ImportError:
        print("PyMuPDF не установлен: pip install pymupdf")
        return False

    if not os.path.exists(sign_path):
        print(f"Подпись не найдена: {sign_path}")
        print("Положи PNG подписи в: cases/_assets/подпись.png")
        return False

    doc = fitz.open(pdf_path)
    last_page = doc[-1]
    rect = find_signature_rect(last_page)
    last_page.insert_image(rect, filename=sign_path, overlay=True)
    doc.save(pdf_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    print(f"Подпись наложена: стр. {len(doc)} → {rect}")
    return True


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
    main()
