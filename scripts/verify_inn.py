#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_inn.py — проверка ИНН/ОГРН и подтягивание актуальных данных из ЕГРЮЛ.

Зачем. Реквизит из скана приходит с ошибкой OCR, и ошибка в одном символе рушит
довод. Прецедент 02.08.2026: в деле `doveritel-3/tamozhnya-kan-avto-2026` ИНН
1657246601 и ОГРН «КАН АВТО ЭКСПЕРТ-26» не проходят контрольную сумму — то есть
такого юрлица не существует, а карта дела на них опиралась.

Два контура, второй включается автоматически при наличии ключа:

1. **Контрольная сумма — всегда, локально, $0, без сети.** ИНН 10/12 знаков и
   ОГРН/ОГРНИП считаются по алгоритму ФНС. Ловит ошибку OCR арифметикой:
   подменённая цифра почти никогда не даёт валидную контрольную. Это уже
   покрывает главный риск и работает без интернета и без ключей.

2. **Актуальные данные из ЕГРЮЛ через DaData** — если задан `DADATA_API_KEY`.
   Тариф «Подсказки» бесплатный, запрос `findById/party` возвращает полное
   наименование, ОГРН, статус (действующее / ликвидировано), адрес и
   руководителя. Это ловит то, что контрольная сумма не видит: ИНН валиден по
   арифметике, но организация ликвидирована либо называется иначе, чем указано
   в документе оппонента.

Ключ берётся в порядке: env `DADATA_API_KEY` → `~/.secrets/dadata.env`
(строка `DADATA_API_KEY=...`, каталог chmod 700). В код и в git ключ не попадает,
в вывод не печатается. Ответы кешируются в `~/.cache/legal_inn/<инн>.json` —
повторная проверка того же ИНН сети не трогает.

Использование:
    python3 scripts/verify_inn.py 7707083893
    python3 scripts/verify_inn.py 1657246601 --json
    python3 scripts/verify_inn.py --ogrn 1021603062930
    python3 scripts/verify_inn.py --scan cases/doveritel-3/tamozhnya-kan-avto-2026
    python3 scripts/verify_inn.py --selftest

Коды возврата: 0 — всё проверенное валидно; 1 — есть невалидные; 2 — ошибка вызова.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

CACHE = os.path.expanduser("~/.cache/legal_inn")
SECRETS = os.path.expanduser("~/.secrets/dadata.env")
KEYCHAIN_ITEM = "THEMIS_DADATA_API_KEY"
API = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"

INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")


def inn_valid(inn: str) -> bool:
    """Контрольные цифры ИНН по алгоритму ФНС. 10 знаков — юрлицо, 12 — ИП/физлицо."""
    if not inn.isdigit():
        return False
    d = [int(c) for c in inn]
    if len(inn) == 10:
        w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        return d[9] == sum(a * b for a, b in zip(w, d[:9])) % 11 % 10
    if len(inn) == 12:
        w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        return (d[10] == sum(a * b for a, b in zip(w1, d[:10])) % 11 % 10
                and d[11] == sum(a * b for a, b in zip(w2, d[:11])) % 11 % 10)
    return False


def ogrn_valid(ogrn: str) -> bool:
    """ОГРН (13 знаков) и ОГРНИП (15): последняя цифра — остаток от деления
    числа без неё на 11 (для 13) или на 13 (для 15), взятый по модулю 10."""
    if not ogrn.isdigit():
        return False
    if len(ogrn) == 13:
        return int(ogrn[-1]) == int(ogrn[:-1]) % 11 % 10
    if len(ogrn) == 15:
        return int(ogrn[-1]) == int(ogrn[:-1]) % 13 % 10
    return False


def _key() -> str | None:
    """Ключ: env → Keychain → ~/.secrets/dadata.env. В код и git не попадает."""
    k = os.environ.get("DADATA_API_KEY")
    if k:
        return k.strip()
    try:
        import subprocess
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_ITEM, "-w"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for line in open(SECRETS, encoding="utf-8"):
            if line.startswith("DADATA_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def dadata(inn: str) -> dict:
    """Актуальные данные ЕГРЮЛ. Без ключа/сети — понятный статус, не молчание."""
    os.makedirs(CACHE, exist_ok=True)
    cached = os.path.join(CACHE, f"{inn}.json")
    if os.path.isfile(cached):
        try:
            return json.load(open(cached, encoding="utf-8"))
        except ValueError:
            pass

    key = _key()
    if not key:
        return {"status": "НЕТ КЛЮЧА",
                "note": f"ключ не найден: ни env DADATA_API_KEY, ни Keychain "
                        f"({KEYCHAIN_ITEM}), ни {SECRETS}"}

    req = urllib.request.Request(
        API, data=json.dumps({"query": inn}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Token {key}"})
    try:
        # Python с python.org идёт без системных корневых сертификатов —
        # берём связку certifi, иначе CERTIFICATE_VERIFY_FAILED на ровном месте.
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            body = json.load(r)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": str(e)[:200]}

    sug = body.get("suggestions") or []
    if not sug:
        return {"status": "НЕ НАЙДЕН В ЕГРЮЛ",
                "note": "ИНН прошёл контрольную сумму, но организации с ним нет"}
    d = sug[0].get("data", {})
    out = {
        "status": "НАЙДЕН",
        "name": sug[0].get("value"),
        "name_full": (d.get("name") or {}).get("full_with_opf"),
        "ogrn": d.get("ogrn"),
        "kpp": d.get("kpp"),
        "state": (d.get("state") or {}).get("status"),
        "liquidation_date": (d.get("state") or {}).get("liquidation_date"),
        "address": (d.get("address") or {}).get("value"),
        "manager": (d.get("management") or {}).get("name"),
        "manager_post": (d.get("management") or {}).get("post"),
    }
    try:
        json.dump(out, open(cached, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except OSError:
        pass
    return out


SUGGEST = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"


def _post(url: str, payload: dict) -> dict:
    key = _key()
    if not key:
        return {"error": "нет ключа"}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Token {key}"})
    try:
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return json.load(r)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return {"error": str(e)[:200]}


def find_by_name(query: str, count: int = 5) -> list[dict]:
    """Поиск организации по названию — когда реквизит из скана оказался битым.

    Это и есть замена гаданию: вместо того чтобы принимать на веру ИНН, вычитанный
    OCR, спрашиваем официальный источник по наименованию и берём реквизиты оттуда.
    """
    body = _post(SUGGEST, {"query": query, "count": count})
    if "error" in body:
        return [{"status": "КАНАЛ НЕДОСТУПЕН", "note": body["error"]}]
    out = []
    for s in body.get("suggestions", []):
        d = s.get("data", {})
        out.append({
            "name": s.get("value"),
            "name_full": (d.get("name") or {}).get("full_with_opf"),
            "inn": d.get("inn"),
            "kpp": d.get("kpp"),
            "ogrn": d.get("ogrn"),
            "ogrn_date": d.get("ogrn_date"),
            "type": d.get("type"),
            "state": (d.get("state") or {}).get("status"),
            "address": (d.get("address") or {}).get("value"),
            "manager": (d.get("management") or {}).get("name"),
            "manager_post": (d.get("management") or {}).get("post"),
            "okved": d.get("okved"),
        })
    return out


def check(inn: str) -> dict:
    ok = inn_valid(inn)
    res = {"inn": inn, "checksum": "валиден" if ok else "НЕВАЛИДЕН"}
    if not ok:
        res["verdict"] = "ОШИБКА РЕКВИЗИТА — такого ИНН не существует, проверить по скану"
        return res
    res.update({f"egrul_{k}": v for k, v in dadata(inn).items()})
    st = res.get("egrul_state")
    if st and st != "ACTIVE":
        res["verdict"] = f"ВНИМАНИЕ: организация не действующая ({st})"
    elif res.get("egrul_status") == "НЕ НАЙДЕН В ЕГРЮЛ":
        res["verdict"] = "ВНИМАНИЕ: контрольная сумма верна, но в ЕГРЮЛ не значится"
    else:
        res["verdict"] = "ок"
    return res


def scan_case(path: str) -> list[dict]:
    """Собрать ИНН/ОГРН из артефактов дела и проверить каждый."""
    found_inn, found_ogrn = set(), set()
    for root, _dirs, files in os.walk(path):
        if "00_intake" in root:
            continue
        for f in files:
            if not f.endswith((".md", ".json", ".txt")):
                continue
            try:
                t = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in INN_RE.findall(t):
                found_inn.add(m)
            for m in OGRN_RE.findall(t):
                found_ogrn.add(m)

    out = []
    for i in sorted(found_inn):
        if inn_valid(i):
            out.append(check(i))
        else:
            # 10/12-значных чисел в тексте много (суммы, счета) — сообщаем только
            # то, что похоже на ИНН по контексту, иначе утонем в ложных тревогах.
            continue
    for o in sorted(found_ogrn):
        if ogrn_valid(o):
            out.append({"ogrn": o, "checksum": "валиден"})
    return out


def selftest() -> None:
    # Реальные действующие реквизиты — проверка алгоритма, не сети.
    assert inn_valid("7707083893"), "ИНН Сбербанка обязан быть валиден"
    assert inn_valid("500100732259"), "12-значный ИНН физлица"
    assert not inn_valid("7707083894"), "подменённая последняя цифра обязана падать"
    assert not inn_valid("1657246601"), "ИНН из дела doveritel-3 — невалиден, это и был улов"
    assert ogrn_valid("1027700132195"), "ОГРН Сбербанка"
    assert not ogrn_valid("1027700132196"), "подменённая цифра ОГРН"
    assert not inn_valid("123"), "короткое число не ИНН"
    print("selftest: контрольные суммы ИНН и ОГРН считаются верно")


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка ИНН/ОГРН + данные ЕГРЮЛ")
    ap.add_argument("inn", nargs="*", help="ИНН (10 или 12 цифр)")
    ap.add_argument("--ogrn", nargs="+", help="проверить ОГРН/ОГРНИП")
    ap.add_argument("--scan", metavar="CASE", help="просканировать артефакты дела")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--find", metavar="НАЗВАНИЕ", help="найти организацию по названию")
    ap.add_argument("--md", action="store_true",
                    help="выдать готовый блок для карты дела с пометкой источника")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    if a.md:
        # Готовый блок в knowledge-map.md / _client.md. Пометка источника
        # обязательна: сведения официальные, но взяты машиной, и юрист должен
        # видеть, что это не из документа дела, а из ЕГРЮЛ на дату запроса.
        import datetime
        today = datetime.date.today().strftime("%d.%m.%Y")
        for i in a.inn:
            r = check(i)
            print(f"\n**{r.get('egrul_name') or 'ИНН ' + i}**")
            print(f"- ИНН: {i} — контрольная сумма {r['checksum']}")
            for label, k in (("Полное наименование", "egrul_name_full"), ("ОГРН", "egrul_ogrn"),
                             ("КПП", "egrul_kpp"), ("Статус", "egrul_state"),
                             ("Дата ликвидации", "egrul_liquidation_date"),
                             ("Адрес", "egrul_address"), ("Руководитель", "egrul_manager"),
                             ("Должность", "egrul_manager_post")):
                if r.get(k):
                    print(f"- {label}: {r[k]}")
            print(f"- _Источник: ЕГРЮЛ через DaData (официальный сервис), запрошено {today}._")
        return 0

    if a.find:
        rows = find_by_name(a.find)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"{r.get('name')} · ИНН {r.get('inn')} · ОГРН {r.get('ogrn')} "
                      f"· {r.get('state')}")
                if r.get("address"):
                    print(f"    адрес: {r['address']}")
                if r.get("manager"):
                    print(f"    руководитель: {r['manager']} ({r.get('manager_post')})")
        return 0

    res = []
    if a.scan:
        res += scan_case(a.scan)
    res += [check(i) for i in a.inn]
    res += [{"ogrn": o, "checksum": "валиден" if ogrn_valid(o) else "НЕВАЛИДЕН"}
            for o in (a.ogrn or [])]
    if not res:
        ap.error("нужен ИНН, --ogrn, --scan или --selftest")

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for r in res:
            head = r.get("inn") or r.get("ogrn")
            print(f"{head}: {r['checksum']}"
                  + (f" · {r['verdict']}" if r.get("verdict") else ""))
            for k in ("egrul_name", "egrul_state", "egrul_address", "egrul_manager",
                      "egrul_note"):
                if r.get(k):
                    print(f"    {k[6:]}: {r[k]}")
    bad = [r for r in res if r["checksum"] != "валиден"
           or (r.get("verdict") or "").startswith(("ОШИБКА", "ВНИМАНИЕ"))]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
