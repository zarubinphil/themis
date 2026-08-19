#!/usr/bin/env python3
"""cli_seats.py — какая роль на каком CLI сидит. Границу тайны держит таблица, не память.

Причина разделения — не качество моделей, а граница процесса: `claude_guard.py` живёт
в нашем процессе, за его пределами наших ворот нет. Поэтому роль, видящая сырые
персональные данные, остаётся на claude, а наружу уходит только обезличенный текст.
Адвокатская тайна — ст. 8 ФЗ № 63-ФЗ; папка дела названа фамилией доверителя, и это
тоже персональные данные.

    --role ИМЯ [--json]   сиденье одной роли
    --list [--json]       все роли
    --selftest

Классы данных:
  pd      сырые материалы, ФИО, реквизиты, пути дел — только claude;
  text    обезличенный правовой вопрос, критика довода без имён — любой CLI;
  public  норма, Пленум, опубликованный акт — любой CLI;
  infra   код самой Фемиды, миграции, ревью — любой CLI.

Два правила, которые прибор исполняет механически:
  · роль класса `pd` сидит ТОЛЬКО на claude;
  · любая цепочка ЗАКАНЧИВАЕТСЯ claude — он сам харнесс и всегда доступен. Подмена
    claude чужим CLI при его недоступности запрещена: это смена гарантий, а не
    деградация, и заметить её по результату нельзя.
"""
from __future__ import annotations

import argparse
import json
import sys

CLAUDE = ["claude"]
# Порядок в цепочке — порядок попыток. Claude последний и всегда: он же харнесс.
SEATS = [
    # ── Сырые персональные данные: за границу процесса не выходят ────────────
    ("case-mapper", "pd", CLAUDE, "картограф читает материалы дела целиком"),
    ("case-reconciler", "pd", CLAUDE, "сверщик держит реквизиты, ФИО и суммы"),
    ("pdf-reader", "pd", CLAUDE, "читатель сканов первички"),
    ("image-reader", "pd", CLAUDE, "читатель фотографий документов"),
    ("docx-reader", "pd", CLAUDE, "читатель текстовых документов дела"),
    ("inbox-triage", "pd", CLAUDE, "переносит материалы, видит пути дел"),
    ("doc-drafter", "pd", CLAUDE, "максимум ПД: составление, humanizer, DocBuilder"),
    ("doc-reviewer", "pd", CLAUDE, "проверяет готовый документ с реквизитами"),
    ("hearing-prep", "pd", CLAUDE, "пакет к заседанию: стороны, суммы, даты"),
    ("archivist", "pd", CLAUDE, "пишет индексы и флаги по делам"),
    ("council-chair", "pd", CLAUDE, "председатель пишет артефакт под маркером"),
    # ── Обезличенный текст: границу переходит, файлов не пишет ───────────────
    ("hunter-leaf", "text", ["codex", "kimi", "claude"],
     "лист охотника: текст → текст, путей не видит"),
    ("council-reviewer", "text", ["codex", "kimi", "claude"],
     "рецензент совета: контракт уже текстовый и анонимный"),
    ("areopag-role", "text", ["codex", "claude"],
     "роль Ареопага в раундах 1-3: файлов не пишет; анонимность метода — риск пилота, "
     "у чужих моделей узнаваемый почерк"),
    ("second-opinion", "text", ["codex", "claude"],
     "второе мнение по документу: кросс-модельное ревью ловит слепые пятна"),
    # ── Публичное и инфраструктурное ─────────────────────────────────────────
    ("norm-lookup", "public", ["codex", "kimi", "claude"], "норма и Пленум опубликованы"),
    ("infra-review", "infra", ["codex", "kimi", "claude"], "код самой Фемиды, не дела"),
]


def seats() -> list:
    return [{"role": r, "data_class": k, "chain": list(c), "why": w} for r, k, c, w in SEATS]


def one(role: str) -> dict | None:
    for s in seats():
        if s["role"] == role:
            return s
    return None


def selftest() -> int:
    vse = seats()
    imena = [s["role"] for s in vse]
    assert len(imena) == len(set(imena)), "роль описана дважды — сиденье неопределённо"
    for s in vse:
        assert s["chain"], f"{s['role']}: пустая цепочка"
        assert s["chain"][-1] == "claude", \
            f"{s['role']}: цепочка не кончается claude — подмена харнесса"
        assert s["data_class"] in ("pd", "text", "public", "infra"), \
            f"{s['role']}: неизвестный класс {s['data_class']}"
        if s["data_class"] == "pd":
            assert s["chain"] == ["claude"], \
                f"{s['role']}: роль с сырыми ПД выпущена за границу процесса"
        assert s["why"], f"{s['role']}: сиденье без причины — правило без причины не переживёт спор"
    # Роли, пишущие артефакты под маркерами, обязаны быть pd: за границей процесса
    # хука нет, а значит нет и порядка шагов.
    for r in ("doc-drafter", "case-mapper", "council-chair", "archivist"):
        assert one(r) and one(r)["data_class"] == "pd", f"{r} обязан быть класса pd"
    assert one("hunter-leaf")["data_class"] == "text", "лист охотника не работает с ПД"
    assert one("net-takoy-roli") is None
    print(f"selftest пройден: {len(vse)} ролей, ПД не покидают claude, цепочки кончаются им")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Сиденья ролей по CLI.")
    ap.add_argument("--role")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.list:
        if a.json:
            print(json.dumps(seats(), ensure_ascii=False))
        else:
            for s in seats():
                print(f"{s['role']:18s} {s['data_class']:7s} {' → '.join(s['chain']):24s} {s['why']}")
        return 0
    if a.role:
        s = one(a.role)
        if not s:
            print(f"роль «{a.role}» не описана — сиденье не назначено, значит только claude",
                  file=sys.stderr)
            return 1
        print(json.dumps(s, ensure_ascii=False) if a.json
              else f"{s['role']}: {s['data_class']} · {' → '.join(s['chain'])} · {s['why']}")
        return 0
    ap.error("нужен --role, --list или --selftest")


if __name__ == "__main__":
    sys.exit(main())
