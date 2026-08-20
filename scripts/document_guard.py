#!/usr/bin/env python3
"""document_guard.py — формат .docx и согласованность .md/.docx машиной, не глазами.

Блок Б6 у проверяющего был инструкцией «распечатать поля и кегли и посмотреть» —
то есть сравнение со спецификацией делала модель, тратя токены на арифметику и
регулярно давая ложные тревоги. Здесь то же сравнение делает код.

Проверяет по .claude/skills/doc-drafter/DOCX_FORMATTING.md:
  поля 20/30/30/15 мм (L3 и кассация ВС — левое 35); ОДНА гарнитура PT Serif на
  весь документ (решение владельца 04.08.2026, иные гарнитуры недопустимы);
  кегли 14/13/12/11, межстрочный 1.15, абзацный отступ 1.25 см, тело JUSTIFY,
  отсутствие курсива и подчеркивания, номер страницы — БЕЗУСЛОВНО в каждом
  документе и БЕЗУСЛОВНО в нижнем колонтитуле.
Плюс: текст .docx совпадает с .md, приложения пронумерованы сквозно и каждое
упомянуто в тексте, запрещенная буква «ё», даты в формате ДД.ММ.ГГГГ.
Плюс (этап 9.8, требование владельца): денежная сумма пишется цифрами и тут
же прописью в КРУГЛЫХ скобках — «1 000 (одна тысяча) рублей» — и пропись
обязана СОВПАДАТЬ с числом (сверяет `scripts/propis.py`, свой конвертер
числительных). Прописи требует только сумма денег: даты, статьи, номера дел,
ИНН, проценты и листы дела её не требуют.

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
# Договор и прочие несудебные документы: нижнее поле 30 мм не нужно — зона
# штампа экспедиции суда там ни при чем, а поля симметричнее и читать удобнее.
# Шрифты, кегли, интервалы и нумерация страниц остаются общими для всего.
SPEC_MARGINS_MM_DOGOVOR = {"top": 20, "bottom": 20, "left": 25, "right": 20}
# Бланк договора адвокатского центра (DOCX_FORMATTING.md §8). Полоса набора 185 мм:
# уже — и таблицы фирменной шапки сжимаются, заголовки начинают рваться по слогам.
# Профиль установлен правкой владельца 20.08.2026 на договоре оказания услуг.
SPEC_MARGINS_MM_ADVOKAT = {"top": 20, "bottom": 20, "left": 15, "right": 10}
SPEC_SIZES_ADVOKAT = {7.0, 8.0, 9.0, 9.5, 10.0, 11.0, 12.0}
SPEC_LINE_SPACING_ADVOKAT = 1.0
# Бланк несет два отступа: 1,0 см у преамбул и 1,25 см у трех абзацев-продолжений
# внутри пунктов. Оба пришли из фирменного образца, править их — ломать бланк.
SPEC_INDENT_CM_ADVOKAT = {1.0, 1.25}
# ОДНА гарнитура на документ — PT Serif. Решение владельца 04.08.2026, отменяет
# набор из четырех гарнитур от 03.08.2026. PT Serif свободен (SIL OFL), кириллица
# родная; закрывает ГОСТ Р 7.0.97-2016 п. 3.3 о бесплатных шрифтах. Любая другая
# гарнитура в документе — нарушение: практика обязана выглядеть одинаково.
SPEC_FONT_BODY = "PT Serif"
SPEC_FONT_DISPLAY = SPEC_FONT_BODY
SPEC_FONT_MONO = SPEC_FONT_BODY
SPEC_FONT_TITLE = SPEC_FONT_BODY
SPEC_FONTS = {SPEC_FONT_BODY}
SPEC_FONT = SPEC_FONT_BODY  # обратная совместимость
SPEC_SIZES = {11.0, 12.0, 13.0, 14.0}
SPEC_LINE_SPACING = 1.15
SPEC_INDENT_CM = 1.25
TOLERANCE_MM = 1.0


def iter_paragraphs(doc):
    """Все абзацы документа: тело, таблицы (рекурсивно) и колонтитулы.

    `doc.paragraphs` возвращает ТОЛЬКО абзацы верхнего уровня. Шапка процессуального
    документа по нашему же стандарту — плавающая таблица, поэтому проверка шрифтов,
    кеглей и дат её не видела, а сверка .md/.docx давала ложное «числа из .md
    отсутствуют в .docx»: числа лежали в ячейках. Найдено аудитом 03.08.2026.
    """
    def walk_container(container):
        for par in getattr(container, "paragraphs", []):
            yield par
        for table in getattr(container, "tables", []):
            for row in table.rows:
                for cell in row.cells:
                    yield from walk_container(cell)

    yield from walk_container(doc)
    for sec in doc.sections:
        for area in (sec.header, sec.footer, sec.first_page_header,
                     sec.first_page_footer, sec.even_page_header, sec.even_page_footer):
            try:
                yield from walk_container(area)
            except (AttributeError, ValueError):
                continue


def doc_text(doc) -> str:
    return "\n".join(p.text for p in iter_paragraphs(doc))


def _theme_fonts(doc) -> dict:
    """Гарнитуры темы документа (theme1.xml): major/minor → имя латиницы.

    Ран со ссылкой w:asciiTheme="majorHAnsi" наследует гарнитуру темы — в
    шаблоне python-docx это Cambria/Calibri, то есть чужая гарнитура при
    внешне зеленом документе (проба круга 6). Нет темы — None, не судим."""
    out = {"major": None, "minor": None}
    try:
        for part in doc.part.package.iter_parts():
            if str(part.partname).endswith("theme1.xml"):
                xml = part.blob.decode("utf-8", "replace")
                for key, tag in (("major", "majorFont"), ("minor", "minorFont")):
                    m = re.search(rf"<a:{tag}>\s*<a:latin[^>]*typeface=\"([^\"]*)\"",
                                  xml)
                    if m and m.group(1):
                        out[key] = m.group(1)
                break
    except Exception:
        pass
    return out


def _fonts_and_sizes(doc) -> tuple[set, set]:
    """Все гарнитуры и кегли, ДЕЙСТВУЮЩИЕ на документ: раны (document +
    колонтитулы, все атрибуты rFonts включая hAnsi — кириллицу Word берет
    оттуда), стили, реально примененные в документе (и Normal как база), и
    docDefaults. Тематические ссылки (asciiTheme/hAnsiTheme) судим только на
    ранах: в docDefaults шаблона они стоят по умолчанию и перекрыты везде —
    судить их там значит браковать каждый документ (ложная тревога)."""
    from docx.oxml.ns import qn

    fonts, sizes = set(), set()
    theme = _theme_fonts(doc)

    def collect_rpr(rpr, allow_theme):
        if rpr is None:
            return
        rf = rpr.find(qn("w:rFonts"))
        if rf is not None:
            for a in ("w:ascii", "w:hAnsi", "w:cs"):
                v = rf.get(qn(a))
                if v:
                    fonts.add(v)
            if allow_theme:
                for a in ("w:asciiTheme", "w:hAnsiTheme"):
                    v = rf.get(qn(a))
                    if not v:
                        continue
                    name = theme.get("major" if v.startswith("major") else "minor")
                    if name:
                        fonts.add(name)
        sz = rpr.find(qn("w:sz"))
        if sz is not None:
            v = sz.get(qn("w:val"))
            if v and v.isdigit():
                sizes.add(round(int(v) / 2, 1))

    parts = [doc.element]
    for sec in doc.sections:
        for area in (sec.header, sec.footer, sec.first_page_header,
                     sec.first_page_footer, sec.even_page_header, sec.even_page_footer):
            try:
                parts.append(area._element)
            except (AttributeError, ValueError):
                continue
    for el in parts:
        for r in el.iter(qn("w:r")):
            collect_rpr(r.find(qn("w:rPr")), allow_theme=True)

    styles_el = doc.styles.element
    by_id = {}
    for st in styles_el.findall(qn("w:style")):
        sid = st.get(qn("w:styleId"))
        if sid:
            by_id[sid] = st
    used = {"Normal"}
    for el in parts:
        for tag in ("w:pStyle", "w:tblStyle"):
            for ps in el.iter(qn(tag)):
                v = ps.get(qn("w:val"))
                if v:
                    used.add(v)
    seen, stack = set(), list(used)
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        st = by_id.get(sid)
        if st is None:
            continue
        collect_rpr(st.find(qn("w:rPr")), allow_theme=False)
        base = st.find(qn("w:basedOn"))
        if base is not None and base.get(qn("w:val")):
            stack.append(base.get(qn("w:val")))

    dd = styles_el.find(qn("w:docDefaults"))
    if dd is not None:
        rprd = dd.find(qn("w:rPrDefault"))
        if rprd is not None:
            collect_rpr(rprd.find(qn("w:rPr")), allow_theme=False)
    return fonts, sizes


def money_text(doc) -> str:
    """Текст для денежной проверки: тело ДО перечня приложений плюс таблицы и
    колонтитулы целиком.

    Раньше срез приложений применялся к общей склейке, а `iter_paragraphs` отдаёт
    таблицы ПОСЛЕ абзацев верхнего уровня — поэтому строка «ПРИЛОЖЕНИЯ:» отрезала
    заодно все таблицы документа. Приложения есть в каждом иске, а расчёт сумм
    и цена иска в шапке живут именно в таблицах: денежная проверка не работала
    ни в одном реальном иске (проба круга 5, 20.08.2026).
    """
    telo = "\n".join(p.text for p in getattr(doc, "paragraphs", []))
    m = re.search(r"(?im)^\s*(?:Приложени[ея]|ПРИЛОЖЕНИ[ЕЯ])\s*:?\s*$", telo)
    if m:
        telo = telo[:m.start()]
    prochee = []

    def _iz_yacheek(container):
        """Абзацы ячеек, включая вложенные таблицы. iter_paragraphs сюда не годится:
        она в конце просит doc.sections, которых у ячейки нет."""
        for par in getattr(container, "paragraphs", []):
            yield par
        for tbl in getattr(container, "tables", []):
            for r in tbl.rows:
                for c in r.cells:
                    yield from _iz_yacheek(c)

    for table in getattr(doc, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                prochee.extend(par.text for par in _iz_yacheek(cell))
    for sec in doc.sections:
        for part in (sec.header, sec.footer, getattr(sec, "first_page_header", None),
                     getattr(sec, "first_page_footer", None)):
            if part is not None:
                prochee.extend(par.text for par in getattr(part, "paragraphs", []))
    return "\n".join([telo] + prochee)


def check_docx(path: str, l3: bool = False, dogovor: bool = False,
               advokat: bool = False) -> list[str]:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    problems: list[str] = []
    doc = Document(path)
    sec = doc.sections[0]
    spec = (SPEC_MARGINS_MM_ADVOKAT if advokat
            else SPEC_MARGINS_MM_DOGOVOR if dogovor
            else SPEC_MARGINS_MM_L3 if l3 else SPEC_MARGINS_MM)
    sizes_spec = SPEC_SIZES_ADVOKAT if advokat else SPEC_SIZES
    spacing_spec = SPEC_LINE_SPACING_ADVOKAT if advokat else SPEC_LINE_SPACING
    indent_spec = SPEC_INDENT_CM_ADVOKAT if advokat else {SPEC_INDENT_CM}
    got = {"top": sec.top_margin.mm, "bottom": sec.bottom_margin.mm,
           "left": sec.left_margin.mm, "right": sec.right_margin.mm}
    for side, want in spec.items():
        if abs(got[side] - want) > TOLERANCE_MM:
            problems.append(f"поле {side}: {got[side]:.0f} мм вместо {want} мм"
                            + (" (L3/кассация ВС)" if l3 and side == "left" else "")
                            + (" (бланк адвокатского центра)" if advokat else "")
                            + (" (профиль договора)" if dogovor and not advokat else ""))

    # Гарнитуру и кегль Word пишет НЕ ТОЛЬКО на ран: стиль абзаца (включая
    # Normal), docDefaults и тема документа задают их мимо ранов, а w:hAnsi —
    # тот атрибут, откуда Word берет КИРИЛЛИЦУ: ascii=PT Serif + hAnsi=Times
    # New Roman дает весь русский текст чужой гарнитурой при зеленом вердикте
    # (проба круга 6, 20.08.2026). Собираем объявления со ВСЕХ уровней,
    # которые реально действуют на этот документ.
    fonts, sizes = _fonts_and_sizes(doc)
    italic, underline = 0, 0
    for p in iter_paragraphs(doc):
        for r in p.runs:
            if r.font.italic:
                italic += 1
            if r.font.underline:
                underline += 1
    alien_fonts = fonts - SPEC_FONTS
    if alien_fonts:
        problems.append(f"чужие шрифты: {', '.join(sorted(alien_fonts))} "
                        f"(допустимы только {', '.join(sorted(SPEC_FONTS))})")
    alien_sizes = sizes - sizes_spec
    if alien_sizes:
        problems.append(f"кегли вне спецификации: {sorted(alien_sizes)} "
                        f"(допустимы {sorted(sizes_spec)})")
    if italic:
        problems.append(f"курсив в {italic} фрагментах — спецификация запрещает")
    # В бланке подчеркивание — это поля под заполнение («Тел. ______») и место
    # под подпись, а не оформление текста: запрет §3 писан для судебных документов.
    if underline and not advokat:
        problems.append(f"подчеркивание в {underline} фрагментах — спецификация запрещает")

    # Гиперссылка — отдельная ловушка: ее runs лежат внутри w:hyperlink, в
    # p.runs не попадают, и проверка выше их не видит. Рендерер оформляет их
    # стилем Hyperlink — синим с подчеркиванием — даже когда прямое
    # форматирование говорит обратное. Стандарт запрещает и то и другое
    # (DOCX_FORMATTING.md §3), поэтому ловим сам факт наличия гиперссылки
    # с видимым текстом (пропущено сторожем до 03.08.2026).
    doc_xml = doc.element.xml
    if "<w:hyperlink" in doc_xml:
        n = doc_xml.count("<w:hyperlink")
        problems.append(f"гиперссылок в тексте: {n} — рендерер красит их синим с "
                        "подчеркиванием поверх прямого форматирования; стандарт "
                        "запрещает цвет и подчеркивание. Навигацию давать обычным "
                        "текстом (add_static_toc с linked=False)")

    body = [p for p in doc.paragraphs if len(p.text.strip()) > 80]
    not_justified = [p for p in body
                     if p.paragraph_format.alignment not in (WD_ALIGN_PARAGRAPH.JUSTIFY, None)]
    if body and len(not_justified) > len(body) * 0.2:
        problems.append(f"тело не по ширине: {len(not_justified)} из {len(body)} длинных абзацев "
                        "выровнены иначе (нужен JUSTIFY)")

    spacings = {round(p.paragraph_format.line_spacing, 2) for p in body
                if p.paragraph_format.line_spacing}
    bad_spacing = spacings - {spacing_spec}
    if bad_spacing:
        problems.append(f"межстрочный интервал {sorted(bad_spacing)} вместо {spacing_spec}")

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
            continue  # корректный висячий отступ нумерованного абзаца
        if abs(fi_cm) <= 0.05 and li_cm > 0:
            continue  # блок-цитата: отступ слева есть, первой строки нет
        if all(abs(fi_cm - want) > 0.05 for want in indent_spec):
            bad_indent.add(fi_cm)
    if bad_indent:
        problems.append(f"абзацный отступ {sorted(bad_indent)} см вместо {sorted(indent_spec)} см "
                        "(висячий отступ нумерованного абзаца засчитывается, если "
                        "левый отступ гасит его ровно)")

    text = doc_text(doc)
    problems += check_text(text, os.path.basename(path), dogovor=dogovor or advokat)
    # Деньги проверяются по СТРУКТУРЕ документа, а не по склейке: перечень
    # приложений режется в теле, а таблицы и колонтитулы идут в проверку целиком.
    # Иначе строка «ПРИЛОЖЕНИЯ:» отрезала все таблицы разом (проба круга 5).
    problems = [p for p in problems if "пропис" not in p and "совпадает" not in p]
    problems += check_money_propis(money_text(doc), os.path.basename(path))

    # Нумерация страниц — безусловное требование протокола (решение владельца
    # 03.08.2026). Порога по объему больше нет: короткий документ тоже может
    # разойтись на две страницы после правки, а незамеченная потеря нумерации
    # обнаруживается уже в суде. DocBuilder.save() ставит поле сам — отсутствие
    # поля означает, что документ собран мимо DocBuilder.
    # Поле живет в колонтитуле — это ОТДЕЛЬНАЯ часть пакета .docx, и в
    # doc.element.xml его нет никогда. Прежняя проверка искала только там,
    # поэтому на корректном документе давала ложную тревогу, а на собранном
    # мимо DocBuilder — не давала никакой (баг найден 03.08.2026).
    def xml_of(areas):
        out = []
        for area in areas:
            try:
                out.append(area._element.xml)
            except Exception:
                pass
        return out

    footers, headers = [], []
    for sec in doc.sections:
        footers += xml_of((sec.footer, sec.first_page_footer, sec.even_page_footer))
        headers += xml_of((sec.header, sec.first_page_header, sec.even_page_header))
    if not any("PAGE" in x for x in footers):
        if any("PAGE" in x for x in headers):
            # Место номера — тоже часть стандарта, и проверять его должна машина.
            problems.append("номер страницы стоит в ВЕРХНЕМ колонтитуле — с 04.08.2026 "
                            "он ставится в нижнем, по центру")
        else:
            problems.append("нет поля номера страницы — нумерация обязательна в каждом "
                            "документе без исключений (протокол 03.08.2026)")
    return problems


# Денежная сумма: число (пробелы/неразрывные или точка-между-тройками как
# разряды, запятая — копейки), затем опционально пропись в круглых скобках,
# затем валюта — либо валюта последним словом внутри скобок. Якорь — слово
# валюты: без него даты, статьи, номера дел, ИНН, ставки и листы дела сюда
# не попадают, и правильно — прописи требует только сумма денег.
# Разряд склеивается ТОЛЬКО пробелом/неразрывным/узким (Word и печатные формы
# ставят U+00A0, U+202F, U+2009 между тройками — без них проверка выключается
# пробелом, проба круга 4) или точкой между тройками:
# перевод строки числа не склеивает — иначе ячейка таблицы сливается с номером
# строки, а номер счета — с суммой под ним (ложные тревоги пробы 20.08.2026).
# «руб.» с точкой — основная письменная форма («Цена иска: 1 250 000 руб.»);
# граница слова после точки не строится \b, поэтому хвост проверяется
# явным запретом буквы.
_MONEY_NUM = (r"\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:,\d{1,2})?"
              r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
              r"|\d{1,3}(?:,\d{3})+"
              r"|\d+(?:,\d{1,2})?")
# Запятая между тройками — тоже разряды («1,250,000»), а не копейки: копейки
# отделяются запятой с ОДНОЙ-двумя цифрами и без повторной тройки (проба
# круга 6, 20.08.2026). Символ валюты — та же валюта: «100 000 (пять) ₽»
# проходил мимо, потому что якорь знал только слова (та же проба).
_MONEY_CUR = r"руб(?:л[а-я]*)?\.?|коп(?:е[а-я]*)?\.?|₽"
_MONEY_RE = re.compile(
    rf"(?P<num>{_MONEY_NUM})"
    rf"(?:\s*\((?P<propis>[^()]*)\))?"
    rf"(?:\s+(?P<cur>{_MONEY_CUR}))?(?![а-яА-Я])",
    re.I)

# Сокращенная форма «500 тыс. руб.» / «12 млн руб.» — та же денежная сумма,
# и прописи она требует так же; само сокращение тысяч/миллионов в документе
# недопустимо (проба круга 4 этапа 9). Якорь-валюта обязателен: «5 тыс. штук»
# — не деньги.
_MONEY_ABBR_RE = re.compile(
    r"(?<![\dа-яА-Я])(?P<num>\d+(?:[ \u00a0\u202f\u2009.]\d{3})*(?:,\d+)?)"
    r"\s*(?P<scale>тыс|млн|млрд)\.?(?P<propis>\s*\([^()]*\))?"
    rf"\s+(?P<cur>{_MONEY_CUR})(?![а-яА-Я])",
    re.I)
_ABBR_MULT = {"тыс": 1_000, "млн": 1_000_000, "млрд": 1_000_000_000}


def _money_int(num_raw: str) -> tuple[str, str | None]:
    """Целая часть и копейки найденного числа: пробелы и точки между тройками —
    разряды, запятая — копейки. Запятая между ТРОЙКАМИ («1,250,000») — тоже
    разряды: две и более группы по три цифры после запятой копейками не бывают
    (проба круга 6, 20.08.2026)."""
    s = num_raw.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ").strip()
    kop = None
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s):
        return s.replace(",", ""), None
    if "," in s:
        s, kop = s.split(",", 1)
    s = s.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    return s, kop


def _sklonenie(n: int, odna: str, dve: str, pyat: str) -> str:
    """Форма существительного после числительного: 1 рубль, 2 рубля, 5 рублей."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return pyat
    n %= 10
    return odna if n == 1 else dve if 2 <= n <= 4 else pyat


def _sklonenie_rubl(n: int) -> str:
    return _sklonenie(n, "рубль", "рубля", "рублей")


def _sklonenie_kop(n: int) -> str:
    return _sklonenie(n, "копейка", "копейки", "копеек")


# Пропись ПЕРЕД числом — «двести тысяч (100 000) рублей» — та же
# контролирующая форма, только зеркальная: скобки тут несут цифры, а не слова.
# Без этой ветки число внутри скобок не деньги (закрывающая скобка отрезает
# его от слова валюты), и ложь о сумме проходила (проба круга 6, 20.08.2026).
_MONEY_BEFORE_PROPIS_RE = re.compile(
    rf"(?P<words>[а-яё][а-яё \-]{{1,90}}?)\(\s*(?P<num>{_MONEY_NUM})\s*\)"
    rf"\s*(?P<cur>{_MONEY_CUR})(?![а-яА-Я])",
    re.I)
# Слово-числительное: им служит хвост фразы перед скобками с цифрами.
_NUMERAL_WORD_RE = re.compile(
    r"^(?:ноль|нул\w*|один|одна|одно|одну|одного|одной|одному|одним|одном|"
    r"два|две|двух|двум|двумя|три|трех|трем|тремя|четыре\w*|пят\w*|шест\w*|"
    r"сем\w*|восем\w*|девят\w*|десят\w*|двадцат\w*|тридцат\w*|сорок\w*|"
    r"сто|ста|сот\w*|тысяч\w*|миллион\w*|миллиард\w*)$",
    re.I)


def _propis_variants(_propis, n: int, gender: str = "м") -> list[list[str]]:
    """Все шесть падежей числа словами. Сумма в документе склоняется по синтаксису
    фразы («взыскать одну тысячу», «к одной тысяче», «одной тысячей») — сверка
    обязана принимать любой падеж, но ложь ловить в каждом (проба круга 6:
    сверка знала только именительный, и просительная часть иска — «Взыскать
    1 000 (одну тысячу) рублей» — объявлялась браком)."""
    out = []
    for c in _propis.CASES:
        try:
            out.append(_propis.propis(n, gender=gender, case=c).split())
        except ValueError:
            pass
    return out


def _words_match(words: list[str], variants: list[list[str]]) -> bool:
    return any(words == v for v in variants)


def check_money_propis(text: str, where: str) -> list[str]:
    """Денежная сумма обязана нести пропись в круглых скобках, и пропись обязана
    совпадать с числом: «1 000 (сто тысяч) рублей» глазами не ловится, для того
    и прибор. Сверка — по словам целиком: «пять» внутри «пятьдесят» и префикс
    «одна тысяча» в «одна тысяча двести» совпадением не считаются. Конвертер —
    свой `scripts/propis.py`; недоступен — fail-closed. Число сверх предела
    конвертера — строка нарушения, а не трасса: до остальных проверок документа
    авария недопустима."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import propis as _propis
    except ImportError:
        return [f"{where}: scripts/propis.py недоступен — совпадение прописи "
                f"с числом НЕ проверено (fail-closed)"]
    # Перечень приложений — реквизиты прилагаемых документов, а не денежные
    # суммы документа: прописи он не несет никогда (ложная тревога круга 4).
    # Срез перечня приложений делает money_text по СТРУКТУРЕ документа: резать
    # склейку строкой нельзя — таблицы в ней идут после приложений и терялись.
    # Для вызовов по .md (плоский текст) срез остаётся здесь.
    m_app = re.search(r"(?im)^\s*(?:Приложени[ея]|ПРИЛОЖЕНИ[ЕЯ])\s*:?\s*$", text)
    if m_app and "\n" in text[m_app.end():]:
        hvost = text[m_app.end():]
        # В .md после приложений идёт только перечень; в .docx money_text уже
        # отдал тело без него, поэтому повторный срез безвреден.
        text = text[:m_app.start()] if not hvost.strip().startswith("|") else text
    # Дословная цитата нормы в кавычках-елочках воспроизводится как в законе —
    # ТРЕБОВАТЬ там пропись значит запретить цитирование (правило проекта —
    # цитировать дословно). Но пропись, которая в цитате ЕСТЬ, обязана совпадать
    # с числом: подмена суммы внутри елочек — та же ложь (проба круга 6: цитаты
    # вырезались целиком, и «сумма 100 000 (пять тысяч) рублей» в кавычках
    # проходила). Цитаты проверяются отдельным проходом в режиме «не требовать,
    # но сверять», а из основного текста вырезаются, чтобы не задвоить находки.
    quotes = [m.group(0)[1:-1] for m in re.finditer(r"«[^»]*»", text)]
    text = re.sub(r"«[^»]*»", " ", text)
    problems = []
    # Пропись ПЕРЕД числом: «двести тысяч (100 000) рублей». Хвост фразы из
    # числительных сверяется с числом в скобках; совпала — внутреннее число
    # второй раз не судим. Числительных в хвосте нет — это не форма прописи
    # (например, «расчет (100 000) рублей») — число судит общий проход ниже.
    covered = []
    for m in _MONEY_BEFORE_PROPIS_RE.finditer(text):
        words_all = re.sub(r"\s+", " ", m.group("words").strip().lower()).split()
        tail = []
        for w in reversed(words_all):
            if _NUMERAL_WORD_RE.match(w):
                tail.insert(0, w)
            else:
                break
        if not tail:
            continue
        covered.append(m.span())
        _int_before, _ = _money_int(m.group("num"))
        if not _int_before or int(_int_before) == 0:
            continue
        if not _words_match(tail, _propis_variants(_propis, int(_int_before))):
            problems.append(f"{where}: пропись «{' '.join(tail)}» НЕ совпадает "
                            f"с числом {m.group('num')} — ожидалось "
                            f"«{_propis.propis(int(_int_before))}»")
    for m in _MONEY_ABBR_RE.finditer(text):
        # «500 тыс. руб.» / «12 млн руб.» — без прописи в скобках нарушение;
        # с прописью — форма редкая, но сумма читается и сверяется глазами.
        if m.group("propis"):
            continue
        int_str, _ = _money_int(m.group("num"))
        full = int(int_str) * _ABBR_MULT[m.group("scale").lower()]
        full_show = f"{full:,}".replace(",", " ")
        problems.append(f"{where}: сумма «{m.group(0).strip()}» сокращением "
                        f"тыс./млн. — недопустимо, денежная сумма пишется "
                        f"полностью цифрами и прописью: «{full_show} "
                        f"({_propis.propis(full)}) {m.group('cur')}»")
    for m in _MONEY_RE.finditer(text):
        if any(a <= m.start() < b for a, b in covered):
            continue  # число в скобках формы «пропись (число)» — уже судили
        num_raw = m.group("num")
        words_raw = m.group("propis")
        cur = m.group("cur")
        words = re.sub(r"\s+", " ", (words_raw or "").strip().lower()).split()
        words = [w for w in words if w]
        # Якорь-валюта: снаружи скобок или последним словом внутри них —
        # «1 000 (сто тысяч рублей)» та же денежная сумма.
        has_cur = cur is not None
        if words and (words[-1].startswith("руб") or words[-1].startswith("коп")):
            has_cur = True
            words = words[:-1]
        if not has_cur:
            continue  # не деньги: дата, статья, номер дела, ИНН, ставка
        int_str, kop_str = _money_int(num_raw)
        if not int_str or int(int_str) == 0:
            continue  # «рублей 00 копеек» — нулевые копейки цифрами это обиход
        cur_show = cur or (words_raw or "").strip().split()[-1]
        # Сверка принимает ЛЮБОЙ из шести падежей (просительная часть —
        # винительный: «взыскать одну тысячу»), но ложь ловится в каждом:
        # родительный от ДРУГОГО числа — брак (проба круга 6, 20.08.2026).
        exp_rub_variants = _propis_variants(_propis, int(int_str))
        if not exp_rub_variants:
            problems.append(f"{where}: число {num_raw} не конвертируется — "
                            f"сумма осталась НЕ проверенной")
            continue
        expected = " ".join(exp_rub_variants[0])  # именительный — для подсказки
        # Подсказка обязана называть ПОЛНУЮ верную форму. Прежде она давала
        # пропись без копеек, а пропись в судебном документе — контролирующая
        # форма: исполнение подсказки меняло взыскиваемую сумму (проба круга 5).
        polnoe = expected
        if kop_str and int(kop_str):
            try:
                polnoe = (f"{expected} {_sklonenie_rubl(int(int_str))} "
                          f"{_propis.propis(int(kop_str), gender='ж')} "
                          f"{_sklonenie_kop(int(kop_str))}")
            except ValueError:
                pass
        if not words:
            problems.append(f"{where}: сумма {num_raw} {cur_show} без прописи в "
                            f"круглых скобках — денежная сумма пишется цифрами "
                            f"и прописью: «{num_raw} ({polnoe}) {cur_show}»")
            continue
        try:
            # Копейка — женский род: «одна копейка», «двадцать одна копейка» —
            # грамотный русский, а не несовпадение (ложная тревога круга 4).
            exp_kop_variants = (_propis_variants(_propis, int(kop_str), gender="ж")
                                if kop_str and int(kop_str) else None)
        except ValueError as e:
            problems.append(f"{where}: копейки {num_raw} не конвертируются ({e}) — "
                            f"сумма осталась НЕ проверенной")
            continue
        if exp_kop_variants is None:
            ok = _words_match(words, exp_rub_variants)
        else:
            # Копейки: «одна тысяча двести тридцать четыре рубля пятьдесят
            # шесть копеек» — обе части внутри одних скобок, словами целиком,
            # каждая в своем падеже.
            ok = any(
                words[:len(v)] == v and len(words) > len(v)
                and words[len(v)].startswith("руб")
                and _words_match(words[len(v) + 1:], exp_kop_variants)
                for v in exp_rub_variants)
        if not ok:
            problems.append(f"{where}: пропись «{words_raw.strip()}» НЕ совпадает "
                            f"с числом {num_raw} — ожидалось «{polnoe}»")
    for q in quotes:
        # Цитата в елочках: пропись не ТРЕБУЕТСЯ (дословная норма), но
        # присутствующая обязана совпадать с числом — подмена суммы внутри
        # кавычек та же ложь (проба круга 6, 20.08.2026).
        problems += _scan_quote(q, where, _propis)
    return problems


def _scan_quote(fragment: str, where: str, _propis) -> list[str]:
    """Проход по цитате в елочках: только СОВПАДЕНИЕ присутствующей прописи,
    без требования её наличия (дословное цитирование нормы — правило проекта)."""
    out = []
    covered = []
    for m in _MONEY_BEFORE_PROPIS_RE.finditer(fragment):
        words_all = re.sub(r"\s+", " ", m.group("words").strip().lower()).split()
        tail = []
        for w in reversed(words_all):
            if _NUMERAL_WORD_RE.match(w):
                tail.insert(0, w)
            else:
                break
        if not tail:
            continue
        covered.append(m.span())
        int_str, _ = _money_int(m.group("num"))
        if not int_str or int(int_str) == 0:
            continue
        if not _words_match(tail, _propis_variants(_propis, int(int_str))):
            out.append(f"{where}: пропись «{' '.join(tail)}» в цитате НЕ совпадает "
                       f"с числом {m.group('num')} — цитата дословна, но подмена "
                       f"суммы в ней — та же ложь")
    for m in _MONEY_RE.finditer(fragment):
        if any(a <= m.start() < b for a, b in covered):
            continue
        words_raw = m.group("propis")
        cur = m.group("cur")
        words = re.sub(r"\s+", " ", (words_raw or "").strip().lower()).split()
        words = [w for w in words if w]
        if not words:
            continue  # прописи нет — в цитате это не нарушение
        has_cur = cur is not None
        if words[-1].startswith("руб") or words[-1].startswith("коп"):
            has_cur = True
            words = words[:-1]
        if not has_cur or not words:
            continue
        int_str, kop_str = _money_int(m.group("num"))
        if not int_str or int(int_str) == 0:
            continue
        variants = _propis_variants(_propis, int(int_str))
        if not variants:
            continue
        kop_variants = None
        if kop_str and int(kop_str):
            kop_variants = _propis_variants(_propis, int(kop_str), gender="ж")
        if kop_variants is None:
            ok = _words_match(words, variants)
        else:
            ok = any(
                words[:len(v)] == v and len(words) > len(v)
                and words[len(v)].startswith("руб")
                and _words_match(words[len(v) + 1:], kop_variants)
                for v in variants)
        if not ok:
            out.append(f"{where}: пропись «{words_raw.strip()}» в цитате НЕ совпадает "
                       f"с числом {m.group('num')} — цитата дословна, но подмена "
                       f"суммы в ней — та же ложь")
    return out


def check_text(text: str, where: str, dogovor: bool = False) -> list[str]:
    problems = []
    # Двойная кодировка: UTF-8, прочитанный как latin-1 (unicode_escape и
    # прочие перекодировщики), — «Ð\x92Ð·Ñ\x8b...» вместо «Взыскать». Такой
    # документ поврежден по факту, а все проверки по тексту слепнут: якоря
    # слов (валюта, разделы) искажены до неузнаваемости (проба круга 4 —
    # сумма с узким пробелом U+202F уходила вместе с кодировкой).
    mojibake = len(re.findall(r"[ÐÑ][\x80-\xbf]", text))
    if mojibake >= 3:
        problems.append(f"{where}: текст поврежден двойной кодировкой (UTF-8, "
                        f"прочитанный как latin-1) — {mojibake} признаков; документ "
                        f"пересобрать из исходника, проверки по нему слепнут")
    yo = len(re.findall(r"[ёЁ]", text))
    if yo:
        problems.append(f"{where}: буква «ё» встречается {yo} раз — в документах проекта запрещена")
    bad_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", text)
    if bad_dates:
        problems.append(f"{where}: даты не в формате ДД.ММ.ГГГГ: {', '.join(sorted(set(bad_dates))[:5])}")
    placeholders = []
    for m in re.finditer(r"\{\{[^}]+\}\}|\bХХХ\b|\bXXX\b|\[ЗАПОЛНИТЬ[^\]]*\]", text):
        token = m.group(0)
        # «ХХХ» — действующая серия бланка полиса ОСАГО (РСА, с 2018), а не забытая
        # заглушка. Отличаем по контексту: «серия ХХХ № 0648968315». Ложная тревога
        # на подлинном реквизите приучает пролистывать вывод сторожа.
        if token in ("ХХХ", "XXX"):
            ctx = text[max(0, m.start() - 40):m.end() + 20]
            if re.search(r"сери\w*\s+(?:ХХХ|XXX)\b", ctx) or re.search(r"(?:ХХХ|XXX)\s*№\s*\d", ctx):
                continue
        placeholders.append(token)
    if placeholders:
        problems.append(f"{where}: незаполненные плейсхолдеры: {', '.join(sorted(set(placeholders))[:5])}")
    # Квадратные скобки: в российском судебном обиходе их не пишут — ни в ссылках
    # на нормы, ни в тексте. Только круглые. Решение владельца 10.08.2026.
    square = len(re.findall(r"[\[\]]", text))
    if square:
        problems.append(f"{where}: квадратные скобки — {square} шт. В документах "
                        "практики их не пишут, ссылки и вставки только в круглых")
    # Ряд подчеркиваний: в процессуальном документе — забытое поле, в договоре —
    # законное место подписи. Под флагом --dogovor это НЕ нарушение: прибор,
    # который сам пишет «в договоре норма» и тут же валит проверку, приучает
    # пролистывать его вывод, а вместе с ложной тревогой пролистают настоящую.
    blanks = len(re.findall(r"_{4,}", text))
    if blanks and not dogovor:
        problems.append(f"{where}: подчеркнутых пропусков {blanks} — в процессуальном "
                        "документе это незаполненное поле; если это договор, гнать с --dogovor")
    problems += check_money_propis(text, where)
    return problems


def docx_appendices(docx_path: str):
    """Раздел приложений из .docx: (текст до раздела, [(номер, заголовок)]).

    С 03.08.2026 номера пунктов ведет сам Word (w:numPr), и в тексте абзаца
    их больше НЕТ. Разбор по тексту здесь слеп — номера берутся из порядка
    нумерованных абзацев после заголовка «ПРИЛОЖЕНИЯ». Вернет пустой список,
    если нумерация ручная: тогда вызывающий разбирает текст, как раньше.
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(docx_path)
    paragraphs = list(doc.paragraphs)
    head = None
    for i, par in enumerate(paragraphs):
        if re.fullmatch(r"\s*(?:Приложени[ея]|ПРИЛОЖЕНИ[ЕЯ])\s*:?\s*", par.text or ""):
            head = i
            break
    if head is None:
        return "\n".join(x.text for x in paragraphs), []

    items = []
    for par in paragraphs[head + 1:]:
        if par._p.find(qn("w:pPr")) is not None and \
                par._p.find(qn("w:pPr")).find(qn("w:numPr")) is not None:
            if par.text.strip():
                items.append((len(items) + 1, par.text.strip()))
    body = "\n".join(x.text for x in paragraphs[:head])
    return body, items


def check_attachments(text: str, items=None) -> list[str]:
    """Приложения: сквозная нумерация и упоминание каждого в тексте документа.

    items — готовый список (номер, заголовок) из автонумерации Word. Передан —
    нумерация заведомо сквозная (ее ведет программа), проверяется только
    упоминание в тексте.
    """
    if items:
        problems = []
        nums = [n for n, _ in items]
        PROCEDURAL = ("пошлин", "егрюл", "егрип", "направлени", "вручени",
                      "почтов", "квитанц", "опись", "доверенност", "диплом",
                      "ордер", "выписка из един")
        procedural_nums = {n for n, title in items
                           if any(k in title.lower() for k in PROCEDURAL)}
        mentioned = set(int(x) for x in re.findall(
            r"(?:приложени[ияюе]\s*№?\s*(\d{1,2}))", text, re.I))
        missing = [n for n in nums
                   if n not in mentioned and n not in procedural_nums]
        if missing and mentioned:
            problems.append(f"приложения {missing} не упомянуты в тексте документа")
        return problems

    m = re.search(r"(?im)^\s*(?:Приложени[ея]|ПРИЛОЖЕНИ[ЕЯ])\s*:?\s*$", text)
    if not m:
        return []
    tail = text[m.end():]
    body = text[:m.start()]
    items = re.findall(r"(?m)^\s*(\d{1,2})[.)]\s+(.+)$", tail)
    nums = [int(n) for n, _ in items]
    # Приложения по ст. 126 АПК и ст. 132 ГПК подаются в силу закона, а не в
    # подтверждение довода: госпошлина, выписка ЕГРЮЛ, доказательства
    # направления копии, доверенность, диплом. Требовать ссылку на них в
    # мотивировочной части бессмысленно — это была ложная тревога.
    PROCEDURAL = ("пошлин", "егрюл", "егрип", "направлени", "вручени",
                  "почтов", "квитанц", "опись", "доверенност", "диплом",
                  "ордер", "выписка из един")
    procedural_nums = {int(n) for n, title in items
                       if any(k in title.lower() for k in PROCEDURAL)}
    problems = []
    if not nums:
        return ["раздел «Приложения» есть, но перечень пуст или не пронумерован"]
    expected = list(range(1, len(nums) + 1))
    if nums != expected:
        problems.append(f"нумерация приложений не сквозная: {nums} (ожидалось {expected})")
    mentioned = set(int(x) for x in re.findall(r"(?:приложени[ияюе]\s*№?\s*(\d{1,2}))", body, re.I))
    missing = [n for n in nums if n not in mentioned and n not in procedural_nums]
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
    dx = norm(doc_text(Document(docx_path)))
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


def _add_page_field(doc, top=False):
    """Поле PAGE в нижнем колонтитуле — как это делает DocBuilder.

    top=True — заведомо неверное место (верх): фикстура для проверки, что
    сторож это ловит.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    sec = doc.sections[0]
    area = sec.header if top else sec.footer
    area.is_linked_to_previous = False
    p = area.paragraphs[0] if area.paragraphs else area.add_paragraph()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), " PAGE ")
    p._p.append(fld)
    return p


def _add_fake_hyperlink(doc):
    """Гиперссылка как её ставит Word: runs внутри w:hyperlink, в p.runs не видны."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    p = doc.add_paragraph()
    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), "toc1")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "к разделу 1"
    run.append(text)
    link.append(run)
    p._p.append(link)
    return p


def selftest() -> int:
    import tempfile
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt, Cm

    tmp = tempfile.mkdtemp()

    def build(path, *, font=SPEC_FONT_BODY, size=12, left=30, top=20, bottom=30,
              right=15, italic=False, underline=False, spacing=1.15,
              indent=1.25, pages=True, pages_top=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY,
              table=None, hyperlink=False, paragraphs=1,
              text="Текст документа, достаточно длинный абзац для проверки выравнивания."):
        """Фикстура документа. КАЖДЫЙ параметр обязан быть покрыт проверкой.

        Прежняя версия объявляла spacing= и indent=, но ни одна фикстура их не
        передавала, таблиц не строила вовсе (`grep -c add_table` → 0), и восемь
        ветвей сторожа можно было молча удалить при зелёном selftest (аудит
        03.08.2026). Шапка процессуального документа — плавающая таблица, то есть
        непокрытой оставалась ровно та часть, ради которой сторож и написан.
        """
        d = Document()
        sec = d.sections[0]
        sec.top_margin, sec.bottom_margin = Mm(top), Mm(bottom)
        sec.left_margin, sec.right_margin = Mm(left), Mm(right)
        if table is not None:
            t = d.add_table(rows=len(table), cols=len(table[0]))
            for row, cells in zip(t.rows, table):
                for cell, spec in zip(row.cells, cells):
                    cp = cell.paragraphs[0]
                    cr = cp.add_run(spec["text"])
                    cr.font.name = spec.get("font", font)
                    cr.font.size = Pt(spec.get("size", size))
                    if spec.get("underline"):
                        cr.font.underline = True
        for _ in range(paragraphs):
            p = d.add_paragraph()
            p.alignment = align
            p.paragraph_format.line_spacing = spacing
            p.paragraph_format.first_line_indent = Cm(indent)
            r = p.add_run(text * 2)
            r.font.name, r.font.size = font, Pt(size)
            r.font.italic, r.font.underline = italic, underline
        if hyperlink:
            _add_fake_hyperlink(d)
        if pages:
            _add_page_field(d, top=pages_top)
        d.save(path)
        return path

    good = build(os.path.join(tmp, "good.docx"))
    no_pages = build(os.path.join(tmp, "nopage.docx"), pages=False)
    pages_up = build(os.path.join(tmp, "pageup.docx"), pages_top=True)
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

    # Шапка процессуального документа — плавающая таблица. До 04.08.2026 ни одна
    # фикстура таблиц не строила, и весь обход iter_paragraphs был не покрыт.
    head_ok = [[{"text": "В Вахитовский районный суд города Казани"}],
               [{"text": "Истец: Иванов Иван Иванович"}]]
    head_alien_font = [[{"text": "В Вахитовский районный суд",
                         "font": "Times New Roman"}]]
    head_alien_size = [[{"text": "В Вахитовский районный суд", "size": 9}]]
    head_underline = [[{"text": "В Вахитовский районный суд", "underline": True}]]
    head_numbers = [[{"text": "Цена иска: 1 250 000 руб."}]]

    with_table = build(os.path.join(tmp, "table.docx"), table=head_ok)
    tbl_font = build(os.path.join(tmp, "tblfont.docx"), table=head_alien_font)
    tbl_size = build(os.path.join(tmp, "tblsize.docx"), table=head_alien_size)
    tbl_underline = build(os.path.join(tmp, "tblund.docx"), table=head_underline)
    tbl_numbers = build(os.path.join(tmp, "tblnum.docx"), table=head_numbers)
    bad_spacing = build(os.path.join(tmp, "spacing.docx"), spacing=2.0)
    bad_indent = build(os.path.join(tmp, "indent.docx"), indent=0.5)
    bad_size = build(os.path.join(tmp, "size.docx"), size=9)
    with_underline = build(os.path.join(tmp, "und.docx"), underline=True)
    with_link = build(os.path.join(tmp, "link.docx"), hyperlink=True)
    left_aligned = build(os.path.join(tmp, "left.docx"),
                         align=WD_ALIGN_PARAGRAPH.LEFT, paragraphs=5)
    mostly_justified = build(os.path.join(tmp, "mostly.docx"), paragraphs=5)
    dogovor_ok = build(os.path.join(tmp, "dog.docx"), top=20, bottom=20, left=25, right=20)

    md_table = os.path.join(tmp, "table.md")
    open(md_table, "w", encoding="utf-8").write(
        "Цена иска: 1 250 000 руб.\n\n"
        + "Текст документа, достаточно длинный абзац для проверки выравнивания." * 2)

    checks = [
        ("корректный документ проходит", check_docx(good) == []),
        # Обход таблиц. Каждая ветвь проверки обязана видеть содержимое ячейки —
        # иначе шапка документа не проверяется вообще ничем.
        ("документ с таблицей-шапкой проходит", check_docx(with_table) == []),
        ("чужой шрифт В ЯЧЕЙКЕ шапки пойман",
         any("шрифт" in p for p in check_docx(tbl_font))),
        ("чужой кегль В ЯЧЕЙКЕ шапки пойман",
         any("кегли" in p for p in check_docx(tbl_size))),
        ("подчёркивание В ЯЧЕЙКЕ шапки поймано",
         any("подчеркивание" in p for p in check_docx(tbl_underline))),
        ("число из .md найдено в ЯЧЕЙКЕ таблицы, а не объявлено пропавшим",
         check_md_vs_docx(md_table, tbl_numbers) == []),
        # Интервал и отступ: параметры build() существовали, но не передавались.
        ("межстрочный интервал 2.0 пойман",
         any("интервал" in p for p in check_docx(bad_spacing))),
        ("абзацный отступ 0.5 см пойман",
         any("отступ" in p for p in check_docx(bad_indent))),
        ("кегль 9 пт пойман", any("кегли" in p for p in check_docx(bad_size))),
        ("подчёркивание в теле поймано",
         any("подчеркивание" in p for p in check_docx(with_underline))),
        ("гиперссылка поймана", any("гиперссыл" in p for p in check_docx(with_link))),
        # Порог JUSTIFY: доля, а не факт. Один абзац из пяти — терпимо, все пять — нет.
        ("сплошное выравнивание влево поймано",
         any("не по ширине" in p for p in check_docx(left_aligned))),
        ("документ по ширине порога не превышает",
         not any("не по ширине" in p for p in check_docx(mostly_justified))),
        # Профиль договора: свои поля 20/20/25/20.
        ("договор со своими полями проходит при --dogovor",
         check_docx(dogovor_ok, dogovor=True) == []),
        ("поля договора без флага считаются нарушением",
         any("поле" in p for p in check_docx(dogovor_ok))),
        ("судебные поля под флагом договора считаются нарушением",
         any("поле" in p for p in check_docx(good, dogovor=True))),
        ("чужой шрифт пойман", any("шрифт" in p for p in check_docx(bad_font))),
        ("поле не по спецификации поймано", any("поле left" in p for p in check_docx(bad_margin))),
        ("L3 с полем 35 проходит при --l3", check_docx(l3_ok, l3=True) == []),
        ("L3-поле без флага считается ошибкой", any("поле left" in p for p in check_docx(l3_ok))),
        ("курсив пойман", any("курсив" in p for p in check_docx(with_italic))),
        ("буква ё поймана", any("«ё»" in p for p in check_docx(with_yo))),
        ("отсутствие нумерации страниц поймано",
         any("номера страницы" in p for p in check_docx(no_pages))),
        # Место номера — часть стандарта: с 04.08.2026 он внизу по центру.
        ("номер страницы наверху пойман",
         any("ВЕРХНЕМ" in p for p in check_docx(pages_up))),
        ("номер страницы внизу претензий не вызывает",
         not any("номер страницы" in p for p in check_docx(good))),
        ("совпадающие md и docx проходят", check_md_vs_docx(md_ok, good) == []),
        ("разошедшиеся md и docx пойманы", check_md_vs_docx(md_other, good) != []),
        ("сквозная нумерация приложений проходит", check_attachments(att_ok) == []),
        ("дыра в нумерации приложений поймана", any("сквозная" in p for p in check_attachments(att_gap))),
        ("плейсхолдер пойман", any("плейсхолдер" in p for p in check_text("Сумма {{amount}}", "t"))),
        ("голое ХХХ поймано", any("плейсхолдер" in p for p in check_text("Полис ХХХ выдан", "t"))),
        ("серия полиса ОСАГО претензий не вызывает",
         check_text("полис ОСАГО серия ХХХ № 0648968315", "t") == []),
        ("дата не того формата поймана", any("ДД.ММ.ГГГГ" in p for p in check_text("2026-08-03", "t"))),
        # Ложная тревога дороже молчания: она приучает пролистывать вывод.
        ("подчеркнутый пропуск в процессуальном документе пойман",
         any("незаполненное поле" in p for p in check_text("Подпись ________", "t"))),
        ("в договоре место подписи претензий не вызывает",
         check_text("Подпись ________", "t", dogovor=True) == []),
        ("квадратные скобки пойманы",
         any("квадратные скобки" in p for p in check_text("(ст. 617 ГК РФ) [ст. 621 ГК РФ]", "t"))),
        ("круглые скобки претензий не вызывают",
         check_text("(ст. 617 ГК РФ)", "t") == []),
        # Пропись денежной суммы (этап 9.8): обе оси — сумма без прописи и
        # несовпадение ловятся, верная пропись и обиход молчат.
        ("сумма без прописи поймана",
         any("без прописи" in p for p in check_text(
             "Прошу взыскать с ответчика 100 000 рублей неустойки (ст. 330 ГК РФ).", "t"))),
        ("несовпадающая пропись поймана — «1 000 (сто тысяч)» глазами не ловится",
         any("НЕ совпадает" in p for p in check_text(
             "Прошу взыскать 1 000 (сто тысяч) рублей неустойки.", "t"))),
        ("верная пропись проходит",
         check_text("Прошу взыскать 1 000 (одна тысяча) рублей неустойки "
                    "(ст. 330 ГК РФ).", "t") == []),
        ("пропись с копейками проходит",
         not any("пропис" in p or "совпадает" in p for p in check_text(
             "Прошу взыскать 1 234,56 (одна тысяча двести тридцать четыре рубля "
             "пятьдесят шесть копеек).", "t"))),
        ("нулевые копейки цифрами — обиход, не нарушение",
         not any("пропис" in p for p in check_text(
             "Прошу взыскать 5 000 (пять тысяч) рублей 00 копеек.", "t"))),
        ("даты, статьи, номера дел, ИНН, ставки и листы прописи НЕ требуют",
         check_text("Заседание назначено на 21.08.2026 (ст. 333 ГК РФ, п. 71) "
                    "по делу № А65-123/2026, ИНН 1655021805, ставка 7,5 % "
                    "годовых, лист дела 82.", "t") == []),
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
    ap.add_argument("--dogovor", action="store_true",
                    help="договор и прочее несудебное: поля 20/20/25/20 мм")
    ap.add_argument("--dogovor-advokat", dest="advokat", action="store_true",
                    help="бланк договора адвокатского центра (DOCX_FORMATTING.md §8): "
                         "поля 20/20/15/10 мм, кегли бланка, подчеркивание допустимо")
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

    problems = check_docx(a.docx, a.l3, a.dogovor, a.advokat)
    body, items = docx_appendices(a.docx)
    if items:
        problems += check_attachments(body, items)
    else:
        from docx import Document
        text = "\n".join(p.text for p in Document(a.docx).paragraphs)
        problems += check_attachments(text)
    if a.md:
        problems += check_md_vs_docx(a.md, a.docx)
        # .md уходит доверителю тем же документом, поэтому проверяется ЦЕЛИКОМ
        # той же машиной: плейсхолдеры, «ё», даты, скобки, пропуски и денежная
        # ось — иначе незаполненное поле переживает пересборку и всплывает в
        # следующей редакции (проба 20.08.2026).
        md_text = open(a.md, encoding="utf-8").read()
        problems += check_text(md_text, os.path.basename(a.md), dogovor=a.dogovor or a.advokat)

    if not problems:
        print(f"✓ {os.path.basename(a.docx)}: формат и согласованность в порядке")
        return 0
    print(f"⚠ {os.path.basename(a.docx)}: нарушений {len(problems)}")
    for p in problems:
        print(f"   • {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
