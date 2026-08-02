#!/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
"""verify_requisites.py — контрольные числа реквизитов: арифметика ловит ошибку OCR за $0.

Проверки (все — официальные алгоритмы контрольных разрядов):
  ИНН       10 зн. (вес 2-4-10-3-5-9-4-6-8, mod 11) и 12 зн. (два контрольных)
  ОГРН      13 зн. (первые 12 mod 11) · ОГРНИП 15 зн. (первые 14 mod 13)
  СНИЛС     9 зн. + контрольное (веса 9..1, mod 101; 100→00)
  Счет+БИК  ключевание: [3 посл. цифры БИК]+счет (корсчет 301..: '0'+БИК[4:6]+счет),
            веса 7-1-3 циклом, сумма mod 10 == 0

Использование:
  verify_requisites.py inn 1655248572
  verify_requisites.py account 40817810808430000005 044525593
  verify_requisites.py --scan FILE.requisites.json   # прогнать весь json из кеша
  verify_requisites.py --selftest                    # тесты на реальных реквизитах

Валидный реквизит != верный (перестановка цифр может дать другой валидный номер),
но невалидный — ГАРАНТИРОВАННО ошибка OCR или опечатка. Дешевый жесткий фильтр.
"""
import json
import re
import sys


def _ctl11(digits, weights):
    return sum(a * b for a, b in zip(digits, weights)) % 11 % 10


def inn_valid(inn: str) -> bool:
    inn = re.sub(r"\D", "", inn)
    if len(inn) not in (10, 12):
        return False
    d = [int(c) for c in inn]
    if len(inn) == 10:
        return _ctl11(d[:9], [2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    return (_ctl11(d[:10], [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
            and _ctl11(d[:11], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11])


def ogrn_valid(ogrn: str) -> bool:
    ogrn = re.sub(r"\D", "", ogrn)
    if len(ogrn) not in (13, 15):
        return False
    mod = 11 if len(ogrn) == 13 else 13
    return int(ogrn[:-1]) % mod % 10 == int(ogrn[-1])


def snils_valid(snils: str) -> bool:
    ds = re.sub(r"\D", "", snils)
    if len(ds) != 11:
        return False
    if int(ds[:9]) <= 1001998:  # ниже порога контрольное число не определено
        return True
    r = sum(int(c) * (9 - i) for i, c in enumerate(ds[:9])) % 101
    return (0 if r == 100 else r) == int(ds[9:])


def isin_valid(isin: str) -> bool:
    """ISIN (12 зн., Luhn). Ловит перестановку/потерю цифры при OCR брокерских
    отчетов: gundam-прогон 02.08.2026 исказил 3 из 3 ISIN на стр. 82 заключения."""
    isin = isin.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin)  # A=10..Z=35
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:  # удваивается каждая вторая, НЕ считая контрольную
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def account_valid(account: str, bik: str) -> bool:
    """Расчетный/корреспондентский счет ключуется БИК-ом банка (Положение ЦБ 2-П)."""
    account, bik = re.sub(r"\D", "", account), re.sub(r"\D", "", bik)
    if len(account) != 20 or len(bik) != 9:
        return False
    seed = ("0" + bik[4:6]) if account.startswith("301") else bik[-3:]
    s = seed + account
    w = (7, 1, 3)
    return sum(int(c) * w[i % 3] for i, c in enumerate(s)) % 10 == 0


def scan_requisites(req: dict, bik: str | None = None) -> list[str]:
    """Прогнать словарь <sha>.requisites.json. Возвращает список проблем."""
    bad = []
    for v in req.get("inn", []):
        if not inn_valid(v):
            bad.append(f"ИНН {v}: контрольное число НЕ сходится — вероятна ошибка OCR")
    for v in req.get("ogrn", []):
        if not ogrn_valid(v):
            bad.append(f"ОГРН {v}: контрольное число НЕ сходится — вероятна ошибка OCR")
    for v in req.get("snils", []):
        if not snils_valid(v):
            bad.append(f"СНИЛС {v}: контрольное число НЕ сходится")
    for v in req.get("isin", []):
        if not isin_valid(v):
            bad.append(f"ISIN {v}: Luhn НЕ сходится — вероятна ошибка OCR")
    if bik:
        for v in req.get("account", []):
            if not account_valid(v, bik):
                bad.append(f"Счет {v} при БИК {bik}: ключевание НЕ сходится")
    return bad


def _selftest():
    ok = [
        inn_valid("1655248572"), inn_valid("7728168971"),
        ogrn_valid("1021600000124"), ogrn_valid("1027739642281"), ogrn_valid("1021603150150"),
        snils_valid("039-656-252-89"), snils_valid("048-020-883 40"), snils_valid("113-195-207 21"),
        account_valid("40817810808430000005", "044525593"),   # прецедент дела боевое-дело
        account_valid("40702810029160002367", "042202824"),
        account_valid("40702810929070003307", "042202824"),
        account_valid("30101810200000000824", "042202824"),   # корсчет
        account_valid("30101810200000000593", "044525593"),
        not inn_valid("1655248573"),
        not account_valid("40817810808430075620", "044525593"),
        not ogrn_valid("1021600000125"),
        not snils_valid("039-656-252-88"),
        isin_valid("US4581401001"),        # Intel — стр. 82 заключения
        isin_valid("US7170811035"),        # Pfizer
        isin_valid("US7802593050"),        # Shell ADR
        isin_valid("RU0007661625"),        # Газпром
        not isin_valid("US7805293050"),    # порча gundam-прогона 02.08.2026
        not isin_valid("US481401001"),     # gundam потерял цифру
        # живой улов 02.08.2026: ИНН/ОГРН «КАН АВТО ЭКСПЕРТ-26» из карты дела
        # doveritel-3/tamozhnya-kan-avto-2026 не существуют (проверено по ЕГРЮЛ)
        not inn_valid("1657246601"),
        not ogrn_valid("1161690019502"),
    ]
    failed = len(ok) - sum(ok)
    print(f"selftest: {sum(ok)}/{len(ok)} OK" + (f", {failed} FAIL" if failed else ""))
    sys.exit(1 if failed else 0)


def main():
    a = sys.argv[1:]
    if not a or a[0] == "--selftest":
        _selftest()
    if a[0] == "--scan":
        req = json.load(open(a[1], encoding="utf-8"))
        bik = a[2] if len(a) > 2 else None
        problems = scan_requisites(req, bik)
        for p in problems:
            print("⚠", p)
        print("все реквизиты сходятся ✓" if not problems else f"итого проблем: {len(problems)}")
        sys.exit(0)
    kind, val = a[0], a[1]
    fn = {"inn": inn_valid, "ogrn": ogrn_valid, "snils": snils_valid}.get(kind)
    res = account_valid(val, a[2]) if kind == "account" else fn(val)
    print("VALID" if res else "INVALID")
    sys.exit(0 if res else 1)


if __name__ == "__main__":
    main()
