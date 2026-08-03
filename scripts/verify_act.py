#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_act.py — верификация реквизитов судебных актов без участия модели.

Зачем. В прогоне боевого дела (раздел имущества) доверификация четырех актов силами LLM стоила
243 605 токенов. Операция механическая: скачать страницу, найти номер, дату,
состав коллегии, ключевую формулировку. Модель нужна только чтобы прочитать
итог. Этот скрипт делает проверку за ~0 токенов и точнее: именно машинная
сверка вскрыла, что 13.06.2019 у Определения № 81-КГ19-2 — дата публикации
на портале, а не дата судебного акта.

ЧТО БЫЛО СЛОМАНО (аудит 02.08.2026). Прежняя версия подставляла в шаблон URL
пустую дату (`t.format(d="")`), получала адрес вида `...-ot--n-81-kg19-2/`,
всегда ловила 404 и выдавала «НЕ НАЙДЕН» одинаково реальному акту и
выдуманному. За всю историю проекта машинно проверено 2 акта из 250 — оба
руками через `--url`. Барьер против выдуманной практики не существовал.

ПРИНЦИП ПОСЛЕ ПОЧИНКИ (решение совета, Н1). Пара «номер + дата» от охотника —
это КАНДИДАТ, а не факт. Фактом становится то, что подтвердила страница
публикатора. Модель, выдумавшая акт, выдумает и дату к нему, поэтому проверять
надо обе величины разом: несовпадение даты при существующем номере — сигнал,
что модель ошиблась в реквизите.

Статусы (несуществование акта и недоступность канала — РАЗНЫЕ вещи):
    ПОДТВЕРЖДЕН     — номер и заявленная дата найдены в тексте акта
    РАСХОЖДЕНИЕ     — номер есть, дата в тексте другая (сверить: дата акта или публикации)
    НЕ ПОДТВЕРЖДЕН  — страница не найдена. НЕ основание утверждать, что акта нет
    КАНАЛ НЕДОСТУПЕН — сеть или публикатор не отвечают. Проверка НЕ выполнена

Использование:
    python3 scripts/verify_act.py 81-КГ19-2@26.03.2019 5-КГ22-82-К2@04.10.2022
    python3 scripts/verify_act.py --json 41-КГ16-17@17.05.2016
    python3 scripts/verify_act.py --url https://... 56-КГ23-6-К9
    python3 scripts/verify_act.py --emit 01_context/_practice/verified.json 81-КГ19-2@26.03.2019
    python3 scripts/verify_act.py --demo        # самопроверка разбора и сборки URL

ponytail: кеш на диске, без БД — актов десятки, не миллионы.
"""
import argparse
import hashlib
import html as htmllib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FETCH = os.path.join(ROOT, "scripts", "fetch_url.sh")
CACHE = os.path.expanduser("~/.cache/legal_acts")

PARSER_VERSION = "2026.08.02"
MIN_PAGE_BYTES = 2000  # меньше — это страница ошибки, кешировать её нельзя

# Шаблоны публикаторов. {d} — дата акта в формате ДДММГГГГ, {slug} — номер латиницей.
# Порядок = приоритет доверия. Оба реально отдают 200 (проверено 02.08.2026):
#   .../opredelenie-verkhovnogo-suda-rf-ot-17052016-n-41-kg16-17/
#   .../opredelenie-sudebnoi-kollegii-...-ot-04102022-n-5-kg22-82-k2/
SOURCES = [
    ("legalacts.ru", "https://legalacts.ru/sud/opredelenie-verkhovnogo-suda-rf-ot-{d}-n-{slug}/"),
    ("legalacts.ru", "https://legalacts.ru/sud/opredelenie-sudebnoi-kollegii-po-grazhdanskim-"
                     "delam-verkhovnogo-suda-rossiiskoi-federatsii-ot-{d}-n-{slug}/"),
]

MONTHS = ("января февраля марта апреля мая июня июля августа "
          "сентября октября ноября декабря").split()

DATE_RE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\s*г")
DOT_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
COLLEGIUM_RE = re.compile(r"председательствующ\w*\s+([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.)")
SUBJECT_HINTS = {
    "раздел совместно нажитого": "раздел имущества супругов",
    "разделе совместно нажитого": "раздел имущества супругов",
    "ценные бумаги": "ценные бумаги",
    "брачн": "семейный спор",
    "административн": "административное дело",
    "трудов": "трудовой спор",
    "налог": "налоговый спор",
}


# Официальный публикатор нормативных актов. Адрес документа — по eoNumber, номеру
# электронного опубликования: 0001 + ГГГГММДД публикации + четырёхзначный порядковый.
#
# ЛОВУШКА, стоившая совету ложного «акт не существует» (03.08.2026). eoNumber
# соседних актов идут подряд: 0001202411300011 — ФЗ № 420-ФЗ, ...0012 — уже № 421-ФЗ.
# Идентификатор, угаданный «по порядку», открывает СУЩЕСТВУЮЩУЮ страницу другого
# акта, и проверка по одному факту «страница открылась» подтверждает что угодно.
# Обратный случай хуже: несуществующий eoNumber отдаёт HTTP 404 страницей в 12,5 КБ —
# она проходит порог MIN_PAGE_BYTES и выглядит как нормальный ответ.
# Поэтому единственный допустимый критерий — СВЕРКА ЗАГОЛОВКА страницы с
# реквизитами акта. Дата внутри самого eoNumber — дата ПУБЛИКАЦИИ, не дата акта,
# и сверять по ней нельзя.
EO_URL = "http://publication.pravo.gov.ru/document/{eo}"
EO_RE = re.compile(r"^\d{16}$")
# «Федеральный закон от 30.11.2024 № 420-ФЗ ∙ Официальное опубликование…»
EO_TITLE_RE = re.compile(r"от\s+(\d{2}\.\d{2}\.\d{4})\s*№\s*([^\s∙|]+)")


def page_title(html_text: str) -> str:
    """Заголовок страницы с раскрытыми сущностями. Пусто — страницы нет."""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
    return htmllib.unescape(m.group(1)).replace("\xa0", " ").strip() if m else ""


def parse_eo_title(title: str) -> tuple[str, str] | None:
    """«… от 30.11.2024 № 420-ФЗ ∙ …» -> ('30.11.2024', '420-ФЗ'). Нет пары — None."""
    m = EO_TITLE_RE.search(title or "")
    return (m.group(1), m.group(2).strip(" .,")) if m else None


def same_act_number(claimed: str, found: str) -> bool:
    """Сравнение номеров без чувствительности к регистру, дефисам и пробелам."""
    norm = lambda s: re.sub(r"[\s\-‑–—]", "", (s or "")).upper().replace("N", "№")
    return bool(claimed) and norm(claimed) == norm(found)


def analyze_eo(title: str, num: str, claimed_date: str | None) -> dict:
    """Решение по странице публикатора. Заголовок — единственное основание."""
    base = {"eo_title": title}
    if not title:
        return {**base, "status": "НЕ ПОДТВЕРЖДЕН",
                "note": "страница без заголовка — идентификатора нет у публикатора "
                        "(HTTP 404 отдаётся полноразмерной страницей)"}
    pair = parse_eo_title(title)
    if not pair:
        return {**base, "status": "НЕ ПОДТВЕРЖДЕН",
                "note": f"в заголовке «{title[:80]}» нет пары «от ДД.ММ.ГГГГ № номер» — "
                        "сверить реквизит невозможно"}
    found_date, found_num = pair
    if not same_act_number(num, found_num):
        return {**base, "status": "НЕ ПОДТВЕРЖДЕН", "eo_date": found_date,
                "eo_num": found_num,
                "note": f"идентификатор указывает на ДРУГОЙ акт: № {found_num} "
                        f"от {found_date}. Заявлен № {num}. eoNumber соседних актов "
                        "идут подряд — угаданный по порядку открывает чужую страницу"}
    if claimed_date and claimed_date != found_date:
        return {**base, "status": "РАСХОЖДЕНИЕ", "eo_date": found_date,
                "eo_num": found_num, "date_match": False,
                "note": f"номер совпал, дата у публикатора {found_date}, заявлена {claimed_date}"}
    return {**base, "status": "ПОДТВЕРЖДЕН", "eo_date": found_date, "eo_num": found_num,
            "date_match": True if claimed_date else None}


def verify_eo(eo: str, num: str, claimed_date: str | None = None) -> dict:
    """Проверка нормативного акта по номеру электронного опубликования."""
    base = {"act": num, "claimed_date": claimed_date, "eo": eo, "hits": 0,
            "dates": [], "collegium": "", "subject": "", "date_match": None,
            "url": "", "snapshot": None}
    if not EO_RE.match(eo or ""):
        return {**base, "status": "НЕ ПОДТВЕРЖДЕН",
                "note": f"eoNumber «{eo}» не 16 цифр — это не идентификатор публикатора"}
    url = EO_URL.format(eo=eo)
    dest = os.path.join(CACHE, f"eo_{eo}.html")
    state = fetch(url, dest)
    if state == "unreachable":
        return {**base, "status": "КАНАЛ НЕДОСТУПЕН", "url": url,
                "note": "публикатор не ответил — проверка НЕ выполнена"}
    if state == "small":
        return {**base, "status": "НЕ ПОДТВЕРЖДЕН", "url": url,
                "note": "ответ короче страницы документа"}
    raw = open(dest, encoding="utf-8", errors="ignore").read()
    res = analyze_eo(page_title(raw), num, claimed_date)
    return {**base, **res, "url": url, "snapshot": snapshot(dest, strip_tags(raw))}


def slugify(num: str) -> str:
    """81-КГ19-2 -> 81-kg19-2 (латиница публикаторов)."""
    table = str.maketrans({"К": "k", "Г": "g", "Э": "e", "С": "s", "П": "p",
                           "А": "a", "В": "v", "Д": "d", "О": "o", "У": "u"})
    return num.translate(table).lower()


def parse_arg(raw: str) -> tuple[str, str | None]:
    """`81-КГ19-2@26.03.2019` -> ('81-КГ19-2', '26.03.2019'). Дата необязательна."""
    if "@" in raw:
        num, date = raw.split("@", 1)
        return num.strip(), date.strip()
    return raw.strip(), None


def url_date(date: str) -> str | None:
    """26.03.2019 -> 26032019. Кривая дата — None, а не молчаливый мусор."""
    m = DOT_DATE_RE.fullmatch(date or "")
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else None


def fetch(url: str, dest: str) -> str:
    """Возвращает 'cached' | 'ok' | 'small' | 'unreachable'.

    Страницы меньше MIN_PAGE_BYTES не кешируются: это заглушки 404, из-за
    которых прежняя версия накопила шесть файлов по 302 байта и считала их
    результатом проверки.
    """
    if os.path.exists(dest) and os.path.getsize(dest) > MIN_PAGE_BYTES:
        return "cached"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    try:
        r = subprocess.run(["bash", FETCH, url, tmp], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "unreachable"
    if not os.path.exists(tmp):
        return "unreachable"
    size = os.path.getsize(tmp)
    if size <= MIN_PAGE_BYTES:
        os.remove(tmp)
        # fetch_url.sh отдаёт !=0, когда не смог достать страницу вообще
        return "unreachable" if r.returncode != 0 else "small"
    os.replace(tmp, dest)
    return "ok"


def strip_tags(html: str) -> str:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt)


def find_act_date(text: str, num: str) -> str | None:
    """Дата САМОГО АКТА — та, что стоит после «от» перед номером.

    «...определение от 26.03.2019 № 81-КГ19-2 13.06.2019 | Судебные решения...»
    Первая дата — акта, вторая — публикации на портале. Различить их можно
    только по позиции: дата акта идёт в связке «от <дата> № <номер>».
    """
    for m in re.finditer(re.escape(num), text):
        window = text[max(0, m.start() - 200):m.start()]
        found = None
        for d in re.finditer(r"от\s+(\d{2})\.(\d{2})\.(\d{4})", window):
            found = ".".join(d.groups())
        for d in re.finditer(r"от\s+(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", window):
            day, mon, y = d.groups()
            found = f"{int(day):02d}.{MONTHS.index(mon) + 1:02d}.{y}"
        if found:
            return found
    return None


def analyze(text: str, num: str, claimed_date: str | None = None) -> dict:
    """Ищет номер, даты, состав, предмет. Сверяет заявленную дату с текстом."""
    hits = text.count(num)
    if not hits:
        return {"status": "НЕ ПОДТВЕРЖДЕН", "hits": 0, "dates": [],
                "collegium": "", "subject": "", "date_match": None}

    # Даты собираем вокруг КАЖДОГО вхождения номера: первое обычно приходится
    # на заголовок страницы, где даты рядом нет (проверено на eg-online.ru).
    dates = []
    for m in re.finditer(re.escape(num), text):
        window = text[max(0, m.start() - 400):m.end() + 200]
        for d in DATE_RE.finditer(window):
            day, mon, y = d.groups()
            dates.append(f"{int(day):02d}.{MONTHS.index(mon) + 1:02d}.{y}")
        for d in DOT_DATE_RE.finditer(window):
            dates.append(".".join(d.groups()))
    dates = list(dict.fromkeys(dates))

    col = COLLEGIUM_RE.search(text)
    subj = ""
    low = text.lower()
    for k, v in SUBJECT_HINTS.items():
        if k in low:
            subj = v
            break

    act_date = find_act_date(text, num)

    # Заявленная охотником дата — кандидат. Решает страница.
    # Простого «дата есть где-то на странице» НЕДОСТАТОЧНО: рядом с номером
    # почти всегда стоит и дата публикации на портале. Именно на этом
    # споткнулись два рецензента совета по № 81-КГ19-2 (26.03.2019 — акт,
    # 13.06.2019 — публикация). Сверяем только с датой, идущей после «от».
    date_match = None
    if claimed_date:
        if act_date:
            date_match = claimed_date == act_date
            status = "ПОДТВЕРЖДЕН" if date_match else "РАСХОЖДЕНИЕ"
        else:
            date_match = claimed_date in dates
            status = "РАСХОЖДЕНИЕ"  # дату акта не опознали — на ручную сверку
    else:
        status = "ПОДТВЕРЖДЕН" if act_date and len(dates) == 1 else "РАСХОЖДЕНИЕ"

    return {"status": status, "hits": hits, "dates": dates, "act_date": act_date,
            "collegium": col.group(1) if col else "", "subject": subj,
            "date_match": date_match}


def snapshot(path: str, text: str) -> dict:
    """Слепок источника: без него conformance-прогон невоспроизводим (Н13)."""
    raw = open(path, "rb").read()
    return {
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "normalized_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parser_version": PARSER_VERSION,
        "bytes": len(raw),
    }


def verify(num: str, claimed_date: str | None = None, url: str | None = None) -> dict:
    """Проверяет акт. Пустой результат = 'не подтверждён', НЕ 'не существует'."""
    base = {"act": num, "claimed_date": claimed_date, "hits": 0, "dates": [],
            "collegium": "", "subject": "", "date_match": None, "url": "",
            "snapshot": None}

    if url:
        urls = [url]
    elif claimed_date:
        d = url_date(claimed_date)
        if not d:
            return {**base, "status": "НЕ ПОДТВЕРЖДЕН",
                    "note": f"дата «{claimed_date}» не в формате ДД.ММ.ГГГГ"}
        urls = [t.format(d=d, slug=slugify(num)) for _, t in SOURCES]
    else:
        return {**base, "status": "НЕ ПОДТВЕРЖДЕН",
                "note": "нужна дата акта (номер@ДД.ММ.ГГГГ) либо прямой --url"}

    unreachable = 0
    for u in urls:
        dest = os.path.join(CACHE, f"{slugify(num)}_{url_date(claimed_date or '') or 'direct'}.html")
        state = fetch(u, dest)
        if state == "unreachable":
            unreachable += 1
            continue
        if state == "small":
            continue
        text = strip_tags(open(dest, encoding="utf-8", errors="ignore").read())
        res = analyze(text, num, claimed_date)
        if res["status"] != "НЕ ПОДТВЕРЖДЕН":
            return {**base, **res, "url": u, "snapshot": snapshot(dest, text)}

    if unreachable == len(urls):
        return {**base, "status": "КАНАЛ НЕДОСТУПЕН", "url": urls[0],
                "note": "публикатор не ответил — проверка НЕ выполнена"}
    return {**base, "status": "НЕ ПОДТВЕРЖДЕН", "url": urls[0],
            "note": "страница не найдена по номеру и дате"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Верификация реквизитов судебных актов")
    ap.add_argument("acts", nargs="+", help="номер@ДД.ММ.ГГГГ, напр. 81-КГ19-2@26.03.2019")
    ap.add_argument("--url", help="прямой URL (для одного акта)")
    ap.add_argument("--eo", help="номер электронного опубликования (16 цифр, "
                                 "publication.pravo.gov.ru) — для одного акта")
    ap.add_argument("--json", action="store_true", help="выводить JSON")
    ap.add_argument("--emit", metavar="FILE", help="дописать результаты в ledger (JSON)")
    a = ap.parse_args()

    pairs = [parse_arg(x) for x in a.acts]
    if a.eo:
        if len(pairs) != 1:
            ap.error("--eo проверяет один акт: один идентификатор — один документ")
        out = [verify_eo(a.eo, pairs[0][0], pairs[0][1])]
    else:
        out = [verify(n, d, a.url if len(pairs) == 1 else None) for n, d in pairs]

    if a.emit:
        os.makedirs(os.path.dirname(os.path.abspath(a.emit)), exist_ok=True)
        prev = {}
        if os.path.exists(a.emit):
            try:
                prev = {r["act"]: r for r in json.load(open(a.emit, encoding="utf-8"))}
            except (json.JSONDecodeError, KeyError, TypeError):
                prev = {}
        prev.update({r["act"]: r for r in out})
        with open(a.emit, "w", encoding="utf-8") as f:
            json.dump(list(prev.values()), f, ensure_ascii=False, indent=2)
        print(f"ledger обновлен: {a.emit} ({len(prev)} актов)", file=sys.stderr)

    # Код возврата — чтобы вызывающий скрипт мог ветвиться, не разбирая текст.
    # Раньше main() всегда возвращал 0: неподтвержденный акт и упавший канал были
    # для автоматики неотличимы от подтвержденного.
    def rc() -> int:
        st = {r["status"] for r in out}
        if any("КАНАЛ" in x for x in st):
            return 4          # проверка не выполнена — повторить, не трактовать
        if any("НЕ ПОДТВЕРЖДЕН" in x for x in st):
            return 3          # акт не найден — нужна аттестация владельцем
        if any("РАСХОЖДЕНИЕ" in x for x in st):
            return 2          # номер есть, дата другая — сверить
        return 0

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return rc()

    print(f"{'АКТ':<18}{'ЗАЯВЛЕНО':<13}{'СТАТУС':<18}{'ДАТЫ В ТЕКСТЕ':<26}{'ПРЕДМЕТ':<26}ВХОЖД.")
    print("-" * 112)
    for r in out:
        print(f"{r['act']:<18}{(r.get('claimed_date') or '—'):<13}{r['status']:<18}"
              f"{', '.join(r['dates'])[:24]:<26}{r['subject'][:24]:<26}{r['hits']}")
        if r.get("note"):
            print(f"{'':<18}└─ {r['note']}")
    print("-" * 112)
    print("ПОДТВЕРЖДЕН      — номер и заявленная дата найдены в тексте акта.")
    print("РАСХОЖДЕНИЕ      — номер есть, дата другая: сверить, где дата акта, где публикации.")
    print("НЕ ПОДТВЕРЖДЕН   — страница не найдена. НЕ означает, что акта не существует:")
    print("                   до подачи акт подтверждает владелец лично (HUMAN_ATTESTED).")
    print("КАНАЛ НЕДОСТУПЕН — публикатор не ответил. Проверка НЕ выполнена, повторить.")
    print("Коды возврата: 0 подтвержден · 2 расхождение даты · 3 не подтвержден · 4 канал недоступен.")
    return rc()


def demo() -> None:
    """Самопроверка без сети. Покрывает и разбор, и сборку URL — прежняя версия
    падала именно на URL, а demo его не трогал и потому проходил."""
    # 1. Дата акта против даты публикации — ровно та ловушка, что стоила совету двух рецензентов.
    sample = ("Главная Документы Судебные решения О разделе совместно нажитого имущества "
              "Верховный Суд РФ определение от 26.03.2019 № 81-КГ19-2 13.06.2019 | "
              "Судебные решения Судебная коллегия по гражданским делам в составе: "
              "председательствующего Юрьева И.М., судей Назаренко Т.Н.")
    r = analyze(sample, "81-КГ19-2", "26.03.2019")
    assert r["hits"] == 1 and r["date_match"] is True, r
    assert r["act_date"] == "26.03.2019", f"дата акта берётся после «от»: {r}"
    assert r["status"] == "ПОДТВЕРЖДЕН", "заявленная дата совпала с датой акта"
    assert r["subject"] == "раздел имущества супругов" and r["collegium"].startswith("Юрьева"), r

    # 2. Модель назвала дату публикации вместо даты акта — обязано быть РАСХОЖДЕНИЕ.
    r2 = analyze(sample, "81-КГ19-2", "13.06.2019")
    assert r2["status"] == "РАСХОЖДЕНИЕ" and r2["date_match"] is False, r2
    assert "13.06.2019" in r2["dates"], "дата публикации на странице есть — но она не дата акта"

    # 3. Номера нет в тексте.
    assert analyze("текст без нужного номера", "5-КГ21-101-К2")["status"] == "НЕ ПОДТВЕРЖДЕН"

    # 4. СБОРКА URL — то, что было сломано: дата обязана попасть в адрес.
    u = SOURCES[0][1].format(d=url_date("17.05.2016"), slug=slugify("41-КГ16-17"))
    assert u == ("https://legalacts.ru/sud/"
                 "opredelenie-verkhovnogo-suda-rf-ot-17052016-n-41-kg16-17/"), u
    assert "ot--n-" not in u, "пустая дата в URL — тот самый дефект"
    assert url_date("кривая дата") is None, "кривую дату не подставлять молча"

    # 5. Разбор аргумента «номер@дата».
    assert parse_arg("81-КГ19-2@26.03.2019") == ("81-КГ19-2", "26.03.2019")
    assert parse_arg("81-КГ19-2") == ("81-КГ19-2", None)

    # 6. eoNumber. Заголовки — дословные слепки живых страниц публикатора
    # (проверено 04.08.2026: ...0011 → 420-ФЗ, соседний ...0012 → уже 421-ФЗ).
    t420 = "Федеральный закон от 30.11.2024 № 420-ФЗ ∙ Официальное опубликование правовых актов"
    t421 = "Федеральный закон от 30.11.2024 № 421-ФЗ ∙ Официальное опубликование правовых актов"
    assert parse_eo_title(t420) == ("30.11.2024", "420-ФЗ"), parse_eo_title(t420)
    ok = analyze_eo(t420, "420-ФЗ", "30.11.2024")
    assert ok["status"] == "ПОДТВЕРЖДЕН", ok
    # ГЛАВНОЕ: чужой идентификатор открывает существующую страницу другого акта.
    # Прежде «страница есть» само по себе считалось подтверждением.
    alien = analyze_eo(t421, "420-ФЗ", "30.11.2024")
    assert alien["status"] == "НЕ ПОДТВЕРЖДЕН" and "ДРУГОЙ акт" in alien["note"], alien
    # Несуществующий eoNumber отдаёт 404 полноразмерной страницей без заголовка.
    # Диагноз обязан отличаться от «заголовок есть, но реквизитов в нём нет»:
    # первое — идентификатора не существует, второе — сменилась вёрстка публикатора.
    empty = analyze_eo("", "420-ФЗ", "30.11.2024")
    assert empty["status"] == "НЕ ПОДТВЕРЖДЕН" and "без заголовка" in empty["note"], empty
    # Заголовок без пары «от … №» не даёт сверить реквизит — подтверждать нечем.
    assert analyze_eo("Официальное опубликование правовых актов",
                      "420-ФЗ", None)["status"] == "НЕ ПОДТВЕРЖДЕН"
    # Номер тот же, дата у публикатора другая — расхождение, не подтверждение.
    diff = analyze_eo(t420, "420-ФЗ", "01.12.2024")
    assert diff["status"] == "РАСХОЖДЕНИЕ" and diff["date_match"] is False, diff
    # Приказ ведомства с косой чертой в номере разбирается так же.
    tprikaz = ("Приказ Федеральной службы государственной регистрации, кадастра и "
               "картографии от 28.10.2024 № П/0335/24 ∙ Официальное опубликование")
    assert parse_eo_title(tprikaz) == ("28.10.2024", "П/0335/24"), parse_eo_title(tprikaz)
    assert analyze_eo(tprikaz, "П/0335/24", "28.10.2024")["status"] == "ПОДТВЕРЖДЕН"
    # Написание номера различается у источников — сравнение это переживает.
    assert same_act_number("420-ФЗ", "420-фз") and same_act_number("420 ФЗ", "420-ФЗ")
    assert not same_act_number("420-ФЗ", "421-ФЗ")
    # Кривой идентификатор в сеть не ходит вовсе.
    assert verify_eo("12345", "420-ФЗ")["status"] == "НЕ ПОДТВЕРЖДЕН"

    print("demo: разбор, сверка даты, сборка URL и сверка eoNumber корректны")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        sys.exit(main())
