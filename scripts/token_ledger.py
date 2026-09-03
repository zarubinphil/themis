#!/usr/bin/env python3
"""token_ledger.py — инструментальный замер расхода токенов по шагам конвейера Фемиды.

Читает session-JSONL Claude Code (основной поток + транскрипты субагентов) и выдает
фактические input/output/cache-write/cache-read по шагам протокола, а не самоотчет модели.

Зачем: до этого скрипта единственным источником цифр было поле `usage`, которое субагент
возвращает в основной поток, плюс оценка основного потока на глаз. Два дефекта такого учета:
  1. Основной поток вообще не измерялся — а он дает основную массу cache-read.
  2. `$HOME/.claude/scripts/token-spend.sh` суммирует КАЖДУЮ строку с usage, а одна и та же
     ассистентская реплика пишется в jsonl по нескольку раз с одним requestId.
     На живой сессии это давало 174 212 561 вместо 72 634 172 — завышение в 2,4 раза.

Использование:
    token_ledger.py                       # свежая сессия проекта в cwd
    token_ledger.py SESSION.jsonl         # конкретная сессия
    token_ledger.py --all                 # все сессии проекта, сводно
    token_ledger.py --track FAST          # + вердикт по цели трека (exit 3 при перерасходе)
    token_ledger.py --ctx-limit 150000    # потолок контекста запроса (exit 3 при пробое, 0 — выкл)
    token_ledger.py --json                # машинный вывод
    token_ledger.py --selftest            # проверка без сети и без реальных сессий

Колонка «преамбула × вызовы» (M08): пол контекста потока (минимальный контекст его
запроса) × число вызовов = счет за повторную доставку одного и того же. Цифра из
журнала, нижняя граница. Считает context_ledger; здесь — свод по потокам и потолок
контекста фазы: превышение — ненулевой код, а не молчание.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

import _obshee as obs

try:
    from swarm_contract import SUBST_RE as _SUBST_RE
except ImportError:  # прибор лежит рядом; его отсутствие не роняет замер расхода
    _SUBST_RE = None

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

# Шаг протокола по типу агента. Порядок ключей = порядок в отчете.
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

# Для general-purpose тип не говорит ничего — шаг берем из описания вызова.
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

# Тип агента, когда описание ничего не сказало. Агенты воркфлоу (`workflow-subagent`)
# получают задание английским промптом и мимо русских шаблонов проходят целиком;
# по делу они не работают — конвейер дела идет поименными агентами из STEP_BY_AGENT.
# Замер 04.08.2026: 8,4 млн токенов «прочего» — это они и были.
STEP_BY_TYPE_FALLBACK = {"workflow-subagent": "система"}

# Шаг протокола по одному вызову инструмента ИЗ ГЛАВНОГО ПОТОКА. Главный поток —
# самая дорогая статья: замер 04.08.2026 по 33 сессиям дал 59,8 % расхода, и весь он
# лежал одной строкой «основной поток». Разбивка по шагам покрывала 22 % денег, то есть
# бюджетные чекпойнты после шагов 2 и 3 отвечали не про те деньги. Сигнал берем из ПУТИ
# и КОМАНДЫ, а не из описания: путь пишется всегда, описание — как получится.
MAIN_SIGNALS = [
    (r"Desktop/inbox|inbox-watcher", "0 интейк"),
    # 00_intake/ — не интейк, а картирование: шаг 0 это ПЕРЕНОС из инбокса (его делает
    # inbox-triage и он считается по типу агента), а чтение материалов оттуда — уже карта.
    (r"knowledge-map\.md|reader_|reconcile_|_working/|00_intake/|markdown_extract|vision-doc|"
     r"render_tail", "1 карта"),
    (r"practice\.md|hunter_|_practice/|practice_index|practice_search|practice_harvest|cite\.py",
     "2 практика"),
    (r"positions\.md|_council/", "3 позиция"),
    (r"\.agent/drafts/|create_docx|md_to_docx|gosposhlina|calc395", "4 составление"),
    (r"quality_gate|document_guard|table_guard|verify_inn|verify_act|crosscheck_numbers|"
     r"sroki|02_hearings/", "5 проверка"),
    (r"_index\.md|_clients\.md|_client\.md|registry_check|redline", "6 архив"),
    (r"themiz_status|token_ledger|retro\.py|setup_doctor|pd_guard|claude_guard|preflight_search|"
     r"update_legal_corpus|lessons-log|_logs/|scripts/|\.claude/", "система"),
]

# Доля нераспределенного расхода, выше которой цифры по шагам нельзя считать
# основанием для решений. Замер 03.08.2026 на живой сессии: 16,3% в «прочее».
OTHER_ALERT_SHARE = 5.0

# «основной поток» больше не шаг: его расход распределен по шагам через MAIN_SIGNALS,
# а сам он остался строкой в разрезе агентов. Ключ сохранен в порядке ради старых --json.
STEP_ORDER = ["основной поток", "0 интейк", "1 карта", "2 практика", "3 позиция",
              "4 составление", "5 проверка", "6 архив", "система", "прочее"]


# Неизвестная модель. Прежде любая строка без opus|sonnet|haiku молча считалась
# по верхней ставке и складывалась в строку «opus» — в логах проекта такой была
# claude-fable-5 (1356 вызовов, 73,1 млн токенов, 5,0 % свода). Прибор при этом
# показывал «opus», и понять, что это другая модель, было нельзя ни по одной цифре.
# Оценка по верхней ставке остается (занижать расход опаснее, чем завышать), но
# СТРОКА В РАЗРЕЗЕ теперь своя, с явной пометкой, что цена — верхняя оценка.
UNKNOWN_PREFIX = "неизвестная: "


def model_key(model: str) -> str:
    """Ключ разреза. Неизвестная модель — собственная строка, а не «opus»."""
    ml = (model or "").lower()
    for k in RATES:
        if k in ml:
            return k
    return UNKNOWN_PREFIX + (model or "без имени")


def rate_key(model_or_key: str) -> str:
    """Ключ тарифа. Неизвестное считаем по верхней ставке — чтобы не занизить."""
    ml = (model_or_key or "").lower()
    for k in RATES:
        if k in ml:
            return k
    return "opus"


def cost(u: dict, model: str) -> float:
    r = RATES[rate_key(model)]
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


def read_jsonl(path: str, with_raw: bool = False):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # оборванная строка живой сессии — не повод падать
            yield (line, entry) if with_raw else entry


def scan_file(path: str):
    """Вернуть (usage по моделям, спавны субагентов, свой тип, реплики по шагам).

    Дедупликация по requestId: одна реплика пишется в jsonl несколько раз.
    Четвертый элемент — реплики в порядке файла с уже проставленным шагом: шаг
    «липкий», реплика без своего сигнала относится к последнему объявленному шагу,
    потому что поток остается в шаге, пока не начнет следующий.
    """
    seen: dict[str, tuple[dict, str]] = {}
    step_at: dict[str, str | None] = {}
    cur_step: str | None = None
    spawns: list[dict] = []
    descriptions: dict[str, str] = {}  # tool_use id -> description
    own_type = ""   # attributionAgent — тип самого агента, чей это транскрипт
    own_desc = ""   # первая реплика-задание в транскрипте субагента
    subst = 0       # подмены модели харнессом — счет по сырой строке журнала

    for raw, entry in read_jsonl(path, with_raw=True):
        if _SUBST_RE is not None and _SUBST_RE.search(raw):
            subst += 1
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
                sig = main_step_signal(block.get("name") or "", block.get("input") or {})
                if sig:
                    cur_step = sig

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
            step_at[key] = cur_step

    per_model: dict[str, dict] = defaultdict(blank)
    for u, model in seen.values():
        add(per_model[model], u)
    turns = [(u, model, step_at.get(key)) for key, (u, model) in seen.items()]
    return per_model, spawns, {"type": own_type, "desc": own_desc, "subst": subst}, turns


def step_of(agent_type: str, description: str) -> str:
    if agent_type in STEP_BY_AGENT:
        return STEP_BY_AGENT[agent_type]
    text = f"{agent_type} {description}".lower()
    for pattern, step in STEP_BY_DESCRIPTION:
        if re.search(pattern, text):
            return step
    return STEP_BY_TYPE_FALLBACK.get(agent_type, "прочее")


def main_step_signal(name: str, inp: dict) -> str | None:
    """Шаг протокола по вызову инструмента из главного потока. None — сигнала нет."""
    if name in ("Agent", "Task"):
        step = step_of(inp.get("subagent_type") or "", inp.get("description") or "")
        return None if step == "прочее" else step
    if name == "Skill":
        step = step_of("", f"{inp.get('skill') or ''} {inp.get('args') or ''}")
        return None if step == "прочее" else step
    text = " ".join(str(inp.get(k) or "")
                    for k in ("file_path", "command", "path", "pattern", "description"))
    # ПРАВКА прибора — работа над системой, а не шаг дела. Запуск того же файла
    # (`python3 scripts/quality_gate.py`) — наоборот, шаг. Различает не путь, а
    # инструмент: Edit/Write по scripts/ и .claude/ чинят Фемиду, Bash ее применяет.
    if name in ("Edit", "Write", "NotebookEdit") and re.search(r"scripts/|\.claude/", text):
        return "система"
    for pattern, step in MAIN_SIGNALS:
        if re.search(pattern, text, re.I):
            return step
    return None


def collect(session_path: str) -> dict:
    """Собрать полный реестр по сессии: основной поток + все субагенты."""
    session_dir = os.path.join(os.path.dirname(session_path),
                               os.path.basename(session_path)[:-len(".jsonl")])
    _, spawns, main_own, main_turns = scan_file(session_path)
    subst = main_own.get("subst", 0)

    agents: dict[str, dict] = {}
    for s in spawns:
        agents[s["agent_id"]] = {"type": s["agent_type"], "desc": s["description"],
                                 "parent": None, "models": defaultdict(blank)}

    # Потоки для колонки «преамбула × вызовы»: главный + каждый транскрипт свой.
    stream_turns: list[tuple[str, list]] = [("основной поток", main_turns)]

    # Агенты лежат на двух глубинах: обычный субагент — в subagents/, агент воркфлоу —
    # в subagents/workflows/<run>/. Без второго шаблона прибор слеп ровно к тому,
    # что дороже всего: рою из десятка агентов.
    transcripts = (glob.glob(os.path.join(session_dir, "subagents", "agent-*.jsonl"))
                   + glob.glob(os.path.join(session_dir, "subagents", "workflows", "*",
                                            "agent-*.jsonl")))
    for path in sorted(transcripts):
        agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        models, child_spawns, own, turns = scan_file(path)
        subst += own.get("subst", 0)
        rec = agents.setdefault(agent_id, {"type": "?", "desc": "", "parent": None,
                                           "models": defaultdict(blank)})
        # транскрипт агента знает свой тип сам — вызов родителя мог не попасть в разбор
        if rec["type"] in ("?", "", None) and own["type"]:
            rec["type"] = own["type"]
        if not rec["desc"]:
            rec["desc"] = own["desc"]
        stream_turns.append((rec["type"] if rec["type"] not in ("?", "", None)
                             else f"агент {agent_id[:8]}", turns))
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

    for u, model, step in main_turns:
        add(by_step[step or "прочее"], u)
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

    # Колонка «преамбула × вызовы» и максимум контекста — из тех же реплик журнала.
    # context_ledger импортирует token_ledger, поэтому импорт здесь ленивый.
    preamble = {"floor_main": 0, "calls": 0, "tokens": 0, "money": 0.0, "streams": []}
    max_ctx = 0
    try:
        import context_ledger as _cl
    except ImportError:  # прибор лежит рядом; его отсутствие не роняет замер расхода
        _cl = None
    if _cl is not None:
        for name, turns in stream_turns:
            p = _cl.preamble_of(turns)
            preamble["calls"] += p["calls"]
            preamble["tokens"] += p["tokens"]
            preamble["money"] += p["money"]
            preamble["streams"].append({"name": name, **p})
            if name == "основной поток":
                preamble["floor_main"] = p["floor"]
                max_ctx = max((_cl.ctx_of(u) for u, _m, _s in turns), default=0)

    return {"session": os.path.basename(session_path), "total": total, "money": money,
            "by_step": dict(by_step), "by_agent": dict(by_agent), "by_model": dict(by_model),
            "agents": len(agents), "preamble": preamble, "max_ctx": max_ctx,
            "substitutions": subst}


def merge(reports: list[dict]) -> dict:
    out = {"session": f"{len(reports)} сессий", "total": blank(), "money": 0.0,
           "by_step": defaultdict(blank), "by_agent": defaultdict(blank),
           "by_model": defaultdict(blank), "agents": 0,
           "preamble": {"floor_main": 0, "calls": 0, "tokens": 0, "money": 0.0, "streams": []},
           "max_ctx": 0, "substitutions": 0}
    for r in reports:
        add(out["total"], r["total"])
        out["money"] += r["money"]
        out["agents"] += r["agents"]
        out["substitutions"] += r.get("substitutions", 0)
        for key in ("by_step", "by_agent", "by_model"):
            for name, u in r[key].items():
                add(out[key][name], u)
        p = r.get("preamble") or {}
        out["preamble"]["calls"] += p.get("calls", 0)
        out["preamble"]["tokens"] += p.get("tokens", 0)
        out["preamble"]["money"] += p.get("money", 0.0)
        out["preamble"]["streams"] += p.get("streams", [])
        if p.get("floor_main"):
            cur = out["preamble"]["floor_main"]
            out["preamble"]["floor_main"] = min(cur, p["floor_main"]) if cur else p["floor_main"]
        out["max_ctx"] = max(out["max_ctx"], r.get("max_ctx", 0))
    for key in ("by_step", "by_agent", "by_model"):
        out[key] = dict(out[key])
    return out


def tokens(u: dict) -> int:
    return u["in"] + u["out"] + u["cw"] + u["cr"]


def ctx_verdict(max_ctx: int, limit: int) -> tuple[str, int]:
    """Потолок контекста фазы: превышение — ненулевой код, а не молчание.

    Меряется максимальный контекст запроса ГЛАВНОГО потока (у субагента контекст
    свой и умирает вместе с ним). Пробой значит: фаза тащит весь накопленный вес
    дальше и переоплачивает его каждым вызовом — обязательна разгрузка
    (handoff.md и продолжение чистой сессией), а не строка в отчете.
    """
    if limit <= 0:
        return "", obs.KOD_OK
    if max_ctx <= 0:
        return ("\nпотолок контекста: ДАННЫХ НЕТ — ни одного запроса главного потока "
                "в разборе. Это НЕ «уложились».", obs.KOD_NE_RABOTAL)
    if max_ctx > limit:
        return (f"\nпотолок контекста {limit:,} ПРОБИТ: контекст запроса дошел до "
                f"{max_ctx:,}. ".replace(",", " ")
                + "СТОП: разгрузка между фазами обязательна ДО следующего шага — "
                  "записать .agent/context/handoff.md и продолжить чистой сессией "
                  "(см. «Экономия и разгрузка контекста» в .claude/CLAUDE.md).",
                obs.KOD_STOP)
    return (f"\nпотолок контекста {limit:,}: держится (максимум {max_ctx:,})."
            .replace(",", " "), obs.KOD_OK)


def render(rep: dict, track: str | None, ctx_limit: int = 0) -> int:
    t = rep["total"]
    print(f"сессия: {rep['session']}   субагентов: {rep['agents']}")
    print(f"\n{'статья':<20}{'токенов':>16}")
    print(f"{'input (без кеша)':<20}{t['in']:>16,}")
    print(f"{'output':<20}{t['out']:>16,}")
    print(f"{'cache-write':<20}{t['cw']:>16,}")
    print(f"{'cache-read':<20}{t['cr']:>16,}")
    print(f"{'ВСЕГО':<20}{tokens(t):>16,}   ≈ ${rep['money']:,.2f}")

    p = rep.get("preamble") or {}
    if p.get("calls"):
        print(f"\nповторная доставка — преамбула × вызовы (пол потока на каждом вызове):")
        print(f"{'поток':<26}{'пол ctx':>12}{'вызовов':>9}{'токенов':>14}{'≈$':>10}")
        for s in sorted(p["streams"], key=lambda x: -x["tokens"])[:8]:
            print(f"{s['name'][:25]:<26}{s['floor']:>12,}{s['calls']:>9}"
                  f"{s['tokens']:>14,}{s['money']:>10,.2f}".replace(",", " "))
        print(f"{'ИТОГО преамбулы':<26}{'':>12}{p['calls']:>9}"
              f"{p['tokens']:>14,}{p['money']:>10,.2f}".replace(",", " "))
        print(f"  {p['tokens']/max(tokens(t), 1)*100:.1f}% трафика — одно и то же, доставленное "
              "заново. Пол потока (минимальный контекст его запроса) × число вызовов; "
              "цифра из журнала, нижняя граница: реальный повтор больше на все, "
              "что наросло сверх пола. Разрез по шагам фазы — context_ledger.py.")

    print(f"\n{'шаг':<18}{'токенов':>14}{'доля':>8}{'вызовов':>9}")
    total_tok = max(tokens(t), 1)
    known = [s for s in STEP_ORDER if s in rep["by_step"]]
    extra = [s for s in rep["by_step"] if s not in STEP_ORDER]
    for step in known + sorted(extra):
        u = rep["by_step"][step]
        print(f"{step:<18}{tokens(u):>14,}{tokens(u)/total_tok*100:>7.1f}%{u['calls']:>9}")
    print("  расход главного потока разнесен по шагам (сигнал — путь и команда вызова); "
          "сам он виден строкой в разрезе агентов")

    print(f"\n{'агент':<26}{'токенов':>14}{'вызовов':>9}")
    for name, u in sorted(rep["by_agent"].items(), key=lambda kv: -tokens(kv[1]))[:15]:
        print(f"{name:<26}{tokens(u):>14,}{u['calls']:>9}")

    other = tokens(rep["by_step"].get("прочее", blank()))
    other_share = other / total_tok * 100
    if other_share > OTHER_ALERT_SHARE:
        print(f"\n⚠ в «прочее» упало {other_share:.1f}% расхода ({other:,} токенов) — "
              f"порог {OTHER_ALERT_SHARE}%.".replace(",", " "))
        print("  Атрибуция по шагам протокола дырявая: выводы «шаг N стоит столько» "
              "по этому прогону занижены. Смотреть, чего не хватает: субагентам — "
              "STEP_BY_DESCRIPTION и STEP_BY_TYPE_FALLBACK, главному потоку — MAIN_SIGNALS "
              "(сигнал берется из пути файла и команды).")

    print(f"\n{'модель':<34}{'токенов':>14}{'доля':>8}")
    for name, u in sorted(rep["by_model"].items(), key=lambda kv: -tokens(kv[1])):
        print(f"{name:<34}{tokens(u):>14,}{tokens(u)/total_tok*100:>7.1f}%")
    unknown = {k: v for k, v in rep["by_model"].items() if k.startswith(UNKNOWN_PREFIX)}
    if unknown:
        n = sum(tokens(v) for v in unknown.values())
        print(f"⚠ модель вне тарифной таблицы: {n:,} токенов ({n/total_tok*100:.1f}%). "
              "Цена по ним — ВЕРХНЯЯ оценка (ставка opus). Внести тариф в RATES.")

    # Подмена модели харнессом — ОТДЕЛЬНАЯ строка разреза, не растворена в строке
    # фактически сработавшей модели: 15 подмен за прогон 01.09 были невидимы.
    n_subst = rep.get("substitutions", 0)
    if n_subst:
        print(f"\nПОДМЕНА МОДЕЛИ ХАРНЕССОМ: {n_subst} событий — заказанная модель не "
              f"поднята, харнесс подставил другую. Расход выше посчитан по фактически "
              f"сработавшей модели; подмена в ее строку НЕ растворена. Детали по "
              f"журналу: python3 scripts/swarm_contract.py --substitutions ФАЙЛ…")

    rc = 0
    if track:
        text, rc = track_verdict(track, tokens(t))
        print(text)
    ctext, crc = ctx_verdict(rep.get("max_ctx", 0), ctx_limit)
    if ctext:
        print(ctext)
    return max(rc, crc)


def track_verdict(track: str, spent: int) -> tuple[str, int]:
    """Вердикт по цели трека: (что печатать, код возврата).

    Пустая сессия — это «прибор не увидел данных», а не «уложились в бюджет».
    Прежде --track FULL на сессии без единой записи usage печатал
    «в пределах (0% цели)» и возвращал 0: молчание читалось как успех, и бюджетный
    чекпойнт протокола проходил на пустом месте.
    """
    limit = TRACK_BUDGET[track]
    if spent <= 0:
        return (f"\nтрек {track}: цель {limit:,} — ДАННЫХ НЕТ. ".replace(",", " ")
                + "Ни одной записи расхода в разобранных файлах: не тот файл сессии, "
                  "не тот проект либо логи еще не сброшены на диск. "
                  "Это НЕ «уложились».", obs.KOD_NE_RABOTAL)
    head = f"\nтрек {track}: цель {limit:,}, факт {spent:,} — ".replace(",", " ")
    if spent > limit:
        return (head + f"ПЕРЕРАСХОД ×{spent/limit:.2f}. "
                       "СТОП: доложить владельцу до продолжения.", obs.KOD_STOP)
    return head + f"в пределах ({spent/limit*100:.0f}% цели).", obs.KOD_OK


def project_dir(cwd: str) -> str:
    return str(obs.dom_sessij(cwd))


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

        def with_tool(model, i, o, cw, cr, rid, name, inp, tu_id="tu"):
            return line(type="assistant", requestId=rid, message={
                "model": model, "usage": {"input_tokens": i, "output_tokens": o,
                                          "cache_creation_input_tokens": cw,
                                          "cache_read_input_tokens": cr},
                "content": [{"type": "tool_use", "id": tu_id, "name": name, "input": inp}]})

        with open(main, "w", encoding="utf-8") as fh:
            # одна реплика записана трижды с одним requestId — считается один раз
            for _ in range(3):
                fh.write(assistant("claude-opus-5", 10, 20, 30, 40, "req_a"))
            # Модель вне тарифной таблицы. В логах проекта это claude-fable-5:
            # 1356 вызовов, 73,1 млн токенов — и все они молча считались «opus».
            fh.write(assistant("claude-fable-5", 5, 6, 7, 8, "req_f"))
            # синтетическая строка не считается вовсе
            fh.write(assistant("<synthetic>", 999, 999, 999, 999, "req_syn"))
            # прибор дела из главного потока — это шаг протокола, а не «прочее»
            fh.write(with_tool("claude-opus-5", 1, 2, 3, 4, "req_b", "Bash",
                               {"command": "python3 scripts/registry_check.py"}))
            # реплика без своего сигнала остается в текущем шаге
            fh.write(assistant("claude-opus-5", 1, 1, 1, 1, "req_h"))
            fh.write(line(type="user", message={"content": [
                {"type": "tool_use", "id": "tu1", "name": "Agent",
                 "input": {"description": "Картирую дело"}}]}))
            fh.write(line(type="user", toolUseResult={
                "agentId": "aaa", "agentType": "case-mapper", "toolUseID": "tu1",
                "resolvedModel": "claude-opus-5"}))
            # харнесс молча подменил недоступную модель — отдельный счетчик разреза
            fh.write(line(type="system",
                          text="claude-sonnet-5[1m] is temporarily unavailable, "
                               "auto mode cannot determine"))

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
        # Через .get: сломанная атрибуция должна давать НАЗВАННЫЙ провал проверки,
        # а не KeyError — иначе непонятно, что именно отвалилось.
        def st(step: str) -> dict:
            return rep["by_step"].get(step, blank())

        checks = [
            ("дубли по requestId схлопнуты", st("прочее")["in"] == 15),
            ("synthetic отброшен", st("прочее")["out"] == 26),
            ("субагент учтен один раз", st("1 карта")["in"] == 1100),
            ("вложенный агент свернут в шаг родителя", st("1 карта")["cr"] == 4400),
            ("шагов ровно четыре",
             set(rep["by_step"]) == {"прочее", "6 архив", "1 карта", "5 проверка"}),
            # Главный поток — самая дорогая статья; строкой «основной поток» он
            # съедал 59,8 % расхода мимо всякой разбивки по шагам.
            ("главный поток разнесен по шагам", "основной поток" not in rep["by_step"]),
            ("вызов прибора дела привязан к своему шагу", st("6 архив")["in"] == 2),
            ("реплика без сигнала наследует текущий шаг", st("6 архив")["cr"] == 5),
            ("главный поток виден в разрезе агентов",
             rep["by_agent"]["основной поток"]["in"] == 17),
            ("до первого сигнала расход честно в «прочем»", st("прочее")["cr"] == 48),
            ("сигнал по команде прибора", main_step_signal(
                "Bash", {"command": "python3 scripts/quality_gate.py --case cases/X/Y"})
                == "5 проверка"),
            ("сигнал по пути файла дела", main_step_signal(
                "Read", {"file_path": "cases/К/дело/.agent/context/practice.md"}) == "2 практика"),
            ("сигнал по запуску агента", main_step_signal(
                "Agent", {"subagent_type": "doc-drafter", "description": "документ"})
                == "4 составление"),
            ("посторонний файл сигналом не считается",
             main_step_signal("Read", {"file_path": "README.md"}) is None),
            # Правка прибора и его запуск — разные вещи: первое чинит систему,
            # второе исполняет шаг дела. Иначе вечер за починкой document_guard
            # ложится в отчет как «шаг 5 проверка».
            ("правка прибора — работа над системой",
             main_step_signal("Edit", {"file_path": "scripts/quality_gate.py"}) == "система"),
            ("запуск того же прибора — шаг дела", main_step_signal(
                "Bash", {"command": "python3 scripts/quality_gate.py --case cases/К/Д"})
                == "5 проверка"),
            ("агент воркфлоу не валится в «прочее»",
             step_of("workflow-subagent", "You are an epistemics specialist") == "система"),
            ("модели разнесены",
             {"opus", "sonnet", "haiku"} <= set(rep["by_model"])),
            # Неизвестная модель обязана быть ВИДНА, а не растворяться в opus.
            ("неизвестная модель — отдельная строка разреза",
             any(k.startswith(UNKNOWN_PREFIX) and "fable" in k for k in rep["by_model"])),
            ("неизвестная модель не приписана opus",
             rep["by_model"]["opus"]["in"] == 19),
            ("токены неизвестной модели не потеряны",
             sum(v["in"] for k, v in rep["by_model"].items()
                 if k.startswith(UNKNOWN_PREFIX)) == 5),
            # Цена по ней — верхняя оценка: занизить расход опаснее, чем завысить.
            ("неизвестная модель считается по верхней ставке",
             rate_key("claude-fable-5") == "opus"),
            ("известная модель тариф не меняет",
             rate_key("claude-sonnet-5") == "sonnet" and rate_key("claude-haiku-4-5") == "haiku"),
            ("агент воркфлоу учтен", st("5 проверка")["out"] == 8),
            # Подмена модели харнессом — отдельный счетчик, а не растворение в
            # строке фактически сработавшей модели (15 невидимых подмен 01.09).
            ("подмена модели посчитана отдельной строкой разреза",
             rep["substitutions"] == 1),
            ("итог сходится", tokens(rep["total"]) == 100 + 26 + 10 + 4 + 11000 + 34),
            ("деньги посчитаны", rep["money"] > 0),
            ("шаг по описанию", step_of("general-purpose", "Ареопаг раунд 2") == "3 позиция"),
            ("неизвестный агент → прочее", step_of("general-purpose", "чинить сборку") == "прочее"),
        ("работа над системой отделена от шагов дела",
         step_of("general-purpose", "исследование по legal design") == "система"),
        ("разбор инбокса — интейк",
         step_of("general-purpose", "обработай файлы из inbox") == "0 интейк"),
        ("порог тревоги по «прочее» задан", OTHER_ALERT_SHARE > 0),
        # Бюджетный чекпойнт: пустая сессия НЕ «уложились».
        ("пустая сессия дает «данных нет», а не «в пределах»",
         track_verdict("FULL", 0)[1] == 2 and "ДАННЫХ НЕТ" in track_verdict("FULL", 0)[0]),
        ("расход в пределах цели дает 0",
         track_verdict("FULL", TRACK_BUDGET["FULL"] // 2)[1] == 0),
        ("перерасход дает 3", track_verdict("FAST", TRACK_BUDGET["FAST"] * 2)[1] == 3),
        ("граница цели — еще не перерасход",
         track_verdict("FAST", TRACK_BUDGET["FAST"])[1] == 0),
        # Тариф: opus дороже sonnet и haiku на одном и том же объеме.
        ("тариф модели не подменяется",
         cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-opus-5") == 15.0
         and cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-sonnet-5") == 3.0
         and cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-haiku-4-5") == 1.0),
        ("неизвестная модель считается по верхней ставке в деньгах",
         cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-fable-5") == 15.0),
        ]

        # Порог путь-резолва (b8086b2): project_dir указывал на литеральный
        # "$HOME/..." → glob всегда пуст → latest_session=None даже при живых
        # сессиях (слепой ноль, читался как «уложились»). Фикстура ПО ОБЕ
        # стороны порога: HOME с реальным журналом обязан найтись, пустой
        # каталог — честный None (шаг отказа main, exit 1).
        _home0 = os.environ.get("HOME")
        key = re.sub(r"[^A-Za-z0-9]", "-", "/x/case")
        try:
            full_home = os.path.join(tmp, "home_full")
            full_dir = os.path.join(full_home, ".claude", "projects", key)
            os.makedirs(full_dir)
            with open(os.path.join(full_dir, "sess.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("{}\n")
            os.environ["HOME"] = full_home
            found = latest_session("/x/case")

            empty_home = os.path.join(tmp, "home_empty")
            os.makedirs(os.path.join(empty_home, ".claude", "projects", key))
            os.environ["HOME"] = empty_home
            empty = latest_session("/x/case")
        finally:
            if _home0 is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = _home0
        checks += [
            ("HOME развернут: реальный журнал найден, не слепой None",
             found is not None and found.endswith("sess.jsonl")),
            ("пустой каталог сессий → честный None, не выдуманный путь",
             empty is None),
        ]

        # Колонка «преамбула × вызовы» (M08): пол потока — минимальный контекст
        # его запроса. В фикстуре главного потока ctx реплик: 80, 20, 8, 3 →
        # пол 3, вызовов 4. Субагенты: aaa 800×1, bbb 8000×1, ccc 26×1.
        pr = rep["preamble"]
        checks += [
            ("пол контекста главного потока из журнала", pr["floor_main"] == 3),
            ("преамбула = пол × вызовы по всем потокам",
             pr["tokens"] == 12 + 800 + 8000 + 26 and pr["calls"] == 7),
            ("счет за повторную доставку ненулевой", pr["money"] > 0),
            ("разрез преамбулы по потокам на месте",
             {s["name"] for s in pr["streams"]}
             >= {"основной поток", "case-mapper", "pdf-reader", "doc-reviewer"}),
            ("максимум контекста главного потока виден", rep["max_ctx"] == 80),
            # Потолок контекста фазы: превышение — ненулевой код, а не молчание.
            ("пробой потолка контекста дает 3",
             ctx_verdict(80, 50)[1] == obs.KOD_STOP and "ПРОБИТ" in ctx_verdict(80, 50)[0]),
            ("потолок держится дает 0", ctx_verdict(80, 200)[1] == obs.KOD_OK),
            ("пустой разбор — «данных нет», а не «уложились»",
             ctx_verdict(0, 200)[1] == obs.KOD_NE_RABOTAL),
            ("потолок 0 выключает проверку", ctx_verdict(80, 0) == ("", obs.KOD_OK)),
        ]

        # Приемка M08 зовет только этот selftest — машина контекста проверяется
        # внутри него, а не рядом: разрез преамбулы по шагам фазы и гейт выгрузки
        # (context_ledger), живой потолок и байтовый предел инструкций (context_guard).
        import context_guard
        import context_ledger
        checks += [
            ("selftest context_ledger пройден", context_ledger.selftest() == 0),
            ("selftest context_guard пройден", context_guard.selftest() == 0),
        ]

        bad = [name for name, ok in checks if not ok]
        for name, ok in checks:
            print(f"  {'✓' if ok else '✗'} {name}")
        if bad:
            print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
            return obs.KOD_OSHIBKA
        print(f"selftest пройден: {len(checks)}/{len(checks)}")
        return 0


def main() -> int:
    ap = obs.parser("Замер расхода токенов по шагам конвейера Фемиды")
    ap.add_argument("session", nargs="?", help="путь к session.jsonl (по умолчанию — свежая сессия проекта)")
    ap.add_argument("--all", action="store_true", help="все сессии проекта сводно")
    ap.add_argument("--track", choices=sorted(TRACK_BUDGET), help="сверить с целью трека, exit 3 при перерасходе")
    ap.add_argument("--ctx-limit", type=int, default=None, metavar="N",
                    help="потолок контекста запроса главного потока, exit 3 при пробое; "
                         "0 — выключить (по умолчанию 200 000)")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    ctx_limit = args.ctx_limit
    if ctx_limit is None:
        try:
            import context_ledger as _cl
            ctx_limit = _cl.CTX_LIMIT_DEFAULT
        except ImportError:
            ctx_limit = 200_000

    if args.all:
        files = sorted(glob.glob(os.path.join(project_dir(os.getcwd()), "*.jsonl")))
        if not files:
            print("сессий проекта не найдено", file=sys.stderr)
            return obs.KOD_OSHIBKA
        rep = merge([collect(f) for f in files])
    else:
        path = args.session or latest_session(os.getcwd())
        if not path or not os.path.isfile(path):
            print(f"session.jsonl не найден ({path}). Передай путь явно.", file=sys.stderr)
            return obs.KOD_OSHIBKA
        rep = collect(path)

    if args.json:
        rep["ctx_limit"] = ctx_limit
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        rc = ctx_verdict(rep.get("max_ctx", 0), ctx_limit)[1]
        if args.track:
            rc = max(rc, track_verdict(args.track, tokens(rep["total"]))[1])
        return rc
    return render(rep, args.track, ctx_limit)


if __name__ == "__main__":
    obs.zavershit(main)
