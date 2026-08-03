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
CODEX_SLUGS = {
    "гк": "gk-rf", "гпк": "gpk-rf", "ск": "sk-rf", "кас": "kas-rf",
    "коап": "koap-rf", "коап рф": "koap-rf", "апк": "apk-rf",
    "нк": "nk-rf-gosposhlina", "тк": "tk-rf", "жк": "zhk-rf",
    "зк": "zk-rf", "ук": "uk-rf", "упк": "upk-rf",
    # ходовые ФЗ — обращение к ним в делах не реже, чем к кодексам
    "зозпп": "zozpp", "озпп": "zozpp",
    "229-фз": "fz-229-ispolnitelnoe", "127-фз": "fz-127-bankrotstvo",
}

# Кодекс в запросе — не только кириллица: «229-ФЗ», «127-ФЗ», «ЗоЗПП». Прежний
# шаблон принимал только буквы, и половина словаря CODEX_SLUGS была недостижима.
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
    # Границу тела берём ровно так же, как её берёт update_legal_corpus.py:
    # content = frontmatter + body, где frontmatter кончается строкой «---\n».
    # Наивный split("---") оставляет лишний перевод строки и даёт ложную тревогу.
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


def find_article(num: str, codex_word: str) -> dict:
    slug = CODEX_SLUGS.get(codex_word.lower())
    result = {"query": f"ст. {num} {codex_word.upper()}", "found": False}
    if not slug:
        have = sorted({k for k, v in CODEX_SLUGS.items()
                       if os.path.isfile(os.path.join(KODEKSY_DIR, f"{v}.md"))})
        result["error"] = (f"кодекс «{codex_word}» не опознан. На диске есть: "
                            + ", ".join(have) if have else "корпус пуст")
        return result
    path = os.path.join(KODEKSY_DIR, f"{slug}.md")
    text = read(path)
    if text is None:
        result["error"] = f"файл {path} не найден — корпус не выгружен (scripts/update_legal_corpus.py --init)"
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
    result.update({
        "found": True,
        "file": os.path.relpath(path, ROOT),
        "redaction_date": frontmatter_field(text, "дата_редакции"),
        "redaction_status": redaction_status(frontmatter_field(text, "дата_редакции"))[0],
        "redaction_note": redaction_status(frontmatter_field(text, "дата_редакции"))[1],
        "integrity": integrity_ok(text),
        "source": frontmatter_field(text, "источник"),
        "text": section,
        "cite_tag": f"[ст. {num} {codex_word.upper()} РФ]",
    })
    return result


def find_chapter(num: str, codex_word: str) -> dict:
    slug = CODEX_SLUGS.get(codex_word.lower())
    result = {"query": f"глава {num} {codex_word.upper()}", "found": False}
    if not slug:
        result["error"] = f"кодекс «{codex_word}» не опознан"
        return result
    path = os.path.join(KODEKSY_DIR, f"{slug}.md")
    text = read(path)
    if text is None:
        result["error"] = f"файл {path} не найден — корпус не выгружен"
        return result
    heading_re = re.compile(rf"^##\s+Глава\s+{re.escape(num)}\b.*$", re.M)
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
    # Глава может состоять из одних заголовков: тела норм в неё не попали.
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
        "cite_tag": f"[глава {num} {codex_word.upper()} РФ]",
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
    heading_re = re.compile(rf"^###\s+п\.\s+{re.escape(punkt)}", re.M)
    m = heading_re.search(text)
    if not m:
        result["error"] = f"пункт {punkt} не найден в {path} ({title_line.strip('# ')})"
        return result
    heading_line = text[m.start():text.index("\n", m.start())]
    section = extract_section(text, heading_line)
    result.update({
        "found": True, "file": os.path.relpath(path, ROOT),
        "title": title_line.lstrip("# ").strip(),
        "redaction_date": frontmatter_field(text, "дата_редакции"),
        "redaction_status": "ОК",   # у Пленума редакция не применима: акт цитируется целиком
        "integrity": integrity_ok(text),
        "source": frontmatter_field(text, "источник"),
        "text": section,
        "cite_tag": f"[{title_line.lstrip('# ').strip()}, п. {punkt}]",
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

    def codex(slug, red, body):
        head = (f'---\nдата_редакции: "{red}"\nисточник: "тест"\n'
                f'sha256: "{hashlib.sha256(body.encode()).hexdigest()}"\n---\n')
        open(os.path.join(KODEKSY_DIR, f"{slug}.md"), "w", encoding="utf-8").write(head + body)

    body_ok = ("## Глава 25.3\n\n### Статья 683. Срок договора\n\n1. Договор найма "
               "заключается на срок, не превышающий пяти лет.\n\n### Статья 152.1. Изображение\n\n"
               "Обнародование изображения допускается с согласия гражданина.\n")
    codex("gk-rf", fresh, body_ok)
    codex("gpk-rf", "?", "### Статья 131. Форма иска\n\nИск подается в письменной форме.\n")
    codex("sk-rf", "29.12.1995", "### Статья 34. Совместная собственность\n\nИмущество общее.\n")
    # заголовок-склейка утративших силу статей рядом с живой статьёй
    codex("koap-rf", fresh,
          "### Статья 7. 29, статья 7.29.1, статья 7.30. Утратили силу\n\n"
          "Статьи 7.29 - 7.30. Утратили силу с 1 марта 2025 года.\n\n"
          "### Статья 7.1. Самовольное занятие земельного участка\n\nСамовольное занятие.\n")
    # битая целостность: тело изменено после подсчета хеша
    open(os.path.join(KODEKSY_DIR, "kas-rf.md"), "w", encoding="utf-8").write(
        '---\nдата_редакции: "' + fresh + '"\nисточник: "тест"\nsha256: "deadbeef"\n---\n'
        "### Статья 1. Предмет\n\nТекст.\n")
    open(os.path.join(PLENUMY_DIR, "2012-06-19-13.md"), "w", encoding="utf-8").write(
        "# Постановление Пленума ВС РФ от 19.06.2012 N 13\n\n### п. 21\n\nСуд апелляционной инстанции.\n")

    r_ok = resolve("ст. 683 ГК")
    r_idx = resolve("ст. 152.1 ГК")
    r_ch = resolve("глава 25.3 ГК")
    r_unknown = resolve("ст. 131 ГПК")
    r_stale = resolve("ст. 34 СК")
    r_broken = resolve("ст. 1 КАС")
    r_absent = resolve("ст. 999 ГК")
    r_nocodex = resolve("ст. 1 МПК")
    r_merged = resolve("ст. 7 КоАП")
    r_alive = resolve("ст. 7.1 КоАП")
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
        ("неизвестный кодекс назван ошибкой", not r_nocodex["found"]),
        ("пункт Пленума найден", r_plenum["found"] and "апелляционной" in r_plenum["text"]),
        ("нераспознанный запрос не даёт ложного попадания", not r_junk["found"]),
        ("тег для вставки собран", r_ok["cite_tag"] == "[ст. 683 ГК РФ]"),
        ("длинный текст режется", clip("x" * 50_000, False)[1] is True),
        ("короткий текст не режется", clip("x" * 100, False) == ("x" * 100, False)),
        ("--full не режет", clip("x" * 50_000, True)[1] is False),
        ("склейка утративших силу не цитируется", not r_merged["found"]),
        ("причина отказа названа склейкой", "склеенный" in r_merged.get("error", "")),
        ("отдельная статья рядом со склейкой цитируется", r_alive["found"]),
    ]
    KODEKSY_DIR, PLENUMY_DIR = real
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


# Потолок вывода. Пример из собственного --help («глава 25.3 НК») отдавал 473 КБ —
# примерно 120 000 токенов в контекст одной командой. Норму цитируют абзацами,
# а не главами; полный текст остаётся доступен явным --full.
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
    # У многочастного кодекса поле несёт перечисление всех частей — как подпись
    # под цитатой это мусор, который уходит в документ. Берём дату той части,
    # где нашлась статья: она первая в перечислении и относится к части первой.
    short_red = re.match(r"[^:;]*?:\s*([\d.]{10})", red)
    if short_red:
        red = short_red.group(1)
    print(f"Источник: {result['file']} ({result.get('source', '?')}, ред. от {red})")
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
