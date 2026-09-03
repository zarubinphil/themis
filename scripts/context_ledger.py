#!/usr/bin/env python3
"""context_ledger.py — цена повторной доставки контекста и потолок контекста фазы.

ЗАЧЕМ. Самая дорогая статья прогона 02.09.2026 не была названа ни одной задачей:
из 360,17 $ — 200,55 $ (55,7 %) ушло на ПОВТОРНУЮ доставку уже прочитанного
контекста Opus. Контекст оркестратора рос с 96 480 до 503 962 токенов без единого
сброса и переоплачивался на каждом из 189 вызовов; 104 запроса шли с контекстом
свыше 300 000. `token_ledger.py` считает, СКОЛЬКО потрачено по шагам, но не считает,
сколько из этого — одно и то же, доставленное заново.

ЧТО СЧИТАЕТ (из журнала, не оценка).
1. Пол контекста потока = МИНИМАЛЬНЫЙ контекст запроса за все вызовы этого потока
   (input + cache-write + cache-read). Это то, что доставлялось ВСЕГДА: системные
   преамбулы, CLAUDE.md, описания инструментов. Умноженный на число вызовов, он
   и есть счет за повторную доставку — нижняя граница, а не догадка: реальный
   повтор больше на все, что накопилось сверх пола и тоже возилось заново.
2. Потолок контекста фазы. Максимальный контекст запроса в рамках шага протокола.
   Превышение потолка — НЕНУЛЕВОЙ КОД (3), а не строчка в отчете.

Прибор читает те же session-JSONL, что и token_ledger, и переиспользует его разбор
(дедупликация по requestId, тарифы, привязка вызова к шагу).

Использование:
    context_ledger.py                     # свежая сессия проекта
    context_ledger.py SESSION.jsonl       # конкретная сессия
    context_ledger.py --ctx-limit 150000  # свой потолок фазы
    context_ledger.py --json
    context_ledger.py --selftest

Код возврата: 0 — потолок держится; 3 — пробит (СТОП, разгрузка); 1 — данных нет.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile

import _obshee as obs
import token_ledger as tl

# Потолок контекста ОДНОГО запроса. Замер 02.09.2026: 104 запроса из 189 шли с
# контекстом свыше 300 000, и каждый следующий вызов переоплачивал весь этот вес.
# 200 000 — порог, после которого фазу пора закрывать и выгружать состояние.
CTX_LIMIT_DEFAULT = 200_000

# Сколько потоков показывать в разрезе преамбулы.
STREAMS_SHOWN = 8


def ctx_of(u: dict) -> int:
    """Вес контекста, доставленного в ОДНОМ запросе."""
    return u["in"] + u["cw"] + u["cr"]


def preamble_of(turns: list) -> dict:
    """Пол контекста потока × число вызовов = счет за повторную доставку.

    Первая доставка пола платится по ставке cache-write, каждая следующая — по
    ставке cache-read. Ставка берется по модели самого вызова, а не средняя.
    """
    if not turns:
        return {"floor": 0, "calls": 0, "tokens": 0, "money": 0.0}
    floor = min(ctx_of(u) for u, _model, _step in turns)
    money = 0.0
    for i, (_u, model, _step) in enumerate(turns):
        rate = tl.RATES[tl.rate_key(model)]
        money += floor * (rate[2] if i == 0 else rate[3]) / 1e6
    return {"floor": floor, "calls": len(turns), "tokens": floor * len(turns), "money": money}


def collect(session_path: str) -> dict:
    """Разрез по потокам: главный поток и каждый транскрипт субагента отдельно."""
    session_dir = os.path.join(os.path.dirname(session_path),
                               os.path.basename(session_path)[: -len(".jsonl")])
    _models, spawns, _own, main_turns = tl.scan_file(session_path)
    names = {s["agent_id"]: s["agent_type"] for s in spawns}

    streams = [{"name": "основной поток", "turns": main_turns}]
    transcripts = (glob.glob(os.path.join(session_dir, "subagents", "agent-*.jsonl"))
                   + glob.glob(os.path.join(session_dir, "subagents", "workflows", "*",
                                            "agent-*.jsonl")))
    for path in sorted(transcripts):
        agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        _m, child_spawns, own, turns = tl.scan_file(path)
        names.update({s["agent_id"]: s["agent_type"] for s in child_spawns})
        name = names.get(agent_id) or own["type"] or f"агент {agent_id[:8]}"
        streams.append({"name": name, "turns": turns})

    for s in streams:
        s.update(preamble_of(s["turns"]))
        s["max_ctx"] = max((ctx_of(u) for u, _m, _st in s["turns"]), default=0)

    total = tl.blank()
    money = 0.0
    for s in streams:
        for u, model, _step in s["turns"]:
            tl.add(total, u)
            money += tl.cost(u, model)

    # Потолок фазы считаем по ГЛАВНОМУ потоку: шаг там липкий (сигнал — путь и
    # команда вызова), и именно его контекст растет без сброса. У субагента
    # контекст свой и умирает вместе с ним — он идет отдельной строкой.
    by_step: dict[str, dict] = {}
    for u, _model, step in main_turns:
        row = by_step.setdefault(step or "прочее", {"max": 0, "calls": 0, "over": 0})
        row["max"] = max(row["max"], ctx_of(u))
        row["calls"] += 1

    return {
        "session": os.path.basename(session_path),
        "total": total,
        "money": money,
        "streams": [{k: v for k, v in s.items() if k != "turns"} for s in streams],
        "by_step_ctx": by_step,
        "preamble_tokens": sum(s["tokens"] for s in streams),
        "preamble_money": sum(s["money"] for s in streams),
        "sub_max_ctx": max((s["max_ctx"] for s in streams[1:]), default=0),
    }


def over_calls(main_turns: list, limit: int) -> dict:
    """Число вызовов сверх потолка по шагам — считается по самим вызовам."""
    out: dict[str, int] = {}
    for u, _model, step in main_turns:
        key = step or "прочее"
        out[key] = out.get(key, 0) + (1 if ctx_of(u) > limit else 0)
    return out


def ctx_verdict(rep: dict, limit: int) -> tuple[str, int]:
    """Вердикт по потолку контекста фазы: (что печатать, код возврата)."""
    if not rep["by_step_ctx"]:
        return ("\nпотолок контекста: ДАННЫХ НЕТ — ни одного запроса в разборе. "
                "Это НЕ «уложились».", obs.KOD_NE_RABOTAL)
    breached = {s: r for s, r in rep["by_step_ctx"].items() if r["max"] > limit}
    if not breached:
        top = max(r["max"] for r in rep["by_step_ctx"].values())
        return (f"\nпотолок контекста {limit:,}: держится (максимум {top:,}).".replace(",", " "),
                obs.KOD_OK)
    worst = max(breached.items(), key=lambda kv: kv[1]["max"])
    return (f"\nпотолок контекста {limit:,} ПРОБИТ на шагах: ".replace(",", " ")
            + ", ".join(f"{s} ({r['max']:,})".replace(",", " ")
                        for s, r in sorted(breached.items()))
            + f".\nСТОП: фаза «{worst[0]}» тащит контекст дальше, чем стоит платить. "
              "Разгрузка обязательна ДО следующего шага — см. «Разгрузка контекста» "
              "в .claude/CLAUDE.md.", obs.KOD_STOP)


def handoff_state(case: str) -> tuple[str, bool]:
    """Выгрузка фазы: (путь, записана ли она ПОЗЖЕ последнего артефакта фазы).

    Свежесть меряется mtime: выгрузка, написанная до последней правки контекста
    дела, описывает не то состояние, в котором сессия закрывается.
    """
    ctx_dir = os.path.join(case, ".agent", "context")
    path = os.path.join(ctx_dir, "handoff.md")
    if not os.path.isfile(path):
        return path, False
    newest = max((os.path.getmtime(p) for p in glob.glob(os.path.join(ctx_dir, "*.md"))
                  if os.path.basename(p) != "handoff.md"), default=0.0)
    return path, os.path.getmtime(path) >= newest


def render(rep: dict, limit: int, over: dict) -> int:
    t = rep["total"]
    total_tok = max(tl.tokens(t), 1)
    print(f"сессия: {rep['session']}   потоков: {len(rep['streams'])}")
    print(f"всего: {tl.tokens(t):,} токенов ≈ ${rep['money']:,.2f}".replace(",", " "))

    print(f"\n{'поток':<26}{'пол ctx':>12}{'вызовов':>9}{'преамбула':>14}{'≈$':>10}")
    for s in sorted(rep["streams"], key=lambda x: -x["tokens"])[:STREAMS_SHOWN]:
        print(f"{s['name'][:25]:<26}{s['floor']:>12,}{s['calls']:>9}"
              f"{s['tokens']:>14,}{s['money']:>10,.2f}".replace(",", " "))
    share = rep["preamble_tokens"] / total_tok * 100
    print(f"{'ИТОГО преамбулы':<26}{'':>12}{'':>9}"
          f"{rep['preamble_tokens']:>14,}{rep['preamble_money']:>10,.2f}".replace(",", " "))
    print(f"  это {share:.1f}% всего трафика сессии — доставка одного и того же заново. "
          "Пол потока (минимальный контекст его запроса) × число вызовов; "
          "реальный повтор БОЛЬШЕ на все, что наросло сверх пола.")

    print(f"\n{'шаг (фаза)':<18}{'макс ctx':>12}{'вызовов':>9}{'сверх потолка':>15}")
    for step in [s for s in tl.STEP_ORDER if s in rep["by_step_ctx"]] + \
                sorted(s for s in rep["by_step_ctx"] if s not in tl.STEP_ORDER):
        row = rep["by_step_ctx"][step]
        mark = " ✗" if row["max"] > limit else ""
        print(f"{step:<18}{row['max']:>12,}{row['calls']:>9}"
              f"{over.get(step, 0):>15}{mark}".replace(",", " "))
    if rep["sub_max_ctx"]:
        print(f"{'субагенты (макс)':<18}{rep['sub_max_ctx']:>12,}".replace(",", " "))

    text, code = ctx_verdict(rep, limit)
    print(text)
    return code


def gate(rep: dict, limit: int, case: str) -> tuple[str, int]:
    """Ворота перехода к следующей фазе. Пробитый потолок закрыт, пока не записана
    свежая выгрузка `.agent/context/handoff.md`: контекст в живой сессии не
    уменьшается сам, единственный способ его сбросить — передать состояние файлом
    и продолжить чистой сессией либо субагентом."""
    code = ctx_verdict(rep, limit)[1]
    if code != obs.KOD_STOP:
        return "", code
    path, fresh = handoff_state(case)
    if not fresh:
        return (f"ВЫГРУЗКА НЕ ЗАПИСАНА: {path} "
                + ("устарел (артефакты фазы новее)." if os.path.isfile(path) else "отсутствует.")
                + "\nЧто в нем: что сделано · что дальше (1-3 шага) · ключевое состояние · "
                  "ССЫЛКИ на файлы, НЕ их содержимое.", obs.KOD_STOP)
    return (f"Выгрузка записана: {path}. Следующая фаза — В ЧИСТОЙ СЕССИИ "
            "или субагентом; продолжать этой сессией нельзя, ее контекст уже оплачен "
            "на каждом будущем вызове.", obs.KOD_STOP)


def selftest() -> int:
    """Проверка без сети: пол контекста, счет за повтор, коды потолка."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = "s1"
        main = os.path.join(tmp, f"{sid}.jsonl")
        sub_dir = os.path.join(tmp, sid, "subagents")
        os.makedirs(sub_dir)

        def assistant(model, i, o, cw, cr, rid, tool=None):
            msg = {"model": model, "usage": {
                "input_tokens": i, "output_tokens": o,
                "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}
            if tool:
                msg["content"] = [{"type": "tool_use", "id": "tu", "name": tool[0],
                                   "input": tool[1]}]
            return json.dumps({"type": "assistant", "requestId": rid, "message": msg},
                              ensure_ascii=False) + "\n"

        with open(main, "w", encoding="utf-8") as fh:
            # три вызова одного потока: пол контекста = 100 (10+40+50)
            fh.write(assistant("claude-opus-5", 10, 5, 40, 50, "r1"))
            fh.write(assistant("claude-opus-5", 10, 5, 40, 50, "r1"))  # дубль requestId
            fh.write(assistant("claude-opus-5", 10, 5, 0, 190, "r2",
                               ("Read", {"file_path": "cases/К/Д/.agent/context/practice.md"})))
            fh.write(assistant("claude-opus-5", 10, 5, 0, 490, "r3"))

        with open(os.path.join(sub_dir, "agent-aaa.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(assistant("claude-sonnet-5", 1, 1, 9, 0, "r4"))

        rep = collect(main)
        main_stream = rep["streams"][0]
        limit = 300
        over = over_calls(tl.scan_file(main)[3], limit)
        r = tl.RATES["opus"]
        want_money = 100 * r[2] / 1e6 + 2 * 100 * r[3] / 1e6

        checks = [
            ("пол контекста = минимальный контекст запроса", main_stream["floor"] == 100),
            ("дубль по requestId не раздувает число вызовов", main_stream["calls"] == 3),
            ("счет за повтор = пол × вызовы", main_stream["tokens"] == 300),
            ("первая доставка по cache-write, следующие по cache-read",
             abs(main_stream["money"] - want_money) < 1e-12),
            ("субагент считается своим потоком, не полом главного",
             len(rep["streams"]) == 2 and rep["streams"][1]["floor"] == 10),
            ("максимум контекста виден", main_stream["max_ctx"] == 500),
            ("шаг фазы взят из сигнала вызова", "2 практика" in rep["by_step_ctx"]),
            ("вызовы сверх потолка посчитаны по вызовам, а не по шагам",
             over.get("2 практика") == 1),
            ("потолок пробит → код 3", ctx_verdict(rep, limit)[1] == obs.KOD_STOP),
            ("потолок держится → код 0", ctx_verdict(rep, 10_000)[1] == obs.KOD_OK),
            ("пустой разбор — «данных нет», а не «уложились»",
             ctx_verdict({"by_step_ctx": {}}, limit)[1] == obs.KOD_NE_RABOTAL),
            ("итог преамбулы складывает все потоки",
             rep["preamble_tokens"] == 300 + 10),
            ("деньги преамбулы ненулевые", rep["preamble_money"] > 0),
            ("пустой поток не падает", preamble_of([])["tokens"] == 0),
        ]
        bad = [n for n, ok in checks if not ok]
        for n, ok in checks:
            print(f"  {'✓' if ok else '✗'} {n}")
        if bad:
            print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
            return obs.KOD_OSHIBKA
        print(f"selftest пройден: {len(checks)}/{len(checks)}")
        return obs.KOD_OK


def main() -> int:
    ap = obs.parser("Цена повторной доставки контекста и потолок контекста фазы")
    ap.add_argument("session", nargs="?", help="путь к session.jsonl (по умолчанию — свежая)")
    ap.add_argument("--ctx-limit", type=int, default=CTX_LIMIT_DEFAULT,
                    help=f"потолок контекста запроса (по умолчанию {CTX_LIMIT_DEFAULT})")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--gate", metavar="ДЕЛО",
                    help="ворота фазы: пробитый потолок требует свежей .agent/context/handoff.md")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    path = args.session or tl.latest_session(os.getcwd())
    if not path or not os.path.isfile(path):
        print(f"session.jsonl не найден ({path}). Передай путь явно.", file=sys.stderr)
        return obs.KOD_OSHIBKA

    rep = collect(path)
    over = over_calls(tl.scan_file(path)[3], args.ctx_limit)
    if args.json:
        rep["over_calls"] = over
        rep["ctx_limit"] = args.ctx_limit
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return ctx_verdict(rep, args.ctx_limit)[1]
    if args.gate:
        code = render(rep, args.ctx_limit, over)
        note = gate(rep, args.ctx_limit, args.gate)[0]
        if note:
            print(note)
        return code
    return render(rep, args.ctx_limit, over)


if __name__ == "__main__":
    obs.zavershit(main)
