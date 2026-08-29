#!/usr/bin/env python3
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


def kpp_valid(kpp: str) -> bool:
    """КПП: 9 знаков. Контрольной суммы у него нет — проверяем формат по приказу
    ФНС: NNNN PP XXX, где NNNN — код органа, PP — причина постановки (цифры либо
    латинские A-Z для иностранных организаций), XXX — порядковый номер.
    Формальная проверка ловит потерю знака при OCR, а это уже половина ошибок."""
    k = re.sub(r"\s", "", kpp).upper()
    return bool(re.fullmatch(r"\d{4}([0-9A-Z]{2})\d{3}", k)) and k[4:6] != "00"


def snils_valid(snils: str) -> bool:
    """СНИЛС: 9 цифр номера + контрольное. Ниже 001-001-998 контроля нет по закону.

    ДЫРА, найденная аудитом 03.08.2026: окно «контроль не определён» проглатывало
    пустышку — snils_valid('000-000-000 00') возвращал True. Ноль и повтор одной
    цифры — не номер, а незаполненное поле бланка или мусор OCR; такое обязано
    отсекаться до всякой арифметики, иначе в документ уходит несуществующий СНИЛС.
    """
    ds = re.sub(r"\D", "", snils)
    if len(ds) != 11:
        return False
    if int(ds[:9]) == 0 or len(set(ds[:9])) == 1:
        return False                       # 000-000-000 и 111-111-111 — не номера
    if int(ds[:9]) <= 1001998:  # ниже порога контрольное число не определено
        return True
    r = sum(int(c) * (9 - i) for i, c in enumerate(ds[:9])) % 101
    return (0 if r == 100 else r) == int(ds[9:])


# Префиксы БИК, реально встречающиеся в справочнике ЦБ РФ (ED807, выгрузка
# 04.08.2026, 1429 записей — прочитано из самого справочника, не предположено):
#   04 — кредитные организации и их подразделения, 1247 записей (то, что видит юрист);
#   00, 01, 02 — подразделения Банка России, 70 записей;
#   10 — банки-нерезиденты (CntrCd UZ, KZ), 27 записей;
#   20-29 — органы Федерального казначейства, PtType 99, 85 записей.
# Ходовое правило «БИК = 04 + 7 цифр» справочником ОПРОВЕРГАЕТСЯ: под него не
# подходят 182 записи из 1429 (12,7 %), и жёсткая проверка на «04» отвергала бы
# реальные реквизиты казначейства — а именно они стоят в платёжках по госпошлине.
BIK_PREFIXES = frozenset({"00", "01", "02", "04", "10",
                          "20", "21", "22", "23", "24", "25", "26", "27", "28", "29"})


def bik_valid(bik: str) -> bool:
    """БИК: 9 цифр, префикс из справочника ЦБ. Контрольной суммы у БИК нет.

    Проверка формальная и намеренно не строже справочника: точное существование
    банка даёт DaData по ключу `bank` (см. knowledge/allowed-services.md), а здесь
    задача одна — поймать потерянный или дорисованный OCR знак за $0 и без сети.
    """
    b = re.sub(r"\D", "", bik or "")
    if len(b) != 9 or len(set(b)) == 1:
        return False
    return b[:2] in BIK_PREFIXES


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
    for v in req.get("kpp", []):
        if not kpp_valid(v):
            bad.append(f"КПП {v}: неверный формат (4 цифры + причина + 3 цифры)")
    for v in req.get("snils", []):
        if not snils_valid(v):
            bad.append(f"СНИЛС {v}: контрольное число НЕ сходится")
    for v in req.get("isin", []):
        if not isin_valid(v):
            bad.append(f"ISIN {v}: Luhn НЕ сходится — вероятна ошибка OCR")
    # БИК читается ИЗ САМОГО документа, а не только из аргумента: ключ `bik`
    # роутер кладёт в <sha>.requisites.json, и до 04.08.2026 scan_requisites его
    # игнорировал полностью — реквизит банка не проверялся ни разу ни на одном деле.
    doc_biks = [v for v in req.get("bik", []) if v]
    for v in doc_biks:
        if not bik_valid(v):
            bad.append(f"БИК {v}: не 9 цифр либо префикс отсутствует в справочнике ЦБ "
                       "— вероятна ошибка OCR, в документ не переносить")
    # Счёт ключуется БИК-ом. В одном документе (выписка, договор, платёжка) БИКов
    # обычно несколько — свой у каждого банка, и какой счёт к какому относится, из
    # плоского списка реквизитов не видно. Поэтому счёт считается подтверждённым,
    # если он ключуется ХОТЬ С ОДНИМ БИКом документа; замечание — только когда ни
    # один не подошёл. Перебор «каждый счёт против каждого БИКа» давал ложную
    # тревогу на живом деле (проверено 04.08.2026 на 18 файлах раздела имущества).
    banks = [bik] if bik else [v for v in doc_biks if bik_valid(v)]
    if banks:
        for v in req.get("account", []):
            if not any(account_valid(v, b) for b in banks):
                bad.append(f"Счет {v}: не ключуется ни с одним БИК документа "
                           f"({', '.join(banks)}) — сверить по первичке")
    return bad


def _selftest():
    ok = [
        inn_valid("1655248572"), inn_valid("7728168971"),
        ogrn_valid("1021600000124"), ogrn_valid("1027739642281"), ogrn_valid("1021603150150"),
        kpp_valid("165501001"), kpp_valid("7728AB001"), not kpp_valid("165500001"),
        not kpp_valid("1655001"), not kpp_valid("16550ab01".upper() + "0"),
        snils_valid("123-456-789 64"), snils_valid("111-222-333 72"), snils_valid("555-666-777 50"),
        account_valid("40817810808430000005", "044525593"),   # синтетический счет, проходит контрольную сумму
        account_valid("40702810029160002367", "042202824"),
        account_valid("40702810929070003307", "042202824"),
        account_valid("30101810200000000824", "042202824"),   # корсчет
        account_valid("30101810200000000593", "044525593"),
        not inn_valid("1655248573"),
        not account_valid("40817810808430075620", "044525593"),
        not ogrn_valid("1021600000125"),
        not snils_valid("123-456-789 63"),
        isin_valid("US4581401001"),        # Intel — стр. 82 заключения
        isin_valid("US7170811035"),        # Pfizer
        isin_valid("US7802593050"),        # Shell ADR
        isin_valid("RU0007661625"),        # Газпром
        not isin_valid("US7805293050"),    # порча gundam-прогона 02.08.2026
        not isin_valid("US481401001"),     # gundam потерял цифру
        # живой улов 02.08.2026: ИНН/ОГРН «КАН АВТО ЭКСПЕРТ-26» из карты дела
        # из таможенного дела 08.2026 не существуют (проверено по ЕГРЮЛ)
        not inn_valid("1657246601"),
        not ogrn_valid("1161690019502"),
        # СНИЛС: окно «контроль не определён» глотало пустышку до 04.08.2026.
        not snils_valid("000-000-000 00"),   # незаполненное поле бланка
        not snils_valid("111-111-111 11"),   # повтор одной цифры
        snils_valid("001-001-998 00"),       # граница окна — законный номер
        # БИК. Положительные — дословно из справочника ЦБ ED807 от 04.08.2026:
        # 040397100 (кредитная организация), 044525593, 042202824, 200000154
        # (казначейство, префикс 20), 100070023 (банк-нерезидент, префикс 10).
        bik_valid("044525593"), bik_valid("042202824"), bik_valid("040397100"),
        bik_valid("200000154"), bik_valid("100070023"),
        not bik_valid("999999999"),          # префикса 99 в справочнике нет
        not bik_valid("04452559"),           # восемь цифр — OCR потерял знак
        not bik_valid("0445255931"),         # десять — OCR дорисовал
        not bik_valid("000000000"),          # незаполненное поле
        not bik_valid(""),
        # Ключ `bik` из <sha>.requisites.json прежде игнорировался целиком.
        scan_requisites({"bik": ["044525593"]}) == [],
        len(scan_requisites({"bik": ["999999999"]})) == 1,
        # Счёт ключуется БИК-ом из самого документа, без явного аргумента.
        scan_requisites({"bik": ["042202824"],
                         "account": ["40702810029160002367"]}) == [],
        len(scan_requisites({"bik": ["042202824"],
                             "account": ["40702810029160002368"]})) == 1,
        # Несколько банков в одном документе: счёт ключуется своим БИКом, чужой
        # мешать не должен. Прямой перебор «каждый с каждым» давал ложную тревогу.
        scan_requisites({"bik": ["044525225", "042202824"],
                         "account": ["40702810029160002367"]}) == [],
        len(scan_requisites({"bik": ["044525225", "042202824"],
                             "account": ["40702810029160002368"]})) == 1,
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
        # Ненулевой код при находке — иначе вызывающий скрипт и хук читают
        # «реквизит невалиден» как успешный прогон (та же дыра была в verify_inn --scan).
        sys.exit(1 if problems else 0)
    kind, val = a[0], a[1]
    fn = {"inn": inn_valid, "ogrn": ogrn_valid, "snils": snils_valid,
          "bik": bik_valid, "kpp": kpp_valid, "isin": isin_valid}.get(kind)
    res = account_valid(val, a[2]) if kind == "account" else fn(val)
    print("VALID" if res else "INVALID")
    sys.exit(0 if res else 1)


if __name__ == "__main__":
    main()
