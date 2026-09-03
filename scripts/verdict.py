#!/usr/bin/env python3
"""verdict.py — вердикт Кони, привязанный к редакции. Этап 3 плана FINAL-PLAN-2026-08-18.

Раньше вердикт был словом в чате и строкой в `review_log.md`. Слово не привязано ни к
чему: одобрили редакцию r2, дописали абзац, собрали `.docx` — и в суд ушел текст,
которого Кони не видел. Вердикт обязан содержать идентификатор документа, номер
редакции и SHA-256 самого `.md`.

Здесь же гейт humanizer-legal — вынесен из `DocBuilder.save()`. На собранном `.docx`
он срабатывал один раз и слишком поздно; прогон по `.md` идет КАЖДЫЙ раунд, до того
как текст стал документом.

    --preflight ФАЙЛ.md                  три машинных гейта + запись по SHA-256
    --scan   ФАЙЛ.md                     проверка humanizer-legal (каждый раунд)
    --record ФАЙЛ.md --verdict "…" --source КТО [-r N]     записать вердикт
    --check  ФАЙЛ.md                     можно ли собирать .docx из этой редакции
    --log    ФАЙЛ.md                     история вердиктов документа

Журнал — `.agent/drafts/verdicts.jsonl` (адрес из case_paths, ОДИН на систему),
append-only. Раньше он лежал в `_working/` — а эту папку сторож (claude_guard)
освобождает от гейта протокола, и журнал был слепым пятном собственного сторожа
(D03, проба 01.09.2026: дописанная строка с верным sha256 открыла сборку .docx без
единого вызова рецензента). Старый адрес читается как запасной, с предупреждением.

Целостность записи держат два рубежа помимо переезда:
  · обязательный источник (`source`) — атрибуция; self-record (сам составитель)
    отклоняется, но строка не заменяет аутентификацию ОС-процесса;
  · внеполосная подпись HMAC-SHA256 — ключ вне журнала (env `THEMIS_VERDICT_KEY`
    либо файл ключа в каталоге секретов), проверка при чтении. Запись без
    проверяемой подписи для сборки НЕ считается вердиктом — отказ, не предупреждение.

Выход: 0 — можно; 1 — нельзя (причина на stdout); 2 — вызов неверен.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _obshee as obs  # noqa: E402
import case_paths as cp  # noqa: E402
from create_docx import DocBuilder, find_scan_legal  # noqa: E402

try:
    import money_rule as _mr  # noqa: E402  единый денежный разбор проекта
except ImportError:
    _mr = None

READY = "ГОТОВ К ПОДАЧЕ"
READY_VERDICTS = frozenset({
    "ГОТОВ К ПОДАЧЕ",
    "ПРОВЕРЕНО БЕЗ КОНИ (ГОТОВ К ПОДАЧЕ)",
})

# Закрытый словарь вердиктов — ОДИН объект уровня модуля. create_docx.py спрашивает
# его, а не держит свою копию: 25.08 два прибора разошлись в словаре и это стоило круга.
VERDICTS = frozenset({
    "ГОТОВ К ПОДАЧЕ",
    "ТРЕБУЕТ ПРАВОК",
    "КРИТИЧЕСКИЕ ОШИБКИ",
    "ПРОВЕРЕНО ЧАСТИЧНО",
    "ПРОВЕРЕНО БЕЗ КОНИ (ГОТОВ К ПОДАЧЕ)",
    "ПРОВЕРЕНО БЕЗ КОНИ (ПРОВЕРЕНО ЧАСТИЧНО)",
    "ПРОВЕРЕНО БЕЗ КОНИ (ТРЕБУЕТ ПРАВОК)",
    "ПРОВЕРЕНО БЕЗ КОНИ (КРИТИЧЕСКИЕ ОШИБКИ)",
})
ROUND_CONFIG = Path(__file__).resolve().parent.parent / "config" / "verdict.json"


class RoundLimitExceeded(Exception):
    """Раунд у потолка case_paths — стоп с эскалацией владельцу."""


class RoundConfigError(Exception):
    """Конфиг лимита не позволяет безопасно определить активный потолок."""


def round_limit(today=None):
    """Потолок из case_paths; конфиг хранит только временное послабление."""
    path = Path(os.environ.get("THEMIS_VERDICT_CONFIG", ROUND_CONFIG))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RoundConfigError(f"конфиг лимита раундов {path} не прочитан: {e}") from e
    base = cp.REVIEW_STOP_ROUND
    legacy_base = data.get("round_limit")
    if legacy_base is not None and legacy_base != base:
        raise RoundConfigError(f"{path}: базовый round_limit задает scripts/case_paths.py")
    override = data.get("override")
    if override is None:
        return base
    if not isinstance(override, dict):
        raise RoundConfigError(f"{path}: override должен быть объектом либо null")
    value, reason, expires = (override.get("round_limit"),
                              str(override.get("reason") or "").strip(),
                              str(override.get("expires") or "").strip())
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise RoundConfigError(f"{path}: override.round_limit должен быть целым числом не меньше 2")
    if not reason:
        raise RoundConfigError(f"{path}: послабление не принято — обязательна причина")
    if not expires:
        raise RoundConfigError(f"{path}: послабление не принято — обязательна дата истечения")
    try:
        expiry = datetime.strptime(expires, "%d.%m.%Y").date()
    except ValueError as e:
        raise RoundConfigError(f"{path}: expires нужна в формате ДД.ММ.ГГГГ") from e
    current = today or date.today()
    if expiry < current:
        print(f"⚠ послабление лимита истекло {expires}; действует базовый лимит {base}",
              file=sys.stderr)
        return base
    return value


# Источник вердикта, который сам составляет документы: его вердикт себе не считается.
# doc-drafter=«Сперанский» генерирует, doc-reviewer=«Кони» судит. Сверка casefold.
SELF_SOURCES = frozenset({
    "doc-drafter", "сперанский", "drafter", "составитель", "генератор",
})
REVIEW_SOURCES = frozenset({
    "doc-reviewer", "кони", "reviewer", "coordinator", "координатор",
})
# Источник нельзя молча назначать рецензентом: отсутствие атрибуции — отказ.
DEFAULT_SOURCE = os.environ.get("THEMIS_ACTOR")
# Поля, которые подписываются. `sig` в подпись не входит (это она сама).
SIGNED_FIELDS = ("document", "path", "round", "verdict", "sha256", "at", "source")
PREFLIGHT_SIGNED_FIELDS = (
    "kind", "document", "path", "sha256", "context_sha256", "at", "source", "green", "checks",
)
_KEYFILE_DEFAULT = Path.home() / ".secrets" / "themis-verdict.key"


def _sign_key():
    """Ключ подписи ВНЕ журнала: env THEMIS_VERDICT_KEY либо файл ключа.
    Файла нет — генерируем один раз (каталог 700, файл 600). Секрет в git/логи не
    уходит — только в каталог секретов (санкция rules/structure.md)."""
    k = os.environ.get("THEMIS_VERDICT_KEY")
    if k:
        return k.encode()
    kf = Path(os.environ.get("THEMIS_VERDICT_KEYFILE", _KEYFILE_DEFAULT))
    if kf.is_file():
        return kf.read_bytes().strip()
    kf.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Первый вердикт может одновременно писаться по двум делам. Отдельные
    # журнальные локи тогда не пересекаются, поэтому создание общего ключа
    # сериализуется своим локом.
    import fcntl
    with open(kf.with_name(kf.name + ".lock"), "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if kf.is_file():
            return kf.read_bytes().strip()
        key = secrets.token_hex(32).encode()
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        return key


def _canon(entry):
    fields = PREFLIGHT_SIGNED_FIELDS if entry.get("kind") == "preflight" else SIGNED_FIELDS
    return json.dumps({f: entry.get(f) for f in fields},
                      sort_keys=True, ensure_ascii=False).encode("utf-8")


def _sig(entry):
    return hmac.new(_sign_key(), _canon(entry), hashlib.sha256).hexdigest()


def _verified(entry):
    """Запись подписана и подпись сходится? Нет подписи/источника — не проверяема."""
    s = entry.get("sig")
    if not s or not entry.get("source"):
        return False
    try:
        return hmac.compare_digest(str(s), _sig(entry))
    except (TypeError, ValueError):
        return False


SCAN = find_scan_legal()
# Категории scan_legal.sh, при которых документ не выпускается. Источник один:
# DocBuilder.HUMANIZER_BLOCKERS, чтобы verdict и сборка .docx не расходились.
BLOCKING = DocBuilder.HUMANIZER_BLOCKERS


def sha(path):
    return obs.kluch_kesha(path)


def _drafts_dir(md):
    """Папка drafts для этого черновика — по восхождению до `.agent/drafts`.
    Черновик не под `.agent/drafts` (тесты, разовые файлы) → его же каталог."""
    md = Path(md).resolve()
    for parent in md.parents:
        if parent.name == "drafts" and parent.parent.name == cp.AGENT_DIR:
            return parent
    return md.parent


def journal_path(md):
    """Новый адрес журнала — прямо в drafts, ВНЕ освобожденного сторожем _working (D03).
    Имя берется из case_paths — один источник на систему."""
    return _drafts_dir(md) / cp.VERDICTS_NAME


def legacy_journal_path(md):
    """Старый адрес (внутри _working) — читается как запасной, не пишется."""
    return _drafts_dir(md) / cp.WORKING / cp.VERDICTS_NAME


_warned_legacy = set()


def _read_journal(md):
    """Все записи по документу: новый журнал + старый (запасной, с предупреждением).

    Каждой записи проставляются транзитные (не пишутся на диск) поля `_verified`
    (подпись сходится) и `_legacy` (взята из старого адреса). Порядок: старые раньше
    новых — номера раундов монотонны сквозь переезд.
    """
    target = Path(md).resolve()
    name = target.name
    out = []
    for jp, legacy in ((legacy_journal_path(md), True), (journal_path(md), False)):
        if not jp.is_file():
            continue
        if legacy:
            key = str(jp)
            if key not in _warned_legacy:
                _warned_legacy.add(key)
                print(f"⚠ журнал вердиктов найден по СТАРОМУ адресу {jp} — прочитан "
                      f"как запасной; новые записи идут в {journal_path(md)}",
                      file=sys.stderr)
        with open(jp, encoding="utf-8") as source:
            for line in source:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("document") != name:
                    continue
                try:
                    if Path(e.get("path", "")).resolve() != target:
                        continue
                except (OSError, TypeError, ValueError):
                    continue
                e["_legacy"] = legacy
                e["_verified"] = _verified(e)
                out.append(e)
    return out


def scan(md):
    """Гейт humanizer-legal по `.md`. Возвращает список сработавших блокирующих категорий.

    Скрипта нет → `None` (fail-closed), не пустой список. Путь ищет `find_scan_legal`
    (репозиторий-первый, домашняя копия — запас): пустой список неотличим от
    «прогнали и чисто», анти-AI-гейт молча пропускал бы все (этап 9).
    """
    if not SCAN.is_file():
        print(f"⛔ {SCAN} не найден — humanizer-legal не проверен, fail-closed", file=sys.stderr)
        return None
    try:
        env = os.environ.copy()
        env["THEMIS_PROJECT_ROOT"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = subprocess.run(["bash", str(SCAN), str(md)], capture_output=True,
                           text=True, timeout=300, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"ВНИМАНИЕ: humanizer-legal не отработал ({e})", file=sys.stderr)
        return None
    out = (p.stdout or "") + (p.stderr or "")
    hits = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) > 0:
            if parts[1] in BLOCKING:
                hits.append(f"{parts[1]} ({parts[0]})")
    if p.returncode not in (0, 1):
        err = [l for l in (p.stderr or "").splitlines() if l.strip()]
        detail = f": {err[-1].strip()}" if err else ""
        print(f"ВНИМАНИЕ: humanizer-legal не отработал (код {p.returncode}){detail}",
              file=sys.stderr)
        return None
    if p.returncode == 1 and not hits:
        print("ВНИМАНИЕ: humanizer-legal вернул код 1 без "
              "блокирующей категории", file=sys.stderr)
        return None
    return hits


# Сумма, за ней (необязательно) круглые скобки, за ними слово валюты. Пропись
# обязана стоять МЕЖДУ числом и словом валюты: «1 000 (одна тысяча) рублей» —
# решение владельца 19.08.2026. Даты, номера статей/дел/страниц, проценты и
# ИНН — не деньги, этот шаблон их не задевает (требует слова «рубл*»/«коп*»).
# «р.» с точкой и евро/EUR — тоже валюты: «100 000 р.» и «100 000 евро»
# без прописи получали «ГОТОВ К ПОДАЧЕ» (проба 20.08.2026, круг 5). «р.»
# требует цифры перед собой и не задевает «стр.»/«г.» — те не идут за суммой.
# Сокращенный разряд — часть суммы, а не текст рядом: «100 тыс. руб.» и
# «1,5 млн руб.» проходили мимо проверки целиком, потому что между числом
# и словом валюты стояло слово (проба 20.08.2026).
_RAZRYAD = r"(?:\s*(?:тыс|млн|млрд|тысяч\w*|миллион\w*|миллиард\w*)\.?)?"
_NUM = r"\d[\d  .]*(?:[,.]\d{1,2})?" + _RAZRYAD
# Словарь валют — ОДИН на проект, из scripts/money_rule.py (копии в вердикте
# и стороже формата разошлись, этап 9.22, круг 9).
_CUR = _mr.CUR_RE if _mr is not None else (
    r"руб(?:\.|л\w*)?|₽|коп(?:\.|е\w*)?|доллар\w*|долл\.?|usd|\$|р\.|евро|eur|€")
_CUR_END = _mr.CUR_END if _mr is not None else r"(?![A-Za-zА-Яа-яЁё])"
_MONEY_AFTER_RE = re.compile(rf"(?<!\d)({_NUM})\s*(\([^()]*\))?\s*({_CUR}){_CUR_END}", re.I)
_MONEY_PREFIX_RE = re.compile(rf"(?<!\w)({_CUR})\s*({_NUM})\s*(\([^()]*\))?", re.I)
_MONEY_PARENS_RE = re.compile(rf"(?<!\d)({_NUM})\s*(\([^()]*{_CUR}[^()]*\))", re.I)
# Сумма в круглых скобках с валютой сразу за ними: «(100 000) рублей». Паттерн
# намеренно БЕЗ префикса слов — прежняя форма требовала перед скобками минимум
# три буквы, и вторая сумма в «… (100 000) рублей и (5 000) руб.» не матчилась
# вовсе: после съеденного первым матчем «рублей» оставалась одна буква «и».
# Пропись судится отдельно по последнему слову перед скобками (этап 9.20,
# круг 8 — вторая половина оси дыры «сумма в скобках без прописи»).
_MONEY_BEFORE_RE = re.compile(
    rf"\(\s*({_NUM})\s*\)\s*({_CUR}){_CUR_END}", re.I)
_PROPIS_WORD_RE = re.compile(
    r"\b(?:ноль|один|одна|два|две|три|четыр|пят|шест|сем|восем|девят|десят|"
    r"сто|ста|сот|тысяч|миллион|миллиард|рубл|копе)\w*",
    re.I)
# Одно числительное ЦЕЛИКОМ (для проверки «последнее слово перед скобками») —
# словарь словоформ propis.py из scripts/money_rule.py, а не префикс со
# свободным хвостом: прежняя форма принимала «однако» за числительное, и
# сумма «однако (100 000) рублей» получала вердикт (этап 9.22, круг 9).
# Незаполненная вставка живет не только в скобках. После запрета квадратных
# скобок в проекте составители перешли на подчеркивание, елочки и угловые
# скобки — форма сменилась, брак остался (проба 20.08.2026: документ с
# «Взыскать ______ рублей» получал «ГОТОВ К ПОДАЧЕ»).
_PLACEHOLDER_RE = re.compile(
    # маркеры-слова
    r"\b(?:TODO|FIXME|XXXXX+|XXX+)\b"
    # линейка подчеркиваний или точек — место для вписывания от руки
    r"|_{4,}|\.{6,}",
    re.I)
# Вставка в скобках любой формы: круглых, квадратных, угловых, елочках.
# Содержимое разбирается отдельно (_empty_slot): круглая скобка в иске —
# основная форма пояснения (реквизиты сторон, адреса, номера дел, ссылки на
# нормы), и считать ее браком по одному слову-маркеру нельзя (этап 9.19,
# круг 7: «(ИНН 7712345678, ОГРН 1027700132195)» в шапке иска против
# организации не давало выдать вердикт вовсе).
_BRACKET_SPAN_RE = re.compile(r"[\[(<«]\s*([^)\]>»]{1,80}?)\s*[\])>»]")
_PLACEHOLDER_KEY_RE = re.compile(
    r"указать|укажите|вставить|вписать|заполнить|прописать|подставить|фио|"
    r"сумм[ауеы]?|дата|дату|адрес|инн|огрн|"
    r"наименование|номер|реквизиты|паспорт",
    re.I)
# Слова, из которых состоит подсказка «что вписать» — и ничего больше.
# Связки («и», «от») и слова-наполнители («сюда», «нужное») дыру не
# заполняют: «(вставить сюда наименование суда)» — та же дыра, что
# «(наименование суда)» (этап 9.20, круг 8).
_PLACEHOLDER_VOCAB = frozenset(
    "указать укажите вставить вписать заполнить прописать подставить фио ф и о "
    "сумма сумму суммы дата дату даты "
    "адрес инн огрн наименование номер реквизиты паспорт суда дела истца "
    "ответчика стороны организации заявителя подписанта г "
    "и или а от по в на со без для при сюда нужное нужную необходимое данные "
    "значение текст".split())


def _empty_slot(content):
    """Содержимое скобок — настоящая незаполненная вставка?

    Брак — скобка, где вместо значения стоит УКАЗАНИЕ, что вписать:
    «(указать дату)», «(ФИО)», «(сумма)», «(наименование суда)». Не брак —
    вписанное значение: «(ИНН 7712345678, ОГРН 1027700132195)»,
    «(адрес: г. Казань, ул. Баумана, д. 5)», «(номер дела А65-12345/2026)»,
    «(ст. 309 ГК РФ)». Отличие: вставка состоит ТОЛЬКО из слов-подсказок —
    короткая, без цифр и двоеточия; как только в скобках появилось значение
    (цифра реквизита, двоеточие с адресом), скобка перестает быть дырой.
    Токены без единой буквы («…», «—», «...») словами не считаются: иначе
    «(указать …)» проходило вердикт, и документ из одних таких скобок уходил
    в суд (этап 9.20, круг 8).
    """
    # Точки не учитываются: «(Ф.И.О.)» — та же дыра, что «(ФИО)».
    if not _PLACEHOLDER_KEY_RE.search(content.lower().replace(".", "")):
        return False
    if re.search(r"\d", content) or ":" in content:
        return False          # значение вписано — это реквизит, а не дыра
    words = [w for w in content.split()
             if re.search(r"[A-Za-zА-Яа-яЁё]", w)]
    if not 1 <= len(words) <= 6:
        return False
    return all(w.lower().replace(".", "") in _PLACEHOLDER_VOCAB for w in words)


# Опознанная пропись целиком: число + круглые скобки с кириллической
# расшифровкой (валюта может жить внутри скобок — форма прибора calc395
# «38 998,29 (тридцать восемь тысяч … рублей 29 копеек)»). Все внутри такого
# блока — часть прописи, второй раз не судится: иначе правило ищет пару
# «валюта + число» ВНУТРИ уже опознанной прописи и печатает несуществующую
# сумму «рублей29», по которой юрист не поймет, что править (этап 9.20,
# круг 8).
_PROPIS_BLOCK_RE = re.compile(rf"(?<!\d){_NUM}\s*\([^()]*[а-яёА-ЯЁ][^()]*\)")


def _has_propis_before_number(text, start):
    """Разрешает форму «сто тысяч рублей (100 000 руб.)»."""
    prefix = text[:start].rstrip()
    if not prefix.endswith("("):
        return False
    return bool(_PROPIS_WORD_RE.search(prefix[-140:]))


def format_problems(md):
    """Скобки и наличие прописи — минимальный формат перед финальным вердиктом.

    Точное СОВПАДЕНИЕ прописи с числом — отдельная, более глубокая проверка
    scripts/document_guard.py (этап 9.8); здесь — что расшифровка вообще есть,
    без нее документ уже брак и до сверки дело можно не доводить. Денежный
    разбор — единый, scripts/money_rule.py: дословная цитата в елочках
    (норма, судебный акт) прописи не требует — цитировать обязаны дословно,
    и возражение, цитирующее обжалуемый акт с суммой, обязано получать
    вердикт (этап 9.22, круг 9).
    """
    text = Path(md).read_text(encoding="utf-8", errors="replace")
    problems = []
    square = len(re.findall(r"[\[\]]", text))
    if square:
        problems.append(f"квадратные скобки — {square} шт. (в практике проекта — "
                        "только круглые)")
    for m in _PLACEHOLDER_RE.finditer(text):
        problems.append(f"незаполненная вставка «{m.group(0)}»")
    for m in _BRACKET_SPAN_RE.finditer(text):
        if _empty_slot(m.group(1)):
            problems.append(f"незаполненная вставка «{m.group(0)}»")
    if _mr is None:
        problems.append("scripts/money_rule.py недоступен — денежная проверка "
                        "не выполнена (fail-closed)")
        return problems
    # Деньги судятся БЕЗ цитат в елочках: цитата акта с суммой («суд указал:
    # «взыскать 100 000 рублей»») — дословное воспроизведение, а не сумма
    # документа. Совпадение прописи внутри цитаты сверяет document_guard.
    money_text, _quotes = _mr.split_quotes(text)
    covered = [m.span() for m in _PROPIS_BLOCK_RE.finditer(money_text)]

    def in_covered(pos):
        return any(a <= pos < b for a, b in covered)

    before_nums = set()
    for m in _MONEY_BEFORE_RE.finditer(money_text):
        # Пропись ПЕРЕД числом — «двести тысяч (200 000) рублей» — верная
        # форма, только зеркальная. Но слово перед скобками обязано быть
        # ЧИСЛИТЕЛЬНЫМ из словаря propis.py: «взыскать (100 000) рублей» —
        # сумма в круглых скобках вообще без прописи (этап 9.20, круг 8), а
        # «однако (100 000) рублей» проходило за счет префиксного опознания
        # (этап 9.22, круг 9). Судит ПОСЛЕДНЕЕ слово перед скобками: проверка
        # вхождением по всему префиксу давала обход — в «сто тысяч (100 000)
        # рублей и (5 000) руб.» вторая сумма проходила за счет слова «рублей»
        # от первой (та же дыра, круг 8, вторая половина оси).
        if in_covered(m.start()):
            continue          # внутри уже опознанной прописи — не судим второй раз
        tail = re.findall(r"[A-Za-zА-Яа-яЁё-]+", money_text[:m.start()])
        if tail and _mr.is_numeral(tail[-1]):
            before_nums.add(m.group(1))
        else:
            problems.append(f"сумма «({m.group(1)}) {m.group(2)}» в круглых скобках "
                            f"без прописи перед ними — пропись обязана стоять перед "
                            f"скобками: «сто тысяч (100 000) рублей»")
    for m in _MONEY_AFTER_RE.finditer(money_text):
        num, parens, currency = m.groups()
        if in_covered(m.start()):
            continue          # внутри уже опознанной прописи — не судим второй раз
        if num in before_nums:
            continue
        if _has_propis_before_number(money_text, m.start()):
            continue
        # «… рублей 00 копеек» — нулевые копейки цифрами после суммы, обиходная
        # форма, а не вторая сумма без прописи (проба круга 6, 20.08.2026).
        if re.fullmatch(r"0+", num.strip()) and \
                currency.lower().startswith("коп"):
            continue
        if not parens or not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{num} {currency}» без прописи в круглых скобках "
                            f"между числом и словом валюты")
    for m in _MONEY_PREFIX_RE.finditer(money_text):
        currency, num, parens = m.groups()
        if in_covered(m.start()):
            continue          # «рублей 29» внутри прописи — не вторая сумма
        # «рублей 00 копеек» — тот же хвост нулевых копеек в зеркальной форме.
        if re.fullmatch(r"0+", num.strip()) and \
                currency.lower().startswith(("руб", "коп")):
            continue
        if not parens or not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{currency}{num}» без прописи в круглых скобках")
    for m in _MONEY_PARENS_RE.finditer(money_text):
        num, parens = m.groups()
        if in_covered(m.start()):
            continue
        if not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{num}» без прописи в круглых скобках")
    return problems


def _case_for_draft(md):
    """Дело, которому принадлежит `.agent/drafts/<файл>`; вне дела — None."""
    resolved = Path(md).resolve()
    for parent in resolved.parents:
        if parent.name == "drafts" and parent.parent.name == cp.AGENT_DIR:
            return parent.parent.parent
    return None


def _preflight_context_inputs(md):
    """Пути входов и входы quality_gate для снимка одной проверки."""
    md = Path(md).resolve()
    scripts = Path(__file__).resolve().parent
    inputs = {
        Path(__file__).resolve(),
        scripts / "case_paths.py",
        scripts / "create_docx.py",
        scripts / "crosscheck_numbers.py",
        scripts / "document_guard.py",
        scripts / "markdown_extract.py",
        scripts / "money_rule.py",
        scripts / "propis.py",
        scripts / "quality_gate.py",
        scripts / "table_guard.py",
        scripts / "verify_requisites.py",
        Path(SCAN).resolve(),
    }
    quality = None
    case = _case_for_draft(md)
    if case is not None:
        import quality_gate as qg
        paths = qg.case_paths(str(case))
        sources = [Path(path).resolve() for path in paths["sources"]]
        requisites = [Path(path).resolve() for path in paths["requisites"]]
        policy = Path(qg.policy_path(str(case))).resolve()
        suppressions = Path(qg.suppressions_path(str(case))).resolve()
        inputs.update(sources)
        inputs.update(requisites)
        inputs.update((policy, suppressions))
        intake = case / cp.INTAKE
        intake_files = ([path.resolve() for path in intake.rglob("*")
                         if path.is_file() or path.is_symlink()]
                        if intake.is_dir() else [])
        inputs.update(intake_files)
        quality = {
            "sources": sources,
            "requisites": requisites,
            "policy": policy,
            "suppressions": suppressions,
            "intake": intake_files,
        }
    return inputs, quality


def _context_sha(inputs, known_digests=None):
    """SHA манифеста canonical-path + content-SHA; known_digests экономит перечитку."""
    known_digests = known_digests or {}
    manifest = []
    for path in sorted(inputs, key=str):
        digest = known_digests.get(str(path))
        if digest is None:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                digest = f"unreadable:{type(exc).__name__}"
        manifest.append([str(path), digest])
    return hashlib.sha256(json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def preflight_context_sha(md):
    """Отпечаток правил и материалов, от которых зависит машинный результат."""
    inputs, _ = _preflight_context_inputs(md)
    return _context_sha(inputs)


def preflight_history(md):
    """Подписанные и неподписанные машинные прогоны; раундами не считаются."""
    return [entry for entry in _read_journal(md) if entry.get("kind") == "preflight"]


def preflight_problem(md, digest=None, context_digest=None):
    """Почему раунд нельзя открыть. None — текущая редакция имеет зеленый preflight."""
    md = Path(md)
    if not md.is_file():
        return f"{md}: файла нет"
    current = digest or sha(md)
    entries = [entry for entry in preflight_history(md)
               if entry.get("sha256") == current]
    if not entries:
        return (f"{md.name}: нет preflight по текущему отпечатку {current[:12]}…; "
                f"сначала: python3 scripts/verdict.py --preflight {md}")
    signed = [entry for entry in entries if entry.get("_verified")]
    if not signed:
        return f"{md.name}: последняя запись preflight по текущему отпечатку не подписана"
    latest = signed[-1]
    context = context_digest or preflight_context_sha(md)
    if latest.get("context_sha256") != context:
        return (f"{md.name}: после preflight изменились правила или материалы машинной "
                "проверки; прогнать preflight заново")
    if not latest.get("green"):
        failed = ", ".join(check.get("tool", "?") for check in latest.get("checks", [])
                           if check.get("code") != 0) or "неизвестный прибор"
        return f"{md.name}: preflight по текущему отпечатку красный ({failed})"
    return None


def _write_preflight(md, digest, green, checks, context_digest=None):
    """Append-only запись машинного прогона в тот же подписанный журнал состояния."""
    md = Path(md)
    jp = journal_path(md)
    jp.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    with open(jp, "a", encoding="utf-8") as journal:
        fcntl.flock(journal.fileno(), fcntl.LOCK_EX)
        entry = {
            "kind": "preflight",
            "document": md.name,
            "path": str(md.resolve()),
            "sha256": digest,
            "context_sha256": context_digest or preflight_context_sha(md),
            "at": time.strftime("%d.%m.%Y %H:%M:%S"),
            "source": "machine-preflight",
            "green": bool(green),
            "checks": checks,
        }
        entry["sig"] = _sig(entry)
        journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
        journal.flush()
        os.fsync(journal.fileno())
        return entry


def _run_preflight_check(name, command, env):
    stdout = ""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300,
                                stdin=subprocess.DEVNULL, env=env,
                                cwd=str(Path(__file__).resolve().parent.parent))
        stdout = result.stdout or ""
        output = stdout + (result.stderr or "")
        code = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        output, code = str(exc), obs.KOD_NE_RABOTAL
    check = {
        "tool": name,
        "code": code,
        "output_sha256": hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest(),
    }
    if name == "quality_gate":
        try:
            payload = json.loads(stdout or "{}")
            check["findings"] = [item.get("id") for item in payload.get("findings", [])
                                 if isinstance(item, dict) and item.get("id")]
            check["suppressed"] = [item.get("id") for item in payload.get("suppressed", [])
                                   if isinstance(item, dict) and item.get("id")]
            if (payload.get("ok") is not True or check["findings"]) and code == obs.KOD_OK:
                code = check["code"] = obs.KOD_OSHIBKA
                output += "\nquality_gate JSON содержит незакрытые находки при коде 0"
        except (ValueError, AttributeError):
            check["findings"] = []
            if code == obs.KOD_OK:
                code = check["code"] = obs.KOD_NE_RABOTAL
                output += "\nquality_gate вернул не JSON при коде 0"
    return check, output


def preflight(md):
    """Прогнать три механических гейта и подписать результат по SHA-256 редакции."""
    md = Path(md).resolve()
    if not md.is_file():
        print(f"⛔ PREFLIGHT НЕ ВЫПОЛНЕН — файла нет: {md}", file=sys.stderr)
        return obs.KOD_NE_RABOTAL
    try:
        content = md.read_bytes()
    except OSError as exc:
        print(f"⛔ PREFLIGHT НЕ ВЫПОЛНЕН — файл не прочитан: {exc}", file=sys.stderr)
        return obs.KOD_NE_RABOTAL
    before = hashlib.sha256(content).hexdigest()
    context_inputs, _ = _preflight_context_inputs(md)
    context_before = _context_sha(context_inputs)
    if preflight_problem(md, before, context_before) is None:
        print(f"ЗЕЛЕНЫЙ preflight уже записан: {md.name} · {before[:12]}…; повтор не нужен")
        return obs.KOD_OK
    case = _case_for_draft(md)
    scripts = Path(__file__).resolve().parent
    env = os.environ.copy()
    env["THEMIS_PROJECT_ROOT"] = str(scripts.parent)
    work = (_drafts_dir(md) / cp.WORKING) if case is not None else md.parent
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"⛔ PREFLIGHT НЕ ВЫПОЛНЕН — снимок редакции не создать: {exc}",
              file=sys.stderr)
        return obs.KOD_NE_RABOTAL
    checks, outputs = [], {}
    snapshot_context_stable = True
    try:
        with tempfile.TemporaryDirectory(dir=work, prefix=f".{md.stem}.preflight-") as tmp:
            stage = Path(tmp)
            snapshot = stage / "document" / md.name
            snapshot.parent.mkdir()
            snapshot.write_bytes(content)
            commands = [
                ("document_guard", [sys.executable, str(scripts / "document_guard.py"),
                                    "--md-only", str(snapshot)]),
            ]
            if case is None:
                checks.append({
                    "tool": "quality_gate",
                    "code": obs.KOD_OK,
                    "status": "not-applicable-outside-case",
                    "output_sha256": hashlib.sha256(b"outside case: no sources").hexdigest(),
                    "findings": [],
                    "suppressed": [],
                })
            else:
                snapshot_inputs, quality = _preflight_context_inputs(md)
                known = {}

                def stage_input(original, group, index=0):
                    target = stage / group / str(index) / original.name
                    try:
                        data = original.read_bytes()
                    except OSError:
                        return target
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    known[str(original)] = hashlib.sha256(data).hexdigest()
                    return target

                sources = [stage_input(path, "sources", index)
                           for index, path in enumerate(quality["sources"])]
                requisites = [stage_input(path, "requisites", index)
                              for index, path in enumerate(quality["requisites"])]
                policy = stage_input(quality["policy"], "config")
                suppressions = stage_input(quality["suppressions"], "config", 1)
                snapshot_context_stable = (
                    _context_sha(snapshot_inputs, known) == context_before)

                quality_command = [
                    sys.executable, str(scripts / "quality_gate.py"),
                    "--doc", str(snapshot), "--subject", md.name,
                ]
                if sources:
                    quality_command += ["--against", *(str(path) for path in sources)]
                if requisites:
                    quality_command += ["--requisites", *(str(path) for path in requisites)]
                if quality["intake"]:
                    quality_command.append("--intake-present")
                quality_command += [
                    "--policy", str(policy), "--suppressions", str(suppressions), "--json",
                ]
                commands.append(("quality_gate", quality_command))
            if SCAN.is_file():
                commands.append(("humanizer-legal", ["bash", str(SCAN), str(snapshot)]))
            else:
                checks.append({
                    "tool": "humanizer-legal",
                    "code": obs.KOD_NE_RABOTAL,
                    "output_sha256": hashlib.sha256(str(SCAN).encode()).hexdigest(),
                })
                outputs["humanizer-legal"] = f"скрипт не найден: {SCAN}"

            for name, command in commands:
                check, output = _run_preflight_check(name, command, env)
                checks.append(check)
                outputs[name] = output
    except OSError as exc:
        message = f"снимок редакции не создан: {exc}"
        checks.append({
            "tool": "snapshot",
            "code": obs.KOD_NE_RABOTAL,
            "output_sha256": hashlib.sha256(message.encode()).hexdigest(),
        })
        outputs["snapshot"] = message
    try:
        stable = sha(md) == before
    except OSError:
        stable = False
    context_stable = snapshot_context_stable and \
        preflight_context_sha(md) == context_before
    if not stable or not context_stable:
        reason = ("файл изменен во время preflight" if not stable
                  else "правила или материалы изменены во время preflight")
        checks.append({
            "tool": "preflight-stability",
            "code": obs.KOD_OSHIBKA,
            "output_sha256": hashlib.sha256(reason.encode()).hexdigest(),
        })
        outputs["preflight-stability"] = reason
    green = stable and context_stable and all(check["code"] == obs.KOD_OK for check in checks)
    try:
        entry = _write_preflight(md, before, green, checks, context_before)
    except OSError as exc:
        print(f"⛔ PREFLIGHT НЕ ЗАПИСАН — журнал недоступен: {exc}", file=sys.stderr)
        return obs.KOD_NE_RABOTAL
    mark = "ЗЕЛЕНЫЙ" if green else "КРАСНЫЙ"
    print(f"{mark} preflight: {md.name} · {entry['sha256'][:12]}…")
    for check in checks:
        print(f"  {check['tool']}: код {check['code']}")
        if check["code"] != obs.KOD_OK and outputs.get(check["tool"]):
            print(outputs[check["tool"]][-4000:].rstrip())
    return obs.KOD_OK if green else obs.KOD_OSHIBKA


def next_round(md):
    rounds = [int(e.get("round") or 0) for e in history(md) if e.get("_verified")]
    return (max(rounds) + 1) if rounds else 1


def record(md, verdict, round_no=None, source=None):
    # Гейт 0 — источник: вердикт обязан быть attributable, и составитель себя не судит.
    src = (source or "").strip()
    if not src:
        print("⛔ ВЕРДИКТ НЕ ЗАПИСАН — не указан источник (--source): вердикт обязан "
              "быть привязан к тому, кто его выписал.", file=sys.stderr)
        return None
    if src.casefold() in SELF_SOURCES:
        print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — self-record: источник «{src}» — сам составитель "
              f"документа. Генератор не судит себя, вердикт выписывает рецензент (Кони).",
              file=sys.stderr)
        return None
    if src.casefold() not in REVIEW_SOURCES:
        print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — источник «{src}» не рецензент и не "
              "координатор. Допустимы: " + ", ".join(sorted(REVIEW_SOURCES)),
              file=sys.stderr)
        return None
    # ponytail: строка источника дает атрибуцию внутри доверенного локального процесса,
    # но не аутентифицирует ОС-пользователя. Полная изоляция требует отдельного сервиса
    # рецензента с недоступным составителю ключом.
    # Гейт 1 — закрытый словарь: вердикт вне списка не пишется, печатаем допустимое.
    if verdict not in VERDICTS:
        print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — «{verdict}» вне закрытого словаря. Разрешены только:",
              file=sys.stderr)
        for v in sorted(VERDICTS):
            print(f"   · {v}", file=sys.stderr)
        return None
    md = Path(md)
    if not md.is_file():
        print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — файла нет: {md}", file=sys.stderr)
        return None
    jp = journal_path(md)
    jp.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    with open(jp, "a+", encoding="utf-8") as journal:
        # Раунд заперт машиной от чтения истории до fsync записи. Два рецензента
        # не могут одновременно получить один номер или перескочить потолок.
        fcntl.flock(journal.fileno(), fcntl.LOCK_EX)
        before = sha(md)
        machine_problem = preflight_problem(md, before)
        if machine_problem:
            print(f"⛔ РАУНД НЕ ОТКРЫТ — {machine_problem}", file=sys.stderr)
            return None
        print(f"✓ preflight зеленый: {md.name} · {before[:12]}…")
        # Неподписанная строка не может сжечь номер или лимит законного раунда.
        hist = [entry for entry in history(md) if entry.get("_verified")]
        expected = max([int(e.get("round") or 0) for e in hist] or [0]) + 1
        if round_no is not None and round_no != expected:
            print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — номер раунда считает машина: "
                  f"ожидается r{expected}, получен r{round_no} по документу «{md.name}».",
                  file=sys.stderr)
            return None
        round_no = expected

        # Гейт sha остается внутри того же лока. Одинаковая редакция не получает
        # новый раунд, а явный номер агента служит только проверочным утверждением.
        same_sha = [e for e in hist if e.get("sha256") == before]
        # Старый формат без подписи читается, но сборку не открывает. Его надо
        # уметь переосвидетельствовать на ТЕХ ЖЕ байтах: это не новая редакция,
        # а первая проверяемая запись. Если по sha уже есть подписанный раунд,
        # прежний антидубль остается без послаблений.
        signed_same = [e for e in same_sha if e.get("_verified")]
        if signed_same:
            e = signed_same[-1]
            print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — «{md.name}» побайтно равен ранее сданной "
                  f"редакции (r{e.get('round')}). Заявление о правке, "
                  f"не подтвержденное файлом, не принимается.", file=sys.stderr)
            return None

        # Клапан: положительный вердикт по новой финальной редакции не блокируется
        # потолком. Все прочие rN на пределе по-прежнему дают код 3.
        if verdict not in READY_VERDICTS:
            limit = round_limit()
            if round_no >= limit:
                raise RoundLimitExceeded(
                    f"⛔ РАУНД {round_no} НЕ ЗАПИСАН — лимит рецензии в {limit - 1} круга по "
                    f"документу «{md.name}» исчерпан. Это эскалация владельцу, а не еще "
                    f"один круг: 25.08 восемь кругов по одному списку сожгли прогон.")

        # Механика уже пройдена и подписана preflight по этим байтам. Здесь не
        # повторяем ее после содержательной рецензии — только закрываем гонку файла.
        if sha(md) != before:
            print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — {md.name} изменен во время проверки; "
                  "повторить раунд на стабильной редакции.", file=sys.stderr)
            return None
        entry = {
            "document": md.name,
            "path": str(md.resolve()),
            "round": round_no,
            "verdict": verdict,
            "sha256": before,
            "at": time.strftime("%d.%m.%Y %H:%M:%S"),
            "source": src,
        }
        entry["sig"] = _sig(entry)      # внеполосная подпись — ключ вне журнала
        journal.write(json.dumps(entry, ensure_ascii=False) + "\n")
        journal.flush()
        os.fsync(journal.fileno())
        return entry


def history(md):
    """Только вердикты; записи preflight не получают номер раунда."""
    return [entry for entry in _read_journal(md) if entry.get("kind") != "preflight"]


def check(md):
    """Причины, по которым из этой редакции нельзя собирать `.docx`. Пусто — можно."""
    md = Path(md)
    if not md.is_file():
        return [f"{md}: файла нет — собирать не из чего"]
    now = sha(md)
    raw_history = history(md)
    hist = [entry for entry in raw_history if entry.get("_verified")]
    if not hist:
        unsigned = [entry for entry in raw_history
                    if entry.get("sha256") == now and not entry.get("_verified")]
        if unsigned and unsigned[-1].get("verdict") in READY_VERDICTS:
            where = "по старому адресу" if unsigned[-1].get("_legacy") else "в журнале"
            return [f"{md.name}: запись «{READY}» {where} без проверяемой подписи — "
                    "вердикт не признан"]
        return [f"{md.name}: вердикта нет вовсе — документ не проходил проверку Кони"]
    # Судит ПОСЛЕДНИЙ вердикт по этой редакции, а не любой одобрительный в
    # истории: иначе «ТРЕБУЕТ ПРАВОК», записанный ПОСЛЕ «ГОТОВ К ПОДАЧЕ» на ту
    # же редакцию, ничего не отзывает — найденная Кони ошибка не останавливала
    # выдачу (этап 9.19, круг 7). Возврат к тексту ранее одобренной редакции
    # по-прежнему собирается: последняя запись по ее отпечатку — одобрение.
    same = [e for e in hist if e.get("sha256") == now]
    if same:
        if same[-1].get("verdict") in READY_VERDICTS:
            machine_problem = preflight_problem(md, now)
            if machine_problem:
                return [f"{md.name}: машинный допуск устарел или красный — "
                        f"{machine_problem}"]
            # Текущий подписанный preflight уже покрыл формат, quality_gate и
            # humanizer по этим байтам и этому контексту. Повтор тут не нужен.
            return []
        v = same[-1]
        if any(e.get("verdict") in READY_VERDICTS for e in same):
            return [f"{md.name}: одобрение этой редакции ОТОЗВАНО — последний вердикт "
                    f"по ней «{v.get('verdict')}» (r{v.get('round')}), не «{READY}»; "
                    f"закрыть замечания Кони и провести новый раунд"]
        return [f"{md.name}: последний вердикт по этой редакции «{v.get('verdict')}» "
                f"(r{v.get('round')}) — не «{READY}»"]
    approved = [e for e in hist if e.get("verdict") in READY_VERDICTS]
    if approved:
        last = approved[-1]
        return [f"{md.name}: вердикт «{READY}» есть, но выдан на ДРУГУЮ редакцию "
                f"(r{last.get('round')}, отпечаток {last.get('sha256', '')[:12]}…, "
                f"сейчас {now[:12]}…) — текст правился после одобрения, нужен новый раунд"]
    last = hist[-1]
    return [f"{md.name}: последний вердикт «{last.get('verdict')}» (r{last.get('round')}) — "
            f"не «{READY}»"]


def selftest():
    import tempfile
    from contextlib import redirect_stderr
    import io
    # Ключ подписи — из env, чтобы селфтест был герметичен и не трогал каталог секретов.
    os.environ["THEMIS_VERDICT_KEY"] = "selftest-key-do-not-use-in-prod"

    def approve(md, green=True):
        return _write_preflight(md, sha(md), green, [{
            "tool": "selftest",
            "code": obs.KOD_OK if green else obs.KOD_OSHIBKA,
            "output_sha256": hashlib.sha256(b"selftest").hexdigest(),
        }])

    def rec(md, verdict, round_no=None, source="doc-reviewer"):
        if preflight_problem(md):
            approve(md)
        return record(md, verdict, round_no, source)

    with tempfile.TemporaryDirectory(prefix="verdict-selftest-") as tmp:
        cfg = Path(tmp) / "verdict.json"
        cfg.write_text(json.dumps({"override": None}), encoding="utf-8")
        os.environ["THEMIS_VERDICT_CONFIG"] = str(cfg)
        d = Path(tmp) / "cases" / "ivanov-ivan" / "delo-2026" / cp.AGENT_DIR / "drafts"
        d.mkdir(parents=True)
        case = d.parent.parent
        context = case / cp.CONTEXT
        context.mkdir(parents=True)
        (context / "knowledge-map.md").write_text("# Карта\n", encoding="utf-8")
        policy = cp.working(case) / "quality_gate.json"
        policy.parent.mkdir(parents=True)
        policy.write_text('{"version":1,"rules":[]}\n', encoding="utf-8")
        md = d / "isk_v1.md"
        md.write_text("# Иск\n\nТекст первой редакции.\n", encoding="utf-8")

        # M06: раунд не рождается до подписанного зеленого preflight текущих байтов.
        assert record(md, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is None, \
            "раунд открылся без preflight"
        assert history(md) == [], "отказ без preflight все же создал раунд"
        approve(md, green=False)
        assert record(md, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is None, \
            "красный preflight открыл раунд"
        approve(md)
        assert preflight_problem(md) is None, "зеленый preflight не принят"
        assert next_round(md) == 1, "preflight ошибочно посчитан раундом рецензии"
        with open(journal_path(md), "a", encoding="utf-8") as journal:
            journal.write(json.dumps({
                "kind": "preflight", "document": md.name, "path": str(md),
                "sha256": sha(md), "green": False, "source": "machine-preflight",
                "checks": [], "at": "01.09.2026 00:00:00",
            }, ensure_ascii=False) + "\n")
        assert preflight_problem(md) is None, \
            "неподписанная строка отравила подписанный зеленый preflight"

        unsigned = d / "preflight-unsigned.md"
        unsigned.write_text("# Ходатайство\n\nНеподписанный машинный прогон.\n", encoding="utf-8")
        unsigned_entry = {
            "kind": "preflight", "document": unsigned.name,
            "path": str(unsigned), "sha256": sha(unsigned),
            "at": "01.09.2026 00:00:00", "source": "machine-preflight",
            "green": True, "checks": [],
        }
        with open(journal_path(unsigned), "a", encoding="utf-8") as journal:
            journal.write(json.dumps(unsigned_entry, ensure_ascii=False) + "\n")
        assert preflight_problem(unsigned), "неподписанный зеленый preflight принят"
        assert record(unsigned, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is None, \
            "неподписанный preflight открыл раунд"

        bound = d / "preflight-bound.md"
        bound.write_text("# Ходатайство\n\nРедакция один.\n", encoding="utf-8")
        approve(bound)
        old_digest = sha(bound)
        assert preflight_problem(bound) is None
        bound.write_text("# Ходатайство\n\nРедакция два.\n", encoding="utf-8")
        assert sha(bound) != old_digest and preflight_problem(bound), \
            "правка файла не отозвала preflight старого отпечатка"
        assert record(bound, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is None, \
            "раунд открылся после правки без нового preflight"
        approve(bound)
        assert record(bound, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is not None, \
            "содержательный красный вердикт заблокирован после зеленой машины"

        # Зеленая запись относится и к действовавшим правилам/материалам. Правка
        # policy без изменения документа требует нового машинного прогона.
        context_bound = d / "preflight-context.md"
        context_bound.write_text("# Ходатайство\n\nТекст без реквизитов.\n", encoding="utf-8")
        approve(context_bound)
        policy.write_text(json.dumps({"version": 1, "rules": [{
            "id": "new-owner-rule", "kind": "remark", "expect": "present",
            "terms": ["согласовано"], "reason": "новое решение владельца",
        }]}, ensure_ascii=False), encoding="utf-8")
        assert preflight_problem(context_bound), \
            "изменение машинных правил не отозвало старый зеленый preflight"
        policy.write_text('{"version":1,"rules":[]}\n', encoding="utf-8")

        build_context = d / "build-context.md"
        build_context.write_text("# Ходатайство\n\nТекст готов.\n", encoding="utf-8")
        assert rec(build_context, READY, 1) is not None
        assert not check(build_context)
        policy.write_text(json.dumps({"version": 1, "rules": [{
            "id": "late-owner-rule", "kind": "prohibition", "expect": "present",
            "terms": ["согласовано"], "reason": "решение после рецензии",
        }]}, ensure_ascii=False), encoding="utf-8")
        assert check(build_context), \
            "изменение policy после READY не закрыло сборку"
        policy.write_text('{"version":1,"rules":[]}\n', encoding="utf-8")

        # Одинаковые имя и байты в подпапках одного drafts не делят допуск.
        twin_a, twin_b = d / "a" / "same.md", d / "b" / "same.md"
        twin_a.parent.mkdir()
        twin_b.parent.mkdir()
        twin_a.write_text("# Ходатайство\n\nОдинаковый текст.\n", encoding="utf-8")
        twin_b.write_bytes(twin_a.read_bytes())
        approve(twin_a)
        assert preflight_problem(twin_b), \
            "preflight чужого пути принят по совпавшим имени и байтам"

        # Вне дела применимые проверки работают, а проверка источников чисел
        # явно помечается неприменимой; разовый документ не запирается навсегда.
        loose = Path(tmp) / "loose.md"
        loose.write_text("# Ходатайство\n\nЗаседание назначено.\n", encoding="utf-8")
        assert preflight(loose) == obs.KOD_OK, "preflight вне дела не стал зеленым"
        preflight_count = len(preflight_history(loose))
        assert preflight(loose) == obs.KOD_OK and \
            len(preflight_history(loose)) == preflight_count, \
            "неизмененный зеленый preflight запущен и записан повторно"
        assert record(loose, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is not None, \
            "зеленый preflight вне дела не открыл раунд"

        real = d / "real-preflight.md"
        real.write_text("# Ходатайство\n\nЗаседание следует отложить.\n", encoding="utf-8")
        assert preflight(real) == obs.KOD_OK, "чистый черновик дела не прошел preflight"
        assert record(real, "ТРЕБУЕТ ПРАВОК", 1, "doc-reviewer") is not None, \
            "содержательное замечание не открыло раунд после реального preflight"

        # Журнал — прямо в drafts, ВНЕ _working (D03): адрес из case_paths.
        assert journal_path(md) == (d / cp.VERDICTS_NAME).resolve(), journal_path(md)
        assert cp.WORKING not in journal_path(md).parts, \
            "журнал остался в слепом пятне сторожа (_working)"
        assert check(md), "документ без вердикта признан готовым к сборке"
        assert "вердикта нет вовсе" in check(md)[0]

        rec(md, "ТРЕБУЕТ ПРАВОК", 1)
        assert check(md), "вердикт ТРЕБУЕТ ПРАВОК пропустил сборку"
        assert "не «ГОТОВ К ПОДАЧЕ»" in check(md)[0]

        md.write_text("# Иск\n\nТекст второй редакции после правки.\n", encoding="utf-8")
        rec(md, READY, 2)
        assert not check(md), f"одобренная редакция не пропущена: {check(md)}"

        # Ровно тот случай, ради которого все это: текст правится ПОСЛЕ одобрения —
        # прежний вердикт по новой редакции сборку не пускает.
        md.write_text("# Иск\n\nТекст второй редакции после правки.\n\nДописанный абзац.\n",
                      encoding="utf-8")
        problems = check(md)
        assert problems, "измененный после одобрения текст пропущен к сборке"
        assert "ДРУГУЮ редакцию" in problems[0], problems

        # Возврат к одобренному тексту не воскрешает прежний вердикт по ошибке:
        # отпечаток совпадает — значит это буквально та самая одобренная редакция.
        md.write_text("# Иск\n\nТекст второй редакции после правки.\n", encoding="utf-8")
        assert not check(md), "возврат к ранее одобренному тексту заблокирован зря"

        assert len(history(md)) == 2, history(md)   # r1 правки, r2 готов; третьего круга нет
        assert check(d / "net.md"), "несуществующий файл признан готовым"

        # Гейт humanizer — fail-closed: нет скрипта, значит СТОП, а не тихий пропуск.
        global SCAN
        saved, SCAN = SCAN, Path(tmp) / "net-skripta.sh"
        try:
            assert scan(md) is None, "отсутствие скрипта не дало fail-closed сигнала"
        finally:
            SCAN = saved

        # Формат перед финальным вердиктом: скобки и наличие прописи.
        chisto = d / "chisto.md"
        chisto.write_text("# Ходатайство\n\nПрошу суд отложить заседание "
                          "(ст. 158 АПК РФ).\n", encoding="utf-8")
        assert format_problems(chisto) == [], format_problems(chisto)
        assert rec(chisto, READY, 1) is not None, "чистый документ не записан"

        skobki = d / "skobki.md"
        skobki.write_text("# Ходатайство\n\nПрошу суд [указать дату] отложить.\n",
                          encoding="utf-8")
        assert format_problems(skobki), "квадратные скобки не пойманы"
        assert preflight(skobki) == obs.KOD_OSHIBKA, "брак получил зеленый preflight"
        assert record(skobki, READY, 1, "doc-reviewer") is None, \
            "красный preflight со скобками открыл раунд"

        summa = d / "summa.md"
        summa.write_text("# Заявление\n\nВзыскать 100 000 рублей неустойки "
                         "(ст. 330 ГК РФ).\n", encoding="utf-8")
        assert format_problems(summa), "сумма без прописи не поймана"
        assert preflight(summa) == obs.KOD_OSHIBKA, "сумма без прописи получила зеленый preflight"
        assert record(summa, READY, 1, "doc-reviewer") is None, \
            "красный preflight суммы открыл раунд"

        propisano = d / "propisano.md"
        propisano.write_text("# Заявление\n\nВзыскать 100 000 (сто тысяч) рублей "
                             "неустойки (ст. 330 ГК РФ).\n", encoding="utf-8")
        assert format_problems(propisano) == [], format_problems(propisano)

        # Зеркальная верная форма — пропись ПЕРЕД числом, и суммы с десятками
        # («сто тридцать»): судится последнее слово перед скобками (круг 8).
        pered = d / "pered.md"
        pered.write_text("# Заявление\n\nВзыскать двести тысяч (200 000) рублей "
                         "долга и сто тридцать (130) рублей расходов.\n",
                         encoding="utf-8")
        assert format_problems(pered) == [], format_problems(pered)
        # Обратная ось: слово «рублей» от ПЕРВОЙ суммы не покрывает вторую —
        # «и (5 000) руб.» без своей прописи остается браком.
        vtorym = d / "vtoraya-summa.md"
        vtorym.write_text("# Заявление\n\nВзыскать сто тысяч (100 000) рублей "
                          "и (5 000) руб. процентов.\n", encoding="utf-8")
        assert format_problems(vtorym), \
            "вторая сумма в скобках без прописи прошла за счет чужой прописи"

        # Этап 9.22, круг 9: дословная цитата судебного акта с суммой вердикту
        # не мешает (возражения и жалобы почти всегда цитируют обжалуемый акт);
        # «однако» — не числительное, сумма в скобках без прописи остается браком.
        citata = d / "citata.md"
        citata.write_text("# Возражения\n\nСуд первой инстанции указал: «взыскать "
                          "с ответчика 100 000 рублей неосновательного обогащения», "
                          "не установив факт приобретения имущества (ст. 1102 ГК РФ).\n",
                          encoding="utf-8")
        assert format_problems(citata) == [], format_problems(citata)
        odnako = d / "odnako.md"
        odnako.write_text("# Заявление\n\nОтветчик оплатил часть поставки, однако "
                          "(100 000) рублей остались невыплаченными.\n",
                          encoding="utf-8")
        assert format_problems(odnako), "слово «однако» принято за числительное"

        # «р.» с точкой и евро/EUR — тоже валюты (проба 20.08.2026, круг 5).
        rtochka = d / "rtochka.md"
        rtochka.write_text("# Заявление\n\nВзыскать 100 000 р. неустойки по договору.\n",
                           encoding="utf-8")
        assert format_problems(rtochka), "сумма с «р.» без прописи не поймана"
        evro = d / "evro.md"
        evro.write_text("# Заявление\n\nВзыскать 100 000 евро по контракту.\n",
                        encoding="utf-8")
        assert format_problems(evro), "сумма в евро без прописи не поймана"
        chistie_valyuty = d / "chistie-valyuty.md"
        chistie_valyuty.write_text("# Заявление\n\nВзыскать 100 000 (сто тысяч) р. и "
                                   "2 000 (две тысячи) евро по контракту.\n",
                                   encoding="utf-8")
        assert format_problems(chistie_valyuty) == [], format_problems(chistie_valyuty)

        # Круглые скобки с реквизитами — НЕ пустая вставка (этап 9.19, круг 7:
        # иск против организации не мог получить вердикт вовсе).
        rekv = d / "rekvizity.md"
        rekv.write_text("# ИСКОВОЕ ЗАЯВЛЕНИЕ\n\nОтветчик: ООО «Ромашка» "
                        "(ИНН 7712345678, ОГРН 1027700132195), проживает "
                        "(адрес: г. Казань, ул. Баумана, д. 5), спор рассмотрен "
                        "(номер дела А65-12345/2026) по норме (ст. 309 ГК РФ).\n",
                        encoding="utf-8")
        assert format_problems(rekv) == [], format_problems(rekv)
        # Обратная ось: настоящие дыры ловятся во всех формах.
        dyra = d / "dyra.md"
        dyra.write_text("# Заявление\n\nДоговор заключен (указать дату). "
                        "Истец: (ФИО) обратился. В (наименование суда) подано. "
                        "Подписант: (Ф.И.О.).\n", encoding="utf-8")
        assert len(format_problems(dyra)) == 4, format_problems(dyra)

        # Повторно судить ТУ ЖЕ редакцию нельзя — гейт sha и лимит раундов держат
        # (заявление о правке без правки не принимается).
        otzyv = d / "otzyv.md"
        otzyv.write_text("# Заявление\n\nТекст без брака и вставок.\n",
                         encoding="utf-8")
        assert rec(otzyv, READY, 1) is not None
        assert not check(otzyv), f"одобренная редакция не пропущена: {check(otzyv)}"
        assert rec(otzyv, "ТРЕБУЕТ ПРАВОК", 2) is None, \
            "новый вердикт на неизменный sha записан"

        # ── Три гейта цикла рецензии (ремонт 25.08) ──
        # Гейт 1 — закрытый словарь: вердикт вне списка не пишется, разрешенный — пишется.
        slovar = d / "slovar.md"
        slovar.write_text("# Ходатайство\n\nЧистый текст без брака.\n", encoding="utf-8")
        assert rec(slovar, "ЧТО-УГОДНО", 1) is None, "вердикт вне словаря записан"
        assert rec(slovar, "ПРОВЕРЕНО БЕЗ КОНИ (ТРЕБУЕТ ПРАВОК)", 1) is not None, \
            "разрешенный резервный вердикт отбит зря"

        # Гейт 2 — правка без изменения файла: тот же sha не пишется,
        # реальная правка — пишется.
        dubl = d / "dubl.md"
        dubl.write_text("# Иск\n\nПервая редакция.\n", encoding="utf-8")
        assert rec(dubl, "ТРЕБУЕТ ПРАВОК", 1) is not None, "первая запись отбита"
        assert rec(dubl, "ТРЕБУЕТ ПРАВОК", 2) is None, \
            "неизменный файл с тем же вердиктом записан повторно"
        assert rec(dubl, "ПРОВЕРЕНО ЧАСТИЧНО", 2) is None, \
            "неизменный файл с другим вердиктом записан повторно"
        dubl.write_text("# Иск\n\nПервая редакция.\n\nПравка.\n", encoding="utf-8")
        assert rec(dubl, "ТРЕБУЕТ ПРАВОК", 2) is not None, "реальная правка отбита зря"

        # Гейт лимита: отрицательный раунд у потолка отбивается стоп-кодом.
        dubl.write_text("# Иск\n\nПервая редакция.\n\nПравка.\n\nЕще правка.\n",
                        encoding="utf-8")
        try:
            rec(dubl, "ТРЕБУЕТ ПРАВОК", cp.REVIEW_STOP_ROUND)
            assert False, "третий раунд не отбит"
        except RoundLimitExceeded:
            pass
        auto = d / "auto.md"
        auto.write_text("# Иск\n\nАвто r1.\n", encoding="utf-8")
        assert rec(auto, "ТРЕБУЕТ ПРАВОК", cp.MAX_REVIEW_ROUNDS) is None, \
            "агент смог назначить машине чужой номер раунда"
        assert rec(auto, "ТРЕБУЕТ ПРАВОК")["round"] == 1, "авто-раунд не начал с r1"
        auto.write_text("# Иск\n\nАвто r2.\n", encoding="utf-8")
        assert rec(auto, "ТРЕБУЕТ ПРАВОК")["round"] == cp.MAX_REVIEW_ROUNDS, \
            "авто-раунд не поднялся до потолка"
        auto.write_text("# Иск\n\nАвто r3.\n", encoding="utf-8")
        try:
            rec(auto, "ТРЕБУЕТ ПРАВОК")
            assert False, "третий авто-раунд не отбит"
        except RoundLimitExceeded:
            pass

        # Клапан M02: лимит останавливает еще один круг правок, но не финальный
        # положительный вердикт по НОВОЙ редакции.
        valve = d / "valve.md"
        valve.write_text("# Ходатайство\n\nПервая редакция.\n", encoding="utf-8")
        assert rec(valve, "ТРЕБУЕТ ПРАВОК")["round"] == 1
        valve.write_text("# Ходатайство\n\nВторая редакция.\n", encoding="utf-8")
        assert rec(valve, "ТРЕБУЕТ ПРАВОК")["round"] == cp.MAX_REVIEW_ROUNDS
        valve.write_text("# Ходатайство\n\nФинальная редакция без замечаний.\n", encoding="utf-8")
        assert rec(valve, READY)["round"] == cp.REVIEW_STOP_ROUND, \
            "положительный финальный вердикт заблокирован на пределе"

        # Послабление существует только как объект с причиной и сроком.
        cfg.write_text(json.dumps({"override": {
            "round_limit": cp.REVIEW_STOP_ROUND + 1,
            "reason": "разовый дополнительный круг"}}), encoding="utf-8")
        try:
            round_limit()
            assert False, "послабление без даты истечения принято"
        except RoundConfigError:
            pass
        cfg.write_text(json.dumps({"override": {
            "round_limit": cp.REVIEW_STOP_ROUND + 1, "reason": "разовый дополнительный круг",
            "expires": "31.12.2099"}}), encoding="utf-8")
        assert round_limit() == cp.REVIEW_STOP_ROUND + 1, \
            "действующее оформленное послабление не принято"
        cfg.write_text(json.dumps({"override": None}), encoding="utf-8")

        # ── D03: три сценария, на которых селфтест обязан краснеть ──
        # Фикстуры строятся по ту сторону порога — на настоящем формате записи
        # (реальный record() пишет sig + source), а не на упрощенном.

        # СЦЕНАРИЙ 1 — дописанная строка без подписи. Подделка 01.09.2026: строка с
        # верным sha256 открывала сборку. Теперь запись без проверяемой подписи
        # вердиктом не считается — check() отказывает.
        forge = d / "forge.md"
        forge.write_text("# Иск\n\nОдобренный судом текст.\n", encoding="utf-8")
        approve(forge)
        forged = {
            "document": "forge.md", "path": str(forge), "round": 1,
            "verdict": READY, "sha256": sha(forge), "at": "01.09.2026 00:00:00",
            "source": "doc-reviewer",     # даже с правдоподобным источником —
        }                                  # но БЕЗ поля sig
        jp = journal_path(forge)
        jp.parent.mkdir(parents=True, exist_ok=True)
        with open(jp, "a", encoding="utf-8") as f:
            f.write(json.dumps(forged, ensure_ascii=False) + "\n")
        problems = check(forge)
        assert problems, "дописанная строка без подписи открыла сборку .docx"
        assert "без проверяемой подписи" in problems[0], problems
        # Подделанная подпись (случайный hex) тоже не проходит.
        forged["sig"] = "deadbeef" * 8
        with open(jp, "w", encoding="utf-8") as f:
            f.write(json.dumps(forged, ensure_ascii=False) + "\n")
        assert check(forge), "подделанная подпись принята как настоящая"
        # А честная запись через record() к сборке пускает (новая редакция — новый sha).
        forge.write_text("# Иск\n\nОдобренный судом текст, редакция 2.\n", encoding="utf-8")
        assert rec(forge, READY, 1) is not None, \
            "неподписанная строка сожгла номер законного раунда"
        assert not check(forge), f"честно подписанный вердикт не пустил сборку: {check(forge)}"
        with open(journal_path(forge), "a", encoding="utf-8") as journal:
            journal.write(json.dumps({**forged, "round": 999,
                                      "verdict": "КРИТИЧЕСКИЕ ОШИБКИ",
                                      "sha256": sha(forge), "sig": "bad"},
                                     ensure_ascii=False) + "\n")
        assert not check(forge), "неподписанная строка отравила допуск к сборке"

        # СЦЕНАРИЙ 2 — источник равен составителю (self-record): генератор не судит себя.
        selfrec = d / "selfrec.md"
        selfrec.write_text("# Иск\n\nТекст без брака.\n", encoding="utf-8")
        assert record(selfrec, READY, 1, source="doc-drafter") is None, \
            "self-record (source=doc-drafter) записан"
        assert record(selfrec, READY, 1, source="Сперанский") is None, \
            "self-record по имени составителя записан"
        assert record(selfrec, READY, 1, source="") is None, \
            "вердикт без источника записан"
        assert record(selfrec, READY, 1, source="unknown-agent") is None, \
            "неизвестный источник принят за рецензента"
        assert rec(selfrec, READY, 1, source="doc-reviewer") is not None, \
            "вердикт рецензента отбит зря"

        # СЦЕНАРИЙ 3 — журнал по старому адресу (в _working) обязан дать предупреждение.
        legok = d / "legacy.md"
        legok.write_text("# Иск\n\nСтарая редакция.\n", encoding="utf-8")
        lp = legacy_journal_path(legok)
        lp.parent.mkdir(parents=True, exist_ok=True)
        old_entry = {"document": "legacy.md", "path": str(legok), "round": 1,
                     "verdict": "ТРЕБУЕТ ПРАВОК", "sha256": sha(legok),
                     "at": "25.08.2026 00:00:00"}       # старый формат: без source/sig
        with open(lp, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_entry, ensure_ascii=False) + "\n")
        _warned_legacy.clear()
        buf = io.StringIO()
        with redirect_stderr(buf):
            hist = history(legok)
        assert hist, "старый журнал не прочитан как запасной"
        assert "СТАРОМУ адресу" in buf.getvalue(), \
            "чтение старого адреса прошло без предупреждения"
        # И старое неподписанное одобрение сборку НЕ открывает.
        legok.write_text("# Иск\n\nСтарая редакция готова.\n", encoding="utf-8")
        lp.write_text(json.dumps({**old_entry, "round": 2, "verdict": READY,
                                  "sha256": sha(legok)}, ensure_ascii=False) + "\n",
                      encoding="utf-8")
        _warned_legacy.clear()
        with redirect_stderr(io.StringIO()):
            assert check(legok), "старое неподписанное одобрение открыло сборку"
        # Но живое дело не запирается навсегда: рецензент может подписать тот же
        # самый отпечаток новым форматом. SHA не меняется и не ослабляется.
        assert rec(legok, READY, 1) is not None, \
            "старую неподписанную редакцию нельзя переосвидетельствовать"
        with redirect_stderr(io.StringIO()):
            assert not check(legok), "переосвидетельствованный старый журнал не принят"

        # Два процесса стартуют один и тот же раунд. Лок держит участок от чтения
        # истории до fsync: записывается ровно один r1, второй видит тот же sha.
        race = d / "race.md"
        race.write_text("# Иск\n\nПараллельная редакция.\n", encoding="utf-8")
        approve(race)
        cmd = [sys.executable, str(Path(__file__).resolve()), str(race), "--record",
               "--verdict", "ТРЕБУЕТ ПРАВОК", "--source", "doc-reviewer"]
        ps = [subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=os.environ.copy()) for _ in range(2)]
        codes = sorted(p.wait(timeout=30) for p in ps)
        assert codes == [0, 1], f"лок раунда не развел параллельные записи: {codes}"
        assert [e["round"] for e in history(race)] == [1], \
            "параллельная запись создала два одинаковых номера"

    print("selftest: preflight по SHA до раунда, правка отзывает preflight, "
          "журнал вне _working (D03), отказ без вердикта, отказ на ТРЕБУЕТ ПРАВОК, "
          "детект правки после одобрения, новый раунд, возврат к одобренному тексту, "
          "humanizer fail-closed, формат перед финальным вердиктом, "
          "валюты «р.» и евро, скобки-реквизиты не дыры, отзыв вердикта, "
          "машинный-раунд·лок·лимит-конфиг·READY-клапан, "
          "ПОДПИСЬ·ИСТОЧНИК·СТАРЫЙ-АДРЕС — ок")
    return 0


def main():
    ap = obs.parser("Вердикт Кони, привязанный к редакции.")
    ap.add_argument("md", nargs="?", help="черновик .md")
    ap.add_argument("--preflight", metavar="FILE",
                    help="прогнать машинные гейты и записать результат по SHA-256")
    ap.add_argument("--scan", action="store_true", help="гейт humanizer-legal по .md")
    ap.add_argument("--record", action="store_true", help="записать вердикт")
    ap.add_argument("--verdict", help="текст вердикта (с --record)")
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help="кто выписал вердикт (с --record); обязателен явно либо через "
                         "env THEMIS_ACTOR. Источник-составитель отклоняется")
    ap.add_argument("-r", "--round", type=int, default=None,
                    help="проверочное ожидание номера; номер всегда считает машина")
    ap.add_argument("--check", action="store_true", help="можно ли собирать .docx")
    ap.add_argument("--log", action="store_true", help="история вердиктов")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.preflight:
        if a.md or a.record or a.check or a.log or a.scan:
            print("--preflight — отдельный режим; передать только путь после флага",
                  file=sys.stderr)
            return obs.KOD_NE_RABOTAL
        return preflight(a.preflight)
    if not a.md:
        ap.print_help()
        return obs.KOD_NE_RABOTAL

    if a.scan:
        blockers = scan(a.md)
        if blockers is None:
            prichina = "скрипт скилла не найден" if not SCAN.is_file() \
                else "скрипт отказал или не отработал (причина — в stderr выше)"
            print(f"❌ humanizer-legal: {prichina} ({SCAN}) — проверка "
                  "не выполнена, fail-closed.")
            return obs.KOD_OSHIBKA
        if blockers:
            print(f"❌ humanizer-legal: сработали блокирующие категории — {', '.join(blockers)}")
            print(f"   Прогнать скилл humanizer-legal по тексту и повторить.")
            print(f"   Полный отчет: bash {SCAN} {a.md}")
            return obs.KOD_OSHIBKA
        print("✓ humanizer-legal: следов автогенерации и незаполненных плейсхолдеров нет")
        return obs.KOD_OK
    if a.record:
        if not a.verdict:
            print("--record требует --verdict", file=sys.stderr)
            return obs.KOD_NE_RABOTAL
        try:
            e = record(a.md, a.verdict, a.round, a.source)
        except RoundLimitExceeded as ex:
            print(str(ex), file=sys.stderr)
            return obs.KOD_STOP
        except RoundConfigError as ex:
            print(f"⛔ ВЕРДИКТ НЕ ЗАПИСАН — {ex}", file=sys.stderr)
            return obs.KOD_OSHIBKA
        if e is None:
            return obs.KOD_OSHIBKA
        print(f"вердикт записан: {e['document']} r{e['round']} «{e['verdict']}» "
              f"отпечаток {e['sha256'][:12]}…")
        return obs.KOD_OK
    if a.log:
        h = history(a.md)
        if not h:
            print("вердиктов нет")
            return obs.KOD_OSHIBKA
        for e in h:
            print(f"  {e['at']}  r{e['round']}  {e['sha256'][:12]}…  {e['verdict']}")
        return obs.KOD_OK
    if a.check:
        problems = check(a.md)
        if problems:
            print("⛔ СБОРКА .docx ЗАПРЕЩЕНА")
            for p in problems:
                print("  · " + p)
            return obs.KOD_OSHIBKA
        print(f"✓ редакция одобрена Кони — сборка .docx разрешена")
        return obs.KOD_OK
    ap.print_help()
    return obs.KOD_NE_RABOTAL


if __name__ == "__main__":
    obs.zavershit(main)
