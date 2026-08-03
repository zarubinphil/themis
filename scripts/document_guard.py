#!/usr/bin/env python3
"""document_guard.py — формат .docx и согласованность .md/.docx машиной, не глазами.

Блок Б6 у проверяющего был инструкцией «распечатать поля и кегли и посмотреть» —
то есть сравнение со спецификацией делала модель, тратя токены на арифметику и
регулярно давая ложные тревоги. Здесь то же сравнение делает код.

Проверяет по .claude/skills/doc-drafter/DOCX_FORMATTING.md:
  поля 20/30/30/15 мм (L3 и кассация ВС — левое 35); шрифты PT Serif (тело),
  Golos Text (заголовки и шапка), PT Mono (числовые колонки) — набор утвержден
  владельцем 03.08.2026, иные гарнитуры недопустимы; кегли 14/13/12/11,
  межстрочный 1.15, абзацный отступ 1.25 см, тело JUSTIFY, отсутствие курсива
  и подчеркивания, нумерация страниц — БЕЗУСЛОВНО в каждом документе.
Плюс: текст .docx совпадает с .md, приложения пронумерованы сквозно и каждое
упомянуто в тексте, запрещенная буква «ё», даты в формате ДД.ММ.ГГГГ.

    document_guard.py ДОКУМЕНТ.docx [--md ДОКУМЕНТ.md] [--l3]
    document_guard.py --selftest

Код возврата: 0 — чисто, 1 — есть нарушения.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SPEC_MARGINS_MM = {"top": 20, "bottom": 30, "left": 30, "right": 15}
SPEC_MARGINS_MM_L3 = {**SPEC_MARGINS_MM, "left": 35}
# Утверждено владельцем 03.08.2026. Три свободные гарнитуры (SIL OFL) с родной
# кириллицей; заодно закрывают ГОСТ Р 7.0.97-2016 п. 3.3 о бесплатных шрифтах.
SPEC_FONT_BODY = "PT Serif"
SPEC_FONT_DISPLAY = "Golos Text"
SPEC_FONT_MONO = "PT Mono"
SPEC_FONTS = {SPEC_FONT_BODY, SPEC_FONT_DISPLAY, SPEC_FONT_MONO}
SPEC_FONT = SPEC_FONT_BODY  # обратная совместимость
SPEC_SIZES = {11.0, 12.0, 13.0, 14.0}
SPEC_LINE_SPACING = 1.15
SPEC_INDENT_CM = 1.25
TOLERANCE_MM = 1.0


def check_docx(path: str, l3: bool = False) -> list[str]:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    problems: list[str] = []
    doc = Document(path)
    sec = doc.sections[0]
    spec = SPEC_MARGINS_MM_L3 if l3 else SPEC_MARGINS_MM
    got = {"top": sec.top_margin.mm, "bottom": sec.bottom_margin.mm,
           "left": sec.left_margin.mm, "right": sec.right_margin.mm}
    for side, want in spec.items():
        if abs(got[side] - want) > TOLERANCE_MM:
            problems.append(f"поле {side}: {got[side]:.0f} мм вместо {want} мм"
                            + (" (L3/кассация ВС)" if l3 and side == "left" else ""))

    fonts, sizes, italic, underline = set(), set(), 0, 0
    for p in doc.paragraphs:
        for r in p.runs:
            if r.font.name:
                fonts.add(r.font.name)
            if r.font.size:
                sizes.add(round(r.font.size.pt, 1))
            if r.font.italic:
                italic += 1
            if r.font.underline:
                underline += 1
    alien_fonts = fonts - SPEC_FONTS
    if alien_fonts:
        problems.append(f"чужие шрифты: {', '.join(sorted(alien_fonts))} "
                        f"(допустимы только {', '.join(sorted(SPEC_FONTS))})")
    alien_sizes = sizes - SPEC_SIZES
    if alien_sizes:
        problems.append(f"кегли вне спецификации: {sorted(alien_sizes)} "
                        f"(допустимы {sorted(SPEC_SIZES)})")
    if italic:
        problems.append(f"курсив в {italic} фрагментах — спецификация запрещает")
    if underline:
        problems.append(f"подчеркивание в {underline} фрагментах — спецификация запрещает")

    body = [p for p in doc.paragraphs if len(p.text.strip()) > 80]
    not_justified = [p for p in body
                     if p.paragraph_format.alignment not in (WD_ALIGN_PARAGRAPH.JUSTIFY, None)]
    if body and len(not_justified) > len(body) * 0.2:
        problems.append(f"тело не по ширине: {len(not_justified)} из {len(body)} длинных абзацев "
                        "выровнены иначе (нужен JUSTIFY)")

    spacings = {round(p.paragraph_format.line_spacing, 2) for p in body
                if p.paragraph_format.line_spacing}
    bad_spacing = spacings - {SPEC_LINE_SPACING}
    if bad_spacing:
        problems.append(f"межстрочный интервал {sorted(bad_spacing)} вместо {SPEC_LINE_SPACING}")

    # Отрицательный отступ первой строки — не ошибка, если это висячий отступ
    # нумерованного абзаца: left_indent положителен и гасит его ровно. Такой
    # абзац дает точную ссылку «пункт 14» и введен протоколом 03.08.2026.
    bad_indent = set()
    for par in body:
        fi = par.paragraph_format.first_line_indent
        if fi is None:
            continue
        fi_cm = round(fi.cm, 2)
        li = par.paragraph_format.left_indent
        li_cm = round(li.cm, 2) if li is not None else 0.0
        if fi_cm < 0 and abs(li_cm + fi_cm) <= 0.05 and li_cm > 0:
            continue  # корректный висячий отступ
        if abs(fi_cm - SPEC_INDENT_CM) > 0.05:
            bad_indent.add(fi_cm)
    if bad_indent:
        problems.append(f"абзацный отступ {sorted(bad_indent)} см вместо {SPEC_INDENT_CM} см "
                        "(висячий отступ нумерованного абзаца засчитывается, если "
                        "левый отступ гасит его ровно)")

    text = "\n".join(p.text for p in doc.paragraphs)
    problems += check_text(text, os.path.basename(path))

    # Нумерация страниц — безусловное требование протокола (решение владельца
    # 03.08.2026). Порога по объему больше нет: короткий документ тоже может
    # разойтись на две страницы после правки, а незамеченная потеря нумерации
    # обнаруживается уже в суде. DocBuilder.save() ставит поле сам — отсутствие
    # поля означает, что документ собран мимо DocBuilder.
    # Поле живет в колонтитуле — это ОТДЕЛЬНАЯ часть пакета .docx, и в
    # doc.element.xml его нет никогда. Прежняя проверка искала только там,
    # поэтому на корректном документе давала ложную тревогу, а на собранном
    # мимо DocBuilder — не давала никакой (баг найден 03.08.2026).
    parts = [doc.element.xml]
    for sec in doc.sections:
        for area in (sec.header, sec.footer, sec.first_page_header,
                     sec.first_page_footer, sec.even_page_header,
                     sec.even_page_footer):
            try:
                parts.append(area._element.xml)
            except Exception:
                pass
    if not any("PAGE" in x for x in parts):
        problems.append("нет поля номера страницы — нумерация обязательна в каждом "
                        "документе без исключений (протокол 03.08.2026)")
    return problems


def check_text(text: str, where: str) -> list[str]:
    problems = []
    yo = len(re.findall(r"[ёЁ]", text))
    if yo:
        problems.append(f"{where}: буква «ё» встречается {yo} раз — в документах проекта запрещена")
    bad_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", text)
    if bad_dates:
        problems.append(f"{where}: даты не в формате ДД.ММ.ГГГГ: {', '.join(sorted(set(bad_dates))[:5])}")
    placeholders = re.findall(r"\{\{[^}]+\}\}|\bХХХ\b|\bXXX\b|\[ЗАПОЛНИТЬ[^\]]*\]", text)
    if placeholders:
        problems.append(f"{where}: незаполненные плейсхолдеры: {', '.join(sorted(set(placeholders))[:5])}")
    # Ряд подчеркиваний — это либо забытый пропуск, либо законное место подписи
    # в договоре. Машине их не различить, поэтому отдельная мягкая строка.
    blanks = len(re.findall(r"_{4,}", text))
    if blanks:
        problems.append(f"{where}: подчеркнутых пропусков {blanks} — в договоре норма (место "
                        "подписи), в процессуальном документе означает незаполненное поле")
    return problems


def check_attachments(text: str) -> list[str]:
    """Приложения: сквозная нумерация и упоминание каждого в тексте документа."""
    m = re.search(r"(?im)^\s*(?:Приложени[ея]|ПРИЛОЖЕНИ[ЕЯ])\s*:?\s*$", text)
    if not m:
        return []
    tail = text[m.end():]
    body = text[:m.start()]
    nums = [int(x) for x in re.findall(r"(?m)^\s*(\d{1,2})[.)]\s+\S", tail)]
    problems = []
    if not nums:
        return ["раздел «Приложения» есть, но перечень пуст или не пронумерован"]
    expected = list(range(1, len(nums) + 1))
    if nums != expected:
        problems.append(f"нумерация приложений не сквозная: {nums} (ожидалось {expected})")
    mentioned = set(int(x) for x in re.findall(r"(?:приложени[ияюе]\s*№?\s*(\d{1,2}))", body, re.I))
    missing = [n for n in nums if n not in mentioned]
    if missing and mentioned:
        problems.append(f"приложения {missing} не упомянуты в тексте документа")
    return problems


def check_md_vs_docx(md_path: str, docx_path: str) -> list[str]:
    """Два файла — один документ. Разошлись — подадут не то, что проверяли."""
    from docx import Document

    def norm(s: str) -> str:
        s = re.sub(r"[*_`#>\|\-]+", " ", s)
        return re.sub(r"\s+", " ", s).strip().lower()

    md = norm(open(md_path, encoding="utf-8", errors="replace").read())
    dx = norm("\n".join(p.text for p in Document(docx_path).paragraphs))
    problems = []
    ratio = len(dx) / max(len(md), 1)
    if not 0.7 <= ratio <= 1.4:
        problems.append(f"объем .docx отличается от .md в {ratio:.2f} раза "
                        f"({len(dx)} против {len(md)} знаков) — файлы разошлись")
    # ключевые числа: сумма из .md обязана быть в .docx
    md_nums = set(re.findall(r"\b\d[\d\s]{4,}\b", md))
    lost = [n for n in md_nums if re.sub(r"\s", "", n) not in re.sub(r"\s", "", dx)]
    if lost:
        problems.append(f"числа из .md отсутствуют в .docx: {', '.join(sorted(lost)[:5])}")
    return problems


def _add_page_field(doc):
    """Поле PAGE в верхнем колонтитуле — как это делает DocBuilder."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    header = doc.sections[0].header
    header.is_linked_to_previous = False
    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), " PAGE ")
    p._p.append(fld)
    return p


def selftest() -> int:
    import tempfile
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt, Cm

    tmp = tempfile.mkdtemp()

    def build(path, *, font=SPEC_FONT_BODY, size=12, left=30, italic=False, spacing=1.15,
              indent=1.25, pages=True,
              text="Текст документа, достаточно длинный абзац для проверки выравнивания."):
        d = Document()
        s = d.sections[0]
        s.top_margin, s.bottom_margin = Mm(20), Mm(30)
        s.left_margin, s.right_margin = Mm(left), Mm(15)
        p = d.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.line_spacing = spacing
        p.paragraph_format.first_line_indent = Cm(indent)
        r = p.add_run(text * 2)
        r.font.name, r.font.size, r.font.italic = font, Pt(size), italic
        if pages:
            _add_page_field(d)
        d.save(path)
        return path

    good = build(os.path.join(tmp, "good.docx"))
    no_pages = build(os.path.join(tmp, "nopage.docx"), pages=False)
    bad_font = build(os.path.join(tmp, "font.docx"), font="Arial")
    bad_margin = build(os.path.join(tmp, "margin.docx"), left=25)
    l3_ok = build(os.path.join(tmp, "l3.docx"), left=35)
    with_italic = build(os.path.join(tmp, "it.docx"), italic=True)
    with_yo = build(os.path.join(tmp, "yo.docx"), text="Ежик все ещё идет в суд. ")

    md_ok = os.path.join(tmp, "ok.md")
    open(md_ok, "w", encoding="utf-8").write(
        "Текст документа, достаточно длинный абзац для проверки выравнивания." * 2)
    md_other = os.path.join(tmp, "other.md")
    open(md_other, "w", encoding="utf-8").write("Совсем другой короткий текст. Сумма 1 250 000 руб.")

    att_ok = ("Прошу приобщить приложение 1 и приложение 2.\n\nПриложения:\n"
              "1. Договор\n2. Квитанция\n")
    att_gap = "Прошу приобщить приложение 1.\n\nПриложения:\n1. Договор\n3. Квитанция\n"

    checks = [
        ("корректный документ проходит", check_docx(good) == []),
        ("чужой шрифт пойман", any("шрифт" in p for p in check_docx(bad_font))),
        ("поле не по спецификации поймано", any("поле left" in p for p in check_docx(bad_margin))),
        ("L3 с полем 35 проходит при --l3", check_docx(l3_ok, l3=True) == []),
        ("L3-поле без флага считается ошибкой", any("поле left" in p for p in check_docx(l3_ok))),
        ("курсив пойман", any("курсив" in p for p in check_docx(with_italic))),
        ("буква ё поймана", any("«ё»" in p for p in check_docx(with_yo))),
        ("отсутствие нумерации страниц поймано",
         any("номера страницы" in p for p in check_docx(no_pages))),
        ("совпадающие md и docx проходят", check_md_vs_docx(md_ok, good) == []),
        ("разошедшиеся md и docx пойманы", check_md_vs_docx(md_other, good) != []),
        ("сквозная нумерация приложений проходит", check_attachments(att_ok) == []),
        ("дыра в нумерации приложений поймана", any("сквозная" in p for p in check_attachments(att_gap))),
        ("плейсхолдер пойман", any("плейсхолдер" in p for p in check_text("Сумма {{amount}}", "t"))),
        ("дата не того формата поймана", any("ДД.ММ.ГГГГ" in p for p in check_text("2026-08-03", "t"))),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка формата .docx и согласованности с .md")
    ap.add_argument("docx", nargs="?", help="путь к .docx")
    ap.add_argument("--md", help="парный .md для сверки")
    ap.add_argument("--l3", action="store_true", help="L3 или кассация ВС: левое поле 35 мм")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.docx:
        ap.print_help()
        return 2
    if os.path.basename(a.docx).startswith("~$"):
        print(f"{a.docx} — временный файл Word, а не документ. Проверять нечего.", file=sys.stderr)
        return 2
    if not os.path.isfile(a.docx):
        print(f"нет файла {a.docx}", file=sys.stderr)
        return 2

    problems = check_docx(a.docx, a.l3)
    from docx import Document
    text = "\n".join(p.text for p in Document(a.docx).paragraphs)
    problems += check_attachments(text)
    if a.md:
        problems += check_md_vs_docx(a.md, a.docx)

    if not problems:
        print(f"✓ {os.path.basename(a.docx)}: формат и согласованность в порядке")
        return 0
    print(f"⚠ {os.path.basename(a.docx)}: нарушений {len(problems)}")
    for p in problems:
        print(f"   • {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
