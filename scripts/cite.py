#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cite.py — точечная цитата из локального корпуса права (knowledge/kodeksy,
knowledge/plenumy), без чтения агентом всего кодекса и без похода в сеть.

Зачем. Дело не должно тратить 100-300k токенов на то, чтобы модель искала
норму в интернете или пересказывала ее по памяти — риск исказить дословную
цитату (прецедент: WebFetch исказил текст ст. 683 ГК РФ на боевом деле).
Здесь только grep по файлам, которые построил scripts/update_legal_corpus.py.
Корпуса нет или статья не найдена — скрипт честно говорит «не найдено», а не
подставляет похожую по звучанию норму.

Использование:
    python3 scripts/cite.py "ст. 683 ГК"
    python3 scripts/cite.py "статья 131 ГПК РФ"
    python3 scripts/cite.py "п. 21 Пленума ВС РФ от 19.06.2012 № 13"
    python3 scripts/cite.py "глава 25.3 НК"
    python3 scripts/cite.py --json "ст. 37 УК"
    python3 scripts/cite.py --list                # какие кодексы/пленумы есть на диске

Не нашел — сеть только если явно попросили: этот скрипт сеть не трогает
никогда, это обязанность вызывающего (Фемида/агент), см. .claude/CLAUDE.md.

ponytail: только stdlib, только grep по уже построенному корпусу.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KODEKSY_DIR = os.path.join(ROOT, "knowledge", "kodeksy")
PLENUMY_DIR = os.path.join(ROOT, "knowledge", "plenumy")

# Разговорные сокращения кодексов -> slug файла в knowledge/kodeksy/.
CODE_SLUGS = {
    "гк": "gk-rf", "гпк": "gpk-rf", "ск": "sk-rf", "кас": "kas-rf",
    "коап": "koap-rf", "коап рф": "koap-rf", "апк": "apk-rf",
    "нк": "nk-rf-gosposhlina", "тк": "tk-rf", "жк": "zhk-rf",
    "зк": "zk-rf", "ук": "uk-rf", "упк": "upk-rf",
    # ходовые ФЗ — обращение к ним в делах не реже, чем к кодексам
    "зозпп": "zozpp", "озпп": "zozpp",
    "229-фз": "fz-229-ispolnitelnoe", "127-фз": "fz-127-bankrotstvo",
    "3-фз": "fz-3-policiya", "о полиции": "fz-3-policiya", "полиции": "fz-3-policiya",
    "89-фз": "fz-89-othody", "об отходах": "fz-89-othody", "отходах": "fz-89-othody",
}

# Кодекс в запросе — не только кириллица: «229-ФЗ», «127-ФЗ», «ЗоЗПП». Прежний
# шаблон принимал только буквы, и половина словаря кодексов была недостижима.
ARTICLE_QUERY_RE = re.compile(
    r"ст(?:атья|атьи|атью)?\.?\s*([\d.]+(?:-\d+)?)\s+([А-Яа-яЁё0-9-]+)(?:\s+РФ)?", re.I)
CHAPTER_QUERY_RE = re.compile(
    r"глав[а-яё]*\s+([\d.]+)\s+([А-Яа-яЁё]+)(?:\s+РФ)?", re.I)
PLENUM_QUERY_RE = re.compile(
    r"п\.?\s*(\d+(?:\.\d+)?)\s+плен[а-яё]*\s+вс\s*рф\s+от\s+(\d{2}\.\d{2}\.\d{4})"
    r"\s*(?:№|n)?\s*(\d+)?", re.I)


def read(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    return open(path, encoding="utf-8").read()


# Действующий федеральный кодекс правится чаще, чем раз в три года. Дата старше —
# это почти всегда не редакция, а дата принятия, подставленная разбором вслепую
# (в sk-rf.md стояло 29.12.1995 — день принятия СК РФ, а не последней редакции).
STALE_YEARS = 3


def redaction_status(value: str | None) -> tuple[str, str]:
    """(статус, пояснение). Неизвестная редакция — не мелочь оформления:
    цитата устаревшей нормы в суде дороже любой экономии токенов."""
    if not value:
        return "НЕИЗВЕСТНА", "поле «дата_редакции» в корпусе отсутствует"
    if "?" in value:
        return "НЕИЗВЕСТНА", f"корпус хранит «{value}» — разбор источника не дал даты"
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", value)
    if not m:
        return "НЕИЗВЕСТНА", f"дата «{value}» не разобрана"
    import datetime
    try:
        d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return "НЕИЗВЕСТНА", f"дата «{value}» невалидна"
    age = (datetime.date.today() - d).days / 365.25
    if age > STALE_YEARS:
        return "ПОДОЗРИТЕЛЬНА", (f"редакция от {value} старше {STALE_YEARS} лет — "
                                 "вероятно, это дата принятия, а не последней редакции")
    return "ОК", value


def integrity_ok(text: str) -> bool | None:
    """Тело корпуса не менялось после выгрузки. None — хеша в файле нет."""
    declared = frontmatter_field(text, "sha256")
    if not declared:
        return None
    # Границу тела берем ровно так же, как ее берет update_legal_corpus.py:
    # content = frontmatter + body, где frontmatter кончается строкой «---\n».
    # Наивный split("---") оставляет лишний перевод строки и дает ложную тревогу.
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    body = text[m.end():] if m else text
    import hashlib
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == declared


def frontmatter_field(text: str, field: str) -> str | None:
    """Однострочное значение поля. Многочастные кодексы (ГК) хранят
    источник/дату как YAML-список (по одному на часть) — берем первый
    элемент с пометкой "+N", а не молчим и не падаем."""
    m = re.search(rf'^{field}:\s*"([^"]*)"', text, re.M)
    if m:
        return m.group(1)
    m = re.search(rf"^{field}:\n((?:  - .*\n)+)", text, re.M)
    if m:
        items = [ln[4:].strip().strip('"') for ln in m.group(1).splitlines()]
        return items[0] + (f" (+{len(items) - 1} частей)" if len(items) > 1 else "")
    return None


def extract_section(text: str, heading_line: str) -> str | None:
    """Текст от строки heading_line (включительно) до следующего '#'-заголовка
    того же или более высокого уровня."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == heading_line.strip():
            level = len(re.match(r"#+", line).group())
            out = [line]
            for j in range(i + 1, len(lines)):
                nm = re.match(r"(#+)\s", lines[j])
                if nm and len(nm.group(1)) <= level:
                    break
                out.append(lines[j])
            return "\n".join(out).strip()
    return None


def part_meta(text: str, pos: int) -> dict:
    """Метаданные ТОЙ ЧАСТИ кодекса, в которой лежит найденная статья.

    Многочастный кодекс (ГК — четыре части) хранит источник и дату редакции списками:
    по элементу на часть. Раньше бралcя первый элемент всегда, и ст. 1229 из части
    четвертой подписывалась редакцией части первой. Дата под цитатой уходит в судебный
    документ, поэтому ошибка не косметическая.
    """
    heads = [(m.start(), m.group(1)) for m in re.finditer(r"^# (Часть [а-яе]+)\s*$", text, re.M)]
    if not heads:
        return {}
    idx = sum(1 for start, _ in heads if start < pos) - 1
    if idx < 0:
        return {}
    out = {"часть": heads[idx][1]}
    for field, key in (("даты_частей", "redaction_date"), ("источник", "source")):
        items = frontmatter_list(text, field)
        if items and idx < len(items):
            out[key] = items[idx]
    return out


def frontmatter_list(text: str, field: str) -> list[str]:
    m = re.search(rf"^{field}:\n((?:  - .*\n)+)", text, re.M)
    if not m:
        return []
    return [ln[4:].strip().strip('"') for ln in m.group(1).splitlines()]


def missing_article_note(text: str, num: str) -> str | None:
    """Статья есть в оглавлении, но не скачалась: это не «не найдено»."""
    wanted = num.replace(" ", "")
    for item in frontmatter_list(text, "пропущенные_статьи"):
        normalized = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", item).replace(" ", "")
        numbers = re.findall(r"(?i)статья(\d+(?:\.\d+)*(?:-\d+)?)", normalized)
        if wanted in numbers:
            return item
    return None


def _found_label(heading_line: str, kind: str) -> str:
    """Ярлык по НАЙДЕННОМУ заголовку: «Глава 25.3» → «глава 25.3»."""
    m = re.search(r"(?i)(?:глава|п\.)\s*(\d+(?:\.\d+)*)", heading_line or "")
    return f"{kind} {m.group(1)}" if m else kind


def _repealed(heading_line: str, section: str) -> bool:
    """Норма утратила силу. Проверка была только на пути статей, хотя пункт Пленума
    утрачивает силу ровно так же (п. 15 Пленума от 05.03.2004 № 1 — с 17.12.2024)."""
    head = (heading_line or "") + " " + (section or "")[:400]
    return bool(re.search(r"(?i)утратил[аи]?\s+силу|признан[аоы]?\s+утратившим", head))


def find_article(num: str, code_word: str) -> dict:
    slug = CODE_SLUGS.get(code_word.lower())
    result = {"query": f"ст. {num} {code_word.upper()}", "found": False}
    if not slug:
        have = sorted({k for k, v in CODE_SLUGS.items()
                       if os.path.isfile(os.path.join(KODEKSY_DIR, f"{v}.md"))})
        result["error"] = (f"кодекс «{code_word}» не опознан. На диске есть: "
                            + ", ".join(have) if have else "корпус пуст")
        return result
    path = os.path.join(KODEKSY_DIR, f"{slug}.md")
    text = read(path)
    if text is None:
        result["error"] = f"файл {path} не найден — корпус не выгружен (scripts/update_legal_corpus.py --init)"
        return result
    missing = missing_article_note(text, num)
    if missing:
        result["error"] = (f"статья {num} отсутствует в выгрузке — докачать: "
                           f"python3 scripts/update_legal_corpus.py --update --doc {slug}. "
                           f"{missing}")
        return result
    heading_re = re.compile(rf"^###\s+Статья\s+{re.escape(num)}\.\s", re.M)
    m = heading_re.search(text)
    if not m:
        result["error"] = f"статья {num} не найдена в {path} (проверьте номер или актуальность корпуса)"
        return result
    heading_line = text[m.start():text.index("\n", m.start())]
    # Оглавление источника склеивает утратившие силу статьи в один заголовок:
    # «Статья 7. 29, статья 7.29.1, … Утратили силу». Запрос «ст. 7 КоАП» совпадал
    # с его началом, и под тегом [ст. 7 КоАП РФ] в судебный документ уходил текст
    # про совсем другие статьи — с кодом возврата 0. Такой заголовок не цитируем.
    if re.search(r"(?i),\s*стать[яи]\s|Утратил[аи]?\s+силу", heading_line):
        result["error"] = (
            f"под номером {num} в корпусе стоит не отдельная статья, а склеенный "
            f"заголовок: «{heading_line.lstrip('# ').strip()[:120]}». Цитировать нельзя — "
            f"запросить конкретную статью из перечня либо сверить по первоисточнику")
        return result
    section = extract_section(text, heading_line)
    part = part_meta(text, m.start())
    result.update({
        "found": True,
        "file": os.path.relpath(path, ROOT),
        "часть": part.get("часть"),
        "redaction_date": part.get("redaction_date") or frontmatter_field(text, "дата_редакции"),
        "redaction_status": redaction_status(
            part.get("redaction_date") or frontmatter_field(text, "дата_редакции"))[0],
        "redaction_note": redaction_status(
            part.get("redaction_date") or frontmatter_field(text, "дата_редакции"))[1],
        "integrity": integrity_ok(text),
        "source": part.get("source") or frontmatter_field(text, "источник"),
        "text": section,
        "cite_tag": f"(ст. {num} {code_word.upper()} РФ)",
    })
    return result


def find_chapter(num: str, code_word: str) -> dict:
    slug = CODE_SLUGS.get(code_word.lower())
    result = {"query": f"глава {num} {code_word.upper()}", "found": False}
    if not slug:
        result["error"] = f"кодекс «{code_word}» не опознан"
        return result
    path = os.path.join(KODEKSY_DIR, f"{slug}.md")
    text = read(path)
    if text is None:
        result["error"] = f"файл {path} не найден — корпус не выгружен"
        return result
    # \b между цифрой и точкой СРАБАТЫВАЕТ, поэтому «глава 25» матчилась заголовком
    # «Глава 25.3» и выдавала госпошлину вместо налога на прибыль. Номер обязан
    # кончаться: следом не цифра и не точка.
    heading_re = re.compile(rf"^##\s+Глава\s+{re.escape(num)}(?!\d)(?!\.\d).*$", re.M)
    m = heading_re.search(text)
    if not m:
        result["error"] = f"глава {num} не найдена в {path}"
        return result
    heading_line = text[m.start():text.index("\n", m.start())]
    # Внутри главы бывают параграфы того же уровня («## § 1. …»), и обычная
    # граница «до следующего заголовка того же уровня» обрезала главу на первой
    # же строке — наружу уходил один заголовок без единой нормы.
    tail = text[m.start():]
    nxt = re.search(r"(?m)^##\s+Глава\s", tail[len(heading_line):])
    section = (tail[: len(heading_line) + nxt.start()] if nxt else tail).strip()
    # Глава может состоять из одних заголовков: тела норм в нее не попали.
    # Прежде это отдавалось с кодом 0, и агент записывал «норма проверена».
    body_only = re.sub(r"(?m)^#{1,6}\s.*$", "", section or "").strip()
    if len(body_only) < 40:
        result["error"] = (f"глава {num} в корпусе есть, но текста норм в ней нет "
                            f"({len(body_only)} знаков вне заголовков) — выгрузка неполна, "
                            f"перевыгрузить: update_legal_corpus.py --update --doc {slug}")
        return result
    result.update({
        "found": True, "file": os.path.relpath(path, ROOT),
        "redaction_date": frontmatter_field(text, "дата_редакции"),
        "redaction_status": redaction_status(frontmatter_field(text, "дата_редакции"))[0],
        "redaction_note": redaction_status(frontmatter_field(text, "дата_редакции"))[1],
        "integrity": integrity_ok(text),
        "source": frontmatter_field(text, "источник"),
        "text": section,
        # Тег строится из найденного заголовка: раньше он эхом отдавал ЗАПРОШЕННЫЙ
        # номер, и под чужим текстом стояла синтаксически безупречная ложная ссылка.
        "cite_tag": f"({_found_label(heading_line, 'глава')} {code_word.upper()} РФ)",
    })
    return result


def find_plenum_punkt(punkt: str, date_str: str, num: str | None) -> dict:
    result = {"query": f"п. {punkt} Пленума ВС РФ от {date_str}"
              + (f" N {num}" if num else ""), "found": False}
    if not os.path.isdir(PLENUMY_DIR):
        result["error"] = f"{PLENUMY_DIR} не существует — Пленумы не выгружены"
        return result
    candidates = []
    for fname in sorted(os.listdir(PLENUMY_DIR)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(PLENUMY_DIR, fname)
        text = read(path)
        if text is None:
            continue
        title_line = next((l for l in text.split("\n") if l.startswith("# Постановление")), "")
        if date_str not in title_line:
            continue
        # «N 1» подстрокой совпадает с «N 10» — и пункт уходил из чужого
        # постановления с кодом 0. Номер сверяем по границе слова.
        if num and not re.search(rf"\bN\s*{re.escape(num)}\b", title_line):
            continue
        candidates.append((path, text, title_line))
    if not candidates:
        result["error"] = (f"Постановление Пленума ВС РФ от {date_str}"
                            + (f" N {num}" if num else "")
                            + f" не найдено в {PLENUMY_DIR} — проверьте дату/номер "
                              "или выгрузите (scripts/update_legal_corpus.py --plenums)")
        return result
    if len(candidates) > 1 and not num:
        result["error"] = ("дата совпала с несколькими Постановлениями — уточните номер "
                            "(№): " + "; ".join(os.path.basename(p) for p, _, _ in candidates))
        return result
    path, text, title_line = candidates[0]
    # То же самое для пунктов Пленумов: «п. 15» матчился заголовком «п. 15.1».
    # Замер совета: подмена задевала 15 пунктов в корпусе.
    heading_re = re.compile(rf"^###\s+п\.\s+{re.escape(punkt)}(?!\d)(?!\.\d)", re.M)
    m = heading_re.search(text)
    if not m:
        result["error"] = f"пункт {punkt} не найден в {path} ({title_line.strip('# ')})"
        return result
    heading_line = text[m.start():text.index("\n", m.start())]
    section = extract_section(text, heading_line)
    if _repealed(heading_line, section):
        result["error"] = (f"пункт {punkt} названного Постановления помечен утратившим "
                           "силу — цитировать нельзя, искать действующую позицию")
        return result
    result.update({
        "found": True, "file": os.path.relpath(path, ROOT),
        "title": title_line.lstrip("# ").strip(),
        "redaction_date": frontmatter_field(text, "дата_редакции"),
        "redaction_status": "ОК",   # у Пленума редакция не применима: акт цитируется целиком
        "integrity": integrity_ok(text),
        "source": frontmatter_field(text, "источник"),
        "text": section,
        "cite_tag": f"({title_line.lstrip('# ').strip()}, "
                    f"{_found_label(heading_line, 'п.')})",
    })
    return result


def resolve(query: str) -> dict:
    m = PLENUM_QUERY_RE.search(query)
    if m:
        return find_plenum_punkt(m.group(1), m.group(2), m.group(3))
    m = CHAPTER_QUERY_RE.search(query)
    if m:
        return find_chapter(m.group(1), m.group(2))
    m = ARTICLE_QUERY_RE.search(query)
    if m:
        return find_article(m.group(1), m.group(2))
    return {"query": query, "found": False,
            "error": "запрос не распознан. Форматы: «ст. 683 ГК», «глава 25.3 НК», "
                     "«п. 21 Пленума ВС РФ от 19.06.2012 № 13»"}


def list_corpus() -> None:
    print("Кодексы (knowledge/kodeksy/):")
    if os.path.isdir(KODEKSY_DIR):
        for f in sorted(os.listdir(KODEKSY_DIR)):
            if f.endswith(".md"):
                text = read(os.path.join(KODEKSY_DIR, f))
                red = frontmatter_field(text, "дата_редакции") or "?"
                arts = len(re.findall(r"^### Статья", text, re.M))
                print(f"  {f:<28} ред. {red:<24} статей: {arts}")
    else:
        print("  (пусто — запустите scripts/update_legal_corpus.py --init)")
    print("Пленумы (knowledge/plenumy/):")
    if os.path.isdir(PLENUMY_DIR):
        files = sorted(f for f in os.listdir(PLENUMY_DIR) if f.endswith(".md"))
        print(f"  {len(files)} документов")
    else:
        print("  (пусто — запустите scripts/update_legal_corpus.py --plenums)")


def selftest() -> int:
    """Синтетический корпус во временной папке: сеть и рабочий корпус не нужны."""
    import datetime
    import hashlib
    import tempfile
    global KODEKSY_DIR, PLENUMY_DIR
    real = (KODEKSY_DIR, PLENUMY_DIR)
    tmp = tempfile.mkdtemp()
    KODEKSY_DIR = os.path.join(tmp, "kodeksy")
    PLENUMY_DIR = os.path.join(tmp, "plenumy")
    os.makedirs(KODEKSY_DIR)
    os.makedirs(PLENUMY_DIR)
    fresh = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%d.%m.%Y")

    def write_fixture(slug, red, body, missing=None):
        head = (f'---\nдата_редакции: "{red}"\nисточник: "тест"\n'
                f'sha256: "{hashlib.sha256(body.encode()).hexdigest()}"\n')
        if missing:
            head += "пропущенные_статьи:\n"
            for item in missing:
                head += f"  - {item}\n"
        head += "---\n"
        open(os.path.join(KODEKSY_DIR, f"{slug}.md"), "w", encoding="utf-8").write(head + body)

    body_ok = ("## Глава 25.3\n\n### Статья 683. Срок договора\n\n1. Договор найма "
               "заключается на срок, не превышающий пяти лет.\n\n### Статья 152.1. Изображение\n\n"
               "Обнародование изображения допускается с согласия гражданина.\n\n"
               "### Статья 123.20. Управление фондом\n\nФонд управляется органами фонда.\n")
    write_fixture("gk-rf", fresh, body_ok,
                  missing=["Статья 123. 20-1, статья 123.20-2, статья 123.20-3. Утратили силу — тест"])
    # 25.3 стоит ПЕРЕД 25 намеренно: так подмена видна. При снятой границе поиск
    # «глава 25» находит первое совпадение — то есть 25.3 — и выдает чужую главу.
    write_fixture("zk-rf", fresh,
          "## Глава 25.3. Государственная пошлина\n\n### Статья 333.16. Пошлина\n\n"
          "Государственная пошлина — сбор, взимаемый с лиц при их обращении в "
          "государственные органы за совершением юридически значимых действий.\n\n"
          "## Глава 25. Налог на прибыль\n\n### Статья 246. Налогоплательщики\n\n"
          "Налогоплательщиками налога на прибыль организаций признаются российские "
          "организации и иностранные организации, осуществляющие деятельность через "
          "постоянные представительства.\n")
    write_fixture("gpk-rf", "?", "### Статья 131. Форма иска\n\nИск подается в письменной форме.\n")
    write_fixture("sk-rf", "29.12.1995", "### Статья 34. Совместная собственность\n\nИмущество общее.\n")
    # заголовок-склейка утративших силу статей рядом с живой статьей
    write_fixture("koap-rf", fresh,
          "### Статья 7. 29, статья 7.29.1, статья 7.30. Утратили силу\n\n"
          "Статьи 7.29 - 7.30. Утратили силу с 1 марта 2025 года.\n\n"
          "### Статья 7.1. Самовольное занятие земельного участка\n\nСамовольное занятие.\n")
    # битая целостность: тело изменено после подсчета хеша
    open(os.path.join(KODEKSY_DIR, "kas-rf.md"), "w", encoding="utf-8").write(
        '---\nдата_редакции: "' + fresh + '"\nисточник: "тест"\nsha256: "deadbeef"\n---\n'
        "### Статья 1. Предмет\n\nТекст.\n")
    open(os.path.join(PLENUMY_DIR, "2020-01-01-7.md"), "w", encoding="utf-8").write(
        "# Постановление Пленума ВС РФ от 01.01.2020 N 7\n\n"
        "### п. 9\n\nУтратил силу.\n\n### п. 15.1\n\nИзвещение СМС-сообщением.\n")
    open(os.path.join(PLENUMY_DIR, "2012-06-19-13.md"), "w", encoding="utf-8").write(
        "# Постановление Пленума ВС РФ от 19.06.2012 N 13\n\n### п. 21\n\nСуд апелляционной инстанции.\n")

    multipart = ("---\nисточник:\n  - \"src-part-1\"\n  - \"src-part-4\"\n"
                 "даты_частей:\n  - \"01.02.2026\"\n  - \"05.06.2026\"\n---\n"
                 "# Кодекс\n\n# Часть первая\n\n### Статья 1. Первая\n\nТекст один.\n\n"
                 "# Часть четвертая\n\n### Статья 1229. Исключительное право\n\nТекст четыре.\n")
    open(os.path.join(KODEKSY_DIR, "apk-rf.md"), "w", encoding="utf-8").write(multipart)
    r_p1 = resolve("ст. 1 АПК")
    r_p4 = resolve("ст. 1229 АПК")

    r_ok = resolve("ст. 683 ГК")
    r_idx = resolve("ст. 152.1 ГК")
    r_ch = resolve("глава 25.3 ГК")
    r_unknown = resolve("ст. 131 ГПК")
    r_stale = resolve("ст. 34 СК")
    r_broken = resolve("ст. 1 КАС")
    r_absent = resolve("ст. 999 ГК")
    r_no_code = resolve("ст. 1 МПК")
    r_merged = resolve("ст. 7 КоАП")
    r_alive = resolve("ст. 7.1 КоАП")
    r_missing = resolve("ст. 123.20-3 ГК")
    r_plenum = resolve("п. 21 Пленума ВС РФ от 19.06.2012 № 13")
    r_junk = resolve("что там про алименты")

    checks = [
        ("статья найдена и текст дословный", r_ok["found"] and "пяти лет" in r_ok["text"]),
        ("статья с индексом 152.1 найдена", r_idx["found"]),
        ("глава найдена", r_ch["found"]),
        ("свежая редакция — статус ОК", r_ok["redaction_status"] == "ОК"),
        ("редакция «?» помечена НЕИЗВЕСТНА", r_unknown["redaction_status"] == "НЕИЗВЕСТНА"),
        ("дата принятия вместо редакции помечена", r_stale["redaction_status"] == "ПОДОЗРИТЕЛЬНА"),
        ("нарушенная целостность поймана", r_broken["integrity"] is False),
        ("целостность целого файла подтверждена", r_ok["integrity"] is True),
        ("несуществующая статья не выдумывается", not r_absent["found"]),
        ("неизвестный кодекс назван ошибкой", not r_no_code["found"]),
        ("пункт Пленума найден", r_plenum["found"] and "апелляционной" in r_plenum["text"]),
        ("нераспознанный запрос не дает ложного попадания", not r_junk["found"]),
        ("тег для вставки собран", r_ok["cite_tag"] == "(ст. 683 ГК РФ)"),
        # Многочастный кодекс: дата и источник берутся ТОЙ части, где лежит статья.
        # Раньше всегда брался первый элемент, и ст. 1229 из части четвертой
        # подписывалась редакцией части первой — эта дата уходит в судебный документ.
        ("часть первая: своя дата", r_p1.get("redaction_date") == "01.02.2026"),
        ("часть четвертая: своя дата", r_p4.get("redaction_date") == "05.06.2026"),
        ("часть четвертая: свой источник", r_p4.get("source") == "src-part-4"),
        ("часть определена по заголовку", r_p4.get("часть") == "Часть четвертая"),
        # Граница номера: \b между цифрой и точкой срабатывает, и «п. 15» матчился
        # заголовком «п. 15.1» — в документ уходила ЧУЖАЯ норма под верным тегом.
        ("пункт 15 не подменяется пунктом 15.1", not resolve(
            "п. 15 Пленума ВС РФ от 01.01.2020 № 7")["found"]),
        ("пункт 15.1 находится сам по себе", resolve(
            "п. 15.1 Пленума ВС РФ от 01.01.2020 № 7")["found"]),
        # В фикстуре есть ОБЕ главы: 25 и 25.3. Раньше запрос главы 25 попадал в 25.3.
        ("глава 25 находит именно главу 25",
         "Налог на прибыль" in (resolve("глава 25 ЗК").get("text") or "")),
        ("глава 25 не подменяется главой 25.3",
         "Государственная пошлина" not in (resolve("глава 25 ЗК").get("text") or "")),
        ("глава 25.3 находится сама по себе", resolve("глава 25.3 ЗК")["found"]),
        # Ярлык берется из НАЙДЕННОГО заголовка: раньше тег эхом отдавал запрошенный
        # номер, и под чужим текстом стояла безупречная по виду ложная ссылка.
        ("ярлык читается из заголовка", _found_label("## Глава 25.3. Пошлина", "глава")
         == "глава 25.3"),
        ("ярлык не тащит хвостовую точку", "." not in
         _found_label("## Глава 25. Налог", "глава").split()[-1]),
        ("ярлык пункта из заголовка", _found_label("### п. 15.1", "п.") == "п. 15.1"),
        ("тег строится по найденному", resolve("глава 25.3 ЗК")["cite_tag"]
         == "(глава 25.3 ЗК РФ)"),
        ("утративший силу пункт не цитируется", not resolve(
            "п. 9 Пленума ВС РФ от 01.01.2020 № 7")["found"]),
        ("длинный текст режется", clip("x" * 50_000, False)[1] is True),
        ("короткий текст не режется", clip("x" * 100, False) == ("x" * 100, False)),
        ("--full не режет", clip("x" * 50_000, True)[1] is False),
        ("склейка утративших силу не цитируется", not r_merged["found"]),
        ("причина отказа названа склейкой", "склеенный" in r_merged.get("error", "")),
        ("отдельная статья рядом со склейкой цитируется", r_alive["found"]),
        ("пропуск 123.20-1 не скрывает живую 123.20", resolve("ст. 123.20 ГК")["found"]),
        ("пропуск 123.20-3 назван неполной выгрузкой", not r_missing["found"]
         and "отсутствует в выгрузке" in r_missing.get("error", "")),
    ]
    KODEKSY_DIR, PLENUMY_DIR = real
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


# Потолок вывода. Пример из собственного --help («глава 25.3 НК») отдавал 473 КБ —
# примерно 120 000 токенов в контекст одной командой. Норму цитируют абзацами,
# а не главами; полный текст остается доступен явным --full.
MAX_OUT_CHARS = 20_000


def clip(text: str, full: bool) -> tuple[str, bool]:
    if full or len(text) <= MAX_OUT_CHARS:
        return text, False
    head = text[:MAX_OUT_CHARS]
    cut = head.rfind("\n\n")
    return (head[:cut] if cut > MAX_OUT_CHARS // 2 else head), True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help=f"выдать целиком (по умолчанию срез до {MAX_OUT_CHARS} знаков)")
    ap.add_argument("--selftest", action="store_true",
                    help="проверка на синтетическом корпусе, без сети и без диска проекта")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.list:
        list_corpus()
        return 0
    if not a.query:
        ap.print_help()
        return 1

    result = resolve(a.query)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["found"]:
            return 1
        if result.get("integrity") is False:
            return 3
        return 0 if result.get("redaction_status", "НЕИЗВЕСТНА") == "ОК" else 2

    if not result["found"]:
        print(f"НЕ НАЙДЕНО: {result['query']}")
        print(f"  причина: {result['error']}")
        return 1

    body, cut = clip(result["text"], a.full)
    print(body)
    if cut:
        print(f"\n… СРЕЗАНО: показано {len(body)} из {len(result['text'])} знаков. "
              f"Цитировать по этому срезу можно только то, что видно целиком; "
              f"весь текст — тот же запрос с --full.")
    print()
    red = result.get("redaction_date") or "?"
    # У многочастного кодекса поле несет перечисление всех частей — как подпись
    # под цитатой это мусор, который уходит в документ. Берем дату той части,
    # где нашлась статья: она первая в перечислении и относится к части первой.
    short_red = re.match(r"[^:;]*?:\s*([\d.]{10})", red)
    if short_red:
        red = short_red.group(1)
    part_note = f", {result['часть'].lower()}" if result.get("часть") else ""
    print(f"Источник: {result['file']}{part_note} ({result.get('source', '?')}, ред. от {red})")
    print(f"Для вставки: {result['cite_tag']}")

    rc = 0
    if result.get("integrity") is False:
        print("\n⛔ ЦЕЛОСТНОСТЬ КОРПУСА НАРУШЕНА: текст файла не совпадает с sha256 "
              "из его же frontmatter. Цитировать нельзя, перевыгрузить корпус.")
        rc = 3
    st = result.get("redaction_status", "НЕИЗВЕСТНА")
    if st != "ОК":
        print(f"\n⚠ РЕДАКЦИЯ {st}: {result.get('redaction_note', '')}.\n"
              "  В судебный документ такую цитату не вставлять без сверки с "
              "официальным источником (pravo.gov.ru).")
        rc = rc or 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
