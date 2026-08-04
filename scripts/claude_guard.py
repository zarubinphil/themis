#!/usr/bin/env python3
"""PreToolUse guard Фемиды: детерминированное исполнение железных правил.

Блокирует (exit 2, причина в stderr — видна модели):
1. Read бинарных документов (.docx/.pdf/.xlsx/.pptx/.doc/.xls) —
   только через scripts/markdown_extract.py (LOCAL-FIRST, кеш, requisites.json).
2. Write/Edit внутрь 00_intake/ — исходники клиента неприкосновенны.
3. Bash rm/rmdir, а равно cp/mv/tee/sed -i/редирект по 00_intake/ или _baselines/ —
   защита первички и базы «ДО»: затирание так же безвозвратно, как удаление.
4. Read текстового файла проекта свыше 48 КБ целиком — брать срезом или грепом.
5. Запись артефактов шагов вне порядка протокола (см. _workflow_gate).
Невалидный JSON на входе — тоже блок: сторож, который молча перестал сторожить, хуже
отсутствующего. Предупреждает (не блокирует) при работе вне корня проекта.
Проверка: python3 scripts/claude_guard.py --selftest

Правила-инварианты продублированы текстом в .claude/CLAUDE.md;
здесь — их жесткое исполнение (advisory-текст модель может пропустить, хук — нет).
"""
import json
import os
import re
import sys


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


# Маркер, названный в отрицании, — не маркер. Поиск вхождением по всему файлу
# засчитывал строку «в practice.md нет маркера «## FAST-СИНТЕЗ ФЕМИДЫ»» как
# пройденный шаг: хук пропускал запись, которую обязан блокировать. Та же дыра
# найдена и закрыта в scripts/themis_status.py (дело 04.08.2026). Проверка
# построчная — все маркеры конвейера однострочные.
_NEGATED_MARKER_RE = re.compile(r"\b(?:без|нет|не)\s+маркера", re.I)


def _has_marker(path, pattern: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    rx = re.compile(pattern)
    return any(rx.search(line) and not _NEGATED_MARKER_RE.search(line)
               for line in text.splitlines())


# Практика считается закрытой ДВУМЯ путями, и они не равны по силе:
#   «## СОВЕТ ЗАВЕРШЕН»      — FULL: охотники + /askacouncil
#   «## FAST-СИНТЕЗ ФЕМИДЫ»  — FAST: синтез Фемидой без совета
# До 02.08.2026 у FAST не было своего маркера: скилл разрешал писать practice.md
# без маркера, а хук за это давал exit 2 — агент шёл искать обход и находил его
# (три дела попали в 03_drafts мимо конвейера).
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


# Порог целикового чтения текстового файла. 48 КБ ≈ 12-15k токенов на один Read;
# конституция велит крупные корпуса (practice_index.md, логи) брать грепом и срезами.
BIG_READ_BYTES = 48 * 1024


def _sudact_allowed() -> bool:
    """Решение по sudact живёт в одном месте — practice_search.py. Хук его читает,
    а не дублирует: два гейта с собственными копиями решения неизбежно разъедутся."""
    env = os.environ.get("THEMIS_SUDACT_SEARCH")
    if env is not None:
        return env == "1"
    try:
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "practice_search.py"), encoding="utf-8").read()
    except OSError:
        return False           # источник решения не прочитан — не пускаем
    return bool(re.search(r"^SUDACT_SEARCH_ALLOWED\s*=\s*True", src, re.M))


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)  # пустой вход — проверять нечего
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # fail-closed: битый вход значит, что контракт хука разошёлся с харнессом.
        # Раньше здесь стоял exit 0 — сторож молча переставал сторожить, и узнать
        # об этом было неоткуда (так же незаметно умирал весь settings.json).
        block(
            "БЛОК (сторож Фемиды): PreToolUse-хук получил невалидный JSON и не может "
            "проверить железные правила. Молча пропускать нельзя. Починить "
            "scripts/claude_guard.py или временно снять хук из .claude/settings.json."
        )

    tool = d.get("tool_name", "")
    ti = d.get("tool_input")
    if not isinstance(ti, dict):
        ti = {}

    # Значения из tool_input приходят от харнесса и не обязаны быть строками.
    # Сторож, падающий на None или числе, перестаёт сторожить молча.
    def as_str(v) -> str:
        return v if isinstance(v, str) else ""

    if tool == "Read":
        p = as_str(ti.get("file_path"))
        if re.search(r"\.(docx|xlsx|pptx|pdf|doc|xls)$", p, re.I):
            block(
                "БЛОК (LOCAL-FIRST): бинарные документы читать только через "
                "python3 scripts/markdown_extract.py FILE --json-meta "
                "(роутер выдаст кеш-путь, срезы и requisites.json). "
                "Read напрямую для .docx/.pdf/.xlsx/.pptx запрещен."
            )
        # Гейт держим на файлах проекта: внешние материалы (справки, чужие репозитории)
        # аудитор обязан читать целиком, и запрещать ему это — не экономия, а слепота.
        if (re.search(r"\.(md|txt|jsonl|log|csv)$", p, re.I)
                and "/themis/" in p.replace("\\", "/")
                and not ti.get("offset") and not ti.get("limit")):
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            if size > BIG_READ_BYTES:
                block(
                    f"БЛОК (бюджет входа): {os.path.basename(p)} — {size // 1024} КБ, "
                    f"порог {BIG_READ_BYTES // 1024} КБ. Целиком читать запрещено: "
                    "взять срез (offset/limit), грепнуть нужное (Grep/rg) либо отдать "
                    "субагенту с точным заданием."
                )

    if tool in ("Write", "Edit", "NotebookEdit"):
        p = as_str(ti.get("file_path")) or as_str(ti.get("notebook_path"))
        if "/00_intake/" in p:
            block(
                "БЛОК: 00_intake/ неприкосновенен — исходники клиента "
                "не редактировать и не перезаписывать (железное правило)."
            )
        _workflow_gate(p)

    if tool == "Bash":
        cmd = as_str(ti.get("command"))
        protected = re.search(r"00_intake|_baselines", cmd)
        # rm только в командной позиции (начало строки / после ; & | $( `) —
        # иначе ложные срабатывания на прозу со словом «rm» в heredoc
        rm_cmd = re.search(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(?:rm|rmdir)\s", cmd, re.M)
        if rm_cmd and protected:
            block(
                "БЛОК: удаление в 00_intake/ или _baselines/ запрещено "
                "(железное правило). Действительно нужно — только пользователь вручную."
            )
        # Затирание не менее разрушительно, чем удаление: `> файл`, cp, mv, tee,
        # truncate, dd, sed -i переписывают первичку и базу «ДО» так же безвозвратно.
        # Каждая альтернатива — только в КОМАНДНОЙ позиции. Без этого «sed -i»
        # и «cp» внутри текста (сообщение коммита, heredoc, комментарий) блокировали
        # безобидную команду: сторож ловил собственное описание правил.
        write_cmd = re.search(
            r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?"
            r"(?:cp|mv|tee|truncate|dd|install|sed\s+(?:-\w+\s+)*-i)\b"
            r"|>\s*\S*(?:00_intake|_baselines)",
            cmd, re.M)
        if write_cmd and protected:
            block(
                "БЛОК: перезапись в 00_intake/ или _baselines/ запрещена — исходники "
                "клиента и база «ДО» для разбора правок неприкосновенны. Класть новое "
                "можно только новым именем через Write, менять существующее нельзя."
            )

        # Гейт по robots.txt источника живет в practice_search.py, но обойти его
        # можно голым curl. Закрываем и этот путь: запрет источника не должен
        # зависеть от того, каким инструментом к нему пошли.
        if re.search(r"doc_ajax", cmd) and not _sudact_allowed():
            block(
                "БЛОК: /doc_ajax/ запрещен robots.txt sudact.ru для всех роботов. "
                "Известный акт открывается по обычному URL: "
                "python3 scripts/practice_search.py --doc URL. Поиск включает владелец "
                "(THEMIS_SUDACT_SEARCH=1) с записью в knowledge/allowed-services.md."
            )

    # Работа вне корня проекта — прецедент 25.07.2026: сессия шла мимо cases/,
    # правила проекта не грузились, счёт 49,5 млн токенов. Предупреждаем, не блокируем.
    cwd = as_str(d.get("cwd"))
    if cwd and "themis" not in cwd:
        print(f"⚠ Фемида: cwd={cwd} вне корня проекта — правила проекта могут не действовать.",
              file=sys.stderr)

    sys.exit(0)


def selftest() -> int:
    """Проверка без сети: каждое правило на паре «должно блокировать / должно пускать»."""
    import subprocess
    import tempfile

    me = [sys.executable, __file__]
    tmp = tempfile.mkdtemp() + "/themis"  # гейт большого Read действует внутри проекта
    os.makedirs(tmp, exist_ok=True)
    big = tmp + "/big.md"
    with open(big, "w", encoding="utf-8") as f:
        f.write("x" * (BIG_READ_BYTES + 10))
    small = tmp + "/small.md"
    with open(small, "w", encoding="utf-8") as f:
        f.write("ok")

    def run(payload, raw=None):
        data = raw if raw is not None else json.dumps(payload, ensure_ascii=False)
        return subprocess.run(me, input=data, capture_output=True, text=True).returncode

    cases = [
        ("битый JSON блокируется", run(None, raw="{не json"), 2),
        ("пустой вход пропускается", run(None, raw="  "), 0),
        ("Read .docx блокируется",
         run({"tool_name": "Read", "tool_input": {"file_path": "/a/b.docx"}}), 2),
        ("Read .md целиком свыше порога блокируется",
         run({"tool_name": "Read", "tool_input": {"file_path": big}}), 2),
        ("Read .md срезом пропускается",
         run({"tool_name": "Read", "tool_input": {"file_path": big, "offset": 1, "limit": 50}}), 0),
        ("Read маленького .md пропускается",
         run({"tool_name": "Read", "tool_input": {"file_path": small}}), 0),
        ("Write в 00_intake блокируется",
         run({"tool_name": "Write", "tool_input": {"file_path": "/c/cases/x/y/00_intake/z.md"}}), 2),
        ("rm по _baselines блокируется",
         run({"tool_name": "Bash", "tool_input": {"command": "rm -rf x/_baselines"}}), 2),
        ("cp поверх 00_intake блокируется",
         run({"tool_name": "Bash", "tool_input": {"command": "cp a.pdf cases/k/d/00_intake/a.pdf"}}), 2),
        ("редирект в _baselines блокируется",
         run({"tool_name": "Bash", "tool_input": {"command": "echo hi > d/_baselines/f.docx"}}), 2),
        ("sed -i по 00_intake блокируется",
         run({"tool_name": "Bash", "tool_input": {"command": "sed -i '' s/a/b/ 00_intake/f.md"}}), 2),
        ("те же слова в прозе команду не блокируют",
         run({"tool_name": "Bash", "tool_input": {"command":
              "git commit -m 'гейт на cp/mv/tee/sed -i по 00_intake и _baselines'"}}), 0),
        ("обычный cp пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "cp a.md b.md"}}), 0),
        ("слово rm в прозе пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "echo 'norm 00_intake'"}}), 0),
        # Гейт следует решению владельца, а не собственной копии: включён поиск —
        # пропускаем, выключен — блокируем. Проверяем именно согласованность.
        ("гейт совпадает с решением в practice_search.py",
         run({"tool_name": "Bash",
              "tool_input": {"command": "curl https://sudact.ru/regular/doc_ajax/?q=1"}}),
         0 if _sudact_allowed() else 2),
    ]
    bad = [name for name, got, want in cases if got != want]
    for name, got, want in cases:
        print(f"  {'✓' if got == want else '✗'} {name}" + ("" if got == want else f" (ждали {want}, вышло {got})"))
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(cases) - len(bad)}/{len(cases)}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    main()
