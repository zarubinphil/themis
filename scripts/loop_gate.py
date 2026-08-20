#!/usr/bin/env python3
"""loop_gate.py — зеленый гейт итерации автономного цикла.

Этап 1.5 плана. Ни одна автономная итерация не закрывается без этого гейта.
Вердикт выносит КОД ВОЗВРАТА, а не мнение модели: generator ≠ verifier значит,
что писавший код не решает, принят ли он.

Проверяет четыре вещи:
  1. компиляция — `compileall` по scripts/ и cockpit/;
  2. `--selftest` затронутых приборов (кто менялся, тот и доказывает);
  3. smoke на синтетическом деле `ivanov-ivan` — протокол читается, сторож сторожит;
  4. ПД — дерево git чисто, рабочие логи вне git.

`--fingerprint` печатает устойчивый отпечаток вердикта. По нему детектится спин:
код меняется, а гейт возвращает тот же набор провалов — работа стоит на месте.

Выход: 0 — зелено; 1 — красно (список провалов на stdout).
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SMOKE_CASE = os.path.join("cases", "ivanov-ivan", "razdel-imushchestva-2026")
TIMEOUT = 300


def run(argv, cwd=ROOT, stdin=None, timeout=TIMEOUT):
    """Внешний вызов только argv-массивом: строка-команда открывает инъекцию через аргумент."""
    try:
        # stdin закрыт, если явно не передан: прибор гейта не спрашивает человека
        p = subprocess.run(argv, cwd=cwd, input=(stdin if stdin is not None else ""),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"таймаут {timeout} с"
    except OSError as e:
        return 127, str(e)


def has_selftest(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "--selftest" in f.read()
    except OSError:
        return False


def touched_scripts(base, root=ROOT):
    """Приборы, изменившиеся с точки base: и закоммиченное, и рабочее дерево."""
    names = set()
    for argv in (["git", "diff", "--name-only", base], ["git", "status", "--porcelain"]):
        code, out = run(argv, cwd=root)
        if code != 0:
            continue
        for line in out.splitlines():
            name = line[3:].strip() if argv[1] == "status" else line.strip()
            name = name.split(" -> ")[-1]
            if name.startswith("scripts/") and name.endswith(".py"):
                names.add(name)
    return sorted(names)


def check_compile(root=ROOT):
    targets = [d for d in ("scripts", "cockpit") if os.path.isdir(os.path.join(root, d))]
    if not targets:
        return []
    code, out = run([sys.executable, "-m", "compileall", "-q", *targets], cwd=root)
    return [] if code == 0 else [("compile", f"compileall вернул {code}: {out.strip()[:400]}")]


def check_selftests(base, root=ROOT, every=False):
    if every:
        rel = sorted(os.path.relpath(p, root)
                     for p in glob.glob(os.path.join(root, "scripts", "*.py")))
    else:
        rel = touched_scripts(base, root)
    fails = []
    for name in rel:
        path = os.path.join(root, name)
        if not os.path.isfile(path) or not has_selftest(path):
            continue
        code, out = run([sys.executable, path, "--selftest"], cwd=root)
        if code != 0:
            fails.append((f"selftest:{os.path.basename(name)}",
                          f"{name} --selftest вернул {code}: {out.strip()[-400:]}"))
    return fails


def check_smoke(root=ROOT):
    """Синтетическое дело: протокол читается, сторож блокирует запрещенную запись."""
    fails = []
    case = os.path.join(root, SMOKE_CASE)
    if not os.path.isdir(case):
        return [("smoke:case", f"нет синтетического дела {SMOKE_CASE} — smoke невозможен")]

    code, out = run([sys.executable, os.path.join(root, "scripts", "themis_status.py"),
                     SMOKE_CASE, "--brief"], cwd=root)
    if code != 0:
        fails.append(("smoke:status", f"themis_status на {SMOKE_CASE} вернул {code}: {out.strip()[:300]}"))

    # Сторож обязан отбивать запись в 00_intake. Если он молча пропустил — сторожа нет.
    hook = json.dumps({"tool_name": "Write", "tool_input": {
        "file_path": os.path.join(root, SMOKE_CASE, "00_intake", "podlog.txt"),
        "content": "проверка сторожа"}})
    code, out = run([sys.executable, os.path.join(root, "scripts", "claude_guard.py")],
                    cwd=root, stdin=hook)
    if code != 2:
        fails.append(("smoke:guard",
                      f"claude_guard пропустил запись в 00_intake (код {code}, ожидался 2)"))
    return fails


def check_prompts(root=ROOT):
    """Производные наборы промптов совпадают с каноном `.claude/`.

    Разошедшийся промпт хуже отсутствующего: агент исполняет устаревшее правило
    уверенно. Правка канона без регенерации красит гейт — это и есть смысл.
    """
    gen = os.path.join(root, "scripts", "sync_prompts.py")
    if not os.path.isfile(gen):
        return []
    code, out = run([sys.executable, gen], cwd=root)
    if code == 0:
        return []
    n = out.count("\n  · ")
    return [("prompts:drift",
             f"производные промпты разошлись с каноном ({n} файлов) — "
             f"починить `python3 scripts/sync_prompts.py --apply`")]


def check_pd(root=ROOT):
    fails = []
    guard = os.path.join(root, "scripts", "pd_guard.py")
    if not os.path.isfile(guard):
        return [("pd:missing", "scripts/pd_guard.py отсутствует — ПД никто не сторожит")]
    for mode, what in (("--tree", "дерево git"), ("--local-logs", "рабочие логи")):
        code, out = run([sys.executable, guard, mode], cwd=root)
        if code != 0:
            fails.append((f"pd{mode}", f"{what}: pd_guard вернул {code}"))
    return fails


def _anchor_paths(root=ROOT):
    """Якорь приёмки лежит ВНЕ рабочего дерева git — HEAD двигает сам исполнитель
    атомарным коммитом, а якорь двигает только координатор явной командой."""
    d = os.path.join(root, ".autoloop")
    return d, os.path.join(d, "spec-anchors.json"), os.path.join(d, "spec-anchor-log.jsonl")


def _load_anchors(store_path):
    if not os.path.isfile(store_path):
        return {}
    try:
        with open(store_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _load_anchors_strict(store_path):
    """None — файл есть, но не читается: испорченное хранилище якорей это не
    «якорей нет», а сломанный контур приёмки."""
    try:
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _log_remembers(log_path, rel):
    """Журнал якорений помнит дайджест этой приёмки — значит, якорь БЫЛ, и его
    исчезновение из хранилища это удаление, а не отсутствие (проба 20.08.2026:
    подмена контракта обходилась двумя командами — правка и удаление файла)."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("spec") == rel:
                    return True
    except OSError:
        pass
    return False


def anchor_spec(spec, root=ROOT):
    """Фиксирует текущую редакцию приёмки как базу сверки, оставляет след в журнале."""
    path = os.path.join(root, spec) if not os.path.isabs(spec) else spec
    if not os.path.isfile(path):
        print(f"приёмка {spec} не найдена — нечего якорить", file=sys.stderr)
        return 1
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    d, store_path, log_path = _anchor_paths(root)
    os.makedirs(d, exist_ok=True)
    anchors = _load_anchors(store_path)
    rel = os.path.relpath(path, root)
    anchors[rel] = digest
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(anchors, f, ensure_ascii=False, indent=1)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"spec": rel, "sha256": digest}, ensure_ascii=False) + "\n")
    print(f"приёмка {rel} заякорена: {digest[:12]}…")
    return 0


def _claude_guard_registered(path):
    """Сторож стоит в блоке PreToolUse, а не упомянут словом в файле.

    Слово в комментарии ловится подстрокой и выглядит как регистрация; git и
    харнесс читают структуру. Правило смотрит туда же, куда смотрит исполнитель.
    """
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    hooks = (data.get("hooks") or {}).get("PreToolUse") or []
    for entry in hooks if isinstance(hooks, list) else []:
        for h in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
            if "claude_guard" in str(h.get("command", "")):
                return True
    return False


def _hook_calls(text, marker):
    """Хук жив, если сторож реально ВЫЗЫВАЕТСЯ до любого безусловного выхода.

    Проба 20.08.2026: «exit 0» первой строкой и слово marker в комментарии
    выключали сторож при зелёном гейте — подстрока по файлу этого не различает.
    Читаем тело как шелл: пустые строки и комментарии (включая шебанг) не
    исполняются; первый же безусловный exit до вызова хоронит всё ниже.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"exit\b", line):
            return False
        if marker in line:
            return True
    return False


def check_hooks(root=ROOT):
    """Сторож РЕАЛЬНО СРАБОТАЕТ, а не лежит на диске.

    Правило смотрит на цель, а не на имя файла: git зовёт хуки из каталога,
    который называет он сам (`core.hooksPath` уводит их куда угодно, вплоть до
    /dev/null), и только исполняемые. Проба 20.08.2026: при `core.hooksPath
    /dev/null`, при снятом бите исполняемости и при `claude_guard`, упомянутом
    словом в комментарии рядом с пустым `PreToolUse`, прежняя проверка «файл
    есть и содержит слово» возвращала зелёный — состояние, неотличимое от
    рабочего, хотя ни один сторож не сторожил.
    """
    fails = []
    code, out = run(["git", "rev-parse", "--git-path", "hooks"], cwd=root)
    hooks_dir = out.strip() if code == 0 and out.strip() else os.path.join(".git", "hooks")
    if not os.path.isabs(hooks_dir):
        hooks_dir = os.path.join(root, hooks_dir)
    # `core.hooksPath` в системный каталог — тот же обход, только вежливее.
    code, cfg = run(["git", "config", "--get", "core.hooksPath"], cwd=root)
    if code == 0 and cfg.strip() and not os.path.realpath(
            os.path.join(root, os.path.expanduser(cfg.strip()))).startswith(os.path.realpath(root)):
        fails.append(("hooks:hookspath",
                      f"core.hooksPath уводит хуки вне репозитория ({cfg.strip()}) — "
                      f"файлы на месте, но git зовёт не их"))

    for name in ("pre-commit", "commit-msg"):
        path = os.path.join(hooks_dir, name)
        if not os.path.isfile(path):
            fails.append((f"hooks:missing-{name}",
                          f"{name} не зарегистрирован — в каталоге хуков git его нет"))
            continue
        if not os.access(path, os.X_OK):
            fails.append((f"hooks:chmod-{name}",
                          f"{name} не исполняем — git молча пропускает такой хук"))
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            text = ""
        if not _hook_calls(text, "pd_guard.py"):
            fails.append((f"hooks:empty-{name}",
                          f"{name} есть, но pd_guard в нём не вызывается — слово в "
                          f"комментарии или выход до вызова сторожом не считаются"))

    settings = os.path.join(root, ".claude", "settings.json")
    if not os.path.isfile(settings):
        fails.append(("hooks:missing-settings", ".claude/settings.json не зарегистрирован — файла нет"))
    elif not _claude_guard_registered(settings):
        fails.append(("hooks:empty-settings",
                      ".claude/settings.json есть, но claude_guard не стоит командой "
                      "в блоке PreToolUse — упоминание словом регистрацией не считается"))
    return fails


def check_spec(spec, root=ROOT):
    """Внешняя приёмка этапа: контракт задан координатором, исполнитель его не правит."""
    if not spec:
        return []
    path = os.path.join(root, spec) if not os.path.isabs(spec) else spec
    if not os.path.isfile(path):
        return [("spec:missing", f"приёмка {spec} не найдена")]

    rel = os.path.relpath(path, root)
    with open(path, encoding="utf-8", errors="replace") as f:
        current = f.read()

    # Заякоренная редакция — база сверки ВНЕ подвижного HEAD, который двигает
    # сам исполнитель атомарным коммитом. Якорь есть — сверяем с ним, а не с git.
    _, store_path, log_path = _anchor_paths(root)
    if os.path.isfile(store_path):
        anchors = _load_anchors_strict(store_path)
        if anchors is None:
            return [("spec:anchor-corrupt",
                     f"файл якорей {os.path.relpath(store_path, root)} испорчен — "
                     f"это подмена контура приёмки, а не отсутствие якоря. "
                     f"Восстановить вправе только координатор: --anchor-spec {rel}")]
    else:
        anchors = {}
    if rel in anchors:
        digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if digest != anchors[rel]:
            return [("spec:tampered",
                     f"{rel} изменён относительно заякоренной редакции — приёмку правит "
                     f"только координатор. Переякорить: --anchor-spec {rel}")]
    elif _log_remembers(log_path, rel):
        return [("spec:anchor-lost",
                 f"журнал якорений помнит дайджест {rel}, а в файле якорей его нет — "
                 f"якорь удалён, приёмка обойдена. Восстановить вправе только "
                 f"координатор: --anchor-spec {rel}")]
    else:
        # Без якоря — прежнее поведение: сверка с зафиксированной в git редакцией.
        code, committed = run(["git", "show", f"HEAD:{rel}"], cwd=root)
        if code == 0 and committed and current != committed:
            return [("spec:tampered",
                     f"{rel} изменён относительно зафиксированного в git — приёмку правит "
                     f"только координатор. Вернуть: git checkout HEAD -- {rel}")]
    code, out = run([sys.executable, path], cwd=root, timeout=1800)
    if code == 0:
        return []
    head = next((l.strip() for l in out.splitlines() if "сдано" in l), "")
    return [("spec:failed", f"приёмка этапа не пройдена ({head or 'код ' + str(code)}). "
                            f"Подробно: python3 {spec}")]


def gate(base="HEAD", root=ROOT, every=False, spec=None, spec_only=False,
         selftests_only=False, hooks_only=False):
    if spec_only:
        return check_spec(spec, root)
    if selftests_only:
        # Герметичная проверка: ВСЕ селфтесты, независимо от того, что "затронуто"
        # от base — атомарный коммит роли делает touched-детект пустым (см. --all-selftests).
        return check_selftests(base, root, every=True)
    if hooks_only:
        return check_hooks(root)
    fails = []
    fails += check_compile(root)
    fails += check_selftests(base, root, every)
    fails += check_smoke(root)
    fails += check_prompts(root)
    fails += check_pd(root)
    fails += check_spec(spec, root)
    return fails


def fingerprint(fails):
    """Устойчивый отпечаток вердикта: меняется только когда меняется НАБОР провалов.

    Тексты провалов в отпечаток не входят — иначе плавающий таймаут или номер строки
    выдавал бы движение там, где работа стоит.
    """
    ids = ";".join(sorted(f_id for f_id, _ in fails))
    return hashlib.sha256(ids.encode("utf-8")).hexdigest()[:16]


def _spec_tamper_probe():
    """Свой git-репозиторий: чистая приёмка проходит, подменённая краснеет.

    Проверка обязана быть герметичной — оглядка на состояние боевого репозитория
    делает результат selftest зависимым от того, что сейчас лежит на диске.
    """
    import shutil as _sh
    import tempfile as _tf
    with _tf.TemporaryDirectory(prefix="loopgate-tamper-") as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        spec = os.path.join(tmp, "scripts", "priemka.py")
        with open(spec, "w", encoding="utf-8") as f:
            f.write("import sys\nsys.exit(0)\n")
        for cmd in (["init", "-q"], ["add", "scripts/priemka.py"],
                    ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "spec"]):
            subprocess.run(["git", *cmd], cwd=tmp, capture_output=True)
        assert check_spec("scripts/priemka.py", tmp) == [], \
            "чистая приёмка объявлена подменённой"
        with open(spec, "a", encoding="utf-8") as f:
            f.write("# подгонка под результат\n")
        ids = {i for i, _ in check_spec("scripts/priemka.py", tmp)}
        assert "spec:tampered" in ids, f"подмена приёмки не поймана: {ids}"
        _sh.rmtree(os.path.join(tmp, ".git"), ignore_errors=True)


def selftest():
    """Проверяет сам гейт: зелёное дерево проходит, сломанный прибор ловится, отпечаток устойчив."""
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory(prefix="loopgate-selftest-") as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, SMOKE_CASE))
        shutil.copy(os.path.join(SCRIPTS, "themis_status.py"), os.path.join(tmp, "scripts"))
        shutil.copy(os.path.join(SCRIPTS, "claude_guard.py"), os.path.join(tmp, "scripts"))
        shutil.copy(os.path.join(SCRIPTS, "pd_guard.py"), os.path.join(tmp, "scripts"))
        good = os.path.join(tmp, "scripts", "pribor_ok.py")
        with open(good, "w", encoding="utf-8") as f:
            f.write("import sys\nif '--selftest' in sys.argv: sys.exit(0)\n")
        bad = os.path.join(tmp, "scripts", "pribor_bad.py")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("import sys\nif '--selftest' in sys.argv: sys.exit(1)\n")

        assert has_selftest(good) and has_selftest(bad), "детект --selftest не работает"
        ok_fails = check_selftests(None, tmp, every=True)
        ids = {i for i, _ in ok_fails}
        assert "selftest:pribor_bad.py" in ids, "сломанный прибор НЕ пойман"
        assert "selftest:pribor_ok.py" not in ids, "исправный прибор объявлен сломанным"

        # Отпечаток: зависит от набора провалов, не от их текстов
        a = fingerprint([("x", "текст один"), ("y", "текст два")])
        b = fingerprint([("y", "СОВСЕМ другой текст"), ("x", "и тут другой")])
        assert a == b, "отпечаток пляшет от текста провала — спин не задетектится"
        assert fingerprint([("x", "")]) != a, "отпечаток не отличает разные наборы провалов"
        assert fingerprint([]) != a, "зелёный вердикт неотличим от красного"

        # Компиляция ловит битый синтаксис
        with open(os.path.join(tmp, "scripts", "slomano.py"), "w", encoding="utf-8") as f:
            f.write("def :\n")
        assert check_compile(tmp), "битый синтаксис НЕ пойман компиляцией"

        # Smoke: сторожа на месте нет → гейт обязан покраснеть, а не промолчать
        assert check_smoke(os.path.join(tmp, "нет-такого")), "smoke на пустом дереве промолчал"

        # Отсутствующая приёмка обязана красить гейт, а не тихо пропускаться
        assert check_spec("scripts/net-takoy-priemki.py", tmp), "пропавшая приёмка не поймана"
        _spec_tamper_probe()
        assert check_spec(None, tmp) == [], "без приёмки гейт обязан работать как прежде"

        # Расхождение промптов с каноном обязано красить гейт
        shutil.copy(os.path.join(SCRIPTS, "sync_prompts.py"), os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, ".claude", "agents"))
        with open(os.path.join(tmp, ".claude", "agents", "kto.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: kto\ndescription: проба\n---\n\nтело\n")
        assert check_prompts(tmp), "отсутствующее производное не покрасило гейт"

        # Регистрация сторожей: пара «сторож работает» + три обхода, при которых
        # он лежит на диске, но git его не зовёт (проба 20.08.2026).
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        assert check_hooks(tmp), "голое дерево без хуков объявлено зарегистрированным"
        hp = os.path.join(tmp, ".git", "hooks")
        os.makedirs(hp, exist_ok=True)
        for name, arg in (("pre-commit", "--staged"), ("commit-msg", '--msg "$1"')):
            f_path = os.path.join(hp, name)
            with open(f_path, "w", encoding="utf-8") as f:
                f.write(f"#!/bin/sh\nexec python3 scripts/pd_guard.py {arg}\n")
            os.chmod(f_path, 0o755)
        cl = os.path.join(tmp, ".claude")
        os.makedirs(cl, exist_ok=True)
        settings = os.path.join(cl, "settings.json")
        rabochiy = {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|Bash|Read", "hooks": [
            {"type": "command", "command": "python3 scripts/claude_guard.py"}]}]}}
        with open(settings, "w", encoding="utf-8") as f:
            json.dump(rabochiy, f)
        assert check_hooks(tmp) == [], f"рабочие сторожа не признаны: {check_hooks(tmp)}"

        subprocess.run(["git", "config", "core.hooksPath", "/dev/null"], cwd=tmp,
                       capture_output=True)
        assert any(i == "hooks:hookspath" for i, _ in check_hooks(tmp)), \
            "core.hooksPath /dev/null не пойман — git не зовёт хуки, гейт зелен"
        subprocess.run(["git", "config", "--unset", "core.hooksPath"], cwd=tmp,
                       capture_output=True)

        os.chmod(os.path.join(hp, "pre-commit"), 0o644)
        assert any(i.startswith("hooks:chmod") for i, _ in check_hooks(tmp)), \
            "неисполняемый хук не пойман — git молча пропускает такой"
        os.chmod(os.path.join(hp, "pre-commit"), 0o755)

        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"_комментарий": "раньше был claude_guard",
                       "hooks": {"PreToolUse": []}}, f, ensure_ascii=False)
        assert any(i == "hooks:empty-settings" for i, _ in check_hooks(tmp)), \
            "упоминание сторожа словом принято за регистрацию"
        with open(settings, "w", encoding="utf-8") as f:
            json.dump(rabochiy, f)
        assert check_hooks(tmp) == [], "восстановленная регистрация не признана"

    # Якорь приёмки: своя песочница-репозиторий, вне текущего tmp выше (нужен git).
    with tempfile.TemporaryDirectory(prefix="loopgate-anchor-") as agit:
        os.makedirs(os.path.join(agit, "scripts"))
        spec_path = os.path.join(agit, "scripts", "priemka.py")
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write("import sys\nsys.exit(0)\n")
        for cmd in (["init", "-q"], ["add", "-A"],
                    ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "spec"]):
            subprocess.run(["git", *cmd], cwd=agit, capture_output=True)
        assert anchor_spec("scripts/priemka.py", agit) == 0, "якорение чистой приёмки отказало"
        anchors_log = [p for p in os.listdir(os.path.join(agit, ".autoloop")) if "anchor" in p]
        assert anchors_log, "якорение не оставило следа в .autoloop/"
        assert check_spec("scripts/priemka.py", agit) == [], "заякоренная нетронутая приёмка красна"

        # Подгонка + обычный коммит — HEAD двигается, якорь нет.
        with open(spec_path, "a", encoding="utf-8") as f:
            f.write("# подгонка под результат\n")
        for cmd in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "podgonka"]):
            subprocess.run(["git", *cmd], cwd=agit, capture_output=True)
        ids = {i for i, _ in check_spec("scripts/priemka.py", agit)}
        assert "spec:tampered" in ids, "подмена приёмки после коммита не поймана якорем"

        # Легитимное переякорение снимает провал.
        assert anchor_spec("scripts/priemka.py", agit) == 0
        assert check_spec("scripts/priemka.py", agit) == [], "переякоренная приёмка не принята"

        # gate(spec_only=True) изолирует ТОЛЬКО приёмку — без smoke/compile/pd.
        assert gate(root=agit, spec="scripts/priemka.py", spec_only=True) == []

    print("selftest: детект приборов, устойчивость отпечатка, компиляция, smoke, "
         "регистрация сторожей, якорь приёмки — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Зеленый гейт итерации автономного цикла.")
    ap.add_argument("--base", default="HEAD", help="точка отсчета изменений (по умолчанию HEAD)")
    ap.add_argument("--all-selftests", action="store_true",
                    help="гонять selftest ВСЕХ приборов, а не только затронутых")
    ap.add_argument("--spec", help="внешняя приёмка этапа (например scripts/stage5_spec.py)")
    ap.add_argument("--spec-only", action="store_true",
                    help="гонять ТОЛЬКО приёмку из --spec, без компиляции/smoke/селфтестов")
    ap.add_argument("--anchor-spec", metavar="SPEC",
                    help="заякорить текущую редакцию приёмки вне HEAD (журнал в .autoloop/) и выйти")
    ap.add_argument("--selftests-only", action="store_true",
                    help="гонять ТОЛЬКО селфтесты, герметично — все, а не только затронутые")
    ap.add_argument("--hooks-only", action="store_true",
                    help="гонять ТОЛЬКО проверку РЕГИСТРАЦИИ сторожей (pre-commit/commit-msg/settings.json)")
    ap.add_argument("--fingerprint", action="store_true", help="печатать только отпечаток вердикта")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.anchor_spec:
        return anchor_spec(a.anchor_spec, ROOT)
    if a.selftest:
        return selftest()

    fails = gate(a.base, ROOT, a.all_selftests, a.spec, a.spec_only,
                a.selftests_only, a.hooks_only)
    fp = fingerprint(fails)
    if a.fingerprint:
        print(fp)
        return 1 if fails else 0
    if a.json:
        print(json.dumps({"green": not fails, "fingerprint": fp,
                          "fails": [{"id": i, "text": t} for i, t in fails]},
                         ensure_ascii=False, indent=1))
        return 1 if fails else 0
    if not fails:
        print(f"✓ ГЕЙТ ЗЕЛЕНЫЙ · отпечаток {fp}")
        return 0
    print(f"❌ ГЕЙТ КРАСНЫЙ · провалов {len(fails)} · отпечаток {fp}")
    for i, t in fails:
        print(f"  · {i}: {t}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
