#!/usr/bin/env python3
"""registry_check.py — сверка трех реестров с тем, что реально лежит на диске.

Конституция обязывает модель руками держать три книги в согласии: `cases/_index.md`,
`cases/_clients.md` и `_client.md` каждого доверителя. Руками — значит с пропусками:
дело заводится, строка забывается, и следующая сессия дела не находит.

Скрипт не переписывает реестры (там курированные заметки, псевдонимы, предупреждения —
их сгенерировать нельзя), а называет расхождения. `--fix` дописывает недостающие строки
в конец таблицы заготовками, ничего не затирая.

    registry_check.py                 отчет о расхождениях (код 1, если они есть)
    registry_check.py --fix           + дописать недостающие строки заготовками
    registry_check.py --selftest      проверка без сети и без диска проекта

ПД доверителей из реестров наружу не выводятся: в отчете только имена папок.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SERVICE = re.compile(r"^[._]")  # _templates, _logs, _archive_*, .DS_Store — не дела
# Чужое, забредшее в папку доверителей. Это не «дело без строки в реестре», это мусор,
# и путать их нельзя: 10 МБ node_modules внутри cases/ реально нашлись 03.08.2026.
ALIEN = {"node_modules", "knowledge", "scripts", "venv", ".venv", "__pycache__", "bin", "docs"}
# Признак настоящей папки дела — рабочая структура протокола.
CASE_MARKS = ("00_intake", ".agent/context", "02_hearings", ".agent/drafts", "_case.md")


def looks_like_case(path: str) -> bool:
    return any(os.path.exists(os.path.join(path, m)) for m in CASE_MARKS)


def scan_disk(cases: str) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """{папка клиента: [папки дел]}, клиенты без _client.md, чужие папки."""
    clients: dict[str, list[str]] = {}
    no_profile: list[str] = []
    alien: list[str] = []
    for entry in sorted(os.listdir(cases)):
        client_dir = os.path.join(cases, entry)
        if not os.path.isdir(client_dir) or SERVICE.match(entry):
            continue
        if entry in ALIEN:
            alien.append(entry)
            continue
        cases_of = []
        for d in sorted(os.listdir(client_dir)):
            sub = os.path.join(client_dir, d)
            if not os.path.isdir(sub) or SERVICE.match(d):
                continue
            if d in ALIEN:
                alien.append(f"{entry}/{d}")
                continue
            if looks_like_case(sub):
                cases_of.append(d)
        clients[entry] = cases_of
        if not os.path.isfile(os.path.join(client_dir, "_client.md")):
            no_profile.append(entry)
    return clients, no_profile, alien


def edit_distance_le1(a: str, b: str) -> bool:
    """Имена различаются не больше чем одной правкой (замена, вставка, удаление)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    short, long = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped, j = True, j + 1
    return True


def disambiguated(cases: str, a: str, b: str) -> bool:
    """Владелец уже разобрал пару и записал ответ в профиль доверителя.

    Однофамильцы — не опечатка: две разные женщины с одной фамилией дают папки
    `familiya` и `familiya-ab`, и вторая начинается с первой. Спрашивать про них
    каждый прогон — значит приучить владельца пролистывать предупреждение, а
    вместе с ним и настоящую опечатку. Поэтому ответ хранится на диске: строка в
    `_client.md` любой из двух папок, где названа соседняя папка и сказано, что
    это разные лица. Совпадение ФИО тут не годится: у одного профиля ФИО бывает
    неполным, у другого стоит девичья фамилия — из строк «Фамильева» и «Фамильева
    Мария Ивановна» вывод о тождестве не следует ни в одну сторону.

    Само ФИО ни при каком исходе не читается в вывод: файл публичный.
    """
    for own, other in ((a, b), (b, a)):
        path = os.path.join(cases, own, "_client.md")
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            low = line.lower()
            if "разные лица" in low and re.search(
                    rf"(?<![a-z0-9-]){re.escape(other)}(?![a-z0-9-])", low):
                return True
    return False


def near_twins(clients: dict[str, list[str]], cases: str) -> list[str]:
    """Пары папок-двойников: имена почти совпадают, а материалы есть у одной.

    Прецедент 03.08.2026: на диске одновременно лежали две папки одного дела,
    имена которых различались одной буквой (`…v-lf` и `…w-lf`); в первой 1191
    файл, во второй ноль, и обе были перечислены в обоих реестрах. Каждая по
    отдельности выглядит законной, расхождения реестра с диском нет — поэтому
    ни одна проверка «строка ↔ папка» такого не видит. Опознается только
    сравнением имен между собой: одна правка в имени — это опечатка при
    заведении дела, а не второй доверитель.

    Имена реальных папок здесь намеренно не приводятся: имя папки дела — фамилия
    доверителя, то есть персональные данные, а файл лежит в публичном репозитории.
    """
    names = sorted(clients)
    weight = {n: sum(len(files) for _, _, files in os.walk(os.path.join(cases, n)))
              for n in names}
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            twin = edit_distance_le1(a, b) or a.startswith(b) or b.startswith(a)
            if not twin:
                continue
            # Однофамильцы, уже разведенные владельцем в профиле, — не находка.
            # Пустая папка остается находкой при любой пометке: у пустышки нечего
            # разводить, там нет ни дела, ни доверителя.
            if weight[a] and weight[b] and disambiguated(cases, a, b):
                continue
            # Двойник опасен, когда одна из папок пуста: система не знает, какая
            # каноническая, и дело доверителя живет рядом с пустышкой.
            if weight[a] == 0 or weight[b] == 0:
                empty, full = (a, b) if weight[a] == 0 else (b, a)
                out.append(f"{empty} (файлов 0) и {full} (файлов {weight[full]}) — "
                           "имена различаются одним знаком; пустая папка почти "
                           "наверняка опечатка при заведении дела")
            else:
                out.append(f"{a} (файлов {weight[a]}) и {b} (файлов {weight[b]}) — "
                           "имена почти совпадают, проверить, не одно ли это лицо")
    return out


def rows_of(path: str) -> str:
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def registry_keys(text: str) -> set[str]:
    """Имена папок из реестра клиентов — из ЛЮБОЙ колонки, озаглавленной «Папка».

    Физлица держат папку первой колонкой, компании — колонкой «Папка (владелец)»:
    по правилу маршрутизации дело компании живет в папке владельца, и имя папки
    стоит четвертой ячейкой. Проверка, читавшая только первую ячейку, объявляла
    пропавшими четырех зарегистрированных клиентов. Ложная тревога опаснее
    молчания: владелец приучается пролистывать предупреждение, а вместе с ним
    пролистает и настоящий пропуск.

    Таблица без колонки «Папка» (связи между клиентами) не читается вовсе:
    упоминание в графе «Клиент Б» — не регистрация. Подстрочного поиска здесь
    нет намеренно: `ivanov` не должен находиться внутри строки про `ivanov-petr`.
    """
    keys: set[str] = set()
    cols: list[int] | None = None  # None — заголовка еще не было
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            cols = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if any(c.lower().startswith("папка") for c in cells):
            cols = [i for i, c in enumerate(cells) if c.lower().startswith("папка")]
            continue
        if any(c.lower() in ("клиент", "компания", "фио") or c.lower().startswith(
                ("клиент ", "компания ")) for c in cells):
            cols = []  # заголовок есть, колонки «Папка» в нем нет — таблица не про папки
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        for i in (cols if cols is not None else [0]):
            if i < len(cells) and cells[i]:
                keys.add(cells[i])
    return keys


def check(cases: str) -> dict:
    clients, no_profile, alien = scan_disk(cases)
    index_txt = rows_of(os.path.join(cases, "_index.md"))
    clients_txt = rows_of(os.path.join(cases, "_clients.md"))

    clients_keys = registry_keys(clients_txt)
    missing_in_index, missing_in_clients = [], []
    for client, case_dirs in clients.items():
        # Раньше был подстрочный фолбэк «client not in clients_txt»: папка ivanov
        # «находилась» в строке про ivanov-petr, и пропуск клиента маскировался.
        # Ключ ищем только точной ячейкой таблицы.
        if client not in clients_keys:
            missing_in_clients.append(client)
        for c in case_dirs:
            if f"{client}/{c}" not in index_txt:
                missing_in_index.append(f"{client}/{c}")

    # обратная сторона: строка есть, папки нет — реестр показывает несуществующее
    ghost = []
    # Папка дела — latin-kebab и начинается с буквы. Иначе в «призраки» попадают
    # номера дел из соседней колонки: «2-1309/2026» синтаксически похож на путь.
    for m in re.finditer(r"\|\s*([a-z][a-z0-9-]*/[a-z][a-z0-9._-]*)\s*\|", index_txt):
        rel = m.group(1)
        if not os.path.isdir(os.path.join(cases, rel)):
            ghost.append(rel)

    return {"clients": clients, "no_profile": no_profile, "alien": alien,
            "twins": near_twins(clients, cases),
            "missing_in_index": missing_in_index,
            "missing_in_clients": sorted(set(missing_in_clients)),
            "ghost": sorted(set(ghost))}


def append_rows(cases: str, res: dict) -> list[str]:
    """Дописать заготовки в конец таблиц. Только append, существующее не трогаем."""
    done = []
    if res["missing_in_index"]:
        path = os.path.join(cases, "_index.md")
        with open(path, "a", encoding="utf-8") as f:
            for rel in res["missing_in_index"]:
                client, case = rel.split("/", 1)
                f.write(f"| {client} | ЗАПОЛНИТЬ: предмет дела | {rel} | — | — | — | — | Активно |\n")
        done.append(f"_index.md: дописано {len(res['missing_in_index'])} строк-заготовок")
    if res["missing_in_clients"]:
        path = os.path.join(cases, "_clients.md")
        with open(path, "a", encoding="utf-8") as f:
            for client in res["missing_in_clients"]:
                f.write(f"| {client} | ЗАПОЛНИТЬ: ФИО | — | |\n")
        done.append(f"_clients.md: дописано {len(res['missing_in_clients'])} строк-заготовок")
    return done


def report(res: dict) -> int:
    total_cases = sum(len(v) for v in res["clients"].values())
    print(f"на диске: клиентов {len(res['clients'])}, дел {total_cases}")
    problems = 0
    for title, items, hint in [
        ("дел нет в _index.md", res["missing_in_index"], "строка на дело обязательна"),
        ("клиентов нет в _clients.md", res["missing_in_clients"], "маршрутизация /new-case сломается"),
        ("нет файла _client.md", res["no_profile"], "профиль доверителя обязателен"),
        ("строк без папки на диске", res["ghost"], "реестр показывает несуществующее дело"),
        ("чужие папки внутри cases/", res.get("alien", []),
         "код и зависимости в папке доверителей — вынести из cases/"),
        ("папки-двойники", res.get("twins", []),
         "дело доверителя рядом с пустышкой; какая каноническая — решает владелец"),
    ]:
        if items:
            problems += len(items)
            print(f"\n⚠ {title} ({len(items)}) — {hint}:")
            for x in items[:30]:
                print(f"   • {x}")
            if len(items) > 30:
                print(f"   … и еще {len(items) - 30}")
    if not problems:
        print("\nреестры сходятся с диском ✓")
    else:
        print(f"\nитого расхождений: {problems}")
    return 1 if problems else 0


def selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()
    cases = os.path.join(tmp, "cases")
    for rel in ["ivanov/dolg-2026/.agent/context", "petrov/razvod-2026/00_intake",
                "ivan/spor-2026/.agent/context",
                # Двойник по одной правке: petrow — опечатка при заведении дела,
                # материалов нет. Так выглядела пара папок из прецедента 03.08.2026.
                "petrow/razvod-2026/00_intake",
                "_templates/x", "_logs/y", "node_modules/pako", "ivanov/node_modules"]:
        os.makedirs(os.path.join(cases, rel))
    open(os.path.join(cases, "ivanov", "_client.md"), "w").write("# профиль")
    open(os.path.join(cases, "_index.md"), "w", encoding="utf-8").write(
        "| Клиент | Дело | Папка |\n|---|---|---|\n"
        "| Иванов | Долг | ivanov/dolg-2026 | \n"
        "| Призрак | Нет такого | sidorov/net-2020 | \n")
    open(os.path.join(cases, "_clients.md"), "w", encoding="utf-8").write(
        "| Папка | ФИО |\n|---|---|\n| ivanov | Иванов |\n"
        "\n## Компании → Владельцы\n\n"
        # Дело компании живет в папке владельца: имя папки — в четвертой ячейке.
        "| Компания | Тип | ИНН | Папка (владелец) |\n|---|---|---|---|\n"
        "| ООО «Пример» | ООО | 1 | petrov |\n"
        "\n## Связи между клиентами\n\n"
        # Упоминание клиента в графе связей регистрацией не считается.
        # `ivan` стоит здесь ПЕРВОЙ ячейкой и больше нигде: строка про связь не
        # регистрирует клиента ни в какой колонке, включая первую.
        "| Клиент А | Клиент Б | Связь |\n|---|---|---|\n| ivanov | petrow | семья |\n"
        "| ivan | ivanov | семья |\n"
        # Как в живом файле: последним идет раздел правил, а не таблица. Именно
        # поэтому дописанные `--fix` строки читаются как таблица папок.
        "\n## Правила маршрутизации\n\n1. Поиск по ФИО.\n")
    open(os.path.join(cases, "ivan", "_client.md"), "w").write("# профиль")
    # У petrov материалы есть, у двойника petrow — ноль. Ровно так выглядела
    # реальная пара папок на диске 03.08.2026: 1191 файл против нуля.
    open(os.path.join(cases, "petrov", "razvod-2026", "_case.md"),
         "w", encoding="utf-8").write("# дело")

    res = check(cases)
    checks = [
        ("служебные папки не считаются делами",
         set(res["clients"]) == {"ivanov", "petrov", "ivan", "petrow"}),
        ("пропущенное дело найдено",
         res["missing_in_index"] == ["ivan/spor-2026", "petrov/razvod-2026",
                                     "petrow/razvod-2026"]),
        # Коллизия префикса: папка ivan НЕ покрыта строкой про ivanov.
        ("пропущенный клиент найден",
         res["missing_in_clients"] == ["ivan", "petrow"]),
        ("префикс чужой строки не засчитывается", "ivan" in res["missing_in_clients"]),
        # Компании: папка владельца стоит не первой ячейкой, а колонкой «Папка».
        ("папка из колонки владельца засчитана", "petrov" not in res["missing_in_clients"]),
        ("упоминание в связях регистрацией не считается",
         "petrow" in res["missing_in_clients"]),
        ("заголовок таблицы папкой не считается", "Папка" not in registry_keys(
            rows_of(os.path.join(cases, "_clients.md")))),
        # Двойники. Каждая папка по отдельности законна, расхождения реестра с
        # диском нет — ловится только сравнением имен между собой.
        ("пустой двойник по одной правке найден",
         any("petrow" in t and "petrov" in t for t in res["twins"])),
        ("пустой двойник назван первым и объявлен опечаткой",
         any(t.startswith("petrow (файлов 0)") and "опечатка" in t for t in res["twins"])),
        ("полная папка названа с числом файлов",
         any("petrov (файлов 1)" in t for t in res["twins"])),
        ("несвязанные имена двойниками не считаются",
         not any("ivanov" in t and "petrov" in t for t in res["twins"])),
        ("одна правка опознается", edit_distance_le1("primerov-ab", "primerow-ab")),
        ("две правки двойником не считаются", not edit_distance_le1("ivanov", "petrov")),
        ("вставка знака опознается", edit_distance_le1("obraztsova", "obraztsovaa")),
        ("клиент без профиля найден", res["no_profile"] == ["petrov", "petrow"]),
        ("строка-призрак найдена", res["ghost"] == ["sidorov/net-2020"]),
        ("чужие папки отделены от дел",
         set(res["alien"]) == {"node_modules", "ivanov/node_modules"}),
    ]
    # Однофамильцы. Своя песочница: пара «familiya» / «familiya-ab» — это две
    # разные женщины, а не опечатка, и владелец записал это в профиль. Прибор
    # обязан прочитать ответ с диска, иначе он спрашивает одно и то же вечно.
    tw = os.path.join(tmp, "twins")
    for rel in ("sestrova/delo-2026", "sestrova-ab/delo-2026",
                "odnofam/delo-2026", "odnofam-cd/delo-2026",
                "pustysh-xy/delo-2026", "pustysh"):
        os.makedirs(os.path.join(tw, rel))
    for c in ("sestrova", "sestrova-ab", "odnofam", "odnofam-cd", "pustysh-xy"):
        open(os.path.join(tw, c, "delo-2026", "_case.md"), "w", encoding="utf-8").write("# дело")
    open(os.path.join(tw, "sestrova-ab", "_client.md"), "w", encoding="utf-8").write(
        "> Не путать с клиентом `sestrova` — это разные лица.\n")
    # Пустышка с такой же пометкой: разводить нечего, находка обязана остаться.
    open(os.path.join(tw, "pustysh-xy", "_client.md"), "w", encoding="utf-8").write(
        "> Не путать с клиентом `pustysh` — это разные лица.\n")
    # Пометка называет ЧУЖУЮ папку с тем же началом — пару она не закрывает.
    open(os.path.join(tw, "odnofam-cd", "_client.md"), "w", encoding="utf-8").write(
        "> Не путать с клиентом `odnofam-cdef` — это разные лица.\n")
    # Простая перекрестная ссылка на соседа — не ответ на вопрос о тождестве.
    # Родственники и оппоненты ссылаются друг на друга сплошь и рядом.
    open(os.path.join(tw, "odnofam", "_client.md"), "w", encoding="utf-8").write(
        "> Смежное дело: см. `odnofam-cd`.\n")
    twins = near_twins({c: [] for c in sorted(os.listdir(tw))}, tw)
    checks += [
        ("разведенные владельцем однофамильцы не всплывают",
         not any("sestrova" in t for t in twins)),
        ("пометка в профиле опознана", disambiguated(tw, "sestrova", "sestrova-ab")),
        ("однофамильцы без пометки остаются вопросом",
         any("odnofam" in t and "odnofam-cd" in t for t in twins)),
        ("пометка про чужую папку пару не закрывает",
         not disambiguated(tw, "odnofam", "odnofam-cd")),
        ("пустой двойник всплывает даже с пометкой",
         any(t.startswith("pustysh (файлов 0)") for t in twins)),
        ("нет профиля — нет и пометки", not disambiguated(tw, "sestrova", "odnofam")),
    ]

    # ── Проверка клиента и конфликта интересов ────────────────────────────────
    lk = os.path.join(tmp, "lookup")
    os.makedirs(lk)
    for client, case in (("ivanov-ii", "razdel-2026"), ("petrova-as", "alimenty-2026"),
                         ("sidorov-vv", "dolg-2026")):
        d = os.path.join(lk, client, case)
        os.makedirs(os.path.join(d, "00_intake"))
        open(os.path.join(lk, client, "_client.md"), "w", encoding="utf-8").write("# профиль")
        card = "# карточка\n## Стороны\n"
        if client == "sidorov-vv":
            card += "- **Оппонент (роль):** Иванов Иван Иванович, ИНН 1650000000\n"
        elif client == "petrova-as":
            card += "- **Оппонент (роль):** —, ИНН —\n"
        open(os.path.join(d, "_case.md"), "w", encoding="utf-8").write(card)
    open(os.path.join(lk, "_clients.md"), "w", encoding="utf-8").write(
        "## Связи между клиентами\n"
        "| Клиент А | Клиент Б | Тип связи | Примечание |\n"
        "|---|---|---|---|\n"
        "| ivanov-ii | petrova-as | супруги | — |\n")

    opp_found, opp_blank = opponents(lk)
    who_nom = lookup(lk, "Иванов И.И.")
    who_dat = lookup(lk, "Иванову Ивану Ивановичу")
    who_lat = lookup(lk, "ivanov-ii")
    who_none = lookup(lk, "Несуществующий Н.Н.")
    checks += [
        ("--who находит клиента по фамилии", "ivanov-ii" in who_nom["client"]),
        ("падеж не ломает поиск (дательный)", "ivanov-ii" in who_dat["client"]),
        ("латинский слаг тоже находится", "ivanov-ii" in who_lat["client"]),
        ("посторонняя фамилия не находится",
         not who_none["client"] and not who_none["opponent"]),
        ("конфликт: мы вели ПРОТИВ этого лица",
         any(k.startswith("sidorov-vv/") for k in who_nom["opponent"])),
        ("падеж не ломает и проверку конфликта", bool(who_dat["opponent"])),
        ("связь между клиентами поднимается", bool(who_nom["links"])),
        ("незаполненный оппонент считается дырой, а не «конфликта нет»",
         "petrova-as/alimenty-2026" in opp_blank),
        ("заполненный оппонент в дыры не попал",
         "sidorov-vv/dolg-2026" in opp_found and "sidorov-vv/dolg-2026" not in opp_blank),
        ("вердикт про непокрытые дела печатается",
         any(v.startswith("ДАННЫХ НЕТ") for v in verdicts(who_nom))),
        ("молчаливого «совпадений нет» не бывает: вердикт всегда есть",
         any(v.startswith("СОВПАДЕНИЙ НЕТ") for v in verdicts(who_none))),
        ("разные короткие фамилии не сливаются",
         not same_person(_surname_of("Петров"), "petrova-as") or
         same_person(_surname_of("Петрова"), "petrova-as")),
        ("двух правок мало, чтобы слить разных людей",
         not same_person(_surname_of("Сидоров"), "ivanov-ii")),
        ("граф реестра ничего не пишет на диск",
         sorted(os.listdir(lk)) == sorted(["_clients.md", "ivanov-ii",
                                           "petrova-as", "sidorov-vv"])),
    ]

    append_rows(cases, res)
    after = check(cases)
    checks.append(("после --fix пропусков нет",
                   not after["missing_in_index"] and not after["missing_in_clients"]))
    checks.append(("--fix ничего не затер",
                   "Иванов" in rows_of(os.path.join(cases, "_index.md"))))
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0



# ── Проверка клиента и конфликта интересов (эфемерный граф реестра) ───────────
# Граф реестра НЕ материализуется в файл. Причина (свод 03.09.2026, развилка 2):
# связка «фамилия + мы ведем ее дело» уже живет ровно в трех местах — имена папок
# cases/*/, cases/_clients.md, cases/_relationships.md, все три вне git. Четвертый
# файл с той же связкой был бы и новой поверхностью утечки, и ЧЕТВЕРТЫМ держателем
# правды в приборе, который написан именно из-за расхождения трех книг. Обход диска
# стоит миллисекунды, поэтому граф строится в памяти на каждый вызов и умирает с
# процессом. Побочно: у эфемерного графа не бывает устаревания.
#
# Вывод идет ТОЛЬКО в терминал и никогда в файл: имя запрашивает сам владелец, а
# найденное дело он и так видит в `ls cases/`. Записывать это в лог нельзя.

_OPPONENT_RE = re.compile(r"^\s*[-*]\s*\*\*Оппонент[^:]*:\*\*\s*(.*)$", re.M)
_EMPTY_FIELD = {"", "—", "-", "–", "нет", "не установлен", "не установлено"}
# Кириллическая форма запроса против латинской папки: сравниваем стемы фамилии.
# Порог 2 правки покрывает падежи («иванов» → «иванову», «иванова»), 1 правка —
# короткие фамилии, где 2 правки сливают разных людей.
_LONG_NAME = 6


def _people_norm(text: str) -> str:
    """Фамилия к сравнимому виду: NFC, нижний регистр, «ё» к «е», без пунктуации."""
    import unicodedata
    text = unicodedata.normalize("NFC", text).strip().lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", "", text)


def _surname_of(query: str) -> str:
    """Первое слово запроса — фамилия. «Иванову И.И.» → «иванову»."""
    parts = [p for p in re.split(r"[\s,]+", query.strip()) if p]
    return _people_norm(parts[0]) if parts else ""


def _distance(a: str, b: str) -> int:
    """Расстояние Левенштейна. Свой, чтобы не тянуть зависимость ради 12 строк."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _slug_forms(slug: str) -> list[str]:
    """Формы имени папки для сличения: сама латиница и кириллическая фамилия.

    Транслитерация берется у pd_guard, чтобы правило «как латиница ложится на
    кириллицу» жило в ОДНОМ месте: два разных транслита разошлись бы молча.
    """
    fam = _people_norm(re.split(r"[-_]", slug)[0])
    forms = {fam}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pd_guard
        cyr = _people_norm(pd_guard._translit_to_cyrillic(re.split(r"[-_]", slug)[0]))
        if cyr:
            forms.add(cyr)
    except Exception:
        pass
    return [f for f in forms if f]


def same_person(query_surname: str, slug: str) -> bool:
    """Совпадает ли фамилия запроса с именем папки. Падежи и инициалы схлопываются.

    Хеш имени здесь невозможен принципиально: по хешам расстояние не считается,
    поэтому «Иванову И.И.» и «Иванов И.И.» дали бы разные хеши и молчаливое
    «совпадений нет» в самой чувствительной проверке системы (свод, развилка 3).
    """
    if not query_surname:
        return False
    for form in _slug_forms(slug):
        limit = 2 if min(len(form), len(query_surname)) >= _LONG_NAME else 1
        if _distance(query_surname, form) <= limit:
            return True
        # Падеж удлиняет основу: «иванов» ⊂ «иванову». Приставка засчитывается,
        # только если общая часть достаточно длинна, иначе «ив» ловит пол-реестра.
        common = min(len(form), len(query_surname))
        if common >= _LONG_NAME and (form.startswith(query_surname)
                                     or query_surname.startswith(form)):
            return True
    return False


def opponents(cases: str) -> tuple[dict[str, str], list[str]]:
    """{«клиент/дело»: строка оппонента}, и список дел, где поле не заполнено.

    Второе важнее первого: проверка конфликта, молчащая из-за незаполненных
    данных, выдает НЕЗНАНИЕ за отсутствие конфликта. Число таких дел печатается.
    """
    found: dict[str, str] = {}
    blank: list[str] = []
    clients, _, _ = scan_disk(cases)
    for client, case_dirs in clients.items():
        for case in case_dirs:
            card = os.path.join(cases, client, case, "_case.md")
            key = f"{client}/{case}"
            if not os.path.isfile(card):
                blank.append(key)
                continue
            m = _OPPONENT_RE.search(rows_of(card))
            value = (m.group(1) if m else "").strip()
            # «—, ИНН —» — это шаблон, а не оппонент.
            bare = _people_norm(re.split(r",", value)[0])
            if not m or not bare or value.strip(" ,—-–") == "" or bare in _EMPTY_FIELD:
                blank.append(key)
            else:
                found[key] = value
    return found, blank


def related(cases: str) -> list[tuple[str, str, str]]:
    """Связи между клиентами из _clients.md и _relationships.md: (А, Б, тип)."""
    out = []
    for name in ("_clients.md", "_relationships.md"):
        path = os.path.join(cases, name)
        if not os.path.isfile(path):
            continue
        for line in rows_of(path).splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or set(cells[0]) <= set("-: "):
                continue
            a, b, kind = cells[0], cells[1], cells[2]
            if not a or not b or a.lower().startswith("клиент а"):
                continue
            out.append((a, b, kind))
    return out


def lookup(cases: str, query: str) -> dict:
    """Эфемерный граф реестра: один вопрос — один разбор диска, ничего не пишем."""
    surname = _surname_of(query)
    clients, _, _ = scan_disk(cases)
    opp, blank = opponents(cases)
    as_client = {c: v for c, v in clients.items() if same_person(surname, c)}
    as_opponent = {k: v for k, v in opp.items() if same_person(surname, _surname_of(v))}
    links = [(a, b, k) for a, b, k in related(cases)
             if same_person(surname, _surname_of(a)) or same_person(surname, _surname_of(b))]
    return {"query": query, "surname": surname, "client": as_client,
            "opponent": as_opponent, "links": links,
            "blank": blank, "cases_total": sum(len(v) for v in clients.values())}


def verdicts(res: dict) -> list[str]:
    """Четыре вердикта, ни одного молчаливого."""
    out = []
    if res["client"]:
        for c, cs in sorted(res["client"].items()):
            out.append(f"КЛИЕНТ: папка {c}, дел {len(cs)}" + (f" ({', '.join(cs)})" if cs else ""))
    if res["opponent"]:
        for key, who in sorted(res["opponent"].items()):
            out.append(f"КОНФЛИКТ: мы вели ПРОТИВ этого лица — дело {key}")
    if res["links"]:
        for a, b, kind in res["links"]:
            out.append(f"СВЯЗЬ: {a} ↔ {b} ({kind}) — проверить сторону в этих делах")
    if not out:
        out.append("СОВПАДЕНИЙ НЕТ")
    if res["blank"]:
        out.append(f"ДАННЫХ НЕТ: у {len(res['blank'])} дел из {res['cases_total']} "
                   f"поле оппонента не заполнено — проверка конфликта их НЕ покрывает")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Сверка реестров дел с диском")
    ap.add_argument("--cases", default="cases", help="папка cases/ (по умолчанию ./cases)")
    ap.add_argument("--fix", action="store_true", help="дописать недостающие строки заготовками")
    ap.add_argument("--who", metavar="ИМЯ", help="есть ли уже такой клиент")
    ap.add_argument("--conflict", metavar="ИМЯ",
                    help="конфликт интересов: не вели ли мы против этого лица")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.who or a.conflict:
        if not os.path.isdir(a.cases):
            print(f"нет папки {a.cases}", file=sys.stderr)
            return 2
        res = lookup(a.cases, a.who or a.conflict)
        for line in verdicts(res):
            print(line)
        # Код 1 только на конфликте: вызывающий скрипт обязан споткнуться там, где
        # мы вели против этого лица. «Клиент найден» — это норма, а не отказ.
        return 1 if (a.conflict and (res["opponent"] or res["links"])) else 0
    if not os.path.isdir(a.cases):
        print(f"нет папки {a.cases}", file=sys.stderr)
        return 2
    res = check(a.cases)
    rc = report(res)
    if a.fix:
        for line in append_rows(a.cases, res):
            print(f"\n✍ {line}")
        print("Заготовки помечены «ЗАПОЛНИТЬ» — дописать предмет и реквизиты руками либо агентом.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
