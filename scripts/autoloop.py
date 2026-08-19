#!/usr/bin/env python3
"""autoloop.py — автономный цикл работ роем. Этап 1.5 плана FINAL-PLAN-2026-08-18.

Механизм взят из роя Олимпуза (`~/Проекты/olympuz`, Apache-2.0, движок на TypeScript)
как МЕТОД, а не как код: п. 5 ст. 1259 ГК РФ — авторские права не распространяются на
идеи, методы и способы решения технических задач. Здесь свой Python в стиле проекта,
со своими приборами и гейтами. Инварианты ниже оплачены ошибками Олимпуза — свои
не выдумывать.

ИНВАРИАНТЫ (нарушен любой — цикл не стартует, код 2):
  · generator ≠ verifier — писавший код не выносит вердикт; пасс держит КОД ВОЗВРАТА
    прибора, мнение модели вердиктом не считается;
  · четыре LoopGuard или старта нет — потолок итераций, бюджет токенов, потолок
    времени, детект застревания по отпечатку вердикта гейта;
  · изоляция — параллельные роли работают в отдельных рабочих копиях (git worktree);
  · дайджест вверх, без бокового обмена — роль видит задание и вердикт гейта,
    но НИКОГДА вывод соседней роли;
  · человеческий гейт только у координатора, ролям недоступен.

РЕЛЬСЫ (жёстко в коде, не в прозе задания):
  · автономно исполняются только этапы 1, 2 и 5 — они покрыты приборами и не трогают
    данные дел. Миграция (этап 3) необратимо трогает 77 дел и требует подтверждения
    владельца перед прогоном, даже при зелёном бэкапе;
  · `cases/` замораживается отпечатком до старта и сверяется каждую итерацию;
  · окружение замораживается так же: установка пакетов автономно запрещена ВСЕГДА —
    цикл встаёт и ждёт владельца;
  · итерация не закрывается без зелёного `loop_gate.py`.

Журнал — `.autoloop/journal.jsonl` (append-only), утренний отчёт — `.autoloop/REPORT.md`.

Выход: 0 — цель достигнута; 1 — остановлен сторожем (причина в отчёте);
       2 — отказ старта (конфигурация нарушает инвариант).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, ".autoloop")

# Этапы, покрытые приборами и не трогающие данные дел. Всё прочее — руками.
# Этап 9 «замкнуть контур» легализован 19.08.2026 ТОЛЬКО в приборной части:
# правки scripts/, приёмок, гейтов и документации. Владельческая часть этапа
# (вывоз 84 файлов кода из-под cases/, 28 practice_context.md, 31 лок Word)
# необратимо трогает данные дел и живёт в knowledge/OWNER-TODO.md — цикл её
# не исполняет, заморозка cases/ отпечатком это дополнительно сторожит.
AUTONOMOUS_STAGES = {"1", "2", "5", "9"}
REQUIRED_GUARDS = ("max_iterations", "max_money", "wall_clock_seconds", "no_progress_limit")
GENERATOR_KINDS = {"generator", "builder"}
REVIEWER_KINDS = {"reviewer", "critic"}
# Установка пакетов автономно запрещена всегда — ловим намерение до старта.
INSTALL_MARKERS = ("pip install", "pip3 install", "npm install", "npm i ", "yarn add",
                   "brew install", "uv pip install", "apt install", "apt-get install",
                   "cargo install", "gem install", "poetry add", "pipx install")


def run(argv, cwd=ROOT, timeout=1800, env=None):
    """Внешний вызов только argv-массивом: строка-команда открывает инъекцию через аргумент.

    stdin закрыт наглухо: человеческий гейт живёт только у координатора. Роль,
    унаследовавшая терминал, либо спрашивает владельца в обход координатора, либо
    молча виснет до таймаута — ночью это значит потерянный прогон.
    """
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout, env=env)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"таймаут {timeout} с"
    except OSError as e:
        return 127, "", str(e)


# ── Заморозка: что автономному циклу трогать нельзя ──────────────────────────

def tree_fingerprint(path):
    """Отпечаток дерева по (путь, размер, mtime). Читать 21 ГБ содержимого не нужно:
    задача — заметить ЛЮБОЕ касание, а не сверить байты (для этого есть intake_backup)."""
    h = hashlib.sha256()
    if not os.path.isdir(path):
        return "нет-каталога"
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            h.update(os.path.relpath(full, path).encode("utf-8"))
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def env_fingerprint():
    """Отпечаток установленных пакетов. Меняется — значит кто-то поставил пакет."""
    h = hashlib.sha256()
    for d in sorted(set(p for p in sys.path if p.endswith(("site-packages", "dist-packages")))):
        try:
            for name in sorted(os.listdir(d)):
                h.update(name.encode("utf-8"))
        except OSError:
            continue
    return h.hexdigest()[:16]


def spent_money(root=ROOT):
    """Реальный расход с диска прибором token_ledger, а не самоотчётом модели."""
    code, out, _ = run([sys.executable, os.path.join(root, "scripts", "token_ledger.py"),
                        "--json"], cwd=root, timeout=300)
    if code != 0:
        return None
    try:
        return float(json.loads(out).get("money") or 0.0)
    except (ValueError, TypeError, AttributeError):
        return None


# ── Приёмка конфигурации: fail-closed ────────────────────────────────────────

def validate(cfg):
    """Отказы старта. Пустой список — можно запускать; иначе цикл НЕ стартует."""
    bad = []
    if not isinstance(cfg, dict):
        return ["конфигурация не объект"]

    stage = str(cfg.get("stage", "")).strip()
    if stage not in AUTONOMOUS_STAGES:
        bad.append(f"этап {stage or '(не указан)'} автономно не исполняется; "
                   f"разрешены только {sorted(AUTONOMOUS_STAGES)}. Миграция трогает 77 дел "
                   f"необратимо и требует подтверждения владельца перед прогоном")
    if not str(cfg.get("task", "")).strip():
        bad.append("нет поля task — цикл без сформулированной цели не имеет условия остановки")

    guards = cfg.get("guards")
    if not isinstance(guards, dict):
        bad.append("нет блока guards — четыре сторожа обязательны")
        guards = {}
    for g in REQUIRED_GUARDS:
        v = guards.get(g)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            bad.append(f"LoopGuard `{g}` отсутствует или не положителен — "
                       f"каждый закрывает свой режим незавершения, цикл без него не стартует")
    if guards.get("stop_when") != "gate_green":
        bad.append("guards.stop_when обязан быть `gate_green`: условие остановки — "
                   "код возврата прибора, а не решение модели")

    roles = cfg.get("roles")
    if not isinstance(roles, list) or not roles:
        bad.append("нет ролей")
        roles = []
    names = set()
    kinds = set()
    for i, r in enumerate(roles):
        if not isinstance(r, dict):
            bad.append(f"роль #{i} не объект")
            continue
        nm = str(r.get("name", "")).strip()
        if not nm:
            bad.append(f"роль #{i} без имени")
        if nm in names:
            bad.append(f"роль `{nm}` объявлена дважды")
        names.add(nm)
        kind = str(r.get("kind", "")).strip()
        if kind not in GENERATOR_KINDS | REVIEWER_KINDS:
            bad.append(f"роль `{nm}`: неизвестный kind `{kind}`")
        kinds.add(kind)
        if kind in GENERATOR_KINDS and kind in REVIEWER_KINDS:
            bad.append(f"роль `{nm}` объявлена и автором, и проверяющим")
        if r.get("verdict") or r.get("verdict_source"):
            bad.append(f"роль `{nm}` претендует на вынесение вердикта: generator ≠ verifier, "
                       f"пасс держит код возврата гейта, мнение модели вердиктом не считается")
        argv = r.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            bad.append(f"роль `{nm}`: argv обязан быть непустым массивом строк "
                       f"(строка-команда открывает инъекцию через аргумент)")
            argv = []
        joined = " ".join(argv).lower()
        for marker in INSTALL_MARKERS:
            if marker in joined:
                bad.append(f"роль `{nm}` ставит пакеты (`{marker.strip()}`) — "
                           f"установка автономно запрещена всегда")
    if not (kinds & GENERATOR_KINDS):
        bad.append("нет ни одной роли-автора — некому делать работу")
    if not (kinds & REVIEWER_KINDS):
        bad.append("нет ни одной роли-рецензента: работу без независимого взгляда "
                   "цикл гонять не будет")

    par = [r for r in roles if isinstance(r, dict) and r.get("parallel")]
    if len(par) > 1 and not cfg.get("isolation_worktree", True):
        bad.append("параллельные роли без изоляции: каждая работает в своей рабочей копии, "
                   "иначе они правят одно дерево одновременно")

    gate = cfg.get("gate")
    if not isinstance(gate, list) or not gate:
        bad.append("нет argv гейта — вердикт выносить нечем")
    return bad


# ── Изоляция ролей ───────────────────────────────────────────────────────────

def worktree_add(name, root=ROOT):
    path = os.path.join(STATE_DIR, "worktrees", name)
    if os.path.isdir(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    code, _, err = run(["git", "worktree", "add", "--detach", path, "HEAD"], cwd=root, timeout=300)
    if code != 0:
        raise RuntimeError(f"рабочая копия для роли `{name}` не создана: {err.strip()[:200]}")
    return path


def worktree_remove(name, root=ROOT):
    path = os.path.join(STATE_DIR, "worktrees", name)
    run(["git", "worktree", "remove", "--force", path], cwd=root, timeout=300)
    shutil.rmtree(path, ignore_errors=True)


# ── Задание роли: дайджест вверх, без бокового обмена ────────────────────────

def role_brief(cfg, role, iteration, gate_fails):
    """Роль видит цель, свою работу и вердикт гейта. Вывод соседней роли — НИКОГДА.

    Боковой обмен превращает рой в испорченный телефон: ошибка одной роли становится
    входными данными другой и размножается вместо того, чтобы быть пойманной.
    """
    lines = [
        f"ЗАДАЧА: {cfg['task']}",
        f"ЭТАП: {cfg['stage']} · ИТЕРАЦИЯ: {iteration} · РОЛЬ: {role['name']} ({role['kind']})",
        "",
        "ВЕРДИКТ ГЕЙТА ПРОШЛОЙ ИТЕРАЦИИ:",
    ]
    lines += [f"  · {i}: {t}" for i, t in gate_fails] or ["  зелено"]
    lines += [
        "",
        "ГРАНИЦЫ (нарушение = остановка цикла, не предупреждение):",
        "  · cases/ не трогать: данные дел заморожены отпечатком и сверяются каждую итерацию;",
        "  · пакеты не ставить: уперся в необходимость пакета — остановись и напиши это;",
        "  · вердикт о своей работе не выносить: пасс держит код возврата loop_gate.py;",
        "  · итог работы — правки в файлах и одна короткая выжимка в конце, без пересказа.",
    ]
    return "\n".join(lines)


# ── Журнал и отчёт ───────────────────────────────────────────────────────────

def journal(entry, root=ROOT):
    os.makedirs(os.path.join(root, ".autoloop"), exist_ok=True)
    with open(os.path.join(root, ".autoloop", "journal.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_report(cfg, runlog, stop_reason, root=ROOT):
    """Утренний отчёт: что сделано, чем доказано, где остановился и почему."""
    os.makedirs(os.path.join(root, ".autoloop"), exist_ok=True)
    path = os.path.join(root, ".autoloop", "REPORT.md")
    green = [r for r in runlog if r["green"]]
    lines = [
        f"# Ночной прогон — {cfg['task']}",
        "",
        f"Этап {cfg['stage']} · итераций {len(runlog)} · зелёных {len(green)}",
        f"Остановлен: **{stop_reason}**",
        "",
        "## Итерации",
        "",
        "| № | гейт | отпечаток | провалов | роли | ушло, с |",
        "|---|---|---|---|---|---|",
    ]
    for r in runlog:
        lines.append(f"| {r['iteration']} | {'зелёный' if r['green'] else 'красный'} | "
                     f"`{r['fingerprint']}` | {len(r['fails'])} | "
                     f"{', '.join(r['roles'])} | {r['seconds']} |")
    last = runlog[-1] if runlog else None
    if last and last["fails"]:
        lines += ["", "## Чем красен последний гейт", ""]
        lines += [f"- `{i}` — {t}" for i, t in last["fails"]]
    lines += ["", "## Чем доказано", "",
              "Каждая зелёная итерация закрыта кодом возврата `scripts/loop_gate.py`: "
              "компиляция, `--selftest` затронутых приборов, smoke на `ivanov-ivan`, ПД-сторож. "
              "Вердикт модели пассом не считается.", "",
              "Журнал прогона: `.autoloop/journal.jsonl` — по нему отчёт воспроизводится целиком.",
              ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def replay(root=ROOT):
    """Пересобрать отчёт из журнала — прогон мог оборваться вместе с процессом.

    Отчёт обязан быть воспроизводим с диска: если он существует только в памяти
    упавшего процесса, утром доказывать нечем.
    """
    path = os.path.join(root, ".autoloop", "journal.jsonl")
    if not os.path.isfile(path):
        return None, "журнала нет"
    cfg, runlog, stop = None, [], "журнал оборван — прогон не дошёл до остановки"
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ev = e.get("event")
            if ev == "start":
                cfg = {"task": e["task"], "stage": e["stage"], "guards": e["guards"]}
                runlog, stop = [], "журнал оборван — прогон не дошёл до остановки"
            elif ev == "gate":
                runlog.append({"iteration": e["iteration"], "green": e["green"],
                               "fingerprint": e["fingerprint"],
                               "fails": [tuple(x) for x in e["fails"]],
                               "roles": e["roles"], "seconds": e["seconds"]})
            elif ev == "stop":
                stop = e["reason"]
    if cfg is None:
        return None, "в журнале нет ни одного старта"
    return write_report(cfg, runlog, stop, root), stop


# ── Цикл ─────────────────────────────────────────────────────────────────────

def loop(cfg, root=ROOT, dry=False):
    guards = cfg["guards"]
    gate_argv = [x.replace("{python}", sys.executable) for x in cfg["gate"]]
    cases_fp = tree_fingerprint(os.path.join(root, "cases"))
    env_fp = env_fingerprint()
    money_start = spent_money(root) or 0.0
    started = time.time()
    runlog, fails, last_fp, stale = [], [], None, 0
    stop = "потолок итераций"

    journal({"event": "start", "task": cfg["task"], "stage": cfg["stage"],
             "guards": guards, "cases_fp": cases_fp, "env_fp": env_fp,
             "money_start": money_start}, root)

    for it in range(1, int(guards["max_iterations"]) + 1):
        t0 = time.time()
        role_names = []
        for role in cfg["roles"]:
            brief = role_brief(cfg, role, it, fails)
            argv = [x.replace("{brief}", brief).replace("{python}", sys.executable)
                    for x in role["argv"]]
            cwd = root
            if role.get("parallel") and cfg.get("isolation_worktree", True) and not dry:
                cwd = worktree_add(role["name"], root)
            if dry:
                journal({"event": "role_dry", "iteration": it, "role": role["name"],
                         "argv": argv[:3]}, root)
            else:
                code, out, err = run(argv, cwd=cwd, timeout=int(guards["wall_clock_seconds"]))
                journal({"event": "role", "iteration": it, "role": role["name"],
                         "kind": role["kind"], "code": code, "cwd": cwd,
                         "tail": (out or err)[-800:]}, root)
            role_names.append(role["name"])

        code, out, _ = run(gate_argv, cwd=root, timeout=3600)
        try:
            verdict = json.loads(out)
            fails = [(f["id"], f["text"]) for f in verdict.get("fails", [])]
            fp = verdict.get("fingerprint", "")
            green = bool(verdict.get("green"))
        except (ValueError, TypeError, KeyError):
            # fail-closed: гейт, чей вердикт не разобран, считается КРАСНЫМ.
            fails = [("gate:unparsed", f"вердикт гейта не разобран (код {code})")]
            fp, green = "неразобран", False

        rec = {"iteration": it, "green": green, "fingerprint": fp, "fails": fails,
               "roles": role_names, "seconds": round(time.time() - t0, 1)}
        runlog.append(rec)
        journal({"event": "gate", **rec}, root)

        if green:
            stop = "цель достигнута — гейт зелёный"
            break

        # ── сторожа ──
        now_cases = tree_fingerprint(os.path.join(root, "cases"))
        if now_cases != cases_fp:
            stop = "ТРОНУТЫ ДАННЫЕ ДЕЛ: отпечаток cases/ изменился — автономно это запрещено"
            break
        if env_fingerprint() != env_fp:
            stop = "УСТАНОВЛЕН ПАКЕТ: отпечаток окружения изменился — установка автономно запрещена"
            break
        spent = spent_money(root)
        if spent is not None and (spent - money_start) > float(guards["max_money"]):
            stop = f"бюджет исчерпан: потрачено ${spent - money_start:.2f} при потолке ${guards['max_money']:.2f}"
            break
        if time.time() - started > float(guards["wall_clock_seconds"]):
            stop = f"потолок времени: {guards['wall_clock_seconds']} с"
            break
        stale = stale + 1 if fp == last_fp else 0
        last_fp = fp
        if stale >= int(guards["no_progress_limit"]):
            stop = (f"застревание: вердикт гейта не сдвинулся {stale + 1} итерации подряд "
                    f"(отпечаток `{fp}`) — код меняется, работа стоит")
            break

    for role in cfg["roles"]:
        if role.get("parallel") and not dry:
            worktree_remove(role["name"], root)
    journal({"event": "stop", "reason": stop, "iterations": len(runlog)}, root)
    report = write_report(cfg, runlog, stop, root)
    return runlog, stop, report


def selftest():
    """Синтетика: отказ старта по каждому инварианту, детект спина, заморозка данных."""
    import tempfile

    base = {"task": "проба", "stage": "5",
            "guards": {"max_iterations": 3, "max_money": 1.0, "wall_clock_seconds": 60,
                       "no_progress_limit": 2, "stop_when": "gate_green"},
            "roles": [{"name": "a", "kind": "generator", "argv": ["true"]},
                      {"name": "b", "kind": "reviewer", "argv": ["true"]}],
            "gate": ["true"]}
    assert validate(base) == [], f"чистая конфигурация отвергнута: {validate(base)}"

    def broken(**over):
        c = json.loads(json.dumps(base))
        for k, v in over.items():
            if k.startswith("guard_"):
                c["guards"][k[6:]] = v
            else:
                c[k] = v
        return validate(c)

    # Каждый LoopGuard проверяется отдельно: «хватит max_iterations» — самый частый самообман
    for g in REQUIRED_GUARDS:
        c = json.loads(json.dumps(base))
        del c["guards"][g]
        assert validate(c), f"цикл стартовал без LoopGuard `{g}`"
    assert broken(guard_stop_when="как решит модель"), "условие остановки отдано модели"
    assert broken(stage="3"), "миграция принята к автономному исполнению"
    assert broken(stage="7"), "непокрытый приборами этап принят к автономному исполнению"
    assert not broken(stage="1") and not broken(stage="2"), "разрешённый этап отвергнут"
    # Пара этапа 9 (19.08.2026): приборная часть исполняется, а снятие сторожа
    # не расползлось — соседний необратимый этап 3 отвергается по-прежнему.
    assert not broken(stage="9"), "этап 9 (приборная часть) отвергнут"
    assert broken(stage="3"), "легализация этапа 9 сняла сторожа с этапа 3"

    # generator ≠ verifier
    c = json.loads(json.dumps(base))
    c["roles"][0]["verdict_source"] = "self"
    assert validate(c), "автор объявил себя источником вердикта и был допущен"
    c = json.loads(json.dumps(base))
    c["roles"] = [c["roles"][0]]
    assert validate(c), "цикл принят без единого рецензента"
    c = json.loads(json.dumps(base))
    c["roles"] = [{"name": "b", "kind": "reviewer", "argv": ["true"]}]
    assert validate(c), "цикл принят без единого автора"

    # Установка пакетов
    c = json.loads(json.dumps(base))
    c["roles"][0]["argv"] = ["bash", "-c", "pip install requests && работать"]
    assert validate(c), "роль с установкой пакета допущена к автономному прогону"

    # argv-массив, а не строка команды
    c = json.loads(json.dumps(base))
    c["roles"][0]["argv"] = "claude -p сделай"
    assert validate(c), "строка-команда принята вместо argv-массива"

    # Изоляция параллельных ролей
    c = json.loads(json.dumps(base))
    c["roles"][0]["parallel"] = True
    c["roles"][1]["parallel"] = True
    c["isolation_worktree"] = False
    assert validate(c), "две параллельные роли допущены в одно рабочее дерево"

    # Дайджест вверх: в задании роли нет вывода соседней роли
    brief = role_brief(base, base["roles"][0], 1, [("x", "провал")])
    assert "провал" in brief and base["roles"][1]["name"] not in brief.split("РОЛЬ")[0], \
        "в задание роли протёк боковой обмен"

    # Заморозка данных: любое касание меняет отпечаток
    with tempfile.TemporaryDirectory(prefix="autoloop-selftest-") as tmp:
        d = os.path.join(tmp, "cases", "ivanov-ivan")
        os.makedirs(d)
        with open(os.path.join(d, "_client.md"), "w", encoding="utf-8") as f:
            f.write("демо\n")
        fp1 = tree_fingerprint(os.path.join(tmp, "cases"))
        with open(os.path.join(d, "_client.md"), "a", encoding="utf-8") as f:
            f.write("правка\n")
        assert tree_fingerprint(os.path.join(tmp, "cases")) != fp1, \
            "правка данных дела не изменила отпечаток — заморозка не сторожит"
        assert tree_fingerprint(os.path.join(tmp, "нет")) == "нет-каталога"

    # Спин: одинаковый отпечаток подряд обязан копиться
    fps, stale, last = ["aa", "aa", "aa"], 0, None
    for fp in fps:
        stale = stale + 1 if fp == last else 0
        last = fp
    assert stale >= 2, "детект застревания не считает повторы"

    assert env_fingerprint() == env_fingerprint(), "отпечаток окружения не воспроизводим"

    # Человеческий гейт только у координатора: роль не должна дотянуться до терминала
    code, out, _ = run(["bash", "-c", "read -r x && echo ПОЛУЧИЛ:$x || echo STDIN-ЗАКРЫТ"],
                       cwd=ROOT, timeout=30)
    assert "STDIN-ЗАКРЫТ" in out, f"роль дотянулась до stdin владельца: {out!r}"
    print("selftest: четыре сторожа, generator≠verifier, рельсы этапов, запрет установки, "
          "изоляция, дайджест вверх, заморозка cases/, детект спина — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Автономный цикл работ роем (этап 1.5).")
    ap.add_argument("config", nargs="?", help="JSON-конфигурация прогона")
    ap.add_argument("--dry", action="store_true", help="без вызова ролей: проверить конфигурацию и гейт")
    ap.add_argument("--replay", action="store_true",
                    help="пересобрать утренний отчёт из журнала (процесс мог упасть)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.replay:
        report, stop = replay()
        if report is None:
            print(f"отчёт не пересобран: {stop}", file=sys.stderr)
            return 1
        print(f"отчёт пересобран из журнала: {os.path.relpath(report, ROOT)}\n  остановка: {stop}")
        return 0
    if not a.config:
        ap.print_help()
        return 2
    try:
        with open(a.config, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        print(f"конфигурация не прочитана: {e}", file=sys.stderr)
        return 2

    bad = validate(cfg)
    if bad:
        print(f"⛔ ЦИКЛ НЕ СТАРТУЕТ · нарушений инвариантов: {len(bad)}", file=sys.stderr)
        for b in bad:
            print(f"  · {b}", file=sys.stderr)
        return 2

    print(f"ЦИКЛ · {cfg['task']} · этап {cfg['stage']} · "
          f"потолок {cfg['guards']['max_iterations']} итераций, "
          f"${cfg['guards']['max_money']}, {cfg['guards']['wall_clock_seconds']} с")
    runlog, stop, report = loop(cfg, ROOT, a.dry)
    green = runlog and runlog[-1]["green"]
    print(f"\n{'✓' if green else '⛔'} остановлен: {stop}")
    print(f"  итераций: {len(runlog)} · отчёт: {os.path.relpath(report, ROOT)}")
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
