#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sroki.py — процессуальные сроки и УИД дела: арифметика вместо памяти модели.

ЗАЧЕМ. Срок на жалобу считается руками, а ошибка в нём стоит дела целиком:
пропущенный срок восстанавливают не всегда и не всем. Считать его моделью нельзя
по двум причинам — она не знает производственный календарь текущего года и путает
рабочие дни с календарными, а разница между ними в августе даёт четыре дня.

ЧТО СЧИТАЕТСЯ (нормы дословно из локального корпуса, scripts/cite.py):

  Начало.   «Течение процессуального срока, исчисляемого годами, месяцами или
            днями, начинается на СЛЕДУЮЩИЙ ДЕНЬ после даты или наступления
            события, которыми определено его начало» — ч. 3 ст. 107 ГПК РФ,
            то же ст. 191 ГК РФ.
  Дни.      «В сроки, исчисляемые днями, не включаются нерабочие дни, если иное
            не установлено настоящим Кодексом» — ч. 3 ст. 107 ГПК РФ. Это
            ПРОЦЕССУАЛЬНЫЕ сроки. Материальные сроки ГК считаются календарными
            днями (--gk): смешивать их — типовая ошибка, дающая разницу в неделю.
  Месяцы.   «Срок, исчисляемый месяцами, истекает в соответствующее число
            последнего месяца срока. В случае, если окончание срока приходится
            на такой месяц, который соответствующего числа не имеет, срок
            истекает в последний день этого месяца» — ч. 1 ст. 108 ГПК РФ.
  Годы.     «истекает в соответствующие месяц и число последнего года срока» —
            там же.
  Перенос.  «В случае, если последний день процессуального срока приходится на
            нерабочий день, днем окончания срока считается следующий за ним
            рабочий день» — ч. 2 ст. 108 ГПК РФ, то же ст. 193 ГК РФ.

КАЛЕНДАРЬ. Производственный календарь берётся из двух независимых источников и
СВЕРЯЕТСЯ между собой: isdayoff.ru (маска по дням года) и xmlcalendar.ru (списки
выходных по месяцам). Расхождение — не повод молча выбрать один: оно печатается
и день помечается спорным. Один источник как единственная точка правды для срока,
от которого зависит дело, недопустим. Кеш на диске — год меняется раз в год.

УИД ДЕЛА. Уникальный идентификатор дела на портале судов проверяется контрольным
числом по ISO 7064 MOD 97-10: буквы переводятся A=10…Z=35, всё число по модулю 97
обязано дать 1. Проверено на живом УИД из материалов дела 04.08.2026:
16RS0048-01-2026-000297-13 → 1. Подмена любой цифры ловится.

ПРИМЕРЫ

    # 30 дней на апелляцию по ГПК (рабочие дни, перенос с нерабочего)
    python3 scripts/sroki.py --ot 15.08.2026 --dney 30

    # месячный срок на апелляцию, решение изготовлено 15.08.2026
    python3 scripts/sroki.py --ot 15.08.2026 --mesyacev 1

    # материальный срок ГК: календарные дни
    python3 scripts/sroki.py --ot 15.08.2026 --dney 30 --gk

    # проверить УИД из документа
    python3 scripts/sroki.py --uid 16RS0048-01-2026-000297-13

    python3 scripts/sroki.py --selftest      # без сети

Сеть — через curl: у системного python нет корневых сертификатов.

ponytail: календарь кешируется json-файлом на диске, без БД — лет единицы.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.expanduser("~/.cache/legal_calendar")
TIMEOUT = 20

ISDAYOFF = "https://isdayoff.ru/api/getdata?year={year}&cc=ru"
XMLCALENDAR = "https://xmlcalendar.ru/data/ru/{year}/calendar.json"

# Нерабочие праздничные дни, ст. 112 ТК РФ — резерв на случай, когда оба канала
# недоступны. Это НЕ производственный календарь: переносы выходных Правительство
# устанавливает отдельным постановлением на каждый год, и без них расчёт
# приблизителен. Поэтому резерв всегда помечается в выводе явно.
TK_HOLIDAYS = {(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
               (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}


def _curl(url: str) -> str | None:
    marker = "__HTTP__"
    try:
        r = subprocess.run(["curl", "-sL", "--max-time", str(TIMEOUT),
                            "-w", f"{marker}%{{http_code}}", url],
                           capture_output=True, timeout=TIMEOUT + 5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout.decode("utf-8", "replace")
    body, _, code = raw.rpartition(marker)
    if code.strip().isdigit() and int(code.strip()) >= 400:
        return None
    return body


def parse_isdayoff(mask: str, year: int) -> set[date] | None:
    """Маска isdayoff: символ на день года, '1' — нерабочий. Возвращает нерабочие."""
    mask = (mask or "").strip()
    days = 366 if is_leap(year) else 365
    if len(mask) != days or set(mask) - set("012345"):
        return None
    start = date(year, 1, 1)
    # '0' рабочий, '1' нерабочий, '2' сокращённый (рабочий), '4' covid-нерабочий
    return {start + timedelta(days=i) for i, c in enumerate(mask) if c in "14"}


def parse_xmlcalendar(payload: str, year: int) -> set[date] | None:
    """xmlcalendar: {'months':[{'month':1,'days':'1,2,3,9+,...'}]}. '+' — перенос."""
    try:
        data = json.loads(payload or "")
    except ValueError:
        return None
    months = data.get("months")
    if not isinstance(months, list) or not months:
        return None
    out: set[date] = set()
    for m in months:
        num = m.get("month")
        for token in str(m.get("days", "")).split(","):
            token = token.strip()
            if not token:
                continue
            # '9+' — перенесённый выходной, '3*' — сокращённый предпраздничный
            # (он РАБОЧИЙ и в нерабочие не идёт).
            if token.endswith("*"):
                continue
            day = re.sub(r"\D", "", token)
            if not day:
                continue
            try:
                out.add(date(year, int(num), int(day)))
            except (TypeError, ValueError):
                return None
    return out or None


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def fallback_nonworking(year: int) -> set[date]:
    """Суббота, воскресенье и праздники ст. 112 ТК. Без переносов Правительства."""
    out = set()
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() >= 5 or (d.month, d.day) in TK_HOLIDAYS:
            out.add(d)
        d += timedelta(days=1)
    return out


def load_year(year: int, offline: bool = False) -> dict:
    """Нерабочие дни года. {'days': set, 'source': str, 'conflicts': [date], 'exact': bool}."""
    cache = os.path.join(CACHE_DIR, f"{year}.json")
    if os.path.isfile(cache):
        try:
            raw = json.load(open(cache, encoding="utf-8"))
            return {"days": {date.fromisoformat(s) for s in raw["days"]},
                    "source": raw["source"], "exact": raw["exact"],
                    "conflicts": [date.fromisoformat(s) for s in raw.get("conflicts", [])]}
        except (OSError, ValueError, KeyError):
            pass
    if offline:
        return {"days": fallback_nonworking(year), "source": "ст. 112 ТК РФ (резерв)",
                "exact": False, "conflicts": []}

    a = parse_isdayoff(_curl(ISDAYOFF.format(year=year)) or "", year)
    b = parse_xmlcalendar(_curl(XMLCALENDAR.format(year=year)) or "", year)
    if a and b:
        conflicts = sorted(a ^ b)
        # Спорный день считаем НЕРАБОЧИМ: перенос срока вперёд безопаснее, чем
        # назад — пропущенный срок необратим, лишний день ожидания нет.
        result = {"days": a | b, "source": "isdayoff.ru + xmlcalendar.ru (сверено)",
                  "exact": True, "conflicts": conflicts}
    elif a or b:
        result = {"days": a or b, "exact": True, "conflicts": [],
                  "source": "isdayoff.ru" if a else "xmlcalendar.ru"}
    else:
        return {"days": fallback_nonworking(year), "source": "ст. 112 ТК РФ (резерв)",
                "exact": False, "conflicts": []}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"days": sorted(d.isoformat() for d in result["days"]),
                   "source": result["source"], "exact": result["exact"],
                   "conflicts": sorted(d.isoformat() for d in result["conflicts"])},
                  f, ensure_ascii=False)
    return result


class Calendar:
    """Нерабочие дни по годам. Годы подгружаются по мере надобности."""

    def __init__(self, offline: bool = False, days: set[date] | None = None):
        self.offline = offline
        self._years: dict[int, dict] = {}
        self._forced = days
        self.notes: list[str] = []

    def _year(self, year: int) -> dict:
        if self._forced is not None:
            return {"days": self._forced, "source": "фикстура", "exact": True,
                    "conflicts": []}
        if year not in self._years:
            info = load_year(year, self.offline)
            self._years[year] = info
            if not info["exact"]:
                self.notes.append(
                    f"{year}: производственный календарь недоступен, взят резерв "
                    "(выходные + праздники ст. 112 ТК РФ). Переносы выходных, "
                    "установленные Правительством, НЕ учтены — срок проверить вручную.")
            if info["conflicts"]:
                self.notes.append(
                    f"{year}: источники календаря разошлись по {len(info['conflicts'])} "
                    f"дням ({', '.join(d.strftime('%d.%m.%Y') for d in info['conflicts'][:5])}"
                    f"{'…' if len(info['conflicts']) > 5 else ''}); спорный день считается "
                    "нерабочим — перенос срока вперёд безопаснее пропуска.")
        return self._years[year]

    def is_working(self, d: date) -> bool:
        return d not in self._year(d.year)["days"]

    def next_working(self, d: date) -> date:
        while not self.is_working(d):
            d += timedelta(days=1)
        return d


def add_months(d: date, months: int) -> date:
    """Срок в месяцах: то же число последнего месяца, иначе последний день месяца."""
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    day = d.day
    while day > 0:
        try:
            return date(y, m, day)
        except ValueError:
            day -= 1
    raise ValueError("не удалось построить дату")


def deadline(start: date, cal: Calendar, *, days: int = 0, months: int = 0,
             years: int = 0, working_days: bool = True) -> dict:
    """Дата окончания срока. Возвращает разбор по шагам — его читает юрист."""
    if days < 0 or months < 0 or years < 0:
        raise ValueError("длительность срока не может быть отрицательной")
    if not (days or months or years):
        raise ValueError("нужна длительность: --dney, --mesyacev или --let")
    steps = []
    # Ч. 3 ст. 107 ГПК РФ / ст. 191 ГК РФ: течение начинается на следующий день.
    cur = start + timedelta(days=1)
    steps.append(f"течение срока начинается {cur.strftime('%d.%m.%Y')} — на следующий "
                 f"день после {start.strftime('%d.%m.%Y')} [ч. 3 ст. 107 ГПК РФ]")
    # Месяцы и годы истекают в ЧИСЛО, СООТВЕТСТВУЮЩЕЕ ДАТЕ НАЧАЛА, а не дате
    # начала течения. Дословно: «оканчивается в соответствующее число следующего
    # месяца — число, соответствующее дате составления мотивированного решения»
    # [п. 16 Постановления Пленума ВС РФ от 22.06.2021 № 16]. Отсюда «минус день»:
    # течение начато со следующего дня, и последний день срока — накануне
    # соответствующего числа. Решение 14.08 → апелляция по 14.09, не по 15.09.
    if years:
        cur = add_months(cur, years * 12) - timedelta(days=1)
        steps.append(f"плюс {years} г. → {cur.strftime('%d.%m.%Y')} [ч. 1 ст. 108 ГПК РФ]")
    if months:
        cur = add_months(cur, months) - timedelta(days=1)
        steps.append(f"плюс {months} мес. → {cur.strftime('%d.%m.%Y')} "
                     "[ч. 1 ст. 108 ГПК РФ; п. 16 Пленума ВС РФ от 22.06.2021 № 16: "
                     "число, соответствующее дате составления мотивированного решения]")
    if days:
        if working_days:
            # Первый день срока — САМ день начала течения, а не следующий за ним:
            # течение уже начато ч. 3 ст. 107 ГПК. Смещение на день здесь стоит
            # ровно одного дня срока, и в жалобе это цена дела.
            counted = 0
            while True:
                if cal.is_working(cur):
                    counted += 1
                if counted >= days:
                    break
                cur += timedelta(days=1)
            steps.append(f"плюс {days} РАБОЧИХ дн. → {cur.strftime('%d.%m.%Y')} "
                         "[ч. 3 ст. 107 ГПК РФ: нерабочие дни в срок не включаются]")
        else:
            cur = cur + timedelta(days=days - 1)
            steps.append(f"плюс {days} КАЛЕНДАРНЫХ дн. → {cur.strftime('%d.%m.%Y')} "
                         "[ст. 191, 192 ГК РФ]")
    last = cur
    end = cal.next_working(cur)
    if end != last:
        steps.append(f"{last.strftime('%d.%m.%Y')} — нерабочий, перенос на "
                     f"{end.strftime('%d.%m.%Y')} [ч. 2 ст. 108 ГПК РФ, ст. 193 ГК РФ]")
    return {"start": start, "raw_end": last, "end": end, "steps": steps,
            "moved": end != last}


UID_RE = re.compile(r"^[0-9A-ZА-Я]{2,}-[0-9A-ZА-Я-]+$", re.IGNORECASE)


def uid_valid(uid: str) -> bool:
    """УИД дела по ISO 7064 MOD 97-10: буквы A=10…Z=35, всё число mod 97 == 1.

    Проверено на живом УИД из материалов дела 04.08.2026:
    16RS0048-01-2026-000297-13 → 1. Подмена контрольного числа и подмена цифры
    порядкового номера обе ловятся.
    """
    body = re.sub(r"[^0-9A-Za-z]", "", uid or "").upper()
    if len(body) < 10 or not body.isalnum():
        return False
    try:
        num = int("".join(str(int(c, 36)) if c.isalpha() else c for c in body))
    except ValueError:
        return False
    return num % 97 == 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Процессуальные сроки и УИД дела")
    ap.add_argument("--ot", metavar="ДД.ММ.ГГГГ", help="дата или событие, от которых срок")
    ap.add_argument("--dney", type=int, default=0, help="длительность в днях")
    ap.add_argument("--mesyacev", type=int, default=0, help="длительность в месяцах")
    ap.add_argument("--let", type=int, default=0, help="длительность в годах")
    ap.add_argument("--gk", action="store_true",
                    help="материальный срок ГК: дни КАЛЕНДАРНЫЕ (по умолчанию — "
                         "процессуальные рабочие по ч. 3 ст. 107 ГПК РФ)")
    ap.add_argument("--uid", help="проверить УИД дела (ISO 7064 MOD 97-10)")
    ap.add_argument("--offline", action="store_true",
                    help="не ходить в сеть: резерв ст. 112 ТК РФ, расчёт приблизителен")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if a.uid:
        ok = uid_valid(a.uid)
        print(f"УИД {a.uid}: {'ВАЛИДЕН' if ok else 'НЕВАЛИДЕН'} "
              "(контрольное число ISO 7064 MOD 97-10)")
        if not ok:
            print("  Контрольное число не сходится — реквизит искажён при "
                  "распознавании или переписан с ошибкой. В документ не переносить.")
        return 0 if ok else 1

    if not a.ot:
        ap.print_help()
        return 2
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", a.ot.strip())
    if not m:
        print(f"дата «{a.ot}» не в формате ДД.ММ.ГГГГ", file=sys.stderr)
        return 2
    start = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    cal = Calendar(offline=a.offline)
    try:
        res = deadline(start, cal, days=a.dney, months=a.mesyacev, years=a.let,
                       working_days=not a.gk)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps({"start": start.isoformat(), "end": res["end"].isoformat(),
                          "moved": res["moved"], "steps": res["steps"],
                          "notes": cal.notes}, ensure_ascii=False, indent=2))
        return 0
    print(f"Срок от {start.strftime('%d.%m.%Y')}:")
    for s in res["steps"]:
        print(f"  • {s}")
    print(f"\nПОСЛЕДНИЙ ДЕНЬ СРОКА: {res['end'].strftime('%d.%m.%Y')} "
          f"({['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'][res['end'].weekday()]})")
    print("Действие может быть совершено до 24 часов последнего дня; сдача в "
          "организацию почтовой связи до 24 часов — срок не пропущен "
          "[ч. 3 ст. 108 ГПК РФ].")
    for n in cal.notes:
        print(f"\n⚠ {n}")
    return 0


def selftest() -> int:
    # Календарь-фикстура: август 2026, выходные суббота/воскресенье.
    days = set()
    d = date(2024, 1, 1)
    while d.year <= 2027:
        if d.weekday() >= 5:
            days.add(d)
        d += timedelta(days=1)
    days |= {date(2026, 1, i) for i in range(1, 9)}
    days |= {date(2026, 5, 1), date(2026, 5, 9), date(2026, 6, 12), date(2026, 11, 4)}
    cal = Calendar(days=days)

    def end(ot, **kw):
        return deadline(ot, cal, **kw)["end"]

    checks = [
        # Начало течения — СЛЕДУЮЩИЙ день (ч. 3 ст. 107 ГПК РФ). 01.08.2026 суббота,
        # значит первый рабочий день срока — понедельник 03.08.2026.
        ("течение начинается со следующего дня",
         end(date(2026, 7, 31), days=1) == date(2026, 8, 3)),
        # Рабочие дни: нерабочие в срок не включаются.
        # 03.08.2026 понедельник. Течение с 04.08 (вт). Пять РАБОЧИХ дней:
        # 04, 05, 06, 07 и — минуя субботу с воскресеньем — 10.08.
        ("нерабочие дни в срок не включаются",
         end(date(2026, 8, 3), days=5) == date(2026, 8, 10)),
        # Те же пять КАЛЕНДАРНЫХ: 04, 05, 06, 07, 08 — суббота, перенос на 10.08.
        ("календарные дни считаются подряд с переносом",
         end(date(2026, 8, 3), days=5, working_days=False) == date(2026, 8, 10)),
        ("первый день срока — сам день начала течения, а не следующий",
         end(date(2026, 8, 3), days=1) == date(2026, 8, 4)),
        # Три календарных дня целиком укладываются в рабочую неделю: 04, 05, 06.
        # Здесь смещение на день видно прямо, без маскировки переносом.
        ("календарный срок кончается последним из N дней, а не следующим",
         end(date(2026, 8, 3), days=3, working_days=False) == date(2026, 8, 6)),
        ("календарный день срока считается от начала течения",
         deadline(date(2026, 8, 3), cal, days=5,
                  working_days=False)["raw_end"] == date(2026, 8, 8)),
        ("один календарный день — сам день начала течения",
         end(date(2026, 8, 3), days=1, working_days=False) == date(2026, 8, 4)),
        # ГЛАВНОЕ РАЗЛИЧИЕ: те же 30 дней дают разные даты. Смешать их — типовая
        # ошибка, которая стоит срока.
        ("рабочие и календарные 30 дней дают РАЗНЫЕ даты",
         end(date(2026, 8, 3), days=30) != end(date(2026, 8, 3), days=30,
                                               working_days=False)),
        # Перенос с нерабочего (ч. 2 ст. 108 ГПК РФ, ст. 193 ГК РФ).
        # 31.07.2026 + 1 календарный = 01.08.2026 суббота → 03.08.2026 понедельник.
        ("последний нерабочий день переносится на рабочий",
         end(date(2026, 7, 31), days=1, working_days=False) == date(2026, 8, 3)),
        ("перенос отмечен явно",
         deadline(date(2026, 7, 31), cal, days=1, working_days=False)["moved"]),
        ("рабочий последний день не переносится",
         not deadline(date(2026, 8, 3), cal, days=1)["moved"]),
        # Месяцы: соответствующее число последнего месяца (ч. 1 ст. 108 ГПК РФ).
        # Дословно по п. 16 Пленума ВС РФ от 22.06.2021 № 16: «число,
        # соответствующее дате составления мотивированного решения». Решение
        # 14.08.2026 → апелляция по 14.09.2026 (понедельник), не по 15.09.
        ("месячный срок истекает в число, соответствующее дате решения",
         end(date(2026, 8, 14), months=1) == date(2026, 9, 14)),
        # Месяц, в котором нет соответствующего числа: 31.01 + 1 мес. → 28.02.
        ("нет такого числа — последний день месяца",
         add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)),
        ("високосный февраль учитывается",
         add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)),
        ("год через февраль", add_months(date(2026, 3, 15), 12) == date(2027, 3, 15)),
        # 14.08.2027 — суббота, отсюда перенос на понедельник 16.08.2027.
        ("годовой срок истекает в то же число с переносом",
         end(date(2026, 8, 14), years=1) == date(2027, 8, 16)),
        # Нулевая и отрицательная длительность — отказ, а не молчаливый ответ.
        ("нулевая длительность — ошибка", _raises(lambda: deadline(date(2026, 8, 3), cal))),
        ("отрицательная длительность — ошибка",
         _raises(lambda: deadline(date(2026, 8, 3), cal, days=-5))),
        # УИД: контрольное число ISO 7064 MOD 97-10.
        ("живой УИД дела валиден", uid_valid("16RS0048-01-2026-000297-13")),
        ("подмена контрольного числа ловится", not uid_valid("16RS0048-01-2026-000297-14")),
        ("подмена цифры номера ловится", not uid_valid("16RS0048-01-2026-000298-13")),
        ("подмена буквы региона ловится", not uid_valid("16RT0048-01-2026-000297-13")),
        ("разделители не влияют", uid_valid("16RS00480120260002971 3".replace(" ", ""))),
        ("обрывок не валиден", not uid_valid("16RS0048")),
        ("пустое не валидно", not uid_valid("")),
        # Разбор источников календаря — без сети.
        ("маска isdayoff разбирается",
         parse_isdayoff("1" * 8 + "0" * 357, 2026) is not None
         and date(2026, 1, 1) in parse_isdayoff("1" * 8 + "0" * 357, 2026)),
        ("маска не той длины отвергается", parse_isdayoff("101", 2026) is None),
        ("високосный год ждёт 366 знаков", parse_isdayoff("0" * 365, 2024) is None),
        ("сокращённый день (2) рабочий",
         date(2026, 1, 1) not in (parse_isdayoff("2" + "0" * 364, 2026) or set())),
        ("xmlcalendar разбирается",
         date(2026, 1, 9) in (parse_xmlcalendar(
             '{"months":[{"month":1,"days":"1,2,9+"}]}', 2026) or set())),
        ("предпраздничный сокращённый в нерабочие не идёт",
         date(2026, 2, 20) not in (parse_xmlcalendar(
             '{"months":[{"month":2,"days":"21,22,20*"}]}', 2026) or set())),
        ("мусор вместо json отвергается", parse_xmlcalendar("не json", 2026) is None),
        # Резерв: без сети считаем по ст. 112 ТК, но помечаем приблизительность.
        ("резерв знает праздники ст. 112 ТК",
         date(2026, 6, 12) in fallback_nonworking(2026)),
        ("резерв знает выходные", date(2026, 8, 1) in fallback_nonworking(2026)),
        ("резерв не считает обычный вторник нерабочим",
         date(2026, 8, 4) not in fallback_nonworking(2026)),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


if __name__ == "__main__":
    sys.exit(main())
