#!/usr/bin/env python3
"""
Конвертер vozrazhenie_na_isk.md → vozrazhenie_na_isk.docx

Читает markdown, парсит структуру и создает DOCX через DocBuilder.

    python3 scripts/md_to_docx_vozrazhenie.py [ВХОД.md [ВЫХОД.docx]]

Без аргументов — пути по умолчанию (разовая сборка возражения). Сборка,
как у каждого входа конвейера, стоит под вердиктом Кони: DocBuilder.save()
откажет без одобренной редакции .md.
"""

import re
import os
import sys
from pathlib import Path

# Та же строка, что в md_to_docx.py: без sys.path на корень репозитория
# «from scripts.create_docx import …» падает с ModuleNotFoundError, и прибор
# мертв — проверка отказа сборки без вердикта вакуумна (этап 9.19, круг 9).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.create_docx import DocBuilder  # noqa: E402

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
    # Аргументы командной строки, как у остальных конвертеров конвейера:
    # вход .md и выход .docx; без них — пути по умолчанию разовой сборки.
    md_path = (sys.argv[1] if len(sys.argv) > 1 else
               "cases/example/case-2026/.agent/drafts/vozrazhenie_na_isk.md")
    out_path = (sys.argv[2] if len(sys.argv) > 2 else
                "cases/example/case-2026/.agent/drafts/vozrazhenie_na_isk.docx")

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
                b.add_body([(t, False) for t in current_body])
                current_body = []
            b.add_title(line.replace('# ', '').strip())

        # Подзаголовок (## на административное...)
        elif re.match(r'^## ', line):
            if current_body:
                b.add_body([(t, False) for t in current_body])
                current_body = []
            b.add_subtitle(line.replace('## ', '').strip())

        # Секция (### I. НАРУШЕНИЕ...)
        elif re.match(r'^### ', line):
            if current_body:
                b.add_body([(t, False) for t in current_body])
                current_body = []
            b.add_section(line.replace('### ', '').strip())

        # Подсекция (#### Обстоятельства.)
        elif re.match(r'^#### ', line):
            if current_body:
                b.add_body([(t, False) for t in current_body])
                current_body = []
            b.add_body([('', False)])  # разделитель
            label = line.replace('#### ', '').strip()
            b.add_body([(label, True)])

        # Полужирный параграф (**Обстоятельства.** текст...)
        elif '**' in line and line.startswith('**'):
            if current_body:
                b.add_body([(t, False) for t in current_body])
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
                b.add_body([(t, False) for t in current_body])
                current_body = []
            item = line.strip()[2:]
            b.add_body(item)

        # Таблица - пропустить для простоты
        elif line.strip().startswith('|'):
            pass

        i += 1

    if current_body:
        b.add_body([(t, False) for t in current_body])

    # Подпись
    b.add_empty()
    # Подписант не зашивается в код публичного репозитория: фамилия доверителя —
    # персональные данные. Передавать окружением при сборке конкретного документа.
    b.add_signature(os.environ.get("THEMIS_SIGNER", "Представитель по доверенности"),
                    os.environ.get("THEMIS_SIGN_DATE", ""))

    # DocBuilder.save() при отказе печатает причину и молча возвращает None, файл
    # не пишет. «Создано» после отказа = ложь: юрист идёт за несуществующим
    # документом. Успех обязан следовать за фактом записи — свежим mtime файла.
    before = os.path.getmtime(out_path) if os.path.exists(out_path) else None
    b.save(out_path)
    after = os.path.getmtime(out_path) if os.path.exists(out_path) else None
    if after is not None and after != before:
        print(f"✓ Создано: {out_path}")
        return 0
    print(f"✗ НЕ создано: {out_path} — сборщик отказал (причина выше).", file=sys.stderr)
    return 1

if __name__ == '__main__':
    sys.exit(main())
