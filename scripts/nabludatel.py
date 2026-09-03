#!/usr/bin/env python3
"""nabludatel.py — дозорный за соседней сессией Фемиды. БЕЗ МОДЕЛИ, $0.

Читает session-jsonl соседней сессии и ее транскрипты роя инкрементально
(смещение отдельно на каждый источник, старое не разгребается) и машинно выделяет
только ошибки и нарушения
протокола: ненулевые коды возврата, блоки сторожа, stderr с сигнатурой отказа,
usage-ошибки argparse, api_error, и счетчики, по которым стоят пороги (чтения
дела основным потоком, веб-поиск, спавны агентов).

Каталог пишется НА ДИСК по ходу (--catalog, JSONL, дописыванием): одна запись на
вхождение ошибки — отметка времени, вид, сигнатура, текущий счетчик повторов,
обезличенный образец. Файл годен и человеку (по строке на ошибку), и следующему
прибору (валидный JSONL). Свод в конце — `--summary`: группирует по сигнатуре,
сортирует по числу повторов и по цене (весу вида).

Режимы: демон (по умолчанию, цикл), --once (один проход — проба или разбор уже
завершенной сессии), --summary (свод по накопленному каталогу).

Обезличивание — прибором scripts/pii_gate.py (канон ПД: фамилии из реестра дел,
пути cases/, ИНН/ОГРН/паспорт/адрес). Свою регулярку на ПД не держим — это был бы
второй механизм на ту же работу, он разошелся бы с pii_gate на первой правке
(корень 2 разбора). Импорт обязателен: не смогли обезличить — не пишем (fail-safe),
а не льем сырье в каталог. Два пути pii_gate НАМЕРЕННО не трогает — /tmp/<рендер> и
домашний каталог: у них нет фамильной логики, дублирования нет, и их сжатие ниже
закрывает ровно дыру, которую реестровый pii_gate закрыть не может, — каталог
рендера ЕЩЕ НЕ ЗАРЕГИСТРИРОВАННОГО дела на фазе интейка (фамилии в реестре нет,
а путь уже есть). Проверка — --selftest, не обещание.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from pii_gate import mask_text
except Exception as e:  # без обезличивателя дозор не стартует — не льем сырье
    print(f"nabludatel: pii_gate недоступен ({e}) — старт запрещен, сырье наружу не идет",
          file=sys.stderr)
    sys.exit(3)

HOME = os.path.expanduser("~")
PROJECT_DIR = os.path.join(HOME, ".claude", "projects",
                           re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
LIVE_WINDOW = 300          # сек: журнал рос за это окно → сессия жива (шире лока)
PREDEL = 180              # длина образца в каталоге

# pii_gate НЕ сжимает эти два пути (см. docstring) — cosmetic-свертка, не логика ПД
TMP_RE = re.compile(r"/(?:private/)?tmp/[^\s\"']+")
HOME_RE = re.compile(re.escape(HOME) + r"[^\s\"']*")

# что ловим в тексте результата/stderr, если код возврата не выставлен
OTKAZ = re.compile(r"БЛОК|ОТКАЗ|СТОП|Traceback|Error|error|failed|не найден|"
                   r"нет команды|unknown command|No such file|usage:|"
                   r"api_error|exit code [1-9]|код [1-9]", re.I)

# цена вида: сторож/код дороже stderr; порог — сигнал, не ошибка
VES = (("хук", 5), ("код", 4), ("ошибка", 3), ("отказ", 3), ("stderr", 2),
       ("порог", 1), ("слепота", 1))

PORAG = {"_чтение_дела": 25, "_веб_поиск": 50, "_спавн_агента": 15}
PORAG_IMYA = {"_чтение_дела": "чтений дела основным потоком",
              "_веб_поиск": "вызовов веб-поиска", "_спавн_агента": "спавнов агента"}

# Имена инструментов взяты ЗАМЕРОМ наземной правды на боевом прогоне 02.09.2026:
# MAIN 1722 строки — Bash 230, Read 35, Write 23, WebFetch 8, TaskOutput 5, Workflow 2;
# SWARM 12 файлов — Bash 74, Read 35, StructuredOutput 6. Это значения поля name
# блоков tool_use журнала сессии Claude Code. Переименование инструмента платформой
# ослепит дозор молча — поэтому набор правится ЗДЕСЬ, а имя вне всех групп попадает
# в свод отдельной строкой «слепота» (см. sobytiya).
INSTRUMENTY = {
    "_веб_поиск": {"WebSearch", "WebFetch"},
    "_спавн_агента": {"Agent", "Task", "Workflow", "TaskOutput", "StructuredOutput"},
}
# Известные, но сознательно не считаемые: замеренные на том же прогоне + штатные
# Claude Code. Имя не из INSTRUMENTY и не отсюда — не ноль, а строка в своде.
IZVESTNYE_PROCHIE = {"Bash", "Read", "Write", "Edit", "MultiEdit", "Grep", "Glob",
                     "LS", "Skill", "TodoWrite", "ExitPlanMode", "AskUserQuestion",
                     "BashOutput", "KillShell", "NotebookEdit", "SlashCommand"}


def cena(kind: str) -> int:
    for klyuch, v in VES:
        if klyuch in kind:
            return v
    return 1


def scrub(s, predel: int = PREDEL) -> str:
    """Обезличить и укоротить. pii_gate — на ПД; TMP/HOME — на пути, что он не трогает."""
    if not isinstance(s, str):
        s = json.dumps(s, ensure_ascii=False) if isinstance(s, (dict, list)) else str(s)
    s = " ".join(s.split())[:2000]          # ограничить контекст до маскировки
    masked, _ = mask_text(s)                # карту (в ней сырье ПД!) отбрасываем, не пишем
    s = masked if masked is not None else s
    s = TMP_RE.sub("/tmp/<рендер>", s)
    s = HOME_RE.sub("~", s)
    return " ".join(s.split())[:predel]


def sig(kind: str, txt: str) -> str:
    """Сигнатура для группировки: вид + обезличенное начало, цифры → # (варианты в одну)."""
    return kind + " :: " + re.sub(r"\d+", "#", txt)[:100]


def sobytiya(stroki):
    """Из сырых строк jsonl — ошибки (вид, обезличенный текст) + приросты счетчиков-порогов."""
    oshibki = []
    prirost = Counter()
    for raw in stroki:
        try:
            z = json.loads(raw)
        except ValueError:
            continue
        msg = z.get("message") or {}
        soder = msg.get("content")
        if isinstance(soder, list):
            for b in soder:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                imya = b.get("name", "?")
                vh = b.get("input") or {}
                if imya == "Bash":
                    cmd = str(vh.get("command", ""))
                    if re.search(r"\b(cat|head|sed|grep)\b", cmd) and "cases/" in cmd:
                        prirost["_чтение_дела"] += 1
                elif imya == "Read" and "cases/" in str(vh.get("file_path", "")):
                    prirost["_чтение_дела"] += 1
                for gruppa, imena in INSTRUMENTY.items():
                    if imya in imena:
                        prirost[gruppa] += 1
                if (imya not in IZVESTNYE_PROCHIE
                        and all(imya not in s for s in INSTRUMENTY.values())):
                    prirost[f"_слепота:{imya}"] += 1
        res = z.get("toolUseResult")
        if isinstance(res, dict):
            if res.get("is_error") or res.get("isError"):
                oshibki.append(("ошибка инструмента",
                                scrub(res.get("stdout") or res.get("content") or res)))
            stderr = str(res.get("stderr") or "")
            if stderr and OTKAZ.search(stderr):
                oshibki.append(("stderr", scrub(stderr)))
            code = res.get("exitCode")
            if isinstance(code, int) and code != 0:
                oshibki.append((f"ненулевой код {code}",
                                scrub(res.get("stderr") or res.get("stdout") or "")))
        elif isinstance(res, str) and OTKAZ.search(res):
            oshibki.append(("результат-отказ", scrub(res)))
        if isinstance(soder, str) and ("БЛОК" in soder or "hook error" in soder):
            oshibki.append(("блок хука", scrub(soder)))
    return oshibki, prirost


def zapis(catalog: str, rec: dict) -> None:
    with open(catalog, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def istochniki(session: str) -> list[str]:
    """Главный журнал + все agent-*.jsonl из одноименной папки сессии.

    Раскладка замерена на диске: обычные листья лежат в subagents/,
    workflow-листья — глубже в subagents/workflows/<run>/. journal.jsonl
    не транскрипт агента и может дублировать события, поэтому его не читаем.
    """
    main = os.path.abspath(session)
    base = Path(main[:-len(".jsonl")] if main.endswith(".jsonl") else main)
    subagents = base / "subagents"
    try:
        transcripts = set(subagents.rglob("agent-*.jsonl"))
        # meta дает учесть ожидавшийся, но не записанный транскрипт как пропуск.
        transcripts.update(Path(str(p)[:-len(".meta.json")] + ".jsonl")
                           for p in subagents.rglob("agent-*.meta.json"))
    except OSError as e:
        raise RuntimeError(f"каталог роя недоступен ({e})") from e
    if not base.is_dir() and Path(main).parent.parent != Path(HOME, ".claude", "projects"):
        # ponytail: скопированный/тестовый bundle держит один соседний корень роя;
        # если появится вторая реальная раскладка, путь апгрейда — явный --source-root.
        bundles = []
        try:
            children = list(Path(main).parent.iterdir())
            for child in children:
                if not child.is_dir():
                    continue
                files = list(child.rglob("agent-*.jsonl"))
                if not files and child.name == "roj":
                    generic = [p for p in child.rglob("*.jsonl") if p.name != "journal.jsonl"]
                    if len(generic) > 1:
                        raise RuntimeError("bundle roj/ неоднозначен: нет имен agent-* и файлов больше одного")
                    files = generic
                if files:
                    bundles.append(files)
        except OSError as e:
            raise RuntimeError(f"каталог bundle недоступен ({e})") from e
        if len(bundles) > 1:
            raise RuntimeError("раскладка роя неоднозначна: найдено несколько каталогов")
        transcripts = set(bundles[0]) if bundles else set()
    return [main, *sorted(str(p) for p in transcripts)]


def _prochitat(source: str, main: str, state: dict):
    """Дочитать один источник. Смещение хранится по пути источника."""
    offs = state.setdefault("offs", {})
    off = offs.get(source, state.get("off", 0) if source == main else 0)
    if not isinstance(off, int) or off < 0:
        off = 0
    try:
        razmer = os.path.getsize(source)
    except FileNotFoundError:
        return "исчез до чтения", [], Counter()
    except PermissionError:
        return "нет доступа", [], Counter()
    except OSError:
        return "ошибка чтения", [], Counter()
    if razmer < off:
        print(f"дозор: источник короче смещения ({razmer} Б < {off} Б) — "
              f"пересоздан, смещение сброшено, читаю сначала",
              file=sys.stderr, flush=True)
        off = 0
    if razmer == off:
        offs[source] = off
        if source == main:
            state["off"] = off          # старый state остается читаемым
        return "новых данных нет", [], Counter()
    try:
        with open(source, encoding="utf-8", errors="replace") as f:
            f.seek(off)
            novye = f.read().splitlines()
            novy_off = f.tell()
    except PermissionError:
        return "нет доступа", [], Counter()
    except OSError:
        return "ошибка чтения", [], Counter()
    offs[source] = novy_off
    if source == main:
        state["off"] = novy_off
    oshibki, prirost = sobytiya(novye)
    return "прочитан", oshibki, prirost


def obrabotat(session: str, state: dict, catalog: str) -> int:
    """Один проход по главному журналу и всем листьям роя.

    Возврат: -1 главный журнал исчез · -2 все источники у конца · >=0 новых записей.
    Ошибки маскируются тем же scrub(), а пороги применяются один раз к сумме приростов.
    """
    main = os.path.abspath(session)
    sources = istochniki(main)
    subroot = os.path.abspath(main[:-len(".jsonl")] + "/subagents")
    fallback = (not os.path.isdir(main[:-len(".jsonl")])
                and Path(main).parent.parent != Path(HOME, ".claude", "projects"))
    # Уже замеченный лист не исчезает из охвата молча, даже если файл удалили между тактами.
    roj_known = 0
    if fallback:
        for known in state.get("offs", {}):
            try:
                relative = Path(os.path.abspath(known)).relative_to(Path(main).parent)
            except ValueError:
                continue
            roj_known += bool(relative.parts and relative.parts[0] == "roj")
    for known in state.get("offs", {}):
        known = os.path.abspath(known)
        try:
            canonical = os.path.commonpath((subroot, known)) == subroot
            relative = Path(known).relative_to(Path(main).parent)
        except ValueError:
            canonical = False
            relative = Path()
        copied = (fallback and relative.parts
                  and (Path(known).match("agent-*.jsonl")
                       or (relative.parts[0] == "roj" and roj_known == 1)))
        if ((canonical and Path(known).match("agent-*.jsonl")) or copied) and known not in sources:
            sources.append(known)
    sources[1:] = sorted(sources[1:])
    prirost_vsego = Counter()
    prochitano = bez_novogo = 0
    prichiny = Counter()
    byli_novye = False
    metka = time.strftime("%Y-%m-%d %H:%M:%S")
    n_new = 0
    schet = state["sig_schet"]
    for source in sources:
        status, oshibki, prirost = _prochitat(source, main, state)
        if source == main and status not in ("прочитан", "новых данных нет"):
            prichiny[status] += 1
            if len(sources) > 1:
                prichiny["главный журнал недоступен: остальные не читались"] += len(sources) - 1
            state["ohvat"] = {"vsego": len(sources), "prochitano": 0,
                               "propushcheno": len(sources), "bez_novogo": 0,
                               "prichiny": dict(prichiny)}
            return -1
        if status in ("прочитан", "новых данных нет"):
            prochitano += 1
            bez_novogo += status == "новых данных нет"
            byli_novye |= status == "прочитан"
        else:
            prichiny[status] += 1
        prirost_vsego.update(prirost)
        for kind, txt in oshibki:
            s = sig(kind, txt)
            schet[s] = schet.get(s, 0) + 1
            zapis(catalog, {"ts": metka, "vid": kind, "sig": s,
                            "povtor": schet[s], "cena": cena(kind), "obrazec": txt})
            n_new += 1
    state["ohvat"] = {"vsego": len(sources), "prochitano": prochitano,
                       "propushcheno": sum(prichiny.values()), "bez_novogo": bez_novogo,
                       "prichiny": dict(prichiny)}
    # слепота: имя инструмента вне всех групп — НЕ порог и НЕ ноль, а строка в своде.
    # Пишется один раз на имя (по state), иначе каталог утонет в повторах.
    slepota = state.setdefault("slepota", {})
    for k in [k for k in list(prirost_vsego) if k.startswith("_слепота:")]:
        v = prirost_vsego.pop(k)
        imya = k.split(":", 1)[1]
        bylo = slepota.get(imya, 0)
        slepota[imya] = bylo + v
        if not bylo:
            kind = "слепота"
            txt = f"инструмент вне групп дозора: {imya} (вхождений: {slepota[imya]})"
            zapis(catalog, {"ts": metka, "vid": kind, "sig": sig(kind, imya),
                            "povtor": 1, "cena": cena(kind), "obrazec": txt})
            n_new += 1
    # пороги: один раз по СУММЕ всех источников за такт
    for k, v in prirost_vsego.items():
        state["pragi"][k] = state["pragi"].get(k, 0) + v
    for k, shag in PORAG.items():
        total = state["pragi"].get(k, 0)
        stupen = total // shag
        if stupen > state["skazano"].get(k, 0):
            state["skazano"][k] = stupen
            kind = "порог"
            txt = f"{PORAG_IMYA[k]} за прогон: {total}"
            zapis(catalog, {"ts": metka, "vid": kind, "sig": sig(kind, PORAG_IMYA[k]),
                            "povtor": stupen, "cena": cena(kind), "obrazec": txt})
            n_new += 1
    return n_new if byli_novye or prichiny else -2


def load_state(path: str) -> dict:
    d = {"off": 0, "offs": {}, "sig_schet": {}, "pragi": {}, "skazano": {}}
    if os.path.exists(path):
        try:
            loaded = json.load(open(path))
            if isinstance(loaded, dict):
                d.update(loaded)
        except (ValueError, OSError):
            pass
    if not isinstance(d.get("off"), int) or d["off"] < 0:
        d["off"] = 0
    for key in ("offs", "sig_schet", "pragi", "skazano"):
        if not isinstance(d.get(key), dict):
            d[key] = {}
    return d


def save_state(path: str, state: dict) -> None:
    json.dump(state, open(path, "w"), ensure_ascii=False)


def default_state_path(session: str) -> str:
    """Состояние per-сессия, выведенное из пути журнала: два дозорных за разными
    сессиями не затрут смещение друг другу. Basename журнала — UUID сессии, уникален."""
    return f"/tmp/nabludatel-{Path(session).stem or 'sess'}.state"


def find_session(exclude: str | None) -> str | None:
    """Живая соседняя сессия = самый свежий journal, растущий в окне LIVE_WINDOW.
    По РОСТУ ЖУРНАЛА (mtime), не по локу и не по процессам: на фазе интейка лока
    еще нет, прибора-процесса может не быть вовсе, а журнал уже пишется."""
    now = time.time()
    excl = os.path.realpath(exclude) if exclude else None
    live = []
    for p in Path(PROJECT_DIR).glob("*.jsonl"):
        sp = str(p)
        if excl and os.path.realpath(sp) == excl:
            continue
        try:
            m = p.stat().st_mtime
        except OSError:                 # живой journal мог исчезнуть между glob и stat
            continue
        if now - m <= LIVE_WINDOW:
            live.append((m, sp))
    return max(live)[1] if live else None


def ohvat_text(ohvat: dict | None) -> str:
    if not ohvat:
        return "охват не установлен: передайте SESSION или --state"
    text = (f"охват: прочитано {ohvat.get('prochitano', 0)}/"
            f"{ohvat.get('vsego', 0)} источников; "
            f"пропущено {ohvat.get('propushcheno', 0)}")
    reasons = ohvat.get("prichiny") or {}
    if reasons:
        text += " (" + ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())) + ")"
    if ohvat.get("bez_novogo"):
        text += f"; без новых данных: {ohvat['bez_novogo']}"
    return text


def cmd_report(catalog: str, ohvat: dict | None = None) -> int:
    if not os.path.exists(catalog):
        print(f"nabludatel: каталога нет ({catalog}) — нечего сводить", file=sys.stderr)
        return 1
    grupp = {}
    for line in open(catalog, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        g = grupp.setdefault(r["sig"], {"vid": r["vid"], "cena": r.get("cena", 1),
                                        "n": 0, "obrazec": r.get("obrazec", "")})
        g["n"] += 1
        g["obrazec"] = r.get("obrazec", g["obrazec"])
    print(f"# {ohvat_text(ohvat)}\n")
    if not grupp:
        print("nabludatel: каталог пуст — ошибок не зафиксировано")
        return 0
    poryadok = sorted(grupp.values(), key=lambda g: (g["n"], g["cena"]), reverse=True)
    print(f"# Свод дозорного — что надо исправить ({len(poryadok)} сигнатур, "
          f"{sum(g['n'] for g in poryadok)} вхождений)\n")
    for g in poryadok:
        print(f"[{g['n']}×  цена {g['cena']}]  {g['vid']}\n    {g['obrazec']}\n")
    return 0


def cmd_watch(session: str, state_path: str, catalog: str, once: bool, interval: int) -> int:
    state = load_state(state_path)
    # Хвост с текущего момента — только демону (не разгребать старое у ЖИВОЙ сессии).
    # --once разбирает завершенную сессию целиком. На старте демона
    # ставим к концу все уже существующие источники; новые листья потом читаются с нуля.
    if not once:
        main = os.path.abspath(session)
        try:
            sources = istochniki(main)
        except RuntimeError as e:
            print(f"nabludatel: {e}; старт запрещен", file=sys.stderr)
            return 2
        offs = state.setdefault("offs", {})
        for source in sources:
            if source in offs:
                continue
            try:
                offs[source] = (state["off"] if source == main and state["off"]
                                else os.path.getsize(source))
            except OSError:
                continue
        state["off"] = offs.get(main, state["off"])
        save_state(state_path, state)
        print(f"дозор встал на сессию, хвост с {state['off'] // 1024} КБ; "
              f"источников под наблюдением: {len(sources)}", flush=True)
    first = True
    while True:
        previous_ohvat = state.get("ohvat")
        try:
            n = obrabotat(session, state, catalog)
        except RuntimeError as e:
            print(f"nabludatel: {e}; наблюдение остановлено", file=sys.stderr)
            return 2
        current_ohvat = state.get("ohvat")
        if once or first or current_ohvat != previous_ohvat:
            print(f"дозор: {ohvat_text(current_ohvat)}", flush=True)
        first = False
        if n == -1:
            print("дозор: журнал сессии исчез, наблюдение окончено", flush=True)
            save_state(state_path, state)
            return 0
        save_state(state_path, state)
        if n == -2:
            # смещение у конца: демон молчит (тихий такт), но --ВЫХОД тихим нулем запрещен
            if once:
                print(f"дозор: смещение {state['off']} Б = размер журнала — "
                      f"непрочитанного нет, каталог не пополнен", file=sys.stderr)
            n = 0
        elif n:
            print(f"[{time.strftime('%H:%M:%S')}] дозор: +{n} записей в каталог", flush=True)
        if once:
            return 0
        time.sleep(interval)


def selftest() -> int:
    import tempfile
    d = tempfile.mkdtemp()
    session = os.path.join(d, "s.jsonl")
    catalog = os.path.join(d, "cat.jsonl")
    state_path = os.path.join(d, "st.json")
    familia = "Кузнецова"      # вымышленная фамилия для пробы обезличивания
    rows = [
        {"toolUseResult": {"exitCode": 1, "stderr":
            f"БЛОК: запись в cases/{familia.lower()}-as/razvod-2026 запрещена. "
            f"Истец {familia} Мария Петровна, ИНН 771234567890. "
            f"Рендер /tmp/{familia.lower()}-as/page_001 в {HOME}/Проекты/themis."}},
        {"message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {}}]},
         "toolUseResult": {"exitCode": 1, "stderr": "usage: tool [-h]"}},
        {"message": {"content": [{"type": "tool_use", "name": n, "input": {}}
                                 for n in ("WebFetch", "Workflow", "TaskOutput",
                                           "StructuredOutput", "MysteryTool")]}},
    ]
    with open(session, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Боевая раскладка: обычный лист + workflow-лист в одноименной папке сессии.
    sub = Path(d, "s", "subagents")
    workflow = sub / "workflows" / "wf_test"
    workflow.mkdir(parents=True)
    direct = sub / "agent-direct.jsonl"
    wf_leaf = workflow / "agent-workflow.jsonl"

    def row_with_tools(n: int, error: str) -> str:
        tools = [{"type": "tool_use", "name": "WebSearch", "input": {}} for _ in range(n)]
        return json.dumps({"message": {"content": tools},
                           "toolUseResult": {"exitCode": 9, "stderr": error}},
                          ensure_ascii=False) + "\n"

    direct.write_text(row_with_tools(24, "ОТКАЗ ЛИСТА РОЯ"), encoding="utf-8")
    wf_leaf.write_text(row_with_tools(25, "ОТКАЗ WORKFLOW-ЛИСТА"), encoding="utf-8")
    state = load_state(state_path)
    n = obrabotat(session, state, catalog)
    assert n >= 2, f"ожидались записи ошибок, получено {n}"
    sources = istochniki(session)
    assert len(sources) == 3, f"ожидались главный источник и два листа, получено {sources}"
    assert set(state["offs"]) == set(sources), "смещение не запомнено отдельно на каждый источник"
    assert state["pragi"]["_веб_поиск"] == 51, "порог не сложил главный поток и рой (WebSearch + WebFetch)"
    assert state["pragi"]["_спавн_агента"] == 3, "Workflow/TaskOutput/StructuredOutput не посчитаны спавном"
    assert state["ohvat"] == {"vsego": 3, "prochitano": 3, "propushcheno": 0,
                               "bez_novogo": 0, "prichiny": {}}, state["ohvat"]
    dump = open(catalog, encoding="utf-8").read()
    for leak in (familia, familia.lower(), "cases/", "771234567890",
                 f"/tmp/{familia.lower()}", HOME):
        assert leak not in dump, f"УТЕЧКА в каталог: {leak!r}\n{dump}"
    recs = [json.loads(line) for line in dump.splitlines()]
    assert sum(r["vid"] == "порог" for r in recs) == 1, "порог по сумме должен сработать один раз"
    slepota = [r for r in recs if r["vid"] == "слепота"]
    assert len(slepota) == 1 and "MysteryTool" in slepota[0]["obrazec"], \
        f"неизвестное имя инструмента промолчало: {slepota}"
    before = os.path.getsize(catalog)
    assert obrabotat(session, state, catalog) == -2, "повторный проход не дошел до конца всех источников"
    assert os.path.getsize(catalog) == before, "повторный проход задублировал рой"
    assert state["ohvat"]["bez_novogo"] == 3
    wf_leaf.unlink()
    assert obrabotat(session, state, catalog) == 0, "исчезнувший лист не попал в охват"
    assert state["ohvat"]["propushcheno"] == 1, state["ohvat"]
    assert "исчез до чтения: 1" in ohvat_text(state["ohvat"])
    assert os.path.getsize(catalog) == before, "пропуск источника изменил каталог ошибок"
    wf_leaf.write_text(row_with_tools(25, "ОТКАЗ WORKFLOW-ЛИСТА"), encoding="utf-8")
    assert obrabotat(session, state, catalog) == -2, "вернувшийся лист с тем же смещением перечитан"
    assert cmd_report(catalog, state["ohvat"]) == 0

    # Упрощенный bundle из D04: один roj/, служебный JSONL не подмешивается,
    # исчезнувший лист остается в охвате с причиной.
    with tempfile.TemporaryDirectory() as bd:
        bmain = Path(bd, "copy.jsonl")
        bleaf = Path(bd, "roj", "agent-leaf.jsonl")
        unrelated = Path(bd, "roj", "catalog.jsonl")
        bleaf.parent.mkdir()
        bmain.write_text("{}\n", encoding="utf-8")
        bleaf.write_text(row_with_tools(1, "ОТКАЗ BUNDLE-ЛИСТА"), encoding="utf-8")
        unrelated.write_text("{}\n", encoding="utf-8")
        bsources = istochniki(str(bmain))
        assert bsources == [str(bmain), str(bleaf)], f"в bundle подмешан посторонний JSONL: {bsources}"
        bstate = load_state(str(Path(bd, "state.json")))
        bcat = str(Path(bd, "out.jsonl"))
        assert obrabotat(str(bmain), bstate, bcat) >= 2
        bleaf.unlink()
        assert obrabotat(str(bmain), bstate, bcat) == 0
        assert bstate["ohvat"]["propushcheno"] == 1, bstate["ohvat"]
        assert "исчез до чтения: 1" in ohvat_text(bstate["ohvat"])

    import contextlib
    import io
    # точка 1: состояние по умолчанию выведено ИЗ ПУТИ сессии, не общая константа
    assert default_state_path("/a/AAA.jsonl") != default_state_path("/a/BBB.jsonl"), \
        "state по умолчанию общий на все сессии — два дозорных затрут смещение"

    # точка 3: журнал КОРОЧЕ смещения → сброс + доклад (смена сессии), не «нового нет»
    st3 = load_state(state_path)
    st3["off"] = os.path.getsize(session) + 10 ** 6      # смещение за концом журнала
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        r3 = obrabotat(session, st3, catalog)
    assert "пересоздан" in buf.getvalue(), f"смена сессии не объявлена: {buf.getvalue()!r}"
    assert r3 >= 2, f"после сброса журнал должен перечитаться целиком, получено {r3}"

    # точка 2: смещение у конца → --once не выходит тихим нулем, говорит в stderr
    save_state(state_path, {"off": os.path.getsize(session),
                            "offs": {source: os.path.getsize(source) for source in sources},
                            "sig_schet": {}, "pragi": {}, "skazano": {}})
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = cmd_watch(session, state_path, catalog, once=True, interval=0)
    assert rc == 0
    assert "непрочитанного нет" in buf.getvalue(), \
        f"caught-up выход промолчал (тихий ноль): {buf.getvalue()!r}"

    print("selftest: OK — рой прочитан, смещения разделены, порог суммарный, "
          "ПД/пути не ушли, тихий ноль закрыт")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="дозорный за соседней сессией Фемиды (без модели)")
    ap.add_argument("session", nargs="?", help="путь к session-jsonl; без него — авто по росту журнала")
    ap.add_argument("--catalog", default="/tmp/nabludatel-catalog.jsonl", help="каталог ошибок (JSONL, дописывается)")
    ap.add_argument("--state", default=None, help="файл смещения/счетчиков; по умолчанию выводится из пути сессии")
    ap.add_argument("--interval", type=int, default=60, help="такт демона, сек")
    ap.add_argument("--once", action="store_true", help="один проход и выход")
    ap.add_argument("--summary", action="store_true", help="свод по накопленному каталогу")
    ap.add_argument("--self", dest="self_path", help="свой journal — исключить из авто-поиска")
    ap.add_argument("--selftest", action="store_true", help="проба обезличивания и разбора")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.summary:
        report_state_path = a.state or (default_state_path(a.session) if a.session else None)
        report_state = load_state(report_state_path) if report_state_path else {}
        return cmd_report(a.catalog, report_state.get("ohvat"))
    session = a.session or find_session(a.self_path)
    if not session:
        print("nabludatel: живой соседней сессии не найдено (нет растущего журнала в окне)",
              file=sys.stderr)
        return 1
    state_path = a.state or default_state_path(session)
    return cmd_watch(session, state_path, a.catalog, a.once, a.interval)


if __name__ == "__main__":
    sys.exit(main())
