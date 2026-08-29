#!/usr/bin/env python3
"""calc395.py — проценты по ст. 395 ГК РФ и сумма прописью, из официальных данных.

ЗАЧЕМ. Расчет по ст. 395 в проекте делался руками — то есть с ошибками, и каждая
из них стоит либо отказа в части иска, либо возражения ответчика. Ставка меняется
по нескольку раз в год, период дробится, и число дней в году зависит от високосности.
Считать это моделью — та же арифметика, только дороже и без воспроизводимости.

ИСТОЧНИК. Ключевая ставка — Банк России, `DailyInfoWebServ/DailyInfo.asmx`,
метод `KeyRateXML` (официальный веб-сервис, без ключа, в белом списке проекта).
Кеш на 7 суток: ставка меняется решением Совета директоров, не ежедневно.

НОРМА (дословно, ст. 395 ГК РФ из локального корпуса):
  п. 1 «Размер процентов определяется ключевой ставкой Банка России, действовавшей
  в соответствующие периоды.»
  п. 3 «Проценты за пользование чужими средствами взимаются по день уплаты суммы
  этих средств кредитору…»
Формула — п. 39 Постановления Пленума ВС РФ от 24.03.2016 № 7: сумма долга умножается
на ставку периода и на число дней периода, делится на число дней в году (365 либо 366
в високосном). Периоды с разной ставкой считаются отдельно и складываются.

    calc395.py --dolg 450000 --s 15.03.2025 --po 04.08.2026
    calc395.py --dolg 450000 --s 15.03.2025 --po 04.08.2026 --md   # таблица в документ
    calc395.py --propisyu 327120.50
    calc395.py --stavka                      # ключевая ставка на сегодня
    calc395.py --selftest                    # проверка без сети

Код возврата: 0 — посчитано; 1 — не хватает данных (нет ставок на период, нет сети).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache", "cbr")
CACHE_TTL = 7 * 86400
SOAP_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
FIRST_KEY_RATE = dt.date(2013, 9, 13)   # ключевая ставка введена 13.09.2013


def _cache_path(name: str) -> str:
    return os.path.join(CACHE, name + ".json")


def _cache_get(name: str):
    p = _cache_path(name)
    if os.path.exists(p) and (dt.datetime.now().timestamp() - os.path.getmtime(p)) < CACHE_TTL:
        try:
            return json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def _cache_put(name: str, value) -> None:
    os.makedirs(CACHE, exist_ok=True)
    try:
        json.dump(value, open(_cache_path(name), "w", encoding="utf-8"), ensure_ascii=False)
    except OSError as e:
        print(f"ВНИМАНИЕ: кеш не записан ({e})", file=sys.stderr)


def fetch_key_rates(since: dt.date, until: dt.date) -> list[tuple[dt.date, float]]:
    """История ключевой ставки: [(дата, ставка), …] по возрастанию даты."""
    name = f"keyrate_{since:%Y%m%d}_{until:%Y%m%d}"
    cached = _cache_get(name)
    if cached:
        return [(dt.date.fromisoformat(d), r) for d, r in cached]

    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        '<KeyRateXML xmlns="http://web.cbr.ru/">'
        f"<fromDate>{since:%Y-%m-%d}</fromDate><ToDate>{until:%Y-%m-%d}</ToDate>"
        "</KeyRateXML></soap:Body></soap:Envelope>")
    try:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "40", "-X", "POST", SOAP_URL,
             "-H", "Content-Type: text/xml; charset=utf-8",
             "-H", 'SOAPAction: "http://web.cbr.ru/KeyRateXML"',
             "--data", envelope],
            capture_output=True, timeout=50)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ЦБ РФ недоступен ({e}) — расчет невозможен, повторить позже", file=sys.stderr)
        return []
    body = r.stdout.decode("utf-8", "replace")
    rows = parse_key_rates(body)
    if rows:
        _cache_put(name, [(d.isoformat(), v) for d, v in rows])
    else:
        print("ЦБ РФ ответил, но ставок в ответе нет — не кеширую, проверить канал",
              file=sys.stderr)
    return rows


def parse_key_rates(xml: str) -> list[tuple[dt.date, float]]:
    """Разобрать ответ KeyRateXML. Вынесено отдельно, чтобы проверять без сети."""
    out = []
    for m in re.finditer(r"<DT>(\d{4})-(\d{2})-(\d{2})[^<]*</DT>\s*<Rate>([\d.,]+)</Rate>", xml):
        y, mo, d, rate = m.groups()
        out.append((dt.date(int(y), int(mo), int(d)), float(rate.replace(",", "."))))
    out.sort()
    return out


def daily_series(rates: list[tuple[dt.date, float]], since: dt.date,
                 until: dt.date) -> list[tuple[dt.date, float]]:
    """Непрерывный ряд ставок на КАЖДЫЙ день периода.

    ЦБ отдает ставку только по рабочим дням, но действует она непрерывно — до
    решения Совета директоров об изменении. Прямой разбор ответа терял субботы,
    воскресенья и праздники: на периоде 15.03.2025-04.08.2026 из расчета выпадало
    больше сотни дней, и проценты занижались. Тянем последнее известное значение
    вперед (forward-fill).
    """
    known = dict(rates)
    prior = [v for d, v in sorted(rates) if d <= since]
    cur = prior[-1] if prior else None
    out = []
    day = since
    while day <= until:
        if day in known:
            cur = known[day]
        if cur is not None:
            out.append((day, cur))
        day += dt.timedelta(days=1)
    return out


def rate_periods(rates: list[tuple[dt.date, float]], since: dt.date, until: dt.date):
    """Свернуть дневные ставки в периоды одинаковой ставки внутри [since, until]."""
    days = daily_series(rates, since, until)
    if not days:
        return []
    periods = []
    start, cur = days[0][0], days[0][1]
    prev = days[0][0]
    for d, v in days[1:]:
        if v != cur:
            periods.append((start, prev, cur))
            start, cur = d, v
        prev = d
    periods.append((start, prev, cur))
    return periods


def days_in_year(year: int) -> int:
    return 366 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0 else 365


def calc(debt: float, since: dt.date, until: dt.date,
         rates: list[tuple[dt.date, float]]) -> tuple[list[dict], float]:
    """Проценты по периодам. Возвращает (строки расчета, итог)."""
    rows, total = [], 0.0
    for a, b, rate in rate_periods(rates, since, until):
        # Период может пересекать границу года: число дней в году берется по году,
        # к которому относится день. Дробим по календарным годам.
        cur = a
        while cur <= b:
            year_end = min(b, dt.date(cur.year, 12, 31))
            n = (year_end - cur).days + 1
            base = days_in_year(cur.year)
            amount = debt * rate / 100 * n / base
            rows.append({"с": cur, "по": year_end, "дней": n, "ставка": rate,
                         "дней_в_году": base, "сумма": round(amount, 2)})
            total += amount
            cur = year_end + dt.timedelta(days=1)
    return rows, round(total, 2)


# ───────────────────────── сумма прописью ─────────────────────────
# Своя реализация вместо num2words: совет отклонил его из-за тихого занижения
# в 100 раз на дробной части и потому, что он не знает падежей рублей и копеек.
_ONES = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
         "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
         "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_ONES_F = dict(_ONES and {1: "одна", 2: "две"})
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
         "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот",
             "шестьсот", "семьсот", "восемьсот", "девятьсот"]
_SCALES = [
    ("", "", "", False),
    ("тысяча", "тысячи", "тысяч", True),
    ("миллион", "миллиона", "миллионов", False),
    ("миллиард", "миллиарда", "миллиардов", False),
    ("триллион", "триллиона", "триллионов", False),
]


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма числительного: 1 рубль, 2 рубля, 5 рублей."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _triple(n: int, feminine: bool) -> list[str]:
    out = []
    if n >= 100:
        out.append(_HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
    if n:
        out.append("одна" if (feminine and n == 1) else
                   "две" if (feminine and n == 2) else _ONES[n])
    return out


def number_words(n: int) -> str:
    """Целое число прописью."""
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + number_words(-n)
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    parts = []
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if not g:
            continue
        one, few, many, fem = _SCALES[idx]
        parts += _triple(g, fem)
        if one:
            parts.append(plural(g, one, few, many))
    return " ".join(parts)


def money_words(amount: float) -> str:
    """Сумма прописью в формате процессуального документа: рубли И копейки
    словами. Копейки цифрами («29 копеек») сторож формата бракует — он сверяет
    пропись СЛОВАМИ с числом (document_guard.check_money_propis), а приборы
    одного проекта обязаны говорить на одном языке: прежняя форма «рублей
    29 копеек» вставала на большинстве денежных исков (этап 9.20)."""
    # round() в python банковское: round(0.5) == 0, и полкопейки пропадала.
    # В деньгах округляем half-up, как считает бухгалтерия и суд.
    from decimal import Decimal, ROUND_HALF_UP
    neg = amount < 0
    cents = int((Decimal(str(abs(amount))) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    rub, kop = divmod(cents, 100)
    kop_words = " ".join(_triple(kop, feminine=True)) or "ноль"  # копейка — ж. род
    text = (f"{number_words(rub)} {plural(rub, 'рубль', 'рубля', 'рублей')} "
            f"{kop_words} {plural(kop, 'копейка', 'копейки', 'копеек')}")
    return ("минус " if neg else "") + text


def money_full(amount: float) -> str:
    """«327 120,50 (триста двадцать семь тысяч сто двадцать рублей пятьдесят копеек)»."""
    body = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{body} ({money_words(amount)})"


def parse_date(s: str) -> dt.date:
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s.strip())
    if not m:
        raise SystemExit(f"дата «{s}» не в формате ДД.ММ.ГГГГ")
    d, mo, y = (int(x) for x in m.groups())
    return dt.date(y, mo, d)


def render_md(debt: float, since: dt.date, until: dt.date,
              rows: list[dict], total: float) -> str:
    out = [f"Расчет процентов по ст. 395 ГК РФ",
           "",
           f"Сумма долга: {money_full(debt)}",
           f"Период просрочки: с {since:%d.%m.%Y} по {until:%d.%m.%Y} включительно",
           "",
           "| Период | Дней | Ключевая ставка | Дней в году | Проценты, руб. |",
           "|---|---:|---:|---:|---:|"]
    for r in rows:
        out.append(f"| {r['с']:%d.%m.%Y} — {r['по']:%d.%m.%Y} | {r['дней']} | "
                   f"{r['ставка']:.2f}% | {r['дней_в_году']} | "
                   + f"{r['сумма']:,.2f}".replace(",", " ").replace(".", ",") + " |")
    out += ["",
            f"Итого процентов: {money_full(total)}",
            "",
            "_Ставка — ключевая ставка Банка России, действовавшая в соответствующие "
            "периоды (п. 1 ст. 395 ГК РФ). Проценты начислены по день уплаты "
            "включительно (п. 3 ст. 395 ГК РФ). Формула — сумма долга, умноженная на "
            "ставку периода и число дней периода, деленная на число дней в году "
            "(Постановление Пленума ВС РФ от 24.03.2016 № 7, п. 39). Источник ставок — "
            "Банк России, официальный веб-сервис._"]
    return "\n".join(out)


def selftest() -> int:
    xml = ("<KeyRate><KR><DT>2026-01-01T00:00:00+03:00</DT><Rate>21.00</Rate></KR>"
           "<KR><DT>2026-01-02T00:00:00+03:00</DT><Rate>21.00</Rate></KR>"
           "<KR><DT>2026-01-03T00:00:00+03:00</DT><Rate>18.00</Rate></KR></KeyRate>")
    rates = parse_key_rates(xml)

    # Ставка 20% на 365 дней от 100 000 = ровно 20 000
    year = [(dt.date(2025, 1, 1) + dt.timedelta(days=i), 20.0) for i in range(365)]
    rows_y, total_y = calc(100_000, dt.date(2025, 1, 1), dt.date(2025, 12, 31), year)

    # Смена ставки внутри периода: два дня по 21%, один по 18%
    rows_m, total_m = calc(1_000_000, dt.date(2026, 1, 1), dt.date(2026, 1, 3), rates)
    expect_m = round(1_000_000 * 21 / 100 * 2 / 365 + 1_000_000 * 18 / 100 * 1 / 365, 2)

    checks = [
        ("ставки разобраны из ответа ЦБ", len(rates) == 3 and rates[0][1] == 21.0),
        ("периоды свернуты по смене ставки",
         [p[2] for p in rate_periods(rates, dt.date(2026, 1, 1), dt.date(2026, 1, 3))] == [21.0, 18.0]),
        # Выходные: ЦБ их не публикует, но ставка действует. Пропуск = занижение процентов.
        ("выходные заполняются последней ставкой",
         len(daily_series([(dt.date(2026, 1, 2), 21.0), (dt.date(2026, 1, 5), 18.0)],
                          dt.date(2026, 1, 2), dt.date(2026, 1, 6))) == 5),
        ("ставка до смены держится на выходных",
         [v for _, v in daily_series([(dt.date(2026, 1, 2), 21.0), (dt.date(2026, 1, 5), 18.0)],
                                     dt.date(2026, 1, 2), dt.date(2026, 1, 6))]
         == [21.0, 21.0, 21.0, 18.0, 18.0]),
        ("ставка на начало периода берется из более ранней даты",
         daily_series([(dt.date(2025, 12, 20), 16.0)],
                      dt.date(2026, 1, 1), dt.date(2026, 1, 2))[0][1] == 16.0),
        ("год по одной ставке считается точно", total_y == 20_000.00),
        ("день уплаты включен", rows_y[0]["дней"] == 365),
        ("смена ставки внутри периода разнесена", len(rows_m) == 2),
        ("сумма при смене ставки сходится", total_m == expect_m),
        ("високосный год — 366 дней", days_in_year(2028) == 366 and days_in_year(2026) == 365),
        ("вековой невисокосный", days_in_year(1900) == 365 and days_in_year(2000) == 366),
        # Сумма прописью: род тысяч женский, падежи рублей и копеек по числу.
        ("тысячи в женском роде", number_words(2_000) == "две тысячи"),
        ("единица тысяч в женском роде", number_words(1_000) == "одна тысяча"),
        ("миллионы в мужском роде", number_words(2_000_000) == "два миллиона"),
        ("подряд 11-19 берут форму «много»", plural(11, "рубль", "рубля", "рублей") == "рублей"),
        ("21 рубль — единственное число", plural(21, "рубль", "рубля", "рублей") == "рубль"),
        ("копейки склоняются", money_words(1.02).endswith("две копейки")),
        ("копейки прописью, не цифрами", not any(c.isdigit() for c in money_words(38998.29))),
        # Якорь 9.20: ровно та форма, которую принимает document_guard.
        ("копейки прописью для сторожа формата",
         money_words(38998.29) == "тридцать восемь тысяч девятьсот девяносто восемь "
                                  "рублей двадцать девять копеек"),
        ("ноль копеек", money_words(5.0).endswith("ноль копеек")),
        ("дробная часть не теряется",
         money_words(327_120.50) == "триста двадцать семь тысяч сто двадцать рублей пятьдесят копеек"),
        ("округление копеек вверх", money_words(0.005).endswith("одна копейка")),
        ("формат для документа", money_full(1234.5).startswith("1 234,50 (")),
        ("дата в формате ДД.ММ.ГГГГ", parse_date("04.08.2026") == dt.date(2026, 8, 4)),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Проценты по ст. 395 ГК РФ и сумма прописью")
    ap.add_argument("--dolg", type=float, help="сумма долга в рублях")
    ap.add_argument("--s", metavar="ДД.ММ.ГГГГ", help="первый день просрочки")
    ap.add_argument("--po", metavar="ДД.ММ.ГГГГ", help="последний день (по день уплаты включительно)")
    ap.add_argument("--md", action="store_true", help="таблица markdown для документа")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--propisyu", type=float, metavar="СУММА", help="только сумма прописью")
    ap.add_argument("--stavka", action="store_true", help="ключевая ставка на сегодня")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.propisyu is not None:
        print(money_full(a.propisyu))
        return 0

    if a.stavka:
        today = dt.date.today()
        rates = fetch_key_rates(today - dt.timedelta(days=30), today)
        if not rates:
            return 1
        d, v = rates[-1]
        print(f"Ключевая ставка Банка России на {d:%d.%m.%Y}: {v:.2f}%")
        return 0

    if not (a.dolg and a.s and a.po):
        ap.print_help()
        return 1

    since, until = parse_date(a.s), parse_date(a.po)
    if until < since:
        print("последний день раньше первого", file=sys.stderr)
        return 1
    if since < FIRST_KEY_RATE:
        print(f"ключевая ставка введена {FIRST_KEY_RATE:%d.%m.%Y}; за более ранний период "
              "применяется ставка рефинансирования либо средние ставки по вкладам — "
              "этот случай скрипт не считает", file=sys.stderr)
        return 1

    # Берем с запасом назад: ставку на первый день просрочки надо откуда-то знать,
    # а если он выходной, в выдаче ЦБ его нет.
    rates = fetch_key_rates(since - dt.timedelta(days=30), until)
    if not rates:
        return 1
    covered = {d for d, _ in daily_series(rates, since, until)}
    missing = [since + dt.timedelta(days=i) for i in range((until - since).days + 1)
               if since + dt.timedelta(days=i) not in covered]
    rows, total = calc(a.dolg, since, until, rates)
    if not rows:
        print("ЦБ не дал ставок на этот период — расчет не выполнен", file=sys.stderr)
        return 1

    if a.json:
        print(json.dumps({"dolg": a.dolg, "s": str(since), "po": str(until),
                          "periods": [{**r, "с": str(r["с"]), "по": str(r["по"])} for r in rows],
                          "itogo": total, "propisyu": money_words(total),
                          "dney_bez_stavki": len(missing)}, ensure_ascii=False, indent=2))
        return 0

    if a.md:
        print(render_md(a.dolg, since, until, rows, total))
    else:
        print(f"Долг: {money_full(a.dolg)}")
        print(f"Период: с {since:%d.%m.%Y} по {until:%d.%m.%Y} включительно\n")
        for r in rows:
            print(f"  {r['с']:%d.%m.%Y} — {r['по']:%d.%m.%Y}  {r['дней']:>4} дн.  "
                  f"{r['ставка']:>6.2f}%  /{r['дней_в_году']}  "
                  + f"{r['сумма']:>14,.2f}".replace(",", " "))
        print(f"\nИТОГО процентов: {money_full(total)}")
    if missing:
        print(f"\n⚠ ЦБ не дал ставку на {len(missing)} дн. периода — эти дни в расчет "
              f"НЕ вошли (первый: {missing[0]:%d.%m.%Y}). Проверить перед подачей.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
