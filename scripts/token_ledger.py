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

# Тип агента, когда описание ничего не сказало. Агенты воркфлоу (`workflow-subagent`)
# получают задание английским промптом и мимо русских шаблонов проходят целиком;
# по делу они не работают — конвейер дела идёт поимёнными агентами из STEP_BY_AGENT.
# Замер 04.08.2026: 8,4 млн токенов «прочего» — это они и были.
STEP_BY_TYPE_FALLBACK = {"workflow-subagent": "система"}

# Шаг протокола по одному вызову инструмента ИЗ ГЛАВНОГО ПОТОКА. Главный поток —
# самая дорогая статья: замер 04.08.2026 по 33 сессиям дал 59,8 % расхода, и весь он
# лежал одной строкой «основной поток». Разбивка по шагам покрывала 22 % денег, то есть
# бюджетные чекпойнты после шагов 2 и 3 отвечали не про те деньги. Сигнал берём из ПУТИ
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
    (r"03_drafts/|create_docx|md_to_docx|gosposhlina|calc395", "4 составление"),
    (r"quality_gate|document_guard|table_guard|verify_inn|verify_act|crosscheck_numbers|"
     r"sroki|02_hearings/", "5 проверка"),
    (r"_index\.md|_clients\.md|_client\.md|registry_check|redline", "6 архив"),
    (r"themis_status|token_ledger|retro\.py|setup_doctor|pd_guard|claude_guard|preflight_search|"
     r"update_legal_corpus|lessons-log|_logs/|scripts/|\.claude/", "система"),
]

# Доля нераспределённого расхода, выше которой цифры по шагам нельзя считать
# основанием для решений. Замер 03.08.2026 на живой сессии: 16,3% в «прочее».
OTHER_ALERT_SHARE = 5.0

# «основной поток» больше не шаг: его расход распределён по шагам через MAIN_SIGNALS,
# а сам он остался строкой в разрезе агентов. Ключ сохранён в порядке ради старых --json.
STEP_ORDER = ["основной поток", "0 интейк", "1 карта", "2 практика", "3 позиция",
              "4 составление", "5 проверка", "6 архив", "система", "прочее"]


# Неизвестная модель. Прежде любая строка без opus|sonnet|haiku молча считалась
# по верхней ставке и складывалась в строку «opus» — в логах проекта такой была
# claude-fable-5 (1356 вызовов, 73,1 млн токенов, 5,0 % свода). Прибор при этом
# показывал «opus», и понять, что это другая модель, было нельзя ни по одной цифре.
# Оценка по верхней ставке остаётся (занижать расход опаснее, чем завышать), но
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
    """Вернуть (usage по моделям, спавны субагентов, свой тип, реплики по шагам).

    Дедупликация по requestId: одна реплика пишется в jsonl несколько раз.
    Четвёртый элемент — реплики в порядке файла с уже проставленным шагом: шаг
    «липкий», реплика без своего сигнала относится к последнему объявленному шагу,
    потому что поток остаётся в шаге, пока не начнёт следующий.
    """
    seen: dict[str, tuple[dict, str]] = {}
    step_at: dict[str, str | None] = {}
    cur_step: str | None = None
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
    return per_model, spawns, {"type": own_type, "desc": own_desc}, turns


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
    # инструмент: Edit/Write по scripts/ и .claude/ чинят Фемиду, Bash её применяет.
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
    _, spawns, _, main_turns = scan_file(session_path)

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
        models, child_spawns, own, _ = scan_file(path)
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
    print("  расход главного потока разнесён по шагам (сигнал — путь и команда вызова); "
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
              "(сигнал берётся из пути файла и команды).")

    print(f"\n{'модель':<34}{'токенов':>14}{'доля':>8}")
    for name, u in sorted(rep["by_model"].items(), key=lambda kv: -tokens(kv[1])):
        print(f"{name:<34}{tokens(u):>14,}{tokens(u)/total_tok*100:>7.1f}%")
    unknown = {k: v for k, v in rep["by_model"].items() if k.startswith(UNKNOWN_PREFIX)}
    if unknown:
        n = sum(tokens(v) for v in unknown.values())
        print(f"⚠ модель вне тарифной таблицы: {n:,} токенов ({n/total_tok*100:.1f}%). "
              "Цена по ним — ВЕРХНЯЯ оценка (ставка opus). Внести тариф в RATES.")

    if not track:
        return 0
    text, rc = track_verdict(track, tokens(t))
    print(text)
    return rc


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
                  "не тот проект либо логи ещё не сброшены на диск. "
                  "Это НЕ «уложились».", 2)
    head = f"\nтрек {track}: цель {limit:,}, факт {spent:,} — ".replace(",", " ")
    if spent > limit:
        return (head + f"ПЕРЕРАСХОД ×{spent/limit:.2f}. "
                       "СТОП: доложить владельцу до продолжения.", 3)
    return head + f"в пределах ({spent/limit*100:.0f}% цели).", 0


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
            # реплика без своего сигнала остаётся в текущем шаге
            fh.write(assistant("claude-opus-5", 1, 1, 1, 1, "req_h"))
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
        # Через .get: сломанная атрибуция должна давать НАЗВАННЫЙ провал проверки,
        # а не KeyError — иначе непонятно, что именно отвалилось.
        def st(step: str) -> dict:
            return rep["by_step"].get(step, blank())

        checks = [
            ("дубли по requestId схлопнуты", st("прочее")["in"] == 15),
            ("synthetic отброшен", st("прочее")["out"] == 26),
            ("субагент учтён один раз", st("1 карта")["in"] == 1100),
            ("вложенный агент свёрнут в шаг родителя", st("1 карта")["cr"] == 4400),
            ("шагов ровно четыре",
             set(rep["by_step"]) == {"прочее", "6 архив", "1 карта", "5 проверка"}),
            # Главный поток — самая дорогая статья; строкой «основной поток» он
            # съедал 59,8 % расхода мимо всякой разбивки по шагам.
            ("главный поток разнесён по шагам", "основной поток" not in rep["by_step"]),
            ("вызов прибора дела привязан к своему шагу", st("6 архив")["in"] == 2),
            ("реплика без сигнала наследует текущий шаг", st("6 архив")["cr"] == 5),
            ("главный поток виден в разрезе агентов",
             rep["by_agent"]["основной поток"]["in"] == 17),
            ("до первого сигнала расход честно в «прочем»", st("прочее")["cr"] == 48),
            ("сигнал по команде прибора", main_step_signal(
                "Bash", {"command": "python3 scripts/quality_gate.py --case cases/X/Y"})
                == "5 проверка"),
            ("сигнал по пути файла дела", main_step_signal(
                "Read", {"file_path": "cases/К/дело/01_context/practice.md"}) == "2 практика"),
            ("сигнал по запуску агента", main_step_signal(
                "Agent", {"subagent_type": "doc-drafter", "description": "документ"})
                == "4 составление"),
            ("посторонний файл сигналом не считается",
             main_step_signal("Read", {"file_path": "README.md"}) is None),
            # Правка прибора и его запуск — разные вещи: первое чинит систему,
            # второе исполняет шаг дела. Иначе вечер за починкой document_guard
            # ложится в отчёт как «шаг 5 проверка».
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
            ("агент воркфлоу учтён", st("5 проверка")["out"] == 8),
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
        ("пустая сессия даёт «данных нет», а не «в пределах»",
         track_verdict("FULL", 0)[1] == 2 and "ДАННЫХ НЕТ" in track_verdict("FULL", 0)[0]),
        ("расход в пределах цели даёт 0",
         track_verdict("FULL", TRACK_BUDGET["FULL"] // 2)[1] == 0),
        ("перерасход даёт 3", track_verdict("FAST", TRACK_BUDGET["FAST"] * 2)[1] == 3),
        ("граница цели — ещё не перерасход",
         track_verdict("FAST", TRACK_BUDGET["FAST"])[1] == 0),
        # Тариф: opus дороже sonnet и haiku на одном и том же объёме.
        ("тариф модели не подменяется",
         cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-opus-5") == 15.0
         and cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-sonnet-5") == 3.0
         and cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-haiku-4-5") == 1.0),
        ("неизвестная модель считается по верхней ставке в деньгах",
         cost({"in": 1_000_000, "out": 0, "cw": 0, "cr": 0}, "claude-fable-5") == 15.0),
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
