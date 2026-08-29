#!/usr/bin/env python3
"""model_policy.py — модель шага выводится из уровня дела, а не из пина в frontmatter.

Зачем. `doc-drafter` запинен на Opus, потому что на L2/L3 составление документа —
самая ответственная работа конвейера. Но на типовом документе (MICRO/L1) тот же пин
означает пятикратную цену за ту же страницу текста: Opus $15/$75 против Sonnet $3/$15
за миллион токенов. Пин не отменяется сам собой — его отменяет решение, принятое
на шаге брифа, и это решение обязано быть машинным, а не «по памяти».

    --level MICRO|L1|L2|L3 --step ШАГ   печатает алиас модели (haiku|sonnet|opus)
    --brief ФАЙЛ                        сверяет таблицу ПЛАН брифа с политикой
    --selftest                          проверка без сети

Алиасы, не версии: `haiku`/`sonnet`/`opus` всегда разрешаются в самую продвинутую
модель линейки, апгрейд подхватывается без правки файлов.

Источник политики — раздел «Модели под шаги» в .claude/CLAUDE.md. Здесь он записан
исполняемо: текст модель может пропустить, код возврата — нет.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

LEVELS = ("MICRO", "L1", "L2", "L3")

# шаг → (модель по уровню | «-» там, где шаг на этом уровне не запускается вовсе)
POLICY = {
    # Составление и проверка: на типовом документе судит Sonnet, на споре — Opus.
    "draft":         {"MICRO": "sonnet", "L1": "sonnet", "L2": "opus", "L3": "opus"},
    "review":        {"MICRO": "sonnet", "L1": "sonnet", "L2": "opus", "L3": "opus"},
    # Охота: сбор перспектив, разнообразие углов важнее силы. На MICRO запрещена
    # триажем — тема уже покрыта practice_index.md.
    "hunt":          {"MICRO": "-", "L1": "sonnet", "L2": "sonnet", "L3": "sonnet"},
    # Скептик-координатор (Карабчевский, practice-hunter-skeptic) — не рядовой
    # охотник: его листья на Sonnet, а САМ он ведет враждебный синтез на Opus
    # (frontmatter агента запинен на opus, CLAUDE.md относит скептика-координатора
    # к Opus). Общий шаг «hunt»=sonnet объявлял правдивый бриф перерасходом —
    # две копии одной правды (пин и политика) расходились. Пин и политика обязаны
    # совпадать: у скептика свой шаг, а не общий охотничий.
    "hunt-skeptic":  {"MICRO": "-", "L1": "opus", "L2": "opus", "L3": "opus"},
    # Совет: роли спорят (Sonnet), председатель сводит и решает (Opus).
    "council-role":  {"MICRO": "-", "L1": "-", "L2": "sonnet", "L3": "sonnet"},
    "council-chair": {"MICRO": "-", "L1": "-", "L2": "opus", "L3": "opus"},
    # Механика: извлечение и классификация — дешевой моделью.
    "read-text":     {"MICRO": "haiku", "L1": "haiku", "L2": "haiku", "L3": "haiku"},
    "classify":      {"MICRO": "haiku", "L1": "haiku", "L2": "haiku", "L3": "haiku"},
    # Скан-читатели: у них фолбэк на облачный vision, дешевле не ставить.
    "read-scan":     {"MICRO": "sonnet", "L1": "sonnet", "L2": "sonnet", "L3": "sonnet"},
}

# Исполнитель из брифа → шаг политики. Кого здесь нет, того политика не судит,
# но модель назвать он обязан все равно (пустая клетка = решение не принято).
AGENT_STEP = {
    # Персоны конвейера: в брифе и в чате исполнителя зовут по имени роли,
    # и политика, знающая только машинное имя, молча пропускает такую строку.
    "сперанский": "draft",
    "кони": "review",
    "спасович": "hunt",
    "плевако": "hunt",
    "карабчевский": "hunt-skeptic",
    "урусов": "council-chair",
    "покровский": "read-text",
    "гольмстен": "read-scan",
    "буринский": "read-scan",
    "doc-drafter": "draft",
    "doc-reviewer": "review",
    "practice-hunter-classic": "hunt",
    "practice-hunter-skeptic": "hunt-skeptic",
    "practice-hunter-tactical": "hunt",
    "askacouncil": "council-role",
    "position-council": "council-role",
    "docx-reader": "read-text",
    "pdf-reader": "read-scan",
    "image-reader": "read-scan",
}


def _step_of(executor: str) -> str:
    """Шаг по исполнителю: точное имя, «Персона (агент)» и просто персона.
    Совпадение по вхождению, чтобы форма записи не решала, судить строку или нет."""
    low = executor.lower()
    for key, step in AGENT_STEP.items():
        if key == low or key in low:
            return step
    return ""


def model_for(level: str, step: str) -> str:
    """Алиас модели либо «-», если шаг на этом уровне не запускается."""
    return POLICY[step][level]


def cmd_pair(level: str, step: str) -> int:
    if level not in LEVELS:
        print(f"ERROR: уровень «{level}» неизвестен, ожидались {', '.join(LEVELS)}", file=sys.stderr)
        return 2
    if step not in POLICY:
        print(f"ERROR: шаг «{step}» неизвестен, ожидались {', '.join(sorted(POLICY))}", file=sys.stderr)
        return 2
    m = model_for(level, step)
    if m == "-":
        print(f"шаг «{step}» на уровне {level} запрещен триажем — не запускать", file=sys.stderr)
        return 1
    print(m)
    return 0


_LEVEL_RE = re.compile(r"Уровень\s*:\s*(MICRO|L1|L2|L3)", re.I)


def check_brief(path: Path) -> int:
    """Сверка плана брифа с политикой. Fail-closed: непонятно — код 1, не 0."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: бриф не прочитан: {e}", file=sys.stderr)
        return 1
    m = _LEVEL_RE.search(text)
    if not m:
        print("ERROR: в брифе не назван уровень (строка КЛАССИФИКАЦИЯ, «Уровень: L2»). "
              "Без уровня модель шага не выводится — бриф не принят.", file=sys.stderr)
        return 1
    level = m.group(1).upper()

    rows, bad = 0, []
    canonical_header = ["Шаг", "Исполнитель", "Модель", "Прогноз"]
    header = canonical_header[:]
    executor_idx, model_idx = 1, 2
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[0].lower().startswith("шаг"):
            header = cells
            normalized = {c.strip().lower(): i for i, c in enumerate(cells)}
            if "исполнитель" not in normalized or "модель" not in normalized:
                bad.append(("шапка таблицы должна содержать «Исполнитель» и «Модель»",
                            cells))
                continue
            executor_idx = normalized["исполнитель"]
            model_idx = normalized["модель"]
            continue
        if len(cells) <= max(executor_idx, model_idx):
            continue
        rows += 1
        executor, model = cells[executor_idx], cells[model_idx].lower()
        if not model:
            bad.append((f"{executor}: колонка «Модель» пуста — решение о модели не принято",
                        cells))
            continue
        step = _step_of(executor)
        if not step:
            continue
        want = model_for(level, step)
        if want == "-":
            bad.append((f"{executor}: шаг «{step}» на уровне {level} запрещен триажем, "
                        f"а в плане стоит", cells))
        elif model != want:
            note = " (перерасход: Opus дороже Sonnet впятеро)" if model == "opus" else ""
            bad.append((f"{executor}: в плане «{model}», политика на {level} — «{want}»{note}",
                        cells))
    if not rows:
        print("ERROR: в брифе нет строк таблицы ПЛАН — сверять нечего, бриф не принят.",
              file=sys.stderr)
        return 1
    if bad:
        print(f"расхождений с политикой моделей ({level}): {len(bad)}", file=sys.stderr)
        print("  ожидаемая шапка таблицы: | " + " | ".join(canonical_header) + " |",
              file=sys.stderr)
        for msg, cells in bad:
            parsed = "; ".join(
                f"{header[i] if i < len(header) else f'колонка{i + 1}'}={cell or '<пусто>'}"
                for i, cell in enumerate(cells)
            )
            print("  · " + msg, file=sys.stderr)
            print("    разобранная строка: " + parsed, file=sys.stderr)
        return 1
    print(f"план брифа сходится с политикой моделей ({level}), строк: {rows}")
    return 0


def selftest() -> int:
    assert model_for("L1", "draft") == "sonnet"
    assert model_for("L3", "draft") == "opus"
    assert model_for("MICRO", "hunt") == "-"
    # Скептик-координатор — Opus и по политике, и по пину: конфликта нет.
    assert model_for("L3", "hunt-skeptic") == "opus", "скептик на L3 не Opus — разошлись с пином"
    assert model_for("MICRO", "hunt-skeptic") == "-", "охота-скептик на MICRO не запрещена триажем"
    assert _step_of("practice-hunter-skeptic") == "hunt-skeptic"
    assert _step_of("practice-hunter-classic") == "hunt", "рядовой охотник ушел в шаг скептика"
    for step, by_level in POLICY.items():
        assert set(by_level) == set(LEVELS), f"{step}: политика не покрывает все уровни"
        for lvl, m in by_level.items():
            assert m in ("haiku", "sonnet", "opus", "-"), f"{step}/{lvl}: странная модель {m}"
    for agent, step in AGENT_STEP.items():
        assert step in POLICY, f"{agent} указывает на неизвестный шаг {step}"

    ok = """КЛАССИФИКАЦИЯ  Уровень: L1 · Трек: FAST
| Шаг | Исполнитель | Модель | Прогноз |
|---|---|---|---|
| 4 | doc-drafter | sonnet | 40k |
"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.md"
        p.write_text(ok, encoding="utf-8")
        assert check_brief(p) == 0, "верный бриф отвергнут"
        p.write_text(ok.replace("sonnet", "opus"), encoding="utf-8")
        assert check_brief(p) == 1, "Opus на L1 пропущен"
        p.write_text(ok.replace("Уровень: L1 · ", ""), encoding="utf-8")
        assert check_brief(p) == 1, "бриф без уровня принят"
        p.write_text(ok.replace("| sonnet ", "|  "), encoding="utf-8")
        assert check_brief(p) == 1, "пустая модель принята"
        p.write_text(ok.replace("| 4 | doc-drafter | sonnet | 40k |",
                                "| 2 | practice-hunter-classic | sonnet | 60k |")
                       .replace("Уровень: L1", "Уровень: MICRO"), encoding="utf-8")
        assert check_brief(p) == 1, "охота на MICRO пропущена"
        p.write_text(ok.replace("doc-drafter", "Сперанский").replace("sonnet", "opus"),
                     encoding="utf-8")
        assert check_brief(p) == 1, "исполнитель-персона не узнан"
        p.write_text("""КЛАССИФИКАЦИЯ  Уровень: L1 · Трек: FAST
| Шаг | Исполнитель | Прогноз | Модель |
|---|---|---|---|
| 4 | doc-drafter | sonnet | opus |
""", encoding="utf-8")
        assert check_brief(p) == 1, "Opus в переставленной колонке Модель пропущен"
    print("selftest пройден: политика полна, бриф судится fail-closed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Модель шага по уровню дела.")
    ap.add_argument("--level", help="MICRO|L1|L2|L3")
    ap.add_argument("--step", help="|".join(sorted(POLICY)))
    ap.add_argument("--brief", help="сверить таблицу ПЛАН брифа с политикой")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.brief:
        return check_brief(Path(a.brief))
    if a.level and a.step:
        return cmd_pair(a.level.upper(), a.step)
    ap.error("нужны --level и --step, либо --brief, либо --selftest")


if __name__ == "__main__":
    sys.exit(main())
