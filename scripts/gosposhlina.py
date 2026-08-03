#!/usr/bin/env python3
"""gosposhlina.py — государственная пошлина из текста НК, а не из памяти модели.

Шкалу берём разбором ст. 333.19 (СОЮ, мировые судьи, ВС РФ) и ст. 333.21 (арбитраж)
прямо из локального корпуса `knowledge/kodeksy/nk-rf-gosposhlina.md`. Зашитая таблица
устаревает молча — а ставки менялись дважды за два года; текст корпуса обновляется
`update_legal_corpus.py` и несёт дату редакции, которую скрипт печатает рядом с суммой.

Отдельная причина существовать: колонка. Для физлица и для организации пошлина по
неимущественному иску отличается в разы, и её уже путали на боевом деле (урок
23.07.2026: «пошлина 20 000» была колонкой организаций, у физлица — 5 000).
Здесь статус — обязательный аргумент, а не подразумеваемый.

    gosposhlina.py --cena 450000                     имущественный иск, СОЮ
    gosposhlina.py --cena 450000 --sud arbitrazh     то же в арбитраже
    gosposhlina.py --neimushchestvennyy --status fiz
    gosposhlina.py --prikaz --cena 80000             судебный приказ (50%)
    gosposhlina.py --cena 450000 --instanciya apellyaciya
    gosposhlina.py --selftest                        проверка без сети

Код возврата: 0 — посчитано; 1 — не хватает данных (шкала не разобрана, нет корпуса).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Путь переопределяем: без этого копия скрипта вне проекта (мутационная проверка,
# разбор в песочнице) не находит корпус, молча пропускает сквозные проверки и
# selftest зеленеет на сломанном коде.
CORPUS = os.environ.get("THEMIS_NK_CORPUS") or os.path.join(
    ROOT, "knowledge", "kodeksy", "nk-rf-gosposhlina.md")

# Пошлина за жалобу — ФИКСИРОВАННАЯ ставка, а не доля от пошлины первой инстанции.
# Подп. 19-21 п. 1 ст. 333.19 и ст. 333.21 НК РФ дают прямые суммы, одинаковые для
# имущественного и неимущественного спора и не зависящие от цены иска.
#
# До 03.08.2026 здесь стояли выдуманные доли 50/70/100 процентов. Для физлица
# апелляция считалась как 1 500 руб. при норме 3 000 — двукратная недоплата, а это
# оставление жалобы без движения и пропуск срока. Ставки больше не зашиваются
# константами: читаются из текста статьи в корпусе (правило проекта — параметры
# внешних систем не угадывать, а брать из самой системы).
INSTANCE_ITEM = {
    "pervaya": (None, "первая инстанция"),
    "apellyaciya": ("19", "апелляционная (частная) жалоба — подп. 19 п. 1"),
    "kassaciya": ("20", "кассационная жалоба — подп. 20 п. 1"),
    "kassaciya_vs": ("21", "кассационная (надзорная) жалоба в ВС РФ — подп. 21 п. 1"),
    "nadzor": ("21", "надзорная жалоба в ВС РФ — подп. 21 п. 1"),
}


def appeal_rates(text: str, item: str) -> dict:
    """Фиксированные ставки подпункта 19/20/21 из текста статьи."""
    m = re.search(rf"^{item}\)\s", text, re.M)
    if not m:
        return {}
    tail = text[m.end():]
    nxt = re.search(r"^\d{1,2}\)\s", tail, re.M)
    body = tail[: nxt.start()] if nxt else tail[:800]
    fiz = re.search(r"для физических лиц\s*[-—]\s*([\d\s]+)", body)
    org = re.search(r"для организаций\s*[-—]\s*([\d\s]+)", body)
    out = {}
    if fiz:
        out["fiz"] = num(fiz.group(1))
    if org:
        out["org"] = num(org.group(1))
    return out


def num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def corpus_text() -> str:
    try:
        return open(CORPUS, encoding="utf-8").read()
    except OSError:
        return ""


def redaction() -> str:
    m = re.search(r'^дата_редакции:\s*"([^"]*)"', corpus_text(), re.M)
    return m.group(1) if m else "?"


def article(number: str) -> str:
    """Текст статьи из корпуса."""
    text = corpus_text()
    m = re.search(rf"^### Статья {re.escape(number)}\.\s.*$", text, re.M)
    if not m:
        return ""
    tail = text[m.start():]
    nxt = re.search(r"^### Статья ", tail[10:], re.M)
    return tail[: nxt.start() + 10] if nxt else tail


TIER_FROM = re.compile(
    r"от\s+([\d\s]+)\s*рубл\w*\s+до\s+([\d\s]+)\s*рубл\w*\s*[-—]\s*([\d\s]+)\s*рубл\w*"
    r"\s*плюс\s*([\d,]+)\s*процент\w*\s*суммы,\s*превышающей\s+([\d\s]+)")
TIER_FIRST = re.compile(r"до\s+([\d\s]+)\s*рубл\w*\s*[-—]\s*([\d\s]+)\s*рубл\w*\s*;")
TIER_LAST = re.compile(
    r"свыше\s+([\d\s]+)\s*рубл\w*\s*[-—]\s*([\d\s]+)\s*рубл\w*\s*плюс\s*([\d,]+)\s*процент\w*"
    r"\s*суммы,\s*превышающей\s+([\d\s]+)\s*рубл\w*(?:,\s*но не более\s+([\d\s]+))?")


def parse_scale(text: str) -> list[dict]:
    """Ступени имущественной шкалы из текста статьи, по возрастанию цены иска."""
    block = text.split("при цене иска", 1)
    if len(block) < 2:
        return []
    body = block[1].split("2)", 1)[0]
    tiers: list[dict] = []
    m = TIER_FIRST.search(body)
    if m:
        tiers.append({"upto": num(m.group(1)), "base": num(m.group(2)),
                      "pct": 0.0, "over": 0, "cap": None})
    for m in TIER_FROM.finditer(body):
        tiers.append({"upto": num(m.group(2)), "base": num(m.group(3)),
                      "pct": float(m.group(4).replace(",", ".")), "over": num(m.group(5)),
                      "cap": None})
    m = TIER_LAST.search(body)
    if m:
        tiers.append({"upto": None, "base": num(m.group(2)),
                      "pct": float(m.group(3).replace(",", ".")), "over": num(m.group(4)),
                      "cap": num(m.group(5)) if m.group(5) else None})
    return tiers


def flat_rates(text: str) -> dict:
    """Пошлина по неимущественному иску: физлицо и организация."""
    block = text.split("не подлежащего оценке", 1)
    if len(block) < 2:
        return {}
    body = block[1][:600]
    fiz = re.search(r"для физических лиц\s*[-—]\s*([\d\s]+)", body)
    org = re.search(r"для организаций\s*[-—]\s*([\d\s]+)", body)
    out = {}
    if fiz:
        out["fiz"] = num(fiz.group(1))
    if org:
        out["org"] = num(org.group(1))
    return out


def duty_property(price: int, tiers: list[dict]) -> int:
    if price <= 0:
        raise ValueError("цена иска должна быть больше нуля")
    for t in tiers:
        if t["upto"] is None or price <= t["upto"]:
            value = t["base"] + (price - t["over"]) * t["pct"] / 100
            if t["cap"]:
                value = min(value, t["cap"])
            return int(round(value))
    raise ValueError("шкала не покрывает эту цену иска")


def main() -> int:
    ap = argparse.ArgumentParser(description="Госпошлина по НК РФ из локального корпуса")
    ap.add_argument("--cena", type=int, help="цена иска в рублях (имущественный иск)")
    ap.add_argument("--neimushchestvennyy", action="store_true",
                    help="иск неимущественного характера или не подлежащий оценке")
    ap.add_argument("--status", choices=["fiz", "org"], help="заявитель: физлицо или организация")
    ap.add_argument("--sud", choices=["soyu", "arbitrazh"], default="soyu",
                    help="соу/мировые/ВС (ст. 333.19) либо арбитраж (ст. 333.21)")
    ap.add_argument("--instanciya", choices=sorted(INSTANCE_ITEM), default="pervaya")
    ap.add_argument("--prikaz", action="store_true", help="заявление о вынесении судебного приказа")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    art_num = "333.19" if a.sud == "soyu" else "333.21"
    text = article(art_num)
    if not text:
        print(f"нет ст. {art_num} в корпусе ({CORPUS}). Выгрузить: "
              f"python3 scripts/update_legal_corpus.py --init --doc nk-rf-gosposhlina",
              file=sys.stderr)
        return 1

    red = redaction()
    print(f"Источник: ст. {art_num} НК РФ, корпус от {red} "
          f"({'СОЮ, мировые судьи, ВС РФ' if a.sud == 'soyu' else 'арбитражные суды'})")

    item, label = INSTANCE_ITEM[a.instanciya]
    if item:
        # Жалоба: ставка фиксированная, цена иска и характер требования не влияют.
        if not a.status:
            print("для жалобы обязателен --status fiz|org: ставки различаются в разы "
                  "(физлицо 3 000, организация 15 000 — апелляция в СОЮ)", file=sys.stderr)
            return 1
        rates = appeal_rates(text, item)
        if a.status not in rates:
            print(f"ставка подп. {item} для «{a.status}» не разобрана из текста ст. {art_num} — "
                  "проверить корпус", file=sys.stderr)
            return 1
        total = rates[a.status]
        who = "физическое лицо" if a.status == "fiz" else "организация"
        print(f"{label}, {who}: " + f"{total:,}".replace(",", " ") + " руб.")
        if a.cena:
            print(f"Цена иска {a.cena:,} руб. на пошлину за жалобу НЕ влияет: "
                  "подп. 19-21 дают фиксированную сумму.".replace(",", " "))
        print(f"\nК УПЛАТЕ: {total:,} руб.".replace(",", " "))
        return 0

    if a.neimushchestvennyy:
        rates = flat_rates(text)
        if not a.status:
            print("для неимущественного иска обязателен --status fiz|org: ставки различаются "
                  "в разы, и их уже путали на боевом деле", file=sys.stderr)
            return 1
        if a.status not in rates:
            print(f"ставка для «{a.status}» не разобрана из текста статьи", file=sys.stderr)
            return 1
        total = rates[a.status]
        who = "физическое лицо" if a.status == "fiz" else "организация"
        print(f"Неимущественный иск, {who}: " + f"{total:,}".replace(",", " ") + " руб.")
        print(f"\nК УПЛАТЕ: {total:,} руб.".replace(",", " "))
        return 0

    if not a.cena:
        ap.print_help()
        return 1
    tiers = parse_scale(text)
    if not tiers:
        print("шкала не разобрана из текста статьи — проверить корпус", file=sys.stderr)
        return 1
    base = duty_property(a.cena, tiers)
    total = base
    note = ""
    if a.prikaz:
        total = int(round(base * 0.5))
        note = "Судебный приказ — 50% от пошлины по исковому заявлению."
    print(f"Цена иска: {a.cena:,} руб.".replace(",", " "))
    print(f"Пошлина по шкале (исковое заявление): {base:,} руб.".replace(",", " "))
    if note:
        print(note.strip())
    print(f"\nК УПЛАТЕ: {total:,} руб.".replace(",", " "))
    print("\nПроверить льготы и освобождения: ст. 333.35, 333.36 НК РФ — "
          "скрипт их не применяет.")
    return 0


def _cli_total(args: list[str]) -> int | None:
    """Прогнать сам скрипт как CLI и вернуть сумму «К УПЛАТЕ». None — отказ считать."""
    import subprocess
    env = dict(os.environ, THEMIS_NK_CORPUS=CORPUS)
    r = subprocess.run([sys.executable, os.path.abspath(__file__)] + args,
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return None
    m = re.search(r"К УПЛАТЕ:\s*([\d\s]+)", r.stdout)
    return num(m.group(1)) if m else None


def _cli_refuses(args: list[str], word: str) -> bool:
    """Отказ должен быть ОСМЫСЛЕННЫМ, а не падением: иначе мутация, убравшая
    требование статуса, выглядит как корректный отказ."""
    import subprocess
    env = dict(os.environ, THEMIS_NK_CORPUS=CORPUS)
    r = subprocess.run([sys.executable, os.path.abspath(__file__)] + args,
                       capture_output=True, text=True, env=env)
    return r.returncode != 0 and word in r.stderr and "Traceback" not in r.stderr


def selftest() -> int:
    """Шкала на фикстуре + сверка с реальным корпусом, если он на диске."""
    fixture = """### Статья 333.19. Размеры
1. ... в следующих размерах:
1) при подаче искового заявления имущественного характера, при цене иска:
до 100 000 рублей - 4000 рублей;
от 100 001 рубля до 300 000 рублей - 4000 рублей плюс 3 процента суммы, превышающей 100 000 рублей;
от 300 001 рубля до 500 000 рублей - 10 000 рублей плюс 2,5 процента суммы, превышающей 300 000 рублей;
свыше 100 000 000 рублей - 314 000 рублей плюс 0,15 процента суммы, превышающей 100 000 000 рублей, но не более 900 000 рублей;
2) при подаче заявления о вынесении судебного приказа - 50 процентов
3) при подаче искового заявления имущественного характера, не подлежащего оценке, искового заявления неимущественного характера:
для физических лиц - 3000 рублей;
для организаций - 20 000 рублей;
19) при подаче апелляционной жалобы, частной жалобы, а также при подаче кассационной жалобы на судебный приказ:
для физических лиц - 3000 рублей;
для организаций - 15 000 рублей;
20) при подаче кассационной жалобы:
для физических лиц - 5000 рублей;
для организаций - 20 000 рублей;
21) при подаче кассационной или надзорной жалобы в Верховный Суд Российской Федерации:
для физических лиц - 7000 рублей;
для организаций - 25 000 рублей.
"""
    tiers = parse_scale(fixture)
    rates = flat_rates(fixture)
    checks = [
        ("ступени разобраны", len(tiers) == 4),
        ("нижняя ступень плоская", duty_property(50_000, tiers) == 4000),
        ("граница ступени включена", duty_property(100_000, tiers) == 4000),
        ("процент считается от превышения", duty_property(200_000, tiers) == 7000),
        ("вторая ступень", duty_property(450_000, tiers) == 13750),
        ("потолок применяется", duty_property(10_000_000_000, tiers) == 900_000),
        ("физлицо и организация различаются", rates == {"fiz": 3000, "org": 20000}),
        ("нулевая цена иска — ошибка", _raises(lambda: duty_property(0, tiers))),
        # Жалоба: ФИКСИРОВАННАЯ ставка подпункта, а не доля от первой инстанции.
        # Раньше здесь стояли выдуманные 50/70/100 процентов и физлицу считалась
        # апелляция 1 500 вместо 3 000 — двукратная недоплата и жалоба без движения.
        ("апелляция физлицу — 3000, не доля", appeal_rates(fixture, "19").get("fiz") == 3000),
        ("апелляция организации — 15 000", appeal_rates(fixture, "19").get("org") == 15000),
        ("кассация физлицу — 5000", appeal_rates(fixture, "20").get("fiz") == 5000),
        ("кассация организации — 20 000", appeal_rates(fixture, "20").get("org") == 20000),
        ("жалоба в ВС физлицу — 7000", appeal_rates(fixture, "21").get("fiz") == 7000),
        ("жалоба в ВС организации — 25 000", appeal_rates(fixture, "21").get("org") == 25000),
        ("ставка жалобы не равна ставке неимущественного иска у организации",
         appeal_rates(fixture, "19").get("org") != rates.get("org")),
        ("несуществующий подпункт не выдумывается", appeal_rates(fixture, "97") == {}),
    ]

    # Сквозная проверка МАРШРУТА, а не только функции: мутация «жалоба обрабатывается
    # как первая инстанция» проходила мимо тестов на appeal_rates и всплыла бы уже в суде.
    if article("333.19"):
        checks += [
            ("CLI: апелляция физлицу печатает 3 000", _cli_total(["--status", "fiz",
                                                                 "--instanciya", "apellyaciya"]) == 3000),
            ("CLI: кассация организации печатает 20 000", _cli_total(["--status", "org",
                                                                      "--instanciya", "kassaciya"]) == 20000),
            ("CLI: цена иска не меняет пошлину за жалобу",
             _cli_total(["--status", "fiz", "--instanciya", "apellyaciya", "--cena", "450000"]) == 3000),
            ("CLI: первая инстанция считает по шкале",
             _cli_total(["--cena", "450000"]) == 13750),
            ("CLI: жалоба без --status отвергается внятно, а не падением",
             _cli_refuses(["--instanciya", "apellyaciya"], "--status")),
        ]
    # Сверка с живым корпусом: если он есть, шкала обязана разобраться и там
    real = article("333.19")
    if real:
        rt, rr = parse_scale(real), flat_rates(real)
        checks += [
            ("живой корпус: апелляция СОЮ физлицу 3000",
             appeal_rates(real, "19").get("fiz") == 3000),
            ("живой корпус: кассация СОЮ организации 20 000",
             appeal_rates(real, "20").get("org") == 20000),
            ("корпус: шкала разобрана", len(rt) >= 8),
            ("корпус: ставки физлица и организации найдены", set(rr) == {"fiz", "org"}),
            ("корпус: 100 000 руб. → 4 000", duty_property(100_000, rt) == 4000),
            ("корпус: 1 000 000 руб. → 25 000", duty_property(1_000_000, rt) == 25000),
        ]
    else:
        print("  · корпус НК не выгружен — сверка с живой шкалой пропущена")
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
