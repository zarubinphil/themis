#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pd_guard.py — фамилия доверителя не уходит в публичный репозиторий. Детерминированно.

ЗАЧЕМ. Инвариант «персональные данные не покидают cases/» держался только текстом
в конституции, а текст исполняется вероятностно. 04.08.2026 это и случилось:
документируя починку реестра, я записал имена двух папок дел в комментарии
scripts/registry_check.py и в тело сообщения коммита. **Имя папки дела — это
фамилия человека.** Коммит ушёл фоновым auto-sync в публичный репозиторий, и
правка файла историю уже не чистит.

ЧЕМ ЭТО ХУЖЕ ОБЫЧНОЙ ОПЕЧАТКИ. Фамилия + предмет дела («раздел имущества»,
«алименты», «банкротство») — это специальная категория сведений о частной жизни.
Опубликованный коммит индексируется и остаётся в форках и зеркалах даже после
удаления. Восстановить положение задним числом нельзя — можно только не допустить.

ЧТО ПРОВЕРЯЕТСЯ. Имена папок доверителей читаются С ДИСКА в момент запуска
(cases/*/), сам сторож их не хранит. Ищутся:
  • в содержимом файлов, попадающих в коммит;
  • в тексте сообщения коммита;
  • в путях добавляемых файлов.

Демо-дело (cases/ivanov-ivan) исключено намеренно: оно заведено как пример для
публичного репозитория и в .gitignore прописано белым списком.

УСТАНОВКА (делает install.sh, можно и руками):
    python3 scripts/pd_guard.py --install

ПРИМЕНЕНИЕ:
    python3 scripts/pd_guard.py --staged           # что уходит в коммит
    python3 scripts/pd_guard.py --msg FILE         # сообщение коммита
    python3 scripts/pd_guard.py --tree             # всё, что уже под контролем git
    python3 scripts/pd_guard.py --selftest

Код возврата: 0 — чисто; 1 — найдены персональные данные, коммит остановлен.
Сами найденные фамилии в вывод НЕ печатаются: сторож не должен становиться
вторым каналом утечки. Печатается файл, строка и длина совпадения.
"""
import argparse
import glob
import html
import io
import os
import re
import subprocess
import sys
import unicodedata
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "cases")
# Заведено как публичный пример, прописано белым списком в .gitignore.
DEMO = {"ivanov-ivan"}
# Служебные каталоги внутри cases/ — не доверители.
SERVICE_PREFIX = ("_", ".")
# Совсем короткие имена дают ложные срабатывания на обычных словах.
MIN_NAME = 5
_ZERO_SHA_RE = re.compile(r"^0{40,64}$")
_CYR_TO_LAT_CONFUSABLES = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
})
_LAT_TO_CYR_CONFUSABLES = str.maketrans({
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О",
    "P": "Р", "C": "С", "T": "Т", "Y": "У", "X": "Х",
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х",
})


def normalize_public_scan(text: str) -> str:
    """Для публичных каналов: NFKC, без невидимых знаков, mixed-script homograph."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    lat = "".join(re.escape(chr(ch)) for ch in _LAT_TO_CYR_CONFUSABLES)
    text = re.sub(
        rf"(?<=[А-Яа-яЁё])[{lat}]+|[{lat}]+(?=[А-Яа-яЁё])",
        lambda m: m.group(0).translate(_LAT_TO_CYR_CONFUSABLES),
        text,
    )
    cyr = "".join(re.escape(chr(ch)) for ch in _CYR_TO_LAT_CONFUSABLES)
    text = re.sub(
        rf"(?<=[A-Za-z])[{cyr}]+|[{cyr}]+(?=[A-Za-z])",
        lambda m: m.group(0).translate(_CYR_TO_LAT_CONFUSABLES),
        text,
    )
    return text


def client_names(cases_dir: str = CASES) -> list[str]:
    """Имена папок доверителей С ДИСКА. Сторож их не хранит и не печатает."""
    dirs = [cases_dir]
    if cases_dir == CASES:
        dirs += _worktree_cases_dirs()
    names: set[str] = set()
    for cdir in dirs:
        if not os.path.isdir(cdir):
            continue
        names.update(
            d for d in os.listdir(cdir)
            if os.path.isdir(os.path.join(cdir, d))
            and not d.startswith(SERVICE_PREFIX)
            and d not in DEMO
            and len(d) >= MIN_NAME)
    return sorted(names)


def _worktree_cases_dirs(root: str = ROOT) -> list[str]:
    """В роли `git worktree` папки дел обычно нет: cases/ игнорируется.

    Берём имена из основного дерева, не из git-индекса. Это держит один и тот же
    ПД-список в главном дереве и рабочих копиях ролей.
    """
    r = subprocess.run(["git", "worktree", "list", "--porcelain"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
            cdir = os.path.join(path, "cases")
            if cdir != CASES:
                out.append(cdir)
    return out


# Обратная транслитерация (латиница → кириллица), самые длинные сочетания первыми:
# фамилия папки пишется латиницей, но в прозе (сообщение коммита, комментарий)
# её могут написать кириллицей — «Тестфама» вместо «testfam-ab».
_LAT2CYR = (
    ("shch", "щ"), ("sch", "щ"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"),
    ("sh", "ш"), ("zh", "ж"), ("yu", "ю"), ("iu", "ю"), ("ya", "я"), ("ia", "я"),
    ("yo", "ё"), ("ye", "е"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"),
    ("z", "з"), ("i", "и"), ("y", "ы"), ("j", "й"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
    ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "к"), ("q", "к"),
    ("w", "в"), ("x", "кс"),
)


def _translit_to_cyrillic(latin: str) -> str:
    """Не точный ГОСТ — только чтобы поймать характерную часть фамилии в кириллице."""
    s = latin.lower()
    out, i = [], 0
    while i < len(s):
        for seq, cyr in _LAT2CYR:
            if s.startswith(seq, i):
                out.append(cyr)
                i += len(seq)
                break
        else:
            i += 1
    return "".join(out)


def _owner_latin_stems() -> set[str]:
    """Латинская фамилия ВЛАДЕЛЬЦА — из публичного адреса самого репозитория.

    Докстринг `_owner_stems` уже объявляет принцип: фамилия владельца это
    публичный бренд фирмы (README, титул, подпись документов), а не тайна
    доверителя. Но исключение было заведено только для кириллицы, и латинская
    форма продолжала красить дерево. Прецедент 21.08.2026: приветствие
    установщика подписано именем автора, и сторож остановил коммит — при том,
    что то же имя стоит в `git clone https://github.com/<handle>/…` двумя
    экранами ниже и опубликовано с первого дня.

    Источник — адрес `origin`, а не отдельный список: список разошёлся бы с
    действительностью, а адрес и есть та публичность, ради которой делается
    исключение. Снимается ТОЛЬКО голый стем: полное имя папки дела
    (`familiya-ab`) ловится по-прежнему, потому что фамилия плюс инициалы это
    уже конкретное дело конкретного человека.
    """
    r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True,
                       text=True, cwd=ROOT)
    url = (r.stdout or "").strip().lower()
    return {url} if url else set()


def _owner_stems() -> set[str]:
    """Кириллические стемы фамилии владельца из git config — его фамилия это
    публичный бренд фирмы (README, титул, подпись документов), не тайна доверителя.
    Латинское имя его папок из-под защиты НЕ выходит — исключение только для
    кириллической прозы."""
    r = subprocess.run(["git", "config", "user.name"], capture_output=True,
                       text=True, cwd=ROOT)
    stems = set()
    for word in (r.stdout or "").split():
        w = word.strip().lower()
        if len(w) >= 5:
            stems.add(w[:5])
    return stems


def name_pattern(names: list[str], cyrillic: bool = False,
                 owner_url_probe: str | None = None) -> re.Pattern | None:
    """Один шаблон на все имена. Границы — чтобы `ivan` не ловился внутри `ivanov`.

    Регистронезависим, разделители `-`/`_`/пробел взаимозаменяемы (04.08.2026 —
    `Testfam-Ab`/`TESTFAM-AB`/`testfam_ab` проходили мимо).

    Кириллическая транслитерация (cyrillic=True) включается ТОЛЬКО для сообщения
    коммита: там фамилию пишут по-русски («по делу Тестфама»). К содержимому
    файлов кириллические стемы не применяются — транслит-стемы неизбежно
    совпадают со словами языка («индикатор», «печатает» — 16 ложных тревог по
    дереву за прогон 19.08.2026), а сторож с ложной тревогой на обиходе не живёт."""
    if not names:
        return None
    owner = _owner_stems()
    owner_url = {owner_url_probe} if owner_url_probe is not None else _owner_latin_stems()
    lat_bodies, cyr_bodies = [], []
    for n in sorted(names, key=len, reverse=True):
        parts = [p for p in re.split(r"[-_ ]+", n) if p]
        if not parts:
            continue
        norm_parts = [normalize_public_scan(p) for p in parts]
        lat_bodies.append(r"[-_ ]".join(re.escape(p) for p in norm_parts))
        fam_norm = norm_parts[0]
        if len(fam_norm) >= MIN_NAME and not any(fam_norm in u for u in owner_url):
            lat_bodies.append(re.escape(fam_norm) + r"(?![-_][A-Za-zА-Яа-яЁё])")
        # Транслитерируется только ФАМИЛЬНАЯ часть (первая): вторая — имя или
        # инициалы, их кириллические стемы коротки и совпадают с обиходом.
        # Стем короче 5 букв в шаблон не идёт: «sud»→«суд» с хвостом [а-яё]{0,3}
        # ловил «суда», «судом», «судебн» — 97 ложных тревог по дереву за один
        # прогон (19.08.2026), а сторож с ложной тревогой на обиходе не живёт.
        fam = parts[0]
        if cyrillic and len(fam) >= MIN_NAME:
            cyr = _translit_to_cyrillic(fam)
            if len(cyr) >= 5 and cyr[:5] not in owner:
                cyr_bodies.append(re.escape(cyr) + r"[а-яё]{0,3}")
    body = "|".join(lat_bodies + cyr_bodies)
    if not body:
        return None
    # Граница — БУКВА, не «дефис/цифра». Имя папки дела в живой форме почти всегда
    # несёт хвост: «testfam-ab-2026.zip», «session-testfam-ab-19-08.md», «testfam-ab2».
    # Прежний класс держал дефис и цифру в границе, поэтому читал такой хвост
    # продолжением слова и пропускал имя целиком (проба 20.08.2026: три формы разом).
    # Буква слева/справа по-прежнему рвёт совпадение — «xfamiliya-abx» и
    # «familiya-abcd» продолжают молчать, иначе сторож краснел бы там, где имя лишь
    # кусок другого слова, и его выключили бы в первый день.
    return re.compile(rf"(?<![A-Za-zА-Яа-яЁё])({body})(?![A-Za-zА-Яа-яЁё])",
                      re.IGNORECASE)


def scan_text(text: str, pat: re.Pattern | None, where: str) -> list[str]:
    """Находки без раскрытия самой фамилии: файл, строка, длина совпадения."""
    if not pat or not text:
        return []
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in pat.finditer(normalize_public_scan(line)):
            out.append(f"{where}:{i} — имя папки доверителя ({len(m.group(1))} знаков). "
                       "Само значение не печатается: сторож не должен стать вторым "
                       "каналом утечки")
    return out


# Только категории со строгим форматом или явной меткой. pii_gate.residual_matches
# целиком (ФИО-эвристика, «cases/…», детские учреждения) написан для ДРУГОЙ
# задачи — обезличивания извлечённого текста дела перед отправкой наружу, где
# «слишком грубо» безопаснее «слишком мягко». Здесь сканируется код и документация
# ЭТОГО репозитория, где «cases/…» и упоминание суда — обиход через строку. Взятый
# сюда набор не пересекается с обиходом предметной области: паспорт/СНИЛС/кадастр/
# госномер/дата рождения не появляются в прозе о самом Фемиде НИКОГДА не как ПД.
_STRONG_PII_CATEGORIES = ("ПАСПОРТ", "СНИЛС", "КАДАСТР", "АВТОНОМЕР", "ДАТАРОЖД")
_STATIC_ASSET_EXT = (
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".css", ".map", ".woff", ".woff2", ".ttf", ".otf",
)
_PUBLIC_HYGIENE_PATTERNS = (
    ("абсолютный локальный путь", re.compile(
        r"(?<![A-Za-z0-9])(?:/Users|/home)/[^\s`\"'<>)]*"
    )),
    ("домашний путь проекта", re.compile(
        r"(?<![A-Za-z0-9])~/(?:Проекты|Projects|Мозг)\b[^\s`\"'<>)]*"
    )),
    ("прямой путь секрета", re.compile(
        r"(?<![A-Za-z0-9])~/\.secrets\b[^\s`\"'<>)]*"
    )),
)


def _skip_strong_pii_scan(where: str) -> bool:
    """Второй рубеж ищет реквизиты в человекочитаемом тексте, не в ассетах.

    Имя папки доверителя по-прежнему ловится в любом пути/файле через scan_text().
    Здесь отключаются только структурные ПД-шаблоны, которые на SVG-координатах
    и публичном демо-кейсе дают шум вместо защиты.
    """
    path = where.replace("\\", "/")
    if any(path.startswith(f"cases/{demo}/") for demo in DEMO):
        return True
    if path in {"knowledge/kadry/_articles.json", "knowledge/kadry/_templates.sha256"}:
        return True
    return path.lower().endswith(_STATIC_ASSET_EXT)


def scan_pii(text: str, where: str) -> list[str]:
    """Второй рубеж на пути коммита: паспорт/СНИЛС/кадастр/госномер/дата рождения
    без метки папки дела. pii_gate живёт своей жизнью (стадии 6/7), здесь его
    структурные шаблоны читаются как модуль этого же каталога — не второй канал."""
    if not text:
        return []
    if _skip_strong_pii_scan(where):
        return []
    try:
        import pii_gate
    except ImportError:
        return []
    try:
        cats = dict(pii_gate.CATEGORIES_STATIC)
        raw = [(m.start(), cat) for cat in _STRONG_PII_CATEGORIES
               for pat in cats.get(cat, ()) for m in pat.finditer(text)]
    except Exception:
        return []
    out = []
    for start, cat in raw:
        line_no = text.count("\n", 0, start) + 1
        out.append(f"{where}:{line_no} — похоже на персональные данные ({cat}), "
                   "не имя папки дела. Само значение не печатается")
    return out


def scan_public_hygiene(text: str, where: str) -> list[str]:
    """Публичный репозиторий не должен хранить машинные пути владельца.

    Значение не печатаем по той же причине, что и фамилии: сторож не становится
    вторым каналом утечки. Внутри используем только строгие формы, без догадок по
    обычным словам документации.
    """
    if not text:
        return []
    out = []
    normalized = normalize_public_scan(text)
    for i, line in enumerate(normalized.splitlines(), 1):
        for label, pat in _PUBLIC_HYGIENE_PATTERNS:
            for m in pat.finditer(line):
                out.append(f"{where}:{i} — публикационная утечка ({label}, "
                           f"{len(m.group(0))} знаков). Само значение не печатается")
    return out


def git(*args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                       capture_output=True, cwd=ROOT)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def git_bytes(*args: str) -> bytes:
    """Сырой блоб без декодирования: .docx это zip, декодировать его в utf-8
    значит потерять содержимое до проверки."""
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                       capture_output=True, cwd=ROOT)
    return r.stdout if r.returncode == 0 else b""


def git_z(*args: str) -> list[str]:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                       capture_output=True, cwd=ROOT)
    if r.returncode != 0:
        return []
    return [p.decode("utf-8", "replace") for p in r.stdout.split(b"\0") if p]


def git_blob(spec: str) -> bytes | None:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "cat-file", "blob", spec],
                       capture_output=True, cwd=ROOT)
    return r.stdout if r.returncode == 0 else None


# Office/ODT-форматы — это zip с XML внутри, а не двоичная непрозрачность. Судебные
# документы именно .docx: фамилия доверителя лежит в word/document.xml, и без
# распаковки сторож объявляет такой коммит чистым (ложная уверенность хуже молчания).
OFFICE_ZIP_EXT = (".docx", ".xlsx", ".pptx", ".odt")
_ZIP_TEXT_EXT = (
    ".xml", ".rels", ".txt", ".md", ".html", ".htm", ".xhtml", ".csv", ".json",
    ".opf", ".ncx",
)


def _office_xml_text(blob: bytes, depth: int = 0) -> str:
    """Видимый текст из zip-контейнера: решаем по содержимому, не по расширению."""
    if depth > 2 or len(blob) > 20 * 1024 * 1024:
        return ""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        return ""
    parts = []
    for name in zf.namelist():
        low = name.lower()
        if name.endswith("/") or "__macosx/" in low:
            continue
        try:
            info = zf.getinfo(name)
            if info.file_size > 5 * 1024 * 1024:
                continue
            data = zf.read(name)
        except (OSError, KeyError, RuntimeError):
            continue
        if data.startswith(b"PK\x03\x04"):
            nested = _office_xml_text(data, depth + 1)
            if nested:
                parts.append(nested)
            continue
        if not low.endswith(_ZIP_TEXT_EXT) and b"\0" in data[:512]:
            continue
        raw = data.decode("utf-8", "replace")
        raw = re.sub(r"</(?:\w+:)?p>", "\n", raw)
        raw = re.sub(r"</(?:\w+:)?tc>", "\t", raw)
        raw = re.sub(r"</(?:p|div|br|li|tr|td|h[1-6])\b[^>]*>", "\n", raw,
                     flags=re.I)
        parts.append(html.unescape(re.sub(r"<[^>]+>", "", raw)))
    return "\n".join(parts)


def _pdf_text(blob: bytes) -> str:
    """Минимальный локальный слой для PDF без зависимостей: видимые ASCII/UTF-8 куски."""
    text = blob.decode("utf-8", "ignore")
    if not text.strip():
        text = blob.decode("latin1", "ignore")
    strings = re.findall(r"\(([^()]{1,500})\)", text)
    if strings:
        text += "\n" + "\n".join(strings)
    return re.sub(r"[^A-Za-zА-Яа-яЁё0-9_.:/@+№()\"'«»\-\s]", " ", text)


def _visible_blob_text(path: str, blob: bytes) -> str:
    zipped = _office_xml_text(blob)
    if zipped:
        return zipped
    low = path.lower()
    if low.endswith(".pdf"):
        return _pdf_text(blob)
    return blob.decode("utf-8", "replace")


def _scan_file_text(path: str, blob: bytes, pat: re.Pattern | None) -> list[str]:
    text = _visible_blob_text(path, blob)
    if not text:
        return []
    hits = scan_text(text, pat, path)
    hits += scan_public_hygiene(text, path)
    if not _is_test_fixture_code(path):
        hits += scan_pii(text, path)
    return hits


def staged_files() -> list[str]:
    return git_z("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT")


def _is_test_fixture_code(path: str) -> bool:
    """`scripts/*.py` — валидаторы реквизитов (ИНН/СНИЛС/паспорт…), их `--selftest`
    штатно несёт СИНТЕТИЧЕСКИЕ примеры нужной формы («СНИЛС 123-456-789 64») —
    именно такими фикстурами код и проверяют. Реальная утечка ПД идёт другим
    каналом (имя папки дела в прозе/пути — `name_pattern` его ловит независимо
    от расширения файла); `scripts/` клиентских данных не несёт по правилам
    проекта. Найдено прогоном 19.08.2026: pii_gate.py и markdown_extract.py —
    оба заведомо чистый код — красили коммит по СВОИМ ЖЕ тестовым фикстурам."""
    return path.startswith("scripts/") and path.endswith(".py")


def check_staged(pat: re.Pattern | None) -> list[str]:
    problems = []
    for f in staged_files():
        problems += scan_text(f, pat, "путь файла")
        problems += scan_public_hygiene(f, "путь файла")
        blob = git_blob(f":{f}")
        if blob is None:
            problems.append(f"{f}:0 — файл есть в индексе, но blob не прочитан. "
                            "Fail-closed: проверка не может считать его чистым")
            continue
        hits = _scan_file_text(f, blob, pat)
        problems += hits
    return problems


def check_ref_txn(pat: re.Pattern | None, state: str, stdin: str | None = None) -> list[str]:
    """reference-transaction: тело и имя аннотированного тега — публичная ссылка.

    Тег создаётся ЛОКАЛЬНО и уезжает при push, а git-хука на создание тега нет.
    Но reference-transaction срабатывает на обновлении refs/tags/* и в фазе
    `prepared` его можно отменить ненулевым кодом. Обновления refs/heads/* (обычный
    коммит) не трогаем — их держат pre-commit/commit-msg; иначе сторож заблокирует
    всякую работу с ветками."""
    if state != "prepared":
        return []
    text = sys.stdin.read() if stdin is None else stdin
    problems = []
    for line in text.splitlines():
        row = line.split()
        if len(row) < 3:
            continue
        new, ref = row[1], row[2]
        if not ref.startswith("refs/tags/"):
            continue
        problems += scan_text(ref[len("refs/tags/"):], pat, "имя тега")
        problems += scan_public_hygiene(ref[len("refs/tags/"):], "имя тега")
        if git("cat-file", "-t", new).strip() == "tag":   # тело только у аннотированного
            tag_body = git("cat-file", "-p", new)
            problems += scan_text(tag_body, pat, "тело тега")
            problems += scan_public_hygiene(tag_body, "тело тега")
    return problems


def check_push_refs(pat: re.Pattern | None, stdin: str | None = None) -> list[str]:
    """pre-push канал: имя ветки/тега тоже публичная ссылка."""
    text = sys.stdin.read() if stdin is None else stdin
    problems = []
    rows = [line.split() for line in text.splitlines() if line.strip()]
    refs = []
    for row in rows:
        if row:
            refs.append(row[0])
        if len(row) >= 3:
            refs.append(row[2])
    if not refs:
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD").strip()
        refs.extend([branch] if branch else [])
        refs.extend(x for x in git("tag", "--points-at", "HEAD").splitlines() if x)
    for ref in refs:
        problems += scan_text(ref, pat, "имя ветки/тега")
        problems += scan_public_hygiene(ref, "имя ветки/тега")
    for row in rows:
        if len(row) < 4:
            continue
        local_sha, remote_sha = row[1], row[3]
        if _ZERO_SHA_RE.match(local_sha):
            continue
        problems += check_push_content(pat, local_sha, remote_sha)
    return problems


def _commit_for_object(obj: str) -> str:
    return git("rev-parse", "--verify", f"{obj}^{{commit}}").strip()


def _push_files(local_sha: str, remote_sha: str) -> list[str]:
    commit = _commit_for_object(local_sha)
    if not commit:
        return []
    if remote_sha and not _ZERO_SHA_RE.match(remote_sha):
        base = _commit_for_object(remote_sha)
        if base:
            return git_z("diff", "--name-only", "-z", f"{base}..{commit}")
    return git_z("ls-tree", "-r", "-z", "--name-only", commit)


def check_push_content(pat: re.Pattern | None, local_sha: str, remote_sha: str) -> list[str]:
    """pre-push держит утечки, попавшие в ветку мимо локальных commit hooks."""
    commit = _commit_for_object(local_sha)
    if not commit:
        return []
    problems = []
    meta_pat = name_pattern(client_names(), cyrillic=True) or pat
    meta = git("cat-file", "-p", commit)
    problems += scan_text(meta, meta_pat, "метаданные коммита")
    problems += scan_public_hygiene(meta, "метаданные коммита")
    for f in _push_files(local_sha, remote_sha):
        problems += scan_text(f, pat, "путь файла")
        problems += scan_public_hygiene(f, "путь файла")
        blob = git_blob(f"{commit}:{f}")
        if blob is None:
            problems.append(f"{f}:0 — blob уходящего коммита не прочитан. "
                            "Fail-closed: pre-push не считает его чистым")
        else:
            problems += _scan_file_text(f, blob, pat)
    return problems


def check_tree(pat: re.Pattern | None) -> list[str]:
    problems = []
    for f in git_z("ls-files", "-z"):
        path = os.path.join(ROOT, f)
        if not os.path.isfile(path):
            continue
        problems += scan_text(f, pat, "путь файла")
        problems += scan_public_hygiene(f, "путь файла")
        try:
            with open(path, "rb") as fh:
                problems += _scan_file_text(f, fh.read(), pat)
        except OSError:
            continue
    return problems


def local_log_files(root: str = ROOT) -> list[str]:
    """Рабочие логи, где имена дел лежат законно: корневые *.log и всё под cases/_logs/."""
    out = list(glob.glob(os.path.join(root, "*.log")))
    out += [p for p in glob.glob(os.path.join(root, "cases", "_logs", "**", "*"), recursive=True)
            if os.path.isfile(p)]
    return sorted(set(out))


def check_local_logs(root: str = ROOT) -> list[str]:
    """Рабочие логи ОБЯЗАНЫ оставаться вне git.

    В `audit.log` и `cases/_logs/` имя дела пишется по делу — это работа, вычищать нечего.
    Опасность другая: правило `.gitignore` сломали или файл добавили `git add -f`, и вся
    история прогонов уезжает в публичный репозиторий разом. Сторож проверяет не текст,
    а статус: игнорируется и не отслеживается.
    """
    problems = []
    for path in local_log_files(root):
        rel = os.path.relpath(path, root)
        if subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=root).returncode != 0:
            problems.append(f"{rel} — рабочий лог НЕ покрыт .gitignore")
        tracked = subprocess.run(["git", "ls-files", "--", rel], cwd=root,
                                 capture_output=True, text=True).stdout.strip()
        if tracked:
            problems.append(f"{rel} — рабочий лог ОТСЛЕЖИВАЕТСЯ git")
    return problems


HOOK = """#!/bin/sh
# Поставлен scripts/pd_guard.py --install. Фамилия доверителя не уходит наружу.
exec python3 "$(git rev-parse --show-toplevel)/scripts/pd_guard.py" %s
"""


def install() -> int:
    common_dir = git("rev-parse", "--git-common-dir").strip()
    if not common_dir:
        print("не удалось найти git common dir", file=sys.stderr)
        return 1
    common_dir = common_dir if os.path.isabs(common_dir) else os.path.join(ROOT, common_dir)
    hooks = os.path.join(os.path.abspath(common_dir), "hooks")
    configured = subprocess.run(
        ["git", "config", "--local", "core.hooksPath", hooks],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if configured.returncode != 0:
        print(f"не удалось включить локальные git-хуки: {configured.stderr.strip()}", file=sys.stderr)
        return 1
    os.makedirs(hooks, exist_ok=True)
    for name, arg in (("pre-commit", "--staged"),
                      ("commit-msg", '--msg "$1"'),
                      ("pre-push", "--push"),
                      ("reference-transaction", '--ref-txn "$1"')):
        path = os.path.join(hooks, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(HOOK % arg)
        os.chmod(path, 0o755)
        print(f"поставлен {path}")
    print("Теперь ни коммит, ни тег с фамилией доверителя не пройдёт наружу.")
    return 0


def report(problems: list[str], what: str) -> int:
    if not problems:
        print(f"✓ ПД-сторож: {what} — чисто")
        return 0
    print(f"\n⛔ ПЕРСОНАЛЬНЫЕ ДАННЫЕ В {what.upper()}: находок {len(problems)}",
          file=sys.stderr)
    for p in problems[:20]:
        print(f"   • {p}", file=sys.stderr)
    if len(problems) > 20:
        print(f"   … и ещё {len(problems) - 20}", file=sys.stderr)
    print("\nИмя папки дела — это фамилия человека. Репозиторий публичный, а "
          "опубликованный коммит остаётся в форках и зеркалах после удаления.\n"
          "Что делать: описать прецедент обезличенно (что произошло, а не с кем), "
          "в фикстурах использовать вымышленные фамилии.\n"
          "Обойти осознанно (демо-дело, ложное совпадение): PD_GUARD=0 git commit …",
          file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Сторож персональных данных")
    ap.add_argument("--staged", action="store_true", help="проверить индекс коммита")
    ap.add_argument("--msg", metavar="FILE", help="проверить сообщение коммита")
    ap.add_argument("--tree", action="store_true", help="проверить всё дерево git")
    ap.add_argument("--local-logs", action="store_true",
                    help="рабочие логи (audit.log, cases/_logs/) вне git")
    ap.add_argument("--push", action="store_true", help="проверить имена веток/тегов pre-push")
    ap.add_argument("--ref-txn", metavar="STATE",
                    help="reference-transaction: тело/имя тега (фаза prepared)")
    ap.add_argument("--install", action="store_true", help="поставить git-хуки")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.install:
        return install()
    if os.environ.get("PD_GUARD") == "0":
        print("ПД-сторож выключен переменной PD_GUARD=0 — под ответственность автора коммита",
              file=sys.stderr)
        return 0

    pat = name_pattern(client_names())
    if a.msg:
        pat = name_pattern(client_names(), cyrillic=True)
        try:
            text = open(a.msg, encoding="utf-8", errors="ignore").read()
        except OSError as e:
            print(f"сообщение коммита не прочитано ({e})", file=sys.stderr)
            return 0
        return report(scan_text(text, pat, "сообщение коммита") +
                      scan_public_hygiene(text, "сообщение коммита"),
                      "сообщении коммита")
    if a.local_logs:
        return report(check_local_logs(), "рабочих логах")
    if a.push:
        return report(check_push_refs(pat), "имени ветки или тега")
    if a.ref_txn:
        # Тело тега — проза (пишут по-русски), имя — латиница; кириллический
        # шаблон покрывает и то и другое.
        pat = name_pattern(client_names(), cyrillic=True)
        return report(check_ref_txn(pat, a.ref_txn), "теле или имени тега")
    if a.tree:
        return report(check_tree(pat), "дереве git")
    if a.staged:
        return report(check_staged(pat), "коммите")
    ap.print_help()
    return 2


def _local_logs_probe(force_add: bool = False) -> int:
    """Синтетический репозиторий: рабочий лог игнорируется, а насильно добавленный — ловится."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pdguard-logs-") as tmp:
        os.makedirs(os.path.join(tmp, "cases", "_logs"))
        with open(os.path.join(tmp, "audit.log"), "w", encoding="utf-8") as f:
            f.write("прогон по делу\n")
        with open(os.path.join(tmp, "cases", "_logs", "session_18-08-2026.md"), "w",
                  encoding="utf-8") as f:
            f.write("разбор\n")
        with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*.log\ncases/_logs/\n")
        for cmd in (["init", "-q"], ["add", ".gitignore"]):
            subprocess.run(["git", *cmd], cwd=tmp, capture_output=True)
        if force_add:
            subprocess.run(["git", "add", "-f", "audit.log"], cwd=tmp, capture_output=True)
        return len(check_local_logs(tmp))


def selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()
    cases = os.path.join(tmp, "cases")
    for d in ("familiya-ab", "drugoy-vg", "ivanov-ivan", "_templates", "ab"):
        os.makedirs(os.path.join(cases, d))
    names = client_names(cases)
    pat = name_pattern(names)
    pat_msg = name_pattern(names, cyrillic=True)
    abs_path = "/" + "Users/test/" + "Проекты/themis"
    home_projects = "~/" + "Проекты/themis"
    secret_path = "~/" + ".secrets/themis.env"
    mixed_paths = "/" + "Users/test/x " + "~/" + ".secrets/a"

    def _docx_bytes(body: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml",
                        "<w:document><w:body><w:p><w:r><w:t>"
                        f"{body}</w:t></w:r></w:p></w:body></w:document>")
        return buf.getvalue()

    def _odt_bytes(body: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("content.xml", f"<office:text><text:p>{body}</text:p></office:text>")
        return buf.getvalue()

    dirty_docx = _office_xml_text(_docx_bytes("По делу familiya-ab прошу"))
    clean_docx = _office_xml_text(_docx_bytes("Ходатайство об истребовании"))

    def _prepush_content_probe() -> int:
        global ROOT
        old_root = ROOT
        try:
            with tempfile.TemporaryDirectory(prefix="pdguard-push-") as repo:
                ROOT = repo
                for cmd in (["init", "-q"],
                            ["-c", "user.email=t@t", "-c", "user.name=t",
                             "commit", "--allow-empty", "-qm", "base"]):
                    subprocess.run(["git", *cmd], cwd=repo, capture_output=True)
                base = git("rev-parse", "HEAD").strip()
                with open(os.path.join(repo, "leak.md"), "w", encoding="utf-8") as f:
                    f.write("familiya\n")
                subprocess.run(["git", "add", "leak.md"], cwd=repo, capture_output=True)
                subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-qm", "leak"], cwd=repo, capture_output=True)
                head = git("rev-parse", "HEAD").strip()
                return len(check_push_refs(pat, f"refs/heads/main {head} "
                                           f"refs/heads/main {base}\n"))
        finally:
            ROOT = old_root

    checks = [
        # Кириллица: пара «утечка в сообщении коммита + обиход в содержимом».
        ("кириллическая фамилия в сообщении коммита ловится",
         len(scan_text("fix: возражения по делу Фамилияна", pat_msg, "msg")) >= 1),
        ("кириллический стем НЕ применяется к содержимому файлов",
         scan_text("возражения по делу Фамилияна", pat, "f.py") == []),
        ("обиход в сообщении коммита молчит",
         scan_text("docs: Постановление и Апелляционное определение разобраны",
                   pat_msg, "msg") == []),
        ("регистр и разделитель нормализованы",
         all(len(scan_text(v, pat, "f")) == 1
             for v in ("Familiya-Ab", "FAMILIYA-AB", "familiya_ab", "familiya ab"))),
        ("имена доверителей прочитаны с диска", set(names) == {"familiya-ab", "drugoy-vg"}),
        # Демо-дело заведено как публичный пример — оно не ПД.
        ("демо-дело исключено", "ivanov-ivan" not in names),
        ("служебная папка не считается доверителем", "_templates" not in names),
        ("слишком короткое имя не берётся", "ab" not in names),
        # Ровно тот случай, который и произошёл 04.08.2026.
        ("фамилия в комментарии кода ловится",
         len(scan_text("# прецедент: familiya-ab и drugoy-vg", pat, "f.py")) == 2),
        ("фамилия в сообщении коммита ловится",
         len(scan_text("fix: развёл familiya-ab и её двойника", pat, "msg")) == 1),
        ("фамилия в пути файла ловится",
         len(scan_text("cases/familiya-ab/delo-2026/x.md", pat, "путь")) == 1),
        ("фамильная часть имени папки ловится отдельно",
         len(scan_text("leak: familiya без суффикса", pat, "f")) == 1),
        ("невидимый разрыв в фамилии не снимает сторож",
         len(scan_text("fami\u200bliya", pat, "f")) == 1),
        ("гомоглиф в фамилии не снимает сторож",
         len(scan_text("familiy\u0430", pat, "f")) == 1),
        ("фамилия в имени ветки ловится pre-push",
         len(check_push_refs(pat, "refs/heads/autoloop/familiya-ab abc "
                             "refs/heads/autoloop/familiya-ab abc\n")) == 2),
        ("pre-push смотрит содержимое уходящих коммитов",
         _prepush_content_probe() >= 1),
        ("обычное имя ветки проходит pre-push",
         check_push_refs(pat, "refs/heads/fix-guard abc refs/heads/fix-guard abc\n") == []),
        ("чистый текст проходит", scan_text("обычный комментарий про реестр", pat, "f") == []),
        # Границы: имя не должно ловиться внутри другого слова, иначе сторож
        # начнёт краснеть на ровном месте и его выключат.
        ("имя внутри длинного слова не ловится",
         scan_text("xfamiliya-abx", pat, "f") == []),
        ("имя с дефисом внутри длинного не ловится",
         scan_text("familiya-abcd", pat, "f") == []),
        ("имя в кавычках ловится", len(scan_text('"familiya-ab"', pat, "f")) == 1),
        ("имя в конце строки ловится", len(scan_text("папка familiya-ab", pat, "f")) == 1),
        # Сторож не печатает саму фамилию — иначе он второй канал утечки.
        ("находка не раскрывает фамилию",
         all("familiya-ab" not in p for p in scan_text("familiya-ab", pat, "f"))),
        ("находка называет файл и строку",
         "f.py:1" in scan_text("familiya-ab", pat, "f.py")[0]),
        # Рабочие логи: сторож смотрит не на текст, а на статус в git.
        ("рабочий лог под .gitignore проходит", _local_logs_probe() == 0),
        ("рабочий лог, добавленный в git, ловится", _local_logs_probe(force_add=True) > 0),
        ("пустой список имён никого не ловит",
         scan_text("familiya-ab", name_pattern([]), "f") == []),
        ("абсолютный путь владельца ловится публикационным сторожем",
         len(scan_public_hygiene(abs_path, "f")) == 1),
        ("домашний путь проекта ловится публикационным сторожем",
         len(scan_public_hygiene(home_projects, "f")) == 1),
        ("прямой путь секрета ловится публикационным сторожем",
         len(scan_public_hygiene(secret_path, "f")) == 1),
        ("публикационный сторож не печатает сам путь",
         all(abs_path.rsplit("/", 1)[0] not in p and secret_path.split("/", 1)[0] not in p
             for p in scan_public_hygiene(mixed_paths, "f"))),
        ("отчёт по находкам даёт код 1", report(["x"], "тесте") == 1),
        ("отчёт без находок даёт код 0", report([], "тесте") == 0),
        # Пара «утечка + обиход» для scan_pii (найдено прогоном 19.08.2026:
        # pii_gate.py/markdown_extract.py красили СВОИМИ ЖЕ тестовыми фикстурами
        # СНИЛС-формы «123-456-789 64» — валидатор не должен ловить собственные
        # примеры формата, но обязан по-прежнему ловить тот же литерал в прозе.
        ("scripts/*.py опознаётся как тестовый код",
         _is_test_fixture_code("scripts/pii_gate.py")),
        ("cases/…/x.py тестовым кодом НЕ считается — не тот канал",
         not _is_test_fixture_code("cases/klient/delo/x.py")),
        ("knowledge/x.md тестовым кодом не считается",
         not _is_test_fixture_code("knowledge/x.md")),
        ("тот же литерал в .md по-прежнему ловится scan_pii (утечка не потеряна)",
         len(scan_pii("СНИЛС 123-456-789 64", "note.md")) >= 1),
        # .docx это zip: фамилия внутри word/document.xml видна, чистый — молчит.
        ("фамилия внутри .docx (zip) распакована и поймана",
         len(scan_text(dirty_docx, pat, "hod.docx")) >= 1),
        ("чистый .docx без имён проходит", scan_text(clean_docx, pat, "hod.docx") == []),
        ("не-zip под видом .docx не роняет сторож", _office_xml_text(b"not a zip") == ""),
        ("фамилия внутри .odt распакована и поймана",
         len(_scan_file_text("z.odt", _odt_bytes("familiya"), pat)) >= 1),
        ("фамилия внутри простого .pdf поймана",
         len(_scan_file_text("z.pdf", b"%PDF-1.4 (familiya)", pat)) >= 1),
        # reference-transaction: тело/имя тега судится в фазе prepared.
        ("тело аннотированного тега с фамилией ловится",
         len(scan_text("релиз по делу familiya-ab", pat_msg, "тело тега")) >= 1),
        ("имя тега с фамилией ловится в prepared",
         len(check_ref_txn(pat, "prepared",
                           "0 0 refs/tags/familiya-ab\n")) >= 1),
        ("фаза committed тег не судит (отмена уже невозможна)",
         check_ref_txn(pat, "committed", "0 0 refs/tags/familiya-ab\n") == []),
        ("обновление ветки reference-transaction не трогает",
         check_ref_txn(pat, "prepared", "0 0 refs/heads/familiya-ab\n") == []),
        ("чистый тег в prepared проходит",
         check_ref_txn(pat, "prepared", "0 0 refs/tags/v2.0\n") == []),
        # Фамилия ВЛАДЕЛЬЦА в латинице — публичный бренд (она в адресе
        # репозитория и в README), голый стем под замок не идёт. Но полное имя
        # папки дела с ней ловится по-прежнему: фамилия плюс инициалы — это уже
        # конкретное дело конкретного человека.
        ("голая фамилия владельца из адреса репозитория не красит",
         scan_text("Собрал Familiya, практикующий юрист",
                   name_pattern(names, owner_url_probe="github.com/familiyaphil/x"),
                   "проба") == []),
        ("полное имя дела владельца ловится и при снятом стеме",
         scan_text("cases/familiya-ab/delo-2026",
                   name_pattern(names, owner_url_probe="github.com/familiyaphil/x"),
                   "проба") != []),
        ("фамилия ПОСТОРОННЕГО доверителя ловится и голой",
         scan_text("файл drugoy.md",
                   name_pattern(names, owner_url_probe="github.com/familiyaphil/x"),
                   "проба") != []),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
