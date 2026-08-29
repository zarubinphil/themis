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
    Возвращает fitz.Rect для размещения подписи.
    """
    import fitz
    page_height = page.rect.height
    page_width = page.rect.width
    lower_third = page_height * 0.65

    # Ищем маркеры подписи. Фамилия подписанта — первый маркер: в судебных
    # документах Фемиды блок подписи это строка таблицы «дата | | Фамилия»,
    # ни «С уважением», ни прочерков в ней нет.
    markers = ["Зарубин", "Зарубина", "С уважением", "Представитель",
               "___________", "Подпись", "подпись"]
    for marker in markers:
        hits = page.search_for(marker)
        for hit in hits:
            if hit.y0 > lower_third:
                # Размещаем подпись чуть выше текста, справа. Ширину держим в
                # поле: без зажима подпись у фамилии в правой колонке вылезала
                # за обрез листа (правое поле документа — 15 мм ≈ 42 пункта).
                width, right_margin = 160, 42
                x0 = max(hit.x0, page_width * 0.55)
                x0 = min(x0, page_width - right_margin - width)
                y0 = hit.y0 - 35
                return fitz.Rect(x0, y0, x0 + width, y0 + 50)

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

    # Word нередко оставляет в хвосте пустую страницу — на ней только колонцифра.
    # Подписывать ее нельзя (подпись уедет с листа с реквизитами), и подавать
    # документ с пустым листом тоже нельзя: удаляем хвост до последней с текстом.
    def is_blank(page):
        words = [w for w in page.get_text().split() if not w.isdigit()]
        return not words

    while doc.page_count > 1 and is_blank(doc[-1]):
        print(f"Удалена пустая последняя страница ({doc.page_count}).")
        doc.delete_page(doc.page_count - 1)

    last_page = doc[-1]
    rect = find_signature_rect(last_page)
    last_page.insert_image(rect, filename=sign_path, overlay=True)
    pages = len(doc)          # считать ДО close: закрытый документ длины не имеет
    # Инкрементальная запись поверх себя не переживает удаление страниц —
    # пишем во временный файл рядом и подменяем.
    out_tmp = pdf_path + ".tmp"
    doc.save(out_tmp, garbage=3, deflate=True)
    doc.close()
    os.replace(out_tmp, pdf_path)
    print(f"Подпись наложена: стр. {pages} → {rect}")
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
