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
TAYMAUT = 40

sys.path.insert(0, str(SCRIPTS))
import pii_gate            # noqa: E402  — сторож ПД один на всю систему, не своя регулярка
import themis_config       # noqa: E402

# Деньги в тексте. Валютная метка обязательна: без неё под правило попали бы даты,
# счётчики дел и время заседания — сторож, кричащий на обиходе, будет выключен.
DENGI = re.compile(r"\d[\d\s  ]{2,}(?:[.,]\d{1,2})?\s*(?:руб|₽|рубл)", re.I)
# Дата в имени папки события: и 04-08-2026_, и 2026-08-14_.
DATA_PAPKI = re.compile(r"^(?:(\d{2})-(\d{2})-(\d{4})|(\d{4})-(\d{2})-(\d{2}))[_-]")


class Otkaz(Exception):
    """Беда, о которой говорим владельцу словами, а не трассировкой."""


# ── Настройки и секрет ──────────────────────────────────────────────────────
def cfg(path: str | None = None) -> dict:
    p = Path(path) if path else themis_config.DEFAULT_PATH
    return themis_config.load(p)


def _konfig_est(path: str | None = None) -> bool:
    p = Path(path) if path else themis_config.DEFAULT_PATH
    return p.exists()


def token(c: dict) -> str:
    return (os.environ.get(c["bot"].get("token_env") or "") or "").strip()


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
    if not owner(c):
        beda.append(f"не задан чат владельца (переменная {c['bot'].get('chat_id_env')}) — "
                    "без него отвечать некому и отличить чужого нечем")
    return beda


# ── Журнал доступа ──────────────────────────────────────────────────────────
def audit_path() -> Path:
    return Path(os.environ.get("THEMIS_BOT_AUDIT") or (Path.home() / ".themis" / "bot-audit.log"))


def audit(sobytie: str, chat: str = "", detal: str = "") -> None:
    """Строка журнала: что произошло, с какого чата. БЕЗ текста сообщения и БЕЗ секрета —
    иначе журнал станет вторым местом хранения тайны, а читают его чаще, чем дела."""
    line = (f"{time.strftime('%d.%m.%Y %H:%M:%S')}\t{sobytie}\t{chat or '-'}\t{detal[:120]}\n")
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
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
        "status_pusto": "Дел в работе нет. Ничего не горит.",
        "hearing": (f"Заседание {d['data']} в {d['vremya']}. Папку собрал, "
                    f"забрать здесь: {d['ssylka']}"),
        "deadline": (f"Срок {d['data']} — это через {d['dney']} дня. "
                     "Что именно, смотрите в панели."),
        "doc_ready": f"Документ готов. Открывается только внутри сети: {d['ssylka']}",
        "voice_ok": "Записал. Возьмусь — напишу.",
        "unknown": ("Не понял. Умею: /status, /hearings, /doc. "
                    "Или надиктуйте голосом — расшифрую здесь."),
        "error": ("Застрял. Что именно сломалось — смотрите в панели, "
                  "сюда такие подробности не пишу."),
        "miniapp": f"Панель здесь: {d['ssylka']}",
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
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    try:
        p.chmod(0o600)        # карта ссылок называет дела — читает её только владелец
    except OSError:
        pass
    return ident


def baza(c: dict) -> str:
    url = (c["server"].get("url") or "").strip().rstrip("/")
    if not url:
        raise Otkaz("адрес панели не задан (server.url) — ссылке некуда вести. "
                    "Пока панель только на этой машине, документы забираются с неё напрямую.")
    return url


def doc_link(c: dict, put: str) -> str:
    # Токен в ссылку НЕ кладём: отправить его в Telegram значит его разгласить.
    # Владельца узнаёт панель — по своему входу, на своей стороне.
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
            return json.loads(r.read().decode("utf-8", "replace"))
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


def skazat(c: dict, api_base: str, text: str, chat: str = "") -> bool:
    """Единственная дверь наружу. Каждый текст проходит сторожа: не сдал — не уходит."""
    text = (text or "").strip()
    if not text:
        return False
    beda = chisto(text)
    if beda:
        audit("исходящее остановлено", chat or owner(c), "; ".join(beda))
        return False
    tg(c, api_base, "sendMessage", {"chat_id": chat or owner(c), "text": text})
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
    hvost = f" Ближайшее заседание {blizh}." if blizh else " Заседаний в календаре нет."
    return f"Дел в работе: {len(dela)}.{hvost}"


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
                zvuk.write_bytes(resp.read())
        except (urllib.error.URLError, OSError):
            raise Otkaz("голосовое не скачалось")
        p = subprocess.run([sys.executable, str(SCRIPTS / "voice_local.py"),
                            "--transcribe", str(zvuk), "--json"],
                           capture_output=True, text=True, timeout=1200, input="")
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
        return f"Ближайшее заседание {blizh}." if blizh else "Заседаний в календаре нет."
    if cmd in ("/doc", "/panel", "/miniapp"):
        try:
            return f"Панель здесь: {miniapp_link(c)}"
        except Otkaz as e:
            audit("ссылка не выдана", owner(c), str(e))
            return sh["error"]
    return sh["unknown"]


def once(c: dict, api_base: str) -> int:
    st = state_path()
    try:
        offset = int(json.loads(st.read_text(encoding="utf-8")).get("offset") or 0)
    except (OSError, ValueError, TypeError):
        offset = 0
    r = tg(c, api_base, "getUpdates", {"offset": offset, "timeout": 0, "limit": 50})
    updates = (r or {}).get("result") or []
    svoy = owner(c)
    for u in updates:
        offset = max(offset, int(u.get("update_id") or 0) + 1)
        msg = u.get("message") or u.get("edited_message") or {}
        # Адрес чата — только от Telegram. Тот же номер, названный в тексте сообщения,
        # остаётся заявлением: им и подделывают доступ.
        chat = str(((msg.get("chat") or {}).get("id") or ""))
        if not chat:
            continue
        if chat != svoy:
            audit("чужой чат — не отвечаю", chat, "молчание")
            continue
        try:
            skazat(c, api_base, otvet(c, api_base, msg), chat)
        except Otkaz as e:
            audit("ответ не отправлен", chat, str(e))
            raise
    try:
        st.parent.mkdir(parents=True, exist_ok=True)
        st.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    except OSError:
        pass
    return 0


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


def cmd_notify(c: dict, api_base: str, text: str) -> int:
    text = (text or "").strip()
    if not text:
        # Молчание — тоже ответ. Сказать нечего — бот не пишет.
        return 0
    beda = chisto(text)
    if beda:
        audit("уведомление остановлено", owner(c), "; ".join(beda))
        print("ОТКАЗ: в уведомлении " + ", ".join(beda) + " — в Telegram это не уходит",
              file=sys.stderr)
        return 1
    tg(c, api_base, "sendMessage", {"chat_id": owner(c), "text": text})
    audit("уведомление отправлено", owner(c), f"знаков {len(text)}")
    return 0


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
    assert chisto("Иванова Мария Петровна ждёт документ"), "сторож слеп к ФИО"
    assert chisto("Взыскано 1 250 000 руб."), "сторож слеп к сумме"
    assert not chisto("Взыскано по одному делу, остальные в работе."), \
        "ложная тревога на обиходе"
    assert not chisto("Дел в работе: 12. Ближайшее заседание 21.08.2026."), \
        "ложная тревога на счёте и дате"

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
    ap.add_argument("--notify", metavar="ТЕКСТ")
    ap.add_argument("--notify-file", metavar="ФАЙЛ")
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
            return once(c, a.api_base)
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
    ap.error("нужна команда: --check, --once, --notify, --templates, --check-out, "
             "--doc-link, --miniapp-link или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
