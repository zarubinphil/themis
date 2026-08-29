#!/usr/bin/env python3
"""stage6_spec.py — приёмка этапа 6 «сервер». Пишет КООРДИНАТОР, не исполнитель.

Инвариант роя: generator ≠ verifier. Контракт задан снаружи и проверяется чёрным
ящиком: панель поднимается настоящим сервером и опрашивается по сети, приборы —
через командную строку. Исполнитель этот файл НЕ ПРАВИТ; правку ловит loop_gate
(`spec:tampered`) сверкой с git.

Граница этапа. Здесь проверяется ГОТОВНОСТЬ к выкатке, а не выкатка: сама выкатка,
закрытие резолвера на сервере и заведение пользователя — действия наружу, их делает
владелец. Приёмка обязана доказать, что система безопасна ДО того, как её выставят.

Пять работ:
  1. изменяющие маршруты панели требуют аутентификации (аноним → 401/403);
  2. без секрета панель не соглашается слушать чужой интерфейс (fail-closed);
  3. частота запросов ограничена (перебор → 429);
  4. каждое изменяющее обращение попадает в аудит, и в аудите нет ни секрета,
     ни персональных данных;
  5. приёмник синхронизации берёт только извлечённый текст: оригинал, бинарник,
     симлинк и путь вне очереди отвергаются.
Плюс дисциплина секрета: значение токена не встречается ни в одном файле git.

Выход: 0 — этап принят; 1 — есть несданное (список с контрактами).
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NO_NET = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1,localhost"}
TOKEN = "priemka-tokena-etapa-6-ne-sekret"
# Изменяющие маршруты панели: каждый запускает работу, пишет на диск либо открывает файл.
MUTATING = [("POST", "/api/task", {"json": {"text": "проверка"}}),
            ("POST", "/api/run", {"json": {}}),
            ("POST", "/api/new-case", {"json": {"text": "проверка"}}),
            ("POST", "/api/learn-redline", {"json": {"path": "/tmp/net.docx"}}),
            ("POST", "/api/open", {"json": {"path": "/tmp/net.docx"}}),
            ("POST", "/api/upload", {"files": [("files", ("a.txt", b"x", "text/plain"))]})]
# С ВЕРНЫМ токеном дёргаем только те маршруты, что не запускают конвейер: проба
# аутентификации не должна поднимать настоящий прогон Claude по делам владельца
# (первый же вызов /api/task на старте приёмки его запустил — снято вручную).
NO_SPAWN = {"/api/open", "/api/upload", "/api/learn-redline"}


def run(argv, cwd=ROOT, timeout=300, env=None, stdin_text=None):
    try:
        p = subprocess.run([sys.executable, *argv], cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout, env=env or NO_NET,
                           input=(stdin_text if stdin_text is not None else ""))
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"таймаут {timeout} с"
    except OSError as e:
        return 127, "", str(e)


def tool(name):
    return str(SCRIPTS / name)


def exists(name):
    return (SCRIPTS / name).is_file()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Panel:
    """Настоящая панель, поднятая настоящим uvicorn. Опрашивается по сети."""

    def __init__(self, **env):
        self.env = {**os.environ, "THEMIS_INBOX": tempfile.mkdtemp(), **env}
        self.port = _free_port()
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--port", str(self.port),
             "--host", "127.0.0.1", "--log-level", "warning"],
            cwd=str(ROOT / "cockpit"), env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        import httpx
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            if self.proc.poll() is not None:
                break
            try:
                httpx.get(self.base + "/", timeout=1.0)
                return self
            except Exception:
                time.sleep(0.2)
        return self

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ask(self, method, path, token=None, **kw):
        import httpx
        headers = {"X-Themis-Token": token} if token else {}
        return httpx.request(method, self.base + path, headers=headers, timeout=20, **kw)

    def __exit__(self, *a):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ── 1. Аутентификация изменяющих маршрутов ──────────────────────────────────
AUTH_CONTRACT = """  cockpit/app.py — аутентификация
    Секрет берётся из окружения THEMIS_PANEL_TOKEN (значение — в $HOME/.secrets, не в git).
    Секрет задан → КАЖДЫЙ изменяющий маршрут (/api/task, /api/run, /api/new-case,
    /api/learn-redline, /api/open, /api/upload) требует его в заголовке
    X-Themis-Token либо в cookie того же имени:
      без токена   → 401
      чужой токен  → 401
      верный токен → маршрут работает как прежде (401 не возвращается)
    Открытие панели по ссылке /login?token=… ставит cookie, дальше браузер носит её сам:
    юрист не должен вводить заголовки руками.
    Статика (/, картинки) остаётся открытой — иначе панель не загрузится, чтобы
    спросить токен."""


def check_auth():
    app = ROOT / "cockpit" / "app.py"
    if not app.is_file():
        return [("cockpit/app.py", "панели нет. Контракт:\n" + AUTH_CONTRACT)]
    try:
        import httpx  # noqa: F401
    except ImportError:
        return [("cockpit/app.py", "для приёмки нужен httpx (стоит в окружении панели)")]
    fails = []
    with Panel(THEMIS_PANEL_TOKEN=TOKEN) as p:
        if not p.alive():
            out = (p.proc.stdout.read() if p.proc.stdout else "")[-400:]
            return [("cockpit/app.py", f"панель не поднялась с токеном: {out}")]
        for method, path, kw in MUTATING:
            r = p.ask(method, path, **kw)
            if r.status_code not in (401, 403):
                fails.append(("auth", f"{method} {path} без токена: ждали 401/403, "
                                      f"вышло {r.status_code}"))
            r = p.ask(method, path, token="chuzhoy-token", **kw)
            if r.status_code not in (401, 403):
                fails.append(("auth", f"{method} {path} с чужим токеном: ждали 401/403, "
                                      f"вышло {r.status_code}"))
            if path in NO_SPAWN:
                r = p.ask(method, path, token=TOKEN, **kw)
                if r.status_code in (401, 403):
                    fails.append(("auth", f"{method} {path} с ВЕРНЫМ токеном отбит {r.status_code} — "
                                          "юрист не сможет работать"))
        r = p.ask("GET", "/login", params={"token": TOKEN})
        if r.status_code >= 400 or "themis" not in str(r.cookies).lower() + str(r.headers).lower():
            fails.append(("auth", f"/login?token=… не поставил cookie: {r.status_code} "
                                  f"{dict(r.headers).get('set-cookie', '—')}"))
        r = p.ask("GET", "/")
        if r.status_code != 200:
            fails.append(("auth", f"статика закрыта ({r.status_code}) — панель не загрузится"))
    return fails


# ── 2. Без секрета — только петля ───────────────────────────────────────────
BIND_CONTRACT = """  cockpit/app.py — fail-closed по интерфейсу
    Панель без заданного THEMIS_PANEL_TOKEN не имеет чем отличить владельца от чужого,
    поэтому НЕ СОГЛАШАЕТСЯ слушать не-петлевой интерфейс: запуск с --host 0.0.0.0
    (или THEMIS_PANEL_HOST=0.0.0.0) завершается ненулевым кодом с внятной причиной
    ещё до приёма первого запроса. С токеном тот же запуск разрешён.
    Умолчание остаётся прежним: 127.0.0.1:8800, локальная работа без токена."""


def check_bind():
    app = ROOT / "cockpit" / "app.py"
    if not app.is_file():
        return [("cockpit/app.py", "панели нет. Контракт:\n" + BIND_CONTRACT)]
    fails = []
    env = {**os.environ, "THEMIS_INBOX": tempfile.mkdtemp(), "THEMIS_PANEL_HOST": "0.0.0.0",
           "THEMIS_PANEL_PORT": str(_free_port())}
    env.pop("THEMIS_PANEL_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=str(ROOT / "cockpit"), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        text = proc.communicate(timeout=20)[0] or ""
        code = proc.returncode
    except subprocess.TimeoutExpired:
        # Не завершилась — значит согласилась слушать и уже принимает запросы.
        proc.kill()
        proc.wait(timeout=10)
        text, code = "", 0
    if code == 0:
        fails.append(("bind", "панель без секрета согласилась слушать 0.0.0.0 — "
                              "выставленная наружу без пароля"))
    elif "токен" not in text.lower() and "секрет" not in text.lower():
        fails.append(("bind", f"отказ не назвал причину: {text.strip()[-200:]}"))
    with Panel(THEMIS_PANEL_TOKEN=TOKEN) as pan:
        if not pan.alive():
            fails.append(("bind", "с токеном панель не поднимается — запрет шире, чем нужно"))
    return fails


# ── 3. Ограничение частоты ──────────────────────────────────────────────────
RATE_CONTRACT = """  cockpit/app.py — ограничение частоты
    Порог задаётся THEMIS_RATE_LIMIT (запросов в минуту на клиента, по умолчанию 60).
    Перебор изменяющих запросов сверх порога → 429 и отказ БЕЗ выполнения работы.
    Порог из окружения обязателен: иначе приёмка вынуждена гонять сотни запросов."""


def check_rate():
    app = ROOT / "cockpit" / "app.py"
    if not app.is_file():
        return [("cockpit/app.py", "панели нет. Контракт:\n" + RATE_CONTRACT)]
    fails = []
    with Panel(THEMIS_PANEL_TOKEN=TOKEN, THEMIS_RATE_LIMIT="3") as p:
        if not p.alive():
            return [("rate", "панель не поднялась")]
        codes = [p.ask("POST", "/api/open", token=TOKEN, json={"path": "/tmp/net.docx"}).status_code
                 for _ in range(6)]
        if 429 not in codes:
            fails.append(("rate", f"перебор не пойман: коды {codes}"))
        if codes[0] == 429:
            fails.append(("rate", "первый же запрос отбит 429 — порог не работает"))
    # Подделка адреса. Заголовок X-Forwarded-For ставит кто угодно, а панель за
    # обратным прокси видит его как правду. Меняя фальшивый адрес на каждом запросе,
    # клиент получает НОВОЕ ведро лимита — то есть лимита нет.
    with Panel(THEMIS_PANEL_TOKEN=TOKEN, THEMIS_RATE_LIMIT="3") as p:
        if not p.alive():
            return fails + [("rate", "панель не поднялась")]
        import httpx
        codes = [httpx.post(p.base + "/api/open", timeout=15,
                            headers={"X-Themis-Token": TOKEN, "X-Forwarded-For": f"10.0.0.{i}"},
                            json={"path": "/tmp/net.docx"}).status_code for i in range(6)]
        if 429 not in codes:
            fails.append(("rate", f"смена фальшивого адреса обошла лимит: коды {codes}"))
    return fails


# ── 4. Аудит доступа ────────────────────────────────────────────────────────
AUDIT_CONTRACT = """  cockpit/app.py — аудит доступа
    Каждое обращение к изменяющему маршруту пишется строкой в файл из
    THEMIS_ACCESS_LOG (по умолчанию — рядом с audit.log проекта): время, метод,
    маршрут, адрес клиента, исход (ok/401/429).
    В аудите НЕТ секрета и НЕТ содержимого запроса: журнал доступа читают чаще,
    чем дела, и он не должен становиться вторым местом хранения тайны."""


def check_audit():
    app = ROOT / "cockpit" / "app.py"
    if not app.is_file():
        return [("cockpit/app.py", "панели нет. Контракт:\n" + AUDIT_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "access.log"
        with Panel(THEMIS_PANEL_TOKEN=TOKEN, THEMIS_ACCESS_LOG=str(log)) as p:
            if not p.alive():
                return [("audit", "панель не поднялась")]
            p.ask("POST", "/api/open", json={"path": "/tmp/net.docx"})                    # 401
            p.ask("POST", "/api/open", token=TOKEN, json={"path": "/tmp/Иванова_иск.docx"})
        if not log.is_file():
            return [("audit", f"журнал доступа не создан: {log}")]
        text = log.read_text(encoding="utf-8", errors="replace")
        if "/api/open" not in text:
            fails.append(("audit", f"обращение не записано: {text[:200]}"))
        if "401" not in text:
            fails.append(("audit", "отказ по аутентификации в журнале не виден"))
        if TOKEN in text:
            fails.append(("audit", "СЕКРЕТ ПОПАЛ В ЖУРНАЛ — журнал стал вторым местом тайны"))
        if "Иванова" in text:
            fails.append(("audit", "содержимое запроса (ФИО) попало в журнал доступа"))
    # Адрес из заголовка — не адрес, а ЗАЯВЛЕНИЕ клиента. Журнал, записавший его
    # как факт, врёт ровно тогда, когда его читают: при разборе чужого доступа.
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "access.log"
        with Panel(THEMIS_PANEL_TOKEN=TOKEN, THEMIS_ACCESS_LOG=str(log)) as p:
            if not p.alive():
                return fails + [("audit", "панель не поднялась")]
            import httpx
            httpx.post(p.base + "/api/open", timeout=15,
                       headers={"X-Themis-Token": TOKEN, "X-Forwarded-For": "203.0.113.7"},
                       json={"path": "/tmp/net.docx"})
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        line = [l for l in text.splitlines() if "/api/open" in l]
        if line and "203.0.113.7" in line[-1] and "заявл" not in line[-1]:
            fails.append(("audit", f"подделанный адрес записан как факт: {line[-1]}"))
    return fails


# ── 5. Приёмник синхронизации ───────────────────────────────────────────────
SYNC_CONTRACT = """  scripts/sync_receiver.py — приёмник синхронизации Mac → сервер
    Решение владельца: на сервер уходит только ИЗВЛЕЧЁННЫЙ ТЕКСТ, оригиналы — никогда.
      --queue КАТАЛОГ --accept ФАЙЛ --as ОТНОСИТЕЛЬНЫЙ_ПУТЬ
        код 0 — принято, файл лежит в очереди по указанному пути;
        код 1 — отвергнуто, причина названа, в очереди НИЧЕГО не появилось.
    Отвергается (каждое — отдельной причиной):
      · оригинал документа (.pdf .docx .xlsx .pptx .doc .rtf и прочая первичка);
      · бинарник (содержимое не UTF-8) под любым именем — .txt с байтами PDF тоже;
      · симлинк — за ним может стоять что угодно вне очереди;
      · путь вне очереди (../, абсолютный, симлинк-каталог) — обход не проходит.
    Принимается: .md/.txt/.json с текстом. --selftest даёт 0 без сети."""


def check_sync():
    if not exists("sync_receiver.py"):
        return [("sync_receiver.py", "прибора нет. Контракт:\n" + SYNC_CONTRACT)]
    fails = []
    code, out, err = run([tool("sync_receiver.py"), "--selftest"])
    if code != 0:
        fails.append(("sync_receiver.py", f"--selftest вернул {code}: {(out + err).strip()[-400:]}"))
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        q = td / "queue"
        q.mkdir()
        good = td / "vypiska.md"
        good.write_text("# карта дела\nтекст извлечён", encoding="utf-8")
        original = td / "isk.pdf"
        original.write_bytes(b"%PDF-1.4\nfake\n")
        binary = td / "pohozhe_na_tekst.txt"
        binary.write_bytes(b"%PDF-1.4\n\x00\x01\x02\xff\xfe")
        link = td / "ssylka.md"
        link.symlink_to(original)

        def accept(src, rel):
            return run([tool("sync_receiver.py"), "--queue", str(q), "--accept", str(src),
                        "--as", rel])

        code, out, err = accept(good, "delo/karta.md")
        if code != 0:
            fails.append(("sync_receiver.py", f"текст отвергнут: {(out + err).strip()[:200]}"))
        elif not (q / "delo" / "karta.md").is_file():
            fails.append(("sync_receiver.py", "принято, но файла в очереди нет"))

        pusto = td / "pusto.md"
        pusto.write_bytes(b"")
        for src, rel, why in ((pusto, "delo/karta.md", "пустой файл поверх принятого "
                                                       "(обрезанная выжимка затирает целую)"),
                              (good, "a\\..\\..\\b.md", "обратные слеши в пути"),
                              (good, "/".join(["a"] * 300) + "/k.md", "путь в 300 сегментов"),
                              (original, "delo/isk.pdf", "оригинал документа"),
                              (binary, "delo/b.txt", "бинарник под видом текста"),
                              (link, "delo/l.md", "симлинк"),
                              (good, "../beglec.md", "путь вне очереди (..)"),
                              (good, "/etc/themis.md", "абсолютный путь")):
            code, out, err = accept(src, rel)
            if code == 0:
                fails.append(("sync_receiver.py", f"ПРИНЯТ {why} — обязан быть отвергнут"))
        strays = [p for p in td.rglob("*") if p.is_file() and "queue" not in p.parts
                  and p.name in ("beglec.md", "themis.md")]
        if strays or (q.parent / "beglec.md").exists():
            fails.append(("sync_receiver.py", f"запись вне очереди состоялась: {strays}"))
        left = sorted(p.relative_to(q).as_posix() for p in q.rglob("*") if p.is_file())
        if left != ["delo/karta.md"]:
            fails.append(("sync_receiver.py", f"в очереди лишнее: {left}"))
    return fails


# ── 6. Дисциплина секрета и протокол сервера ────────────────────────────────
SECRET_CONTRACT = """  Секрет и протокол сервера
    Ни один отслеживаемый git файл не ПРИСВАИВАЕТ значение переменной секрета
    (`THEMIS_PANEL_TOKEN=…`): в коде живёт только имя переменной, значение —
    в $HOME/.secrets. Читать сам файл секретов приёмка не имеет права, поэтому
    проверяется форма, а не совпадение со значением.
    Журнал доступа (access.log) закрыт .gitignore: в нём адреса и время обращений,
    репозиторий публичный.
    knowledge/server-protocol.md существует и называет поимённо: отдельного
    непривилегированного пользователя, порядок перезагрузки/отзыва/восстановления
    ключа, закрытие открытого DNS-резолвера, что результат режима 2 на сервере —
    .docx, а подпись и PDF остаются на Mac (решение владельца 18.08.2026)."""

PROTOCOL_MUST = [("непривилегированн", "отдельный пользователь без прав администратора"),
                 ("отзыв", "отзыв ключа"),
                 ("восстановлен", "восстановление доступа"),
                 ("резолвер", "закрытие открытого DNS-резолвера"),
                 ("docx", "результат режима 2 на сервере"),
                 ("подпись", "подпись и PDF остаются на Mac")]


def check_secret_and_protocol():
    fails = []
    # Присваивание значения переменной секрета в отслеживаемом файле — утечка.
    # Само ИМЯ переменной встречается в коде и в документации законно.
    r = subprocess.run(["git", "grep", "-I", "-n", "-E",
                        r"THEMIS_PANEL_TOKEN\s*=\s*[\"\'A-Za-z0-9]", "--",
                        ".", ":(exclude)scripts/stage6_spec.py"],
                       cwd=str(ROOT), capture_output=True, text=True)
    hits = [l for l in r.stdout.splitlines()
            if "os.environ" not in l and "get(" not in l]
    if hits:
        fails.append(("секрет", "значение секрета присвоено в отслеживаемом файле: "
                                + hits[0][:200]))
    gi = ROOT / ".gitignore"
    if not gi.is_file() or "access.log" not in gi.read_text(encoding="utf-8"):
        fails.append(("секрет", "access.log не закрыт .gitignore — журнал доступа "
                                "с адресами уедет в публичный репозиторий"))
    doc = ROOT / "knowledge" / "server-protocol.md"
    if not doc.is_file():
        fails.append(("server-protocol.md", "протокола сервера нет. Контракт:\n" + SECRET_CONTRACT))
        return fails
    text = doc.read_text(encoding="utf-8").lower()
    for needle, what in PROTOCOL_MUST:
        if needle not in text:
            fails.append(("server-protocol.md", f"протокол не называет: {what}"))
    return fails


CHECKS = [
    ("изменяющие маршруты требуют токен", check_auth, AUTH_CONTRACT),
    ("без секрета — только петля", check_bind, BIND_CONTRACT),
    ("частота запросов ограничена", check_rate, RATE_CONTRACT),
    ("доступ пишется в аудит без тайны", check_audit, AUDIT_CONTRACT),
    ("приёмник берёт только текст", check_sync, SYNC_CONTRACT),
    ("секрет вне git, протокол назван", check_secret_and_protocol, SECRET_CONTRACT),
]


def selftest():
    """Приёмка обязана краснеть на отсутствующем приборе — иначе она не приёмка."""
    global SCRIPTS
    saved = SCRIPTS
    try:
        with tempfile.TemporaryDirectory() as td:
            SCRIPTS = Path(td)
            assert check_sync(), "пропавший sync_receiver.py не пойман"
    finally:
        SCRIPTS = saved
    print("selftest: приёмка краснеет на отсутствующем приборе — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Приёмка этапа 6 (пишет координатор).")
    ap.add_argument("--contracts", action="store_true", help="напечатать контракты и выйти")
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
        print("✓ ЭТАП 6 ПРИНЯТ")
        return 0
    print("\nчто не сдано:")
    for title, fails in all_fails:
        for name, why in fails:
            print(f"\n· {name} — {title}\n  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
