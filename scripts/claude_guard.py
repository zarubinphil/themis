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

Сторож судит ЦЕЛЬ, а не строку (враждебная проба 20.08.2026): относительный путь
резолвится через cwd из payload (Bash — через ведущий `cd`), симлинк цели
разворачивается, а «наши дела» — только cases/ ЭТОГО проекта, не любой каталог со
словом cases (чужой репозиторий, /tmp/cases — мимо).
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


# Корень НАШЕГО проекта и его cases/. Сторож судит материалы наших дел, а не любой
# путь со словом «cases»: чужой репозиторий под /tmp/chuzhoy/cases и /tmp/cases — не
# наши дела (проба 20.08.2026 ловила их как свои — сторож с такой тревогой снимают в
# первый день). Якорь — расположение самого сторожа: он всегда в {корень}/scripts/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PROJECT_CASES = os.path.join(PROJECT_ROOT, "cases")


def _resolve(path: str, base: str) -> str:
    """Абсолютный нормализованный путь цели. Относительный — от base (cwd из payload,
    ведущий `cd` для Bash: харнесс относительные пути принимает, а цель по ним всё
    равно вычислима). Затем разворачиваем симлинк самой цели: подмена файла-ссылки
    бьёт по оригиналу, судить надо назначение, а не имя ссылки."""
    if not path:
        return path
    p = os.path.expanduser(path.strip("'\""))
    if not os.path.isabs(p):
        p = os.path.join(base, p)
    return os.path.realpath(p)     # normpath + разбор симлинков; несуществующий путь — лексически


def _under_cases(abspath: str) -> bool:
    """abspath внутри cases/ НАШЕГО проекта (а не любого каталога со словом cases).

    Сравнение регистронезависимое: APFS/HFS+ регистр не различают, `00_INTAKE` и
    `cases` заглавными — ТОТ ЖЕ каталог, что `00_intake` и `cases`. Сторож, судящий
    по чувствительной к регистру строке, снимается сменой регистра (проба 20.08.2026)."""
    if not abspath:
        return False
    a = abspath.replace(os.sep, "/").split("/")
    c = PROJECT_CASES.replace(os.sep, "/").split("/")
    return (len(a) > len(c)
            and [x.casefold() for x in a[:len(c)]] == [x.casefold() for x in c])


def _case_rel(abspath: str):
    """Компоненты пути цели относительно наших cases/: [клиент, дело, ...хвост].
    None — цель вне наших дел. Регистр prefix не важен (APFS), хвост — как на диске."""
    if not _under_cases(abspath):
        return None
    a = abspath.replace(os.sep, "/").split("/")
    c = PROJECT_CASES.replace(os.sep, "/").split("/")
    return a[len(c):]


_LEADING_CD_RE = re.compile(r"^\s*cd\s+([^\s;&|]+)\s*(?:&&|;|\|)")


def _base_dir(cmd: str, payload: dict) -> str:
    """База относительных целей Bash: ведущий `cd DIR &&|;` → cwd из payload → cwd процесса."""
    m = _LEADING_CD_RE.match(cmd)
    base = m.group(1).strip("'\"") if m else ""
    if not base:
        cwd = payload.get("cwd")
        base = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    base = os.path.expanduser(base)
    if not os.path.isabs(base):
        base = os.path.join(os.getcwd(), base)
    return base


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
    if re.search(r"00_intake|_baselines", src, re.I):
        return False
    if not re.search(r"/00_intake/", dst.replace(os.sep, "/"), re.I):
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
    """Порядок шагов протокола — детерминированно. p — уже абсолютная цель записи.

    Запись артефакта шага N блокируется, пока нет маркера шага N-1 на диске:
      practice.md   ← требует «## КАРТА ГОТОВА ✓» в knowledge-map.md
      positions.md  ← требует маркер практики (СОВЕТ ЗАВЕРШЕН либо FAST-СИНТЕЗ)
      .agent/drafts/*   ← требует оба маркера (кроме _working/ и _baselines/)
    """
    rel = _case_rel(p)
    # структура cases/{клиент}/{дело}/{хвост...}; служебные папки (_templates, _logs) — мимо
    if rel is None or len(rel) < 3 or rel[0].startswith("_"):
        return
    case_root = os.path.join(PROJECT_CASES, rel[0], rel[1])
    km = os.path.join(case_root, ".agent/context/knowledge-map.md")
    pr = os.path.join(case_root, ".agent/context/practice.md")
    tail = "/".join(rel[2:])
    tail_cf = tail.casefold()       # APFS регистр не различает — сторож обязан тоже

    if tail_cf == ".agent/context/practice.md" and not _has_marker(km, r"## КАРТА ГОТОВА ✓"):
        block(
            "БЛОК ПРОТОКОЛА: practice.md пишется только после Шага 1 — "
            "в knowledge-map.md нет маркера «## КАРТА ГОТОВА ✓». Запустить case-mapper. "
            "Статус: python3 scripts/themis_status.py " + case_root
        )
    if tail_cf == ".agent/context/positions.md" and not _has_marker(pr, PRACTICE_MARKER):
        block(
            "БЛОК ПРОТОКОЛА: positions.md пишется только после Шага 2 — "
            "в practice.md нет ни «## СОВЕТ ЗАВЕРШЕН», ни «## FAST-СИНТЕЗ ФЕМИДЫ». "
            "Запустить охоту/совет либо поставить честный FAST-маркер. "
            "Статус: python3 scripts/themis_status.py " + case_root
        )
    # Кухня и слой человека сторожатся ОДИНАКОВО. После переезда на два слоя
    # (19.08.2026) документ мог лечь прямо в GOTOVO/ мимо конвейера — то есть
    # мимо маркеров попасть сразу на стол юристу, минуя и карту, и практику.
    guarded = tail_cf.startswith(".agent/drafts/") or tail_cf.startswith("gotovo/")
    tail_parts_cf = [x.casefold() for x in rel[2:]]
    exempt = "_working" in tail_parts_cf or "_baselines" in tail_parts_cf
    if guarded and not exempt:
        if not _has_marker(km, r"## КАРТА ГОТОВА ✓") or not _has_marker(pr, PRACTICE_MARKER):
            where = "GOTOVO/" if tail_cf.startswith("gotovo/") else ".agent/drafts/"
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


def _cases_write_gate(paths) -> None:
    """Что нельзя класть под cases/ НАШЕГО проекта. Пути — уже абсолютные цели записи."""
    for p in paths:
        rel = _case_rel(p)
        if rel is None:
            continue
        ext = os.path.splitext(p)[1].lower().lstrip(".")
        # cases/_assets, cases/_templates, cases/_logs — служебное хозяйство системы, а
        # не дело. Там растр законен: `cases/_assets/подпись.png` — подпись владельца.
        service = rel[0].startswith("_")
        in_intake = any(c.casefold() == "00_intake" for c in rel)   # APFS: 00_INTAKE == 00_intake
        if ext in RASTER_EXT and service:
            continue
        if ext in CODE_EXT:
            block(
                f"БЛОК: код (.{ext}) внутри cases/ запрещён — там материалы дела, не программа. "
                "Генератор документа мимо DocBuilder обходит гейты формата (так под cases/ "
                "накопились 84 скрипта, 15 с запрещённым шрифтом). Прибор пишется в scripts/, "
                "разовая обработка — во временный каталог."
            )
        if ext in RASTER_EXT and not in_intake:
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
_VERB_LAST = ("cp", "mv", "install", "rsync", "ditto", "ln")
_VERB_ALL = ("tee", "touch")
# Правка на месте — тоже запись: sed -i / perl -i / ruby -i меняют уже лежащий под
# cases/ файл, и запрет «не создавать» без «не править» держится ровно до первого созданного.
_INPLACE_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(?:sed|perl|ruby)\s+((?:-\w+\s+)*-i\b[^;&|<>]*)", re.M)
_VERB_RE = re.compile(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(" + "|".join(_VERB_LAST + _VERB_ALL)
                      + r")\s+([^;&|<>]+)", re.M)
# Обнуляют/затирают файл-аргумент, не редиректом и не позицией cp: truncate -s 0 FILE,
# gzip FILE (заменяет на .gz, удаляя оригинал), split (пишет по префиксу), cpio/zip
# (сборка/разбор архива), shred (уничтожает). Цель — позиционный аргумент; блокируем
# лишь когда он резолвится под наши cases/ или в 00_intake/_baselines.
_FILE_VERB_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?"
    r"(?:truncate|gzip|gunzip|bzip2|bunzip2|xz|unxz|split|cpio|zip|shred)\s+([^;&|<>]+)", re.M)
# Загрузчики пишут в файл флагом, а не редиректом.
_FETCH_RE = re.compile(r"(?:^|\s)(?:-o|--output|-O|--output-document)[=\s]+([^\s;&|<>]+)")
# Однострочник интерпретатора обходит и редирект, и cp. Целью считаем путь
# ТОЛЬКО когда в теле есть признак записи: чтение картинки дела разрешено.
_INTERP_RE = re.compile(r"\b(?:python3?|node|ruby|perl|php)\b[^\n|;]*?\s-(?:c|e)\b")
_WRITE_HINT_RE = re.compile(
    r"open\s*\([^)]*['\"][wax]b?['\"]|write_bytes|write_text|savefig|to_csv"
    r"|shutil\.(?:copy|move)|writeFileSync|File\.write|os\.rename")
_PATH_RE = re.compile(r"[\w./\\~-]*cases/[\w./\\-]+\.\w+")
# Путь записи ОТНОСИТЕЛЬНЫЙ: `cd дело && python3 -c "open('00_intake/x.pdf','w')"` —
# `cases/` в строке нет, _PATH_RE слеп, а цель резолвится от ведущего cd (проба круга 4).
_OPEN_TARGET_RE = re.compile(r"open\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wax]b?['\"]")


def _split(args: str) -> list:
    """Позиционные аргументы (без опций). Инлайн-комментарий `# …` и всё после него
    отбрасываем: слово «00_intake» в комментарии — не цель (проба 20.08.2026)."""
    try:
        import shlex
        toks = shlex.split(args)
    except ValueError:
        toks = args.split()
    out = []
    for a in toks:
        if a.startswith("#"):
            break
        if not a.startswith("-"):
            out.append(a)
    return out


def _target_dir_flag(toks: list):
    """Значение -t/--target-directory (cp/mv/install кладут источники ВНУТРЬ этого
    каталога, а не в последний позиционный) либо None."""
    for i, t in enumerate(toks):
        if t in ("-t", "--target-directory") and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith("--target-directory="):
            return t.split("=", 1)[1]
        if t.startswith("-t") and len(t) > 2 and not t.startswith("--"):
            return t[2:]
    return None


def _is_dir_dest(dst_raw: str, dst_abs: str) -> bool:
    """DST — каталог: cp SRC DST кладёт SRC внутрь как DST/basename(SRC). Определяем по
    завершающему слэшу, наличию на диске, либо по структурному каталогу дела без
    расширения (GOTOVO, 02_hearings, .agent/…, сам корень дела) — цель-каталог без
    слэша иначе прошла бы мимо гейта протокола (проба 20.08.2026)."""
    if dst_raw.endswith("/") or dst_raw.endswith(os.sep):
        return True
    try:
        if os.path.isdir(dst_abs):
            return True
    except OSError:
        pass
    return _under_cases(dst_abs) and not os.path.splitext(dst_abs)[1]


def _copy_move_targets(args: str, base: str) -> list:
    """Абсолютные цели cp/mv/install/rsync/ditto/ln с учётом -t DIR и dst-каталога."""
    try:
        import shlex
        toks = shlex.split(args)
    except ValueError:
        toks = args.split()
    cut = []
    for t in toks:                 # инлайн-комментарий обрывает разбор
        if t.startswith("#"):
            break
        cut.append(t)
    toks = cut
    tdir = _target_dir_flag(toks)
    pos = [t for t in toks if not t.startswith("-")]
    if tdir is not None:
        d = _resolve(tdir, base)
        srcs = [p for p in pos if p != tdir]     # сам -t DIR — не источник
        return [os.path.join(d, os.path.basename(s.rstrip("/"))) for s in srcs] or [d]
    if len(pos) < 2:
        return [_resolve(p, base) for p in pos]
    srcs, dst = pos[:-1], pos[-1]
    dst_abs = _resolve(dst, base)
    if _is_dir_dest(dst, dst_abs):
        return [os.path.join(dst_abs, os.path.basename(s.rstrip("/"))) for s in srcs]
    return [dst_abs]


def _write_targets(cmd: str, base: str) -> list:
    """Абсолютные пути, КУДА команда пишет. Упоминание пути в аргументе чтения целью
    не считается; относительные резолвятся от base (ведущий cd / cwd payload)."""
    body = _strip_heredocs(cmd)
    targets = [_resolve(t, base) for t in _REDIRECT_RE.findall(body)]
    targets += [_resolve(t, base) for t in _FETCH_RE.findall(body)]
    targets += [_resolve(t, base) for t in _git_checkout_targets(body)]
    targets += [_resolve(t, base) for t in _DD_OF_RE.findall(body)]
    for verb, args in _VERB_RE.findall(body):
        if verb in _VERB_ALL:                       # tee, touch — каждый аргумент
            targets += [_resolve(t, base) for t in _split(args)]
        else:                                       # cp mv install rsync ditto ln
            targets += _copy_move_targets(args, base)
    for args in _INPLACE_RE.findall(body):
        parts = _split(args)
        # первый позиционный — выражение sed/perl (s/a/b/), файл дальше
        targets += [_resolve(t, base) for t in (parts[1:] if len(parts) > 1 else parts)]
    for args in _FILE_VERB_RE.findall(body):
        targets += [_resolve(t, base) for t in _split(args)]
    if _INTERP_RE.search(body) and _WRITE_HINT_RE.search(body):
        targets += [_resolve(t, base) for t in _PATH_RE.findall(body)]
        targets += [_resolve(t, base) for t in _OPEN_TARGET_RE.findall(body)]
    return targets


# Обёртки, которыми враждебная проба 19.08.2026 провела запись мимо сторожа:
# sh -c/bash -c (тело — строка, не команда), var=cmd;$var (глагол через переменную),
# $(echo cmd) (глагол через сабшелл) и функция оболочки f(){...};f. Ни одна не меняет
# ЦЕЛЬ записи — только прячет ГЛАГОЛ от regexp, который её вычисляет. Разворачиваем
# рекурсивно (глубина 4 — щедрый потолок против случайного бесконечного цикла) и
# отдаём дальше плоский текст: вся остальная логика файла работает с ним как обычно.
_ASSIGN_RE = re.compile(r"(?:^|[;&\n])\s*([A-Za-z_]\w*)=([^\s;&|]+)")
_VAR_REF_RE = re.compile(r"\$\{?(\w+)\}?")
_ECHO_SUBST_RE = re.compile(r"\$\(\s*echo\s+([^\s)]+)\s*\)")
_SHC_RE = re.compile(r"\b(?:sh|bash|zsh)\s+-c\s+([\"'])(.*?)\1", re.S)
_FUNC_RE = re.compile(r"\b\w+\s*\(\)\s*\{(.*?)\}", re.S)
# Ещё обёртки, прячущие ГЛАГОЛ от regexp (проба круга 4, 20.08.2026). Ни одна не меняет
# ЦЕЛЬ записи — разворачиваем к плоскому глаголу тем же приёмом, что sh -c/var=/$(echo):
#   eval 'cp … дело'            — тело-строка исполняется оболочкой;
#   bash <<< 'cp … дело'        — here-string кормит команду на stdin;
#   $(which cp) … дело          — глагол через подстановку пути;
#   echo … | xargs -I F cp F …  — глагол за пределами командной позиции;
#   find … -exec cp {} … \;     — то же, глагол внутри -exec.
_EVAL_RE = re.compile(r"\beval\s+([\"'])(.*?)\1", re.S)
_HERESTRING_RE = re.compile(r"\b(?:sh|bash|zsh)\s+<<<\s*([\"'])(.*?)\1", re.S)
_WHICH_SUBST_RE = re.compile(r"\$\(\s*which\s+([^\s)]+)\s*\)")
# xargs + его опции (значение-берущие -I/-E/-d/-n/-P/-s/-L/-a глотают следующий токен,
# флаги -0/-r/-t/-p/-x — нет) → следующий токен и есть исполняемый глагол.
_XARGS_RE = re.compile(
    r"\bxargs\b"
    r"(?:\s+-[IEdnPsLa]\s*\S+|\s+-[0rtpx]+|\s+--[\w-]+(?:=\S+)?)*"
    r"\s+", re.M)
# find … -exec CMD … {} … \;|+  — CMD исполняется для каждого совпадения.
_FIND_EXEC_RE = re.compile(r"-exec(?:dir)?\s+(.+?)\s+(?:\\?;|\+)", re.S)

# Префикс-модификатор команды прячет ГЛАГОЛ от regexp, не трогая ЦЕЛЬ записи:
# `env A=B cp`, `command cp`, `nice -n5 cp`, `exec cp`, `FOO=bar cp`, `\cp` — всё
# это проходило сторожа мимо (проба скептика 19.08.2026), потому что после префикса
# `cp` уже не в командной позиции. Снимаем префиксы ДО вычисления целей — тот же
# приём, что для sh -c/var=/$(echo)/функции: разворачиваем к плоскому глаголу.
_ESC_VERB_RE = re.compile(r"((?:^|[;&|]|\$\(|`)[ \t]*)\\(?=[A-Za-z])", re.M)
_PREFIX_RE = re.compile(
    r"((?:^|[;&|]|\$\(|`)[ \t]*)"                                   # 1: командная позиция (сохраняем)
    r"(?:"
      r"(?:sudo|env|command|builtin|exec|nohup|nice|stdbuf|time|ionice|setsid)\b"
      r"(?:[ \t]+-{1,2}[^\s;&|]+)*"                                 # флаги префикса: -n5, --
      r"|[A-Za-z_]\w*=[^\s;&|]*"                                    # либо VAR=val
    r")[ \t]+", re.M)


def _strip_cmd_prefixes(cmd: str) -> str:
    out = _ESC_VERB_RE.sub(lambda m: m.group(1), cmd)
    for _ in range(8):                       # префиксы стекаются: sudo env A=B nice cp
        nxt = _PREFIX_RE.sub(lambda m: m.group(1), out)
        if nxt == out:
            break
        out = nxt
    return out


def _normalize(cmd: str, depth: int = 0) -> str:
    """Разворачивает типовые обёртки shell до плоского текста. Не полноценный
    интерпретатор — эвристика под обходы, которые реально нашла проба.

    ponytail: static-target модель — эвристика под реально найденные пробой обёртки,
    не полный shell. `xargs`/`find -exec` разворачиваем к командной позиции (`; глагол`),
    ставя следующий за обёрткой токен глаголом; редкие формы (`-P4 cp` без -I) — потолок."""
    if depth > 4:
        return cmd
    out = cmd
    assigns = dict(_ASSIGN_RE.findall(out))
    out = _VAR_REF_RE.sub(lambda m: assigns.get(m.group(1), m.group(0)), out)
    out = _ECHO_SUBST_RE.sub(lambda m: m.group(1), out)
    out = _WHICH_SUBST_RE.sub(lambda m: m.group(1), out)
    out = _strip_cmd_prefixes(out)
    out = _SHC_RE.sub(lambda m: _normalize(m.group(2), depth + 1), out)
    out = _EVAL_RE.sub(lambda m: "; " + _normalize(m.group(2), depth + 1) + " ;", out)
    out = _HERESTRING_RE.sub(lambda m: "; " + _normalize(m.group(2), depth + 1) + " ;", out)
    out = _XARGS_RE.sub("; ", out)
    out = _FIND_EXEC_RE.sub(lambda m: "; " + m.group(1) + " ;", out)
    out = _FUNC_RE.sub(lambda m: "; " + _normalize(m.group(1), depth + 1) + " ;", out)
    return out


# git checkout/restore пишут (перезаписывают) файл рабочего дерева из индекса/истории —
# такая же перезапись, как cp/dd/sed -i, просто именем команды не похожая ни на одну.
_GIT_CO_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?git\s+(?:checkout|restore)\b([^;&|<>]*)", re.M)


def _git_checkout_targets(body: str) -> list:
    out = []
    for args in _GIT_CO_RE.findall(body):
        try:
            import shlex
            toks = shlex.split(args)
        except ValueError:
            toks = args.split()
        if "--" in toks:
            out += toks[toks.index("--") + 1:]
        else:
            # Без «--»: путь узнаём по «/», чтобы не принять ветку/HEAD за файл.
            out += [t for t in toks if "/" in t and not t.startswith("-")]
    return [t.strip("'\"") for t in out]


# dd пишет через `of=ПУТЬ`, не позиционным аргументом — отдельный разбор.
_DD_OF_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?dd\b[^;&|<>]*?\bof=([^\s;&|<>]+)", re.M)

# git apply / patch пишут файл, указанный ВНУТРИ содержимого патча — командная строка
# его не называет. Сторож не может проверить конкретную цель, поэтому судит по
# ОБЛАСТИ действия (-C у git apply, -d у patch): если она внутри cases/, применение
# патча запрещено целиком — патч непрозрачен, а дело не терпит правки мимо конвейера.
_PATCH_SCOPE_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?git\s+-C\s+(\S+)\s+apply\b"
    r"|(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?patch\b(?:\s+-\w+)*\s+-d\s+(\S+)"
)


def _patch_scope_hits_cases(cmd: str, base: str) -> bool:
    for m in _PATCH_SCOPE_RE.finditer(cmd):
        d = (m.group(1) or m.group(2) or "").strip("'\"")
        if d and _under_cases(_resolve(d, base)):
            return True
    return False


def _under_protected(path: str) -> bool:
    """Цель лежит внутри 00_intake/ или _baselines/ — первичка и база «ДО»."""
    norm = path.replace(os.sep, "/").strip("'\"")
    return bool(re.search(r"(?:^|/)(?:00_intake|_baselines)(?:/|$)", norm, re.I))


# mv УДАЛЯЕТ источник — перенос СУЩЕСТВУЮЩЕГО файла ИЗ 00_intake/_baselines
# так же разрушителен, как перезапись, хотя цель записи (последний аргумент)
# лежит вне охраняемых папок и по ней одной это не видно.
_MV_RE = re.compile(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?mv\s+([^;&|<>]+)", re.M)


def _mv_sources(body: str) -> list:
    out = []
    for args in _MV_RE.findall(body):
        parts = _split(args)
        if len(parts) >= 2:
            out += parts[:-1]
    return out


# rm/rmdir — удаление. Судим ЦЕЛИ команды, а не подстроку по всей строке: слово
# «00_intake»/«_baselines» в аргументе чтения дальше по строке или в комментарии
# не делает `rm /tmp/x` ударом по делу (проба 20.08.2026 отбивала обиход координатора).
_RM_RE = re.compile(r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(?:rm|rmdir)\s+([^;&|<>]+)", re.M)


def _rm_targets(cmd: str, base: str) -> list:
    out = []
    for args in _RM_RE.findall(cmd):
        out += [_resolve(t, base) for t in _split(args)]
    return out


# Перенос ЦЕЛОГО каталога расширения не имеет: `mv /tmp/ocr дело/.../ocr` кладёт
# в дело сотню рендеров, а по имени цели этого не видно. Смотрим на диск —
# что в каталоге-источнике, то и приедет.
_BULK_LIMIT = 400

# Распаковка архива — запись «вслепую»: что внутри, сторож не видит, а в дело
# высыпается всё разом. По первичке `unzip -o` вдобавок затирает оригиналы.
# Законный путь: распаковать во временный каталог и положить файлы по одному.
_UNPACK_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?(?:unzip|tar|bsdtar|7z|unrar)\b[^;&|<>]*?"
    r"\s(?:-d|-C|--directory|-o(?=\s))\s*([^\s;&|<>]+)", re.M)
# python3 -m zipfile -e ARCH DST  /  python3 -m tarfile -e ARCH DST — распаковка модулем
# stdlib: каталог назначения — последний позиционный (проба круга 4).
_PY_UNPACK_RE = re.compile(
    r"\bpython3?\s+-m\s+(?:zipfile|tarfile)\s+-e\s+\S+\s+(\S+)", re.M)
# Распаковка БЕЗ флага каталога кладёт архив в CWD. После ведущего `cd дело/00_intake`
# CWD — внутри дела: `cd дело/00_intake && tar xf a.tar` высыпает архив в первичку,
# хотя каталог назначения в командной строке не назван (проба круга 4).
_EXTRACT_VERB_RE = re.compile(
    r"(?:^|[;&|]|\$\(|`)\s*(?:sudo\s+)?"
    r"(?:tar\s+-?[A-Za-z]*x|unzip\b|bsdtar\s+-?[A-Za-z]*x|7z\s+[ex]\b|unrar\s+[ex]\b)", re.M)
_DIR_FLAG_RE = re.compile(r"(?:^|\s)(?:-C|--directory|-d)\b")


def _unpack_into_cases(cmd: str, base: str) -> str:
    body = _strip_heredocs(cmd)
    for d in _UNPACK_RE.findall(body) + _PY_UNPACK_RE.findall(body):
        d_abs = _resolve(d.strip("'\""), base)
        if _under_cases(d_abs):
            return d.strip("'\"")
    return ""


def _extract_into_cwd(cmd: str, base: str) -> bool:
    """Распаковка без явного каталога назначения — цель есть CWD (ведущий cd / cwd payload)."""
    body = _strip_heredocs(cmd)
    if not _EXTRACT_VERB_RE.search(body) or _DIR_FLAG_RE.search(body):
        return False              # каталог назван явно — им займётся _unpack_into_cases
    b = os.path.expanduser(base)
    if not os.path.isabs(b):
        b = os.path.join(os.getcwd(), b)
    return _under_cases(os.path.realpath(b))


def _bulk_forbidden(cmd: str, base: str) -> str:
    body = _strip_heredocs(cmd)
    for verb, args in _VERB_RE.findall(body):
        if verb in _VERB_ALL:
            continue
        parts = _split(args)
        if len(parts) < 2:
            continue
        dst = _resolve(parts[-1], base)
        if not _under_cases(dst):
            continue
        for src in parts[:-1]:
            src = os.path.expanduser(src.strip("'\""))
            if not os.path.isabs(src):
                src = os.path.join(base, src)
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


# Чужой CLI за границей процесса — мимо наших ворот. claude_guard живёт ВНУТРИ
# нашего процесса; прямой вызов чужого CLI из Bash уносит материалы дела
# без обезличивания и пробы, а за границей процесса сторожа нет вовсе (проба
# 20.08.2026). Имена берём из декларативного реестра (единственный дом имён, этап
# 9.1) — не хардкод; наш claude и наш коннектор foreign_cli/cli_router под запрет
# не попадают. Судим ГЛАГОЛ в командной позиции, а не подстроку: имя в пути
# (`scripts/foreign_cli.py`) или в кавычках (`echo '… cli_registry.json'`) — не вызов.
def _foreign_cli_names() -> list:
    reg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli_registry.json")
    try:
        names = json.loads(open(reg, encoding="utf-8").read())
    except (OSError, ValueError):
        return []
    return [n for n in names if isinstance(n, str) and n and n != "claude"]


def _foreign_cli_re():
    names = _foreign_cli_names()
    if not names:
        return None
    body = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # Командная позиция: начало, после ;/&/|, сабшелла $(…) или обратной кавычки.
    # Префиксы (env/sudo/…) уже сняты _normalize. `\b` — имя целым словом, не куском.
    return re.compile(rf"(?:^|[;&|]|\$\(|`)\s*(?:{body})\b")


_FOREIGN_CLI_RE = _foreign_cli_re()

# Имя чужого CLI в ТЕКСТЕ команды — не вызов: `git commit -m "… ; ИМЯ через коннектор"`,
# `git log --grep=ИМЯ` упоминают имя как данные. Снимаем содержимое кавычек ДО поиска
# командной позиции — иначе `;` внутри сообщения читается как разделитель и собственный
# коммит цикла встаёт (проба круга 4). Реальный вызов `ИМЯ exec "…"` держит имя ВНЕ
# кавычек и переживает стрижку. (Имена — только в реестре, в коде их нет: этап 9.1.)
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _strip_quoted(s: str) -> str:
    return _QUOTED_RE.sub(" ", s)


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

    if not isinstance(d, dict):
        d = {}
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
        # Порог бюджета обходили регистром (/THEMIS/), симлинком (ссылка вне проекта на
        # файл внутри) и `~` (getsize не раскрывает тильду → размер 0). Резолвим цель
        # ДО замера: expanduser + realpath снимают все три; регистр держит re.I.
        rcwd = as_str(d.get("cwd")) or os.getcwd()
        p_res = _resolve(p, rcwd)
        # Гейт держим на файлах проекта: внешние материалы (справки, чужие репозитории)
        # аудитор обязан читать целиком, и запрещать ему это — не экономия, а слепота.
        if (re.search(r"\.(md|txt|jsonl|log|csv)$", p_res, re.I)
                and re.search(r"/themis/", p_res.replace("\\", "/"), re.I)
                and not ti.get("offset") and not ti.get("limit")):
            try:
                size = os.path.getsize(p_res)
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
        # Относительный путь резолвим через cwd payload ДО всех гейтов: харнесс его
        # принимает, а `00_intake/x.pdf`+cwd=дело — та же цель, что абсолютная.
        p_raw = as_str(ti.get("file_path")) or as_str(ti.get("notebook_path"))
        wcwd = as_str(d.get("cwd")) or os.getcwd()
        p = _resolve(p_raw, wcwd)
        if re.search(r"/00_intake/", p.replace(os.sep, "/"), re.I):   # APFS: 00_INTAKE == 00_intake
            block(
                "БЛОК: 00_intake/ неприкосновенен — исходники клиента "
                "не редактировать и не перезаписывать (железное правило)."
            )
        _cases_write_gate([p])
        _workflow_gate(p)

    if tool == "Bash":
        raw_cmd = as_str(ti.get("command"))
        stripped = _strip_heredocs(raw_cmd)     # для _is_new_intake_file — узкая легитимность
        cmd = _normalize(stripped)              # sh -c/bash -c/var=/$( echo )/функция — плоско
        base = _base_dir(cmd, d)                # ведущий cd → cwd payload → cwd процесса

        # Прямой вызов чужого CLI мимо коннектора: за границей процесса ворот нет.
        # Ищем ГЛАГОЛ в командной позиции по строке БЕЗ содержимого кавычек — имя в
        # тексте сообщения/аргумента вызовом не считается.
        if _FOREIGN_CLI_RE is not None and _FOREIGN_CLI_RE.search(_strip_quoted(cmd)):
            block(
                "БЛОК: прямой вызов чужого CLI мимо коннектора запрещён — за границей "
                "процесса claude_guard нет, и материалы дела уйдут без обезличивания и "
                "пробы (ст. 8 ФЗ № 63-ФЗ). Чужой инструмент вызывается только через "
                "python3 scripts/foreign_cli.py --role … — он обезличит текст, вычистит "
                "окружение и обернёт вызов гейтами."
            )

        targets = _write_targets(cmd, base)
        _cases_write_gate(targets)
        for t in targets:
            _workflow_gate(t)      # документ въезжает в GOTOVO и обычным cp, не только Write

        unpack = _unpack_into_cases(cmd, base)
        if unpack or _extract_into_cwd(cmd, base):
            where = unpack or (base + " (CWD после ведущего cd)")
            block(
                f"БЛОК: распаковка архива прямо в дело ({where}) запрещена — сторож не видит, "
                "что внутри, а по первичке распаковка ещё и затирает оригиналы. Распаковать "
                "во временный каталог, затем класть файлы по одному (материалы — в 00_intake)."
            )
        bulk = _bulk_forbidden(cmd, base)
        if bulk:
            block(
                f"БЛОК: перенос каталога с кодом или рендерами внутрь cases/ — {bulk}. "
                "Рендеры живут в кеше (/tmp/{дело}/{имя}), приборы — в scripts/. "
                "Нужны сами материалы — их место в 00_intake, по одному файлу."
            )
        if _patch_scope_hits_cases(cmd, base):
            block(
                "БЛОК: применение патча внутри cases/ (git apply / patch) запрещено — "
                "цель правки не видна из командной строки (она внутри самого патча), "
                "а дело может измениться мимо конвейера. Патч — во временный каталог, "
                "материалы дела кладутся по одному новым файлом."
            )
        # rm/rmdir — по ЦЕЛЯМ, не по подстроке: слово в аргументе чтения или комментарии
        # дальше по строке не превращает удаление во временном каталоге в удар по делу.
        if any(_under_protected(t) for t in _rm_targets(cmd, base)):
            block(
                "БЛОК: удаление в 00_intake/ или _baselines/ запрещено "
                "(железное правило). Действительно нужно — только пользователь вручную."
            )
        # ПОПОЛНЕНИЕ первички — не перезапись. Материалы клиента обязаны попадать
        # в 00_intake/, этим и занят inbox-triage (Bash mv из инбокса). Прежнее
        # правило рубило и его: сторож видел «mv … 00_intake/…» и блокировал
        # перенос НОВОГО файла наравне с затиранием существующего (прецедент
        # 04.08.2026 — сертификат ЭЦП не удалось положить в дело). Послабление
        # узкое: ровно cp/mv, ровно два аргумента (в СЫРОЙ, необёрнутой команде —
        # sh -c/var-subst/... легитимности не получают), источник вне охраняемых
        # папок, а целевого файла на диске ещё НЕТ. Существует — блок как прежде.
        #
        # Сторож судит ЦЕЛЬ записи, а не имя команды (этап 9, аудит 19.08.2026):
        # раньше здесь стояло регэксп-угадывание глагола (cp/mv/tee/dd/sed -i) —
        # 16 форм (git checkout/restore, git apply, patch, ln, sh -c, bash -c,
        # var=/$(echo)/функция, python3 -c) обходили его, не будучи похожими ни на
        # один из перечисленных глаголов. Теперь источник истины один — ТЕ ЖЕ
        # targets, что уже посчитаны выше для code/raster-гейта и workflow-гейта:
        # что реально пишется, а не как это названо в командной строке.
        protected_targets = [t for t in targets if _under_protected(t)]
        removed_sources = [_resolve(s, base) for s in _mv_sources(cmd)]
        removed_sources = [s for s in removed_sources if _under_protected(s)]
        if (protected_targets or removed_sources) and not _is_new_intake_file(stripped):
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
    # Обход бюджета Read: симлинк ИЗ вне проекта на большой файл внутри — путь ссылки
    # «/themis/» не содержит, но realpath ведёт в проект. И большой файл вне проекта —
    # его аудитор читает целиком, гейт молчит (обе оси, круг 4).
    ext = tempfile.mkdtemp()          # без «themis» в пути — внешний материал
    ext_big = ext + "/plain.md"
    with open(ext_big, "w", encoding="utf-8") as f:
        f.write("y" * (BIG_READ_BYTES + 10))
    ext_link = ext + "/link.md"
    try:
        os.symlink(big, ext_link)
    except (OSError, NotImplementedError):
        ext_link = None
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
        ("распаковка архива в кухню дела — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "unzip mat.zip -d cases/klient/delo-2026/.agent/context/_working"}}), 2),
        ("распаковка архива в первичку — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "tar -xf mat.tar -C cases/klient/delo-2026/00_intake"}}), 2),
        ("распаковка во временный каталог пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "unzip mat.zip -d /tmp/mat"}}), 0),
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
        # Префикс-модификатор прячет глагол, но не цель (проба скептика 19.08.2026).
        # Каждая форма — код в дело мимо DocBuilder, обязана блокироваться.
        ("env VAR=val cp кода в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "env A=B cp /tmp/g.py cases/klient/delo-2026/gen.py"}}), 2),
        ("FOO=bar cp кода в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "FOO=bar cp /tmp/g.py cases/klient/delo-2026/gen.py"}}), 2),
        ("command cp кода в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "command cp /tmp/g.py cases/klient/delo-2026/gen.py"}}), 2),
        ("\\cp (обход алиаса) кода в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "\\cp /tmp/g.py cases/klient/delo-2026/gen.py"}}), 2),
        ("nice -n5 cp кода в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "nice -n5 cp /tmp/g.py cases/klient/delo-2026/gen.py"}}), 2),
        ("exec cp кода в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "exec cp /tmp/g.py cases/klient/delo-2026/gen.py"}}), 2),
        ("env cp поверх существующей первички — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": f"env A=B cp a.pdf {shlex_quote(existing_intake)}"}}), 2),
        # Ложная тревога недопустима: префикс на безобидной команде молчит.
        ("env VAR=val перед echo пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "env FOO=bar echo hi"}}), 0),
        ("nice перед обычным cp вне cases пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "nice cp a.md b.md"}}), 0),
        # ── Сторож судит ЦЕЛЬ, а не строку (враждебная проба 20.08.2026) ──────
        # Относительный путь: харнесс его принимает, цель резолвится через cwd/cd.
        ("Write относительной первички с cwd — блок",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "00_intake/podmena.pdf", "content": "x"}, "cwd": intake[:-len("/00_intake")]}), 2),
        ("cd в первичку и cp поверх существующего — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": f"cd {shlex_quote(intake)} && cp /tmp/e.pdf est.pdf"}}), 2),
        ("truncate первички — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": f"truncate -s 0 {shlex_quote(existing_intake)}"}}), 2),
        ("truncate во временном каталоге пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "truncate -s 0 /tmp/scratch.bin"}}), 0),
        ("perl -i по первичке — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": f"perl -i -pe 's/a/b/' {shlex_quote(existing_intake)}"}}), 2),
        ("perl -i по прибору вне cases пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "perl -i -pe 's/a/b/' scripts/pribor.py"}}), 0),
        ("cp -t каталог-цель с кодом в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cp -t cases/klient/delo-2026 /tmp/gen.py"}}), 2),
        ("cp -t в /tmp пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "cp -t /tmp/out /some/gen.py"}}), 0),
        ("install -t в первичку — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": f"install -m644 -t {shlex_quote(intake)} /tmp/evil.pdf"}}), 2),
        ("cp в каталог GOTOVO без слэша мимо маркеров — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cp /tmp/isk.docx cases/klient/delo-2026/GOTOVO"}}), 2),
        # Ложная тревога снята: rm во временном каталоге не блокируется из-за слова
        # 00_intake/_baselines в аргументе чтения дальше или в комментарии.
        ("rm /tmp при чтении 00_intake дальше по строке пропускается",
         run({"tool_name": "Bash", "tool_input": {"command":
              "rm -rf /tmp/render && python3 scripts/markdown_extract.py "
              "cases/k/d/00_intake/isk.pdf --render-dir /tmp/render"}}), 0),
        ("rm /tmp с комментарием про _baselines пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "rm /tmp/junk.txt   # база _baselines не трогается"}}), 0),
        # Чужой репозиторий и /tmp/cases — не наши дела (якорь на корне проекта).
        ("код в cases/ ЧУЖОГО репозитория пропускается",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "/tmp/chuzhoy-repo/cases/util.py", "content": "x"}}), 0),
        ("распаковка в /tmp/cases пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "tar -xf /tmp/mat.tar -C /tmp/cases"}}), 0),
        # ── Чужой CLI мимо коннектора (проба 20.08.2026) ── обе оси.
        # Имена берутся ИЗ РЕЕСТРА, а не пишутся здесь: подключение нового CLI
        # строкой реестра обязано сразу попадать под сторожа, без правки кода.
        ("прямой вызов чужого CLI из Bash блокируется",
         all(run({"tool_name": "Bash", "tool_input": {
             "command": f'{imya} exec "составь карту дела"'}}) == 2
             for imya in _foreign_cli_names()) if _foreign_cli_names() else True, True),
        ("чужой CLI после && — тоже блок (командная позиция)",
         all(run({"tool_name": "Bash", "tool_input": {
             "command": f"cd /tmp && {imya} -p 'x'"}}) == 2
             for imya in _foreign_cli_names()) if _foreign_cli_names() else True, True),
        ("наш claude -p пропускается (наш харнесс, не чужой CLI)",
         run({"tool_name": "Bash", "tool_input": {"command": "claude -p 'вопрос'"}}), 0),
        ("поиск по имени чужого CLI не блокируется — это чтение, не вызов",
         all(run({"tool_name": "Bash", "tool_input": {
             "command": f"grep -n {imya} scripts/cli_registry.json"}}) == 0
             for imya in _foreign_cli_names()) if _foreign_cli_names() else True, True),
        ("коннектор foreign_cli.py --role пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "python3 scripts/foreign_cli.py --role hunter-leaf --prompt v.txt"}}), 0),
        ("cli_router.py --role пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "python3 scripts/cli_router.py --role hunter-leaf --json"}}), 0),
        ("имя чужого CLI в прозе не блокирует",
         all(run({"tool_name": "Bash", "tool_input": {
             "command": f"echo 'реестр {imya} описан в scripts/cli_registry.json'"}}) == 0
             for imya in _foreign_cli_names()) if _foreign_cli_names() else True, True),
        # ── Регистр пути не снимает правил (APFS = тот же каталог, круг 4) ──────
        ("Write кода в CASES заглавными — блок",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "CASES/klient/delo-2026/gen.py", "content": "x"}}), 2),
        ("Write в 00_INTAKE заглавными — блок",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "cases/klient/delo-2026/00_INTAKE/scan.pdf", "content": "x"}}), 2),
        ("rm в 00_INTAKE заглавными — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "rm cases/klient/delo-2026/00_INTAKE/est.pdf"}}), 2),
        ("документ в gotovo строчными мимо маркеров — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cp /tmp/x.md cases/klient/delo-2026/gotovo/isk.md"}}), 2),
        ("каталог со словом Cases в имени — не наши дела, пропуск",
         run({"tool_name": "Write", "tool_input": {
             "file_path": "/tmp/DataCases/util.py", "content": "x"}}), 0),
        # ── Обёртки глагола: eval/here-string/$(which)/xargs/find -exec (круг 4) ──
        ("eval кладёт код в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "eval 'cp /tmp/x.py cases/klient/delo-2026/gen.py'"}}), 2),
        ("here-string кладёт код в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "bash <<< 'cp /tmp/x.py cases/klient/delo-2026/gen.py'"}}), 2),
        ("$(which cp) кладёт код в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "$(which cp) /tmp/x.py cases/klient/delo-2026/gen.py"}}), 2),
        ("xargs -I кладёт код в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "echo /tmp/x.py | xargs -I F cp F cases/klient/delo-2026/gen.py"}}), 2),
        ("find -exec кладёт код в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "find /tmp -name x.py -exec cp {} cases/klient/delo-2026/gen.py \\;"}}), 2),
        ("eval вне дела пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "eval 'cp /tmp/a /tmp/b'"}}), 0),
        ("xargs вне дела пропускается",
         run({"tool_name": "Bash", "tool_input": {
             "command": "echo /tmp/a | xargs -I F cp F /tmp/b"}}), 0),
        # ── Распаковка без флага каталога после ведущего cd (круг 4) ────────────
        ("tar без -C после cd в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cd cases/klient/delo-2026/00_intake && tar xf /tmp/a.tar"}}), 2),
        ("unzip без -d после cd в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cd cases/klient/delo-2026/00_intake && unzip /tmp/a.zip"}}), 2),
        ("python3 -m zipfile в дело — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "python3 -m zipfile -e /tmp/a.zip cases/klient/delo-2026/00_intake/"}}), 2),
        ("tar без флага в /tmp пропускается",
         run({"tool_name": "Bash", "tool_input": {"command": "cd /tmp && tar xf /tmp/a.tar"}}), 0),
        ("python3 -c с относительным путём после cd — блок",
         run({"tool_name": "Bash", "tool_input": {
             "command": "cd cases/klient/delo-2026 && python3 -c \"open('00_intake/est.pdf','w')\""}}), 2),
        # ── Имя чужого CLI в тексте команды — не вызов (круг 4) ─────────────────
        ("имя CLI в сообщении коммита — не вызов",
         all(run({"tool_name": "Bash", "tool_input": {
             "command": f'git commit -m "fix: убран прямой вызов; {imya} через коннектор"'}}) == 0
             for imya in _foreign_cli_names()) if _foreign_cli_names() else True, True),
        # ── Бюджет Read: обход симлинком снят, внешний большой файл читается (круг 4) ──
        ("большой файл вне проекта читается целиком",
         run({"tool_name": "Read", "tool_input": {"file_path": ext_big}}), 0),
        ("симлинк вне проекта на большой файл внутри — блок",
         run({"tool_name": "Read", "tool_input": {"file_path": ext_link}}) if ext_link else 2, 2),
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
