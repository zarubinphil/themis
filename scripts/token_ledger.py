#!/usr/bin/env python3
"""token_ledger.py — инструментальный замер расхода токенов по шагам конвейера Фемиды.

Читает session-JSONL Claude Code (основной поток + транскрипты субагентов) и выдаёт
фактические input/output/cache-write/cache-read по шагам протокола, а не самоотчёт модели.

Зачем: до этого скрипта единственным источником цифр было поле `usage`, которое субагент
возвращает в основной поток, плюс оценка основного потока на глаз. Два дефекта такого учёта:
  1. Основной поток вообще не измерялся — а он даёт основную массу cache-read.
  2. `~/.claude/scripts/token-spend.sh` суммирует КАЖДУЮ строку с usage, а одна и та же
     ассистентская реплика пишется в jsonl по нескольку раз с одним requestId.
     На живой сессии это давало 174 212 561 вместо 72 634 172 — завышение в 2,4 раза.

Использование:
    token_ledger.py                       # свежая сессия проекта в cwd
    token_ledger.py SESSION.jsonl         # конкретная сессия
    token_ledger.py --all                 # все сессии проекта, сводно
    token_ledger.py --track FAST          # + вердикт по цели трека (exit 3 при перерасходе)
    token_ledger.py --json                # машинный вывод
    token_ledger.py --selftest            # проверка без сети и без реальных сессий
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

# Цена за миллион токенов: [input, output, cache-write, cache-read]
RATES = {
    "opus": [15.0, 75.0, 18.75, 1.50],
    "sonnet": [3.0, 15.0, 3.75, 0.30],
    "haiku": [1.0, 5.0, 1.25, 0.10],
}

# Пороги трека — ИЗМЕРЕННЫЕ базовые линии по 26 сессиям проекта на 03.08.2026,
# всего токенов (input + output + cache-write + cache-read).
#
# ВНИМАНИЕ: цифры вида «FAST ~400k, FULL ~1,5M» из optimization-plan.md — ДРУГОЙ ПРИБОР.
# Они складывали поле `usage`, которое субагент возвращает в основной поток, а оно
# содержит только последнюю итерацию агента и вовсе не видит основной поток. Реальный
# трафик тех же прогонов: FULL-сессии 125-190 млн, FAST 22-33 млн, короткая работа 1-10 млн.
# Расхождение ×40-100. Порог здесь = сегодняшняя норма: превышение значит «хуже обычного»,
# а не «хуже идеала». Цель плана (минус 60-80%) — половина этих чисел и ниже.
TRACK_BUDGET = {"MICRO": 10_000_000, "FAST": 40_000_000, "FULL": 200_000_000}

# Шаг протокола по типу агента. Порядок ключей = порядок в отчёте.
STEP_BY_AGENT = {
    "inbox-triage": "0 интейк",
    "case-mapper": "1 карта",
    "case-reconciler": "1 карта",
    "docx-reader": "1 карта",
    "pdf-reader": "1 карта",
    "image-reader": "1 карта",
    "practice-hunter-classic": "2 практика",
    "practice-hunter-skeptic": "2 практика",
    "practice-hunter-tactical": "2 практика",
    "doc-drafter": "4 составление",
    "doc-reviewer": "5 проверка",
    "hearing-prep": "5 проверка",
    "archivist": "6 архив",
}

# Для general-purpose тип не говорит ничего — шаг берём из описания вызова.
STEP_BY_DESCRIPTION = [
    (r"ареопаг|позици|урусов|муравьев|маклаков|фойницк|владимиров|прокурор|тактик", "3 позиция"),
    (r"совет|рецензент|председател|askacouncil|council", "2 практика"),
    (r"охотник|практик|hunter", "2 практика"),
    (r"картир|карта дела|mapper|читател|reader", "1 карта"),
    (r"составл|черновик|draft|иск|жалоб|ходатайств", "4 составление"),
    (r"провер|ревью|review|кони", "5 проверка"),
    (r"архив|индекс", "6 архив"),
    (r"инбокс|inbox|входящ", "0 интейк"),
    # Работа НАД системой, а не по делу: аудит, исследование, справки, правка промптов,
    # legal design. Раньше валилась в «прочее» и создавала впечатление дырявой
    # атрибуции шагов, хотя шагов протокола там нет вовсе.
    (r"исследоват|исследован|аудит|разведк|справк|фактур|legal design|дизайн|"
     r"промпт|фабрик|синтезатор|рецензент|верификатор|migrat|рефактор", "система"),
]

# Доля нераспределённого расхода, выше которой цифры по шагам нельзя считать
# основанием для решений. Замер 03.08.2026 на живой сессии: 16,3% в «прочее».
OTHER_ALERT_SHARE = 5.0

STEP_ORDER = ["основной поток", "0 интейк", "1 карта", "2 практика", "3 позиция",
              "4 составление", "5 проверка", "6 архив", "система", "прочее"]


def model_key(model: str) -> str:
    ml = (model or "").lower()
    for k in RATES:
        if k in ml:
            return k
    return "opus"  # неизвестная модель считается по верхней ставке, чтобы не занизить


def cost(u: dict, model: str) -> float:
    r = RATES[model_key(model)]
    return (u["in"] * r[0] + u["out"] * r[1] + u["cw"] * r[2] + u["cr"] * r[3]) / 1e6


def blank() -> dict:
    return {"in": 0, "out": 0, "cw": 0, "cr": 0, "calls": 0}


def add(dst: dict, u: dict) -> None:
    for k in ("in", "out", "cw", "cr", "calls"):
        dst[k] += u[k]


def usage_of(entry: dict) -> dict | None:
    """Извлечь usage из ассистентской строки jsonl."""
    if entry.get("type") != "assistant":
        return None
    msg = entry.get("message") or {}
    if msg.get("model") == "<synthetic>":
        return None
    u = msg.get("usage")
    if not u:
        return None
    return {
        "in": u.get("input_tokens", 0),
        "out": u.get("output_tokens", 0),
        "cw": u.get("cache_creation_input_tokens", 0),
        "cr": u.get("cache_read_input_tokens", 0),
        "calls": 1,
    }


def read_jsonl(path: str):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # оборванная строка живой сессии — не повод падать


def scan_file(path: str):
    """Вернуть (usage по моделям, спавны субагентов) из одного jsonl.

    Дедупликация по requestId: одна реплика пишется в jsonl несколько раз.
    """
    seen: dict[str, tuple[dict, str]] = {}
    spawns: list[dict] = []
    descriptions: dict[str, str] = {}  # tool_use id -> description
    own_type = ""   # attributionAgent — тип самого агента, чей это транскрипт
    own_desc = ""   # первая реплика-задание в транскрипте субагента

    for entry in read_jsonl(path):
        msg = entry.get("message") or {}
        own_type = own_type or entry.get("attributionAgent") or ""
        if not own_desc and entry.get("isSidechain") and entry.get("type") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                own_desc = content[:300]
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        own_desc = (b.get("text") or "")[:300]
                        break
        for block in (msg.get("content") or []) if isinstance(msg.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                desc = (block.get("input") or {}).get("description")
                if desc:
                    descriptions[block.get("id", "")] = desc

        res = entry.get("toolUseResult")
        if isinstance(res, dict) and res.get("agentId"):
            spawns.append({
                "agent_id": res["agentId"],
                "agent_type": res.get("agentType") or "?",
                "model": res.get("resolvedModel") or "",
                "description": descriptions.get(res.get("toolUseID") or "", "")
                or (res.get("prompt") or "")[:160],
            })

        u = usage_of(entry)
        if u:
            key = entry.get("requestId") or entry.get("uuid")
            seen[key] = (u, msg.get("model") or "?")

    per_model: dict[str, dict] = defaultdict(blank)
    for u, model in seen.values():
        add(per_model[model], u)
    return per_model, spawns, {"type": own_type, "desc": own_desc}


def step_of(agent_type: str, description: str) -> str:
    if agent_type in STEP_BY_AGENT:
        return STEP_BY_AGENT[agent_type]
    text = f"{agent_type} {description}".lower()
    for pattern, step in STEP_BY_DESCRIPTION:
        if re.search(pattern, text):
            return step
    return "прочее"


def collect(session_path: str) -> dict:
    """Собрать полный реестр по сессии: основной поток + все субагенты."""
    session_dir = os.path.join(os.path.dirname(session_path),
                               os.path.basename(session_path)[:-len(".jsonl")])
    main_models, spawns, _ = scan_file(session_path)

    agents: dict[str, dict] = {}
    for s in spawns:
        agents[s["agent_id"]] = {"type": s["agent_type"], "desc": s["description"],
                                 "parent": None, "models": defaultdict(blank)}

    # Агенты лежат на двух глубинах: обычный субагент — в subagents/, агент воркфлоу —
    # в subagents/workflows/<run>/. Без второго шаблона прибор слеп ровно к тому,
    # что дороже всего: рою из десятка агентов.
    transcripts = (glob.glob(os.path.join(session_dir, "subagents", "agent-*.jsonl"))
                   + glob.glob(os.path.join(session_dir, "subagents", "workflows", "*",
                                            "agent-*.jsonl")))
    for path in sorted(transcripts):
        agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        models, child_spawns, own = scan_file(path)
        rec = agents.setdefault(agent_id, {"type": "?", "desc": "", "parent": None,
                                           "models": defaultdict(blank)})
        # транскрипт агента знает свой тип сам — вызов родителя мог не попасть в разбор
        if rec["type"] in ("?", "", None) and own["type"]:
            rec["type"] = own["type"]
        if not rec["desc"]:
            rec["desc"] = own["desc"]
        for model, u in models.items():
            add(rec["models"][model], u)
        for c in child_spawns:
            child = agents.setdefault(c["agent_id"], {"type": c["agent_type"], "desc": c["description"],
                                                      "parent": None, "models": defaultdict(blank)})
            child["parent"] = agent_id
            if child["type"] == "?":
                child["type"] = c["agent_type"]

    def root(agent_id: str, depth: int = 0) -> str:
        rec = agents[agent_id]
        if rec["parent"] and rec["parent"] in agents and depth < 20:
            return root(rec["parent"], depth + 1)
        return agent_id

    by_step: dict[str, dict] = defaultdict(blank)
    by_agent: dict[str, dict] = defaultdict(blank)
    by_model: dict[str, dict] = defaultdict(blank)
    money = 0.0

    for model, u in main_models.items():
        add(by_step["основной поток"], u)
        add(by_agent["основной поток"], u)
        add(by_model[model_key(model)], u)
        money += cost(u, model)

    for agent_id, rec in agents.items():
        r = agents[root(agent_id)]
        step = step_of(r["type"], r["desc"])
        for model, u in rec["models"].items():
            add(by_step[step], u)
            add(by_agent[rec["type"]], u)
            add(by_model[model_key(model)], u)
            money += cost(u, model)

    total = blank()
    for u in by_step.values():
        add(total, u)
    return {"session": os.path.basename(session_path), "total": total, "money": money,
            "by_step": dict(by_step), "by_agent": dict(by_agent), "by_model": dict(by_model),
            "agents": len(agents)}


def merge(reports: list[dict]) -> dict:
    out = {"session": f"{len(reports)} сессий", "total": blank(), "money": 0.0,
           "by_step": defaultdict(blank), "by_agent": defaultdict(blank),
           "by_model": defaultdict(blank), "agents": 0}
    for r in reports:
        add(out["total"], r["total"])
        out["money"] += r["money"]
        out["agents"] += r["agents"]
        for key in ("by_step", "by_agent", "by_model"):
            for name, u in r[key].items():
                add(out[key][name], u)
    for key in ("by_step", "by_agent", "by_model"):
        out[key] = dict(out[key])
    return out


def tokens(u: dict) -> int:
    return u["in"] + u["out"] + u["cw"] + u["cr"]


def render(rep: dict, track: str | None) -> int:
    t = rep["total"]
    print(f"сессия: {rep['session']}   субагентов: {rep['agents']}")
    print(f"\n{'статья':<20}{'токенов':>16}")
    print(f"{'input (без кеша)':<20}{t['in']:>16,}")
    print(f"{'output':<20}{t['out']:>16,}")
    print(f"{'cache-write':<20}{t['cw']:>16,}")
    print(f"{'cache-read':<20}{t['cr']:>16,}")
    print(f"{'ВСЕГО':<20}{tokens(t):>16,}   ≈ ${rep['money']:,.2f}")

    print(f"\n{'шаг':<18}{'токенов':>14}{'доля':>8}{'вызовов':>9}")
    total_tok = max(tokens(t), 1)
    known = [s for s in STEP_ORDER if s in rep["by_step"]]
    extra = [s for s in rep["by_step"] if s not in STEP_ORDER]
    for step in known + sorted(extra):
        u = rep["by_step"][step]
        print(f"{step:<18}{tokens(u):>14,}{tokens(u)/total_tok*100:>7.1f}%{u['calls']:>9}")

    print(f"\n{'агент':<26}{'токенов':>14}{'вызовов':>9}")
    for name, u in sorted(rep["by_agent"].items(), key=lambda kv: -tokens(kv[1]))[:15]:
        print(f"{name:<26}{tokens(u):>14,}{u['calls']:>9}")

    other = tokens(rep["by_step"].get("прочее", blank()))
    other_share = other / total_tok * 100
    if other_share > OTHER_ALERT_SHARE:
        print(f"\n⚠ в «прочее» упало {other_share:.1f}% расхода ({other:,} токенов) — "
              f"порог {OTHER_ALERT_SHARE}%.".replace(",", " "))
        print("  Атрибуция по шагам протокола дырявая: выводы «шаг N стоит столько» "
              "по этому прогону занижены. Пополнить STEP_BY_DESCRIPTION под реальные "
              "описания вызовов либо передавать шаг явно при запуске агента.")

    print(f"\n{'модель':<12}{'токенов':>14}{'доля':>8}")
    for name, u in sorted(rep["by_model"].items(), key=lambda kv: -tokens(kv[1])):
        print(f"{name:<12}{tokens(u):>14,}{tokens(u)/total_tok*100:>7.1f}%")

    if not track:
        return 0
    limit = TRACK_BUDGET[track]
    spent = tokens(t)
    print(f"\nтрек {track}: цель {limit:,}, факт {spent:,} — ", end="")
    if spent > limit:
        print(f"ПЕРЕРАСХОД ×{spent/limit:.2f}. СТОП: доложить владельцу до продолжения.")
        return 3
    print(f"в пределах ({spent/limit*100:.0f}% цели).")
    return 0


def project_dir(cwd: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(os.path.expanduser("~/.claude/projects"), key)


def latest_session(cwd: str) -> str | None:
    files = glob.glob(os.path.join(project_dir(cwd), "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def selftest() -> int:
    """Проверка без сети: дубли по requestId, вложенные агенты, привязка к шагам."""
    with tempfile.TemporaryDirectory() as tmp:
        sid = "s1"
        main = os.path.join(tmp, f"{sid}.jsonl")
        sub_dir = os.path.join(tmp, sid, "subagents")
        os.makedirs(sub_dir)

        def line(**kw):
            return json.dumps(kw, ensure_ascii=False) + "\n"

        def assistant(model, i, o, cw, cr, rid):
            return line(type="assistant", requestId=rid, message={
                "model": model, "usage": {"input_tokens": i, "output_tokens": o,
                                          "cache_creation_input_tokens": cw,
                                          "cache_read_input_tokens": cr}})

        with open(main, "w", encoding="utf-8") as fh:
            # одна реплика записана трижды с одним requestId — считается один раз
            for _ in range(3):
                fh.write(assistant("claude-opus-5", 10, 20, 30, 40, "req_a"))
            fh.write(assistant("claude-opus-5", 1, 2, 3, 4, "req_b"))
            # синтетическая строка не считается вовсе
            fh.write(assistant("<synthetic>", 999, 999, 999, 999, "req_syn"))
            fh.write(line(type="user", message={"content": [
                {"type": "tool_use", "id": "tu1", "name": "Agent",
                 "input": {"description": "Картирую дело"}}]}))
            fh.write(line(type="user", toolUseResult={
                "agentId": "aaa", "agentType": "case-mapper", "toolUseID": "tu1",
                "resolvedModel": "claude-opus-5"}))

        with open(os.path.join(sub_dir, "agent-aaa.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(assistant("claude-sonnet-5", 100, 200, 300, 400, "req_c"))
            fh.write(assistant("claude-sonnet-5", 100, 200, 300, 400, "req_c"))  # дубль
            fh.write(line(type="assistant", toolUseResult={
                "agentId": "bbb", "agentType": "pdf-reader", "resolvedModel": "claude-haiku-4-5"}))

        with open(os.path.join(sub_dir, "agent-bbb.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(assistant("claude-haiku-4-5", 1000, 2000, 3000, 4000, "req_d"))

        # агент воркфлоу лежит глубже — subagents/workflows/<run>/
        wf_dir = os.path.join(sub_dir, "workflows", "wf_test")
        os.makedirs(wf_dir)
        with open(os.path.join(wf_dir, "agent-ccc.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(line(type="user", isSidechain=True, attributionAgent="doc-reviewer",
                          message={"content": [{"type": "text", "text": "проверь документ"}]}))
            fh.write(assistant("claude-opus-5", 7, 8, 9, 10, "req_e"))

        rep = collect(main)
        checks = [
            ("дубли по requestId схлопнуты", rep["by_step"]["основной поток"]["in"] == 11),
            ("synthetic отброшен", rep["by_step"]["основной поток"]["out"] == 22),
            ("субагент учтён один раз", rep["by_step"]["1 карта"]["in"] == 1100),
            ("вложенный агент свёрнут в шаг родителя", rep["by_step"]["1 карта"]["cr"] == 4400),
            ("шагов ровно три", set(rep["by_step"]) == {"основной поток", "1 карта", "5 проверка"}),
            ("модели разнесены", set(rep["by_model"]) == {"opus", "sonnet", "haiku"}),
            ("агент воркфлоу учтён", rep["by_step"].get("5 проверка", {}).get("out") == 8),
            ("итог сходится", tokens(rep["total"]) == 110 + 11000 + 34),
            ("деньги посчитаны", rep["money"] > 0),
            ("шаг по описанию", step_of("general-purpose", "Ареопаг раунд 2") == "3 позиция"),
            ("неизвестный агент → прочее", step_of("general-purpose", "чинить сборку") == "прочее"),
        ("работа над системой отделена от шагов дела",
         step_of("general-purpose", "исследование по legal design") == "система"),
        ("разбор инбокса — интейк",
         step_of("general-purpose", "обработай файлы из inbox") == "0 интейк"),
        ("порог тревоги по «прочее» задан", OTHER_ALERT_SHARE > 0),
        ]
        bad = [name for name, ok in checks if not ok]
        for name, ok in checks:
            print(f"  {'✓' if ok else '✗'} {name}")
        if bad:
            print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
            return 1
        print(f"selftest пройден: {len(checks)}/{len(checks)}")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Замер расхода токенов по шагам конвейера Фемиды")
    ap.add_argument("session", nargs="?", help="путь к session.jsonl (по умолчанию — свежая сессия проекта)")
    ap.add_argument("--all", action="store_true", help="все сессии проекта сводно")
    ap.add_argument("--track", choices=sorted(TRACK_BUDGET), help="сверить с целью трека, exit 3 при перерасходе")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.all:
        files = sorted(glob.glob(os.path.join(project_dir(os.getcwd()), "*.jsonl")))
        if not files:
            print("сессий проекта не найдено", file=sys.stderr)
            return 1
        rep = merge([collect(f) for f in files])
    else:
        path = args.session or latest_session(os.getcwd())
        if not path or not os.path.isfile(path):
            print(f"session.jsonl не найден ({path}). Передай путь явно.", file=sys.stderr)
            return 1
        rep = collect(path)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        if args.track and tokens(rep["total"]) > TRACK_BUDGET[args.track]:
            return 3
        return 0
    return render(rep, args.track)


if __name__ == "__main__":
    sys.exit(main())
