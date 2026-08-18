#!/usr/bin/env python3
"""stage5_spec.py — приёмка приборов этапа 5. Пишется КООРДИНАТОРОМ, не исполнителем.

Инвариант роя: generator ≠ verifier. Если рой пишет и прибор, и его `--selftest`,
то «selftest зелёный» не доказывает ничего — исполнитель подгонит проверку под то,
что получилось. Поэтому контракт каждого прибора задан здесь, снаружи, и проверяется
чёрным ящиком: только через командную строку, без импорта потрохов.

Роли этот файл НЕ ПРАВЯТ. Прибор обязан подстроиться под приёмку, не наоборот.

Сеть при приёмке заглушена подставным прокси: прибор, полезший в сеть на `--selftest`,
упрётся в отказ соединения. «Работает без сети» — проверяемое утверждение, а не обещание.

Выход: 0 — все приборы этапа 5 сданы; 1 — есть несданные (список с контрактами).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
# Любая попытка выйти в сеть упрётся в закрытый порт и упадёт быстро.
NO_NET = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}


def run(argv, cwd=ROOT, timeout=300, env=None):
    try:
        p = subprocess.run([sys.executable, *argv], cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env=env or NO_NET)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"таймаут {timeout} с"
    except OSError as e:
        return 127, "", str(e)


def tool(name):
    """Абсолютный путь прибора от ТЕКУЩЕГО SCRIPTS: selftest подменяет каталог,
    и относительный путь молча увёл бы запуск обратно в боевой scripts/."""
    return str(SCRIPTS / name)


def exists(name):
    return (SCRIPTS / name).is_file()


def missing(name, contract):
    return [(name, f"прибора нет. Контракт:\n{contract}")]


def selftest_clean(name):
    """Общее требование ко всем приборам: `--selftest` даёт 0 и не ходит в сеть."""
    code, out, err = run([tool(name), "--selftest"])
    if code != 0:
        return [(name, f"--selftest вернул {code}: {(out + err).strip()[-500:]}")]
    return []


# ── 1. Обезличивание, fail-closed, обратимое ────────────────────────────────
PII_CONTRACT = """  scripts/pii_gate.py
    --mask ВХОД --out ВЫХОД --map КАРТА.json
        заменяет ФИО, ИНН, ОГРН, номер дела, паспорт, адрес на устойчивые токены;
        пишет обратимую карту. Ничего не нашёл → код 1 и ВЫХОД НЕ СОЗДАЁТСЯ
        (fail-closed: пустая карта значит «не сработало», а не «чисто»).
    --unmask ВХОД --map КАРТА.json --out ВЫХОД
        восстанавливает исходный текст ОДИН В ОДИН, побайтово.
    --selftest"""


def check_pii():
    name = "pii_gate.py"
    if not exists(name):
        return missing(name, PII_CONTRACT)
    fails = selftest_clean(name)
    with tempfile.TemporaryDirectory(prefix="stage5-pii-") as tmp:
        t = Path(tmp)
        src = t / "vopros.md"
        original = (
            "Истец Кузнецова Мария Петровна, ИНН 771234567890, обратилась в суд.\n"
            "Дело № А65-12345/2026. Ответчик ООО «Ромашка», ОГРН 1157746123456.\n"
            "Паспорт 9203 456789, адрес: г. Казань, ул. Баумана, д. 5, кв. 12.\n"
        )
        src.write_text(original, encoding="utf-8")
        masked, mapf, back = t / "masked.md", t / "map.json", t / "back.md"

        code, out, err = run([tool(name), "--mask", str(src), "--out", str(masked),
                              "--map", str(mapf)])
        if code != 0 or not masked.is_file():
            fails.append((name, f"--mask вернул {code}, файл создан: {masked.is_file()}. "
                                f"{(out + err).strip()[-300:]}"))
            return fails

        m = masked.read_text(encoding="utf-8")
        leaked = [s for s in ("Кузнецова", "771234567890", "А65-12345/2026",
                              "1157746123456", "9203 456789", "Баумана")
                  if s in m]
        if leaked:
            fails.append((name, f"обезличенный текст СОДЕРЖИТ исходные данные: {leaked}"))

        code, out, err = run([tool(name), "--unmask", str(masked),
                              "--map", str(mapf), "--out", str(back)])
        if code != 0 or not back.is_file():
            fails.append((name, f"--unmask вернул {code}: {(out + err).strip()[-300:]}"))
        elif back.read_text(encoding="utf-8") != original:
            fails.append((name, "восстановление НЕ побайтовое — обратимость нарушена"))

        # Fail-closed: нечего обезличивать → вызов не уходит и выхода не появляется
        clean = t / "chisto.md"
        clean.write_text("Обзор практики по неустойке. Норм права достаточно.\n", encoding="utf-8")
        out_clean, map_clean = t / "clean_out.md", t / "clean_map.json"
        code, _, _ = run([tool(name), "--mask", str(clean), "--out", str(out_clean),
                          "--map", str(map_clean)])
        if code == 0:
            fails.append((name, "при пустой карте прибор вернул 0 — обезличивание не сработало, "
                                "а вызов был бы отпущен наружу"))
        if out_clean.exists():
            fails.append((name, "при пустой карте создан выходной файл — fail-closed нарушен"))
    return fails


# ── 2. Preflight бюджета ────────────────────────────────────────────────────
BUDGET_CONTRACT = """  scripts/budget_preflight.py
    --track FAST|FULL [--limit ДОЛЛАРЫ]
        код 0 — остатка лимита хватает на трек; код 3 — не хватает, FULL не стартует.
        Расход берётся прибором с диска (token_ledger), не самоотчётом.
    --selftest"""


def check_budget():
    name = "budget_preflight.py"
    if not exists(name):
        return missing(name, BUDGET_CONTRACT)
    fails = selftest_clean(name)
    code, out, err = run([tool(name), "--track", "FULL", "--limit", "0.01"])
    if code != 3:
        fails.append((name, f"при заведомо малом лимите вернул {code}, ожидался 3 "
                            f"(перерасход). {(out + err).strip()[-200:]}"))
    code, out, err = run([tool(name), "--track", "FAST", "--limit", "100000"])
    if code != 0:
        fails.append((name, f"при заведомо большом лимите вернул {code}, ожидался 0"))
    return fails


# ── 3. Независимая сверка расхода ───────────────────────────────────────────
AUDIT_CONTRACT = """  scripts/token_audit.py
    --json    {"total": целое, "money": число} — СВОЙ путь подсчёта, не вызов token_ledger
    --compare код 1, если расходится с token_ledger больше допуска (по умолчанию 2%)
    --selftest"""


def check_audit():
    name = "token_audit.py"
    if not exists(name):
        return missing(name, AUDIT_CONTRACT)
    fails = selftest_clean(name)
    code, out, err = run([tool(name), "--json"])
    if code != 0:
        fails.append((name, f"--json вернул {code}: {(out + err).strip()[-200:]}"))
        return fails
    try:
        d = json.loads(out)
    except ValueError:
        fails.append((name, f"--json выдал неразбираемое: {out.strip()[:200]}"))
        return fails
    for k in ("total", "money"):
        if k not in d:
            fails.append((name, f"--json без поля `{k}`"))
    src = (SCRIPTS / name).read_text(encoding="utf-8", errors="ignore")
    if "token_ledger" in src and "--compare" not in src:
        fails.append((name, "считает через token_ledger — это не независимая сверка, "
                            "а её пересказ; свой проход по session-JSONL обязателен"))
    return fails


# ── 4. superseded_by в логе уроков ──────────────────────────────────────────
LESSONS_CONTRACT = """  scripts/lessons_supersede.py
    --check   код 1, если урок отменён более новым, но не помечен `superseded_by`
    --mark СТАРЫЙ НОВЫЙ   проставить связку в knowledge/lessons-log.md
    --selftest"""


def check_lessons():
    name = "lessons_supersede.py"
    if not exists(name):
        return missing(name, LESSONS_CONTRACT)
    fails = selftest_clean(name)
    code, out, err = run([tool(name), "--check"])
    if code not in (0, 1):
        fails.append((name, f"--check вернул {code}; допустимы 0 (чисто) и 1 (находки)"))
    return fails


# ── 5. Разбор правок по структуре, а не по байтам ───────────────────────────
REDLINE_CONTRACT = """  scripts/redline_diff.py
    ДО.docx ПОСЛЕ.docx --json
        {"content": [...], "format": [...]} — что доверитель изменил ПО СМЫСЛУ
        (абзацы, формулировки) и ПО ФОРМАТУ (шрифт, поля, выравнивание).
        Байтовое сравнение не годится: оно говорит «отличается», а не «что именно».
    --selftest"""


def check_redline():
    name = "redline_diff.py"
    if not exists(name):
        return missing(name, REDLINE_CONTRACT)
    fails = selftest_clean(name)
    src = (SCRIPTS / name).read_text(encoding="utf-8", errors="ignore")
    if "filecmp" in src and "docx" not in src:
        fails.append((name, "сравнивает байты — контракт требует разбора по содержанию и формату"))
    return fails


# ── 6. Кадастровая проверка объекта ─────────────────────────────────────────
CADASTRE_CONTRACT = """  scripts/cadastre.py
    --check НОМЕР
        формат и контрольная структура кадастрового номера ЛОКАЛЬНО, без сети
        (AA:BB:CCCCCCC:DD). Код 0 — правдоподобен, 1 — заведомо неверен.
    --selftest"""


def check_cadastre():
    name = "cadastre.py"
    if not exists(name):
        return missing(name, CADASTRE_CONTRACT)
    fails = selftest_clean(name)
    good = ("16:50:011234:567", "77:01:0004042:1234")
    bad = ("16-50-011234-567", "не номер", "16:50:011234", "")
    for n in good:
        code, out, err = run([tool(name), "--check", n])
        if code != 0:
            fails.append((name, f"правдоподобный номер {n!r} отвергнут (код {code})"))
    for n in bad:
        code, out, err = run([tool(name), "--check", n])
        if code == 0:
            fails.append((name, f"заведомо неверный номер {n!r} принят"))
    return fails


CHECKS = (
    ("обезличивание fail-closed с обратимой картой", check_pii, PII_CONTRACT),
    ("preflight бюджета перед FULL", check_budget, BUDGET_CONTRACT),
    ("независимая сверка расхода", check_audit, AUDIT_CONTRACT),
    ("superseded_by в логе уроков", check_lessons, LESSONS_CONTRACT),
    ("разбор правок по структуре", check_redline, REDLINE_CONTRACT),
    ("кадастровая проверка объекта", check_cadastre, CADASTRE_CONTRACT),
)


def selftest():
    """Проверяет саму приёмку: заглушка сети стоит, отсутствующий прибор виден,
    подставной прибор с пустым обещанием не проходит."""
    assert NO_NET["HTTPS_PROXY"].endswith(":1"), "заглушка сети не выставлена"
    for title, fn, contract in CHECKS:
        assert contract.strip().startswith("scripts/"), f"{title}: контракт без имени файла"
    with tempfile.TemporaryDirectory(prefix="stage5-spec-selftest-") as tmp:
        # Подставной pii_gate, который «делает вид»: выход пишет всегда, карту не заполняет
        fake = Path(tmp) / "scripts"
        fake.mkdir()
        (fake / "pii_gate.py").write_text(
            "import sys, json\n"
            "a = sys.argv\n"
            "if '--selftest' in a: sys.exit(0)\n"
            "out = a[a.index('--out')+1]\n"
            "open(out,'w').write('что угодно')\n"
            "sys.exit(0)\n", encoding="utf-8")
        global SCRIPTS
        saved, SCRIPTS = SCRIPTS, fake
        try:
            fails = check_pii()
            texts = " ".join(t for _, t in fails)
            assert fails, "подставной обезличиватель принят приёмкой"
            assert "СОДЕРЖИТ исходные данные" in texts or "fail-closed" in texts, texts
        finally:
            SCRIPTS = saved
    print("selftest: заглушка сети, контракты названы, подставной прибор отбит — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Приёмка приборов этапа 5 (пишет координатор).")
    ap.add_argument("--contracts", action="store_true", help="напечатать контракты и выйти")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.contracts:
        for title, _, contract in CHECKS:
            print(f"\n{title}:\n{contract}")
        return 0

    all_fails, done = [], 0
    for title, fn, _ in CHECKS:
        fails = fn()
        if fails:
            all_fails.append((title, fails))
        else:
            done += 1
        print(f"  {'✓' if not fails else '✗'} {title}")
    print(f"\nсдано приборов: {done}/{len(CHECKS)}")
    if not all_fails:
        print("✓ ЭТАП 5 ПРИНЯТ")
        return 0
    print("\nчто не сдано:")
    for title, fails in all_fails:
        for name, why in fails:
            print(f"\n· {name} — {title}\n  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
