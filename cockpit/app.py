#!/usr/bin/env python3
"""
Femida Cockpit — тонкая оболочка над существующим протоколом Фемида.

Принцип: кокпит НЕ меняет pipeline. Он только:
  1. принимает drag-drop документы → кладёт в ~/Desktop/inbox/ (Femida inbox)
  2. запускает протокол Themis (claude -p в корне проекта)
  3. стримит работу офиса (агенты-юристы) в браузер через SSE,
     строя реплики из реального audit.log + stdout прогона.

Всё локально на этом Mac. Только localhost. Без секретов.
Запуск:  python3 app.py   →   http://localhost:8800
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse

# ── Пути (фиксированы на систему Фемида, НЕ Мнемозина) ──────────────────────
HOME = Path.home()
INBOX = Path(os.environ.get("THEMIS_INBOX") or HOME / "Desktop" / "inbox")  # НЕ _ВХОДЯЩИЕ
import shutil as _shutil
# Корень проекта Themis: env THEMIS_HOME или каталог на уровень выше cockpit/
LEGAL = Path(os.environ.get("THEMIS_HOME") or Path(__file__).resolve().parent.parent)
AUDIT_LOG = LEGAL / "audit.log"
CASES_INDEX = LEGAL / "cases" / "_index.md"
CLAUDE_BIN = Path(_shutil.which("claude") or (HOME / ".local" / "bin" / "claude"))
COCKPIT = LEGAL / "cockpit"
STATIC = COCKPIT / "static"

INBOX.mkdir(parents=True, exist_ok=True)

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    yield
    # при остановке сервера не оставляем осиротевший claude-прогон
    proc = RUN.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()


app = FastAPI(title="Femida Cockpit", lifespan=_lifespan)

# ── Сторож панели (этап 6) ──────────────────────────────────────────────────
# Панель запускает конвейер по делам, открывает файлы и принимает загрузки.
# Пока она слушает петлю на Маке владельца, её защищает сама машина; как только
# она окажется на сервере, любой запрос снаружи станет командой Фемиде.
# Отсюда три вещи: секрет, ограничение частоты и журнал доступа.
#
# Секрет живёт в ~/.secrets/themis-panel.env (переменная THEMIS_PANEL_TOKEN);
# в коде и git — только ИМЯ переменной. Секрет не задан = локальный режим:
# аутентификации нет, но и слушать не-петлевой интерфейс панель откажется.
import hmac as _hmac
from collections import deque as _deque

PANEL_TOKEN = (os.environ.get("THEMIS_PANEL_TOKEN") or "").strip()
ACCESS_LOG = Path(os.environ.get("THEMIS_ACCESS_LOG") or (LEGAL / "access.log"))
COOKIE_NAME = "X-Themis-Token"
# Маршруты, которые ЧТО-ТО ДЕЛАЮТ: запускают прогон, пишут на диск, открывают файл.
MUTATING = ("/api/task", "/api/run", "/api/new-case", "/api/learn-redline",
            "/api/open", "/api/upload")
_HITS: dict = {}
# Адрес клиента — сокет, а не заголовок. За обратным прокси uvicorn подставляет
# X-Forwarded-For в scope, и заголовок начинает выглядеть правдой: клиент меняет
# фальшивый адрес на каждом запросе и получает НОВОЕ ведро лимита, а в журнале
# остаётся выдуманный след. Доверяем заголовку только когда прокси назван явно
# (THEMIS_TRUSTED_PROXY); иначе заявленный адрес помечается и делит одно ведро.
TRUSTED_PROXY = (os.environ.get("THEMIS_TRUSTED_PROXY") or "").strip()


def _client_of(request) -> str:
    peer = request.client.host if request.client else "?"
    zayavlen = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not zayavlen or TRUSTED_PROXY:
        return peer
    return f"{zayavlen}(заявлен)"


def _rate_key(request) -> str:
    """Ключ ведра. Непроверяемый адрес — одно общее ведро на всех: подделка
    заголовка не должна умножать лимит."""
    client = _client_of(request)
    return "заявленные" if client.endswith("(заявлен)") else client


def _rate_ok(client: str) -> bool:
    """Скользящее окно в минуту на клиента. Порог — THEMIS_RATE_LIMIT."""
    limit = _limit("THEMIS_RATE_LIMIT", 60)
    now = time.time()
    q = _HITS.setdefault(client, _deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _audit(method: str, path: str, client: str, dopusk: str) -> None:
    """Журнал доступа: кто и куда стучался. БЕЗ секрета и БЕЗ содержимого запроса —
    журнал читают чаще, чем дела, и он не должен стать вторым местом хранения тайны."""
    line = f"{time.strftime('%d.%m.%Y %H:%M:%S')}\t{method}\t{path}\t{client}\t{dopusk}\n"
    try:
        ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ACCESS_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass          # журнал не должен ронять панель, но и молчать о себе не будет


@app.middleware("http")
async def strazh(request, call_next):
    path = request.url.path
    mutating = any(path.startswith(p) for p in MUTATING)
    client = _client_of(request)
    if PANEL_TOKEN and (path.startswith("/api/") or path.startswith("/miniapp")):
        given = (request.headers.get("x-themis-token")
                 or request.cookies.get(COOKIE_NAME) or "")
        if not _hmac.compare_digest(given, PANEL_TOKEN):
            _audit(request.method, path, client, "401")
            return JSONResponse({"ok": False, "error": "нужен токен панели"}, status_code=401)
    if mutating:
        if not _rate_ok(_rate_key(request)):
            _audit(request.method, path, client, "429")
            return JSONResponse({"ok": False, "error": "слишком часто, подождите минуту"},
                                status_code=429)
        _audit(request.method, path, client, "ok")
    return await call_next(request)


@app.get("/login")
def login(token: str = ""):
    """Вход по ссылке: юрист открывает панель с токеном один раз, дальше cookie
    носит браузер. Заголовки руками никто вводить не будет."""
    if not PANEL_TOKEN:
        return JSONResponse({"ok": True, "error": "токен не задан — локальный режим"})
    if not _hmac.compare_digest(token.strip(), PANEL_TOKEN):
        return JSONResponse({"ok": False, "error": "неверный токен"}, status_code=401)
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE_NAME, PANEL_TOKEN, httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return r

# Состояние текущего прогона (один за раз — high-stakes юр-логика)
RUN: dict = {"active": False, "started": None, "proc": None}

# Диалог офиса — источник истины на сервере (переживает перезагрузку страницы)
MSGS: list = []          # [{id, role: me|sys|agent, agent, text}]
_MID = {"n": 0}

# Персист диалога на диск — переживает РЕСТАРТ сервера, не только перезагрузку страницы.
STATE_FILE = COCKPIT / ".state.json"


def _save_state() -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"msgs": MSGS, "mid": _MID["n"]}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def _load_state() -> None:
    try:
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            MSGS.extend(d.get("msgs", []))
            _MID["n"] = int(d.get("mid", 0))
    except (OSError, ValueError, TypeError):
        pass


_load_state()


def push(role: str, text: str, person: dict | None = None, extra: dict | None = None) -> None:
    text = (text or "").strip()
    if not text:
        return
    _MID["n"] += 1
    m = {"id": _MID["n"], "role": role, "text": text}
    if person:
        m.update(face=person["face"], name=person["name"],
                 prole=person["role"], station=person["station"])
    if extra:
        m.update(extra)
    MSGS.append(m)
    if len(MSGS) > 600:
        del MSGS[:200]
    _save_state()


import threading
import collections

# Очередь реплик с темпом: claude отдаёт ассистент-блок из многих строк РАЗОМ.
# Если пушить всё сразу — чат валится пачкой. Кладём в очередь и выпускаем по одной
# с паузой → диалог течёт последовательно, как живой чат, между репликами «печатает…».
_PENDING: "collections.deque" = collections.deque()
_PLOCK = threading.Lock()


def _recent_texts(n: int = 20) -> list:
    with _PLOCK:
        pend = [t for (_r, t, _p, _e) in _PENDING]
    return [m["text"] for m in MSGS[-n:]] + pend


def _enqueue(role: str, text: str, person: dict | None = None, extra: dict | None = None) -> None:
    """Поставить реплику в очередь показа (дедуп против недавних и ждущих)."""
    text = (text or "").strip()
    if not text or text in _recent_texts(20):
        return
    with _PLOCK:
        _PENDING.append((role, text, person, extra))


def _pending_count() -> int:
    with _PLOCK:
        return len(_PENDING)


def _drain_pending() -> None:
    """Дождаться, пока очередь опустеет — финал идёт последним."""
    while _pending_count() > 0:
        time.sleep(0.2)


def _drainer() -> None:
    while True:
        item = None
        with _PLOCK:
            if _PENDING:
                item = _PENDING.popleft()
        if item is None:
            time.sleep(0.15)
            continue
        push(*item)
        # ритм: при заторе быстрее, иначе спокойный темп живой речи
        time.sleep(0.5 if _pending_count() > 6 else 0.9)


threading.Thread(target=_drainer, daemon=True).start()

# ── Офис: маппинг реальных агентов Фемиды → персонажи ленты ─────────────────
# Ключи — маркеры/имена что встречаются в audit.log и выводе протокола.
OFFICE = [
    {"id": "gruzenberg",  "name": "Грузенберг",   "role": "Канцелярия",   "step": 0,
     "markers": ["inbox-triage", "INBOX", "00_intake", "Грузенберг"]},
    {"id": "lokhvitsky",  "name": "Лохвицкий",    "role": "Сортировка",   "step": 0,
     "markers": ["case-sorter", "Лохвицкий", "раскладыва"]},
    {"id": "meyer",       "name": "Мейер",        "role": "Картирование", "step": 1,
     "markers": ["case-mapper", "Мейер", "Картир"]},
    {"id": "readers",     "name": "Читатели",     "role": "Чтение дела",  "step": 1,
     "members": [{"id": "pokrovsky", "name": "Покровский"}, {"id": "holmsten", "name": "Гольмстен"}, {"id": "burinsky", "name": "Буринский"}],
     "markers": ["Покровский", "Гольмстен", "Буринский", "docx-reader", "pdf-reader", "image-reader", "OCR"]},
    {"id": "shershenevich","name": "Шершеневич",  "role": "Сверка карты", "step": 1,
     "markers": ["case-reconciler", "Шершеневич", "КАРТА ГОТОВА"]},
    {"id": "hunters",     "name": "Охотники",     "role": "Поиск практики","step": 2,
     "members": [{"id": "spasovich", "name": "Спасович"}, {"id": "karabchevsky", "name": "Карабчевский"}, {"id": "plevako", "name": "Плевако"}],
     "markers": ["Спасович", "Карабчевский", "Плевако", "practice-hunter", "hunter_", "СОВЕТ ЗАВЕРШ"]},
    {"id": "areopag",     "name": "Ареопаг",      "role": "Совет",        "step": 3,
     "members": [{"id": "muravyov", "name": "Муравьев"}, {"id": "maklakov", "name": "Маклаков"}, {"id": "foynitsky", "name": "Фойницкий"}, {"id": "vladimirov", "name": "Владимиров"}, {"id": "urusov", "name": "Урусов"}],
     "markers": ["council", "Урусов", "Муравьев", "Маклаков", "Фойницкий", "Владимиров", "СОГЛАСОВАНО"]},
    {"id": "speransky",   "name": "Сперанский",   "role": "Документ",     "step": 4,
     "markers": ["doc-drafter", "Сперанский", ".agent/drafts", "черновик"]},
    {"id": "koni",        "name": "Кони",         "role": "Проверка",     "step": 5,
     "markers": ["doc-reviewer", "Кони", "ГОТОВ К ПОДАЧЕ", "ТРЕБУЕТ ПРАВОК"]},
]


# Реестр ВСЕХ говорящих (станции + члены групп) — чтобы отвечал конкретный юрист
PEOPLE: list = []
for _a in OFFICE:
    PEOPLE.append({"id": _a["id"], "name": _a["name"], "role": _a["role"], "station": _a["id"], "face": _a["id"]})
    for _m in _a.get("members", []):
        PEOPLE.append({"id": _m["id"], "name": _m["name"], "role": _a["role"], "station": _a["id"], "face": _m["id"]})
# имя → person (сначала длинные имена, чтобы «Карабчевский» не ловился как «...ский»)
# Фемида — ведущая/оркестратор (не станция релея), своё лицо
PEOPLE.append({"id": "femida", "name": "Фемида", "role": "Богиня Справедливости", "station": None, "face": "femida"})
NAME2PERSON = {p["name"].lower(): p for p in PEOPLE}
PEOPLE_BY_ID = {p["id"]: p for p in PEOPLE}
_NAMES_SORTED = sorted(NAME2PERSON, key=len, reverse=True)

import re as _re
_PREFIX = _re.compile(r"^\s*[\[\(<]?\s*([А-ЯЁ][а-яё]+)\s*[\]\)>:.,—–-]+\s*")
# техно-строки (не речь): пути, файлы, команды, разметка, чек-листы
_TECH = _re.compile(r"\.py\b|\.docx|\.md\b|create_docx|колонтитул|JUSTIFY|Times New Roman|00_intake|\\.agent/drafts|cases/|приложени[ея]\s*№|МЗ-|```|knowledge-map|practice\.md|positions\.md|\bWrite\b|директори|запис[ьи] файл|повторяю запис|сохран(ил|яю) файл|разреш[иь]|висят в очеред|в очеред|жму один раз|^\s*[-*]\s+[a-zа-я]|^#{1,6}\s|^\s*\d+\.\s"
    # статусы-маркеры шагов, токен-отчёт, имена инструментов, служебка — НЕ речь:
    r"|^\s*\*{0,2}\s*✓?\s*Шаг\s*\d|Шаг\s*\d+\s*завершён|Отчёт по токен|\bтокен"
    r"|^\s*\||SendMessage|Реестр агентов|хрупкая семёрка|журнал дела|лог сесси"
    r"|unsorted|Перехожу к Протоколу|пишу лог", _re.IGNORECASE)

_SEP = _re.compile(r"^[-*=_~#]{2,}\s*$")  # строки-сепараторы: --- *** === ###


def _strip_md(s: str) -> str:
    """Убрать markdown из чат-реплики — это живая речь, не документ."""
    s = _re.sub(r"\*\*(.+?)\*\*", r"\1", s)   # **жирный** → жирный
    s = _re.sub(r"__(.+?)__", r"\1", s)
    s = _re.sub(r"\*(.+?)\*", r"\1", s)        # *курсив* → курсив
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = _re.sub(r"^\s*#{1,6}\s*", "", s)       # ## заголовок → текст
    s = _re.sub(r"^\s*[-*]\s+", "", s)         # «- пункт» → «пункт» (ASCII-маркер)
    return s.strip()


def speaker_prefix(line: str):
    """ТОЛЬКО явный префикс «[Имя] …». Иначе (None, '') — строка не речь, глушим."""
    m = _PREFIX.match(line)
    if m:
        nm = m.group(1).lower()
        if nm in NAME2PERSON:
            return NAME2PERSON[nm], line[m.end():].strip()
    return None, ""


def speaker_of(line: str):
    """Префикс «[Имя] …» или имя в тексте. Вернуть (person|None, чистый_текст)."""
    m = _PREFIX.match(line)
    if m:
        nm = m.group(1).lower()
        if nm in NAME2PERSON:
            return NAME2PERSON[nm], line[m.end():].strip()
    low = line.lower()
    for nm in _NAMES_SORTED:
        if nm in low:
            return NAME2PERSON[nm], line.strip()
    return None, line.strip()


def classify(line: str):
    p, _ = speaker_of(line)
    return p["station"] if p else None


# ── API ─────────────────────────────────────────────────────────────────────
# Оболочка читается с диска ОДИН раз при старте — не блокируем хендлер диском
# на каждом запросе. Изменил index.html → перезапусти сервер (start.command и так бьёт порт).
_INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(
        _INDEX_HTML,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


# Статика стабильна по содержимому — даём браузеру кэшировать сутки.
# Это убирает повторную загрузку гербов/лиц при перезагрузке страницы.
_CACHE = {"Cache-Control": "public, max-age=86400"}


@app.get("/logo.png")
def logo() -> FileResponse:
    return FileResponse(STATIC / "logo.png", media_type="image/png", headers=_CACHE)


@app.get("/hero-crest.png")
def hero() -> FileResponse:
    return FileResponse(STATIC / "hero-crest.png", media_type="image/png", headers=_CACHE)


@app.get("/logo-white.svg")
def logo_white() -> FileResponse:
    return FileResponse(STATIC / "logo-white.svg", media_type="image/svg+xml", headers=_CACHE)


@app.get("/crest.svg")
def crest() -> FileResponse:
    return FileResponse(STATIC / "crest.svg", media_type="image/svg+xml", headers=_CACHE)


@app.get("/api/office")
def office() -> JSONResponse:
    return JSONResponse(OFFICE)


@app.get("/api/people")
def people() -> JSONResponse:
    return JSONResponse(PEOPLE)


@app.get("/api/inbox")
def inbox() -> JSONResponse:
    items = []
    for f in sorted(INBOX.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            items.append({"name": f.name, "size": f.stat().st_size,
                          "ext": f.suffix.lower().lstrip(".")})
    return JSONResponse(items)


CASES_DIR = LEGAL / "cases"

import sys as _sys
_sys.path.insert(0, str(LEGAL / "scripts"))
import case_paths as _cp   # контракт раскладки: где готовое, где кухня


def _initials(fio: str) -> str:
    """«Иванов Иван Иванович» → «Иванов И.И.»"""
    parts = fio.split()
    if not parts:
        return fio
    fam = parts[0]
    ini = "".join(f"{p[0]}." for p in parts[1:3] if p)
    return f"{fam} {ini}".strip()


def _client_name(folder: Path) -> str:
    """ФИО клиента из _client.md (первый H1 или поле ФИО). Фолбэк — slug."""
    cm = folder / "_client.md"
    if cm.exists():
        for ln in cm.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                # шаблонный H1 «Профиль: Фамилия И.О.» — берём часть после двоеточия
                if title.lower().startswith("профиль:"):
                    return title.split(":", 1)[1].strip()
                return _initials(title)
            if "ФИО:" in s:
                raw = s.split("ФИО:")[1].strip(" *|")
                # если уже в форме «Фамилия И.О.» — не укорачивать повторно
                return raw if _re.match(r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.", raw) else _initials(raw)
    return folder.name


def _visible_docx(base: Path):
    """Документы, которые видит юрист: только то, что лежит в папке готовых.

    Позитивный фильтр (этап 3, 19.08.2026): показываем `GOTOVO/`, а не «всё, кроме
    служебного». Негативный список надо было пополнять на каждый новый служебный
    каталог, и любой пропущенный протекал к юристу как готовый документ.
    Локи Word отсекаются отдельно: они лежат рядом с самим документом.
    """
    for f in base.rglob("*.docx"):
        if f.name.startswith("~$"):
            continue
        if _cp.READY not in f.relative_to(base).parts[:-1]:
            continue
        yield f


def _case_title(folder: Path) -> str:
    """Русское название дела из _case.md (первый H1). Фолбэк — slug."""
    cm = folder / "_case.md"
    if cm.exists():
        for ln in cm.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if s.startswith("# "):
                return s[2:].strip()
    return folder.name


@app.get("/api/clients")
def clients() -> JSONResponse:
    """Клиенты: slug + «Фамилия И.О.» (кириллица) + число дел."""
    out = []
    if CASES_DIR.exists():
        for d in sorted(CASES_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith(("_", ".")):
                cases = [x for x in d.iterdir()
                         if x.is_dir() and not x.name.startswith((".", "_"))]
                out.append({"slug": d.name, "name": _client_name(d), "cases": len(cases)})
    out.sort(key=lambda c: c["name"])
    return JSONResponse(out)


@app.get("/api/client/{slug}/cases")
def client_cases(slug: str) -> JSONResponse:
    """Дела клиента: slug + русское название + число готовых docx."""
    base = (CASES_DIR / slug).resolve()
    if base.parent != CASES_DIR.resolve() or not base.exists():
        return JSONResponse([])
    out = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")):
            docx = list(_visible_docx(d))
            out.append({"slug": d.name, "title": _case_title(d), "docx": len(docx)})
    return JSONResponse(out)


@app.get("/api/case-docs")
def case_docs(client: str, case: str) -> JSONResponse:
    """Только ГОТОВЫЕ .docx внутри дела — остальное не показываем."""
    base = (CASES_DIR / client / case).resolve()
    if CASES_DIR.resolve() not in base.parents or not base.exists():
        return JSONResponse([])
    docs = []
    for f in sorted(_visible_docx(base), key=lambda p: p.stat().st_mtime, reverse=True):
        docs.append({
            "name": f.name, "path": str(f),
            "rel": str(f.relative_to(base)),
            "size": f.stat().st_size, "mtime": int(f.stat().st_mtime),
        })
    return JSONResponse(docs)


@app.post("/api/open")
def open_file(payload: dict) -> JSONResponse:
    """Открыть готовый файл в системе (Finder/Word). Только внутри cases/."""
    p = Path(payload.get("path", "")).resolve()
    if CASES_DIR.resolve() not in p.parents:
        return JSONResponse({"ok": False, "error": "вне cases/"}, status_code=400)
    if not p.exists():
        return JSONResponse({"ok": False, "error": "нет файла"}, status_code=404)
    subprocess.Popen(["open", str(p)])
    return JSONResponse({"ok": True})


# ── Этап 8: то, куда ведёт ссылка из Telegram ───────────────────────────────
# Бот отправляет владельцу ссылку, а не файл: всё, что попало в Telegram,
# разглашено Telegram. В ссылке нет ни имени дела, ни токена — только непрозрачный
# идентификатор; узнаёт владельца панель, на своей стороне и своим входом.
DOC_LINKS = Path(os.environ.get("THEMIS_DOC_LINKS") or (HOME / ".themis" / "doc-links.json"))
_HEARING_DATE = _re.compile(r"^(?:(\d{2})-(\d{2})-(\d{4})|(\d{4})-(\d{2})-(\d{2}))[_-]")


def _link_path(ident: str) -> Path | None:
    """Путь по идентификатору. Карта ссылок — на диске у владельца, не в URL."""
    if not _re.match(r"^[a-z]{8,32}$", ident or ""):
        return None
    try:
        karta = json.loads(DOC_LINKS.read_text(encoding="utf-8")).get("links") or {}
    except (OSError, ValueError, AttributeError):
        return None
    rel = karta.get(ident)
    return (LEGAL / rel).resolve() if rel else None


@app.get("/api/doc", response_model=None)
def doc_by_id(id: str = ""):
    p = _link_path(id)
    if p is None:
        return JSONResponse({"ok": False, "error": "нет такой ссылки"}, status_code=404)
    # Идентификатор пришёл снаружи: путь из карты всё равно обязан лежать внутри
    # дел — иначе подменённая карта откроет любой файл машины.
    if CASES_DIR.resolve() not in p.parents or not p.is_file():
        return JSONResponse({"ok": False, "error": "нет такой ссылки"}, status_code=404)
    return FileResponse(str(p), filename=p.name)


@app.get("/api/hearings")
def hearings() -> JSONResponse:
    """Ближайшие события по всем делам: дата, дело, что назначено."""
    segodnya = time.strftime("%Y-%m-%d")
    out = []
    if CASES_DIR.exists():
        for ev in CASES_DIR.glob("*/*/02_hearings/*"):
            if not ev.is_dir():
                continue
            m = _HEARING_DATE.match(ev.name)
            if not m:
                continue
            d, mm, g, g2, m2, d2 = m.groups()
            iso = f"{g}-{mm}-{d}" if g else f"{g2}-{m2}-{d2}"
            if iso < segodnya:
                continue
            out.append({"date": iso, "client": ev.parts[-4], "case": ev.parts[-3],
                        "event": ev.name.split("_", 1)[-1].replace("-", " ")})
    out.sort(key=lambda x: x["date"])
    return JSONResponse(out[:20])


MINIAPP = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Фемида</title><style>
:root{--fon:#0f1219;--kart:#171b24;--text:#e8e4dc;--tikho:#8b8f99;--zoloto:#c6a460}
*{box-sizing:border-box}body{margin:0;background:var(--fon);color:var(--text);
font:16px/1.45 -apple-system,system-ui,sans-serif;padding:16px 14px 40px}
h1{font-size:19px;margin:0 0 4px;letter-spacing:.02em}
.tikho{color:var(--tikho);font-size:13px}
.blok{margin-top:22px}.blok h2{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:var(--zoloto);margin:0 0 8px;font-weight:600}
.kart{background:var(--kart);border-radius:12px;padding:12px 14px;margin-bottom:8px}
.kart b{font-weight:600}.data{color:var(--zoloto);font-variant-numeric:tabular-nums}
button{width:100%;padding:13px;border:0;border-radius:11px;background:var(--zoloto);
color:#141414;font:600 15px/1 -apple-system,system-ui;margin-top:6px}
button.tihaya{background:#242a36;color:var(--text)}
a{color:var(--zoloto);text-decoration:none}
select{width:100%;padding:11px;border-radius:10px;background:#242a36;color:var(--text);
border:0;font-size:15px;margin-bottom:6px}
</style></head><body>
<h1>Фемида</h1><div class=tikho id=svodka>смотрю…</div>
<div class=blok id=sroki></div>
<div class=blok id=golos></div>
<div class=blok><h2>Дела</h2><div id=dela></div></div>
<div class=blok><h2>Сделать документ</h2>
<select id=vybor></select>
<button id=sdelat>Собрать документ по делу</button>
<div class=tikho id=itog style="margin-top:8px"></div></div>
<script>
const q=(s)=>document.querySelector(s), esc=(t)=>String(t).replace(/[<>&]/g,'');
const den=(iso)=>{const [g,m,d]=iso.split('-');return d+'.'+m+'.'+g};
async function dai(u,o){const r=await fetch(u,o);if(!r.ok)throw new Error(r.status);return r.json()}
(async()=>{
 try{
  const [kl,zas]=await Promise.all([dai('/api/clients'),dai('/api/hearings')]);
  q('#svodka').textContent='Доверителей: '+kl.length+'. Ближайших событий: '+zas.length+'.';
  q('#sroki').innerHTML='<h2>Ближайшее</h2>'+(zas.length?zas.slice(0,5).map(z=>
    '<div class=kart><span class=data>'+den(z.date)+'</span> — '+esc(z.event)+
    '<div class=tikho>'+esc(z.case)+'</div></div>').join(''):'<div class=kart>Пусто.</div>');
  const gol=await dai('/api/voice-queue').catch(()=>[]);
  if(gol.length)q('#golos').innerHTML='<h2>Надиктовано</h2>'+gol.slice(0,5).map(g=>
    '<div class=kart>'+esc(g.text)+'<div class=tikho>'+esc(g.kogda)+'</div></div>').join('');
  const dela=[];
  for(const k of kl){
    const c=await dai('/api/client/'+encodeURIComponent(k.slug)+'/cases');
    c.forEach(x=>dela.push({klient:k.slug, imya:k.name, delo:x.slug||x.name||x, obj:x}));
  }
  q('#dela').innerHTML=dela.map(d=>'<div class=kart><b>'+esc(d.imya)+'</b>'+
    '<div class=tikho>'+esc(d.delo)+'</div></div>').join('')||'<div class=kart>Дел нет.</div>';
  q('#vybor').innerHTML=dela.map(d=>'<option value="'+esc(d.klient)+'/'+esc(d.delo)+'">'+
    esc(d.imya)+' — '+esc(d.delo)+'</option>').join('');
 }catch(e){q('#svodka').textContent='Панель не отвечает. Войдите заново.'}
})();
q('#sdelat').onclick=async()=>{
 const v=q('#vybor').value; if(!v)return;
 if(!confirm('Собрать документ по делу '+v+'?'))return;
 q('#itog').textContent='Отдал в работу. Напишу, когда будет готово.';
 try{await dai('/api/task',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({text:'Собрать документ по делу '+v})});}
 catch(e){q('#itog').textContent='Не отдалось. Откройте панель на компьютере.'}
};
</script></body></html>"""


VOICE_QUEUE = Path(os.environ.get("THEMIS_BOT_QUEUE") or (HOME / ".themis" / "voice-queue.jsonl"))


@app.get("/api/voice-queue")
def voice_queue() -> JSONResponse:
    """Надиктованное боту. Расшифровка живёт только здесь, внутри сети: в чат она
    не возвращается, потому что в надиктовке звучат фамилии и суммы."""
    out = []
    try:
        for line in VOICE_QUEUE.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            out.append({"kogda": d.get("kogda", ""), "text": (d.get("text") or "")[:400]})
    except OSError:
        pass
    return JSONResponse(list(reversed(out)))


@app.get("/miniapp", response_class=HTMLResponse)
def miniapp() -> HTMLResponse:
    """Пульт с телефона: дела, ближайшие события, кнопка «собрать документ».

    Живёт ВНУТРИ приватной сети и закрыт тем же токеном, что и остальная панель.
    В Telegram уходит только ссылка сюда — не содержимое."""
    return HTMLResponse(MINIAPP)


@app.get("/faces/{fid}.png", response_model=None)
def face(fid: str):
    if not _re.match(r"^[a-zA-Z0-9_-]+$", fid):  # без путей/точек → нет traversal
        return JSONResponse({"error": "bad id"}, status_code=400)
    f = STATIC / "faces" / f"{fid}.png"
    if f.exists():
        return FileResponse(f, media_type="image/png", headers=_CACHE)
    return JSONResponse({"error": "no face"}, status_code=404)


# ── Общие куски промптов (DRY — раньше дублировались в трёх эндпоинтах) ──────
# Правила живого диалога юристов в чате.
CHAT_RULES = (
    "Общение между агентами — скиллами /caveman + /humanizer: коротко, но живым "
    "человеческим языком (не робот, не рублёный телеграф), тепло и в характере, без эмодзи. "
    "Каждую реплику начинай с имени говорящего юриста в квадратных скобках: [Грузенберг], "
    "[Плевако], [Кони] и т.д. Каждая реплика — с новой строки. Если юристы спорят (особенно "
    "охотники Спасович/Карабчевский/Плевако или Ареопаг) — показывай их перепалку отдельными "
    "репликами, они возражают друг другу по имени. В чат пиши ТОЛЬКО живую человеческую речь "
    "юриста в его характере (по его душе из файла агента) — как реплики в мессенджере. "
    "Технические шаги, чек-листы, имена файлов, команды, колонтитулы, статусы НЕ выводи в чат — "
    "делай их молча. Это ЧАТ, живая речь — пиши ПРОСТЫМ текстом без markdown: НЕ ставь "
    "звёздочки для жирного (**текст**), решётки заголовков (#), горизонтальные черты (---), "
    "списки с маркерами, таблицы. Никаких отчётов по токенам и итоговых сводок в чат. "
    "Ведущий голос без имени — это Фемида, богиня правосудия. Говорит КАК богиня: "
    "веско, спокойно, с достоинством, образ держит — но КОРОТКО, без лишних слов и суеты. "
    "О себе — ТОЛЬКО в женском роде (услышала, решила, свела, приняла, рассудила; НИКОГДА «сам», "
    "«принял», «готов», «занят»). Юристов она сама избрала — лучших мастеров для служения правосудию, "
    "потому призывает и благодарит их с глубоким уважением, по имени-отчеству или «господин X», "
    "направляет твёрдо, но почтительно, как достойных избранников. "
    "Не описывай работу с папками/файлами/Write. Файлы сохраняются автоматически — НЕ проси "
    "разрешения на запись, не упоминай очередь файлов. Если нужен выбор пользователя — выведи ОДНОЙ "
    "строкой: «[Фемида] ВОПРОС: <короткий вопрос> || Вариант 1 || Вариант 2 || Вариант 3». "
    "Готовый документ покажи коротко: «[Сперанский] Готов отзыв» — сам файл подхватится в чат."
)

# Полный конвейер Шаг 0→5 по делу. Гонится без пауз до готового документа.
PIPELINE = (
    "Прогони полный Протокол Фемиды от Шага 0 до Шага 5 по этому делу, end-to-end, без пауз и без "
    "отдельной команды на продолжение: "
    "Шаг 0 — проверь knowledge/practice_index.md по категории дела. "
    "Шаг 0.5 — триаж трека (MICRO/FAST/FULL) по CLAUDE.md и объяви его строкой; дальше "
    "исполняй трек, а не самый тяжёлый вариант по умолчанию. "
    "Шаг 1 — инбокс отдай агенту inbox-triage: он сам отберёт из ~/Desktop/inbox/ ТОЛЬКО группу "
    "файлов этого дела (окно mtime плюс тематическая сверка) и перенесёт её в 00_intake. "
    "Забирать «все файлы» подряд запрещено: в инбоксе лежат материалы разных доверителей, и "
    "смешение дел — утечка персональных данных, а не неаккуратность. Затем case-mapper "
    "(Мейер + читатели + Шершеневич на FULL) → knowledge-map.md. "
    "Шаг 2 — практика по треку: FULL — три охотника плюс Ареопаг, FAST — один охотник и синтез, "
    "MICRO — только knowledge/practice_index.md → practice.md. "
    "Шаг 3 — позиция (position-council для L2/L3). "
    "Перед Шагом 4 — заморозка фактуры: открытые вопросы закрыты, строка ФАКТУРА ЗАМОРОЖЕНА "
    "в брифе; составитель запускается ОДИН раз, новые вводные вносятся Edit-патчем. "
    "Шаг 4 — документ (Сперанский). ПЕРЕД составлением Сперанский ОБЯЗАН прочитать "
    "knowledge/redlines.md (уроки из прежних правок доверителя) и применить усвоенные "
    "предпочтения. Затем → .md + .docx, каждое правовое утверждение с источником в тексте. "
    "Шаг 5 — проверка (Кони). "
    "Шаг 6 — самоконтроль (молча, не в чат): если в прогоне были ошибки, сбои или перезапуски "
    "агентов, предупреждения, пропуски маркеров — проведи разбор «причина → исправление → как "
    "не повторять» и занеси в cases/_logs/session_ДД-ММ-ГГГГ.md; если урок системный — также в "
    "knowledge/lessons-log.md. Это обязательная часть протокола. "
    "Соблюдай все маркеры-ворота. НЕ останавливайся и НЕ спрашивай разрешения продолжать между "
    "шагами — иди до готового документа сам. Останавливайся ВОПРОСом только если реально нужен "
    "выбор юриста по существу спора (а не по технике)."
)


# Самообучение по правкам доверителя: сравнить черновик (.md) с правленым (.docx) → уроки.
def _redline_prompt(doc_path: str) -> str:
    return (
        "Ты — Фемида, богиня правосудия. Доверитель открыл готовый документ, внёс СВОИ правки и "
        "сохранил. Научись на его правках, чтобы впредь их не повторять. " + CHAT_RULES + "\n\n"
        "ПРАВЛЕНЫЙ ДОКУМЕНТ (ПОСЛЕ): " + doc_path + "\n"
        "ТВОЙ ИСХОДНЫЙ ЧЕРНОВИК (ДО) — неизменяемый снимок: в подпапке `_baselines/` рядом, "
        "файл с тем же именем (`<каталог>/_baselines/<имя>.docx`). Если его нет — возьми "
        "соседний `.md` с тем же именем. Текст обоих .docx тяни через scripts/markdown_extract.py. "
        "Сравни ДО и ПОСЛЕ по ДВУМ осям. "
        "(1) СОДЕРЖАНИЕ: что доверитель убрал, добавил, переформулировал; какие реквизиты, нормы, "
        "структуру, тон, просительную часть поправил. "
        "(2) ФОРМАТИРОВАНИЕ (важно — тут бывают ошибки): шрифт и кегль, поля, абзацный отступ, "
        "межстрочный интервал, выравнивание (шапка справа, тело по ширине, подпись справа), "
        "расположение шапки/подписи, нумерация, пробелы и пустые строки. Сверься с "
        ".claude/skills/doc-drafter/DOCX_FORMATTING.md. "
        "По каждой оси выведи ПРАВИЛО на будущее (и ПОЧЕМУ). Дополни knowledge/redlines.md: каждый "
        "урок — конкретное правило в нужный раздел по категории (или в раздел «Форматирование»), "
        "в формате «- [дата · дело] правило». "
        "Если правка вскрыла системный огрех протокола — добавь урок и в "
        "knowledge/lessons-log.md. Документ доверителя НЕ меняй. "
        "В чат — кратко, живым голосом богини: что именно усвоила (1-3 реплики), без техники."
    )


def _emit_event(line: str) -> None:
    """Разобрать одну строку stream-json от claude -p → реплики в диалог."""
    line = line.strip()
    if not line:
        return
    try:
        j = json.loads(line)
    except Exception:
        return  # не-JSON служебное — игнор
    t = j.get("type")
    blocks = []
    if t == "assistant":
        blocks = [b.get("text", "") for b in (j.get("message", {}).get("content") or []) if b.get("type") == "text"]
    elif t == "result":
        # result дублирует уже стримленный assistant-текст (финальный блок) → не эмитим,
        # иначе итог+таблица задваиваются. Финал даёт pump после завершения процесса.
        return
    else:
        return
    femida = PEOPLE_BY_ID["femida"]
    last_person = None  # кто говорил выше в этом блоке — для продолжений-перечислений
    for txt in blocks:
        for raw in txt.splitlines():
            s = raw.strip()
            if not s or _SEP.match(s) or _TECH.search(s):
                continue
            person, clean = speaker_prefix(raw)
            if person is None:
                # продолжение реплики предыдущего юриста: перечисление с тире «—/–/•/-»
                # или строка со строчной буквы. Иначе — голос Фемиды (нарратор).
                if last_person is not None and _re.match(r"^\s*[—–•\-]|^\s*[а-яё]", s):
                    person, clean = last_person, s
                else:
                    person, clean = femida, s
            clean = _strip_md(clean)
            if not clean:
                continue
            last_person = person
            # вопрос с вариантами: «… || вар1 || вар2»
            if "||" in clean:
                parts = [p.strip() for p in clean.split("||")]
                q = parts[0].replace("ВОПРОС:", "").replace("ВОПРОС", "").strip(" :—-")
                opts = [p for p in parts[1:] if p]
                if q and opts:
                    _enqueue("agent", q, person, {"options": opts})
                    continue
            _enqueue("agent", clean, person)


def _docx_snapshot() -> dict:
    snap = {}
    if CASES_DIR.exists():
        for f in _visible_docx(CASES_DIR):
            try:
                snap[str(f)] = f.stat().st_mtime
            except OSError:
                pass
    return snap


def _start_run(prompt: str) -> bool:
    if RUN["active"]:
        return False
    # stream-json → живой вывод; --add-dir → inbox.
    # bypassPermissions: headless-прогон без человека за клавиатурой — Bash (python-роутер
    # markdown_extract.py для OCR/markitdown) и запись файлов идут без запроса одобрения.
    # acceptEdits НЕ годится: он разрешает только запись, но блокирует Bash → роутер падает
    # автоотказом, и любое дело с документом из inbox валится.
    # list-form без shell=True: нет шелл-слоя → нет любого риска инъекции из текста чата.
    cmd = [str(CLAUDE_BIN), "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions",
           "--add-dir", str(INBOX)]
    before = _docx_snapshot()
    proc = subprocess.Popen(cmd, cwd=str(LEGAL),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    RUN.update(active=True, started=time.time(), proc=proc)

    def pump() -> None:
        if proc.stdout is None:  # иначе RUN навсегда active при сбое запуска
            RUN["active"] = False
            push("agent", "Не удалось запустить прогон. Попробуйте снова.", PEOPLE_BY_ID["femida"])
            return
        for ln in proc.stdout:
            _emit_event(ln)
        proc.wait()
        _drain_pending()      # дать очереди показать все реплики, ПОТОМ финал
        RUN["active"] = False
        # новый/обновлённый .docx → карточка документа в чат
        femida = PEOPLE_BY_ID["femida"]
        after = _docx_snapshot()
        new = [p for p, mt in after.items() if before.get(p) != mt]
        new.sort(key=lambda p: after[p], reverse=True)
        if new:
            f = Path(new[0])
            push("agent", f"Документ готов: {f.name}", femida, {"doc": str(f)})
        # Честный финал: не врать «готово», если прогон оборвался лимитом/ошибкой.
        # Маркер лимита приходит обычной репликой в чат (result-блок claude) — ищем в хвосте.
        recent = " ".join(m["text"].lower() for m in MSGS[-8:])
        limited = any(p in recent for p in
                      ("session limit", "hit your", "usage limit", "rate limit", "лимит сесси"))
        if limited:
            push("agent", "Работа прервана: лимит сессии исчерпан. Шаги выполнены частично. "
                          "После сброса лимита запустите дело снова — продолжу с готовых файлов.", femida)
        elif proc.returncode not in (0, None):
            push("agent", "Работа прервалась, и я не довела дело до конца. "
                          "Запустите снова — продолжу с того, что уже готово.", femida)
        else:
            push("agent", "Дело отработано.", femida)

    import threading
    threading.Thread(target=pump, daemon=True).start()
    return True


@app.post("/api/task")
def task(payload: dict) -> JSONResponse:
    """Свободная задача из чата: «Новое дело: клиент X, сделать документ Y»."""
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "пусто"}, status_code=400)
    prompt = (
        "Ты — Фемида (юридический ассистент). Выполни задачу юриста строго по "
        "Протоколу Фемида, с нужными агентами и воротами. " + CHAT_RULES + " Документы humanizer.\n\n"
        f"ЗАДАЧА: {text}"
    )
    push("me", text)
    if not _start_run(prompt):
        push("agent", "Я сейчас занята делом. Подождите, пожалуйста.", PEOPLE_BY_ID["femida"])
        return JSONResponse({"ok": False, "error": "Прогон уже идёт"}, status_code=409)
    push("agent", "Приняла. Беру в работу.", PEOPLE_BY_ID["femida"])
    return JSONResponse({"ok": True})


# Лимиты загрузки — три оси. Один файл ограничивался и раньше, но без потолка на
# число файлов и на объём запроса перетащенная в окно папка забивала диск, а инбокс
# становился нечитаемым. Пороги вынесены в окружение: приёмка обязана проверять
# лимит, не гоняя сотни мегабайт.
def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


UPLOAD_MAX_BYTES = _limit("THEMIS_UPLOAD_MAX_BYTES", 50 * 1024 * 1024)
UPLOAD_MAX_FILES = _limit("THEMIS_UPLOAD_MAX_FILES", 30)
UPLOAD_MAX_TOTAL = _limit("THEMIS_UPLOAD_MAX_TOTAL", 200 * 1024 * 1024)


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> JSONResponse:
    def otkaz(why: str) -> JSONResponse:
        # Отказ громкий и целиком: раньше файл сверх лимита молча пропускался,
        # юрист видел половину дела и считал, что загрузил всё.
        push("agent", "Не приняла загрузку: " + why, PEOPLE_BY_ID["gruzenberg"])
        return JSONResponse({"ok": False, "error": why, "saved": []}, status_code=413)

    mb = 1024 * 1024
    if len(files) > UPLOAD_MAX_FILES:
        return otkaz(f"файлов {len(files)}, разом принимаю не больше {UPLOAD_MAX_FILES}")

    prinyato, total = [], 0
    for uf in files:
        data = await uf.read()
        total += len(data)
        if len(data) > UPLOAD_MAX_BYTES:
            return otkaz(f"«{uf.filename}» весит {len(data) / mb:.1f} МБ, "
                         f"предел на файл — {UPLOAD_MAX_BYTES / mb:.0f} МБ")
        if total > UPLOAD_MAX_TOTAL:
            return otkaz(f"всего вышло больше {UPLOAD_MAX_TOTAL / mb:.0f} МБ — "
                         "разбейте на несколько заходов")
        prinyato.append((uf.filename, data))

    saved = []
    INBOX.mkdir(parents=True, exist_ok=True)
    for filename, data in prinyato:
        # имя из последнего сегмента и для unix, и для windows-путей; срез до 200
        raw = (filename or "файл").replace("\\", "/")
        name = (os.path.basename(raw) or "файл")[:200]
        dest = INBOX / name
        i = 1
        while dest.exists():
            dest = INBOX / f"{dest.stem}_{i}{dest.suffix}"
            i += 1
        await asyncio.to_thread(dest.write_bytes, data)
        saved.append(dest.name)
    if saved:
        push("me", f"Загружены документы ({len(saved)}): " + ", ".join(saved))
        push("agent", "принял канцелярию. опись сделал. жду запуск.", PEOPLE_BY_ID["gruzenberg"])
    return JSONResponse({"saved": saved})


@app.post("/api/run")
def run() -> JSONResponse:
    prompt = (
        "Ты — Фемида. " + PIPELINE + " " + CHAT_RULES
    )
    push("me", "Запустить полный прогон по входящим документам.")
    if not _start_run(prompt):
        push("agent", "Я сейчас занята делом. Подождите, пожалуйста.", PEOPLE_BY_ID["femida"])
        return JSONResponse({"ok": False, "error": "Прогон уже идёт"}, status_code=409)
    push("agent", "Приняла. Беру в работу.", PEOPLE_BY_ID["femida"])
    return JSONResponse({"ok": True})


@app.post("/api/new-case")
def new_case(payload: dict) -> JSONResponse:
    """Создать новое дело через команду Фемиды /new-case из данных юриста."""
    data = (payload.get("text") or "").strip()
    if not data:
        return JSONResponse({"ok": False, "error": "пусто"}, status_code=400)
    prompt = (
        "Ты — Фемида. Сначала заведи новое дело по данным ниже: определи правильную папку "
        "клиента/дела (свериться с cases/_clients.md), создай структуру в cases/, заполни "
        "_client.md и _case.md, обнови _index.md и _clients.md. "
        "ЗАТЕМ СРАЗУ, НЕ ОСТАНАВЛИВАЯСЬ и не ожидая отдельной команды, по этому же делу: " + PIPELINE
        + " " + CHAT_RULES + "\n\nДАННЫЕ: " + data
    )
    push("me", "Новое дело: " + data)
    if not _start_run(prompt):
        push("agent", "Я сейчас занята делом. Подождите, пожалуйста.", PEOPLE_BY_ID["femida"])
        return JSONResponse({"ok": False, "error": "Прогон уже идёт"}, status_code=409)
    push("agent", "Завожу новое дело.", PEOPLE_BY_ID["femida"])
    return JSONResponse({"ok": True})


@app.post("/api/learn-redline")
def learn_redline(payload: dict) -> JSONResponse:
    """Изучить правки доверителя в готовом документе → уроки в knowledge/redlines.md."""
    raw = payload.get("path")
    if not raw or not isinstance(raw, str):
        return JSONResponse({"ok": False, "error": "нет пути"}, status_code=400)
    p = Path(raw).resolve()
    if CASES_DIR.resolve() not in p.parents or not p.exists():
        return JSONResponse({"ok": False, "error": "файл вне дел"}, status_code=400)
    push("me", f"Изучи мои правки: {p.name}")
    if not _start_run(_redline_prompt(str(p))):
        push("agent", "Я сейчас занята делом. Подождите, пожалуйста.", PEOPLE_BY_ID["femida"])
        return JSONResponse({"ok": False, "error": "Прогон уже идёт"}, status_code=409)
    push("agent", "Беру ваши правки на изучение. Сравню с моим черновиком.", PEOPLE_BY_ID["femida"])
    return JSONResponse({"ok": True})


@app.get("/api/history")
def history() -> JSONResponse:
    return JSONResponse({"msgs": MSGS[-300:], "active": RUN["active"]})


@app.get("/api/stream")
async def stream(after: int = 0) -> StreamingResponse:
    """SSE: новые сообщения диалога (id > cursor) + статус прогона."""
    async def gen():
        cursor = after
        last_active = None
        while True:
            new = [m for m in list(MSGS) if m["id"] > cursor]  # снапшот — не рвётся при del MSGS
            for m in new:
                cursor = m["id"]
                yield f"data: {json.dumps(m, ensure_ascii=False)}\n\n"
            if RUN["active"] != last_active:
                last_active = RUN["active"]
                yield f"data: {json.dumps({'_status': True, 'active': RUN['active']}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


def _bind_or_refuse(host: str) -> None:
    """Без секрета панель слушает только петлю.

    Панель — пульт: она запускает конвейер по живым делам и открывает файлы.
    Выставленная на чужой интерфейс без токена, она отдаёт этот пульт любому,
    кто знает адрес. Отказ происходит ДО приёма первого запроса, а не после
    первого чужого. Локальная работа владельца при этом не меняется:
    умолчание — 127.0.0.1, токен не нужен.
    """
    local = host in ("127.0.0.1", "localhost", "::1")
    if not local and not PANEL_TOKEN:
        sys.exit(
            f"ОТКАЗ: панель просят слушать {host}, а секрет не задан — "
            "отличить владельца от чужого нечем. Положить токен в "
            "~/.secrets/themis-panel.env (переменная THEMIS_PANEL_TOKEN) и "
            "передать его в окружение запуска. Значение в git не хранить."
        )


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("THEMIS_PANEL_HOST", "127.0.0.1")
    port = _limit("THEMIS_PANEL_PORT", 8800)
    _bind_or_refuse(host)
    rezhim = "с токеном" if PANEL_TOKEN else "локальный, без токена"
    print(f"Femida Cockpit → http://{host}:{port}   ({rezhim}; Ctrl+C стоп)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
