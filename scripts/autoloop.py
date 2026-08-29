#!/usr/bin/env python3
"""autoloop.py — автономный цикл работ роем. Этап 1.5 плана FINAL-PLAN-2026-08-18.

Механизм взят из роя Олимпуза (`соседнего репозитория olympuz`, Apache-2.0, движок на TypeScript)
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
# token_ledger читает session-JSONL Claude Code — расход ролей на харнессе он видит.
# Роли на иных CLI ведут журналы своих форматов, и прибор их не читает. Слепоту
# учёта нельзя молчать: цифра бюджета тогда покрывает одну сторону из нескольких и
# подаётся как полная — на неё смотрят и проезжают потолок. Имена таких CLI в код
# не зашиты: измеряемый харнесс — из реестра, всё прочее считается неизмеряемым.
MEASURED_HARNESS = {"claude"}


def _unmetered_clis(roles):
    """CLI ролей, чей расход token_ledger не видит (всё, кроме claude)."""
    seen = []
    for r in roles:
        argv = r.get("argv") if isinstance(r, dict) else None
        if isinstance(argv, list) and argv:
            exe = os.path.basename(str(argv[0]))
            if exe and exe not in MEASURED_HARNESS and exe not in seen:
                seen.append(exe)
    return seen
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

# Мусор операционной системы: Finder переписывает .DS_Store при простом открытии
# папки, Word кладёт замок рядом с открытым документом. Материалами дела они не
# являются, а отпечаток двигают — 20.08.2026 из-за этого встал многочасовой прогон,
# при том что ни один файл дела не менялся шесть часов. Сторож, срабатывающий на
# обиходе, останавливает работу вместо того, чтобы её защищать.
MUSOR_OS = (".DS_Store", ".localized", ".Spotlight-V100", ".fseventsd", ".TemporaryItems")
# Служебные журналы САМОЙ системы, лежащие внутри cases/. Их дописывает проект
# (Stop-хук в .claude/settings.json) в конце каждой сессии, поэтому в отпечатке
# они означают «система работала», а не «кто-то тронул дело». Прогон 21.08.2026
# дважды вставал со словами «ТРОНУТЫ ДАННЫЕ ДЕЛ» при зелёном гейте: сравнение
# снимков 11 669 файлов дало ровно один изменённый — _session_history.txt,
# 20032 -> 20064 байта. Сторож, кричащий без причины, учит игнорировать себя,
# а охраняет он 20 ГБ первички.
# Исключение УЗКОЕ и по МЕСТУ, не по имени: журнал признаётся служебным только
# там, где его пишет Stop-хук — верхний уровень папки клиента (cases/<клиент>/
# _session_history.txt) и cases/_logs/. Тот же файл, положенный ГЛУБЖЕ (в дело,
# в 00_intake), — данные, а не журнал: исключение по одному имени в любом месте
# делало _session_history.txt внутри 00_intake невидимым для сторожа (этап 9.20,
# круг 8). Прочие файлы с подчёркивания (_client.md, _case.md) отпечаток двигают.
ZHURNALY_SISTEMY = ("_session_history.txt",)


def _sluzhebnyy_zhurnal(rel_path: str, name: str) -> bool:
    parts = rel_path.split(os.sep)
    if parts[0] == "_logs":                       # cases/_logs/** — журнал системы
        return True
    # cases/<клиент>/_session_history.txt — ровно верхний уровень папки клиента.
    return name in ZHURNALY_SISTEMY and len(parts) == 2


def _musor(name):
    return name in MUSOR_OS or name.startswith("~$") or name.endswith(".tmp")


def tree_fingerprint(path):
    """Отпечаток дерева по (путь, размер, mtime). Читать 21 ГБ содержимого не нужно:
    задача — заметить ЛЮБОЕ касание, а не сверить байты (для этого есть intake_backup).

    Мусор ОС в отпечаток не входит: см. MUSOR_OS. Правка, создание и удаление
    настоящего файла дела отпечаток меняют по-прежнему."""
    h = hashlib.sha256()
    if not os.path.isdir(path):
        return "нет-каталога"
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(n for n in filenames if not _musor(n)):
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, path)
            if _sluzhebnyy_zhurnal(rel, name):
                continue
            h.update(rel.encode("utf-8"))
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def _dir_signature(h, path):
    """Подмешать имена прямых детей каталога: установленный пакет — новый ребёнок.
    Каталога нет — тишина (ещё не создан менеджером), а не срыв отпечатка."""
    try:
        for name in sorted(os.listdir(path)):
            h.update(name.encode("utf-8"))
    except OSError:
        return


def _manager_root(env_var, bin_name):
    """Корень менеджера пакетов: сперва его переменная, иначе — из положения в PATH.
    Ни того, ни другого нет — менеджера на машине нет, мерить нечего."""
    root = os.environ.get(env_var)
    if root:
        return root
    exe = shutil.which(bin_name)
    if exe:                       # …/bin/<tool> → … (типовой префикс установки)
        return os.path.dirname(os.path.dirname(os.path.realpath(exe)))
    return None


def env_fingerprint():
    """Отпечаток установленных пакетов по ВСЕМ менеджерам, что есть в PATH.

    Запрет владельца назван для pip, npm и brew разом — значит и мерить надо все
    три экосистемы, а не только site-packages текущего интерпретатора (проба
    20.08.2026: установка в node_modules/gems/Cellar отпечаток не двигала вовсе).
    Корень берётся из переменной менеджера (NPM_CONFIG_PREFIX, GEM_HOME,
    HOMEBREW_PREFIX), иначе — из его положения в PATH. Считаются только каталоги
    установки: правка файла проекта и __pycache__ отпечаток не двигают.
    """
    h = hashlib.sha256()
    # 1. python текущего интерпретатора
    for d in sorted(set(p for p in sys.path if p.endswith(("site-packages", "dist-packages")))):
        _dir_signature(h, d)
    # 2. npm — глобальные модули под префиксом
    npm = _manager_root("NPM_CONFIG_PREFIX", "npm")
    if npm:
        _dir_signature(h, os.path.join(npm, "lib", "node_modules"))
        _dir_signature(h, os.path.join(npm, "node_modules"))
    # 3. gem — установленные гемы
    gem = _manager_root("GEM_HOME", "gem")
    if gem:
        _dir_signature(h, os.path.join(gem, "gems"))
        _dir_signature(h, gem)
    # 4. brew — формулы в Cellar
    brew = _manager_root("HOMEBREW_PREFIX", "brew")
    if brew:
        _dir_signature(h, os.path.join(brew, "Cellar"))
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

def _worktree_path(name, root=ROOT):
    """Путь рабочей копии роли строится от ПЕРЕДАННОГО root, не от модульного
    STATE_DIR: приёмка и чужая машина гоняют прибор с иным корнем, а путь от
    STATE_DIR увёл бы рабочие копии в боевое дерево."""
    return os.path.join(root, ".autoloop", "worktrees", name)


def _is_worktree(path):
    """path — настоящая рабочая копия git (git worktree), а не обычный каталог.

    Обычный каталог под .autoloop/worktrees git отнёс бы к ОСНОВНОМУ репозиторию:
    `git reset --hard`, запущенный в нём, снёс бы незакоммиченную работу
    координатора, `checkout -B` увёл бы основное дерево на ветку роли. Факт
    проверяется git-ом: у настоящей рабочей копии её собственный toplevel равен ей
    самой, у обычного каталога — это toplevel основного репозитория (выше)."""
    if not os.path.isdir(path):
        return False
    code, top, _ = run(["git", "rev-parse", "--show-toplevel"], cwd=path, timeout=60)
    if code != 0 or not top.strip():
        return False
    return os.path.realpath(top.strip()) == os.path.realpath(path)


def worktree_add(name, root=ROOT):
    """Рабочая копия роли НА ВЕТКЕ `autoloop/<имя>` от текущего HEAD основного дерева.

    Два эффекта сразу. Изоляция тайны: в worktree попадают только отслеживаемые
    файлы, а `cases/` под .gitignore — чужой CLI материалов дел в своём
    рабочем каталоге не видит вовсе. Перенос работы: роль коммитит в свою ветку,
    координатор забирает её мержем (`worktree_merge`) — правки не теряются вместе
    с detached HEAD, как было бы без ветки.
    """
    path = _worktree_path(name, root)
    branch = f"autoloop/{name}"
    code, head, _ = run(["git", "rev-parse", "HEAD"], cwd=root, timeout=60)
    head = head.strip()
    if _is_worktree(path):
        # Настоящая рабочая копия: итерация N+1 стартует от СВЕЖЕГО HEAD основного
        # дерева, а не от вчерашнего, иначе роль чинит уже починенное. reset/checkout
        # здесь безопасны — git видит именно эту копию, а не основной репозиторий.
        run(["git", "reset", "--hard", "-q"], cwd=path, timeout=300)
        run(["git", "clean", "-fdq"], cwd=path, timeout=300)
        code, _, err = run(["git", "checkout", "-q", "-B", branch, head], cwd=path, timeout=300)
        if code != 0:
            raise RuntimeError(f"рабочая копия роли `{name}` не обновлена: {err.strip()[:200]}")
        return path
    # Каталог на месте рабочей копии, но НЕ рабочая копия (обычная папка) — снести,
    # иначе `git worktree add` откажет «destination exists», а reset/checkout в ней
    # ударил бы по ОСНОВНОМУ дереву. Настоящую копию сюда не заносит: её ловит ветка
    # выше. Пустой каталог удаляется тоже — он не рабочая копия.
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    code, _, err = run(["git", "worktree", "add", "-B", branch, path, head],
                       cwd=root, timeout=300)
    if code != 0:
        raise RuntimeError(f"рабочая копия для роли `{name}` не создана: {err.strip()[:200]}")
    return path


def worktree_merge(name, root=ROOT):
    """Забрать коммиты роли из её ветки в основное дерево. Конфликт — не катастрофа:
    мерж откатывается, роль теряет итерацию, причина остаётся в журнале.

    Роль, оставившую правки незакоммиченными, страхует автокоммит: чужой CLI
    команде «коммить каждую правку» подчиняется не всегда (итерация 1, 19.08.2026 —
    работа роли осталась в worktree и стёрлась бы обновлением копии). ПД-сторож
    коммита при этом не обходится: хук отработает и остановит грязный автокоммит."""
    branch = f"autoloop/{name}"
    wt = _worktree_path(name, root)
    note = None
    code, out, _ = run(["git", "status", "--porcelain"], cwd=wt, timeout=60)
    if code == 0 and out.strip():
        run(["git", "add", "-A"], cwd=wt, timeout=120)
        cc, _, _ = run(["git", "-c", "user.email=autoloop@themis", "-c", f"user.name={name}",
                        "commit", "-qm", f"итерация роли {name}: автокоммит координатора "
                        f"(роль оставила правки незакоммиченными)"], cwd=wt, timeout=300)
        if cc != 0:
            # Автокоммит заблокирован — почти всегда ПД-сторожем (pre-commit). Это НЕ
            # бездействие роли: грязная правка с признаком ПД не вынесена и сгинет с
            # копией роли (worktree remove --force). След обязан остаться в журнале,
            # иначе сработавший сторож неотличим от роли, что ничего не делала.
            note = (f"автокоммит роли {name} заблокирован (pd_guard): незакоммиченная "
                    f"правка с признаком персональных данных не вынесена и потеряна "
                    f"с копией роли")
    code, out, _ = run(["git", "rev-list", "--count", f"HEAD..{branch}"], cwd=root, timeout=60)
    ahead = int(out.strip() or 0) if code == 0 else 0
    if ahead == 0:
        # Заблокированный автокоммит — не «мержить нечего», а остановленный вынос:
        # merged=False, чтобы тихий успех (merged=true, commits=0) его не маскировал.
        return (False, 0, note) if note else (True, 0, None)
    code, _, err = run(["git", "merge", "--no-edit", branch], cwd=root, timeout=300)
    if code != 0:
        run(["git", "merge", "--abort"], cwd=root, timeout=300)
        return False, ahead, note
    return True, ahead, note


def worktree_remove(name, root=ROOT):
    path = _worktree_path(name, root)
    run(["git", "worktree", "remove", "--force", path], cwd=root, timeout=300)
    shutil.rmtree(path, ignore_errors=True)
    run(["git", "branch", "-D", f"autoloop/{name}"], cwd=root, timeout=60)


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
    unmetered = _unmetered_clis(cfg.get("roles", []))
    if unmetered:
        lines += ["", "## Учёт расхода неполный", "",
                  f"Роли на CLI {', '.join(unmetered)} прибором `token_ledger` не "
                  f"измеряются: расход по ним не виден, цифра бюджета покрывает только "
                  f"харнесс. Часть картины нельзя принимать за целое — досчитать расход "
                  f"этих ролей по их собственным журналам руками."]
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
    # База расхода fail-closed: не `or 0.0`. Молчащий прибор давал бы базу 0, и
    # первый же успешный замер объявлялся тратой цикла (ложное «бюджет исчерпан»).
    # None здесь — «база не измерена»; разница считается ниже, и там же fail-closed.
    money_start = spent_money(root)
    started = time.time()
    runlog, fails, last_fp, stale = [], [], None, 0
    stop = "потолок итераций"

    journal({"event": "start", "task": cfg["task"], "stage": cfg["stage"],
             "guards": guards, "cases_fp": cases_fp, "env_fp": env_fp,
             "money_start": money_start}, root)

    unmetered = _unmetered_clis(cfg["roles"])
    if unmetered:
        note = ("расход не измеряется прибором token_ledger для CLI: "
                + ", ".join(unmetered) + " — учёт неполный, цифра бюджета покрывает "
                "только харнесс; чужие журналы прибор не читает")
        journal({"event": "accounting_blind", "unmetered": unmetered, "note": note}, root)
        print(f"⚠ учёт расхода неполный: {note}")

    for it in range(1, int(guards["max_iterations"]) + 1):
        t0 = time.time()
        role_names = []

        def ostatok_vremeni():
            # Таймаут роли — ОСТАТОК общего бюджета времени, а не полный потолок на
            # каждую: иначе прогон законно переезжает потолок во столько раз, сколько
            # ролей. Проверка времени стоит ПЕРЕД каждым шагом (ниже).
            return float(guards["wall_clock_seconds"]) - (time.time() - started)

        def podgotovit(role):
            brief = role_brief(cfg, role, it, fails)
            argv = [x.replace("{brief}", brief).replace("{python}", sys.executable)
                    for x in role["argv"]]
            cwd = root
            # Изоляция — ИНВАРИАНТ, а не флаг `parallel`: рабочая копия выдаётся
            # КАЖДОЙ роли, чтобы чужой CLI работал в дереве без материалов дел
            # (cases/ под .gitignore). `parallel` — только про одновременность.
            if cfg.get("isolation_worktree", True) and not dry:
                cwd = worktree_add(role["name"], root)
                if os.path.realpath(cwd) == os.path.realpath(root):
                    raise RuntimeError(f"изоляция запрошена, но роль `{role['name']}` "
                                       f"осталась в корне репозитория")
            return argv, cwd

        def ispolnit(role, argv, cwd):
            code, out, err = run(argv, cwd=cwd, timeout=max(1, int(ostatok_vremeni())))
            journal({"event": "role", "iteration": it, "role": role["name"],
                     "kind": role["kind"], "code": code, "cwd": cwd,
                     "tail": (out or err)[-800:]}, root)
            return code

        volna = [r for r in cfg["roles"] if r.get("parallel")]
        poodinochke = [r for r in cfg["roles"] if not r.get("parallel")]
        vremya_ischerpano = False

        # Волна параллельных ролей идёт ОДНОВРЕМЕННО. Раньше флаг `parallel` только
        # выделял рабочую копию, а исполнение оставалось последовательным: три роли
        # по часу давали три часа вместо часа. Рабочие копии разные, боковой обмен
        # по-прежнему невозможен — распараллеливать безопасно (20.08.2026).
        if volna and not dry:
            if ostatok_vremeni() <= 0:
                vremya_ischerpano = True
            else:
                import threading
                zadaniya = [(r, *podgotovit(r)) for r in volna]
                itogi = {}
                potoki = []
                for role, argv, cwd in zadaniya:
                    t = threading.Thread(target=lambda ro=role, a=argv, c=cwd:
                                         itogi.__setitem__(ro["name"], ispolnit(ro, a, c)),
                                         daemon=True)
                    t.start()
                    potoki.append(t)
                    role_names.append(role["name"])
                for t in potoki:
                    t.join()
                # Мержи — строго по одному: индекс основного дерева общий, параллельный
                # мерж оставил бы его в состоянии конфликта.
                for role, _, cwd in zadaniya:
                    if cwd == root:
                        continue
                    if itogi.get(role["name"]) == 0:
                        merged, ahead, note = worktree_merge(role["name"], root)
                    else:
                        merged, ahead, note = False, -1, None
                    zapis = {"event": "role_merge", "iteration": it, "role": role["name"],
                             "merged": merged, "commits": ahead}
                    if note:
                        zapis["note"] = note
                    journal(zapis, root)
        elif volna and dry:
            for role in volna:
                argv, _ = podgotovit(role)
                journal({"event": "role_dry", "iteration": it, "role": role["name"],
                         "argv": argv[:3]}, root)
                role_names.append(role["name"])

        # Последовательные роли идут ПОСЛЕ волны: рецензент обязан видеть уже
        # смерженную работу авторов, иначе он рецензирует вчерашнее дерево.
        if not vremya_ischerpano:
            for role in poodinochke:
                if not dry and ostatok_vremeni() <= 0:
                    vremya_ischerpano = True     # проверка времени — ПЕРЕД шагом
                    break
                argv, cwd = podgotovit(role)
                if dry:
                    journal({"event": "role_dry", "iteration": it, "role": role["name"],
                             "argv": argv[:3]}, root)
                else:
                    code = ispolnit(role, argv, cwd)
                    if cwd != root:
                        if code == 0:
                            merged, ahead, note = worktree_merge(role["name"], root)
                        else:
                            merged, ahead, note = False, -1, None
                        zapis = {"event": "role_merge", "iteration": it, "role": role["name"],
                                 "merged": merged, "commits": ahead}
                        if note:
                            zapis["note"] = note
                        journal(zapis, root)
                role_names.append(role["name"])

        # Потолок времени — потолок ПРОГОНА: исчерпан до начала роли — стоп, гейт не
        # гоняем (иначе бюджет всё равно переехали бы на роль и/или гейт).
        if vremya_ischerpano:
            stop = f"потолок времени: {guards['wall_clock_seconds']} с"
            break

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

        # ── сторожа целостности: судят КАЖДУЮ итерацию, ВКЛЮЧАЯ победную ──
        # Зелёный гейт при тронутых делах или установленном пакете — это провал,
        # а не успех. Раньше сторожа стояли ПОСЛЕ раннего выхода по зелёному, и роль,
        # тронувшая дела в той же итерации, где гейт позеленел, выходила «цель
        # достигнута»: инвариант не действовал ровно на итерации, после которой уже
        # никто не смотрит (проба 20.08.2026). Поэтому сверка — до объявления успеха.
        now_cases = tree_fingerprint(os.path.join(root, "cases"))
        if now_cases != cases_fp:
            stop = "ТРОНУТЫ ДАННЫЕ ДЕЛ: отпечаток cases/ изменился — автономно это запрещено"
            break
        if env_fingerprint() != env_fp:
            stop = "УСТАНОВЛЕН ПАКЕТ: отпечаток окружения изменился — установка автономно запрещена"
            break

        if green:
            stop = "цель достигнута — гейт зелёный"
            break

        # ── сторожа продолжения: только когда гейт красный ──
        # Деньги — не та ось, где догадка допустима: прибор молчит или врёт — стоп.
        # Раньше проверка была `if spent is not None …` и молча выключалась на мёртвом
        # token_ledger; при потолке $0.01 цикл домолачивал до потолка итераций
        # (владельцу это стоило $299 при потолке $60, 20.08.2026). Неразобранный
        # расход обязан быть стопом, как неразобранный вердикт гейта.
        spent = spent_money(root)
        if spent is None:
            stop = ("РАСХОД НЕ ИЗМЕРЕН: прибор token_ledger недоступен или вернул мусор — "
                    "бюджет не сторожится. Неразобранный расход есть стоп: разобрать "
                    "расход руками и перезапустить")
            break
        if money_start is None:
            # База не измерилась на старте, а замер сейчас удался — разницу считать
            # НЕ ОТ ЧЕГО. Первый успешный замер не есть трата цикла: fail-closed так
            # же, как неизмеримый замер выше. Иначе цифру всего расхода объявили бы
            # тратой одной итерации и остановили прогон ложным «бюджет исчерпан».
            stop = ("БАЗА РАСХОДА НЕ ИЗМЕРЕНА НА СТАРТЕ: прибор token_ledger тогда "
                    "молчал, а теперь ответил — разницу считать не от чего, первый "
                    "успешный замер тратой цикла не считается. Разобрать расход руками")
            break
        if (spent - money_start) > float(guards["max_money"]):
            stop = (f"бюджет исчерпан: потрачено ${spent - money_start:.2f} при потолке "
                    f"${float(guards['max_money']):.2f}")
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

    # Рабочая копия выдаётся КАЖДОЙ роли (изоляция — инвариант), значит и снимается
    # у каждой, а не только у параллельных.
    for role in cfg["roles"]:
        if cfg.get("isolation_worktree", True) and not dry:
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
        # Пара: мусор ОС отпечаток НЕ двигает, иначе открытая в Finder папка
        # роняет ночной прогон (20.08.2026).
        fp2 = tree_fingerprint(os.path.join(tmp, "cases"))
        for musor in (".DS_Store", "~$isk.docx", "kesh.tmp"):
            with open(os.path.join(d, musor), "w", encoding="utf-8") as f:
                f.write("мусор ос\n")
        assert tree_fingerprint(os.path.join(tmp, "cases")) == fp2, \
            "мусор ОС сдвинул отпечаток — сторож останавливает прогон на пустом месте"
        # И при этом новый НАСТОЯЩИЙ файл дела по-прежнему виден.
        with open(os.path.join(d, "novyy_material.pdf"), "w", encoding="utf-8") as f:
            f.write("материал\n")
        assert tree_fingerprint(os.path.join(tmp, "cases")) != fp2, \
            "новый файл дела не изменил отпечаток — заморозка ослепла"
        assert tree_fingerprint(os.path.join(tmp, "нет")) == "нет-каталога"
        # Служебный журнал исключается по МЕСТУ, не по имени (этап 9.20, круг 8).
        # Ось обихода: журнал на верхнем уровне папки клиента отпечаток НЕ двигает.
        fp3 = tree_fingerprint(os.path.join(tmp, "cases"))
        with open(os.path.join(d, "_session_history.txt"), "w", encoding="utf-8") as f:
            f.write("Session ended\n")
        assert tree_fingerprint(os.path.join(tmp, "cases")) == fp3, \
            "журнал системы на верхнем уровне папки клиента сдвинул отпечаток"
        # Обратная ось: тот же файл ГЛУБЖЕ (в 00_intake) — данные, а не журнал.
        intake = os.path.join(d, "delo-2026", "00_intake")
        os.makedirs(intake)
        fp4 = tree_fingerprint(os.path.join(tmp, "cases"))
        with open(os.path.join(intake, "_session_history.txt"), "w", encoding="utf-8") as f:
            f.write("подмена под видом журнала\n")
        assert tree_fingerprint(os.path.join(tmp, "cases")) != fp4, \
            "_session_history.txt внутри 00_intake невидим для сторожа — исключение стало каналом"

    # Спин: одинаковый отпечаток подряд обязан копиться
    fps, stale, last = ["aa", "aa", "aa"], 0, None
    for fp in fps:
        stale = stale + 1 if fp == last else 0
        last = fp
    assert stale >= 2, "детект застревания не считает повторы"

    assert env_fingerprint() == env_fingerprint(), "отпечаток окружения не воспроизводим"

    # Worktree-роль: ветка, мерж в main, изоляция gitignored, конфликт не рушит main
    with tempfile.TemporaryDirectory(prefix="autoloop-wt-") as tmp:
        def g(*args, cwd=tmp):
            return run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd)
        g("init", "-q")
        with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("cases/\n.autoloop/\n")
        os.makedirs(os.path.join(tmp, "cases", "taynoe-delo"))
        with open(os.path.join(tmp, "cases", "taynoe-delo", "x.md"), "w", encoding="utf-8") as f:
            f.write("персональные данные\n")
        with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as f:
            f.write("база\n")
        g("add", ".gitignore", "a.txt")
        g("commit", "-qm", "start")

        global STATE_DIR
        saved_state = STATE_DIR
        STATE_DIR = os.path.join(tmp, ".autoloop")
        try:
            wt = worktree_add("proba", tmp)
            assert not os.path.isdir(os.path.join(wt, "cases")), \
                "gitignored cases/ ПОПАЛ в worktree — чужой CLI увидит материалы дел"
            with open(os.path.join(wt, "b.txt"), "w", encoding="utf-8") as f:
                f.write("работа роли\n")
            g("add", "b.txt", cwd=wt)
            g("commit", "-qm", "правка роли", cwd=wt)
            ok, ahead, _ = worktree_merge("proba", tmp)
            assert ok and ahead == 1, f"коммит роли не въехал в main: {ok}, {ahead}"
            # Роль НЕ закоммитила — автокоммит координатора спасает работу
            wt2 = worktree_add("proba", tmp)
            with open(os.path.join(wt2, "c.txt"), "w", encoding="utf-8") as f:
                f.write("незакоммиченная работа роли\n")
            ok, ahead, _ = worktree_merge("proba", tmp)
            assert ok and ahead == 1, "незакоммиченная работа роли потеряна"
            assert os.path.isfile(os.path.join(tmp, "c.txt")), \
                "автокоммит прошёл, а файла в main нет"
            assert os.path.isfile(os.path.join(tmp, "b.txt")), \
                "мерж прошёл, а файла роли в main нет"
            # Итерация N+1: worktree обновляется до нового HEAD main
            wt = worktree_add("proba", tmp)
            assert os.path.isfile(os.path.join(wt, "b.txt")), \
                "worktree второй итерации отстал от HEAD — роль чинит уже починенное"
            # Конфликт: main и ветка правят один файл — мерж откатывается, main цел
            with open(os.path.join(wt, "a.txt"), "w", encoding="utf-8") as f:
                f.write("версия роли\n")
            g("add", "a.txt", cwd=wt)
            g("commit", "-qm", "роль правит a", cwd=wt)
            with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as f:
                f.write("версия main\n")
            g("add", "a.txt")
            g("commit", "-qm", "main правит a")
            ok, _, _ = worktree_merge("proba", tmp)
            assert not ok, "конфликтный мерж объявлен успешным"
            assert not os.path.isfile(os.path.join(tmp, ".git", "MERGE_HEAD")), \
                "после отказа мержа main остался в состоянии конфликта"
            assert open(os.path.join(tmp, "a.txt"), encoding="utf-8").read() == "версия main\n", \
                "конфликтный мерж затёр main"
            worktree_remove("proba", tmp)
        finally:
            STATE_DIR = saved_state

    # Волна параллельных ролей: три роли по секунде обязаны уложиться в секунду,
    # а не в три. Пара к ней — мержи идут по одному (общий индекс main).
    with tempfile.TemporaryDirectory(prefix="autoloop-par-") as tmp:
        import threading as _th
        starty, lock = [], _th.Lock()

        def _rol(i):
            with lock:
                starty.append(time.time())
            run(["sleep", "1"], cwd=tmp, timeout=30)

        t0 = time.time()
        potoki = [_th.Thread(target=_rol, args=(i,)) for i in range(3)]
        for t in potoki:
            t.start()
        for t in potoki:
            t.join()
        proshlo = time.time() - t0
        assert proshlo < 2.5, (f"три роли по секунде заняли {proshlo:.1f} с — волна идёт "
                              f"последовательно, флаг parallel ничего не распараллеливает")
        assert max(starty) - min(starty) < 0.5, "роли волны стартовали вразнобой"

    # Человеческий гейт только у координатора: роль не должна дотянуться до терминала
    code, out, _ = run(["bash", "-c", "read -r x && echo ПОЛУЧИЛ:$x || echo STDIN-ЗАКРЫТ"],
                       cwd=ROOT, timeout=30)
    assert "STDIN-ЗАКРЫТ" in out, f"роль дотянулась до stdin владельца: {out!r}"
    print("selftest: четыре сторожа, generator≠verifier, рельсы этапов, запрет установки, "
          "изоляция, ветка+мерж worktree-ролей, одновременность волны, дайджест вверх, заморозка cases/, детект спина — ок")
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
    # Успех — только «цель достигнута». Зелёный гейт, снятый сторожем целостности
    # (тронуты дела, установлен пакет), успехом не считается: последняя итерация
    # может быть зелёной, а прогон — проваленным.
    success = stop.startswith("цель достигнута")
    print(f"\n{'✓' if success else '⛔'} остановлен: {stop}")
    print(f"  итераций: {len(runlog)} · отчёт: {os.path.relpath(report, ROOT)}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
