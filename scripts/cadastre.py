#!/usr/bin/env python3
"""cadastre.py — локальная проверка кадастрового номера объекта, без сети.

Зачем. Кадастровый номер приходит из документов дела (ЕГРН, договоры, решения) и
часто через OCR, который путает цифры и разделители. Прежде чем номер попадёт в
карту дела и в требования, дешёвая локальная проверка отсекает заведомо
испорченный: неверный разделитель, не то число блоков, нереальный округ. Это не
подтверждение существования объекта (для этого нужен Росреестр по сети) — это
отсев мусора до обращения к платному/сетевому источнику.

Контрольная структура, а не контрольная сумма. У ИНН и ОГРН есть контрольная
цифра — у кадастрового номера её нет. «Контроль» здесь структурный: формат
`округ:район:квартал:объект` = `AA:BB:CCCCCCC:DD`, где округ 01-91 (столько
кадастровых округов в РФ, 90 — Крым, 91 — Севастополь), квартал 6-7 цифр, объект
не ноль. Номер, нарушивший эту структуру, заведомо неверен; прошедший её —
правдоподобен и достоин сетевой сверки.
"""
from __future__ import annotations

import argparse
import re
import sys

# округ(2):район(2):квартал(6-7):объект(1-7). Разделитель — только двоеточие.
CADASTRE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{6,7}):(\d{1,7})$")
OKRUG_MAX = 91  # кадастровых округов в РФ: 01-91 (91 — Севастополь)


def plausible(number: str) -> tuple[bool, str]:
    """(правдоподобен ли, причина отказа). Причина пуста при успехе."""
    num = (number or "").strip()
    m = CADASTRE_RE.match(num)
    if not m:
        return False, "не формат AA:BB:CCCCCCC:DD (округ:район:квартал:объект через двоеточие)"
    okrug = int(m.group(1))
    if not (1 <= okrug <= OKRUG_MAX):
        return False, f"округ {okrug:02d} вне диапазона 01-{OKRUG_MAX}"
    if int(m.group(4)) == 0:
        return False, "номер объекта не может быть нулём"
    return True, ""


def cmd_check(number: str) -> int:
    ok, why = plausible(number)
    if ok:
        print(f"cadastre: {number.strip()} — правдоподобен (структура верна)")
        return 0
    print(f"cadastre: {number.strip()!r} — заведомо неверен: {why}", file=sys.stderr)
    return 1


def selftest() -> int:
    good = ("16:50:011234:567", "77:01:0004042:1234",
            " 16:50:011234:567 ",           # пробелы по краям срезаются
            "90:01:0011223:44")             # округ 90 (Крым) — в диапазоне
    bad = ("16-50-011234-567",              # дефисы вместо двоеточий
           "не номер", "16:50:011234", "",  # мусор, три блока, пусто
           "00:50:011234:567",              # округ 00
           "92:01:0004042:1234",            # округ 92 > 91
           "16:50:01123:567",               # квартал 5 цифр
           "16:50:011234:0")                # объект ноль
    checks = [(f"правдоподобен {n!r}", plausible(n)[0]) for n in good]
    checks += [(f"отвергнут {n!r}", not plausible(n)[0]) for n in bad]
    bad_names = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    if bad_names:
        print(f"selftest ПРОВАЛЕН: {len(bad_names)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)} — структурная проверка без сети")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Локальная проверка кадастрового номера")
    ap.add_argument("--check", metavar="НОМЕР", help="проверить номер (exit 1 при заведомо неверном)")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.check is not None:  # именно is not None: пустая строка — валидный вход, её тоже проверяем
        return cmd_check(a.check)
    ap.error("нужен --check НОМЕР или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
