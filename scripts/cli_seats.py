#!/usr/bin/env python3
"""Совместимый просмотр классов ролей; выбор исполнителя делает cli_router."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REGISTRY = Path(__file__).with_name("cli_registry.json")
ROLES = [
    ("case-mapper", "pd", "картограф читает материалы дела целиком"),
    ("case-reconciler", "pd", "сверщик держит реквизиты, ФИО и суммы"),
    ("pdf-reader", "pd", "читатель сканов первички"),
    ("image-reader", "pd", "читатель фотографий документов"),
    ("docx-reader", "pd", "читатель текстовых документов дела"),
    ("inbox-triage", "pd", "переносит материалы, видит пути дел"),
    ("doc-drafter", "pd", "составление документа с реквизитами"),
    ("doc-reviewer", "pd", "проверяет документ с реквизитами"),
    ("hearing-prep", "pd", "пакет к заседанию"),
    ("archivist", "pd", "пишет индексы по делам"),
    ("council-chair", "pd", "пишет артефакт под маркером"),
    ("hunter-leaf", "text", "обезличенный правовой вопрос"),
    ("council-reviewer", "text", "рецензия обезличенного текста"),
    ("areopag-role", "text", "роль в анонимном раунде"),
    ("second-opinion", "text", "второе мнение по обезличенному тексту"),
    ("norm-lookup", "public", "опубликованная норма или акт"),
    ("infra-review", "infra", "код самой системы"),
]


def chain(data_class: str) -> list[str]:
    if data_class == "pd":
        return ["claude"]
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        registry = {}
    names = [name for name, entry in registry.items()
             if data_class in entry.get("data_classes", []) and name != "claude"]
    return names + ["claude"]


def seats() -> list[dict]:
    return [{"role": role, "data_class": data_class, "chain": chain(data_class), "why": why}
            for role, data_class, why in ROLES]


def one(role: str) -> dict | None:
    return next((seat for seat in seats() if seat["role"] == role), None)


def selftest() -> int:
    all_seats = seats()
    assert len({seat["role"] for seat in all_seats}) == len(all_seats)
    assert all(seat["chain"][-1] == "claude" for seat in all_seats)
    assert all(seat["chain"] == ["claude"] for seat in all_seats if seat["data_class"] == "pd")
    assert one("hunter-leaf")["data_class"] == "text"
    print(f"selftest пройден: {len(all_seats)} ролей, выбор CLI делегирован роутеру")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Классы ролей; CLI выбирает cli_router.")
    ap.add_argument("--role")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    result = seats() if a.list else one(a.role) if a.role else None
    if result is None:
        ap.error("нужен --role, --list или --selftest")
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    elif isinstance(result, list):
        for seat in result:
            print(f"{seat['role']}: {seat['data_class']} · {' → '.join(seat['chain'])}")
    else:
        print(f"{result['role']}: {result['data_class']} · {' → '.join(result['chain'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
