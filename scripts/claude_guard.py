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

    if tool == "Bash":
        cmd = ti.get("command", "")
        if re.search(r"\b(rm|rmdir)\b", cmd) and re.search(r"00_intake|_baselines", cmd):
            block(
                "БЛОК: удаление в 00_intake/ или _baselines/ запрещено "
                "(железное правило). Действительно нужно — только пользователь вручную."
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
