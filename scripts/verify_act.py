#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_act.py — верификация реквизитов судебных актов без участия модели.

Зачем. В прогоне дела боевое-дело доверификация четырех актов силами LLM стоила
243 605 токенов. Операция механическая: скачать страницу, найти номер, дату,
состав коллегии, ключевую формулировку. Модель нужна только чтобы прочитать
итог. Этот скрипт делает проверку за ~0 токенов и точнее: именно машинная
сверка вскрыла, что 13.06.2019 у Определения № 81-КГ19-2 — дата публикации
на портале, а не дата судебного акта.

Использование:
    python3 scripts/verify_act.py 81-КГ19-2 5-КГ22-82-К2
    python3 scripts/verify_act.py --json 41-КГ16-17
    python3 scripts/verify_act.py --url https://... 56-КГ23-6-К9

Выход: таблица «акт → статус → дата в тексте → предмет → источник».
Статусы: ПОДТВЕРЖДЕН · НЕ НАЙДЕН · РАСХОЖДЕНИЕ (номер есть, дата спорна).

ponytail: кеш на диске, без БД — актов десятки, не миллионы.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FETCH = os.path.join(ROOT, "scripts", "fetch_url.sh")
CACHE = os.path.expanduser("~/.cache/legal_acts")

# Шаблоны публикаторов. Порядок = приоритет доверия.
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


def slugify(num: str) -> str:
    """81-КГ19-2 -> 81-kg19-2 (латиница публикаторов)."""
    table = str.maketrans({"К": "k", "Г": "g", "Э": "e", "С": "s", "П": "p",
                           "А": "a", "В": "v", "Д": "d", "О": "o", "У": "u"})
    return num.translate(table).lower()


def fetch(url: str, dest: str) -> bool:
    if os.path.exists(dest) and os.path.getsize(dest) > 2000:
        return True
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        subprocess.run(["bash", FETCH, url, dest], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return os.path.exists(dest) and os.path.getsize(dest) > 2000


def strip_tags(html: str) -> str:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt)


def analyze(text: str, num: str) -> dict:
    """Ищет номер, даты, состав, предмет. Возвращает вердикт."""
    hits = text.count(num)
    if not hits:
        return {"status": "НЕ НАЙДЕН", "hits": 0, "dates": [], "collegium": "", "subject": ""}

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

    status = "ПОДТВЕРЖДЕН" if dates else "РАСХОЖДЕНИЕ"
    if len(dates) > 1:
        status = "РАСХОЖДЕНИЕ"
    return {"status": status, "hits": hits, "dates": dates,
            "collegium": col.group(1) if col else "", "subject": subj}


def verify(num: str, url: str | None = None) -> dict:
    dest = os.path.join(CACHE, slugify(num) + ".html")
    urls = [url] if url else [t.format(d="", slug=slugify(num)) for _, t in SOURCES]
    for u in urls:
        if not u:
            continue
        if fetch(u, dest):
            text = strip_tags(open(dest, encoding="utf-8", errors="ignore").read())
            res = analyze(text, num)
            res["url"] = u
            res["act"] = num
            if res["status"] != "НЕ НАЙДЕН":
                return res
    return {"act": num, "status": "НЕ НАЙДЕН", "hits": 0, "dates": [],
            "collegium": "", "subject": "", "url": urls[0] if urls else ""}


def main() -> int:
    ap = argparse.ArgumentParser(description="Верификация реквизитов судебных актов")
    ap.add_argument("acts", nargs="+", help="номера, напр. 81-КГ19-2")
    ap.add_argument("--url", help="прямой URL (для одного акта)")
    ap.add_argument("--json", action="store_true", help="выводить JSON")
    a = ap.parse_args()

    out = [verify(n, a.url if len(a.acts) == 1 else None) for n in a.acts]

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"{'АКТ':<18}{'СТАТУС':<14}{'ДАТЫ В ТЕКСТЕ':<26}{'ПРЕДМЕТ':<28}ВХОЖД.")
    print("-" * 96)
    for r in out:
        print(f"{r['act']:<18}{r['status']:<14}{', '.join(r['dates'])[:24]:<26}"
              f"{r['subject'][:26]:<28}{r['hits']}")
    print("-" * 96)
    print("ПОДТВЕРЖДЕН — номер и дата найдены в тексте акта.")
    print("РАСХОЖДЕНИЕ — номер есть, дат несколько: сверить, какая дата акта, "
          "а какая дата публикации.")
    print("НЕ НАЙДЕН — в открытых публикаторах не обнаружен, в документ не включать.")
    return 0


def demo() -> None:
    """Самопроверка разбора без сети: главный риск — путаница даты акта
    и даты публикации, ровно она стоила совету двух рецензентов."""
    sample = ("Главная Документы Судебные решения О разделе совместно нажитого имущества "
              "Верховный Суд РФ определение от 26.03.2019 № 81-КГ19-2 13.06.2019 | "
              "Судебные решения Судебная коллегия по гражданским делам в составе: "
              "председательствующего Юрьева И.М., судей Назаренко Т.Н.")
    r = analyze(sample, "81-КГ19-2")
    assert r["hits"] == 1, r
    assert "26.03.2019" in r["dates"], r
    assert r["status"] == "РАСХОЖДЕНИЕ", "две даты рядом обязаны дать РАСХОЖДЕНИЕ"
    assert r["subject"] == "раздел имущества супругов", r
    assert r["collegium"].startswith("Юрьева"), r

    empty = analyze("текст без нужного номера", "5-КГ21-101-К2")
    assert empty["status"] == "НЕ НАЙДЕН", empty
    print("demo: разбор корректен, дата акта и дата публикации различаются")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        sys.exit(main())
