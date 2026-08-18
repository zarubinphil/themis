#!/usr/bin/env python3
"""budget_preflight.py — проверка бюджета ПЕРЕД запуском дорогого трека.

Зачем. FULL-прогон (полный рой охотников, советы, reconciler) стоит на порядок
дороже FAST и в разы дороже MICRO. Узнать о перерасходе ПОСЛЕ прогона (как это
делает token_ledger на чекпойнтах) — значит уже сжечь деньги. Этот прибор
считает наперёд: хватит ли остатка лимита на трек, и если нет — FULL не стартует
(код 3), а владелец решает, что делать. Дешёвая страховка перед дорогой работой.

Почему расход берётся с диска, а не из самоотчёта. Модель не знает, сколько уже
потрачено этой сессией: поле `usage` субагента видит только последнюю итерацию и
занижает расход в десятки раз (прецедент token_ledger). Поэтому уже-потраченное
берём тем же прибором, что и чекпойнты — token_ledger.collect по session-JSONL с
диска. Один источник правды на весь учёт токенов.

Оценка стоимости трека. TRACK_BUDGET из token_ledger задаёт типовой объём трека в
токенах (измеренная база проекта). Перевод токен→доллар — по фактическому
смешанному тарифу ЭТОЙ сессии (деньги/токены с диска): так оценка сама
подстраивается под реальную долю кеша и модельный микс прогона, без вшитых
допущений о составе. Пустой диск (первый запуск) → запасной тариф ниже.
"""
from __future__ import annotations

import argparse
import os
import sys

# Прибор лежит рядом с token_ledger в scripts/ — его каталог первым в sys.path,
# поэтому прямой импорт работает без возни с путями.
import token_ledger

# Фактический смешанный тариф проекта, $/млн токенов (замер 19.08.2026: $112.32 /
# 60.0 млн ток = 1.87). Кеш-чтение доминирует, оттого дёшево. Это лишь ЗАПАСНОЙ
# тариф на случай, когда на диске ещё нет ни одной сессии; при живой сессии тариф
# считается с диска и сам себя калибрует.
# ponytail: одна калибровочная константа; путь апгрейда — уже основной (тариф с диска).
DEFAULT_BLEND_PER_MTOK = 1.87


def track_estimate(track: str, blend_per_mtok: float) -> float:
    """Оценка полной стоимости трека в долларах."""
    return token_ledger.TRACK_BUDGET[track] / 1e6 * blend_per_mtok


def decide(track: str, limit: float, spent: float, blend_per_mtok: float) -> int:
    """Код возврата: 0 — остатка лимита хватает на трек, 3 — не хватает.

    Остаток = лимит − уже потрачено. Трек стартует, только если остаток покрывает
    его оценку целиком: начать и упереться в потолок посреди роя — худший исход,
    чем не начать.
    """
    remaining = limit - spent
    return 0 if remaining >= track_estimate(track, blend_per_mtok) else 3


def disk_spend_and_blend(cwd: str) -> tuple[float, float]:
    """С диска: (уже потрачено $, смешанный тариф $/млн ток). Нет данных → (0, запас)."""
    path = token_ledger.latest_session(cwd)
    if not path or not os.path.isfile(path):
        return 0.0, DEFAULT_BLEND_PER_MTOK
    try:
        rep = token_ledger.collect(path)
    except Exception as e:  # живой jsonl может быть оборван на полустроке — не падать
        print(f"budget_preflight: не удалось разобрать {os.path.basename(path)}: {e}; "
              "считаю потраченное = 0", file=sys.stderr)
        return 0.0, DEFAULT_BLEND_PER_MTOK
    tok = token_ledger.tokens(rep["total"])
    blend = (rep["money"] / tok * 1e6) if tok > 0 else DEFAULT_BLEND_PER_MTOK
    return rep["money"], blend


def run(track: str, limit: float | None, cwd: str) -> int:
    spent, blend = disk_spend_and_blend(cwd)
    est = track_estimate(track, blend)
    if limit is None:
        # Лимита нет — гейту нечего стеречь; печатаем оценку и не блокируем.
        print(f"budget_preflight: трек {track} ≈ ${est:.2f}, уже потрачено ${spent:.2f}. "
              "Лимит не задан (--limit) — оценка без гейта.")
        return 0
    rc = decide(track, limit, spent, blend)
    remaining = limit - spent
    verdict = ("хватает" if rc == 0 else "НЕ ХВАТАЕТ — трек не стартует, доложить владельцу")
    print(f"budget_preflight: трек {track} ≈ ${est:.2f}; лимит ${limit:.2f} − потрачено "
          f"${spent:.2f} = ${remaining:.2f} остатка → {verdict}.")
    return rc


def selftest() -> int:
    # Решение — чистая функция, проверяется на синтетике без сети и без диска.
    d = DEFAULT_BLEND_PER_MTOK
    checks = [
        ("FULL при грошовом лимите не стартует", decide("FULL", 0.01, 0.0, d) == 3),
        ("FAST при огромном лимите стартует", decide("FAST", 100000.0, 0.0, d) == 0),
        # Управляемая арифметика: blend 0.1 → FAST (40 млн ток) стоит ровно $4.
        ("оценка трека = объём × тариф", abs(track_estimate("FAST", 0.1) - 4.0) < 1e-9),
        ("на границе остатка трек стартует", decide("FAST", 5.0, 1.0, 0.1) == 0),   # остаток 4 == оценка 4
        ("потраченное съедает остаток", decide("FAST", 5.0, 2.0, 0.1) == 3),        # остаток 3 < оценка 4
        ("дороже трек — больше оценка",
         track_estimate("FULL", d) > track_estimate("FAST", d) > track_estimate("MICRO", d)),
    ]
    # Тариф действительно берётся с диска: собираем крошечную сессию и считаем blend.
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sess = os.path.join(tmp, "s.jsonl")
        with open(sess, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "requestId": "r1", "message": {
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 1000, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}) + "\n")
        rep = token_ledger.collect(sess)
        tok = token_ledger.tokens(rep["total"])
        blend = rep["money"] / tok * 1e6
        # sonnet input $3/млн → 1000 ток = $0.003, тариф = $3/млн.
        checks.append(("тариф считается с диска", abs(blend - 3.0) < 1e-6))

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'✓' if ok else '✗'} {n}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)} — решение без сети, тариф с диска")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка бюджета перед запуском трека (exit 3 при нехватке)")
    ap.add_argument("--track", choices=sorted(token_ledger.TRACK_BUDGET),
                    help="трек прогона: MICRO | FAST | FULL")
    ap.add_argument("--limit", type=float, metavar="ДОЛЛАРЫ", help="потолок расхода в долларах")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.track:
        ap.error("нужен --track (или --selftest)")
    return run(a.track, a.limit, os.getcwd())


if __name__ == "__main__":
    sys.exit(main())
