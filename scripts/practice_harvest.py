#!/usr/bin/env python3
"""practice_harvest.py — что уже найдено по делам, но не попало в базу практики.

Базу кормят единицы дел из семидесяти: практика, добытая охотниками, оседает в
`01_context/` дела и умирает вместе с ним, а следующая охота по той же теме идёт
заново. Скрипт вычитывает ссылки на судебные акты из всех дел regex-ом ($0, без
модели), вычитает то, что уже есть в `knowledge/practice_index.md`, и выдаёт
кандидатов с цитатой-контекстом. Модели остаётся правовая оценка, не поиск.

    practice_harvest.py                кандидаты в базу (по убыванию частоты)
    practice_harvest.py --audit        качество самой базы: записи без источника,
                                       дубли, категории без записей
    practice_harvest.py --md FILE      выгрузить кандидатов в markdown для archivist
    practice_harvest.py --selftest     проверка без сети

ПД доверителей не выводятся: только реквизиты актов и путь к делу.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter, defaultdict

# Реквизиты актов. Пишем строго — «суд рассмотрел» без номера базе не нужен.
PATTERNS = {
    "Пленум/Обзор ВС": re.compile(
        r"Пленума?\s+(?:ВС|Верховного\s+Суда)[^\n№]{0,40}от\s+(\d{2}\.\d{2}\.\d{4})\s*№\s*(\d+)"),
    "Определение ВС": re.compile(r"\b(\d{1,3}-(?:КГ|КАД|КАС|АД|ЭС|УД)\d{2}-\d+(?:-[А-Яа-я\d]+)?)\b"),
    # КС РФ — ТОЛЬКО с датой. Ключ без даты сплавлял в одну запись разные акты:
    # № 6-П соответствовал пяти датам, № 35-П — трём, № 9-П, 16-П и 18-П — двум
    # (замер 03.08.2026, 4 склейки на 21 ключ). Номера у КС повторяются каждый год,
    # поэтому «Постановление КС РФ № 35-П» — не реквизит, а омоним.
    "Постановление КС": re.compile(
        r"(?:Постановлени\w+\s+)?(?:КС|Конституционного\s+Суда)[^\n№]{0,40}"
        r"от\s+(\d{2}\.\d{2}\.\d{4})\s*№\s*(\d+(?:-\d+)?)-П\b"),
    "Арбитраж": re.compile(r"\b(А\d{2}-\d+/\d{4})\b"),
    "СОЮ": re.compile(r"\b(\d{1,2}-\d{2,6}/\d{4})\b"),
}

SOURCE_GLOBS = ("01_context/practice.md", "01_context/hunter_*.md",
                "01_context/positions.md", "01_context/_practice/*.md")


def act_key(kind: str, m: re.Match) -> str:
    if kind == "Пленум/Обзор ВС":
        return f"Постановление Пленума ВС РФ от {m.group(1)} № {m.group(2)}"
    if kind == "Постановление КС":
        return f"Постановление КС РФ от {m.group(1)} № {m.group(2)}-П"
    return m.group(1)


def context_of(text: str, span: tuple[int, int], width: int = 160) -> str:
    a = max(0, span[0] - width // 2)
    frag = " ".join(text[a:span[1] + width // 2].split())
    return frag


def harvest(cases_dir: str) -> dict[str, dict]:
    found: dict[str, dict] = defaultdict(lambda: {"count": 0, "cases": set(), "context": ""})
    for client in sorted(os.listdir(cases_dir)):
        cdir = os.path.join(cases_dir, client)
        if not os.path.isdir(cdir) or client.startswith((".", "_")):
            continue
        for case in sorted(os.listdir(cdir)):
            case_dir = os.path.join(cdir, case)
            if not os.path.isdir(case_dir) or case.startswith((".", "_")):
                continue
            # Номер САМОГО дела встречается в его же материалах на каждой странице —
            # и попадал в кандидаты как «практика». Собираем и исключаем.
            own = set()
            for probe in ("_case.md", "01_context/knowledge-map.md"):
                try:
                    head = open(os.path.join(case_dir, probe), encoding="utf-8",
                                errors="replace").read()
                except OSError:
                    continue
                own |= set(PATTERNS["СОЮ"].findall(head))
                own |= set(PATTERNS["Арбитраж"].findall(head))
            for pattern in SOURCE_GLOBS:
                for path in glob.glob(os.path.join(case_dir, pattern)):
                    try:
                        text = open(path, encoding="utf-8", errors="replace").read()
                    except OSError:
                        continue
                    for kind, rx in PATTERNS.items():
                        for m in rx.finditer(text):
                            key = act_key(kind, m)
                            if key in own:
                                continue
                            rec = found[key]
                            rec["count"] += 1
                            rec["cases"].add(f"{client}/{case}")
                            rec["kind"] = kind
                            if not rec["context"]:
                                rec["context"] = context_of(text, m.span())
    return found


def already_in_index(index_path: str) -> str:
    try:
        return open(index_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


# Номер дела первой инстанции — персональные данные, а не практика. По нему
# открывается карточка на портале суда: стороны, предмет, движение. Номер, который
# встретился РОВНО В ОДНОМ деле, почти всегда номер собственного производства
# доверителя, попавший в hunter-файл из материалов: замер 03.08.2026 дал 36 таких
# из 151 кандидата формата СОЮ, и все они шли в общую базу знаний.
# Прецедент практики цитируется другими делами — реальный прецедент встречается
# минимум дважды. Пороговое правило действует только для номеров производств
# (СОЮ и арбитраж); у Пленума, КС и определений ВС реквизит сам по себе публичен.
PERSONAL_KINDS = frozenset({"СОЮ", "Арбитраж"})
MIN_CASES_FOR_PERSONAL = 2


def personal_data_risk(kind: str, rec: dict) -> bool:
    """Кандидат — вероятный номер производства доверителя, а не прецедент."""
    return kind in PERSONAL_KINDS and len(rec.get("cases", ())) < MIN_CASES_FOR_PERSONAL


def candidates(cases_dir: str, index_path: str,
               allow_personal: bool = False) -> list[tuple[str, dict]]:
    idx = already_in_index(index_path)
    out = []
    for key, rec in harvest(cases_dir).items():
        if not allow_personal and personal_data_risk(rec.get("kind", ""), rec):
            continue
        probe = re.sub(r"^Постановление (Пленума ВС РФ|КС РФ) ", "", key)
        # Подстрочная проверка «поглощала» кандидатов: 81-КГ19-2 находился внутри
        # 81-КГ19-20, А65-1234/2024 — внутри А65-12345/2024, и акт молча терялся.
        # Границы токена: слева и справа не должно быть цифры, буквы, дефиса и слеша.
        if re.search(rf"(?<![\w/-]){re.escape(probe)}(?![\w/-])", idx):
            continue
        out.append((key, rec))
    out.sort(key=lambda kv: (-len(kv[1]["cases"]), -kv[1]["count"], kv[0]))
    return out


def audit_index(index_path: str) -> list[str]:
    """Качество базы: заявленное число записей, безымянные источники, дубли."""
    text = already_in_index(index_path)
    if not text:
        return [f"{index_path}: не читается"]
    problems = []
    entries = re.findall(r"(?m)^### (.+)$", text)
    declared = re.search(r"Записей:\s*(\d+)", text)
    if declared and int(declared.group(1)) != len(entries):
        problems.append(f"шапка обещает {declared.group(1)} записей, на диске {len(entries)}")

    blocks = re.split(r"(?m)^### ", text)[1:]
    no_source, no_quote, memory = [], [], []
    for b in blocks:
        title = b.splitlines()[0].strip()
        if not re.search(r"\*\*Источник:\*\*", b):
            no_source.append(title)
        elif re.search(r"\*\*Источник:\*\*[^\n]*(из памяти|по памяти|без источника)", b, re.I):
            memory.append(title)
        if not re.search(r"(?i)\*\*(Цитата|Позиция):\*\*", b):
            no_quote.append(title)
    for label, items in [("записей без поля «Источник»", no_source),
                         ("записей с источником «из памяти» (не верифицировано)", memory),
                         ("записей без цитаты/позиции", no_quote)]:
        if items:
            problems.append(f"{label}: {len(items)} — напр. {'; '.join(items[:3])}")

    dup = [t for t, n in Counter(entries).items() if n > 1]
    if dup:
        problems.append(f"дубли заголовков: {len(dup)} — напр. {'; '.join(dup[:3])}")

    urls = len(re.findall(r"https?://", text))
    problems.append(f"ссылок на первоисточник во всей базе: {urls} на {len(entries)} записей")
    for code in ("АПК", "КАС"):
        if not re.search(rf"(?m)^## .*{code}", text):
            problems.append(f"нет ни одной категории по {code} РФ — дела такого рода в системе есть")
    return problems


def sync_header(index_path: str) -> list[str]:
    """Счётчики шапки и оглавления — с диска. Шапка обещала 101 запись при 82 на диске,
    и каждый агент, читавший базу, исходил из чужого числа."""
    text = already_in_index(index_path)
    if not text:
        return [f"{index_path}: не читается"]
    entries = re.findall(r"(?m)^### (.+)$", text)
    changed = []

    new, n = re.subn(r"(Записей:\s*)\d+", lambda m: m.group(1) + str(len(entries)), text, count=1)
    if n and new != text:
        changed.append(f"шапка: записей → {len(entries)}")
        text = new

    # счётчики по категориям в таблице «Содержание»
    per: dict[str, int] = {}
    current = None
    for line in text.splitlines():
        h2 = re.match(r"^## (.+)$", line)
        if h2 and h2.group(1).strip() != "Содержание":
            current = h2.group(1).strip()
            per.setdefault(current, 0)
        elif line.startswith("### ") and current:
            per[current] += 1

    def fix_row(m: re.Match) -> str:
        name = m.group(1).strip()
        real = per.get(name)
        if real is None or str(real) == m.group(2):
            return m.group(0)
        changed.append(f"категория «{name}»: {m.group(2)} → {real}")
        return f"| [{name}]({m.group(3)}) | {real} |"

    text = re.sub(r"\| \[([^\]]+)\]\(([^)]+)\) \| (\d+) \|",
                  lambda m: (lambda name, link, cnt: (
                      m.group(0) if per.get(name) is None or per[name] == int(cnt)
                      else (changed.append(f"категория «{name}»: {cnt} → {per[name]}")
                            or f"| [{name}]({link}) | {per[name]} |")))(
                      m.group(1).strip(), m.group(2), m.group(3)),
                  text)

    if changed:
        open(index_path, "w", encoding="utf-8").write(text)
    return changed


def to_markdown(items: list[tuple[str, dict]]) -> str:
    lines = ["# Кандидаты в practice_index.md",
             "",
             f"Собрано механически из дел, {len(items)} актов не найдено в базе.",
             "Проверить применимость и внести через archivist. Реквизиты — сверить `verify_act.py`.",
             f"Номера производств, встреченные меньше чем в {MIN_CASES_FOR_PERSONAL} делах, "
             "в выгрузку не попадают: это номера дел доверителей, а не прецеденты.",
             "Пути дел и цитаты-контекст намеренно не выводятся: имя папки — фамилия "
             "доверителя. Нужен контекст — грепнуть реквизит по `cases/` локально.",
             ""]
    for key, rec in items:
        lines.append(f"## {key}")
        lines.append(f"- **Тип:** {rec.get('kind', '?')}")
        # Ни путей дел, ни сырого контекста: имя папки — это фамилия доверителя,
        # а контекст берётся из материалов дела. Обещание «без ПД» должно быть правдой.
        lines.append(f"- **Встречается:** {rec['count']} раз в {len(rec['cases'])} делах")
        lines.append("")
    return "\n".join(lines)


def selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()
    cases = os.path.join(tmp, "cases")
    ctx = os.path.join(cases, "ivanov", "dolg-2026", "01_context")
    os.makedirs(ctx)
    open(os.path.join(ctx, "practice.md"), "w", encoding="utf-8").write(
        "Постановление Пленума ВС РФ от 23.06.2015 № 25, п. 86. Также 81-КГ19-2, 81-КГ19-20 "
        "и дело А65-12345/2024. Ещё дело А65-99999/2024 упомянуто один раз. "
        "Постановление Конституционного Суда РФ от 21.12.2011 № 30-П. "
        "Суд рассмотрел дело и удовлетворил.")
    own_case = os.path.join(cases, "sidorov", "spor-2026")
    os.makedirs(os.path.join(own_case, "01_context"))
    open(os.path.join(own_case, "_case.md"), "w", encoding="utf-8").write(
        "Дело № 2-777/2026 в Вахитовском районном суде.")
    open(os.path.join(own_case, "01_context", "practice.md"), "w", encoding="utf-8").write(
        "По делу 2-777/2026 суд применил позицию 5-КГ24-9-К2.")

    ctx2 = os.path.join(cases, "petrov", "razdel-2026", "01_context")
    os.makedirs(ctx2)
    open(os.path.join(ctx2, "hunter_classic.md"), "w", encoding="utf-8").write(
        "Опять Постановление Пленума ВС РФ от 23.06.2015 № 25. "
        "И то же дело А65-12345/2024 — значит это прецедент, а не чужое производство. "
        "Постановление КС РФ от 25.06.2015 № 17-П и Постановление КС РФ от 08.12.2017 № 39-П.")

    index = os.path.join(tmp, "practice_index.md")
    open(index, "w", encoding="utf-8").write(
        "_Обновлено: 01.01.2026 | Записей: 5_\n## ГК РФ\n"
        "### Мнимые сделки\n- **Источник:** hunter\n- **Позиция:** текст 81-КГ19-20\n"
        "Пленума ВС РФ от 23.06.2015 № 25\n"
        "### Дубль\n- **Источник:** из памяти\n- **Позиция:** текст\n"
        "### Дубль\n- **Позиция:** текст\n")

    cands = dict(candidates(cases, index))
    aud = audit_index(index)
    checks = [
        ("уже внесённый Пленум не предлагается", "Постановление Пленума ВС РФ от 23.06.2015 № 25" not in cands),
        ("внесённое определение ВС не предлагается повторно", "81-КГ19-20" not in cands),
        ("арбитражное дело найдено", "А65-12345/2024" in cands),
        ("постановление КС найдено с датой",
         "Постановление КС РФ от 21.12.2011 № 30-П" in cands),
        # Ключ без даты сплавлял разные акты в одну запись: № 6-П — пять дат.
        ("два КС с разными датами не сливаются в один ключ",
         "Постановление КС РФ от 25.06.2015 № 17-П" in cands
         and "Постановление КС РФ от 08.12.2017 № 39-П" in cands),
        ("КС без даты в ключ не попадает",
         all(re.fullmatch(r"Постановление КС РФ № \d+-П", k) is None for k in cands)),
        # Персональные данные: номер производства, встреченный в одном деле,
        # почти всегда номер собственного дела доверителя.
        ("одиночный номер производства в общую базу не идёт",
         "А65-99999/2024" not in cands),
        ("номер, встреченный в двух делах, остаётся прецедентом",
         "А65-12345/2024" in cands),
        ("одиночный номер виден только с --allow-personal",
         "А65-99999/2024" in dict(candidates(cases, index, allow_personal=True))),
        ("одиночный номер не попадает в выгрузку",
         "А65-99999/2024" not in to_markdown(list(cands.items()))),
        ("«суд рассмотрел» без номера не попадает", all("рассмотрел" not in k for k in cands)),
        # Подстрочное поглощение: 81-КГ19-2 внесён, 81-КГ19-20 — другой акт и обязан
        # остаться кандидатом. Раньше он молча исчезал.
        # В базе лежит 81-КГ19-20; 81-КГ19-2 — ДРУГОЙ акт и обязан остаться кандидатом.
        # Подстрочная проверка находила его внутри внесённого и молча выбрасывала.
        ("короткий номер не поглощается длинным внесённым", "81-КГ19-2" in cands),
        ("номер собственного дела не идёт в кандидаты", "2-777/2026" not in cands),
        ("чужой акт из того же файла остаётся", "5-КГ24-9-К2" in cands),
        ("выгрузка не содержит путей дел",
         "ivanov" not in to_markdown(list(cands.items()))),
        ("расхождение счётчика записей поймано", any("обещает 5" in p for p in aud)),
        ("дубль заголовка пойман", any("дубли заголовков" in p for p in aud)),
        ("источник «из памяти» пойман", any("из памяти" in p for p in aud)),
        ("отсутствие АПК/КАС отмечено", any("АПК" in p for p in aud)),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Сбор практики из дел и аудит базы")
    ap.add_argument("--cases", default="cases")
    ap.add_argument("--index", default="knowledge/practice_index.md")
    ap.add_argument("--audit", action="store_true", help="проверить качество самой базы")
    ap.add_argument("--sync-header", action="store_true",
                    help="привести счётчики шапки и оглавления к тому, что на диске")
    ap.add_argument("--md", metavar="FILE", help="выгрузить кандидатов в markdown")
    ap.add_argument("--top", type=int, default=40, help="сколько кандидатов показать")
    ap.add_argument("--allow-personal", action="store_true",
                    help="не отсекать номера производств, встреченные в одном деле "
                         "(это персональные данные доверителей — только для разбора глазами)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if a.sync_header:
        for line in sync_header(a.index) or ["счётчики уже сходятся"]:
            print(f"  {line}")
        return 0

    if a.audit:
        problems = audit_index(a.index)
        for p in problems:
            print(f"⚠ {p}")
        return 1 if problems else 0

    items = candidates(a.cases, a.index, allow_personal=a.allow_personal)
    held = len(candidates(a.cases, a.index, allow_personal=True)) - len(items)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(to_markdown(items))
        print(f"кандидатов {len(items)} → {a.md}")
        return 1 if items else 0
    print(f"актов в делах, которых нет в базе: {len(items)}")
    if held:
        print(f"придержано как персональные данные: {held} номеров производств, "
              f"встреченных меньше чем в {MIN_CASES_FOR_PERSONAL} делах "
              "(показать: --allow-personal, в общую базу не вносить)")
    print()
    print(f"{'акт':<44}{'дел':>5}{'упом.':>7}  тип")
    for key, rec in items[:a.top]:
        print(f"{key[:43]:<44}{len(rec['cases']):>5}{rec['count']:>7}  {rec.get('kind', '')}")
    if len(items) > a.top:
        print(f"\n… и ещё {len(items) - a.top}. Полный список: --md FILE")
    # Находка — это работа, а не справка: код 0 при 402 несобранных актах читается
    # вызывающим скриптом как «всё в порядке».
    return 1 if items else 0


if __name__ == "__main__":
    sys.exit(main())
