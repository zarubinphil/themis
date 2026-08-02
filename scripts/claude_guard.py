#!/usr/bin/env python3
"""PreToolUse guard Фемиды: детерминированное исполнение железных правил.

Блокирует (exit 2, причина в stderr — видна модели):
1. Read бинарных документов (.docx/.pdf/.xlsx/.pptx/.doc/.xls) —
   только через scripts/markdown_extract.py (LOCAL-FIRST, кеш, requisites.json).
2. Write/Edit внутрь 00_intake/ — исходники клиента неприкосновенны.
3. Bash rm/rmdir по 00_intake/ или _baselines/ — защита первички и базы «ДО».

Правила-инварианты продублированы текстом в .claude/CLAUDE.md;
здесь — их жесткое исполнение (advisory-текст модель может пропустить, хук — нет).
"""
import json
import re
import sys


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


def _has_marker(path, pattern: str) -> bool:
    import os
    try:
        with open(path, encoding="utf-8") as f:
            return bool(re.search(pattern, f.read()))
    except OSError:
        return False


# Практика считается закрытой ДВУМЯ путями, и они не равны по силе:
#   «## СОВЕТ ЗАВЕРШЕН»      — FULL: охотники + /askacouncil
#   «## FAST-СИНТЕЗ ФЕМИДЫ»  — FAST: синтез Фемидой без совета
# До 02.08.2026 у FAST не было своего маркера: скилл разрешал писать practice.md
# без маркера, а хук за это давал exit 2 — агент шёл искать обход и находил его
# (дела doveritel-8, doveritel-9, doveritel-2 попали в 03_drafts мимо конвейера).
# Запрет без легального пути производит обходы, а не дисциплину.
PRACTICE_MARKER = r"## (СОВЕТ ЗАВЕРШ|FAST-СИНТЕЗ ФЕМИДЫ)"


def _workflow_gate(p: str) -> None:
    """Порядок шагов протокола — детерминированно.

    Запись артефакта шага N блокируется, пока нет маркера шага N-1 на диске:
      practice.md   ← требует «## КАРТА ГОТОВА ✓» в knowledge-map.md
      positions.md  ← требует маркер практики (СОВЕТ ЗАВЕРШЕН либо FAST-СИНТЕЗ)
      03_drafts/*   ← требует оба маркера (кроме _working/ и _baselines/)
    """
    norm = p.replace("\\", "/")
    parts = norm.split("/")
    if "cases" not in parts:
        return
    i = parts.index("cases")
    # структура cases/{клиент}/{дело}/... ; служебные папки (_templates, _logs) — мимо
    if len(parts) < i + 4 or parts[i + 1].startswith("_"):
        return
    case_root = "/".join(parts[: i + 3])
    km = case_root + "/01_context/knowledge-map.md"
    pr = case_root + "/01_context/practice.md"
    tail = "/".join(parts[i + 3:])

    if tail == "01_context/practice.md" and not _has_marker(km, r"## КАРТА ГОТОВА ✓"):
        block(
            "БЛОК ПРОТОКОЛА: practice.md пишется только после Шага 1 — "
            "в knowledge-map.md нет маркера «## КАРТА ГОТОВА ✓». Запустить case-mapper. "
            "Статус: python3 scripts/themis_status.py " + case_root
        )
    if tail == "01_context/positions.md" and not _has_marker(pr, PRACTICE_MARKER):
        block(
            "БЛОК ПРОТОКОЛА: positions.md пишется только после Шага 2 — "
            "в practice.md нет ни «## СОВЕТ ЗАВЕРШЕН», ни «## FAST-СИНТЕЗ ФЕМИДЫ». "
            "Запустить охоту/совет либо поставить честный FAST-маркер. "
            "Статус: python3 scripts/themis_status.py " + case_root
        )
    if (tail.startswith("03_drafts/")
            and "/_working/" not in norm and "/_baselines/" not in norm):
        if not _has_marker(km, r"## КАРТА ГОТОВА ✓") or not _has_marker(pr, PRACTICE_MARKER):
            block(
                "БЛОК ПРОТОКОЛА: черновик в 03_drafts/ пишется только после Шагов 1-2 — "
                "нет маркера карты и/или практики. Судебные документы вне конвейера запрещены. "
                "Статус: python3 scripts/themis_status.py " + case_root
            )


def main() -> None:
    try:
        d = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # нет валидного входа — не мешать

    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}

    if tool == "Read":
        p = ti.get("file_path", "")
        if re.search(r"\.(docx|xlsx|pptx|pdf|doc|xls)$", p, re.I):
            block(
                "БЛОК (LOCAL-FIRST): бинарные документы читать только через "
                "python3 scripts/markdown_extract.py FILE --json-meta "
                "(роутер выдаст кеш-путь, срезы и requisites.json). "
                "Read напрямую для .docx/.pdf/.xlsx/.pptx запрещен."
            )

    if tool in ("Write", "Edit", "NotebookEdit"):
        p = ti.get("file_path", "") or ti.get("notebook_path", "")
        if "/00_intake/" in p:
            block(
                "БЛОК: 00_intake/ неприкосновенен — исходники клиента "
                "не редактировать и не перезаписывать (железное правило)."
            )
        _workflow_gate(p)

    if tool == "Bash":
        cmd = ti.get("command", "")
        # rm только в командной позиции (начало строки / после ; & | $( `) —
        # иначе ложные срабатывания на прозу со словом «rm» в heredoc
        rm_cmd = re.search(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(?:rm|rmdir)\s", cmd, re.M)
        if rm_cmd and re.search(r"00_intake|_baselines", cmd):
            block(
                "БЛОК: удаление в 00_intake/ или _baselines/ запрещено "
                "(железное правило). Действительно нужно — только пользователь вручную."
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
