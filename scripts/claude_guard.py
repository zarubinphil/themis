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


def _is_new_intake_file(cmd: str) -> bool:
    """Команда кладёт НОВЫЙ файл в 00_intake — пополнение, а не затирание.

    True только для `cp`/`mv` с ровно двумя аргументами, где источник лежит вне
    охраняемых папок, а цели на диске ещё нет. Любое отклонение — False, то есть
    блок: сторож ошибается в сторону запрета, а не разрешения.
    """
    import shlex
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False
    if len(parts) != 3 or parts[0] not in ("cp", "mv"):
        return False
    src, dst = parts[1], parts[2]
    if re.search(r"00_intake|_baselines", src):
        return False
    if "/00_intake/" not in dst.replace(os.sep, "/"):
        return False
    dst_abs = os.path.expanduser(dst)
    if os.path.isdir(dst_abs):        # цель-папка: имя внутри неизвестно, не рискуем
        return False
    return not os.path.exists(dst_abs)


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
# (три дела попали в .agent/drafts мимо конвейера).
# Запрет без легального пути производит обходы, а не дисциплину.
PRACTICE_MARKER = r"## (СОВЕТ ЗАВЕРШ|FAST-СИНТЕЗ ФЕМИДЫ)"


def _workflow_gate(p: str) -> None:
    """Порядок шагов протокола — детерминированно.

    Запись артефакта шага N блокируется, пока нет маркера шага N-1 на диске:
      practice.md   ← требует «## КАРТА ГОТОВА ✓» в knowledge-map.md
      positions.md  ← требует маркер практики (СОВЕТ ЗАВЕРШЕН либо FAST-СИНТЕЗ)
      .agent/drafts/*   ← требует оба маркера (кроме _working/ и _baselines/)
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
    km = case_root + "/.agent/context/knowledge-map.md"
    pr = case_root + "/.agent/context/practice.md"
    tail = "/".join(parts[i + 3:])

    if tail == ".agent/context/practice.md" and not _has_marker(km, r"## КАРТА ГОТОВА ✓"):
        block(
            "БЛОК ПРОТОКОЛА: practice.md пишется только после Шага 1 — "
            "в knowledge-map.md нет маркера «## КАРТА ГОТОВА ✓». Запустить case-mapper. "
            "Статус: python3 scripts/themis_status.py " + case_root
        )
    if tail == ".agent/context/positions.md" and not _has_marker(pr, PRACTICE_MARKER):
        block(
            "БЛОК ПРОТОКОЛА: positions.md пишется только после Шага 2 — "
            "в practice.md нет ни «## СОВЕТ ЗАВЕРШЕН», ни «## FAST-СИНТЕЗ ФЕМИДЫ». "
            "Запустить охоту/совет либо поставить честный FAST-маркер. "
            "Статус: python3 scripts/themis_status.py " + case_root
        )
    # Кухня и слой человека сторожатся ОДИНАКОВО. После переезда на два слоя
    # (19.08.2026) документ мог лечь прямо в GOTOVO/ мимо конвейера — то есть
    # мимо маркеров попасть сразу на стол юристу, минуя и карту, и практику.
    guarded = (tail.startswith(".agent/drafts/") or tail.startswith("GOTOVO/"))
    if guarded and "/_working/" not in norm and "/_baselines/" not in norm:
        if not _has_marker(km, r"## КАРТА ГОТОВА ✓") or not _has_marker(pr, PRACTICE_MARKER):
            where = "GOTOVO/" if tail.startswith("GOTOVO/") else ".agent/drafts/"
            block(
                f"БЛОК ПРОТОКОЛА: документ в {where} пишется только после Шагов 1-2 — "
                "нет маркера карты и/или практики. Судебные документы вне конвейера запрещены. "
                "Статус: python3 scripts/themis_status.py " + case_root
            )


# ── Дисциплина содержимого дела (этап 4) ────────────────────────────────────
# Под cases/ лежат материалы дела, а не программа. Два класса файлов туда не пишутся:
#   код     — генератор документа внутри дела обходит DocBuilder и гейты формата.
#             Так завелись 84 скрипта, 15 из них с запрещённым шрифтом Times New Roman:
#             каждый — отдельная реализация оформления, мимо document_guard.
#   растр   — рендер страницы производен от первички и восстанавливается за секунды;
#             487 картинок кухни занимали место в дереве дел и лезли в бэкап наравне
#             с доказательствами. Место рендера — кеш вне cases/ (--render-dir /tmp/…).
# Первичка (00_intake) из растрового правила исключена: там картинка и есть
# доказательство, и трогать её запрещено отдельным правилом выше.
CODE_EXT = ("py", "sh", "bash", "zsh")
RASTER_EXT = ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp")


def _service_dir(parts) -> bool:
    """cases/_assets, cases/_templates, cases/_logs — служебное хозяйство системы,
    а не дело. Там растр законен: `cases/_assets/подпись.png` — подпись владельца,
    её читает sign_and_pdf.py. Правило рендеров туда не распространяется."""
    i = parts.index("cases")
    return len(parts) > i + 1 and parts[i + 1].startswith("_")


def _cases_write_gate(paths) -> None:
    """Что нельзя класть под cases/. Пути — уже вычисленные цели записи."""
    for p in paths:
        norm = p.replace("\\", "/")
        parts = norm.split("/")
        if "cases" not in parts:
            continue
        ext = os.path.splitext(norm)[1].lower().lstrip(".")
        if ext in RASTER_EXT and _service_dir(parts):
            continue
        if ext in CODE_EXT:
            block(
                f"БЛОК: код (.{ext}) внутри cases/ запрещён — там материалы дела, не программа. "
                "Генератор документа мимо DocBuilder обходит гейты формата (так под cases/ "
                "накопились 84 скрипта, 15 с запрещённым шрифтом). Прибор пишется в scripts/, "
                "разовая обработка — во временный каталог."
            )
        if ext in RASTER_EXT and "/00_intake/" not in norm:
            block(
                f"БЛОК: растр (.{ext}) под cases/ вне 00_intake запрещён — рендер страницы "
                "производен и место ему в кеше: --render-dir /tmp/{дело}/{имя}. "
                "Картинка-доказательство кладётся в 00_intake новым файлом."
            )


# Тело heredoc — данные, а не команда. Сторож, читающий тело, ловит собственное
# описание правил: 19.08.2026 запись приёмки была заблокирована за строки
# «cp …» и «00_intake» ВНУТРИ текста файла. Остаток ПЕРВОЙ строки стрижку переживает:
# `cat <<'EOF' > файл` держит цель записи именно там, и съесть её значит открыть
# обход (враждебная проба 19.08.2026 — форма прошла сторожа).
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?([^\n]*)\n.*?^\s*\1\s*$", re.S | re.M)


def _strip_heredocs(cmd: str) -> str:
    return _HEREDOC_RE.sub(lambda m: "<<HEREDOC" + m.group(2), cmd)


_REDIRECT_RE = re.compile(r">>?\s*\|?\s*([^\s;&|<>()]+)")
# Цель — последний аргумент (cp SRC DST) либо каждый (tee A B, touch A B).
_VERB_LAST = ("cp", "mv", "install", "rsync", "ditto")
_VERB_ALL = ("tee", "touch")
# Правка на месте — тоже запись: sed -i меняет уже лежащий под cases/ генератор,
# и запрет «не создавать» без «не править» держится ровно до первого созданного файла.
_INPLACE_RE = re.compile(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?sed\s+((?:-\w+\s+)*-i\b[^;&|<>]*)", re.M)
_VERB_RE = re.compile(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(" + "|".join(_VERB_LAST + _VERB_ALL)
                      + r")\s+([^;&|<>]+)", re.M)
# Загрузчики пишут в файл флагом, а не редиректом.
_FETCH_RE = re.compile(r"(?:^|\s)(?:-o|--output|-O|--output-document)[=\s]+([^\s;&|<>]+)")
# Однострочник интерпретатора обходит и редирект, и cp. Целью считаем путь
# ТОЛЬКО когда в теле есть признак записи: чтение картинки дела разрешено.
_INTERP_RE = re.compile(r"\b(?:python3?|node|ruby|perl|php)\b[^\n|;]*?\s-(?:c|e)\b")
_WRITE_HINT_RE = re.compile(
    r"open\s*\([^)]*['\"][wax]b?['\"]|write_bytes|write_text|savefig|to_csv"
    r"|shutil\.(?:copy|move)|writeFileSync|File\.write|os\.rename")
_PATH_RE = re.compile(r"[\w./\\~-]*cases/[\w./\\-]+\.\w+")


def _split(args: str) -> list:
    try:
        import shlex
        return [a for a in shlex.split(args) if not a.startswith("-")]
    except ValueError:
        return [a for a in args.split() if not a.startswith("-")]


def _write_targets(cmd: str) -> list:
    """Пути, КУДА команда пишет. Упоминание пути в аргументе чтения целью не считается."""
    body = _strip_heredocs(cmd)
    targets = [t.strip("'\"") for t in _REDIRECT_RE.findall(body)]
    targets += [t.strip("'\"") for t in _FETCH_RE.findall(body)]
    for verb, args in _VERB_RE.findall(body):
        parts = _split(args)
        if not parts:
            continue
        targets += parts if verb in _VERB_ALL else parts[-1:]
    for args in _INPLACE_RE.findall(body):
        parts = _split(args)
        targets += parts[1:] if len(parts) > 1 else parts   # первый аргумент — выражение sed
    if _INTERP_RE.search(body) and _WRITE_HINT_RE.search(body):
        targets += [t.strip("'\"") for t in _PATH_RE.findall(body)]
    return targets


# Перенос ЦЕЛОГО каталога расширения не имеет: `mv /tmp/ocr дело/.../ocr` кладёт
# в дело сотню рендеров, а по имени цели этого не видно. Смотрим на диск —
# что в каталоге-источнике, то и приедет.
_BULK_LIMIT = 400


def _bulk_forbidden(cmd: str) -> str:
    body = _strip_heredocs(cmd)
    for verb, args in _VERB_RE.findall(body):
        if verb in _VERB_ALL:
            continue
        parts = _split(args)
        if len(parts) < 2:
            continue
        dst = parts[-1].strip("'\"")
        if "cases" not in dst.replace("\\", "/").split("/"):
            continue
        for src in parts[:-1]:
            src = os.path.expanduser(src.strip("'\""))
            if not os.path.isdir(src):
                continue
            for i, (dirpath, _dirs, files) in enumerate(os.walk(src)):
                if i > _BULK_LIMIT:
                    break
                for f in files:
                    ext = os.path.splitext(f)[1].lower().lstrip(".")
                    if ext in CODE_EXT or ext in RASTER_EXT:
                        return f"{src} → {dst} (внутри {f})"
    return ""


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
        _cases_write_gate([p])
        _workflow_gate(p)

    if tool == "Bash":
        cmd = as_str(ti.get("command"))
        _cases_write_gate(_write_targets(cmd))
        for t in _write_targets(cmd):
            _workflow_gate(t)      # документ въезжает в GOTOVO и обычным cp, не только Write
        bulk = _bulk_forbidden(cmd)
        if bulk:
            block(
                f"БЛОК: перенос каталога с кодом или рендерами внутрь cases/ — {bulk}. "
                "Рендеры живут в кеше (/tmp/{дело}/{имя}), приборы — в scripts/. "
                "Нужны сами материалы — их место в 00_intake, по одному файлу."
            )
        cmd = _strip_heredocs(cmd)
        protected = re.search(r"00_intake|_baselines", cmd)
        # rm только в командной позиции (начало строки / после ; & | $( `) —
        # иначе ложные срабатывания на прозу со словом «rm» в heredoc
        rm_cmd = re.search(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(?:rm|rmdir)\s", cmd, re.M)
        if rm_cmd and protected:
            block(
                "БЛОК: удаление в 00_intake/ или _baselines/ запрещено "
                "(железное правило). Действительно нужно — только пользователь вручную."
            )
        # ПОПОЛНЕНИЕ первички — не перезапись. Материалы клиента обязаны попадать
        # в 00_intake/, этим и занят inbox-triage (Bash mv из инбокса). Прежнее
        # правило рубило и его: сторож видел «mv … 00_intake/…» и блокировал
        # перенос НОВОГО файла наравне с затиранием существующего (прецедент
        # 04.08.2026 — сертификат ЭЦП не удалось положить в дело). Послабление
        # узкое: ровно cp/mv, ровно два аргумента, источник вне охраняемых папок,
        # а целевого файла на диске ещё НЕТ. Существует — блок как прежде.
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
        if write_cmd and protected and not _is_new_intake_file(cmd):
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
    # Первичка для проверки границы «затирание против пополнения»: один файл на
    # диске есть, второго нет. Пути реальные — правило смотрит именно на диск.
    from shlex import quote as shlex_quote
    intake = tmp + "/cases/k/d/00_intake"
    os.makedirs(intake, exist_ok=True)
    existing_intake = intake + "/est.pdf"
    with open(existing_intake, "w", encoding="utf-8") as f:
        f.write("scan")
    new_intake = intake + "/novyy.pdf"

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
        # Затирание СУЩЕСТВУЮЩЕЙ первички запрещено, пополнение новым файлом —
        # разрешено: без второго inbox-triage не смог бы положить материал в дело.
        ("cp поверх существующего файла в 00_intake блокируется",
         run({"tool_name": "Bash",
              "tool_input": {"command": f"cp a.pdf {shlex_quote(existing_intake)}"}}), 2),
        ("cp нового файла в 00_intake пропускается",
         run({"tool_name": "Bash",
              "tool_input": {"command": f"cp a.pdf {shlex_quote(new_intake)}"}}), 0),
        ("mv существующего файла ИЗ 00_intake блокируется",
         run({"tool_name": "Bash",
              "tool_input": {"command": f"mv {shlex_quote(existing_intake)} /tmp/x.pdf"}}), 2),
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
        # Слой человека сторожится так же, как кухня: документ не должен
        # оказаться на столе юриста мимо маркеров карты и практики.
        ("документ в папку готовых без маркеров блокируется",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/GOTOVO/isk.docx",
             "content": "x"}}), 2),
        ("рабочий файл кухни маркеров не требует",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/.agent/drafts/_working/review_log.md",
             "content": "x"}}), 0),
        # Дисциплина содержимого дела: под cases/ не пишутся ни код, ни рендеры.
        # Обе оси — и пропуск, и ложная тревога: сторож, срабатывающий на обиходе,
        # будет снят в первый же день, а снятый не сторожит вовсе.
        ("код .py под cases блокируется",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/.agent/context/_working/build_isk.py",
             "content": "x"}}), 2),
        ("код .sh под cases блокируется",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/gen.sh", "content": "x"}}), 2),
        ("heredoc, кладущий .py в дело, блокируется",
         run({"tool_name": "Bash", "tool_input": {"command":
              "cat > cases/klient/delo-2026/build.py <<'EOF'\nprint(1)\nEOF"}}), 2),
        ("cp .py в дело блокируется",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cp /tmp/gen.py cases/klient/delo-2026/gen.py"}}), 2),
        ("прибор в scripts/ пишется свободно",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "scripts/novyy_pribor.py", "content": "x"}}), 0),
        ("рендер .png в кухне дела блокируется",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/.agent/context/_working/ocr/page_001.png",
             "content": "x"}}), 2),
        ("сайдкар .txt рядом с рендером пишется",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/.agent/context/_working/ocr/page_001.txt",
             "content": "x"}}), 0),
        ("чтение картинки под cases разрешено (фолбэк vision)",
         run({"tool_name": "Read", "tool_input": {
             "file_path": "cases/klient/delo-2026/00_intake/foto.png"}}), 0),
        ("рендер в /tmp разрешён",
         run({"tool_name": "Bash", "tool_input": {"command":
              "python3 scripts/markdown_extract.py cases/k/d/00_intake/isk.pdf "
              "--render-dir /tmp/k/isk"}}), 0),
        # Формы записи, найденные враждебной пробой 19.08.2026: каждая проходила
        # сторожа, пока правило смотрело только на Write, cp и редирект.
        ("heredoc с редиректом ПОСЛЕ метки блокируется",
         run({"tool_name": "Bash", "tool_input": {"command":
              "cat <<'EOF' > cases/klient/delo-2026/build.py\nprint(1)\nEOF"}}), 2),
        ("touch .py в деле блокируется",
         run({"tool_name": "Bash", "tool_input": {"command": "touch cases/klient/delo-2026/gen.py"}}), 2),
        ("touch заметки .md в деле пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "touch cases/klient/delo-2026/n.md"}}), 0),
        ("curl -o картинки в дело блокируется",
         run({"tool_name": "Bash", "tool_input": {
             "command": "curl -o cases/klient/delo-2026/foto.png https://example/1"}}), 2),
        ("curl -o в /tmp пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "curl -o /tmp/foto.png https://example/1"}}), 0),
        ("запись интерпретатором в дело блокируется",
         run({"tool_name": "Bash", "tool_input": {
             "command": "python3 -c \"open('cases/klient/delo-2026/g.py','w').write('x')\""}}), 2),
        ("подпись владельца в служебном cases/_assets пишется",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/_assets/podpis.png", "content": "x"}}), 0),
        ("чтение картинки дела интерпретатором пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "python3 -c \"print(open('cases/k/d/00_intake/f.png','rb').read()[:4])\""}}), 0),
        ("sed -i правит генератор в деле — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "sed -i '' s/a/b/ cases/klient/delo-2026/gen.py"}}), 2),
        ("sed -i по файлу вне cases пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "sed -i '' s/a/b/ scripts/pribor.py"}}), 0),
        ("документ в GOTOVO командой мимо маркеров — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cp /tmp/isk.docx cases/klient/delo-2026/GOTOVO/isk.docx"}}), 2),
        ("рабочий файл кухни командой пишется",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cp /tmp/log.md cases/klient/delo-2026/.agent/drafts/_working/log.md"}}), 0),
        # Прецедент 19.08.2026: тело heredoc со строками «cp …» и «00_intake»
        # блокировало запись файла приёмки. Тело — данные, цель записи — команда.
        ("тело heredoc не принимается за команду",
         run({"tool_name": "Bash", "tool_input": {"command":
              "cat > scripts/spec.py <<'EOF'\nCONTRACT = 'cp a.pdf 00_intake/b.pdf блокируется'\nEOF"}}), 0),
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
