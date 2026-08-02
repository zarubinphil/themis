#!/usr/bin/env python3
"""Машина состояний протокола Фемиды — детерминированный статус дела.

Использование: python3 scripts/themis_status.py cases/{клиент}/{дело}

Читает маркеры с ДИСКА (не из памяти модели) и печатает: статус каждого шага
и СЛЕДУЮЩИЙ ШАГ. Фемида обязана работать по этому выводу — это единственный
источник правды о состоянии протокола.
"""
import datetime
import re
import sys
from pathlib import Path


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
        return []
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


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: themis_status.py cases/{клиент}/{дело}", file=sys.stderr)
        return 1

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
    pr_fresh = s2 and age_days(pr) <= 30
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
    approved_in = next((f for f in candidates if has_marker(f, r"ГОТОВ К ПОДАЧЕ")), None)
    approved = approved_in is not None

    def mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    print(f"# Статус протокола — {case.name} (уровень: {level})")
    print(f"Шаг 1 Карта:     {mark(s1)}  knowledge-map.md {'с маркером' if s1 else '— нет маркера КАРТА ГОТОВА'}")
    fresh_note = "" if not s2 else (" (свежая, ≤30 дн.)" if pr_fresh else f" (устарела: {age_days(pr)} дн. — проверить актуальность)")
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
        nxt = (f"Шаг 2 — практике {age_days(pr)} дн. (порог 30): подтвердить актуальность "
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


if __name__ == "__main__":
    sys.exit(main())
