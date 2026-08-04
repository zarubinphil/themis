#!/usr/bin/env python3
"""Машина состояний протокола Фемиды — детерминированный статус дела.

Использование:
    python3 scripts/themis_status.py cases/{клиент}/{дело}
    python3 scripts/themis_status.py cases/{клиент}/{дело} --brief
    python3 scripts/themis_status.py --selftest

Читает маркеры с ДИСКА (не из памяти модели) и печатает: статус каждого шага
и СЛЕДУЮЩИЙ ШАГ. Фемида обязана работать по этому выводу — это единственный
источник правды о состоянии протокола.

`--brief` добавляет сводку старта сессии и заменяет собой ритуал из шести чтений
(лог, индекс, `_case.md`, профиль, событие, карта). Смысл не в удобстве, а в
деньгах: прочитанный файл остаётся в контексте до конца сессии и переоплачивается
КАЖДЫМ следующим обращением к инструменту. Индекс дел — 16,9 КБ, карта знаний —
десятки килобайт; вместе ритуал заносил в контекст порядка 30 000 знаков, из
которых для решения нужны полтора десятка строк. Их скрипт и печатает — бесплатно
по токенам, потому что считает python, а не модель.
"""
import argparse
import datetime
import hashlib
import os
import re
import sys
from pathlib import Path

# Кеш роутера извлечения: если файл там есть, он уже распознан и
# перераспознавать его запрещено (конституция, раздел LOCAL-FIRST).
EXTRACT_CACHE = Path(os.environ.get(
    "THEMIS_EXTRACT_CACHE", Path.home() / ".cache" / "legal_extract"))
SCAN_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".bmp"}
TEXT_EXT = {".docx", ".xlsx", ".pptx", ".rtf", ".txt", ".md", ".html", ".csv"}
FLAGS = ("[ОБНОВИТЬ КЛИЕНТА]", "[ОБНОВИТЬ ИНДЕКС]")


def has_marker(f: Path, pattern: str) -> bool:
    try:
        return bool(re.search(pattern, f.read_text(encoding="utf-8")))
    except OSError:
        return False


def age_days(f: Path) -> int:
    try:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        return (datetime.datetime.now() - mtime).days
    except OSError:
        return 10**6


def check_frontmatter() -> list[str]:
    """Сломанный YAML во frontmatter = агент молча не попадает в реестр.

    Прецедент 02.08.2026: у doc-drafter поле description начиналось с двойной
    кавычки без обрамления одинарными — конвейер встал на шаге 4 после трех
    завершенных шагов и полутора миллионов токенов работы. Проверка стоит
    три строки и выполняется перед каждым шагом.
    """
    try:
        import yaml
    except ImportError:
        # Молчаливый return [] отключал проверку сломанного frontmatter — ту самую,
        # что конституция называет главной причиной остановки конвейера. Отсутствие
        # библиотеки обязано быть видно, а не выглядеть как «всё чисто».
        return ["pyyaml не установлен — проверка frontmatter агентов НЕ выполнена. "
                "Установить pyyaml либо считать реестр агентов непроверенным"]
    root = Path(__file__).resolve().parent.parent / ".claude"
    bad = []
    for f in sorted(root.glob("agents/*.md")) + sorted(root.glob("skills/**/SKILL.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            continue
        try:
            data = yaml.safe_load(m.group(1))
            if not isinstance(data, dict) or "name" not in data:
                bad.append(f"{f.name}: во frontmatter нет поля name")
        except yaml.YAMLError as e:
            bad.append(f"{f.name}: YAML сломан ({str(e).splitlines()[0][:60]})")
    return bad


def field(text: str, name: str) -> str:
    """Значение поля `- **Имя:** значение` из _case.md. Пусто — прочерк."""
    m = re.search(rf"(?m)^\s*[-*]\s*\*\*{re.escape(name)}:\*\*\s*(.+?)\s*$", text)
    v = m.group(1).strip() if m else ""
    return "" if v in ("—", "-", "") else v


def extracted(files: list[Path]) -> int:
    """Сколько материалов уже лежит в кеше роутера — их не перераспознавать."""
    n = 0
    for f in files:
        try:
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
        if (EXTRACT_CACHE / f"{sha}.md").exists() or (EXTRACT_CACHE / sha).is_dir():
            n += 1
    return n


def brief(case: Path, level: str) -> None:
    """Сводка старта сессии: то, ради чего конституция велела читать шесть файлов."""
    case_txt = read(case / "_case.md")
    client_dir = case.parent
    client_txt = read(client_dir / "_client.md")

    head = " · ".join(x for x in (
        f"стадия: {field(case_txt, 'Стадия')}" if field(case_txt, "Стадия") else "",
        f"суд: {field(case_txt, 'Суд')}" if field(case_txt, "Суд") else "",
        f"дело № {field(case_txt, 'Номер дела')}" if field(case_txt, "Номер дела") else "",
        f"судья: {field(case_txt, 'Судья')}" if field(case_txt, "Судья") else "",
    ) if x) or "реквизиты в _case.md не заполнены"
    print(f"# Сводка — {client_dir.name}/{case.name} (уровень {level})")
    print(f"  {head}")

    hearing = field(case_txt, "Следующее заседание")
    events = sorted((p for p in (case / "02_hearings").iterdir() if p.is_dir()),
                    reverse=True) if (case / "02_hearings").is_dir() else []
    print(f"  заседание: {hearing or 'не назначено'}"
          f" · последнее событие: {events[0].name if events else 'нет'}")

    fio = field(client_txt, "ФИО") or "профиль пуст"
    print(f"  доверитель: {fio}"
          f"{'' if (client_dir / '_client.md').exists() else ' ⚠ файла _client.md нет'}")

    intake = case / "00_intake"
    files = [f for f in intake.rglob("*") if f.is_file() and not f.name.startswith((".", "~$"))] \
        if intake.is_dir() else []
    scans = [f for f in files if f.suffix.lower() in SCAN_EXT]
    done = extracted(files)
    print(f"  материалы: {len(files)} шт (сканов {len(scans)}), уже извлечено {done} — "
          f"{'перераспознавать нельзя' if done else 'кеш пуст'}")

    # Флаги живут в файлах дела и в последнем логе сессий: необработанный флаг
    # означает, что реестр или профиль разошлись с делом.
    flagged = []
    for f in list(case.rglob("*.md")) + sorted(
            (case.parents[1] / "_logs").glob("session_*.md"), reverse=True)[:1]:
        t = read(f)
        if any(flag in t for flag in FLAGS):
            flagged.append(f.name)
    if flagged:
        print(f"  ⚠ необработанные флаги в: {', '.join(sorted(set(flagged))[:4])}")

    # Трек считается по счётному: объём и природа материалов. Правовой вопрос
    # машине не виден — про него говорится прямо, а не умалчивается.
    if len(files) <= 3 and not scans:
        hint = "MICRO по объёму"
    elif len(files) <= 6 and (not scans or done >= len(scans)):
        hint = "FAST по объёму"
    else:
        hint = "FULL по объёму"
    print(f"  трек: {hint}; новизну правового вопроса оценивает Фемида по practice_index\n")


def read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("case", nargs="?")
    ap.add_argument("--brief", action="store_true",
                    help="сводка старта сессии вместо шести чтений")
    ap.add_argument("--selftest", action="store_true")
    a, _ = ap.parse_known_args()
    if a.selftest:
        return selftest()
    if not a.case:
        print("usage: themis_status.py cases/{клиент}/{дело} [--brief]", file=sys.stderr)
        return 1
    sys.argv = [sys.argv[0], a.case]

    broken = check_frontmatter()
    if broken:
        print("⛔ СЛОМАН FRONTMATTER — эти агенты и скиллы НЕ попадут в реестр:")
        for b in broken:
            print(f"   {b}")
        print("   Чинить до работы: значение с кавычками либо двоеточием "
              "обернуть в одинарные кавычки.\n")
    case = Path(sys.argv[1]).resolve()
    if not case.is_dir():
        print(f"СТОП: {case} не существует. Сначала /new-case.", file=sys.stderr)
        return 1

    ctx = case / "01_context"
    km, pr, pos = ctx / "knowledge-map.md", ctx / "practice.md", ctx / "positions.md"
    case_md = case / "_case.md"

    s1 = has_marker(km, r"## КАРТА ГОТОВА ✓")
    # Практика закрывается двумя путями разной силы: FULL — «## СОВЕТ ЗАВЕРШЕН»,
    # FAST — «## FAST-СИНТЕЗ ФЕМИДЫ». Раньше FAST маркера не имел, поэтому FAST и
    # FULL на диске были неотличимы, а агент шёл в обход хука. Тот же список — в
    # claude_guard.PRACTICE_MARKER; расходиться им нельзя.
    s2_full = has_marker(pr, r"## СОВЕТ ЗАВЕРШ")
    s2_fast = has_marker(pr, r"## FAST-СИНТЕЗ ФЕМИДЫ")
    s2 = s2_full or s2_fast
    # Порог — от даты попадания практики В НАШУ БАЗУ (mtime practice.md), а не
    # от даты вынесения актов. Решение владельца 02.08.2026: судебная практика
    # так быстро не меняется, 30 дней было необоснованно жёстко. Год.
    PRACTICE_TTL_DAYS = 365
    pr_fresh = s2 and age_days(pr) <= PRACTICE_TTL_DAYS
    s3 = has_marker(pos, r"СОГЛАСОВАНО СОВЕТОМ")
    s3_skip = has_marker(case_md, r"position-council пропущен")

    level = "?"
    try:
        m = re.search(r"\bL[123]\b", case_md.read_text(encoding="utf-8"))
        if m:
            level = m.group(0)
    except OSError:
        pass
    s3_not_needed = level == "L1"

    if a.brief:
        brief(case, level)

    drafts = sorted((case / "03_drafts").glob("*.md")) if (case / "03_drafts").is_dir() else []
    drafts = [d for d in drafts if "_working" not in d.parts and "_baselines" not in d.parts]
    # Вердикт приёмки лежал только по одному захардкоженному пути, а Кони пишет
    # его куда придётся: на диске «ГОТОВ К ПОДАЧЕ» встречается в 14 файлах, из них
    # по каноническому пути — 2. Из-за этого машина печатала «Шаг 5 ✗» примерно в
    # 95% дел и приучала оператора себя игнорировать. Ищем везде, где он бывает.
    candidates = [case / "03_drafts" / "_working" / "review_log.md"]
    if (case / "03_drafts").is_dir():
        candidates += sorted((case / "03_drafts").rglob("*.md"))
    candidates += [case / "_case.md", ctx / "review_log.md"]
    # Подстрока «ГОТОВ К ПОДАЧЕ» входит в отрицательный вердикт «НЕ ГОТОВ К ПОДАЧЕ»
    # и в «документ пока НЕ готов к подаче» — машина принимала отказ Кони за приёмку
    # и пускала протокол на шаг вперёд. Отрицание отсекаем явно.
    approved_in = next((f for f in candidates
                        if has_marker(f, r"(?<!НЕ )(?<!не )ГОТОВ К ПОДАЧЕ")), None)
    approved = approved_in is not None

    def mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    print(f"# Статус протокола — {case.name} (уровень: {level})")
    print(f"Шаг 1 Карта:     {mark(s1)}  knowledge-map.md {'с маркером' if s1 else '— нет маркера КАРТА ГОТОВА'}")
    fresh_note = "" if not s2 else (f" (свежая, ≤{PRACTICE_TTL_DAYS} дн.)" if pr_fresh else f" (в базе {age_days(pr)} дн., порог {PRACTICE_TTL_DAYS} — проверить актуальность)")
    track = " [FULL, совет]" if s2_full else (" [FAST, синтез Фемиды]" if s2_fast else "")
    print(f"Шаг 2 Практика:  {mark(s2)}  practice.md "
          f"{'с маркером' + track + fresh_note if s2 else '— нет маркера (нужен СОВЕТ ЗАВЕРШЕН либо FAST-СИНТЕЗ ФЕМИДЫ)'}")
    if s3_not_needed:
        print("Шаг 3 Позиция:   —  L1: не требуется")
    elif s3_skip:
        print("Шаг 3 Позиция:   ✓  пропуск зафиксирован в _case.md")
    else:
        print(f"Шаг 3 Позиция:   {mark(s3)}  positions.md {'СОГЛАСОВАНО СОВЕТОМ' if s3 else '— нет маркера'}")
    print(f"Шаг 4 Черновики: {mark(bool(drafts))}  {len(drafts)} файл(ов) в 03_drafts")
    print(f"Шаг 5 Кони:      {mark(approved)}  "
          f"{'ГОТОВ К ПОДАЧЕ — ' + approved_in.name if approved else 'вердикта ГОТОВ К ПОДАЧЕ нет'}")

    # Состояния «артефакт шага N есть, а шага N-1 нет» в модели раньше не было:
    # для дела с готовым документом и пустым конвейером скрипт бодро печатал
    # «СЛЕДУЮЩИЙ ШАГ: Шаг 1», не сказав ни слова о документе вне протокола.
    if drafts and not (s1 and s2):
        missing = ", ".join(x for x, ok in (("карта", s1), ("практика", s2)) if not ok)
        print(f"\n⚠ НАРУШЕН ПОРЯДОК: в 03_drafts есть {len(drafts)} документ(ов), "
              f"но не пройдено: {missing}. Документ создан вне конвейера — "
              f"проверять реквизиты вручную, к подаче не готов.")

    if not s1:
        nxt = "Шаг 1 — case-mapper (карта дела)"
    elif not s2:
        nxt = "Шаг 2 — охота за практикой (FAST: 1 охотник; FULL: 3 + /askacouncil)"
    elif not pr_fresh:
        nxt = (f"Шаг 2 — практика в базе {age_days(pr)} дн. (порог {PRACTICE_TTL_DAYS}): подтвердить актуальность "
               f"или обновить охоту")
    elif not (s3 or s3_skip or s3_not_needed):
        nxt = "Шаг 3 — /position-council (или зафиксировать пропуск в _case.md)"
    elif not drafts:
        nxt = "Шаг 4 — /draft (doc-drafter)"
    elif not approved:
        nxt = "Шаг 5 — doc-reviewer (Кони) до вердикта ГОТОВ К ПОДАЧЕ"
    else:
        nxt = "/finalize — пакет в 02_hearings (guard правок доверителя)"
    print(f"\nСЛЕДУЮЩИЙ ШАГ: {nxt}")
    return 0


def selftest() -> int:
    """Без сети и без диска проекта. Фикстуры враждебные: каждая метит в ветку,
    которая уже ломалась или может тихо соврать."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    case = tmp / "cases" / "klient" / "delo-2026"
    (case / "01_context").mkdir(parents=True)
    (case / "00_intake").mkdir()
    (case / "02_hearings" / "2026-06-29_zasedanie").mkdir(parents=True)
    (case / "02_hearings" / "2026-05-12_beseda").mkdir(parents=True)
    (tmp / "cases" / "_logs").mkdir(parents=True)
    (case / "_case.md").write_text(
        "# дело\n- **Стадия:** Первая инстанция\n- **Уровень:** L2\n"
        "- **Суд:** Советский районный суд\n- **Номер дела:** 2-4590/2026\n"
        "- **Судья:** —\n- **Следующее заседание:** 29.06.2026 в 13:00\n", encoding="utf-8")
    (case.parent / "_client.md").write_text(
        "# профиль\n- **ФИО:** Тестова Тестина Тестовна\n", encoding="utf-8")
    (case / "01_context" / "knowledge-map.md").write_text("## КАРТА ГОТОВА ✓", encoding="utf-8")
    # Содержимое разное: у одинаковых файлов один sha, и кеш засчитал бы все три.
    for n, body in (("a.pdf", b"pdf"), ("b.jpg", b"jpeg"), ("c.docx", b"docx")):
        (case / "00_intake" / n).write_bytes(body)
    (case / "01_context" / "zametka.md").write_text("надо [ОБНОВИТЬ КЛИЕНТА]", encoding="utf-8")

    txt = read(case / "_case.md")
    cache = Path(tempfile.mkdtemp())
    global EXTRACT_CACHE
    EXTRACT_CACHE = cache
    files = sorted((case / "00_intake").iterdir())
    # Один из трёх материалов уже в кеше роутера.
    sha = hashlib.sha256((case / "00_intake" / "a.pdf").read_bytes()).hexdigest()
    (cache / f"{sha}.md").write_text("уже извлечено", encoding="utf-8")

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        brief(case, "L2")
    out = buf.getvalue()

    checks = [
        ("поле читается из _case.md", field(txt, "Номер дела") == "2-4590/2026"),
        # Прочерк — это ОТСУТСТВИЕ значения, а не значение «—»: иначе сводка
        # уверенно печатает «судья: —» и выглядит заполненной.
        ("прочерк считается пустым", field(txt, "Судья") == ""),
        ("несуществующее поле не выдумывается", field(txt, "Кадастровый номер") == ""),
        # Поле берётся целиком: обрезка по первому пробелу теряла зал и время.
        ("значение берётся до конца строки",
         field(txt, "Следующее заседание") == "29.06.2026 в 13:00"),
        ("кеш роутера опознан", extracted(files) == 1),
        ("не в кеше — не засчитан", extracted(files) != len(files)),
        ("сводка называет суд", "Советский районный суд" in out),
        ("сводка называет доверителя", "Тестова" in out),
        # События сортируются по имени: даты ISO, последнее — старшее.
        ("последнее событие — самое свежее", "2026-06-29_zasedanie" in out
         and "2026-05-12_beseda" not in out),
        ("материалы сосчитаны", "материалы: 3 шт (сканов 2), уже извлечено 1" in out),
        ("необработанный флаг виден", "флаги" in out and "zametka.md" in out),
        # Два скана, извлечён один — по объёму это ещё не FAST.
        ("трек не занижается при нераспознанных сканах", "FULL по объёму" in out),
        ("машина не молчит о правовом вопросе", "practice_index" in out),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
