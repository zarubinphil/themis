#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_inn.py — проверка ИНН/ОГРН и подтягивание актуальных данных из ЕГРЮЛ.

Зачем. Реквизит из скана приходит с ошибкой OCR, и ошибка в одном символе рушит
довод. Прецедент 02.08.2026: в таможенном деле 08.2026 ИНН
1657246601 и ОГРН «КАН АВТО ЭКСПЕРТ-26» не проходят контрольную сумму — то есть
такого юрлица не существует, а карта дела на них опиралась.

Два контура, второй включается автоматически при наличии ключа:

1. **Контрольная сумма — всегда, локально, $0, без сети.** ИНН 10/12 знаков и
   ОГРН/ОГРНИП считаются по алгоритму ФНС. Ловит ошибку OCR арифметикой:
   подмененная цифра почти никогда не дает валидную контрольную. Это уже
   покрывает главный риск и работает без интернета и без ключей.

2. **Актуальные данные из ЕГРЮЛ через DaData** — если задан `DADATA_API_KEY`.
   Тариф «Подсказки» бесплатный, запрос `findById/party` возвращает полное
   наименование, ОГРН, статус (действующее / ликвидировано), адрес и
   руководителя. Это ловит то, что контрольная сумма не видит: ИНН валиден по
   арифметике, но организация ликвидирована либо называется иначе, чем указано
   в документе оппонента.

	Ключ берется в порядке: env `DADATA_API_KEY` → Keychain → файл из env
	`THEMIS_DADATA_ENV` (строка `DADATA_API_KEY=...`). В код и в git ключ не попадает,
в вывод не печатается. Ответы кешируются в `~/.cache/legal_inn/<инн>.json` —
повторная проверка того же ИНН сети не трогает.

Использование:
    python3 scripts/verify_inn.py 7707083893
    python3 scripts/verify_inn.py 1657246601 --json
    python3 scripts/verify_inn.py --ogrn 1021603062930
    python3 scripts/verify_inn.py --scan cases/{доверитель}/{дело}
    python3 scripts/verify_inn.py --selftest

Коды возврата: 0 — все проверенное валидно; 1 — есть невалидные; 2 — ошибка вызова.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
import pii_gate  # noqa: E402

CACHE = os.path.expanduser("~/.cache/legal_inn")
SECRETS = os.environ.get("THEMIS_DADATA_ENV")
KEYCHAIN_ITEM = "THEMIS_DADATA_API_KEY"
API = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"

INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"\b(\d{13}|\d{15})\b")

# С МЕТКОЙ: число прямо названо реквизитом. Голое 10-значное число в тексте — чаще
# сумма или счет, поэтому невалидное среди них молчаливо отбрасывается. Но если рядом
# стоит слово «ИНН», невалидность — это находка, а не шум: прецедент 02.08.2026, когда
# в карте таможенного дела и ИНН, и ОГРН оказались несуществующими, а --scan промолчал.
INN_LABELED_RE = re.compile(r"ИНН[^\d]{0,12}(\d{12}|\d{10})\b", re.I)
OGRN_LABELED_RE = re.compile(r"ОГРН(?:ИП)?[^\d]{0,12}(\d{15}|\d{13})\b", re.I)


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
    числа без нее на 11 (для 13) или на 13 (для 15), взятый по модулю 10."""
    if not ogrn.isdigit():
        return False
    if len(ogrn) == 13:
        return int(ogrn[-1]) == int(ogrn[:-1]) % 11 % 10
    if len(ogrn) == 15:
        return int(ogrn[-1]) == int(ogrn[:-1]) % 13 % 10
    return False


def _key() -> str | None:
    """Ключ: env → Keychain → файл из THEMIS_DADATA_ENV. В код и git не попадает."""
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
        if SECRETS:
            for line in open(os.path.expanduser(SECRETS), encoding="utf-8"):
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
        # Python с python.org идет без системных корневых сертификатов —
        # берем связку certifi, иначе CERTIFICATE_VERIFY_FAILED на ровном месте.
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
                "note": "ИНН прошел контрольную сумму, но организации с ним нет"}
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
        # unrestricted_value несет индекс и регион («420087, Респ Татарстан, ...»).
        # В трудовом договоре адрес работодателя без индекса — неполный реквизит.
        "address_full": (d.get("address") or {}).get("unrestricted_value"),
        "manager": (d.get("management") or {}).get("name"),
        "manager_post": (d.get("management") or {}).get("post"),
    }
    try:
        json.dump(out, open(cached, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except OSError:
        pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# ЕГРЮЛ напрямую: egrul.nalog.ru. Официально, бесплатно, БЕЗ КЛЮЧА — и, в отличие
# от DaData, отдает ВЫПИСКУ, ПОДПИСАННУЮ УКЭП. Именно ее прикладывают к
# процессуальному документу: распечатка карточки из чужого сервиса доказательством
# состава участников и полномочий директора не является.
#
# Цепочка проверена целиком 04.08.2026 (не предполагается — прогнана curl'ом):
#   POST /                     query=<ИНН>  → {"t": …, "captchaRequired": false}
#   GET  /search-result/<t>                 → {"rows":[{n,i,o,k,g,r,t}]}
#   GET  /vyp-request/<t строки>            → {"t": …}
#   GET  /vyp-status/<t>                    → {"status":"ready"}
#   GET  /vyp-download/<t>                  → application/pdf, 425 753 байта,
#                                             10 страниц, «ДОКУМЕНТ ПОДПИСАН
#                                             УСИЛЕННОЙ КВАЛИФИЦИРОВАННОЙ
#                                             ЭЛЕКТРОННОЙ ПОДПИСЬЮ»
#
# КАПЧА — ЭТО «ПРИТОРМОЗИ», А НЕ «ОРГАНИЗАЦИЯ НЕ НАЙДЕНА». Источник ставит ее по
# темпу: восьмой запрос подряд в минуту дает captchaRequired/ERRORS.captchaSearch,
# через минуту доступ возвращается. Трактовать это как отрицательный ответ —
# значит записать существующую организацию в несуществующие. Отсюда пауза между
# запросами и отдельный статус.
EGRUL = "https://egrul.nalog.ru"
EGRUL_PAUSE = 5.0        # секунд между обращениями; решение владельца 04.08.2026
EGRUL_TIMEOUT = 40
_last_egrul_call = [0.0]


def _egrul_curl(url: str, post_query: str | None = None) -> tuple[int, bytes]:
    """(HTTP-код, тело). Сеть через curl: у системного python нет корневых сертификатов."""
    wait = EGRUL_PAUSE - (time.monotonic() - _last_egrul_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_egrul_call[0] = time.monotonic()
    marker = b"__HTTP__"
    cmd = ["curl", "-sL", "--max-time", str(EGRUL_TIMEOUT),
           "-H", "X-Requested-With: XMLHttpRequest",
           "-w", "__HTTP__%{http_code}"]
    if post_query is not None:
        cmd += ["-X", "POST",
                "-H", "Content-Type: application/x-www-form-urlencoded; charset=UTF-8",
                "--data-urlencode", f"query={post_query}"]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=EGRUL_TIMEOUT + 10)
    except (subprocess.TimeoutExpired, OSError):
        return 0, b""
    if r.returncode != 0:
        return 0, b""
    body, _, code = r.stdout.rpartition(marker)
    try:
        return int(code.decode("ascii", "replace").strip() or 0), body
    except ValueError:
        return 0, body


def egrul_captcha(payload: dict) -> bool:
    """Капча в ответе. Это темп обращений, а не отсутствие организации."""
    if payload.get("captchaRequired"):
        return True
    err = payload.get("ERRORS") or payload.get("errors") or {}
    return bool(isinstance(err, dict) and any("captcha" in str(k).lower() for k in err))


def egrul_find(inn: str) -> dict:
    """Карточка организации из ЕГРЮЛ по ИНН. Без ключа, официальный источник."""
    code, body = _egrul_curl(EGRUL + "/", post_query=inn)
    if code != 200 or not body:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": f"ЕГРЮЛ ответил HTTP {code or '—'}"}
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": "ответ ЕГРЮЛ не разобран как JSON"}
    if egrul_captcha(d):
        return {"status": "КАПЧА",
                "note": "источник просит притормозить (капча ставится по темпу, "
                        "не по существу запроса). Это НЕ «организация не найдена»: "
                        "повторить через минуту"}
    token = d.get("t")
    if not token:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": "ЕГРЮЛ не выдал токен поиска"}
    code, body = _egrul_curl(f"{EGRUL}/search-result/{token}")
    if code != 200 or not body:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": f"выдача поиска: HTTP {code or '—'}"}
    try:
        res = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": "выдача ЕГРЮЛ не разобрана"}
    if egrul_captcha(res):
        return {"status": "КАПЧА", "note": "капча на выдаче — повторить через минуту"}
    rows = res.get("rows") or []
    if not rows:
        return {"status": "НЕ НАЙДЕН В ЕГРЮЛ",
                "note": "ИНН прошел контрольную сумму, но организации с ним нет"}
    r0 = rows[0]
    return {"status": "НАЙДЕН", "name": r0.get("n"), "inn": r0.get("i"),
            "ogrn": r0.get("o"), "kind": r0.get("k"), "manager": r0.get("g"),
            "registered": r0.get("r"), "token": r0.get("t"),
            "source": "ЕГРЮЛ, egrul.nalog.ru"}


def egrul_extract(row_token: str, dest: str) -> dict:
    """Скачать выписку, ПОДПИСАННУЮ УКЭП. dest — куда положить .pdf."""
    code, body = _egrul_curl(f"{EGRUL}/vyp-request/{row_token}")
    if code != 200 or not body:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": f"заявка на выписку: HTTP {code or '—'}"}
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {"status": "КАНАЛ НЕДОСТУПЕН", "note": "ответ на заявку не разобран"}
    if egrul_captcha(d):
        return {"status": "КАПЧА", "note": "капча на заявке — повторить через минуту"}
    token = d.get("t") or row_token
    for _ in range(8):
        code, body = _egrul_curl(f"{EGRUL}/vyp-status/{token}")
        if code == 200 and b'"ready"' in body:
            break
    else:
        return {"status": "НЕ ГОТОВО", "note": "выписка не собралась за восемь опросов"}
    code, pdf = _egrul_curl(f"{EGRUL}/vyp-download/{token}")
    if code != 200 or not pdf.startswith(b"%PDF"):
        return {"status": "КАНАЛ НЕДОСТУПЕН",
                "note": f"загрузка выписки: HTTP {code or '—'}, ответ не PDF"}
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(pdf)
    return {"status": "ВЫПИСКА ПОЛУЧЕНА", "path": dest, "bytes": len(pdf),
            "note": "подписана УКЭП налогового органа — прикладывается к "
                    "процессуальному документу как есть, в .pdf"}


SUGGEST = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"


def outbound_text(value: str, label: str = "запрос") -> str:
    """Строковый запрос наружу: PII маскируется, остаток блокирует отправку."""
    if not value:
        return value
    masked, _ = pii_gate.mask_text(value)
    clean = masked if masked is not None else value
    if pii_gate.residual_matches(clean):
        raise ValueError(f"{label}: текст содержит персональные данные и не очищен")
    return clean


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
    OCR, спрашиваем официальный источник по наименованию и берем реквизиты оттуда.
    """
    try:
        query = outbound_text(query, "название организации")
    except ValueError as e:
        return [{"status": "ОТКАЗ", "note": str(e)}]
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
        # unrestricted_value несет индекс и регион («420087, Респ Татарстан, ...»).
        # В трудовом договоре адрес работодателя без индекса — неполный реквизит.
        "address_full": (d.get("address") or {}).get("unrestricted_value"),
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
    labeled_inn, labeled_ogrn = set(), set()
    # Путь может указывать и на одиночный файл (карта дела, отчет) — os.walk по файлу
    # молча обходит ноль элементов и выдает «ничего не найдено» вместо проверки.
    walker = ([(os.path.dirname(path) or ".", [], [os.path.basename(path)])]
              if os.path.isfile(path) else os.walk(path))
    for root, _dirs, files in walker:
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
            for m in INN_LABELED_RE.findall(t):
                labeled_inn.add(m)
            for m in OGRN_LABELED_RE.findall(t):
                labeled_ogrn.add(m)

    out = []
    for i in sorted(found_inn):
        if inn_valid(i):
            out.append(check(i))
        elif i in labeled_inn:
            out.append({"inn": i, "checksum": "НЕВАЛИДЕН",
                        "verdict": "ОШИБКА: ИНН не проходит контрольную сумму — в карту "
                                   "дела не вносить, искать верный по названию (--find)"})
        else:
            # Голых 10/12-значных чисел в тексте много (суммы, счета) — о них молчим,
            # иначе утонем в ложных тревогах.
            continue
    for o in sorted(found_ogrn):
        if ogrn_valid(o):
            out.append({"ogrn": o, "checksum": "валиден"})
        elif o in labeled_ogrn:
            out.append({"ogrn": o, "checksum": "НЕВАЛИДЕН",
                        "verdict": "ОШИБКА: ОГРН не проходит контрольную сумму — в карту "
                                   "дела не вносить, сверить по ЕГРЮЛ"})
    return out


def selftest() -> None:
    # ИНН юрлица — публичный реквизит организации. 12-значный — СИНТЕТИЧЕСКИЙ,
    # проходит контрольную сумму: ИНН физлица это персональные данные, и в
    # публичном репозитории им делать нечего даже в тесте.
    assert inn_valid("7707083893"), "ИНН Сбербанка обязан быть валиден"
    assert inn_valid("770000000082"), "12-значный ИНН (синтетический)"
    assert not inn_valid("7707083894"), "подмененная последняя цифра обязана падать"
    assert not inn_valid("1657246601"), "ИНН из боевого дела — невалиден, это и был улов"
    assert ogrn_valid("1027700132195"), "ОГРН Сбербанка"
    assert not ogrn_valid("1027700132196"), "подмененная цифра ОГРН"
    assert not inn_valid("123"), "короткое число не ИНН"
    # Реквизит С МЕТКОЙ обязан извлекаться: невалидный «ИНН 1657246601» в карте дела —
    # находка, а не шум. Раньше scan_case молча его отбрасывал.
    txt = "Ответчик ООО «Пример», ИНН 1657246601, ОГРН 1161690019502. Сумма 1234567890 руб."
    assert INN_LABELED_RE.findall(txt) == ["1657246601"], "ИНН с меткой обязан извлекаться"
    assert OGRN_LABELED_RE.findall(txt) == ["1161690019502"], "ОГРН с меткой обязан извлекаться"
    assert "1234567890" not in INN_LABELED_RE.findall(txt), "голое число не считается ИНН"
    # ЕГРЮЛ напрямую: разбор ответов БЕЗ СЕТИ, на слепках живых ответов
    # источника от 04.08.2026.
    assert egrul_captcha({"captchaRequired": True}), "капча по флагу"
    assert egrul_captcha({"ERRORS": {"captchaSearch": "введите код"}}), "капча в ERRORS"
    assert not egrul_captcha({"captchaRequired": False, "t": "ABC"}), "чистый ответ не капча"
    assert not egrul_captcha({}), "пустой ответ не капча"
    assert "Кузнецова" not in outbound_text("Кузнецова Мария Петровна, ООО Пример"), \
        "ФИО в строковом внешнем запросе должно маскироваться"
    assert outbound_text("ООО Пример") == "ООО Пример", "чистое название не искажается"
    # Капча — это «притормози», отдельный статус, а НЕ «организация не найдена»:
    # спутать их значит записать существующую организацию в несуществующие.
    assert EGRUL_PAUSE >= 5.0, "троттлинг обязателен: капча ставится по темпу"
    assert EGRUL.startswith("https://egrul.nalog.ru"), "источник — только официальный"

    print("selftest: контрольные суммы ИНН и ОГРН считаются верно, метки распознаются, разбор ЕГРЮЛ верен")


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка ИНН/ОГРН + данные ЕГРЮЛ")
    ap.add_argument("inn", nargs="*", help="ИНН (10 или 12 цифр)")
    ap.add_argument("--ogrn", nargs="+", help="проверить ОГРН/ОГРНИП")
    ap.add_argument("--scan", metavar="CASE", help="просканировать артефакты дела")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--find", metavar="НАЗВАНИЕ", help="найти организацию по названию")
    ap.add_argument("--md", action="store_true",
                    help="выдать готовый блок для карты дела с пометкой источника")
    ap.add_argument("--egrul", action="store_true",
                    help="сверить с ЕГРЮЛ напрямую (egrul.nalog.ru, без ключа)")
    ap.add_argument("--vypiska", metavar="КУДА",
                    help="скачать выписку ЕГРЮЛ, ПОДПИСАННУЮ УКЭП, в указанный .pdf "
                         "(ее прикладывают к процессуальному документу)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    if a.egrul or a.vypiska:
        if not a.inn:
            print("нужен ИНН", file=sys.stderr)
            return 2
        rc = 0
        for i in a.inn:
            if not inn_valid(i):
                print(f"ИНН {i}: НЕВАЛИДЕН по контрольной сумме — в ЕГРЮЛ не идем, "
                      "реквизит искажен", file=sys.stderr)
                rc = 1
                continue
            r = egrul_find(i)
            print(json.dumps(r, ensure_ascii=False, indent=2) if a.json
                  else f"{i}: {r['status']} — {r.get('name') or r.get('note', '')}")
            if r["status"] == "КАПЧА":
                rc = max(rc, 4)      # «притормози», а НЕ «не найден»
                continue
            if r["status"] != "НАЙДЕН":
                rc = max(rc, 1)
                continue
            if a.vypiska:
                dest = a.vypiska if len(a.inn) == 1 else \
                    os.path.join(a.vypiska, f"egrul-{i}.pdf")
                v = egrul_extract(r["token"], dest)
                print(json.dumps(v, ensure_ascii=False, indent=2) if a.json
                      else f"   выписка: {v['status']} {v.get('path', v.get('note', ''))}")
                if v["status"] != "ВЫПИСКА ПОЛУЧЕНА":
                    rc = max(rc, 1)
        return rc

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
        if a.scan:
            # Пустой результат сканирования — не повод показывать usage: это
            # осмысленный ответ «реквизитов не нашлось», и он не равен ошибке ввода.
            print(f"в {a.scan} не найдено ни одного ИНН/ОГРН с меткой. "
                  "Если реквизиты в деле есть — проверить, не лежат ли они только "
                  "в 00_intake/ (сканирование его не читает) или в бинарных файлах.")
            return 0
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
    bad = [r for r in res if r.get("checksum") != "валиден"
           or (r.get("verdict") or "").startswith(("ОШИБКА", "ВНИМАНИЕ"))]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
