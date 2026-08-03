#!/usr/bin/env python3
"""Проверка .docx глазами без Word.

QuickLook macOS рендерит только первую страницу, а Word на этой машине занят
рабочей сессией владельца и требует ручного разрешения доступа. Обходим:
делаем копию документа с очень высокой страницей — весь текст попадает на
одну «полосу» и виден целиком одним изображением. Оригинал не трогаем.

Использование: render_qa.py файл.docx [ширина_рендера]
"""
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.shared import Mm


def main():
    src = Path(sys.argv[1]).resolve()
    size = sys.argv[2] if len(sys.argv) > 2 else "1500"
    qa_dir = src.parent / "_qa"
    qa_dir.mkdir(exist_ok=True)

    doc = Document(str(src))
    for section in doc.sections:
        section.page_height = Mm(int(sys.argv[3]) if len(sys.argv) > 3 else 2200)
    tall = qa_dir / f"tall_{src.name}"
    doc.save(str(tall))

    for old in qa_dir.glob(f"{tall.name}*.png"):
        old.unlink()
    subprocess.run(["qlmanage", "-t", "-s", size, "-o", str(qa_dir), str(tall)],
                   check=True, capture_output=True)
    out = qa_dir / f"{tall.name}.png"
    print(out if out.exists() else "РЕНДЕР НЕ СОЗДАН")


if __name__ == "__main__":
    main()
