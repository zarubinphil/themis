#!/usr/bin/env python3
"""registry_check.py — сверка трёх реестров с тем, что реально лежит на диске.

Конституция обязывает модель руками держать три книги в согласии: `cases/_index.md`,
`cases/_clients.md` и `_client.md` каждого доверителя. Руками — значит с пропусками:
дело заводится, строка забывается, и следующая сессия дела не находит.

Скрипт не переписывает реестры (там курированные заметки, псевдонимы, предупреждения —
их сгенерировать нельзя), а называет расхождения. `--fix` дописывает недостающие строки
в конец таблицы заготовками, ничего не затирая.

    registry_check.py                 отчёт о расхождениях (код 1, если они есть)
    registry_check.py --fix           + дописать недостающие строки заготовками
    registry_check.py --selftest      проверка без сети и без диска проекта

ПД доверителей из реестров наружу не выводятся: в отчёте только имена папок.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SERVICE = re.compile(r"^[._]")  # _templates, _logs, _archive_*, .DS_Store — не дела
# Чужое, забредшее в папку доверителей. Это не «дело без строки в реестре», это мусор,
# и путать их нельзя: 10 МБ node_modules внутри cases/ реально нашлись 03.08.2026.
ALIEN = {"node_modules", "knowledge", "scripts", "venv", ".venv", "__pycache__", "bin", "docs"}
# Признак настоящей папки дела — рабочая структура протокола.
CASE_MARKS = ("00_intake", "01_context", "02_hearings", "03_drafts", "_case.md")


def looks_like_case(path: str) -> bool:
    return any(os.path.exists(os.path.join(path, m)) for m in CASE_MARKS)


def scan_disk(cases: str) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """{папка клиента: [папки дел]}, клиенты без _client.md, чужие папки."""
    clients: dict[str, list[str]] = {}
    no_profile: list[str] = []
    alien: list[str] = []
    for entry in sorted(os.listdir(cases)):
        client_dir = os.path.join(cases, entry)
        if not os.path.isdir(client_dir) or SERVICE.match(entry):
            continue
        if entry in ALIEN:
            alien.append(entry)
            continue
        cases_of = []
        for d in sorted(os.listdir(client_dir)):
            sub = os.path.join(client_dir, d)
            if not os.path.isdir(sub) or SERVICE.match(d):
                continue
            if d in ALIEN:
                alien.append(f"{entry}/{d}")
                continue
            if looks_like_case(sub):
                cases_of.append(d)
        clients[entry] = cases_of
        if not os.path.isfile(os.path.join(client_dir, "_client.md")):
            no_profile.append(entry)
    return clients, no_profile, alien


def rows_of(path: str) -> str:
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def check(cases: str) -> dict:
    clients, no_profile, alien = scan_disk(cases)
    index_txt = rows_of(os.path.join(cases, "_index.md"))
    clients_txt = rows_of(os.path.join(cases, "_clients.md"))

    # Ключи первой колонки таблицы реестра клиентов — точными значениями.
    clients_keys = {m.group(1).strip()
                    for m in re.finditer(r"(?m)^\|\s*([^|]+?)\s*\|", clients_txt)}
    missing_in_index, missing_in_clients = [], []
    for client, case_dirs in clients.items():
        # Раньше был подстрочный фолбэк «client not in clients_txt»: папка ivanov
        # «находилась» в строке про ivanov-petr, и пропуск клиента маскировался.
        # Ключ ищем только точной ячейкой таблицы.
        if client not in clients_keys:
            missing_in_clients.append(client)
        for c in case_dirs:
            if f"{client}/{c}" not in index_txt:
                missing_in_index.append(f"{client}/{c}")

    # обратная сторона: строка есть, папки нет — реестр показывает несуществующее
    ghost = []
    # Папка дела — latin-kebab и начинается с буквы. Иначе в «призраки» попадают
    # номера дел из соседней колонки: «2-1309/2026» синтаксически похож на путь.
    for m in re.finditer(r"\|\s*([a-z][a-z0-9-]*/[a-z][a-z0-9._-]*)\s*\|", index_txt):
        rel = m.group(1)
        if not os.path.isdir(os.path.join(cases, rel)):
            ghost.append(rel)

    return {"clients": clients, "no_profile": no_profile, "alien": alien,
            "missing_in_index": missing_in_index,
            "missing_in_clients": sorted(set(missing_in_clients)),
            "ghost": sorted(set(ghost))}


def append_rows(cases: str, res: dict) -> list[str]:
    """Дописать заготовки в конец таблиц. Только append, существующее не трогаем."""
    done = []
    if res["missing_in_index"]:
        path = os.path.join(cases, "_index.md")
        with open(path, "a", encoding="utf-8") as f:
            for rel in res["missing_in_index"]:
                client, case = rel.split("/", 1)
                f.write(f"| {client} | ЗАПОЛНИТЬ: предмет дела | {rel} | — | — | — | — | Активно |\n")
        done.append(f"_index.md: дописано {len(res['missing_in_index'])} строк-заготовок")
    if res["missing_in_clients"]:
        path = os.path.join(cases, "_clients.md")
        with open(path, "a", encoding="utf-8") as f:
            for client in res["missing_in_clients"]:
                f.write(f"| {client} | ЗАПОЛНИТЬ: ФИО | — | |\n")
        done.append(f"_clients.md: дописано {len(res['missing_in_clients'])} строк-заготовок")
    return done


def report(res: dict) -> int:
    total_cases = sum(len(v) for v in res["clients"].values())
    print(f"на диске: клиентов {len(res['clients'])}, дел {total_cases}")
    problems = 0
    for title, items, hint in [
        ("дел нет в _index.md", res["missing_in_index"], "строка на дело обязательна"),
        ("клиентов нет в _clients.md", res["missing_in_clients"], "маршрутизация /new-case сломается"),
        ("нет файла _client.md", res["no_profile"], "профиль доверителя обязателен"),
        ("строк без папки на диске", res["ghost"], "реестр показывает несуществующее дело"),
        ("чужие папки внутри cases/", res.get("alien", []),
         "код и зависимости в папке доверителей — вынести из cases/"),
    ]:
        if items:
            problems += len(items)
            print(f"\n⚠ {title} ({len(items)}) — {hint}:")
            for x in items[:30]:
                print(f"   • {x}")
            if len(items) > 30:
                print(f"   … и ещё {len(items) - 30}")
    if not problems:
        print("\nреестры сходятся с диском ✓")
    else:
        print(f"\nитого расхождений: {problems}")
    return 1 if problems else 0


def selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()
    cases = os.path.join(tmp, "cases")
    for rel in ["ivanov/dolg-2026/01_context", "petrov/razvod-2026/00_intake",
                "ivan/spor-2026/01_context",
                "_templates/x", "_logs/y", "node_modules/pako", "ivanov/node_modules"]:
        os.makedirs(os.path.join(cases, rel))
    open(os.path.join(cases, "ivanov", "_client.md"), "w").write("# профиль")
    open(os.path.join(cases, "_index.md"), "w", encoding="utf-8").write(
        "| Клиент | Дело | Папка |\n|---|---|---|\n"
        "| Иванов | Долг | ivanov/dolg-2026 | \n"
        "| Призрак | Нет такого | sidorov/net-2020 | \n")
    open(os.path.join(cases, "_clients.md"), "w", encoding="utf-8").write(
        "| Папка | ФИО |\n|---|---|\n| ivanov | Иванов |\n")
    open(os.path.join(cases, "ivan", "_client.md"), "w").write("# профиль")

    res = check(cases)
    checks = [
        ("служебные папки не считаются делами",
         set(res["clients"]) == {"ivanov", "petrov", "ivan"}),
        ("пропущенное дело найдено",
         res["missing_in_index"] == ["ivan/spor-2026", "petrov/razvod-2026"]),
        # Коллизия префикса: папка ivan НЕ покрыта строкой про ivanov.
        ("пропущенный клиент найден", res["missing_in_clients"] == ["ivan", "petrov"]),
        ("префикс чужой строки не засчитывается", "ivan" in res["missing_in_clients"]),
        ("клиент без профиля найден", res["no_profile"] == ["petrov"]),
        ("строка-призрак найдена", res["ghost"] == ["sidorov/net-2020"]),
        ("чужие папки отделены от дел",
         set(res["alien"]) == {"node_modules", "ivanov/node_modules"}),
    ]
    append_rows(cases, res)
    after = check(cases)
    checks.append(("после --fix пропусков нет",
                   not after["missing_in_index"] and not after["missing_in_clients"]))
    checks.append(("--fix ничего не затёр",
                   "Иванов" in rows_of(os.path.join(cases, "_index.md"))))
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Сверка реестров дел с диском")
    ap.add_argument("--cases", default="cases", help="папка cases/ (по умолчанию ./cases)")
    ap.add_argument("--fix", action="store_true", help="дописать недостающие строки заготовками")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not os.path.isdir(a.cases):
        print(f"нет папки {a.cases}", file=sys.stderr)
        return 2
    res = check(a.cases)
    rc = report(res)
    if a.fix:
        for line in append_rows(a.cases, res):
            print(f"\n✍ {line}")
        print("Заготовки помечены «ЗАПОЛНИТЬ» — дописать предмет и реквизиты руками либо агентом.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
