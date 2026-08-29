#!/usr/bin/env python3
"""token_audit.py — НЕЗАВИСИМАЯ сверка расхода токенов.

Зачем. token_ledger считает деньги по шагам, и его цифра идет в бюджетные гейты.
Но кто проверяет сам ledger? Одна реализация, ошибись она в дедупликации или
тарифе, врет молча и уверенно. Этот прибор — второй, НАМЕРЕННО отдельный счетчик:
свой проход по session-JSONL, своя дедупликация, своя тарифная таблица. Две
независимые реализации, сходящиеся в пределах допуска, дают доверие к цифре;
расхождение (`--compare` → код 1) означает баг в одной из них — сигнал разобраться.

Почему код здесь дублирует token_ledger, а не переиспользует его. Независимость —
весь смысл прибора. Импортируй мы счетчик ledger — сверяли бы его с самим собой,
и общий баг остался бы невидимым. Дублирование логики подсчета тут не нарушение
DRY, а требование: проверяющий обязан быть отдельным от проверяемого. Тариф
(реальные цены Anthropic) у обоих один — иначе они не сошлись бы никогда; это
внешний факт, а не разделяемый код.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# $/млн токенов: [input, output, cache-write, cache-read]. Реальные цены Anthropic —
# те же, что у token_ledger: сверяем СПОСОБ подсчета, а не выдумываем свой прайс.
RATES = {
    "opus": [15.0, 75.0, 18.75, 1.50],
    "sonnet": [3.0, 15.0, 3.75, 0.30],
    "haiku": [1.0, 5.0, 1.25, 0.10],
}
DEFAULT_TOL = 0.02  # 2% — допуск на мелкие расхождения двух реализаций


def _rate(model: str) -> list[float]:
    ml = (model or "").lower()
    for k in RATES:
        if k in ml:
            return RATES[k]
    return RATES["opus"]  # неизвестную модель считаем по верхней ставке — не занизить


def _read_jsonl(path: str):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # оборванная строка живой сессии — не повод падать


def _project_dir(cwd: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(os.path.expanduser("$HOME/.claude/projects"), key)


def _latest_session(cwd: str) -> str | None:
    files = glob.glob(os.path.join(_project_dir(cwd), "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def _transcripts(session_path: str) -> list[str]:
    """Файлы субагентов сессии: обычные и глубже — воркфлоу-рой."""
    base = session_path[:-len(".jsonl")] if session_path.endswith(".jsonl") else session_path
    return (sorted(glob.glob(os.path.join(base, "subagents", "agent-*.jsonl")))
            + sorted(glob.glob(os.path.join(base, "subagents", "workflows", "*", "agent-*.jsonl"))))


def scan_files(files: list[str]) -> tuple[int, float]:
    """(всего токенов, деньги $) по списку файлов.

    Дедуп по requestId — ПОФАЙЛОВО: одна ассистентская реплика пишется в jsonl по
    нескольку раз с одним requestId (иначе завышение в 2,4 раза). Синтетические
    строки и строки без usage не в счет.
    """
    total = 0
    money = 0.0
    for path in files:
        seen: dict[str, tuple[dict, str]] = {}
        for entry in _read_jsonl(path):
            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message") or {}
            if msg.get("model") == "<synthetic>":
                continue
            u = msg.get("usage")
            if not u:
                continue
            key = entry.get("requestId") or entry.get("uuid")
            seen[key] = (u, msg.get("model") or "?")
        for u, model in seen.values():
            i = u.get("input_tokens", 0)
            o = u.get("output_tokens", 0)
            cw = u.get("cache_creation_input_tokens", 0)
            cr = u.get("cache_read_input_tokens", 0)
            total += i + o + cw + cr
            r = _rate(model)
            money += (i * r[0] + o * r[1] + cw * r[2] + cr * r[3]) / 1e6
    return total, money


def scan_session(session_path: str) -> tuple[int, float]:
    return scan_files([session_path] + _transcripts(session_path))


def audit(cwd: str) -> tuple[int, float]:
    path = _latest_session(cwd)
    if not path or not os.path.isfile(path):
        return 0, 0.0
    return scan_session(path)


def agree(mine: float, ref: float, tol: float) -> bool:
    """Сходятся ли числа в пределах относительного допуска."""
    if ref == 0:
        return mine == 0
    return abs(mine - ref) / abs(ref) <= tol


def cmd_json(cwd: str) -> int:
    total, money = audit(cwd)
    print(json.dumps({"total": total, "money": round(money, 6)}, ensure_ascii=False))
    return 0


def cmd_compare(cwd: str, tol: float) -> int:
    mine_tot, mine_money = audit(cwd)
    # token_ledger импортируется ТОЛЬКО здесь — эталон для сверки. Путь --json его
    # не касается и остается независимым счетчиком.
    import token_ledger
    path = token_ledger.latest_session(cwd)
    if not path:
        print("token_audit: сессий проекта не найдено — сверять нечего", file=sys.stderr)
        return 0
    rep = token_ledger.collect(path)
    ref_tot = token_ledger.tokens(rep["total"])
    ref_money = rep["money"]
    ok_tot = agree(mine_tot, ref_tot, tol)
    ok_money = agree(mine_money, ref_money, tol)
    print(f"token_audit: токены аудит {mine_tot:,} / ledger {ref_tot:,} — "
          f"{'сходится' if ok_tot else 'РАСХОЖДЕНИЕ'}".replace(",", " "))
    print(f"token_audit: деньги аудит ${mine_money:.2f} / ledger ${ref_money:.2f} — "
          f"{'сходится' if ok_money else 'РАСХОЖДЕНИЕ'}")
    if ok_tot and ok_money:
        return 0
    print(f"token_audit: расхождение больше допуска {tol*100:.0f}% — баг в одном из "
          "счетчиков, разобраться", file=sys.stderr)
    return 1


def selftest() -> int:
    import tempfile
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        main = os.path.join(tmp, "s.jsonl")
        sub = os.path.join(tmp, "s", "subagents")
        os.makedirs(sub)

        def line(**kw):
            return json.dumps(kw, ensure_ascii=False) + "\n"

        def asst(model, i, o, cw, cr, rid):
            return line(type="assistant", requestId=rid, message={
                "model": model, "usage": {"input_tokens": i, "output_tokens": o,
                                          "cache_creation_input_tokens": cw,
                                          "cache_read_input_tokens": cr}})

        with open(main, "w", encoding="utf-8") as fh:
            fh.write(asst("claude-sonnet-5", 10, 20, 30, 40, "req_a"))
            fh.write(asst("claude-sonnet-5", 10, 20, 30, 40, "req_a"))   # дубль → раз
            fh.write(asst("<synthetic>", 999, 999, 999, 999, "req_syn"))  # мимо
            fh.write(line(type="user", message={"content": "не ассистент"}))  # мимо
        with open(os.path.join(sub, "agent-x.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(asst("claude-haiku-4-5", 1000, 2000, 3000, 4000, "req_b"))

        tot, money = scan_session(main)
        # sonnet 100 ток = (10*3+20*15+30*3.75+40*0.3)/1e6 = 0.0004545
        # haiku 10000 ток = (1000*1+2000*5+3000*1.25+4000*0.1)/1e6 = 0.01515
        checks += [
            ("дубль по requestId схлопнут, subagent учтен", tot == 100 + 10000),
            ("деньги по тарифам моделей", abs(money - (0.0004545 + 0.01515)) < 1e-9),
        ]

        # Неизвестная модель — по верхней (opus) ставке, как и ledger.
        unk = os.path.join(tmp, "u.jsonl")
        with open(unk, "w", encoding="utf-8") as fh:
            fh.write(asst("claude-fable-5", 1_000_000, 0, 0, 0, "req_u"))
        _, um = scan_files([unk])
        checks.append(("неизвестная модель по верхней ставке", abs(um - 15.0) < 1e-9))

    checks += [
        ("совпадение в пределах допуска — сходится", agree(100, 101, DEFAULT_TOL)),
        ("расхождение сверх допуска — не сходится", not agree(100, 105, DEFAULT_TOL)),
        ("нули сходятся", agree(0, 0, DEFAULT_TOL)),
        ("ноль против ненуля — расхождение", not agree(5, 0, DEFAULT_TOL)),
    ]
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'✓' if ok else '✗'} {n}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)} — свой счетчик без сети")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Независимая сверка расхода токенов")
    ap.add_argument("--json", action="store_true", help="свой подсчет: {total, money}")
    ap.add_argument("--compare", action="store_true", help="сверить с token_ledger (exit 1 при расхождении)")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL, help="допуск расхождения (доля, по умолчанию 0.02)")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.compare:
        return cmd_compare(os.getcwd(), a.tol)
    if a.json:
        return cmd_json(os.getcwd())
    ap.error("нужен --json, --compare или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
