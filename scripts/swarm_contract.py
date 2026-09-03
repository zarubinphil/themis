#!/usr/bin/env python3
"""swarm_contract.py — контракт роя: тип, модель, бюджет, приемка, потолок числа.

Корень №3 разбора 01.09.2026: рой поднимался без контракта. 30 спавнов из 54 —
general-purpose вне реестра ролей; три координатора собрали листовой рой тремя
несовместимыми способами; один спавнил агентов собственного типа; девять агентов
старше 20 минут, TaskStop не вызывался ни разу; бюджет и время не были аргументом
спавна; приемка шла по тексту агента, а не по маркеру на диске; потолок числа
одновременных агентов был объявлен, а не посчитан.

Этот прибор — единый источник правды контракта роя. $0, без сети, без модели.

    --limits [--json]              потолок числа агентов (посчитан) + потолки токенов/времени листа
    --accept МАРКЕР ФАЙЛ           приемка по маркеру на диске: код 1, если маркера нет
    --audit-agents [ДИР]           frontmatter model всех агентов ↔ AGENT_PIN (заголовок ≠ правда)
    --substitutions ФАЙЛ...        подмены модели харнессом отдельной строкой, не растворять
    --audit-run ДИР [--amendment T]  разбор транскриптов роя: general-purpose под охоту,
                                   спавн собственного типа, слепой веб, потерянная поправка
    --stale ДИР                    транскрипты со сроком жизни выше потолка — доклад, не снятие
    --selftest

Потолок ЧИСЛА агентов исполняется счетом живых (live_register/live_release), а не
печатью формулы: claude_guard зовет live_register на каждом спавне Agent. Строка
подмены модели живет в разрезе расхода token_ledger.render (fold выполнен); флаг
--substitutions здесь — детализация по журналу, а не второй источник правды.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# ── Потолки контракта ─────────────────────────────────────────────────────────
# Потолок ЧИСЛА одновременных агентов посчитан, а не объявлен: за порогом
# min(16, ядра−2) рост числа листьев упирается в очередь ОС и ускорения не дает,
# а платится полная цена. На 8 логических ядрах = 6.
CONCURRENCY_HARD_MAX = 16


def concurrency_cap() -> int:
    cores = os.cpu_count() or 1
    return max(1, min(CONCURRENCY_HARD_MAX, cores - 2))


# Потолки листа — аргумент спавна, не пожелание. Лист охоты, упершийся в мертвый
# источник, за прогон 01.09 жег 1,3–2,4 млн токенов вслепую; 20 минут — порог, на
# котором в разборе стояли девять неснятых агентов. Координатор живет дольше листа.
LEAF_TOKEN_CEILING = 600_000
LEAF_TIME_CEILING_MIN = 20
COORD_TIME_CEILING_MIN = 45


def limits() -> dict:
    return {
        "concurrency_cap": concurrency_cap(),
        "cpu_count": os.cpu_count() or 1,
        "leaf_token_ceiling": LEAF_TOKEN_CEILING,
        "leaf_time_ceiling_min": LEAF_TIME_CEILING_MIN,
        "coord_time_ceiling_min": COORD_TIME_CEILING_MIN,
    }


def cmd_limits(as_json: bool) -> int:
    lim = limits()
    if as_json:
        print(json.dumps(lim, ensure_ascii=False))
        return 0
    print(f"потолок одновременных агентов (посчитан min(16, ядра−2)): {lim['concurrency_cap']} "
          f"(ядер {lim['cpu_count']})")
    print(f"потолок токенов листа (аргумент спавна): {lim['leaf_token_ceiling']:,}")
    print(f"потолок времени листа, мин: {lim['leaf_time_ceiling_min']}")
    print(f"потолок времени координатора, мин: {lim['coord_time_ceiling_min']}")
    return 0


# ── Счет живых агентов: потолок исполняется счетом, а не печатью (item 6) ──────
# Записи на диске — прогон переживает перезапуск процесса. Два снятия записи:
#   1) новый спавн той же сессии закрывает ее прежние ФОНОВЫЕ-НЕТ (foreground)
#      записи: главный поток вернулся за следующим спавном — прежний агент завершился.
#      Без этого последовательный конвейер (карта→охота→документ) ложно упирался бы
#      в потолок, хотя одновременно бежит один агент.
#   2) протухание: запись старше LIVE_STALE_MIN считается ушедшей — гарантия от
#      вечной блокировки при падении сессии посреди фонового агента.
# ponytail: снятие по ФАКТУ выхода агента — апгрейд через SubagentStop-хук, когда
# разморозят .claude/settings.json; потолок утечки и так закрыт протуханием.
# Граница видимости та же, что у spawn_verdict: хук видит только главный поток,
# листья из координатора судит --audit-run пост-фактум.
LIVE_STALE_MIN = COORD_TIME_CEILING_MIN


def live_state_path() -> Path:
    override = os.environ.get("THEMIS_SWARM_LIVE")  # селфтест/изоляция прогона
    return Path(override) if override else PROJECT_ROOT / ".cache" / "swarm_live.json"


def live_load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []                       # битый/отсутствующий реестр — пустой, не вечный блок
    return data if isinstance(data, list) else []


def live_save(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)               # атомарно: оборванная запись не портит реестр


def live_prune(records: list[dict], now: float) -> list[dict]:
    floor = now - LIVE_STALE_MIN * 60
    return [r for r in records if r.get("ts", 0) >= floor]


def live_register(name: str, state_path: Path | None = None, session: str = "",
                  background: bool = False, now: float | None = None) -> str:
    """Взять слот на спавн. Возврат: "" — слот взят (запись добавлена), иначе
    причина отбоя. Седьмой за потолком отбит СЧЕТОМ живых, а не напечатанным числом."""
    path = state_path or live_state_path()
    now = time.time() if now is None else now
    import fcntl                        # Unix: хук и так живет на macOS/Linux
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as fh:          # два хука не перепишут реестр вдвоем
        fcntl.flock(fh, fcntl.LOCK_EX)
        records = live_prune(live_load(path), now)
        if session:                     # главный поток вернулся — его foreground-агенты завершились
            records = [r for r in records if r.get("session") != session or r.get("bg")]
        if len(records) >= concurrency_cap():
            live_save(path, records)    # протухшие сняты даже при отбое
            return (f"живых агентов {len(records)} — потолок {concurrency_cap()} "
                    f"(min(16, ядра−2)). За потолком рост числа листьев упирается в "
                    f"очередь ОС: ускорения нет, цена полная. Дождаться освобождения "
                    f"слота либо снять лишнего через TaskStop; реестр: {path}")
        records.append({"id": uuid.uuid4().hex[:12], "name": name,
                        "session": session, "bg": background, "ts": now})
        live_save(path, records)
    return ""


def live_release(record_id: str, state_path: Path | None = None) -> int:
    """Снять запись по id (выход агента / решение владельца). 0 — снята, 1 — не нашлась."""
    path = state_path or live_state_path()
    records = live_load(path)
    left = [r for r in records if r.get("id") != record_id]
    if len(left) == len(records):
        return 1
    live_save(path, left)
    return 0


# ── Приемка по маркеру на диске (item 5) ──────────────────────────────────────
def cmd_accept(marker: str, path: str) -> int:
    """Агент, объявивший себя закончившим без маркера на диске, — незакрыт.
    Приемка по тексту агента не принимается: правда — только файл."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        print(f"НЕ ЗАКРЫТ: файл артефакта не читается ({path}) — маркера «{marker}» нет, "
              f"агент не выполнил задание", file=sys.stderr)
        return 1
    if re.search(marker, text):
        print(f"принято: маркер «{marker}» на диске в {p.name}")
        return 0
    print(f"НЕ ЗАКРЫТ: маркер «{marker}» не найден в {p.name}. Агент объявил работу "
          f"законченной, но артефакт на диске его не подтверждает — считается незакрытым.",
          file=sys.stderr)
    return 1


# ── Аудит frontmatter агентов ↔ политика (item 2) ─────────────────────────────
# Источник правды о модели — таблица, не заголовок. Пин = самая ответственная
# модель роли. Совпадает с разделом «Модели под шаги» в .claude/CLAUDE.md.
#
# Записи ДВУХ видов, и судить их одинаково нельзя (ложная тревога 02.09.2026:
# «council-role: файла агента нет» на исправном доме — сторож, краснеющий зря,
# перестает читаться):
#   FILE_PIN — файловые типы, лежат в .claude/agents/. Файл обязан существовать,
#              frontmatter обязан совпасть с пином, и наоборот: файл без пина —
#              тоже расхождение (заголовок стал единственной правдой о модели).
#   ROLE_PIN — логические роли. Файла агента у них нет и быть не должно: на роль
#              отображаются скиллы и агенты. Красная такая запись ровно тогда,
#              когда потребителя нет ни одного — мертвая строка политики.
FILE_PIN = {
    "archivist": "haiku",
    "case-mapper": "sonnet",
    "case-reconciler": "sonnet",
    "doc-drafter": "opus",
    "doc-reviewer": "opus",
    "docx-reader": "haiku",
    "hearing-prep": "sonnet",
    "image-reader": "sonnet",
    "inbox-triage": "haiku",
    "pdf-reader": "sonnet",
    "practice-hunter-classic": "sonnet",
    "practice-hunter-skeptic": "opus",
    "practice-hunter-tactical": "sonnet",
}

ROLE_PIN = {
    "practice-leaf": "haiku",   # тип листа охоты, назначается координатором на спавне
    "council-role": "sonnet",   # на нее отображаются скиллы askacouncil / position-council
}

AGENT_PIN = {**FILE_PIN, **ROLE_PIN}  # общий вид политики моделей

# Где ищется потребитель логической роли: упоминание имени роли в конфигурации дома.
_ROLE_CONSUMER_DIRS = ("scripts", ".claude")
_ROLE_CONSUMER_SUFFIXES = (".py", ".md", ".js", ".json")

_FM_MODEL_RE = re.compile(r"^\s*model\s*:\s*['\"]?([A-Za-z0-9._-]+)['\"]?\s*$", re.M)
_FM_NAME_RE = re.compile(r"^\s*name\s*:\s*['\"]?([A-Za-z0-9._-]+)['\"]?\s*$", re.M)


def _frontmatter(text: str) -> str:
    m = re.match(r"\s*---\s*\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def _pin_alias(model: str) -> str:
    low = model.lower()
    for alias in ("opus", "sonnet", "haiku"):
        if alias in low:
            return alias
    return low


def _role_consumers(role: str, root: Path) -> list[str]:
    """Файлы дома, отображающие что-либо на эту логическую роль (себя не считаем)."""
    me = Path(__file__).resolve()
    hits = []
    for d in _ROLE_CONSUMER_DIRS:
        for f in (root / d).rglob("*"):
            if not f.is_file() or f.suffix not in _ROLE_CONSUMER_SUFFIXES:
                continue
            if f.resolve() == me:
                continue
            if role in f.read_text(encoding="utf-8", errors="replace"):
                hits.append(str(f.relative_to(root)))
    return hits


def audit_agents(agents_dir: Path, root: Path | None = None,
                 roles: dict[str, str] | None = None) -> int:
    root = root or PROJECT_ROOT
    roles = ROLE_PIN if roles is None else roles
    if not agents_dir.is_dir():
        print(f"ERROR: каталог агентов не найден: {agents_dir}", file=sys.stderr)
        return 2
    bad, seen = [], set()
    for f in sorted(agents_dir.glob("*.md")):
        fm = _frontmatter(f.read_text(encoding="utf-8"))
        nm = _FM_NAME_RE.search(fm)
        name = nm.group(1) if nm else f.stem
        seen.add(name)
        mm = _FM_MODEL_RE.search(fm)
        if not mm:
            bad.append(f"{f.name}: в frontmatter нет строки model:")
            continue
        got = _pin_alias(mm.group(1))
        if name not in FILE_PIN:
            bad.append(f"{name} ({f.name}): файлового типа нет в FILE_PIN — модель не "
                       f"сторожится, заголовок стал единственным источником правды")
            continue
        want = FILE_PIN[name]
        if got != want:
            bad.append(f"{name} ({f.name}): frontmatter «{got}», политика — «{want}»")
    for name in sorted(set(FILE_PIN) - seen):
        bad.append(f"{name}: пин «{FILE_PIN[name]}» есть в политике, а файла агента нет")
    for name in sorted(roles):
        if name in seen:
            bad.append(f"{name}: логическая роль, а в .claude/agents/ лежит файл агента — "
                       f"либо роль стала типом (в FILE_PIN), либо файл лишний")
            continue
        if not _role_consumers(name, root):
            bad.append(f"{name}: логическая роль без единого потребителя — мертвая запись "
                       f"политики (ни один скилл и ни один агент на нее не отображается)")
    if bad:
        print(f"расхождений frontmatter агентов с политикой: {len(bad)}", file=sys.stderr)
        for msg in bad:
            print("  · " + msg, file=sys.stderr)
        return 1
    print(f"файловых типов сходится с политикой: {len(seen)}; "
          f"логических ролей с живым потребителем: {len(roles)}")
    return 0


# ── Подмена модели харнессом отдельной строкой (item 3) ───────────────────────
# «claude-…[1m] is temporarily unavailable … auto mode cannot determine» — реальная
# запись журнала о том, что харнесс не смог поднять заказанную модель. Растворять
# ее в разрезе нельзя: 15 подмен за прогон 01.09 были невидимы. Считаем по журналу.
# Регулярка ПУБЛИЧНАЯ: тот же счет ведет token_ledger (строка в разрезе расхода),
# два прибора обязаны считать одинаково — иначе два источника правды (корень №2).
SUBST_RE = re.compile(
    r"(is temporarily unavailable|auto mode cannot determine|"
    r"falling back to|automatically switched|подмен)", re.I)
_MODEL_IN_LINE_RE = re.compile(r"claude-[a-z0-9.\-]+", re.I)


def _iter_jsonl_text(path: Path):
    """Строки файла как текст (JSONL или произвольный лог) — для поиска сигнатур."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield line
    except OSError:
        return


def substitutions(paths: list[str]) -> int:
    events: list[tuple[str, str]] = []
    for path in paths:
        p = Path(path)
        for line in _iter_jsonl_text(p):
            if SUBST_RE.search(line):
                m = _MODEL_IN_LINE_RE.search(line)
                model = m.group(0) if m else "модель не названа в строке"
                events.append((p.name, model))
    n = len(events)
    if not n:
        print("подмен модели харнессом в журнале не найдено")
        return 0
    from collections import Counter
    by_model = Counter(model for _, model in events)
    print(f"ПОДМЕНА МОДЕЛИ ХАРНЕССОМ — отдельная строка разреза: {n} событий")
    for model, cnt in by_model.most_common():
        print(f"  · {model}: {cnt}")
    print("  (событие подмени — заказанная модель не поднята; токены на подмену "
          "считаются по фактически сработавшей модели в token_ledger, здесь — число событий)")
    return 1


# ── Разбор транскриптов роя: контракт листа (items 1, 7) ──────────────────────
# PreToolUse-хук claude_guard видит только спавны ГЛАВНОГО потока; спавн листа из
# координатора (субагент) хук не видит. Поэтому контракт листа держится пост-фактум
# по транскриптам: тип листа, слепой веб, потерянная поправка.
HUNTER_TYPES = {"practice-hunter-classic", "practice-hunter-skeptic",
                "practice-hunter-tactical"}
PRACTICE_TOOLS_RE = re.compile(r"practice_search\.py|practice_index\.md|cite\.py|verify_act\.py",
                               re.I)
WEB_TOOLS = ("WebSearch", "WebFetch")
LEGAL_HINT_RE = re.compile(r"практик|постановлен|определен|пленум|суд|иск|жалоб|ст\.\s*\d",
                           re.I)


def _agent_type_of(transcript: Path) -> str:
    """Тип агента транскрипта — из первой строки с subagent_type/agentType, если есть."""
    for line in _iter_jsonl_text(transcript):
        m = re.search(r'"(?:subagent_type|agentType|subagentType)"\s*:\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return ""


def _spawns_in(transcript: Path):
    """Спавны Agent из транскрипта: [(subagent_type, prompt_text)]."""
    out = []
    for line in _iter_jsonl_text(transcript):
        if '"Agent"' not in line and '"Task"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        for tu in _find_tool_uses(obj):
            name = tu.get("name")
            if name not in ("Agent", "Task"):
                continue
            inp = tu.get("input") or {}
            st = (inp.get("subagent_type") or inp.get("agentType")
                  or inp.get("subagentType") or "")
            prompt = " ".join(str(inp.get(k, "")) for k in ("prompt", "description"))
            out.append((st, prompt))
    return out


def _find_tool_uses(obj):
    """tool_use-блоки где угодно в структуре записи (формат транскрипта варьируется)."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "tool_use" and "name" in x:
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return found


def audit_run(transcript_dir: Path, amendment: str = "") -> int:
    if not transcript_dir.is_dir():
        print(f"ERROR: каталог транскриптов не найден: {transcript_dir}", file=sys.stderr)
        return 2
    findings: list[str] = []
    for tr in sorted(transcript_dir.glob("*.jsonl")):
        own = _agent_type_of(tr)
        # слепой веб листа: веб есть, приборов практики нет
        text = "".join(_iter_jsonl_text(tr))
        web = sum(text.count(f'"{w}"') for w in WEB_TOOLS)
        pribor = len(PRACTICE_TOOLS_RE.findall(text))
        if web > 0 and pribor == 0 and LEGAL_HINT_RE.search(text):
            findings.append(f"{tr.name}: слепой веб — {web} веб-вызовов, 0 приборов практики")
        for st, prompt in _spawns_in(tr):
            if st == "general-purpose" and LEGAL_HINT_RE.search(prompt):
                findings.append(f"{tr.name}: задача поиска практики отдана general-purpose "
                                f"вместо practice-leaf")
            if own and st == own:
                findings.append(f"{tr.name}: спавн агента собственного типа ({st}) — "
                                f"координатор сажает свой промпт листом")
            if st in ("practice-leaf",) and amendment and amendment not in prompt:
                findings.append(f"{tr.name}: спавн листа без действующей поправки прогона "
                                f"«{amendment[:40]}…» — поправка не унаследована вниз")
    if findings:
        print(f"нарушений контракта листа: {len(findings)}", file=sys.stderr)
        for f in findings:
            print("  · " + f, file=sys.stderr)
        return 1
    print("контракт листа соблюден: типизированные листья, приборы вызваны, поправка унаследована")
    return 0


# ── Живой вердикт одного спавна (items 1, 7) — вызывается claude_guard на Agent ──
# PreToolUse видит спавны главного потока: тут ловим типизацию роли и наследование
# поправки ДО запуска, а не пост-фактум по транскрипту (audit_run — второй эшелон).
def spawn_verdict(name: str, prompt: str, own_type: str = "", amendment: str = "") -> str:
    """Причина блока для одного спавна Agent или "" (пропуск). $0, без сети.

    - поиск практики, отданный general-purpose → блок с называнием practice-leaf;
    - спавн агентом агента СВОЕГО типа → блок (координатор сажает свой промпт листом);
    - спавн листа без действующей поправки прогона → блок (поправка не унаследована).
    Универсальный агент ВНЕ работы по делу НЕ трогаем (нет правового намека → "")."""
    if own_type and name == own_type:
        return (f"спавн агента собственного типа ({name}) — координатор сажает свой "
                f"промпт листом. Лист должен быть иного типа роли.")
    if name == "general-purpose" and LEGAL_HINT_RE.search(prompt or ""):
        return ("задача поиска практики отдана general-purpose вне реестра ролей. "
                "Правильный тип листа — practice-leaf (координатор — practice-hunter-*). "
                "Универсальный агент под охоту практики запрещен.")
    if name == "practice-leaf" and amendment and amendment not in (prompt or ""):
        return (f"спавн листа без действующей поправки прогона «{amendment[:40]}…» — "
                f"поправка обязана наследоваться вниз по рою (текстом в промпте листа).")
    return ""


def cmd_verdict(name: str, prompt: str, own_type: str, amendment: str) -> int:
    reason = spawn_verdict(name, prompt, own_type, amendment)
    if reason:
        print("БЛОК КОНТРАКТА РОЯ: " + reason, file=sys.stderr)
        return 1
    print(f"спавн {name}: контракт роя соблюден")
    return 0


# ── Сторож времени: доклад, не снятие (item 4) ────────────────────────────────
def stale(transcript_dir: Path) -> int:
    """Транскрипты со сроком жизни выше потолка — доклад владельцу и предложение
    TaskStop. Молча не снимаем: снятие — решение владельца. Срок = last−first mtime
    строк недоступен дешево, берем span файла (первая↔последняя запись по ctime→mtime)."""
    if not transcript_dir.is_dir():
        print(f"ERROR: каталог транскриптов не найден: {transcript_dir}", file=sys.stderr)
        return 2
    over = []
    for tr in sorted(transcript_dir.glob("*.jsonl")):
        try:
            span_min = (tr.stat().st_mtime - tr.stat().st_ctime) / 60.0
        except OSError:
            continue
        own = _agent_type_of(tr)
        ceiling = COORD_TIME_CEILING_MIN if own in HUNTER_TYPES else LEAF_TIME_CEILING_MIN
        if span_min > ceiling:
            over.append((tr.name, own or "?", span_min, ceiling))
    if not over:
        print("агентов старше потолка времени не найдено")
        return 0
    print(f"агенты старше потолка времени — предложить TaskStop (НЕ снято автоматически): {len(over)}",
          file=sys.stderr)
    for name, typ, span, ceil in over:
        print(f"  · {name} [{typ}]: {span:.1f} мин, потолок {ceil} — предложить владельцу TaskStop",
              file=sys.stderr)
    return 1


# ── selftest ──────────────────────────────────────────────────────────────────
def selftest() -> int:
    import tempfile
    assert concurrency_cap() == max(1, min(16, (os.cpu_count() or 1) - 2))
    assert 1 <= concurrency_cap() <= 16
    lim = limits()
    assert lim["leaf_token_ceiling"] == LEAF_TOKEN_CEILING

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # Потолок числа — ПРИНУЖДЕНИЕ, а не формула. Сверка concurrency_cap() с той
        # же формулой (выше) — тавтология и пункт 6 ею НЕ закрывается: зеленая
        # проба обязана показать, что седьмой за потолком отбит, а освобожденный
        # слот пускает (урок «проба, зеленая при обеих подстановках»).
        sp = d / "live.json"
        cap = concurrency_cap()
        for i in range(cap):
            assert live_register(f"leaf-{i}", state_path=sp, session="s1",
                                 background=True) == ""
        over = live_register("sedmoy", state_path=sp, session="s1", background=True)
        assert over and str(cap) in over, "спавн за потолком живых не отбит"
        recs = live_load(sp)
        assert len(recs) == cap
        assert live_release(recs[0]["id"], state_path=sp) == 0
        assert live_register("vmesto-osvobozhdennogo", state_path=sp, session="s1",
                             background=True) == "", "освобожденный слот не отдал место"
        assert live_release("net-takogo", state_path=sp) == 1
        # Последовательный конвейер (карта→охота→документ) слот не держит: новый
        # спавн сессии закрывает ее прежние foreground-записи — ложного отбоя нет.
        live_save(sp, [])
        for i in range(cap + 5):
            assert live_register(f"fg-{i}", state_path=sp, session="s2",
                                 background=False) == "", \
                "последовательный конвейер уперся в потолок — ложный отбой"
        # Протухшая запись не держит слот вечно (гарантия от вечной блокировки).
        live_save(sp, [{"id": "old", "name": "drevny", "session": "s3", "bg": True,
                        "ts": time.time() - (LIVE_STALE_MIN + 1) * 60}])
        for i in range(cap):
            assert live_register(f"fresh-{i}", state_path=sp, session="s4",
                                 background=True) == "", \
                "протухшая запись заблокировала спавн навсегда"

        # accept
        good = d / "practice.md"
        good.write_text("тело\n## СОВЕТ ЗАВЕРШЕН\n", encoding="utf-8")
        assert cmd_accept(r"## СОВЕТ ЗАВЕРШЕН", str(good)) == 0
        assert cmd_accept(r"## СОВЕТ ЗАВЕРШЕН", str(d / "нет.md")) == 1
        good.write_text("тело без маркера\n", encoding="utf-8")
        assert cmd_accept(r"## СОВЕТ ЗАВЕРШЕН", str(good)) == 1

        # audit-agents
        ad = d / "agents"
        ad.mkdir()
        for name, m in FILE_PIN.items():
            (ad / f"{name}.md").write_text(
                f"---\nname: {name}\nmodel: {m}\ntools: Read\n---\nтело", encoding="utf-8")
        # дом-макет с потребителем логической роли
        (d / "scripts").mkdir()
        (d / ".claude").mkdir()
        (d / "scripts" / "policy.py").write_text('MAP = {"askacouncil": "council-role"}\n',
                                                 encoding="utf-8")
        live = {"council-role": "sonnet"}
        assert audit_agents(ad, root=d, roles=live) == 0, "полный верный набор отвергнут"
        assert audit_agents(ad, root=d, roles={"мертвая-роль": "haiku"}) == 1, \
            "роль без потребителя пропущена"
        (ad / "doc-drafter.md").write_text(
            "---\nname: doc-drafter\nmodel: sonnet\ntools: Read\n---\nтело", encoding="utf-8")
        assert audit_agents(ad, root=d, roles=live) == 1, "подмененный model пропущен"
        (ad / "doc-drafter.md").write_text(
            "---\nname: doc-drafter\nmodel: opus\ntools: Read\n---\nтело", encoding="utf-8")
        (ad / "чужой.md").write_text(
            "---\nname: чужой\nmodel: opus\ntools: Read\n---\nтело", encoding="utf-8")
        assert audit_agents(ad, root=d, roles=live) == 1, "файл без пина в политике пропущен"
        (ad / "чужой.md").unlink()

        # substitutions
        log = d / "s.jsonl"
        log.write_text(
            '{"text":"claude-sonnet-5[1m] is temporarily unavailable, so auto mode..."}\n'
            '{"text":"обычная строка"}\n'
            '{"text":"claude-opus-4-8 is temporarily unavailable"}\n', encoding="utf-8")
        assert substitutions([str(log)]) == 1, "подмена не замечена"
        clean = d / "clean.jsonl"
        clean.write_text('{"text":"все хорошо"}\n', encoding="utf-8")
        assert substitutions([str(clean)]) == 0

        # audit-run: general-purpose под охоту + собственный тип
        rd = d / "run"
        rd.mkdir()
        (rd / "coord.jsonl").write_text(
            '{"type":"tool_use_meta","subagent_type":"practice-hunter-tactical"}\n'
            '{"type":"assistant","content":[{"type":"tool_use","name":"Agent",'
            '"input":{"subagent_type":"general-purpose","prompt":"найди практику ВС РФ по ст. 333"}}]}\n'
            '{"type":"assistant","content":[{"type":"tool_use","name":"Agent",'
            '"input":{"subagent_type":"practice-hunter-tactical","prompt":"клон координатора"}}]}\n',
            encoding="utf-8")
        assert audit_run(rd) == 1, "нарушения контракта листа не пойманы"
        # чистый лист
        rd2 = d / "run2"
        rd2.mkdir()
        (rd2 / "leaf.jsonl").write_text(
            '{"subagent_type":"practice-leaf"}\n'
            '{"type":"assistant","content":[{"type":"text","text":"practice_search.py отработал"}]}\n',
            encoding="utf-8")
        assert audit_run(rd2) == 0, "чистый лист забракован"

        # spawn_verdict (item 1, 7): типизация роли и наследование поправки
        assert spawn_verdict("general-purpose", "найди практику ВС РФ по ст. 333"), \
            "general-purpose под охоту пропущен"
        assert not spawn_verdict("general-purpose", "почини сборку проекта"), \
            "универсальный агент вне дела ошибочно заблокирован"
        assert spawn_verdict("practice-hunter-tactical", "клон",
                             own_type="practice-hunter-tactical"), "свой тип пропущен"
        assert spawn_verdict("practice-leaf", "ищи практику",
                             amendment="пользоваться Линкеем"), "потерянная поправка пропущена"
        assert not spawn_verdict("practice-leaf", "ищи Линкеем практику",
                                 amendment="Линкеем"), "поправка в промпте — ложный блок"

        # stale: файл с искусственно старым ctime не смоделировать переносимо —
        # проверяем лишь, что пустой каталог дает код 0
        empty = d / "empty"
        empty.mkdir()
        assert stale(empty) == 0

    print("selftest пройден: лимиты посчитаны, приемка/аудит/подмена/контракт листа судятся fail-closed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Контракт роя.")
    ap.add_argument("--limits", action="store_true")
    ap.add_argument("--json", action="store_true", help="машинный вывод для --limits")
    ap.add_argument("--accept", nargs=2, metavar=("МАРКЕР", "ФАЙЛ"))
    ap.add_argument("--audit-agents", nargs="?", const="", metavar="ДИР")
    ap.add_argument("--substitutions", nargs="+", metavar="ФАЙЛ")
    ap.add_argument("--audit-run", metavar="ДИР")
    ap.add_argument("--amendment", default="", help="действующая поправка прогона для --audit-run")
    ap.add_argument("--stale", metavar="ДИР")
    ap.add_argument("--verdict", nargs=2, metavar=("ТИП", "ПРОМПТ"),
                    help="живой вердикт спавна: тип листа + текст промпта")
    ap.add_argument("--own-type", default="", help="тип координатора для --verdict")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.verdict:
        return cmd_verdict(a.verdict[0], a.verdict[1], a.own_type, a.amendment)
    if a.limits:
        return cmd_limits(a.json)
    if a.accept:
        return cmd_accept(a.accept[0], a.accept[1])
    if a.audit_agents is not None:
        d = Path(a.audit_agents) if a.audit_agents else (PROJECT_ROOT / ".claude" / "agents")
        return audit_agents(d)
    if a.substitutions:
        return substitutions(a.substitutions)
    if a.audit_run:
        return audit_run(Path(a.audit_run), a.amendment)
    if a.stale:
        return stale(Path(a.stale))
    ap.error("нужен один из: --limits, --accept, --audit-agents, --substitutions, "
             "--audit-run, --stale, --selftest")


if __name__ == "__main__":
    sys.exit(main())
