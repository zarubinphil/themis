#!/usr/bin/env python3
"""themis_bot.py — бот Фемиды: пульт в кармане, а не витрина дела.

Граница тайны, она же граница дизайна. Всё, что ушло в Telegram, разглашено Telegram:
это чужой сервер, и адвокатская тайна (ст. 8 ФЗ № 63-ФЗ) на нём не действует. Отсюда
устройство бота: наружу уходит только команда, статус и напоминание — без ФИО, без
номера дела, без сумм. Сам документ забирается по ссылке ВНУТРЬ приватной сети, где
владельца узнаёт панель по своему токену. Это же бот объясняет владельцу первым
сообщением, а не прячет в документации.

Секрет. Токен живёт в `~/.secrets/themis-telegram.env` (переменная
`THEMIS_TELEGRAM_BOT_TOKEN`), имя переменной берётся из конфига. В коде, в git и в
примерах — только ИМЯ. Аргументом командной строки секрет не принимается вовсе:
argv видно любому процессу машины через `ps`.

Доступ. Отвечаем единственному `chat_id` из окружения. Чужое сообщение не получает
ничего и попадает в аудит одной строкой без текста: журнал не должен стать вторым
местом хранения чужой тайны. `chat_id`, названный в ТЕЛЕ сообщения, — заявление,
а не факт; адрес чата берётся только из `message.chat.id`, который проставил Telegram.

Опрос, а не webhook: сервер не открывает входящий порт.

    --check                     готов ли бот к запуску (значение секрета не печатается)
    --once --api-base URL       один проход опроса: забрать, ответить, выйти
    --notify ТЕКСТ | --notify-file Ф   одно уведомление владельцу
    --templates --json          весь корпус исходящих текстов на худших данных
    --check-out ФАЙЛ            пропустил бы бот этот текст наружу
    --doc-link ПУТЬ             ссылка на документ внутрь приватной сети
    --miniapp-link              ссылка на мини-приложение
    --selftest

Пустой конфиг = бота нет. Система при этом работает целиком: бот — канал уведомлений,
а не часть конвейера. Бот персональный: каждый заводит своего в BotFather.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from hashlib import sha256
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
CASES = ROOT / "cases"
API = "https://api.telegram.org"
PREDEL_ZVUKA = 25 * 1024 * 1024        # потолок скачиваемого голосового
TAYMAUT = 40

sys.path.insert(0, str(SCRIPTS))
import pii_gate            # noqa: E402  — сторож ПД один на всю систему, не своя регулярка
import themis_config       # noqa: E402

# Деньги в тексте. Валютная метка обязательна: без неё под правило попали бы даты,
# счётчики дел и время заседания — сторож, кричащий на обиходе, будет выключен.
DENGI = re.compile(
    r"\d[\d  ]*(?:[.,]\d{1,2})?\s*(?:тыс\.?|млн\.?|млрд\.?)?\s*(?:руб\w*|₽|коп\.|копе\w*)"
    r"|\d[\d  ]*\s*(?:тыс\.|млн|млрд)\b", re.I)
# Дата в имени папки события: и 04-08-2026_, и 2026-08-14_.
DATA_PAPKI = re.compile(r"^(?:(\d{2})-(\d{2})-(\d{4})|(\d{4})-(\d{2})-(\d{2}))[_-]")


class Otkaz(Exception):
    """Беда, о которой говорим владельцу словами, а не трассировкой."""


def _slovar(x) -> dict:
    """Поле, которое ОБЯЗАНО быть объектом. Пришло не оно — считаем, что пусто."""
    return x if isinstance(x, dict) else {}


def _tseloe(x) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _skl(n: int, odin: str, dva: str, mnogo: str) -> str:
    """Согласование числа со словом. Мелочь, по которой сразу видно машину."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return odin
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return dva
    return mnogo


# ── Настройки и секрет ──────────────────────────────────────────────────────
def cfg(path: str | None = None) -> dict:
    p = Path(path) if path else themis_config.DEFAULT_PATH
    return themis_config.load(p)


def _konfig_est(path: str | None = None) -> bool:
    p = Path(path) if path else themis_config.DEFAULT_PATH
    return p.exists()


def token(c: dict) -> str:
    t = (os.environ.get(c["bot"].get("token_env") or "") or "").strip()
    if t:
        _SEKRET["tok"] = t
    return t


def owner(c: dict) -> str:
    return (os.environ.get(c["bot"].get("chat_id_env") or "") or "").strip()


def bez_sekreta(s: str, c: dict) -> str:
    """Ни одна строка наружу не несёт значение токена — ни в жалобе, ни в журнале."""
    t = token(c)
    return s.replace(t, "…") if t and t in s else s


def gotovnost(c: dict, path: str | None = None) -> list:
    beda = []
    if not c["bot"].get("enabled"):
        beda.append("бот выключен в конфиге (bot.enabled=false)" if _konfig_est(path)
                    else f"конфига нет ({Path(path) if path else themis_config.DEFAULT_PATH}) — "
                         "бот выключен, система работает без него")
    if not c["bot"].get("token_env"):
        beda.append("не названа переменная с токеном (bot.token_env)")
    elif not token(c):
        beda.append(f"переменная {c['bot']['token_env']} пуста — положить токен в "
                    "~/.secrets/themis-telegram.env и передать в окружение запуска")
    svoy = owner(c)
    if not svoy:
        beda.append(f"не задан чат владельца (переменная {c['bot'].get('chat_id_env')}) — "
                    "без него отвечать некому и отличить чужого нечем")
    elif not re.fullmatch(r"-?\d{5,20}", svoy):
        # Молчаливое несовпадение выглядит как «бот сломался»: он честно
        # сравнивает строки, просто ни одна не совпадёт никогда.
        beda.append(f"чат владельца «{svoy}» не похож на номер чата Telegram — "
                    "бот не узнает владельца и промолчит на каждое сообщение. "
                    "Узнать свой: --chat-probe")
    return beda


# ── Журнал доступа ──────────────────────────────────────────────────────────
# Значение токена, известное этому процессу. Держится в одном месте, чтобы журнал
# и печать вычищали его, не таская конфиг за собой.
_SEKRET: dict = {"tok": ""}


def audit_path() -> Path:
    return Path(os.environ.get("THEMIS_BOT_AUDIT") or (Path.home() / ".themis" / "bot-audit.log"))


def audit(sobytie: str, chat: str = "", detal: str = "") -> None:
    """Строка журнала: что произошло, с какого чата. БЕЗ текста сообщения и БЕЗ секрета —
    иначе журнал станет вторым местом хранения тайны, а читают его чаще, чем дела."""
    # Токен вычищается на записи, а не «его тут и так не бывает»: формулировки
    # отказов меняются, и однажды в деталь затечёт строка с адресом запроса.
    t = _SEKRET.get("tok") or ""
    if t:
        detal = detal.replace(t, "…")
    line = (f"{time.strftime('%d.%m.%Y %H:%M:%S')}\t{sobytie}\t{chat or '-'}\t{detal[:120]}\n")
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
        p.chmod(0o600)          # в журнале номера чатов — читает его владелец
    except OSError:
        pass          # журнал не роняет бота, но и молчать о себе не будет


def state_path() -> Path:
    """Смещение опроса лежит рядом с журналом: у приёмки и у сервера свои каталоги."""
    return audit_path().parent / "bot-state.json"


# ── Сторож на выходе ────────────────────────────────────────────────────────
def chisto(text: str) -> list:
    """Что в этом тексте нельзя выпускать в Telegram. Пусто — можно.

    ПД ищет общий сторож системы (pii_gate), а не своя регулярка: правило вносится
    один раз и держится везде. Сверху — деньги: сумма не персональные данные, но она
    так же выдаёт дело, а pii_gate ею не занимается."""
    beda = [f"ПД ({cat})" for _, _, cat in pii_gate.residual_matches(text)]
    m = DENGI.search(text)
    if m:
        beda.append(f"сумма ({m.group(0).strip()})")
    return sorted(set(beda))


# ── Речь ────────────────────────────────────────────────────────────────────
# Худший случай для приёмки: шаблонам подаётся дело со ВСЕМИ реквизитами. Ни одно
# из этих значений не смеет оказаться в исходящем тексте. Данные вымышленные.
PD = {
    "fio": "Кузнецова Мария Петровна",
    "nomer_dela": "А65-12345/2026",
    "inn": "771234567890",
    "summa": "1 250 000 руб.",
    "adres": "г. Казань, ул. Баумана, д. 5, кв. 12",
    "dokument": "vozrazheniya-na-isk.docx",
    "sud": "Вахитовский районный суд г. Казани",
    "predmet": "раздел совместно нажитого имущества супругов",
}
# То, что бот называть ОБЯЗАН, иначе он не пульт, а молчащий ящик: дата, время,
# счёт и ссылка на панель. Ничего из этого не указывает на доверителя.
SAFE = {
    "data": "21.08.2026",
    "vremya": "10:00",
    "dney": 3,
    "del_v_rabote": 12,
    "ssylka": "https://themis.vnutri.local/api/doc?id=qwkzhbmxrtaevlnc",
}
FIXTURE = {**PD, **SAFE}


def shablony(d: dict) -> dict:
    """Весь корпус того, что бот вообще умеет сказать. Живой язык — требование, а не
    украшение: с ботом разговаривают на бегу, между заседаниями."""
    return {
        "start": (
            "Я Фемида. Веду дела, слежу за сроками, собираю документы.\n\n"
            "Сразу про главное: этот чат лежит на серверах Telegram, и всё "
            "написанное здесь им видно. Поэтому имён, номеров дел и сумм я тут "
            "не называю — только даты, счёт и «готово».\n\n"
            "Документ отдаю ссылкой: она открывается внутри нашей сети, по паролю "
            "панели. Сам файл в Telegram не уходит.\n\n"
            "Команды: /status — где мы. /hearings — что ближайшее. /doc — открыть "
            "панель. Можно голосом: расшифрую здесь, на машине, звук никуда не уйдёт."
        ),
        "status": f"Дел в работе: {d['del_v_rabote']}. Ближайшее заседание {d['data']}.",
        "status_bez_dat": f"Дел в работе: {d['del_v_rabote']}. Заседаний в календаре нет.",
        "status_pusto": "Дел в работе нет. Ничего не горит.",
        "hearing_next": f"Ближайшее заседание {d['data']}.",
        "hearing_none": "Заседаний в календаре нет.",
        "hearing": (f"Заседание {d['data']} в {d['vremya']}. Папку собрал, "
                    f"забрать здесь: {d['ssylka']}"),
        "deadline": (f"Срок {d['data']} — это через {d['dney']} "
                     f"{_skl(d['dney'], 'день', 'дня', 'дней')}. "
                     "Что именно, смотрите в панели."),
        "doc_ready": f"Документ готов. Открывается только внутри сети: {d['ssylka']}",
        "voice_ok": "Записал. Лежит в панели, в надиктованном — оттуда и заберу в работу.",
        "unknown": ("Не понял. Умею: /status, /hearings, /doc. "
                    "Или надиктуйте голосом — расшифрую здесь."),
        "error": ("Застрял. Что именно сломалось — смотрите в панели, "
                  "сюда такие подробности не пишу."),
        "miniapp": f"Панель здесь: {d['ssylka']}",
        "digest": (f"Заседаний {d.get('srok', 'на завтра')}: {d.get('skolko', 1)} — "
                   f"{d.get('kogda', d['data'])}. Что брать с собой, смотрите в панели."),
    }


# ── Ссылки внутрь приватной сети ────────────────────────────────────────────
def links_path() -> Path:
    return Path(os.environ.get("THEMIS_DOC_LINKS")
                or (Path.home() / ".themis" / "doc-links.json"))


def _links() -> dict:
    try:
        d = json.loads(links_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def doc_id(put: str) -> str:
    """Непрозрачный идентификатор вместо пути. Путь называет доверителя и предмет спора —
    в ссылку, которая уйдёт в Telegram, это попадать не должно. Соль своя у каждой
    установки: без неё идентификатор перебирается по списку фамилий."""
    d = _links()
    sol = d.get("salt")
    if not sol:
        sol = os.urandom(16).hex()
        d["salt"] = sol
    # Только буквы, без цифр: шестнадцатеричный идентификатор с шансом даёт десять
    # цифр подряд, а это форма ИНН — сторож на выходе честно принял бы такую ссылку
    # за реквизит и не выпустил её. Буквенная запись эту коллизию исключает.
    syroe = sha256((sol + put).encode("utf-8")).digest()
    ident = "".join(chr(97 + b % 26) for b in syroe[:16])
    d.setdefault("links", {})[ident] = put
    p = links_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        raise Otkaz(f"карта ссылок не пишется ({p}): {e.strerror}. "
                    "Без неё панель не узнает, какой документ отдавать.")
    try:
        p.chmod(0o600)        # карта ссылок называет дела — читает её только владелец
    except OSError:
        pass
    return ident


def baza(c: dict) -> str:
    url = (c["server"].get("url") or "").strip().rstrip("/")
    if not c["server"].get("enabled"):
        raise Otkaz("панель выключена в конфиге (server.enabled) — ссылке некуда вести")
    if not url:
        raise Otkaz("адрес панели не задан (server.url) — ссылке некуда вести. "
                    "Пока панель только на этой машине, документы забираются с неё напрямую.")
    return url


def _v_delakh(put: str) -> bool:
    """Путь ведёт внутрь cases/. Проверяется ЗДЕСЬ, а не только у панели: карта
    ссылок иначе копит записи на «../../etc/passwd», и любой её недосмотр
    превращается в готовую ссылку."""
    p = Path(put)
    p = (p if p.is_absolute() else ROOT / p).resolve()
    return CASES.resolve() in p.parents


def doc_link(c: dict, put: str) -> str:
    # Токен в ссылку НЕ кладём: отправить его в Telegram значит его разгласить.
    # Владельца узнаёт панель — по своему входу, на своей стороне.
    if not _v_delakh(put):
        raise Otkaz("ссылку даю только на документ внутри дел (cases/) — "
                    f"путь «{put}» ведёт наружу")
    return f"{baza(c)}/api/doc?id={doc_id(put)}"


def miniapp_link(c: dict) -> str:
    return f"{baza(c)}/miniapp"


# ── Telegram ────────────────────────────────────────────────────────────────
def tg(c: dict, api_base: str, metod: str, telo: dict | None = None, taymaut: int = TAYMAUT):
    t = token(c)
    if not t:
        raise Otkaz("токен не задан")
    url = f"{api_base.rstrip('/')}/bot{t}/{metod}"
    data = json.dumps(telo or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=taymaut) as r:
            otvet = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(otvet, dict) and otvet.get("ok") is False:
            # 200 с «ok»: false — тоже отказ. Молча прочитав пустой result, бот
            # решил бы, что сообщений нет, и потерял бы их без следа.
            raise Otkaz(f"Telegram отклонил {metod}: {str(otvet.get('description'))[:120]}")
        return otvet
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Otkaz(f"Telegram не принял токен ({e.code}). Проверить значение "
                        f"{c['bot'].get('token_env')} в ~/.secrets/themis-telegram.env — "
                        "здесь его не печатаю.")
        raise Otkaz(f"Telegram ответил {e.code} на {metod}")
    except urllib.error.URLError as e:
        # Причину печатаем СВОЮ: в чужой может оказаться адрес запроса вместе с токеном.
        raise Otkaz(f"до Telegram не достучались на {metod}: {type(e.reason).__name__}")
    except (ValueError, OSError):
        raise Otkaz(f"ответ Telegram на {metod} не разобран")


def _pismo(chat: str, text: str) -> dict:
    """Тело sendMessage. Превью ссылки выключено намеренно: увидев ссылку, сервер
    Telegram идёт по ней САМ, чтобы собрать карточку. Наш адрес ведёт внутрь
    приватной сети — незачем звать туда чужой обходчик и незачем отдавать ему
    заголовок документа, если панель однажды окажется на публичном имени."""
    return {"chat_id": chat, "text": text, "disable_web_page_preview": True}


def skazat(c: dict, api_base: str, text: str, chat: str = "") -> bool:
    """Единственная дверь наружу. Каждый текст проходит сторожа: не сдал — не уходит."""
    text = (text or "").strip()
    if not text:
        return False
    beda = chisto(text)
    if beda:
        audit("исходящее остановлено", chat or owner(c), "; ".join(beda))
        return False
    tg(c, api_base, "sendMessage", _pismo(chat or owner(c), text))
    return True


# ── Что бот знает о делах (числа и даты, не имена) ──────────────────────────
def _dela() -> list:
    if not CASES.is_dir():
        return []
    out = []
    for client in CASES.iterdir():
        if not client.is_dir() or client.name.startswith("_"):
            continue
        for delo in client.iterdir():
            if delo.is_dir() and ((delo / "00_intake").is_dir() or (delo / "_case.md").is_file()):
                out.append(delo)
    return out


def _blizhaishee(dela: list) -> str:
    """Ближайшее событие — датой. Имя события наружу не идёт: в нём бывает суть спора."""
    segodnya = time.strftime("%Y-%m-%d")
    daty = []
    for delo in dela:
        for ev in (delo / "02_hearings").glob("*"):
            if not ev.is_dir():
                continue
            m = DATA_PAPKI.match(ev.name)
            if not m:
                continue
            d, mm, g, g2, m2, d2 = m.groups()
            iso = f"{g}-{mm}-{d}" if g else f"{g2}-{m2}-{d2}"
            if iso >= segodnya:
                daty.append(iso)
    if not daty:
        return ""
    g, mm, d = min(daty).split("-")
    return f"{d}.{mm}.{g}"


def status_text() -> str:
    dela = _dela()
    if not dela:
        return shablony(FIXTURE)["status_pusto"]
    blizh = _blizhaishee(dela)
    sh = shablony({**FIXTURE, "del_v_rabote": len(dela), "data": blizh or FIXTURE["data"]})
    return sh["status" if blizh else "status_bez_dat"]


# ── Голос ───────────────────────────────────────────────────────────────────
def queue_path() -> Path:
    return Path(os.environ.get("THEMIS_BOT_QUEUE")
                or (Path.home() / ".themis" / "voice-queue.jsonl"))


def golos(c: dict, api_base: str, file_id: str) -> str:
    """Скачать голосовое, расшифровать ЗДЕСЬ, положить задачу в очередь. Расшифровка
    остаётся на машине: в надиктовке звучат фамилии и суммы, и наружу она не уходит —
    ни в ответ боту, ни в журнал."""
    r = tg(c, api_base, "getFile", {"file_id": file_id})
    put = ((r or {}).get("result") or {}).get("file_path") or ""
    if not put:
        raise Otkaz("Telegram не отдал путь к голосовому")
    t = token(c)
    with tempfile.TemporaryDirectory(prefix="themis-golos-") as td:
        zvuk = Path(td) / Path(put).name
        req = urllib.request.Request(f"{api_base.rstrip('/')}/file/bot{t}/{put}")
        try:
            with urllib.request.urlopen(req, timeout=TAYMAUT) as resp:
                # Читаем с потолком: длину сообщает та же сторона, что и файл,
                # и верить ей на слово значит отдать ей память машины.
                telo = resp.read(PREDEL_ZVUKA + 1)
            if len(telo) > PREDEL_ZVUKA:
                raise Otkaz("голосовое неправдоподобно большое — не беру")
            zvuk.write_bytes(telo)
        except (urllib.error.URLError, OSError):
            raise Otkaz("голосовое не скачалось")
        sreda = {k: v for k, v in os.environ.items()
                 if not any(g in k.upper() for g in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))}
        p = subprocess.run([sys.executable, str(SCRIPTS / "voice_local.py"),
                            "--transcribe", str(zvuk), "--json"],
                           capture_output=True, text=True, timeout=1200, input="", env=sreda)
        if p.returncode != 0:
            raise Otkaz("расшифровать не вышло: локального движка нет либо он молчит")
        try:
            text = (json.loads(p.stdout).get("text") or "").strip()
        except ValueError:
            raise Otkaz("расшифровка не разобрана")
    if not text:
        raise Otkaz("в голосовом ничего не разобрано")
    q = queue_path()
    q.parent.mkdir(parents=True, exist_ok=True)
    with open(q, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kogda": time.strftime("%d.%m.%Y %H:%M:%S"), "text": text},
                           ensure_ascii=False) + "\n")
    try:
        q.chmod(0o600)
    except OSError:
        pass
    audit("голосовое расшифровано", owner(c), f"знаков {len(text)}")
    return text


# ── Один проход опроса ──────────────────────────────────────────────────────
def otvet(c: dict, api_base: str, msg: dict) -> str:
    """Что ответить владельцу. Текст сообщения НЕ отражается обратно: эхо вернуло бы
    в Telegram ровно то, что владелец случайно там написал."""
    sh = shablony(FIXTURE)
    if msg.get("voice"):
        try:
            golos(c, api_base, msg["voice"].get("file_id", ""))
            return sh["voice_ok"]
        except Otkaz as e:
            audit("голосовое не принято", owner(c), str(e))
            return sh["error"]
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text else ""
    if cmd in ("/start", "/help"):
        return sh["start"]
    if cmd == "/status":
        return status_text()
    if cmd in ("/hearings", "/zasedaniya"):
        blizh = _blizhaishee(_dela())
        return shablony({**FIXTURE, "data": blizh or FIXTURE["data"]})[
            "hearing_next" if blizh else "hearing_none"]
    if cmd in ("/doc", "/panel", "/miniapp"):
        try:
            return shablony({**FIXTURE, "ssylka": miniapp_link(c)})["miniapp"]
        except Otkaz as e:
            audit("ссылка не выдана", owner(c), str(e))
            return "Панель пока только на рабочей машине — ссылку дать некуда."
    return sh["unknown"]


def once(c: dict, api_base: str, dolgiy: bool = False) -> int:
    st = state_path()
    try:
        offset = int(json.loads(st.read_text(encoding="utf-8")).get("offset") or 0)
    except (OSError, ValueError, TypeError):
        offset = 0
    # Долгий опрос: держим соединение сами и ждём ответа Telegram, а не дёргаем
    # его в цикле. Свой таймаут заведомо больше — иначе рвём собственный запрос.
    zhdat = 25 if dolgiy else 0
    r = tg(c, api_base, "getUpdates", {"offset": offset, "timeout": zhdat, "limit": 50},
           taymaut=TAYMAUT + zhdat)
    updates = (r or {}).get("result")
    if not isinstance(updates, list):
        updates = []
    svoy = owner(c)
    for u in updates:
        # Форма обновления — заявление той стороны, а не факт. Одно кривое поле
        # не должно убивать проход: в --serve это тихая смерть канала уведомлений,
        # которую владелец заметит только по молчанию бота.
        u = _slovar(u)
        offset = max(offset, _tseloe(u.get("update_id")) + 1)
        msg = _slovar(u.get("message")) or _slovar(u.get("edited_message"))
        # Адрес чата — только от Telegram. Тот же номер, названный в тексте сообщения,
        # остаётся заявлением: им и подделывают доступ.
        chat = str(_slovar(msg.get("chat")).get("id") or "")
        otpravitel = str(_slovar(msg.get("from")).get("id") or "")
        if not chat:
            audit("обновление без чата — пропускаю", "", f"update {u.get('update_id')}")
            continue
        if chat != svoy:
            audit("чужой чат — не отвечаю", chat, "молчание")
            continue
        # Личный чат — это чат самого владельца, там отправитель равен чату. Если
        # они разошлись, бот оказался в группе либо сообщение прислал не владелец:
        # разговаривать с таким собеседником нельзя, даже если чат «правильный».
        if otpravitel and otpravitel != svoy:
            audit("чужой отправитель в чате владельца", otpravitel, "молчание")
            continue
        try:
            skazat(c, api_base, otvet(c, api_base, msg), chat)
        except Otkaz as e:
            audit("ответ не отправлен", chat, str(e))
            raise
        except Exception as e:                                  # noqa: BLE001
            # Своя ошибка на одном сообщении — беда этого сообщения, а не всего
            # опроса. Пишем в журнал ТИП, не текст: в тексте бывает чужое.
            audit("сообщение не обработано", chat, type(e).__name__)
            continue
    try:
        st.parent.mkdir(parents=True, exist_ok=True)
        st.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    except OSError:
        pass
    return 0


def _zamok() -> Path:
    return audit_path().parent / "bot.lock"


def _vzyat_zamok() -> bool:
    """Один опрашивающий на машину. Два процесса, тянущие getUpdates, делят
    обновления между собой случайным образом: половина сообщений владельца
    осталась бы без ответа, а другая половина получила бы два."""
    z = _zamok()
    z.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(z, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            chuzhoy = int(z.read_text(encoding="utf-8").strip() or 0)
            os.kill(chuzhoy, 0)          # процесс жив — уступаем
            return False
        except (ValueError, OSError):
            # Замок от процесса, которого больше нет: снимаем и берём себе.
            try:
                z.unlink()
            except OSError:
                return False
            return _vzyat_zamok()


def serve(c: dict, api_base: str, cycles: int | None = None) -> int:
    """Непрерывный опрос. Именно это владелец и запускает — `--once` показывает
    механизм, но день бот живёт здесь. Порт наружу не открывается: инициатива
    всегда наша."""
    if not _vzyat_zamok():
        print(f"ОТКАЗ: бот уже опрашивает Telegram (замок {_zamok()}). "
              "Два опрашивающих делят сообщения между собой — половина ответов пропадёт.",
              file=sys.stderr)
        return 1
    audit("опрос начат", owner(c), f"pid {os.getpid()}")
    sdelano = 0
    try:
        while cycles is None or sdelano < cycles:
            try:
                once(c, api_base, dolgiy=True)
            except Otkaz as e:
                audit("проход не удался", owner(c), str(e))
                if cycles is not None:
                    raise
                time.sleep(15)          # сеть моргнула — не колотиться в дверь
            sdelano += 1
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _zamok().unlink()
        except OSError:
            pass
        audit("опрос остановлен", owner(c), f"проходов {sdelano}")
    return 0


def blizhaishie(dney: int = 1) -> list:
    """Даты событий на ближайшие N суток. Только даты: что именно назначено и по
    какому делу — в панели, за паролем."""
    segodnya = time.strftime("%Y-%m-%d")
    do = time.strftime("%Y-%m-%d", time.localtime(time.time() + dney * 86400))
    daty = []
    for delo in _dela():
        for ev in (delo / "02_hearings").glob("*"):
            if not ev.is_dir():
                continue
            m = DATA_PAPKI.match(ev.name)
            if not m:
                continue
            d, mm, g, g2, m2, d2 = m.groups()
            iso = f"{g}-{mm}-{d}" if g else f"{g2}-{m2}-{d2}"
            if segodnya <= iso <= do:
                daty.append(iso)
    return sorted(daty)


def svodka_sobytiy(dney: int = 1) -> str:
    """Текст утреннего напоминания. Событий нет — пустая строка, и бот молчит:
    ежедневное «сегодня ничего» превращает уведомления в шум, который перестают
    читать, а вместе с ним перестают замечать и настоящее."""
    daty = blizhaishie(dney)
    if not daty:
        return ""
    unikalnye = sorted(set(daty))
    pokazat = unikalnye[:8]
    kogda = ", ".join(f"{i[8:10]}.{i[5:7]}.{i[:4]}" for i in pokazat)
    if len(unikalnye) > len(pokazat):
        # Перечислять всё нельзя: длинное уведомление не уйдёт вовсе (предел
        # Telegram), и владелец останется вообще без напоминания.
        kogda += f" и ещё дней с заседаниями: {len(unikalnye) - len(pokazat)}"
    srok = ("на завтра" if dney <= 1
            else f"на ближайшие {dney} {_skl(dney, 'день', 'дня', 'дней')}")
    return shablony({**FIXTURE, "srok": srok, "skolko": len(daty), "kogda": kogda})["digest"]


# ── Команды ─────────────────────────────────────────────────────────────────
def cmd_check(c: dict, path: str | None) -> int:
    beda = gotovnost(c, path)
    if beda:
        print("бот к запуску не готов:", file=sys.stderr)
        for b in beda:
            print("  · " + bez_sekreta(b, c), file=sys.stderr)
        return 1
    print(f"бот готов: чат владельца задан, токен взят из {c['bot']['token_env']} "
          f"(значение здесь не печатается), опрос long polling, "
          f"журнал — {audit_path()}")
    return 0


def cmd_check_out(src: str) -> int:
    try:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"ОТКАЗ: {e}", file=sys.stderr)
        return 2
    beda = chisto(text)
    if beda:
        print("наружу не уйдёт: " + ", ".join(beda), file=sys.stderr)
        return 1
    print("чисто — такой текст бот отправит")
    return 0


PREDEL = 4096          # жёсткий предел Telegram на одно сообщение


def cmd_notify(c: dict, api_base: str, text: str) -> int:
    text = (text or "").strip()
    if not text:
        # Молчание — тоже ответ. Сказать нечего — бот не пишет.
        return 0
    if len(text) > PREDEL:
        # Резать на куски нельзя: это лента статусов вместо одного события, и
        # сторож на выходе проверял бы каждый кусок по отдельности, теряя контекст.
        audit("уведомление не отправлено", owner(c), f"длина {len(text)} > {PREDEL}")
        print(f"ОТКАЗ: уведомление длиной {len(text)} знаков не влезает в одно сообщение "
              f"({PREDEL}) — событие описывается короче, подробности живут в панели",
              file=sys.stderr)
        return 1
    beda = chisto(text)
    if beda:
        audit("уведомление остановлено", owner(c), "; ".join(beda))
        print("ОТКАЗ: в уведомлении " + ", ".join(beda) + " — в Telegram это не уходит",
              file=sys.stderr)
        return 1
    tg(c, api_base, "sendMessage", _pismo(owner(c), text))
    audit("уведомление отправлено", owner(c), f"знаков {len(text)}")
    return 0


def cmd_chat_probe(c: dict, api_base: str) -> int:
    """Кто писал боту. Нужно один раз, при первой настройке: свой chat_id владелец
    иначе взять неоткуда, а без него бот не отличает владельца от чужого.
    Печатает номера чатов и ничего не отвечает — на этом шаге ещё некому."""
    r = tg(c, api_base, "getUpdates", {"timeout": 0, "limit": 50})
    chaty = {}
    for u in (r or {}).get("result") or []:
        msg = u.get("message") or u.get("edited_message") or {}
        ident = str(((msg.get("chat") or {}).get("id") or ""))
        if ident:
            chaty[ident] = chaty.get(ident, 0) + 1
    if not chaty:
        print("боту пока никто не писал — напишите ему что-нибудь и повторите")
        return 1
    print("писали эти чаты (свой впишите в переменную "
          f"{c['bot'].get('chat_id_env')}):")
    for ident, skolko in sorted(chaty.items(), key=lambda x: -x[1]):
        print(f"  {ident}  — сообщений {skolko}")
    return 0


def cmd_notify_doc(c: dict, api_base: str, put: str) -> int:
    """Документ готов — одно сообщение со ссылкой внутрь сети. Зовётся, когда
    составитель закончил: имя документа и дело в текст не попадают, ссылка
    непрозрачна, забирается всё на панели."""
    text = shablony({**FIXTURE, "ssylka": doc_link(c, put)})["doc_ready"]
    return cmd_notify(c, api_base, text)


def cmd_notify_hearing(c: dict, api_base: str, data: str, vremya: str, put: str) -> int:
    """Заседание и собранная к нему папка — одно сообщение. Зовётся, когда готов
    пакет подготовки: наружу идут дата, время и непрозрачная ссылка, а что за дело
    и с кем спор — видно только на панели."""
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", data or ""):
        print(f"ОТКАЗ: дата «{data}» не в формате ДД.ММ.ГГГГ", file=sys.stderr)
        return 2
    if not put:
        print("ОТКАЗ: нужен путь к собранной папке (--doc)", file=sys.stderr)
        return 2
    text = shablony({**FIXTURE, "data": data, "vremya": vremya or FIXTURE["vremya"],
                     "ssylka": doc_link(c, put)})["hearing"]
    return cmd_notify(c, api_base, text)


def cmd_notify_deadline(c: dict, api_base: str, data: str) -> int:
    """Срок подходит. Дату считает scripts/sroki.py, бот только напоминает —
    и называет только её: какой именно срок и по какому делу, видно в панели."""
    try:
        d, m, g = data.split(".")
        kogda = time.mktime(time.strptime(f"{g}-{m}-{d}", "%Y-%m-%d"))
    except (ValueError, OverflowError):
        print(f"ОТКАЗ: дата «{data}» не в формате ДД.ММ.ГГГГ", file=sys.stderr)
        return 2
    ostalos = max(0, int((kogda - time.time()) // 86400) + 1)
    return cmd_notify(c, api_base,
                      shablony({**FIXTURE, "data": data, "dney": ostalos})["deadline"])


def cmd_templates(as_json: bool) -> int:
    sh = shablony(FIXTURE)
    if as_json:
        print(json.dumps({"fixture": {"pd": PD, "safe": SAFE},
                          "templates": [{"name": k, "text": v} for k, v in sh.items()]},
                         ensure_ascii=False, indent=1))
    else:
        for k, v in sh.items():
            print(f"── {k} ──\n{v}\n")
    return 0


# ── Селфтест ────────────────────────────────────────────────────────────────
def selftest() -> int:
    sh = shablony(FIXTURE)
    ves = "\n".join(sh.values())
    for k, v in PD.items():
        assert str(v) not in ves, f"значение fixture.pd «{k}» ушло бы в Telegram"
    assert not chisto("\n".join(str(v) for v in SAFE.values())), \
        "объявленное безопасным на деле похоже на ПД"
    assert not chisto(ves), f"корпус шаблонов не сдал сторожу: {chisto(ves)}"
    # Шаблон без вызывающего — мёртвая речь: приёмка его проверяет, а владелец
    # никогда не услышит, и он тихо расходится с тем, что уходит на самом деле.
    tekst_fayla = Path(__file__).read_text(encoding="utf-8")
    telo = tekst_fayla.split("def shablony(")[1].split("\ndef ")[0]
    for imya in sh:
        vne_opredeleniya = tekst_fayla.count(f'"{imya}"') - telo.count(f'"{imya}"')
        assert vne_opredeleniya > 0, \
            f"шаблон «{imya}» ничем не отправляется — мёртвая речь"
    assert chisto("Иванова Мария Петровна ждёт документ"), "сторож слеп к ФИО"
    assert chisto("Взыскано 1 250 000 руб."), "сторож слеп к сумме"
    assert not chisto("Взыскано по одному делу, остальные в работе."), \
        "ложная тревога на обиходе"
    assert not chisto("Дел в работе: 12. Ближайшее заседание 21.08.2026."), \
        "ложная тревога на счёте и дате"
    # Проба 19.08.2026: короткие формы суммы, которых первая редакция не видела.
    for summa in ("250 тыс. руб. взыскано", "1,2 млн рублей неустойки",
                  "цена иска — 3 млн", "500000₽ по договору", "45 коп. пени"):
        assert chisto(summa), f"сторож слеп к сумме: {summa!r}"
    for obikhod in ("Заседание 21.08.2026 в 10:00.", "Дел в работе: 12.",
                    "Срок 21.08.2026 — это через 3 дня.", "Готово, забирайте.",
                    "2 копии договора приложены."):
        assert not chisto(obikhod), f"ложная тревога на обиходе: {obikhod!r}"

    assert svodka_sobytiy.__doc__ and "молч" in svodka_sobytiy.__doc__, \
        "молчание при отсутствии событий не описано"

    assert _pismo("1", "текст")["disable_web_page_preview"] is True, \
        "превью ссылки включено — обходчик Telegram пойдёт по нашему адресу сам"
    assert _slovar("строка") == {} and _slovar({"a": 1}) == {"a": 1}, "разбор поля-объекта"
    assert _tseloe("девяносто") == 0 and _tseloe("7") == 7, "разбор числа из чужого ответа"
    assert (_skl(1, "день", "дня", "дней"), _skl(3, "день", "дня", "дней"),
            _skl(7, "день", "дня", "дней"), _skl(11, "день", "дня", "дней")) == \
        ("день", "дня", "дней", "дней"), "число со словом не согласовано"

    m = DATA_PAPKI.match("04-08-2026_zasedanie")
    assert m and m.group(3) == "2026", "дата ДД-ММ-ГГГГ не разобрана"
    assert DATA_PAPKI.match("2026-08-14_zasedanie"), "дата ГГГГ-ММ-ДД не разобрана"
    assert not DATA_PAPKI.match("_baselines"), "служебная папка принята за событие"

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        saved = dict(os.environ)
        try:
            os.environ["THEMIS_DOC_LINKS"] = str(td / "links.json")
            os.environ["THEMIS_BOT_AUDIT"] = str(td / "audit.log")
            c = {"bot": {"enabled": True, "token_env": "PROBA_TOK", "chat_id_env": "PROBA_CHAT"},
                 "server": {"enabled": True, "url": "https://vnutri.local"}}
            put = "cases/petrov-petr/spor-2026/.agent/drafts/isk.docx"
            ssylka = doc_link(c, put)
            for sled in ("petrov", "spor-2026", "isk.docx"):
                assert sled not in ssylka, f"ссылка называет дело: {sled}"
            assert not chisto(ssylka), "ссылка не сдала сторожу"
            assert doc_link(c, put) == ssylka, "идентификатор пляшет между запусками"
            assert not any(ch.isdigit() for ch in ssylka.split("id=")[1]), \
                "в идентификаторе цифры — длинный ряд цифр сторож примет за ИНН"
            for naruzhu in ("/etc/passwd", "../../../etc/passwd", "knowledge/lessons-log.md"):
                try:
                    doc_link(c, naruzhu)
                    raise AssertionError(f"выдана ссылка наружу дел: {naruzhu}")
                except Otkaz:
                    pass
            karta = json.loads((td / "links.json").read_text(encoding="utf-8"))
            assert put in karta["links"].values(), "карта ссылок не сохранила путь"

            os.environ["PROBA_TOK"] = "1234567890:" + "Z" * 35
            os.environ["PROBA_CHAT"] = "700100200"
            assert not gotovnost(c), f"готовый бот объявлен неготовым: {gotovnost(c)}"
            assert "Z" * 35 not in bez_sekreta("токен " + os.environ["PROBA_TOK"], c), \
                "секрет не вычищается из строки"
            os.environ.pop("PROBA_CHAT", None)
            assert gotovnost(c), "бот без чата владельца объявлен готовым"

            c["bot"]["enabled"] = False
            assert gotovnost(c), "выключенный бот объявлен готовым"
        finally:
            os.environ.clear()
            os.environ.update(saved)
    print("selftest: корпус чист, сторож ловит утечку и молчит на обиходе, "
          "ссылка не называет дело")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Бот Фемиды. Секрет — только через окружение, аргументом не принимается.")
    ap.add_argument("--config", help="путь к конфигу установки")
    ap.add_argument("--api-base", default=API, help="адрес Telegram Bot API")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--serve", action="store_true",
                    help="непрерывный опрос (то, что запускается на весь день)")
    ap.add_argument("--cycles", type=int,
                    help="сколько проходов сделать в --serve (по умолчанию без конца)")
    ap.add_argument("--notify", metavar="ТЕКСТ")
    ap.add_argument("--notify-file", metavar="ФАЙЛ")
    ap.add_argument("--notify-doc", metavar="ПУТЬ",
                    help="документ готов: одно сообщение со ссылкой внутрь сети")
    ap.add_argument("--notify-deadline", metavar="ДД.ММ.ГГГГ",
                    help="срок подходит: напоминание одной датой")
    ap.add_argument("--notify-hearing", metavar="ДД.ММ.ГГГГ",
                    help="заседание и собранная папка: дата, время и ссылка")
    ap.add_argument("--at", metavar="ЧЧ:ММ", default="", help="время заседания")
    ap.add_argument("--doc", metavar="ПУТЬ", default="",
                    help="путь к собранному документу для --notify-hearing")
    ap.add_argument("--notify-hearings", action="store_true",
                    help="напоминание о ближайших заседаниях (запускается по расписанию)")
    ap.add_argument("--days", type=int, default=1, help="горизонт напоминания в сутках")
    ap.add_argument("--chat-probe", action="store_true",
                    help="какие чаты писали боту — чтобы владелец узнал свой chat_id")
    ap.add_argument("--templates", action="store_true")
    ap.add_argument("--check-out", metavar="ФАЙЛ")
    ap.add_argument("--doc-link", metavar="ПУТЬ")
    ap.add_argument("--miniapp-link", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.templates:
        return cmd_templates(a.json)
    if a.check_out:
        return cmd_check_out(a.check_out)

    c = cfg(a.config)
    try:
        if a.check:
            return cmd_check(c, a.config)
        if a.doc_link:
            print(doc_link(c, a.doc_link))
            return 0
        if a.miniapp_link:
            print(miniapp_link(c))
            return 0

        # Дальше — работа с Telegram. Выключенный бот не стучится туда даже разок.
        beda = gotovnost(c, a.config)
        if beda:
            print("бот не запускается: " + "; ".join(bez_sekreta(b, c) for b in beda),
                  file=sys.stderr)
            return 1
        if a.once:
            if not _vzyat_zamok():
                print(f"ОТКАЗ: Telegram уже опрашивает другой процесс (замок {_zamok()}). "
                      "Два опрашивающих делят обновления — часть сообщений пропала бы.",
                      file=sys.stderr)
                return 1
            try:
                return once(c, a.api_base)
            finally:
                try:
                    _zamok().unlink()
                except OSError:
                    pass
        if a.serve:
            return serve(c, a.api_base, a.cycles)
        if a.chat_probe:
            return cmd_chat_probe(c, a.api_base)
        if a.notify_hearings:
            return cmd_notify(c, a.api_base, svodka_sobytiy(max(1, a.days)))
        if a.notify_doc:
            return cmd_notify_doc(c, a.api_base, a.notify_doc)
        if a.notify_deadline:
            return cmd_notify_deadline(c, a.api_base, a.notify_deadline)
        if a.notify_hearing:
            return cmd_notify_hearing(c, a.api_base, a.notify_hearing, a.at, a.doc)
        if a.notify is not None:
            return cmd_notify(c, a.api_base, a.notify)
        if a.notify_file:
            try:
                text = Path(a.notify_file).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"ОТКАЗ: {e}", file=sys.stderr)
                return 2
            return cmd_notify(c, a.api_base, text)
    except Otkaz as e:
        print("ОТКАЗ: " + bez_sekreta(str(e), c), file=sys.stderr)
        return 1
    ap.error("нужна команда: --check, --once, --serve, --notify, --templates, "
             "--check-out, --doc-link, --miniapp-link или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
