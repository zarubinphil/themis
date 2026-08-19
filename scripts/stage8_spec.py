#!/usr/bin/env python3
"""stage8_spec.py — приёмка этапа 8 «бот Фемида». Пишет КООРДИНАТОР, не исполнитель.

Инвариант роя: generator ≠ verifier. Контракт задан снаружи, проверка — чёрным ящиком:
командная строка, подставной Telegram на петле, подставной движок расшифровки. Исполнитель
этот файл НЕ ПРАВИТ; правку ловит loop_gate (`spec:tampered`).

Честная граница. Настоящий Telegram здесь не дёргается ни разу: первый живой запуск —
действие наружу, его делает владелец. Подставной сервер на 127.0.0.1 для приёмки лучше
настоящего: он записывает КАЖДОЕ обращение бота и показывает, что именно ушло бы в чат.

Почему так строго. Всё, что попало в Telegram, разглашено Telegram — это чужой сервер,
и адвокатская тайна (ст. 8 ФЗ № 63-ФЗ) на нём не действует. Значит, бот — пульт:
команда, статус, уведомление. Ни ФИО, ни номера дела, ни суммы в исходящем тексте
не бывает НИКОГДА, а документ забирается по ссылке внутрь приватной сети.

Восемь работ:
  1. секрет не всплывает — ни в отслеживаемом файле, ни в выводе, ни в журнале;
  2. чужой `chat_id` не получает ответа и попадает в аудит; `chat_id` из ТЕЛА сообщения —
     заявление, а не факт;
  3. корпус исходящих текстов чист от ПД, сумм и номеров дел, а сторож на выходе
     проверен по ОБЕИМ осям: ловит утечку и молчит на обиходе;
  4. голос расшифрован локально, исходящих обращений при расшифровке ноль, движка нет —
     отказ, а не тихий уход в облако;
  5. пустой конфиг = бот не стартует и не стучится никуда, система работает;
  6. ссылка на документ ведёт внутрь приватной сети, токена в себе не несёт и снаружи
     не открывается;
  7. одно сообщение на событие; сказать нечего — бот молчит;
  8. аватар — файл на диске, повторяемый байт в байт.

Выход: 0 — этап принят; 1 — есть несданное.
"""
import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
COCKPIT = ROOT / "cockpit"

# Сеть наружу закрыта наглухо, петля разрешена: подставной Telegram живёт на 127.0.0.1.
NO_NET = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1,localhost"}

# Подставной секрет собирается из кусков НАМЕРЕННО: литерал вида «цифры:35 знаков»
# в этом файле сам попал бы под проверку «токен в отслеживаемом файле» (проверка 1)
# и красил бы приёмку на ровном месте.
FAKE_TOKEN = "1234567890" + ":" + ("A" * 30) + "priem"
OWNER_CHAT = "700100200"
CHUZHOY_CHAT = "900900900"

# Форма токена Telegram: id бота, двоеточие, 35 знаков секрета.
TOKEN_SHAPE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
# Деньги в тексте: «1 234 567 руб.», «1234567₽», «1 234 567,89 рублей».
DENGI = re.compile(r"\d[\d   ]{3,}(?:[.,]\d{2})?\s*(?:руб|₽|рубл)", re.I)
# Номер дела: А65-12345/2026, 2-1234/2026, 33-5678/2025.
NOMER_DELA = re.compile(r"\b[А-Яа-яA-Za-z]?\d{1,4}-\d{3,6}/\d{4}\b")


def run(argv, cwd=ROOT, timeout=300, env=None, stdin=""):
    try:
        p = subprocess.run([sys.executable, *argv], cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout, env=env or NO_NET, input=stdin)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"таймаут {timeout} с"
    except OSError as e:
        return 127, "", str(e)


def tool(name):
    return str(SCRIPTS / name)


def exists(name):
    return (SCRIPTS / name).is_file()


def stub(path: Path, body: str) -> str:
    """Подставная программа: пишется на диск и запускается как настоящая."""
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return str(path)


def config(path: Path, **over) -> str:
    """Конфиг установки. Секрет сюда не пишется никогда — только ИМЯ переменной."""
    cfg = {"bot": {"enabled": True, "token_env": "THEMIS_TELEGRAM_BOT_TOKEN",
                   "chat_id_env": "THEMIS_TELEGRAM_CHAT_ID"},
           "server": {"enabled": True, "url": "https://themis.vnutri.local",
                      "token_env": "THEMIS_PANEL_TOKEN"}}
    for k, v in over.items():
        cfg.setdefault(k, {})
        cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(path)


def bot_env(cfg_path, audit, extra=None):
    e = {**NO_NET,
         "THEMIS_CONFIG": str(cfg_path),
         "THEMIS_TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
         "THEMIS_TELEGRAM_CHAT_ID": OWNER_CHAT,
         "THEMIS_BOT_AUDIT": str(audit)}
    e.update(extra or {})
    return e


# ── Подставной Telegram ─────────────────────────────────────────────────────
class FakeTelegram:
    """Записывает каждое обращение бота: путь, chat_id, текст. Ничего не выдумывает."""

    def __init__(self, updates):
        self.updates = list(updates)
        self.sent = []        # [{chat_id, text}]
        self.paths = []       # каждый путь, куда стучался бот
        self.bad_token = 0
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _telo(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
                try:
                    return json.loads(raw) if raw.startswith("{") else dict(
                        p.split("=", 1) for p in raw.split("&") if "=" in p)
                except ValueError:
                    return {}

            def _otvet(self, obj, code=200):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _rabota(self):
                put = self.path.split("?")[0]
                srv.paths.append(put)
                # Токен сверяется ЦЕЛИКОМ отдельным сегментом пути. Проверка «верный
                # токен встречается в строке» пропускала «…-nevernyy»: приписанный
                # хвост её не ломал, и проба на отказ доступа ничего не проверяла.
                chasti = put.strip("/").split("/")
                nesu = chasti[1] if chasti[:1] == ["file"] else (chasti[0] if chasti else "")
                if nesu != f"bot{FAKE_TOKEN}":
                    srv.bad_token += 1
                    return self._otvet({"ok": False, "description": "Unauthorized"}, 401)
                metod = put.rsplit("/", 1)[-1]
                telo = self._telo()
                if metod == "getUpdates":
                    out, srv.updates = srv.updates, []
                    return self._otvet({"ok": True, "result": out})
                if metod == "sendMessage":
                    srv.sent.append({"chat_id": str(telo.get("chat_id", "")),
                                     "text": str(telo.get("text", ""))})
                    return self._otvet({"ok": True, "result": {"message_id": len(srv.sent)}})
                if metod == "getFile":
                    return self._otvet({"ok": True, "result": {"file_path": "voice/golos.oga"}})
                if put.startswith("/file/"):
                    self.send_response(200)
                    self.send_header("content-length", "8")
                    self.end_headers()
                    return self.wfile.write(b"OggS\x00\x00\x00\x00")
                if metod == "getMe":
                    return self._otvet({"ok": True, "result": {"id": 1, "username": "proba_bot"}})
                return self._otvet({"ok": True, "result": {}})

            def do_GET(self):
                self._rabota()

            def do_POST(self):
                self._rabota()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


class SchetchikProxy:
    """Считает ЛЮБОЕ обращение. Ставится прокси-сервером расшифровке: расшифровка,
    которая молча ушла в облако, обязана отметиться здесь."""

    def __init__(self):
        srv = self
        self.hits = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                srv.hits.append(self.path)
                self.send_response(502)
                self.send_header("content-length", "0")
                self.end_headers()

            do_GET = do_POST = do_CONNECT = do_PUT = _any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def upd(uid, chat, text=None, voice=False):
    m = {"message_id": uid, "chat": {"id": int(chat)}, "from": {"id": int(chat)},
         "date": 1755000000}
    if voice:
        m["voice"] = {"file_id": "golos-1", "duration": 3}
    if text is not None:
        m["text"] = text
    return {"update_id": uid, "message": m}


def wait_port(port, sec=25):
    do = time.time() + sec
    while time.time() < do:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.25)
    return False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_code(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")
    except Exception as e:                                    # noqa: BLE001
        return 0, str(e)


# ── 1. Секрет ───────────────────────────────────────────────────────────────
SECRET_CONTRACT = """  scripts/themis_bot.py — бот-пульт. Секрет живёт ТОЛЬКО в окружении.
    --check [--config Ф]      готов ли бот к запуску: 0 — да, 1 — нет с причиной.
                              Печатает состояние (конфиг, есть ли секрет, задан ли chat_id)
                              и НЕ печатает значение секрета ни при каком исходе.
    Имя переменной с токеном берётся из конфига (bot.token_env), значение — из окружения.
    Секрет НЕ принимается аргументом командной строки: `--token` не существует, вызов
    с ним обязан оборваться разбором (чужой процесс читает argv через ps).
    Значение секрета не появляется ни в stdout, ни в stderr, ни в журнале аудита,
    ни в одном отслеживаемом git файле (форма токена Telegram там не встречается вовсе)."""


def check_secret():
    if not exists("themis_bot.py"):
        return [("themis_bot.py", "прибора нет. Контракт:\n" + SECRET_CONTRACT)]
    fails = []
    code, out, err = run([tool("themis_bot.py"), "--selftest"])
    if code != 0:
        fails.append(("themis_bot.py", f"--selftest вернул {code}: {(out + err).strip()[-300:]}"))

    # Токен аргументом — отказ. Аргументы видны в ps любому пользователю машины.
    code, out, err = run([tool("themis_bot.py"), "--check", "--token", FAKE_TOKEN])
    if code == 0:
        fails.append(("themis_bot.py", "секрет принят аргументом --token: argv виден в ps"))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cfg = Path(config(td / "config.json"))
        audit = td / "audit.log"
        env = bot_env(cfg, audit)
        code, out, err = run([tool("themis_bot.py"), "--check"], env=env)
        if code != 0:
            fails.append(("themis_bot.py", f"--check при заданном секрете вернул {code}: "
                                           f"{(out + err).strip()[:200]}"))
        if FAKE_TOKEN in out + err:
            fails.append(("themis_bot.py", "--check напечатал ЗНАЧЕНИЕ секрета"))

        # Неверный секрет: подставной Telegram отвечает 401. Жалоба не цитирует токен.
        with FakeTelegram([upd(1, OWNER_CHAT, "/status")]) as tg:
            bad = {**env, "THEMIS_TELEGRAM_BOT_TOKEN": FAKE_TOKEN + "-nevernyy"}
            code, out, err = run([tool("themis_bot.py"), "--once", "--api-base", tg.base],
                                 env=bad, timeout=120)
            if code == 0:
                fails.append(("themis_bot.py", "Telegram ответил 401, а бот вернул код 0"))
            if FAKE_TOKEN in out + err:
                fails.append(("themis_bot.py", "жалоба на отказ доступа несёт значение секрета"))

        with FakeTelegram([upd(2, OWNER_CHAT, "/status")]) as tg:
            run([tool("themis_bot.py"), "--once", "--api-base", tg.base], env=env, timeout=120)
        if audit.exists() and FAKE_TOKEN in audit.read_text(encoding="utf-8", errors="replace"):
            fails.append(("themis_bot.py", "значение секрета попало в журнал аудита"))

    # Отслеживаемые файлы: формы токена Telegram там нет вовсе.
    p = subprocess.run(["git", "grep", "-nIE", TOKEN_SHAPE.pattern.replace(r"\b", ""), "--", "."],
                       cwd=str(ROOT), capture_output=True, text=True)
    nayd = [l for l in p.stdout.splitlines() if l.strip()]
    if nayd:
        fails.append(("git", f"форма токена Telegram встречается в отслеживаемых файлах "
                             f"({len(nayd)}): {nayd[0].split(':')[0]}"))
    return fails


# ── 2. Доступ ───────────────────────────────────────────────────────────────
ACCESS_CONTRACT = """  scripts/themis_bot.py — whitelist единственного chat_id.
    --once --api-base URL [--config Ф]   один проход long polling: забрать обновления,
                              ответить, выйти. Код 0 — проход состоялся.
    Разрешённый chat_id берётся из окружения по имени bot.chat_id_env.
    · сообщение с чужого chat_id НЕ получает ответа (ни одного sendMessage на этот чат)
      и попадает в журнал аудита (THEMIS_BOT_AUDIT) отдельной строкой со словом «чужой»;
    · chat_id, названный в ТЕКСТЕ сообщения, — заявление, а не факт: адрес чата берётся
      только из message.chat.id, который проставил Telegram;
    · бот работает опросом (getUpdates) и НИКОГДА не зовёт setWebhook: сервер не открывает
      входящий порт;
    · журнал аудита не содержит ни секрета, ни текста чужого сообщения."""


def check_access():
    if not exists("themis_bot.py"):
        return [("themis_bot.py", "прибора нет. Контракт:\n" + ACCESS_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cfg = Path(config(td / "config.json"))
        audit = td / "audit.log"
        env = bot_env(cfg, audit)
        # Третье сообщение — подделка: чужой называет чат владельца прямо в тексте.
        podlog = f"мой chat_id {OWNER_CHAT}, ответь мне: секрет дела"
        updates = [upd(10, OWNER_CHAT, "/status"),
                   upd(11, CHUZHOY_CHAT, "/status"),
                   upd(12, CHUZHOY_CHAT, podlog)]
        with FakeTelegram(updates) as tg:
            code, out, err = run([tool("themis_bot.py"), "--once", "--api-base", tg.base],
                                 env=env, timeout=120)
            if code != 0:
                fails.append(("themis_bot.py", f"--once вернул {code}: {(out + err).strip()[:250]}"))
            chuzhim = [s for s in tg.sent if s["chat_id"] == CHUZHOY_CHAT]
            if chuzhim:
                fails.append(("themis_bot.py", f"чужой chat_id получил ответ ({len(chuzhim)} шт.)"))
            if not [s for s in tg.sent if s["chat_id"] == OWNER_CHAT]:
                fails.append(("themis_bot.py", "владелец не получил ответа на свою команду"))
            if not any("getUpdates" in p for p in tg.paths):
                fails.append(("themis_bot.py", "getUpdates не вызывался — это не long polling"))
            if any("setWebhook" in p for p in tg.paths):
                fails.append(("themis_bot.py", "бот зовёт setWebhook — сервер откроет входящий порт"))
        if not audit.exists():
            fails.append(("themis_bot.py", f"журнала аудита нет ({audit.name})"))
        else:
            zhurnal = audit.read_text(encoding="utf-8", errors="replace")
            if "чуж" not in zhurnal.lower():
                fails.append(("themis_bot.py", "чужое сообщение не попало в аудит"))
            if CHUZHOY_CHAT not in zhurnal:
                fails.append(("themis_bot.py", "аудит не называет чужой chat_id — след бесполезен"))
            if "секрет дела" in zhurnal:
                fails.append(("themis_bot.py", "аудит записал ТЕКСТ чужого сообщения — "
                                               "журнал стал вторым местом хранения чужого"))
    return fails


# ── 3. Речь: корпус исходящих и сторож на выходе ────────────────────────────
RECH_CONTRACT = """  scripts/themis_bot.py — что бот вообще умеет сказать.
    --templates --json        {"fixture": {"pd": {...}, "safe": {...}},
                               "templates": [{"name","text"}, ...]}
                              Каждый шаблон отрендерен НА ХУДШИХ данных: в fixture.pd лежат
                              ФИО, номер дела, ИНН, сумма и адрес, и они поданы шаблонам
                              на вход. Ни одно значение fixture.pd не смеет появиться
                              в тексте: бот — пульт, а не витрина дела. В fixture.safe —
                              то, что бот называть обязан (дата, время, счёт дел, ссылка);
                              приёмка сама проверяет, что объявленное безопасным
                              действительно безопасно, иначе объявлением можно обелить
                              что угодно. Обязательные имена шаблонов:
                              start, hearing, deadline, doc_ready, voice_ok, error.
                              Шаблон start объясняет владельцу границу тайны: называет
                              Telegram и говорит, что документ забирается по ссылке.
    --check-out ФАЙЛ          пропустил бы бот этот текст наружу: 0 — чисто, 1 — похоже
                              на персональные данные. Сторож строится на scripts/pii_gate.py,
                              а не на новой регулярке, и проверяется по ОБЕИМ осям:
                              ловит утечку и молчит на обиходе («Взыскано», «заседание
                              во вторник», «дел в работе: 12»).
    Весь корпус проходит `pii_gate --residual` с кодом 0, не содержит сумм денег
    и не содержит номеров дел."""

OBIKHOD = ["Взыскано по одному делу, остальные в работе.",
           "Заседание во вторник, напомню утром.",
           "Дел в работе: 12. Срочных нет.",
           "Документ готов, ссылка ниже — открывается только внутри сети.",
           "Ничего срочного. Отдыхайте."]
UTECHKI = ["Иванова Мария Петровна подала иск.",
           "По делу № А65-12345/2026 назначено заседание.",
           "ИНН 771234567890 проверен по ЕГРЮЛ.",
           "Паспорт 9203 456789 приобщён к материалам."]


def check_rech():
    if not exists("themis_bot.py"):
        return [("themis_bot.py", "прибора нет. Контракт:\n" + RECH_CONTRACT)]
    fails = []
    code, out, err = run([tool("themis_bot.py"), "--templates", "--json"])
    if code != 0:
        return fails + [("themis_bot.py", f"--templates --json вернул {code}: "
                                          f"{(out + err).strip()[:250]}")]
    try:
        d = json.loads(out)
        pd = d["fixture"]["pd"]
        safe = d["fixture"]["safe"]
        shablony = {t["name"]: t["text"] for t in d["templates"]}
    except (ValueError, KeyError, TypeError) as e:
        return fails + [("themis_bot.py", f"--templates --json не разобран ({e}): {out[:200]}")]

    for name in ("start", "hearing", "deadline", "doc_ready", "voice_ok", "error"):
        if name not in shablony:
            fails.append(("themis_bot.py", f"нет обязательного шаблона «{name}»"))
    if len(pd) < 4:
        fails.append(("themis_bot.py", f"fixture.pd из {len(pd)} значений — худший случай "
                                       "обязан нести ФИО, номер дела, ИНН, сумму и адрес"))
    ves = "\n".join(shablony.values())
    for klyuch, znach in pd.items():
        if znach and str(znach) in ves:
            fails.append(("themis_bot.py", f"значение fixture.pd «{klyuch}» ушло бы в Telegram"))
    # Объявление «это безопасно» проверяется, а не принимается на веру.
    with tempfile.TemporaryDirectory() as td_safe:
        f = Path(td_safe) / "safe.txt"
        f.write_text("\n".join(str(v) for v in safe.values()), encoding="utf-8")
        code_s, out_s, err_s = run([tool("pii_gate.py"), "--residual", str(f)])
        if code_s != 0:
            fails.append(("themis_bot.py", "в fixture.safe объявлено безопасным то, что "
                                           f"pii_gate считает ПД: {(out_s + err_s)[:150]}"))
    for klyuch, znach in safe.items():
        if DENGI.search(str(znach)) or NOMER_DELA.search(str(znach)):
            fails.append(("themis_bot.py", f"fixture.safe «{klyuch}» объявлено безопасным, "
                                           "а это сумма либо номер дела"))
    if DENGI.search(ves):
        fails.append(("themis_bot.py", f"в исходящем тексте сумма денег: "
                                       f"{DENGI.search(ves).group(0)!r}"))
    if NOMER_DELA.search(ves):
        fails.append(("themis_bot.py", f"в исходящем тексте номер дела: "
                                       f"{NOMER_DELA.search(ves).group(0)!r}"))
    start = shablony.get("start", "")
    if "telegram" not in start.lower() or "ссыл" not in start.lower():
        fails.append(("themis_bot.py", "первое сообщение не объясняет границу тайны: "
                                       "обязано назвать Telegram и ссылку на документ"))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        korpus = td / "korpus.txt"
        korpus.write_text(ves, encoding="utf-8")
        code, out, err = run([tool("pii_gate.py"), "--residual", str(korpus)])
        if code != 0:
            fails.append(("themis_bot.py", f"корпус шаблонов не прошёл pii_gate --residual: "
                                           f"{(out + err).strip()[:200]}"))
        # Ось «ложная тревога»: сторож, кричащий на обиходе, будет выключен в первый день.
        for i, text in enumerate(OBIKHOD + list(shablony.values())):
            f = td / f"obikhod_{i}.txt"
            f.write_text(text, encoding="utf-8")
            code, out, err = run([tool("themis_bot.py"), "--check-out", str(f)])
            if code != 0:
                fails.append(("themis_bot.py", f"ложная тревога на обиходе: {text[:60]!r}"))
        # Ось «пропуск»: настоящая утечка обязана быть остановлена.
        for i, text in enumerate(UTECHKI):
            f = td / f"utechka_{i}.txt"
            f.write_text(text, encoding="utf-8")
            code, out, err = run([tool("themis_bot.py"), "--check-out", str(f)])
            if code == 0:
                fails.append(("themis_bot.py", f"сторож пропустил бы наружу: {text[:60]!r}"))

        # Живой проход: владелец пишет боту текст с ПД. Эхо запрещено.
        cfg = Path(config(td / "config.json"))
        env = bot_env(cfg, td / "audit.log")
        with FakeTelegram([upd(20, OWNER_CHAT, "найди Иванову Марию Петровну, ИНН 771234567890")]) as tg:
            run([tool("themis_bot.py"), "--once", "--api-base", tg.base], env=env, timeout=120)
            ushlo = "\n".join(s["text"] for s in tg.sent)
            for sled in ("Иванов", "Марию", "771234567890"):
                if sled in ushlo:
                    fails.append(("themis_bot.py", f"бот вернул в Telegram эхо с ПД: {sled!r}"))
    return fails


# ── 4. Голос ────────────────────────────────────────────────────────────────
GOLOS_CONTRACT = """  scripts/voice_local.py — расшифровка голосового ТОЛЬКО на этой машине.
    --transcribe ФАЙЛ [--json]   печатает {"text","engine","local":true} и код 0.
                              Движок выбирается по платформе (на Маке — SMLTLK/Neural
                              Engine, на сервере — whisper); подставить свой можно
                              переменной THEMIS_STT_CMD (команда получает путь к файлу).
    · при расшифровке НОЛЬ исходящих сетевых обращений — проверяется счётчиком,
      поставленным прокси-сервером на весь трафик процесса;
    · локального движка нет → код 1 и честный отказ; молчаливого ухода в облачную
      расшифровку не бывает, и отказ не предлагает облако как замену;
    · звук не копируется никуда за пределы своего каталога;
    --selftest даёт 0 без сети.
  scripts/themis_bot.py --once — голосовое от владельца расшифровывается этим прибором,
    а ответ в Telegram НЕ ЦИТИРУЕТ расшифровку: сказанное вслух остаётся на машине."""


def check_golos():
    if not exists("voice_local.py"):
        return [("voice_local.py", "прибора нет. Контракт:\n" + GOLOS_CONTRACT)]
    fails = []
    code, out, err = run([tool("voice_local.py"), "--selftest"])
    if code != 0:
        fails.append(("voice_local.py", f"--selftest вернул {code}: {(out + err).strip()[-300:]}"))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        zvuk = td / "golos.oga"
        zvuk.write_bytes(b"OggS" + b"\x00" * 64)
        rasp = "Собери возражения по аренде к пятнице"
        dvizhok = stub(td / "stt.sh", f'echo "{rasp}"\n')

        with SchetchikProxy() as pr:
            env = {**os.environ, "THEMIS_STT_CMD": dvizhok,
                   "HTTPS_PROXY": f"http://127.0.0.1:{pr.port}",
                   "HTTP_PROXY": f"http://127.0.0.1:{pr.port}",
                   "ALL_PROXY": f"http://127.0.0.1:{pr.port}",
                   "NO_PROXY": ""}
            do = sorted(p.name for p in td.iterdir())
            code, out, err = run([tool("voice_local.py"), "--transcribe", str(zvuk), "--json"],
                                 env=env, timeout=180)
            if code != 0:
                fails.append(("voice_local.py", f"расшифровка подставным движком вернула {code}: "
                                                f"{(out + err).strip()[:250]}"))
            else:
                try:
                    d = json.loads(out)
                    if rasp.split()[0] not in d.get("text", ""):
                        fails.append(("voice_local.py", f"расшифровка потеряна: {out[:150]}"))
                    if d.get("local") is not True:
                        fails.append(("voice_local.py", "не объявляет расшифровку локальной"))
                except ValueError:
                    fails.append(("voice_local.py", f"--json не разобран: {out[:150]}"))
            if pr.hits:
                fails.append(("voice_local.py", f"при расшифровке ушло наружу {len(pr.hits)} "
                                                f"обращений: {pr.hits[:2]}"))
            posle = sorted(p.name for p in td.iterdir())
            lishnee = set(posle) - set(do) - {"golos.oga"}
            if any(n.endswith((".oga", ".ogg", ".wav", ".m4a", ".mp3")) for n in lishnee):
                fails.append(("voice_local.py", f"звук скопирован рядом: {sorted(lishnee)}"))

        # Движка нет — отказ, а не тихий уход в облако.
        pusto = {**NO_NET, "THEMIS_STT_CMD": str(td / "net-takogo-dvizhka"), "PATH": str(td)}
        code, out, err = run([tool("voice_local.py"), "--transcribe", str(zvuk)], env=pusto)
        if code == 0:
            fails.append(("voice_local.py", "без локального движка вернул успех — "
                                            "молчаливая деградация"))
        prilozheno = (out + err).lower()
        for oblako in ("openai", "api.openai", "облачн", "google", "яндекс", "yandex"):
            if oblako in prilozheno:
                fails.append(("voice_local.py", f"отказ предлагает облако как замену: {oblako!r}"))

        if exists("themis_bot.py"):
            cfg = Path(config(td / "config.json"))
            env = bot_env(cfg, td / "audit.log", {"THEMIS_STT_CMD": dvizhok})
            with FakeTelegram([upd(30, OWNER_CHAT, voice=True)]) as tg:
                code, out, err = run([tool("themis_bot.py"), "--once", "--api-base", tg.base],
                                     env=env, timeout=180)
                ushlo = "\n".join(s["text"] for s in tg.sent)
                if not tg.sent:
                    fails.append(("themis_bot.py", "на голосовое бот не ответил вовсе"))
                for slovo in rasp.split()[:3]:
                    if slovo in ushlo:
                        fails.append(("themis_bot.py", f"ответ цитирует расшифровку ({slovo!r}) — "
                                                       "сказанное вслух ушло в Telegram"))
                        break
    return fails


# ── 5. Пустой конфиг ────────────────────────────────────────────────────────
VYKL_CONTRACT = """  Пустой конфиг = бота нет, система работает.
    Конфига нет либо bot.enabled=false:
      · `themis_bot.py --check` → код 1 с внятной причиной;
      · `themis_bot.py --once --api-base URL` → код 1 и НИ ОДНОГО обращения к Telegram
        (выключенный бот не стучится даже разок);
      · `themis_status.py <дело> --brief` продолжает работать — система от бота не зависит."""


def check_vykl():
    if not exists("themis_bot.py"):
        return [("themis_bot.py", "прибора нет. Контракт:\n" + VYKL_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for imya, cfg in (("конфига нет", td / "netu.json"),
                          ("бот выключен", Path(config(td / "off.json", bot={
                              "enabled": False, "token_env": "THEMIS_TELEGRAM_BOT_TOKEN",
                              "chat_id_env": "THEMIS_TELEGRAM_CHAT_ID"})))):
            env = bot_env(cfg, td / "audit.log")
            code, out, err = run([tool("themis_bot.py"), "--check"], env=env)
            if code == 0:
                fails.append(("themis_bot.py", f"{imya}: --check объявил бота готовым"))
            with FakeTelegram([upd(40, OWNER_CHAT, "/status")]) as tg:
                code, out, err = run([tool("themis_bot.py"), "--once", "--api-base", tg.base],
                                     env=env, timeout=120)
                if code == 0:
                    fails.append(("themis_bot.py", f"{imya}: --once вернул успех"))
                if tg.paths:
                    fails.append(("themis_bot.py", f"{imya}: выключенный бот стучался в Telegram "
                                                   f"({tg.paths[:2]})"))
    smoke = os.path.join("cases", "ivanov-ivan", "razdel-imushchestva-2026")
    if (ROOT / smoke).is_dir():
        code, out, err = run([tool("themis_status.py"), smoke, "--brief"])
        if code != 0:
            fails.append(("themis_status.py", f"без бота система не работает: код {code}"))
    return fails


# ── 6. Ссылка ───────────────────────────────────────────────────────────────
SSYLKA_CONTRACT = """  Документ забирается по ссылке ВНУТРЬ приватной сети, не через Telegram.
    scripts/themis_bot.py --doc-link ПУТЬ_ОТ_КОРНЯ   печатает одну ссылку.
    scripts/themis_bot.py --miniapp-link             ссылка на мини-приложение.
    · ссылка начинается с адреса из конфига (server.url) — не с публичного файлохранилища;
    · ссылка НЕ несёт в себе токен панели: отправить токен в Telegram значит его разгласить;
      авторизация происходит на стороне панели;
    · ссылка не называет ни дело, ни доверителя: путь заменён непрозрачным идентификатором
      (`pii_gate --residual` по самой ссылке даёт 0);
    · панель отвечает на этот адрес 401 без токена и НЕ 401 с токеном — снаружи
      приватной сети ссылка не открывается;
    · мини-приложение (`/miniapp`) закрыто тем же токеном: список дел, заседания и сроки
      видны только владельцу."""


def check_ssylka():
    if not exists("themis_bot.py"):
        return [("themis_bot.py", "прибора нет. Контракт:\n" + SSYLKA_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        links = td / "doc-links.json"
        cfg = Path(config(td / "config.json"))
        env = bot_env(cfg, td / "audit.log", {"THEMIS_DOC_LINKS": str(links),
                                              "THEMIS_PANEL_TOKEN": "panel-proba-token"})
        put = "cases/ivanov-ivan/razdel-imushchestva-2026/.agent/drafts/vozrazheniya.docx"
        code, out, err = run([tool("themis_bot.py"), "--doc-link", put], env=env)
        if code != 0:
            return fails + [("themis_bot.py", f"--doc-link вернул {code}: {(out + err)[:250]}")]
        ssylka = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not ssylka.startswith("https://themis.vnutri.local"):
            fails.append(("themis_bot.py", f"ссылка ведёт не на адрес из конфига: {ssylka[:80]!r}"))
        if "token=" in ssylka or "panel-proba-token" in ssylka:
            fails.append(("themis_bot.py", "ссылка несёт токен панели — отправить его в Telegram "
                                           "значит разгласить"))
        for sled in ("ivanov", "razdel-imushchestva", "vozrazheniya"):
            if sled in ssylka.lower():
                fails.append(("themis_bot.py", f"ссылка называет дело или документ: {sled!r}"))
        f = td / "ssylka.txt"
        f.write_text(ssylka, encoding="utf-8")
        code, out, err = run([tool("pii_gate.py"), "--residual", str(f)])
        if code != 0:
            fails.append(("themis_bot.py", "сама ссылка не прошла pii_gate --residual"))

        code, out, err = run([tool("themis_bot.py"), "--miniapp-link"], env=env)
        mini = out.strip().splitlines()[-1].strip() if code == 0 and out.strip() else ""
        if not mini.startswith("https://themis.vnutri.local"):
            fails.append(("themis_bot.py", f"--miniapp-link дал {mini[:80]!r}"))
        if "token=" in mini:
            fails.append(("themis_bot.py", "ссылка на мини-приложение несёт токен"))

        # Панель: тот же адрес снаружи не открывается.
        if not (COCKPIT / "app.py").is_file():
            return fails + [("cockpit/app.py", "панели нет — некуда вести ссылке")]
        port = free_port()
        panel_token = "panel-proba-token"
        penv = {**os.environ, "THEMIS_PANEL_TOKEN": panel_token, "THEMIS_PANEL_HOST": "127.0.0.1",
                "THEMIS_PANEL_PORT": str(port), "THEMIS_ACCESS_LOG": str(td / "access.log"),
                "THEMIS_DOC_LINKS": str(links)}
        proc = subprocess.Popen([sys.executable, str(COCKPIT / "app.py")], cwd=str(ROOT),
                                env=penv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        try:
            if not wait_port(port):
                return fails + [("cockpit/app.py", "панель не поднялась за отведённое время")]
            for imya, url in (("документ", ssylka), ("мини-приложение", mini)):
                if not url:
                    continue
                mestnyy = url.replace("https://themis.vnutri.local", f"http://127.0.0.1:{port}")
                code_bez, _ = http_code(mestnyy)
                if code_bez != 401:
                    fails.append(("cockpit/app.py", f"{imya}: без токена панель ответила "
                                                    f"{code_bez}, а обязана 401"))
                code_s, telo = http_code(mestnyy, {"x-themis-token": panel_token})
                if code_s == 401:
                    fails.append(("cockpit/app.py", f"{imya}: с верным токеном тоже 401"))
                if imya == "мини-приложение" and code_s == 200 and "<" not in telo:
                    fails.append(("cockpit/app.py", "мини-приложение отдало не страницу"))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    return fails


# ── 7. Одно сообщение на событие ────────────────────────────────────────────
TISHINA_CONTRACT = """  Одно сообщение на одно событие; сказать нечего — бот молчит.
    scripts/themis_bot.py --notify-file ФАЙЛ --api-base URL
      · файл пуст (или в нём только пробелы) → НИ ОДНОГО sendMessage, код 0:
        молчание — тоже ответ;
      · файл с событием → РОВНО ОДНО сообщение, а не лента статусов;
      · текст уведомления проходит того же сторожа, что и шаблоны: уведомление с ПД
        наружу не уходит вовсе (код 1, ноль отправок), а не уходит «частично»."""


def check_tishina():
    if not exists("themis_bot.py"):
        return [("themis_bot.py", "прибора нет. Контракт:\n" + TISHINA_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cfg = Path(config(td / "config.json"))
        env = bot_env(cfg, td / "audit.log")

        pusto = td / "pusto.txt"
        pusto.write_text("   \n\n", encoding="utf-8")
        with FakeTelegram([]) as tg:
            code, out, err = run([tool("themis_bot.py"), "--notify-file", str(pusto),
                                  "--api-base", tg.base], env=env, timeout=120)
            if code != 0:
                fails.append(("themis_bot.py", f"пустое уведомление дало код {code}"))
            if tg.sent:
                fails.append(("themis_bot.py", f"сказать нечего, а бот написал {len(tg.sent)} раз"))

        sobytie = td / "sobytie.txt"
        sobytie.write_text("Заседание завтра в 10:00, документы собраны.\n", encoding="utf-8")
        with FakeTelegram([]) as tg:
            code, out, err = run([tool("themis_bot.py"), "--notify-file", str(sobytie),
                                  "--api-base", tg.base], env=env, timeout=120)
            if code != 0:
                fails.append(("themis_bot.py", f"уведомление о событии дало код {code}: "
                                               f"{(out + err)[:200]}"))
            if len(tg.sent) != 1:
                fails.append(("themis_bot.py", f"на одно событие ушло {len(tg.sent)} сообщений"))

        s_pd = td / "s_pd.txt"
        s_pd.write_text("Иванова Мария Петровна ждёт документ по делу № А65-12345/2026.\n",
                        encoding="utf-8")
        with FakeTelegram([]) as tg:
            code, out, err = run([tool("themis_bot.py"), "--notify-file", str(s_pd),
                                  "--api-base", tg.base], env=env, timeout=120)
            if code == 0:
                fails.append(("themis_bot.py", "уведомление с ПД принято к отправке"))
            if tg.sent:
                fails.append(("themis_bot.py", f"уведомление с ПД всё же ушло: "
                                               f"{tg.sent[0]['text'][:60]!r}"))
    return fails


# ── 8. Аватар ───────────────────────────────────────────────────────────────
AVATAR_CONTRACT = """  scripts/bot_avatar.py — изображение Фемиды для BotFather.
    --out ФАЙЛ [--size N]     кладёт PNG не меньше 512×512 и печатает путь; код 0.
    Установка картинки боту за владельцем: Bot API этого не умеет, наша часть — файл.
    · рисуется своим кодом, без сети и без внешних пакетов;
    · два прогона подряд дают побайтово одинаковый файл (картинка не пляшет от запуска);
    · под cases/ прибор не пишет ничего."""


def check_avatar():
    if not exists("bot_avatar.py"):
        return [("bot_avatar.py", "прибора нет. Контракт:\n" + AVATAR_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        a, b = td / "a.png", td / "b.png"
        for out_file in (a, b):
            code, out, err = run([tool("bot_avatar.py"), "--out", str(out_file)])
            if code != 0:
                return fails + [("bot_avatar.py", f"вернул {code}: {(out + err).strip()[:250]}")]
        if not a.exists():
            return fails + [("bot_avatar.py", "файла на диске нет, а код 0")]
        raw = a.read_bytes()
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            fails.append(("bot_avatar.py", "это не PNG"))
        else:
            shirina = int.from_bytes(raw[16:20], "big")
            vysota = int.from_bytes(raw[20:24], "big")
            if shirina < 512 or vysota < 512:
                fails.append(("bot_avatar.py", f"размер {shirina}×{vysota} — мельче 512×512"))
        if raw != b.read_bytes():
            fails.append(("bot_avatar.py", "два прогона дали разные файлы — картинка пляшет"))
    return fails


CHECKS = [
    ("секрет не всплывает нигде", check_secret, SECRET_CONTRACT),
    ("чужой chat_id — молчание и аудит", check_access, ACCESS_CONTRACT),
    ("исходящее без ПД, сумм и номеров дел", check_rech, RECH_CONTRACT),
    ("голос расшифрован локально", check_golos, GOLOS_CONTRACT),
    ("пустой конфиг — бота нет, система работает", check_vykl, VYKL_CONTRACT),
    ("ссылка ведёт внутрь и снаружи не открывается", check_ssylka, SSYLKA_CONTRACT),
    ("одно сообщение на событие, молчание — ответ", check_tishina, TISHINA_CONTRACT),
    ("аватар — файл на диске, повторяемый", check_avatar, AVATAR_CONTRACT),
]


def selftest():
    global SCRIPTS
    saved = SCRIPTS
    try:
        with tempfile.TemporaryDirectory() as td:
            SCRIPTS = Path(td)
            for imya, fn in (("check_secret", check_secret), ("check_access", check_access),
                             ("check_rech", check_rech), ("check_golos", check_golos),
                             ("check_vykl", check_vykl), ("check_ssylka", check_ssylka),
                             ("check_tishina", check_tishina), ("check_avatar", check_avatar)):
                assert fn(), f"{imya}: пропавший прибор не пойман"
    finally:
        SCRIPTS = saved
    # Подставной Telegram обязан работать сам по себе, иначе приёмка меряет себя.
    with FakeTelegram([upd(1, OWNER_CHAT, "/status")]) as tg:
        req = urllib.request.Request(f"{tg.base}/bot{FAKE_TOKEN}/getUpdates")
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        assert d["result"][0]["message"]["chat"]["id"] == int(OWNER_CHAT), "подставной Telegram врёт"
        code, _ = http_code(f"{tg.base}/botNEVERNYY/getUpdates")
        assert code == 401, "подставной Telegram пускает с чужим токеном"
    assert DENGI.search("Взыскано 1 250 000 руб."), "детектор сумм слеп"
    assert not DENGI.search("Дел в работе: 12"), "детектор сумм кричит на обиходе"
    assert NOMER_DELA.search("дело № А65-12345/2026"), "детектор номера дела слеп"
    assert not NOMER_DELA.search("заседание 20.08.2026 в 10:00"), "номер дела спутан с датой"
    assert not TOKEN_SHAPE.search(Path(__file__).read_text(encoding="utf-8")), \
        "сама приёмка содержит строку формы токена"
    print("selftest: приёмка краснеет на отсутствующих приборах, подставной Telegram честен — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Приёмка этапа 8 (пишет координатор).")
    ap.add_argument("--contracts", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.contracts:
        for title, _, contract in CHECKS:
            print(f"\n{title}:\n{contract}")
        return 0

    all_fails, done = [], 0
    for title, fn, _ in CHECKS:
        fails = fn()
        if fails:
            all_fails.append((title, fails))
        else:
            done += 1
        print(f"  {'✓' if not fails else '✗'} {title}")
    print(f"\nсдано проверок: {done}/{len(CHECKS)}")
    if not all_fails:
        print("✓ ЭТАП 8 ПРИНЯТ")
        return 0
    print("\nчто не сдано:")
    for title, fails in all_fails:
        for name, why in fails:
            print(f"\n· {name} — {title}\n  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
