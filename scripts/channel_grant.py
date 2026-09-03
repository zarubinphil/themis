#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""channel_grant.py — ЕДИНСТВЕННЫЙ законный путь правки knowledge/allowed-services.md.

Зачем. 01.09.2026 белый список был дописан ПОСРЕДИ прогона той же стороной,
которую он ограничивает: два новых канала появились абзацем по ходу работы, и
правка так и висела в дереве незакоммиченной. Разрешение, выданное себе на ходу,
перестает быть разрешением владельца.

Теперь: прямая правка файла блокируется сторожем (`scripts/claude_guard.py`), а
санкция владельца проходит через этот прибор и становится МАШИННОЙ записью —
дата, канал, режим, причина, срок. Строка добавляется, старая не переписывается:
история решений владельца не редактируется задним числом.

Использование:
    python3 scripts/channel_grant.py --list
    python3 scripts/channel_grant.py --host cian.ru --reason "аналоги по адресу" \\
        --owner-approved
    python3 scripts/channel_grant.py --host cian.ru --deny --reason "больше не нужен" \\
        --owner-approved
    python3 scripts/channel_grant.py --selftest

`--owner-approved` — подтверждение, что санкция владельца ПОЛУЧЕНА (голосом, в
переписке, в брифе). Без флага прибор ничего не пишет: он оформляет решение
владельца, а не заменяет его.
"""
import argparse
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "knowledge", "allowed-services.md")

SECTION = "## Машинные записи каналов (channel_grant.py)"
HEADER = (f"{SECTION}\n\n"
          "> Записи ставит только `python3 scripts/channel_grant.py` по санкции владельца.\n"
          "> Строки не редактируются и не удаляются: отмена оформляется новой строкой `запрещен`.\n\n"
          "| Дата | Канал | Режим | Причина | Санкция |\n|---|---|---|---|---|\n")

# Точек правды по sudact.ru ДВЕ — решение владельца 02.09.2026 («точек две,
# прибор знает про обе»). Пути к хосту два, и запрет источника накрывает один:
#   • ПОИСК (путь /{раздел}/doc_ajax/, закрыт robots.txt для User-agent: *)
#     живет константой SUDACT_SEARCH_ALLOWED в scripts/practice_search.py.
#     Запись о поиске сюда НЕ заводится: прибор не дублирует константу.
#   • ОТКРЫТИЕ АКТА ПО ИЗВЕСТНОМУ URL (/{раздел}/doc/<id>/, под запрет robots
#     не подпадало никогда) оформляется ЗДЕСЬ с --scope doc-url — и обязано
#     нести перекрестную ссылку на константу: две точки правды, не видящие
#     друг друга, расходятся молча, а это главный риск выбранного варианта.
# Ключ отказа — «хост + путь доступа», а не хост: отбивается ровно поиск.
TRUTH_ELSEWHERE = {
    ("sudact.ru", "поиск"): {
        "why": "решение по ПОИСКУ на sudact.ru — константа SUDACT_SEARCH_ALLOWED "
               "в scripts/practice_search.py (выключатель THEMIZ_SUDACT_SEARCH). "
               "Здесь его не дублировать",
        "cross": "ПОИСК по этому хосту живет константой SUDACT_SEARCH_ALLOWED "
                 "в scripts/practice_search.py и этой записью НЕ управляется",
    },
}


def today() -> str:
    return datetime.date.today().strftime("%d.%m.%Y")


def _read(path=REGISTRY) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def rows(text: str) -> list:
    """Машинные записи как список кортежей (дата, канал, режим, причина, санкция)."""
    body = text.split(SECTION, 1)[1] if SECTION in text else ""
    out = []
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) == 5 and cells[0] not in ("Дата", "---") and not cells[0].startswith("--"):
            out.append(tuple(cells))
    return out


def state(text: str, host: str) -> str:
    """Действующий режим канала по последней машинной записи: allow / deny / ''."""
    last = [r for r in rows(text) if r[1].lower() == host.lower()]
    return ("deny" if last[-1][2].startswith("запрещ") else "allow") if last else ""


def grant(host: str, reason: str, allow: bool, sanction: str, scope: str = "",
          path=REGISTRY) -> str:
    """Добавить машинную запись. Возвращает добавленную строку."""
    host = host.strip().lower()
    scope = scope.strip()
    if not host or " " in host:
        raise SystemExit("канал задается доменом без пробелов, например cian.ru")
    if not reason.strip():
        raise SystemExit("причина обязательна: запись без причины через полгода нечитаема")
    for (known, protected), meta in TRUTH_ELSEWHERE.items():
        if host == known or host.endswith("." + known):
            if not scope or scope == protected:
                raise SystemExit(f"ОТКАЗ: {meta['why']}. Запись о ДРУГОМ пути доступа "
                                 f"к этому хосту заводится с --scope (например --scope doc-url).")
            # Путь доступа разрешен к записи — но запись обязана назвать вторую
            # точку правды, иначе две точки разъедутся молча.
            reason = f"{reason.strip()} [путь доступа: {scope}. {meta['cross']}]"

    text = _read(path)
    if SECTION not in text:
        text = text.rstrip("\n") + "\n\n---\n\n" + HEADER
    row = (f"| {today()} | {host} | {'разрешен' if allow else 'запрещен'} "
           f"| {reason.strip()} | {sanction} |\n")
    # Дописываем в конец таблицы раздела — он последний в файле, история идет вниз.
    text = text.rstrip("\n") + "\n" + row
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return row


def selftest() -> int:
    """Без сети. Порог — санкция и режим: запись появляется только по санкции,
    отмена не стирает историю, а добавляет строку."""
    import tempfile
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "allowed-services.md")
        open(p, "w", encoding="utf-8").write("# Реестр\n\nтекст\n")

        grant("cian.ru", "аналоги по адресу", True, "владелец, устно", path=p)
        t = _read(p)
        checks.append(("запись появилась машинной строкой с датой",
                       today() in t and "cian.ru" in t and "разрешен" in t))
        checks.append(("канал числится разрешенным", state(t, "cian.ru") == "allow"))

        grant("cian.ru", "больше не нужен", False, "владелец, устно", path=p)
        t = _read(p)
        checks.append(("отмена — новая строка, история цела",
                       len([r for r in rows(t) if r[1] == "cian.ru"]) == 2))
        checks.append(("действует последняя запись", state(t, "cian.ru") == "deny"))
        checks.append(("незнакомый канал не разрешен", state(t, "avito.ru") == ""))

        try:
            grant("sudact.ru", "поиск", True, "владелец", path=p)
            checks.append(("запись о поиске sudact отбита с указанием на константу", False))
        except SystemExit as e:
            checks.append(("запись о поиске sudact отбита с указанием на константу",
                           "SUDACT_SEARCH_ALLOWED" in str(e)))
        try:
            grant("sudact.ru", "поиск практики", True, "владелец", scope="поиск", path=p)
            checks.append(("запись о поиске отбита и при явной области «поиск»", False))
        except SystemExit as e:
            checks.append(("запись о поиске отбита и при явной области «поиск»",
                           "SUDACT_SEARCH_ALLOWED" in str(e)))
        # Вторая точка правды (решение владельца 02.09.2026): путь по известному
        # URL заводится и несет перекрестную ссылку на константу поиска.
        row = grant("sudact.ru", "открытие акта по известному URL Линкеем", True,
                    "решение владельца 01.09.2026", scope="doc-url", path=p)
        t = _read(p)
        checks.append(("путь по известному URL заводится", state(t, "sudact.ru") == "allow"))
        checks.append(("запись о пути несет ссылку на константу поиска",
                       "SUDACT_SEARCH_ALLOWED" in row and "doc-url" in row))
        try:
            grant("example.com", "   ", True, "владелец", path=p)
            checks.append(("запись без причины отклонена", False))
        except SystemExit:
            checks.append(("запись без причины отклонена", True))
        checks.append(("хост канала попадает в текст реестра — сторож WebFetch его увидит",
                       re.search(r"\bcian\.ru\b", _read(p)) is not None))
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'✓' if ok else '✗'} {n}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Машинная правка белого списка сервисов")
    ap.add_argument("--host", help="домен канала, например cian.ru")
    ap.add_argument("--scope", default="",
                    help="путь доступа к хосту (например doc-url); без него запись "
                         "трактуется как весь хост, включая поиск")
    ap.add_argument("--reason", default="", help="зачем канал нужен (обязательно)")
    ap.add_argument("--deny", action="store_true", help="отменить разрешение")
    ap.add_argument("--owner-approved", action="store_true",
                    help="санкция владельца получена")
    ap.add_argument("--sanction", default="владелец, подтверждено в сессии",
                    help="как именно получена санкция")
    ap.add_argument("--list", action="store_true", help="показать машинные записи")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.list or not a.host:
        for r in rows(_read()):
            print(" | ".join(r))
        if not a.host:
            return 0
        return 0
    if not a.owner_approved:
        print("ОТКАЗ: санкции владельца нет. Прибор оформляет решение владельца, "
              "а не выдает разрешение сам. Спросить владельца, затем повторить "
              "с --owner-approved.", file=sys.stderr)
        return 2
    row = grant(a.host, a.reason, allow=not a.deny, sanction=a.sanction, scope=a.scope)
    print(row.strip())
    print(f"Записано в {os.path.relpath(REGISTRY, ROOT)}. Коммитить отдельным коммитом: "
          "решение владельца не смешивается с работой по делу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
