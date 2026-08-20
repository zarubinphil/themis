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

    fonts, sizes, italic, underline = set(), set(), 0, 0
    for p in iter_paragraphs(doc):
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
# Разряд склеивается ТОЛЬКО пробелом/неразрывным или точкой между тройками:
# перевод строки числа не склеивает — иначе ячейка таблицы сливается с номером
# строки, а номер счета — с суммой под ним (ложные тревоги пробы 20.08.2026).
# «руб.» с точкой — основная письменная форма («Цена иска: 1 250 000 руб.»);
# граница слова после точки не строится \b, поэтому хвост проверяется
# явным запретом буквы.
_MONEY_NUM = (r"\d{1,3}(?:[ \u00a0]\d{3})+(?:,\d{1,2})?"
              r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
              r"|\d+(?:,\d{1,2})?")
_MONEY_CUR = r"руб(?:л[а-я]*)?\.?|коп(?:е[а-я]*)?\.?"
_MONEY_RE = re.compile(
    rf"(?P<num>{_MONEY_NUM})"
    rf"(?:\s*\((?P<propis>[^()]*)\))?"
    rf"(?:\s+(?P<cur>{_MONEY_CUR}))?(?![а-яА-Я])",
    re.I)


def _money_int(num_raw: str) -> tuple[str, str | None]:
    """Целая часть и копейки найденного числа: пробелы и точки между тройками —
    разряды, запятая — копейки."""
    s = num_raw.replace("\u00a0", " ").strip()
    kop = None
    if "," in s:
        s, kop = s.split(",", 1)
    s = s.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    return s, kop


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
    # Дословная цитата нормы в кавычках-елочках воспроизводится как в законе —
    # требовать там пропись значит запретить цитирование (правило проекта —
    # цитировать дословно). Сверяем текст без цитат.
    text = re.sub(r"«[^»]*»", " ", text)
    problems = []
    for m in _MONEY_RE.finditer(text):
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
        try:
            expected = _propis.propis(int(int_str))
        except ValueError as e:
            problems.append(f"{where}: число {num_raw} не конвертируется ({e}) — "
                            f"сумма осталась НЕ проверенной")
            continue
        if not words:
            problems.append(f"{where}: сумма {num_raw} {cur_show} без прописи в "
                            f"круглых скобках — денежная сумма пишется цифрами "
                            f"и прописью: «{num_raw} ({expected}) {cur_show}»")
            continue
        exp_rub = expected.split()
        try:
            exp_kop = (_propis.propis(int(kop_str)).split()
                       if kop_str and int(kop_str) else None)
        except ValueError as e:
            problems.append(f"{where}: копейки {num_raw} не конвертируются ({e}) — "
                            f"сумма осталась НЕ проверенной")
            continue
        if exp_kop is None:
            ok = words == exp_rub
        else:
            # Копейки: «одна тысяча двести тридцать четыре рубля пятьдесят
            # шесть копеек» — обе части внутри одних скобок, словами целиком.
            n = len(exp_rub)
            ok = (words[:n] == exp_rub and len(words) > n
                  and words[n].startswith("руб")
                  and words[n + 1:] == exp_kop)
        if not ok:
            problems.append(f"{where}: пропись «{words_raw.strip()}» НЕ совпадает "
                            f"с числом {num_raw} — ожидалось «{expected}»")
    return problems


def check_text(text: str, where: str, dogovor: bool = False) -> list[str]:
    problems = []
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
