#!/usr/bin/env python3
"""context_guard.py — машинная часть правил экономии контекста. Зовется из claude_guard.

ЗАЧЕМ. Правило «не читать заранее, не читать дважды, разгружаться между фазами» три
редакции стояло в .claude/CLAUDE.md текстом — и три редакции не исполнялось: замер
02.09.2026 дал 200,55 $ из 360,17 $ (55,7 %) на ПОВТОРНУЮ доставку уже прочитанного,
контекст оркестратора вырос с 96 480 до 503 962 токенов без единого сброса.
Правило, которое не держит машина, — пожелание.

ТРИ ВОРОТ (PreToolUse, блок = exit 2 из claude_guard):
1. ПОТОЛОК КОНТЕКСТА. Контекст текущего запроса выше потолка → работать этой сессией
   нельзя: каждый следующий вызов оплачивает весь накопленный вес заново. Пропускаются
   только действия РАЗГРУЗКИ: запись/чтение handoff.md, запуск приборов замера, спавн
   субагента (у него контекст свой). Выключатель на крайний случай: THEMIZ_CTX_LIMIT=0.
2. ПОВТОРНОЕ ЧТЕНИЕ. Тот же файл тем же срезом, файл на диске не менялся → блок:
   он уже в контексте, второй раз платится зря. Другой срез (offset/limit) и файл,
   изменившийся после первого чтения, проходят — это не повтор.
3. ВЕС ИНСТРУКЦИЙ. Запись в CLAUDE.md / AGENTS.md меряется В БАЙТАХ, а не в строках:
   199 строк при 45 181 байте — это 95 792 токена стартового контекста на КАЖДОМ
   запросе сессии. Лимит строк остается, байтовый добавляется.

Состояние чтений — сайдкар в каталоге временных файлов, по одному на сессию;
переживать сессию ему незачем.

Проверка: python3 scripts/context_guard.py --selftest
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import sreda  # noqa: E402,F401  переходный период имен переменных

# Потолок контекста ОДНОГО запроса. 200 000 — порог, после которого фазу пора
# закрывать: замер 02.09.2026 показал 104 запроса из 189 с контекстом свыше 300 000.
CTX_LIMIT = int(os.environ.get("THEMIZ_CTX_LIMIT", "200000"))

# Потолок веса файла инструкций. 16 КБ ≈ 4 000 токенов на каждый запрос сессии.
INSTRUCTION_MAX_BYTES = 16 * 1024
INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md")

# Что разрешено, когда потолок пробит. Пропускается то, что контекст НЕ растит:
# запись (ею и делается выгрузка), спавн субагента (контекст у него свой), приборы
# замера и состояния, самопроверки. Блокируется то, что тащит новый вес: чтение,
# произвольный Bash, сеть.
UNLOAD_PATH = re.compile(r"handoff\.md$", re.I)
UNLOAD_CMD = re.compile(r"context_ledger|token_ledger|themiz_status|retro\.py|--selftest")
UNLOAD_TOOLS = ("Agent", "Write", "Edit", "NotebookEdit")

# Осознанный обход ОДНОГО вызова: префикс прямо в команде. Слово-пропуск в середине
# строки (первым таким был `git `) молча превращается в лазейку, через которую
# проходит вся работа; здесь обход виден и в команде, и в журнале сессии.
BREAK_GLASS = "THEMIZ_CTX_OK=1"

# Хвост транскрипта, которого хватает, чтобы найти последнюю реплику с usage.
TAIL_BYTES = 512 * 1024


def ctx_current(transcript: str) -> int:
    """Контекст последнего запроса сессии: input + cache-write + cache-read.

    0 — прочитать нечем (нет файла, нет usage): молчаливый ноль здесь безопаснее
    блока, потому что сторож не должен запирать сессию из-за отсутствия журнала.
    """
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        u = msg.get("usage") if isinstance(msg, dict) else None
        if isinstance(u, dict):
            return (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                    + u.get("cache_read_input_tokens", 0))
    return 0


def _state_path(session_id: str) -> str:
    d = os.path.join(tempfile.gettempdir(), "themiz-context")
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", session_id or "no-session")
    return os.path.join(d, f"{safe}.json")


def _state(session_id: str) -> dict:
    try:
        with open(_state_path(session_id), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(session_id: str, data: dict) -> None:
    try:
        with open(_state_path(session_id), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass  # сайдкар — удобство, а не источник правды


def read_key(ti: dict, base: str) -> tuple[str, str]:
    """Ключ чтения: (путь+срез, mtime файла). Разные срезы — разные чтения."""
    path = ti.get("file_path") if isinstance(ti.get("file_path"), str) else ""
    abspath = os.path.abspath(os.path.join(base, os.path.expanduser(path)))
    key = f"{abspath}|{ti.get('offset')}|{ti.get('limit')}"
    try:
        stamp = str(os.path.getmtime(abspath))
    except OSError:
        stamp = ""
    return key, stamp


def instruction_size(tool: str, ti: dict, base: str) -> tuple[str, int] | None:
    """(файл, его размер после записи) для CLAUDE.md/AGENTS.md, иначе None."""
    path = ti.get("file_path") if isinstance(ti.get("file_path"), str) else ""
    if not path or os.path.basename(path) not in INSTRUCTION_FILES:
        return None
    abspath = os.path.abspath(os.path.join(base, os.path.expanduser(path)))
    if tool == "Write":
        content = ti.get("content")
        return abspath, len((content if isinstance(content, str) else "").encode("utf-8"))
    if tool in ("Edit", "NotebookEdit"):
        try:
            size = os.path.getsize(abspath)
        except OSError:
            return None
        old = ti.get("old_string") or ti.get("new_source") or ""
        new = ti.get("new_string") or ""
        old_b = len(str(old).encode("utf-8"))
        new_b = len(str(new).encode("utf-8"))
        return abspath, size - old_b + new_b
    return None


def check(payload: dict) -> str | None:
    """Причина блока либо None. Побочный эффект — запись сайдкара чтений."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    base = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    session_id = payload.get("session_id") or ""

    # 3. Вес файла инструкций — в байтах.
    size_info = instruction_size(tool, ti, base)
    if size_info and size_info[1] > INSTRUCTION_MAX_BYTES:
        name, size = size_info
        return (f"БЛОК (вес инструкций): {os.path.basename(name)} после записи — "
                f"{size:,} байт при потолке {INSTRUCTION_MAX_BYTES:,}. ".replace(",", " ")
                + "Инструкция едет в КАЖДОМ запросе сессии: 45 181 байт = ~95 792 токена "
                  "стартового контекста на каждый вызов. Правило, выразимое хуком, "
                  "переносить в хук; невыразимое — в knowledge/claude-md-razbor.md "
                  "с причиной. Лимит строк при этом остается.")

    # 1. Потолок контекста сессии.
    limit = CTX_LIMIT
    transcript = payload.get("transcript_path")
    if limit > 0 and isinstance(transcript, str) and transcript:
        ctx = ctx_current(transcript)
        if ctx > limit:
            # Поля проверяем ПООТДЕЛЬНОСТИ: склейка в одну строку ломает якорь конца
            # пути — «handoff.md» перестает быть концом строки (проба selftest).
            fields = [str(ti.get(k) or "").strip() for k in ("file_path", "command", "path")]
            razgruzka = (tool in UNLOAD_TOOLS
                         or any(BREAK_GLASS in f or UNLOAD_PATH.search(f)
                                or UNLOAD_CMD.search(f) for f in fields))
            if not razgruzka:
                return (f"БЛОК (потолок контекста): в запросе {ctx:,} токенов при потолке "
                        f"{limit:,}. ".replace(",", " ")
                        + "Контекст живой сессии не уменьшается сам, и каждый следующий "
                          "вызов оплачивает весь этот вес заново (замер 02.09.2026: 55,7 % "
                          "счета — повторная доставка прочитанного). Разгрузка: записать "
                          ".agent/context/handoff.md (что сделано · что дальше · состояние · "
                          "ССЫЛКИ на файлы, не содержимое) и продолжить чистой сессией либо "
                          "субагентом — спавн Agent разрешен, у него контекст свой. "
                          "Замер: python3 scripts/context_ledger.py. Запись, спавн агента "
                          "и приборы замера проходят; чтение и произвольный Bash — нет. "
                          f"Разовый осознанный обход — префикс {BREAK_GLASS} в команде; "
                          "снять потолок совсем — THEMIZ_CTX_LIMIT=0.")

    # 2. Повторное чтение того же среза неизмененного файла.
    if tool == "Read" and ti.get("file_path"):
        key, stamp = read_key(ti, base)
        state = _state(session_id)
        seen = state.setdefault("reads", {})
        if seen.get(key) == stamp and stamp:
            return (f"БЛОК (повторное чтение): {os.path.basename(key.split('|')[0])} этим же "
                    "срезом уже прочитан в этой сессии, и файл с тех пор не менялся — он "
                    "лежит в контексте, второе чтение платится зря. Нужен другой участок — "
                    "звать со срезом (offset/limit) или грепом; нужен свежий файл — "
                    "он пройдет сам, как только изменится на диске.")
        seen[key] = stamp
        _save(session_id, state)
    return None


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        transcript = os.path.join(tmp, "s.jsonl")
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 10, "cache_creation_input_tokens": 90,
                "cache_read_input_tokens": 400}}}) + "\n")
        target = os.path.join(tmp, "f.md")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        claude_md = os.path.join(tmp, "CLAUDE.md")
        with open(claude_md, "w", encoding="utf-8") as fh:
            fh.write("y" * 100)

        def pl(**kw):
            base = {"session_id": "t1", "cwd": tmp, "transcript_path": transcript}
            base.update(kw)
            return base

        global CTX_LIMIT
        first = check(pl(tool_name="Read", tool_input={"file_path": target}, transcript_path=""))
        second = check(pl(tool_name="Read", tool_input={"file_path": target}, transcript_path=""))
        sliced = check(pl(tool_name="Read", tool_input={"file_path": target, "offset": 50},
                          transcript_path=""))
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("changed")
        os.utime(target, (0, 0))
        after_change = check(pl(tool_name="Read", tool_input={"file_path": target},
                                transcript_path=""))

        saved, CTX_LIMIT = CTX_LIMIT, 100
        over = check(pl(tool_name="Bash", tool_input={"command": "cat cases/x"}))
        over_read = check(pl(tool_name="Read", tool_input={"file_path": target}))
        unload = check(pl(tool_name="Write", tool_input={
            "file_path": ".agent/context/handoff.md", "content": "z"}))
        agent = check(pl(tool_name="Agent", tool_input={"description": "охота"}))
        measure = check(pl(tool_name="Bash", tool_input={
            "command": "python3 scripts/context_ledger.py"}))
        glass = check(pl(tool_name="Bash", tool_input={
            "command": f"{BREAK_GLASS} cat cases/x"}))
        sneaky = check(pl(tool_name="Bash", tool_input={"command": "git log && cat cases/x"}))
        CTX_LIMIT = 0
        off = check(pl(tool_name="Bash", tool_input={"command": "cat cases/x"}))
        CTX_LIMIT = saved

        big = check(pl(tool_name="Write", tool_input={
            "file_path": claude_md, "content": "y" * (INSTRUCTION_MAX_BYTES + 1)}))
        small = check(pl(tool_name="Write", tool_input={"file_path": claude_md, "content": "y"}))
        grow = check(pl(tool_name="Edit", tool_input={
            "file_path": claude_md, "old_string": "y",
            "new_string": "y" * (INSTRUCTION_MAX_BYTES + 10)}))
        other = check(pl(tool_name="Write", tool_input={
            "file_path": os.path.join(tmp, "note.md"), "content": "y" * 999_999}))

        checks = [
            ("первое чтение проходит", first is None),
            ("повторное чтение того же среза блокируется", second is not None),
            ("другой срез — не повтор", sliced is None),
            ("измененный файл читается заново", after_change is None),
            ("контекст выше потолка блокирует произвольный Bash", over is not None),
            ("контекст выше потолка блокирует чтение — оно и растит вес",
             over_read is not None),
            ("запись выгрузки проходит при пробитом потолке", unload is None),
            ("спавн субагента проходит: его контекст свой", agent is None),
            ("прибор замера проходит", measure is None),
            ("осознанный обход префиксом проходит", glass is None),
            ("слово-пропуск в середине команды лазейкой не работает", sneaky is not None),
            ("THEMIZ_CTX_LIMIT=0 снимает потолок", off is None),
            ("CLAUDE.md сверх байтового потолка блокируется", big is not None),
            ("CLAUDE.md в пределах потолка проходит", small is None),
            ("рост через Edit считается по байтам", grow is not None),
            ("посторонний файл байтовым потолком не меряется", other is None),
            ("битый payload не роняет сторожа", check(None) is None),
            ("нет транскрипта — нет потолка, а не блок",
             ctx_current(os.path.join(tmp, "нет.jsonl")) == 0),
            ("контекст читается с конца журнала", ctx_current(transcript) == 500),
        ]
        bad = [n for n, ok in checks if not ok]
        for n, ok in checks:
            print(f"  {'✓' if ok else '✗'} {n}")
        if bad:
            print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
            return 1
        print(f"selftest пройден: {len(checks)}/{len(checks)}")
        return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    reason = check(data)
    if reason:
        print(reason, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(0)
