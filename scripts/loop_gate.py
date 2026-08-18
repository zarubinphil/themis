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


def gate(base="HEAD", root=ROOT, every=False):
    fails = []
    fails += check_compile(root)
    fails += check_selftests(base, root, every)
    fails += check_smoke(root)
    fails += check_prompts(root)
    fails += check_pd(root)
    return fails


def fingerprint(fails):
    """Устойчивый отпечаток вердикта: меняется только когда меняется НАБОР провалов.

    Тексты провалов в отпечаток не входят — иначе плавающий таймаут или номер строки
    выдавал бы движение там, где работа стоит.
    """
    ids = ";".join(sorted(f_id for f_id, _ in fails))
    return hashlib.sha256(ids.encode("utf-8")).hexdigest()[:16]


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

        # Расхождение промптов с каноном обязано красить гейт
        shutil.copy(os.path.join(SCRIPTS, "sync_prompts.py"), os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, ".claude", "agents"))
        with open(os.path.join(tmp, ".claude", "agents", "kto.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: kto\ndescription: проба\n---\n\nтело\n")
        assert check_prompts(tmp), "отсутствующее производное не покрасило гейт"
    print("selftest: детект приборов, устойчивость отпечатка, компиляция, smoke — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Зеленый гейт итерации автономного цикла.")
    ap.add_argument("--base", default="HEAD", help="точка отсчета изменений (по умолчанию HEAD)")
    ap.add_argument("--all-selftests", action="store_true",
                    help="гонять selftest ВСЕХ приборов, а не только затронутых")
    ap.add_argument("--fingerprint", action="store_true", help="печатать только отпечаток вердикта")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    fails = gate(a.base, ROOT, a.all_selftests)
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
