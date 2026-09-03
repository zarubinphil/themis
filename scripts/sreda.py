#!/usr/bin/env python3
"""sreda.py — переходный период имени семейства в переменных окружения.

Проект переименован 03.09.2026, и вместе с ним переименованы все переменные
окружения. Уже запущенное прежнее имя не забывает: launchd с прежней меткой,
чужие оболочки, каталог секретов, конфиг прошлой установки. Молчаливый отказ тут
хуже громкого — панель без токена просто не пустит юриста, а причина будет не
видна ни в одном логе.

Правило: новое имя читается первым, прежнее принимается запасным и печатает
предупреждение. Подключается одной строкой `import sreda` в начале прибора;
дальше весь код читает только THEMIZ_*.

    python3 scripts/sreda.py --selftest
"""
from __future__ import annotations

import os
import subprocess
import sys

NOVYJ = "THEMIZ_"
# Прежний префикс получается из нового заменой буквы, а не пишется литералом:
# массовая замена имени по дереву не должна съесть запасной путь вместе со всем
# остальным. Прибор, который чинит переименование, не может от него сломаться.
PREZHNIJ = NOVYJ.replace("Z_", "S_")


def perenos(okruzhenie=None) -> list:
    """Прежние имена в новые. Отдает список принятых прежних имен.

    Новое имя, если оно уже задано, не перетирается никогда: явно заданное
    сильнее унаследованного.
    """
    env = os.environ if okruzhenie is None else okruzhenie
    perenesli = []
    for imya in sorted(env):
        if not imya.startswith(PREZHNIJ):
            continue
        novoe = NOVYJ + imya[len(PREZHNIJ):]
        if novoe in env:
            continue
        env[novoe] = env[imya]
        perenesli.append(imya)
    return perenesli


def _predupredit(perenesli) -> None:
    if not perenesli:
        return
    try:
        print("предупреждение: прежние имена переменных приняты запасным путем: "
              + ", ".join(perenesli) + " — новые имена начинаются с " + NOVYJ,
              file=sys.stderr)
    except (OSError, ValueError):
        pass          # закрытый stderr фонового задания перенос не отменяет


def selftest() -> int:
    besedy = []

    proba = {PREZHNIJ + "CASE": "cases/ivanov/delo-2026"}
    if perenos(proba) != [PREZHNIJ + "CASE"]:
        besedy.append("прежнее имя не принято запасным путем")
    if proba.get(NOVYJ + "CASE") != "cases/ivanov/delo-2026":
        besedy.append("значение не доехало до нового имени")

    oba = {PREZHNIJ + "CASE": "staroe", NOVYJ + "CASE": "novoe"}
    if perenos(oba) != []:
        besedy.append("новое имя перетерто прежним")
    if oba[NOVYJ + "CASE"] != "novoe":
        besedy.append("явно заданное новое имя не выиграло у прежнего")

    chisto = {"PATH": "/usr/bin", NOVYJ + "HOME": "/tmp"}
    if perenos(chisto) != []:
        besedy.append("перенос сработал там, где прежних имен нет")

    # Живой прогон: сам импорт обязан перенести имя и сказать об этом в stderr.
    okr = dict(os.environ)
    okr[PREZHNIJ + "PROBA"] = "1"
    okr.pop(NOVYJ + "PROBA", None)
    kod = ("import sreda, os; "
           "print(os.environ.get('" + NOVYJ + "PROBA', '__net__'))")
    r = subprocess.run([sys.executable, "-c", kod], capture_output=True, text=True,
                       env=okr, cwd=os.path.dirname(os.path.abspath(__file__)))
    if r.stdout.strip() != "1":
        besedy.append("импорт не перенес имя: " + repr(r.stdout) + " " + r.stderr[-200:])
    if PREZHNIJ + "PROBA" not in r.stderr:
        besedy.append("импорт перенес имя молча — предупреждения нет")

    # Пара в оболочке проверяется здесь же: прибор один, значит и проверка одна.
    # Три вопроса разом — доехало ли прежнее имя; выигрывает ли явно заданное
    # новое, даже когда значение выглядит служебным; не рождается ли переменная
    # из значения соседа, внутри которого перевод строки и похожая на имя строка.
    ryadom = os.path.dirname(os.path.abspath(__file__))
    okr_sh = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
              PREZHNIJ + "CASE": "cases/ivanov/delo",
              PREZHNIJ + "YES": "staroe", NOVYJ + "YES": "__net__",
              PREZHNIJ + "MNOGO": "hvost\n" + PREZHNIJ + "PODLOG=1"}
    zapros = ('. ./sreda.sh; echo "${%sCASE-net}|${%sYES-net}|${%sPODLOG-net}"'
              % (NOVYJ, NOVYJ, NOVYJ))
    r = subprocess.run(["bash", "-c", zapros], capture_output=True, text=True,
                       env=okr_sh, cwd=ryadom)
    if r.stdout.strip() != "cases/ivanov/delo|__net__|net":
        besedy.append("слой оболочки: " + repr(r.stdout.strip()) + " " + r.stderr[-200:])

    for beda in besedy:
        print("✗ " + beda, file=sys.stderr)
    if besedy:
        return 1
    print("✓ переходный период имен: 9 проверок (питон и оболочка)")
    return 0


_predupredit(perenos())

if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv else selftest())
