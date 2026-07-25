#!/usr/bin/env python3
"""Машина состояний протокола Фемиды — детерминированный статус дела.

Использование: python3 scripts/themis_status.py cases/{клиент}/{дело}

Читает маркеры с ДИСКА (не из памяти модели) и печатает: статус каждого шага
и СЛЕДУЮЩИЙ ШАГ. Фемида обязана работать по этому выводу — это единственный
источник правды о состоянии протокола.
"""
import datetime
import re
import sys
from pathlib import Path


def has_marker(f: Path, pattern: str) -> bool:
    try:
        return bool(re.search(pattern, f.read_text(encoding="utf-8")))
    except OSError:
        return False


def age_days(f: Path) -> int:
    try:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        return (datetime.datetime.now() - mtime).days
    except OSError:
        return 10**6


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: themis_status.py cases/{клиент}/{дело}", file=sys.stderr)
        return 1
    case = Path(sys.argv[1]).resolve()
    if not case.is_dir():
        print(f"СТОП: {case} не существует. Сначала /new-case.", file=sys.stderr)
        return 1

    ctx = case / "01_context"
    km, pr, pos = ctx / "knowledge-map.md", ctx / "practice.md", ctx / "positions.md"
    case_md = case / "_case.md"

    s1 = has_marker(km, r"## КАРТА ГОТОВА ✓")
    s2 = has_marker(pr, r"## СОВЕТ ЗАВЕРШ")
    pr_fresh = s2 and age_days(pr) <= 30
    s3 = has_marker(pos, r"СОГЛАСОВАНО СОВЕТОМ")
    s3_skip = has_marker(case_md, r"position-council пропущен")

    level = "?"
    try:
        m = re.search(r"\bL[123]\b", case_md.read_text(encoding="utf-8"))
        if m:
            level = m.group(0)
    except OSError:
        pass
    s3_not_needed = level == "L1"

    drafts = sorted((case / "03_drafts").glob("*.md")) if (case / "03_drafts").is_dir() else []
    drafts = [d for d in drafts if "_working" not in d.parts and "_baselines" not in d.parts]
    review_log = case / "03_drafts" / "_working" / "review_log.md"
    approved = has_marker(review_log, r"ГОТОВ К ПОДАЧЕ")

    def mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    print(f"# Статус протокола — {case.name} (уровень: {level})")
    print(f"Шаг 1 Карта:     {mark(s1)}  knowledge-map.md {'с маркером' if s1 else '— нет маркера КАРТА ГОТОВА'}")
    fresh_note = "" if not s2 else (" (свежая, ≤30 дн.)" if pr_fresh else f" (устарела: {age_days(pr)} дн. — проверить актуальность)")
    print(f"Шаг 2 Практика:  {mark(s2)}  practice.md {'с маркером' + fresh_note if s2 else '— нет маркера СОВЕТ ЗАВЕРШЕН'}")
    if s3_not_needed:
        print("Шаг 3 Позиция:   —  L1: не требуется")
    elif s3_skip:
        print("Шаг 3 Позиция:   ✓  пропуск зафиксирован в _case.md")
    else:
        print(f"Шаг 3 Позиция:   {mark(s3)}  positions.md {'СОГЛАСОВАНО СОВЕТОМ' if s3 else '— нет маркера'}")
    print(f"Шаг 4 Черновики: {mark(bool(drafts))}  {len(drafts)} файл(ов) в 03_drafts")
    print(f"Шаг 5 Кони:      {mark(approved)}  {'ГОТОВ К ПОДАЧЕ в review_log' if approved else 'вердикта ГОТОВ К ПОДАЧЕ нет'}")

    if not s1:
        nxt = "Шаг 1 — case-mapper (карта дела)"
    elif not s2:
        nxt = "Шаг 2 — охота за практикой (FAST: 1 охотник; FULL: 3 + /askacouncil)"
    elif not (s3 or s3_skip or s3_not_needed):
        nxt = "Шаг 3 — /position-council (или зафиксировать пропуск в _case.md)"
    elif not drafts:
        nxt = "Шаг 4 — /draft (doc-drafter)"
    elif not approved:
        nxt = "Шаг 5 — doc-reviewer (Кони) до вердикта ГОТОВ К ПОДАЧЕ"
    else:
        nxt = "/finalize — пакет в 02_hearings (guard правок доверителя)"
    print(f"\nСЛЕДУЮЩИЙ ШАГ: {nxt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
