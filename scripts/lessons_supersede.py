#!/usr/bin/env python3
"""lessons_supersede.py — стеречь отменённые уроки в журнале самообучения.

Зачем. `knowledge/lessons-log.md` копит уроки годами. Когда новый урок отменяет
старый (сменился стандарт, отклонён прежний движок, развёрнуто прежнее решение),
старая запись остаётся в файле как ни в чём не бывало. А устаревшая конвенция
ХУЖЕ отсутствующей: будущий агент читает её сверху вниз и уверенно исполняет
мёртвое правило (прецедент: конституция год держала движком OCR то, что пилот уже
отклонил). Помётка `superseded_by` на старой записи гасит этот риск — читатель
сразу видит, что урок снят и чем заменён.

Что именно ловит `--check`. Только ОБЪЯВЛЕННУЮ, но не проведённую отмену: новый
урок словами говорит «отменяет / заменяет / вместо / устарело» и ссылается на
ДАТУ существующего более старого урока — а тот не помечен `superseded_by`.
Смысловые противоречия «по духу» прибор не ищет: это работа модели, не регулярки.
Ложные срабатывания срезаны тем, что дата рядом с отменяющим словом обязана
ТОЧНО совпасть с заголовком существующего урока и быть строго старше — «заменой
на КС № 22-П от 26.05.2025» в прозе не заголовок и потому молчит.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "knowledge" / "lessons-log.md"

DATE = r"\d{2}\.\d{2}\.\d{4}"
HEADING = re.compile(r"^##\s+(" + DATE + r")\s*—\s*(.+)$", re.M)
# Слова объявленной отмены. \w* ловит падежи (отменяет/отменена/отменяющий).
CUE = re.compile(r"отмен\w*|замен\w*|устарел\w*|\bвместо\b|отныне", re.I)
# Окно вокруг отменяющего слова, где ищем дату. Узкое (60 символов): «отменяет
# набор гарнитур от 03.08.2026» умещается, а дата за три предложения — уже не рядом.
WINDOW = 60


def date_key(s: str) -> tuple[int, int, int]:
    d, m, y = s.split(".")
    return (int(y), int(m), int(d))


def parse_sections(text: str) -> list[dict]:
    marks = list(HEADING.finditer(text))
    secs = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        secs.append({"date": m.group(1), "title": m.group(2).strip(),
                     "head": m.group(0), "body": text[m.end():end],
                     "full": text[m.start():end]})
    return secs


def find_unmarked(text: str) -> list[tuple[str, str]]:
    """Пары (старый_урок_дата, новый_урок_дата) для отмен без пометки. Отсортировано."""
    secs = parse_sections(text)
    known = {s["date"] for s in secs}
    findings: set[tuple[str, str]] = set()
    for sec in secs:
        newk = date_key(sec["date"])
        for cue in CUE.finditer(sec["body"]):
            window = sec["body"][max(0, cue.start() - WINDOW): cue.end() + WINDOW]
            for dm in re.finditer(DATE, window):
                old = dm.group(0)
                if old not in known or date_key(old) >= newk:
                    continue  # не заголовок урока либо не строго старше — не отмена
                for other in secs:
                    if other["date"] == old and "superseded_by" not in other["full"]:
                        findings.add((old, sec["date"]))
    return sorted(findings)


def mark_text(text: str, old: str, new: str) -> tuple[str, int]:
    """Проставить `superseded_by` под каждым заголовком с датой old (ещё не помеченным)."""
    changed = 0
    for m in list(HEADING.finditer(text)):
        if m.group(1) != old:
            continue
        if "superseded_by" in text[m.end():m.end() + 200]:
            continue  # уже помечен — идемпотентно
        head = m.group(0)
        text = text.replace(head, f"{head}\n> superseded_by: {new}", 1)
        changed += 1
    return text, changed


def cmd_check() -> int:
    if not LOG.is_file():
        print(f"lessons_supersede: {LOG} нет — проверять нечего")
        return 0
    findings = find_unmarked(LOG.read_text(encoding="utf-8"))
    if not findings:
        print("lessons_supersede: отменённых без пометки не найдено — чисто")
        return 0
    print("lessons_supersede: уроки отменены более новыми, но без `superseded_by`:")
    for old, new in findings:
        print(f"  {old} отменён уроком {new} → пометить: "
              f"lessons_supersede.py --mark {old} {new}")
    return 1


def cmd_mark(old: str, new: str) -> int:
    if not LOG.is_file():
        print(f"lessons_supersede: {LOG} нет", file=sys.stderr)
        return 1
    text = LOG.read_text(encoding="utf-8")
    if not any(m.group(1) == old for m in HEADING.finditer(text)):
        print(f"lessons_supersede: урока с датой {old} в журнале нет", file=sys.stderr)
        return 1
    new_text, changed = mark_text(text, old, new)
    if changed == 0:
        print(f"lessons_supersede: урок {old} уже помечен — ничего не меняю")
        return 0
    LOG.write_text(new_text, encoding="utf-8")
    print(f"lessons_supersede: {old} помечен superseded_by: {new} ({changed} заголовк(ов))")
    return 0


def selftest() -> int:
    sample = (
        "# журнал\n\n"
        "## 03.08.2026 — старый стандарт четырёх гарнитур\n"
        "Правило: держать четыре шрифта в документе.\n\n"
        "## 04.08.2026 — единый шрифт PT Serif\n"
        "Решение владельца отменяет набор из четырёх гарнитур от 03.08.2026.\n\n"
        "## 23.07.2026 — ссылка на КС\n"
        "В дополнении дана формула с заменой на КС № 22-П от 26.05.2025.\n\n"
        "## 10.08.2026 — смотрит вперёд\n"
        "Правило отменяет прежнее от 12.08.2026 (дата новее — не отмена).\n\n"
        "## 12.08.2026 — более новый урок\n"
        "Просто текст.\n"
    )
    checks = []
    f = find_unmarked(sample)
    checks.append(("объявленная отмена по дате-заголовку поймана", ("03.08.2026", "04.08.2026") in f))
    checks.append(("отмена ровно одна", len(f) == 1))
    checks.append(("дата в прозе (не заголовок) не считается",
                   not any(o == "26.05.2025" for o, _ in f)))
    checks.append(("ссылка на более новый урок отменой не считается",
                   not any(o == "12.08.2026" for o, _ in f)))

    marked, n = mark_text(sample, "03.08.2026", "04.08.2026")
    checks.append(("пометка проставлена", n == 1 and "superseded_by: 04.08.2026" in marked))
    checks.append(("после пометки отмен нет", find_unmarked(marked) == []))
    checks.append(("повторная пометка идемпотентна", mark_text(marked, "03.08.2026", "04.08.2026")[1] == 0))
    # Строго старше: одинаковая дата отменой не считается.
    same = ("## 05.08.2026 — A\nотменяет правило от 05.08.2026.\n")
    checks.append(("одинаковая дата — не отмена", find_unmarked(same) == []))

    bad = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)} — без сети, на синтетике")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Стеречь отменённые уроки в журнале самообучения")
    ap.add_argument("--check", action="store_true", help="код 1, если есть отмена без пометки")
    ap.add_argument("--mark", nargs=2, metavar=("СТАРЫЙ", "НОВЫЙ"), help="проставить superseded_by")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.mark:
        return cmd_mark(a.mark[0], a.mark[1])
    if a.check:
        return cmd_check()
    ap.error("нужен --check, --mark или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
