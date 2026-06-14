#!/usr/bin/env python3
"""
Конвертер vozrazhenie_na_isk.md → vozrazhenie_na_isk.docx

Читает markdown, парсит структуру и создает DOCX через DocBuilder.
"""

import re
import sys
from pathlib import Path
from scripts.create_docx import DocBuilder

def read_md(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def parse_md_sections(content: str) -> list:
    """Разбить markdown на секции: пустые строки, headers, body."""
    lines = content.split('\n')
    sections = []
    current = []

    for line in lines:
        if not line.strip():
            if current:
                sections.append('\n'.join(current))
                current = []
        else:
            current.append(line)

    if current:
        sections.append('\n'.join(current))

    return sections

def process_court_header(text: str) -> dict:
    """Извлечь из шапки реквизиты суда и сторон."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    court_name = ""
    case_number = ""
    parties = []
    current_party = None

    for line in lines:
        if 'районный суд' in line.lower():
            court_name = line.replace('В ', '').strip()
        elif 'Дело №' in line:
            case_number = line.replace('Дело №', '').strip()
        elif '**Административный' in line:
            if current_party:
                parties.append(current_party)
            current_party = {'label': line.replace('**', '').replace(':', '').strip() + ':', 'lines': []}
        elif current_party and line:
            # Это строка в свойствах стороны
            is_bold = '**' in line
            clean = line.replace('**', '').strip()
            if clean:
                current_party['lines'].append((clean, is_bold))

    if current_party:
        parties.append(current_party)

    return {'court': court_name, 'case': case_number, 'parties': parties}

def main():
    md_path = "cases/example/case-2026/03_drafts/vozrazhenie_na_isk.md"
    out_path = "cases/example/case-2026/03_drafts/vozrazhenie_na_isk.docx"

    content = read_md(md_path)

    b = DocBuilder()

    # Парсинг шапки
    header_match = re.match(r'^(.+?)\n---', content, re.DOTALL)
    if header_match:
        header_text = header_match.group(1)
        header_info = process_court_header(header_text)

        b.add_header_table(
            court_name=header_info['court'] or "ЛАИШЕВСКИЙ РАЙОННЫЙ СУД РЕСПУБЛИКИ ТАТАРСТАН",
            court_route="",
            parties=header_info['parties'],
            case_number=header_info['case'],
        )
        b.add_empty()

    # Извлечь основной текст после шапки
    main_content = re.sub(r'^.+?\n---\s*\n', '', content, count=1, flags=re.DOTALL)

    # Парсить основной контент
    lines = main_content.split('\n')
    i = 0

    current_body = []

    while i < len(lines):
        line = lines[i]

        # Заголовок (# ВОЗРАЖЕНИЕ)
        if re.match(r'^# ', line):
            if current_body:
                b.add_body(current_body)
                current_body = []
            b.add_title(line.replace('# ', '').strip())

        # Подзаголовок (## на административное...)
        elif re.match(r'^## ', line):
            if current_body:
                b.add_body(current_body)
                current_body = []
            b.add_subtitle(line.replace('## ', '').strip())

        # Секция (### I. НАРУШЕНИЕ...)
        elif re.match(r'^### ', line):
            if current_body:
                b.add_body(current_body)
                current_body = []
            b.add_section(line.replace('### ', '').strip())

        # Подсекция (#### Обстоятельства.)
        elif re.match(r'^#### ', line):
            if current_body:
                b.add_body(current_body)
                current_body = []
            b.add_body(('', False))  # разделитель
            label = line.replace('#### ', '').strip()
            b.add_body([(label, True)])

        # Полужирный параграф (**Обстоятельства.** текст...)
        elif '**' in line and line.startswith('**'):
            if current_body:
                b.add_body(current_body)
                current_body = []
            # Парсить жирный текст + остаток
            parts = []
            text = line
            while '**' in text:
                idx = text.index('**')
                if idx > 0:
                    parts.append((text[:idx], False))
                text = text[idx+2:]
                if '**' in text:
                    idx = text.index('**')
                    parts.append((text[:idx], True))
                    text = text[idx+2:]
                else:
                    parts.append((text, True))
                    text = ""
            if text:
                parts.append((text, False))
            b.add_body(parts)

        # Обычный параграф
        elif line.strip() and not line.startswith('---') and not line.startswith('['):
            current_body.append(line.strip())

        # Маркированный список
        elif line.strip().startswith('- '):
            if current_body:
                b.add_body(current_body)
                current_body = []
            item = line.strip()[2:]
            b.add_body(item)

        # Таблица - пропустить для простоты
        elif line.strip().startswith('|'):
            pass

        i += 1

    if current_body:
        b.add_body(current_body)

    # Подпись
    b.add_empty()
    b.add_signature("Ахметгалиев А.Р.", "10.06.2026")

    b.save(out_path)
    print(f"✓ Создано: {out_path}")

if __name__ == '__main__':
    main()
