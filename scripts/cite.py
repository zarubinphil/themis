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
}

ARTICLE_QUERY_RE = re.compile(
    r"стать?[яию]?\.?\s*([\d.]+(?:-\d+)?)\s+([А-Яа-яЁё]+)(?:\s+РФ)?", re.I)
CHAPTER_QUERY_RE = re.compile(
    r"глав[а-яё]*\s+([\d.]+)\s+([А-Яа-яЁё]+)(?:\s+РФ)?", re.I)
PLENUM_QUERY_RE = re.compile(
    r"п\.?\s*(\d+(?:\.\d+)?)\s+плен[а-яё]*\s+вс\s*рф\s+от\s+(\d{2}\.\d{2}\.\d{4})"
    r"\s*(?:№|n)?\s*(\d+)?", re.I)


def read(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    return open(path, encoding="utf-8").read()


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
        result["error"] = (f"кодекс «{codex_word}» не опознан. Известные: "
                            + ", ".join(sorted(set(CODEX_SLUGS))))
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
    section = extract_section(text, heading_line)
    result.update({
        "found": True,
        "file": os.path.relpath(path, ROOT),
        "redaction_date": frontmatter_field(text, "дата_редакции"),
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
    section = extract_section(text, heading_line)
    result.update({
        "found": True, "file": os.path.relpath(path, ROOT),
        "redaction_date": frontmatter_field(text, "дата_редакции"),
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
        head = text.split("\n", 5)
        title_line = next((l for l in head if l.startswith("# Постановление")), "")
        if date_str not in title_line:
            continue
        if num and f"N {num}" not in title_line:
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
    heading_re = re.compile(rf"^###\s+п\.\s+{re.escape(punkt)}\b", re.M)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        list_corpus()
        return 0
    if not a.query:
        ap.print_help()
        return 1

    result = resolve(a.query)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["found"] else 1

    if not result["found"]:
        print(f"НЕ НАЙДЕНО: {result['query']}")
        print(f"  причина: {result['error']}")
        return 1

    print(result["text"])
    print()
    print(f"Источник: {result['file']} ({result.get('source', '?')}, "
          f"ред. от {result.get('redaction_date', '?')})")
    print(f"Для вставки: {result['cite_tag']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
