#!/usr/bin/env python3
"""stage7_spec.py — приемка этапа 7 «три CLI». Пишет КООРДИНАТОР, не исполнитель.

Инвариант роя: generator ≠ verifier. Контракт снаружи, проверка черным ящиком —
командной строкой и подставными CLI, которые записывают, ЧТО они увидели. Исполнитель
файл НЕ ПРАВИТ; правку ловит loop_gate (`spec:tampered`).

Честная граница. Настоящий вызов Codex или Kimi — отправка наружу, и делает ее владелец,
а не приемка: замер и пилот из плана здесь не исполняются. Проверяется то, что решает
судьбу тайны доверителя: что именно уходит за границу процесса и что остается, когда
чужой CLI отказал. Подставной CLI для этого лучше настоящего — он показывает свое
окружение целиком.

Почему так строго. `claude_guard.py` не пересекает границу процесса: за ней наших гейтов
нет вовсе. Чужой CLI возвращает ТЕКСТ, а на диск пишет всегда Claude через наши ворота.
Материалы дела, ФИО и реквизиты за границу не уходят никогда (адвокатская тайна,
ст. 8 ФЗ № 63-ФЗ); уходит обезличенный правовой вопрос.

Четыре работы:
  1. `cli_probe` — пять исходов вместо «работает/нет», с кешем и протуханием отказа;
  2. `foreign_cli` — герметичное окружение: чужой CLI видит только обезличенный
     временный каталог, наших секретов в его окружении нет, stdin закрыт;
  3. отказ ничего не пишет — ни в дело, ни рядом;
  4. сиденья ролей — роли с сырыми ПД остаются на claude, цепочка всегда кончается им.

Выход: 0 — этап принят; 1 — есть несданное.
"""
import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NO_NET = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1,localhost"}
# Текст с персональными данными: именно он не должен уйти за границу процесса.
S_PD = ("Иванова Мария Петровна, ИНН 771234567890, дело № А65-1234/2026: "
        "применима ли ст. 333 ГК РФ к неустойке по договору подряда?\n")
UTECHKI = ("Иванова", "Мария", "Петровна", "771234567890", "А65-1234/2026")


def run(argv, cwd=ROOT, timeout=300, env=None):
    try:
        p = subprocess.run([sys.executable, *argv], cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout, env=env or NO_NET, input="")
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
    """Подставной CLI. Пишется на диск и запускается как настоящий."""
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return str(path)


# ── 1. Пять исходов пробы ───────────────────────────────────────────────────
PROBE_CONTRACT = """  scripts/cli_probe.py — доступность чужого CLI, пять исходов вместо «работает/нет»
    --provider ИМЯ --json [--probe-cmd КОМАНДА] [--workdir КАТАЛОГ] [--timeout СЕК]
              [--cache ФАЙЛ] [--now ЭПОХА]
    Печатает {"provider", "outcome", "detail", "cached"} и код 0 при outcome=ok,
    иначе 1. Исходы:
      ok         — бинарник есть, вход выполнен, среда пишет
      no_binary  — команды нет в PATH («command -v» проверкой доступности не считается,
                   но отсутствие бинарника — отдельный исход, а не общий отказ)
      no_auth    — инструмент отвечает, что вход не выполнен
      no_quota   — отвечает, что кончилась квота либо лимит
      no_write   — рабочий каталог недоступен для записи (чужому CLI некуда работать)
      timeout    — не ответил за отведенное время
    Кеш (--cache): повторная проба возвращает cached=true. Отказ по квоте БЕЗ даты
    сброса протухает через 5 часов — запись несет `until`, и после него проба идет
    заново (проверяется через --now, чтобы не ждать пять часов).
    --selftest дает 0 без сети."""


def reestr(td: Path, cmd: str, imya: str = "proba") -> str:
    """Подставной реестр CLI: исполнитель задается строкой реестра, а не свободной
    командой. Прежде приемка звала `--provider X --cmd путь` — тот же шов, которым
    материалы дела уходили мимо роутера (контракт 9.9). Приемка не вправе держать
    открытым шов, который сама объявляет закрытым.
    """
    proba = stub(td / f"{imya}_proba.sh", 'echo "logged in"\nexit 0\n')
    put = td / f"reestr_{imya}.json"
    put.write_text(json.dumps({
        imya: {"probe": [proba], "invoke": [cmd], "model": "proba-max",
               "effort": "max", "data_classes": ["text", "public", "infra"]},
        "claude": {"probe": [proba], "invoke": [cmd], "model": "opus",
                   "effort": "max", "data_classes": ["pd", "text", "public", "infra"]},
    }, ensure_ascii=False), encoding="utf-8")
    return str(put)


def po_roli(td: Path, cmd: str, prompt, **kw) -> list:
    """argv вызова коннектора по РОЛИ через подставной реестр."""
    argv = [tool("foreign_cli.py"), "--role", "hunter-leaf", "--prompt", str(prompt),
            "--registry", reestr(td, cmd), "--cache", str(td / "probe_cache.json")]
    for flag, val in kw.items():
        if val is not None:
            argv += [f"--{flag}", str(val)]
    return argv


def check_probe():
    if not exists("cli_probe.py"):
        return [("cli_probe.py", "прибора нет. Контракт:\n" + PROBE_CONTRACT)]
    fails = []
    code, out, err = run([tool("cli_probe.py"), "--selftest"])
    if code != 0:
        fails.append(("cli_probe.py", f"--selftest вернул {code}: {(out + err).strip()[-300:]}"))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        work = td / "work"
        work.mkdir()
        cache = td / "probe.json"
        ok_cmd = stub(td / "ok.sh", 'echo "Logged in using ChatGPT"; exit 0\n')
        auth_cmd = stub(td / "auth.sh", 'echo "not logged in"; exit 1\n')
        quota_cmd = stub(td / "quota.sh", 'echo "usage limit reached"; exit 1\n')
        slow_cmd = stub(td / "slow.sh", 'sleep 30\n')
        ro = td / "ro"
        ro.mkdir()
        ro.chmod(0o500)

        def probe(extra, want, why, provider=None):
            # У каждого исхода свой провайдер: кеш прибора помнит отказ, и общий
            # ключ маскировал бы четыре следующих пробы первым же результатом.
            code, out, err = run([tool("cli_probe.py"), "--provider", provider or want,
                                  "--json", "--cache", str(cache), *extra], timeout=120)
            try:
                d = json.loads(out)
            except ValueError:
                fails.append(("cli_probe.py", f"{why}: вывод не JSON: {(out + err)[:150]}"))
                return None
            if d.get("outcome") != want:
                fails.append(("cli_probe.py", f"{why}: ждали «{want}», вышло «{d.get('outcome')}» "
                                              f"({d.get('detail', '')[:80]})"))
            if want == "ok" and code != 0:
                fails.append(("cli_probe.py", f"{why}: исход ok, а код возврата {code}"))
            if want != "ok" and code == 0:
                fails.append(("cli_probe.py", f"{why}: исход {want}, а код возврата 0"))
            return d

        probe(["--probe-cmd", ok_cmd, "--workdir", str(work)], "ok", "рабочий CLI")
        probe(["--probe-cmd", str(td / "net-takoy-komandy.sh"), "--workdir", str(work)],
              "no_binary", "бинарника нет")
        probe(["--probe-cmd", auth_cmd, "--workdir", str(work)], "no_auth", "вход не выполнен")
        d = probe(["--probe-cmd", quota_cmd, "--workdir", str(work)], "no_quota", "квота кончилась")
        probe(["--probe-cmd", ok_cmd, "--workdir", str(ro)], "no_write", "каталог только на чтение")
        probe(["--probe-cmd", slow_cmd, "--workdir", str(work), "--timeout", "1"],
              "timeout", "не ответил вовремя")

        # Кеш и протухание отказа по квоте: пять часов, потом проба заново.
        if d:
            code, out, err = run([tool("cli_probe.py"), "--provider", "no_quota", "--json",
                                  "--cache", str(cache), "--probe-cmd", ok_cmd,
                                  "--workdir", str(work)])
            try:
                povtor = json.loads(out)
            except ValueError:
                povtor = {}
            if not povtor.get("cached"):
                fails.append(("cli_probe.py", "повторная проба не взялась из кеша — "
                                              "отказ по квоте будет долбиться каждый вызов"))
            until = povtor.get("until") or d.get("until")
            if not until:
                fails.append(("cli_probe.py", "у отказа нет срока `until` — протухание не проверить"))
            else:
                code, out, err = run([tool("cli_probe.py"), "--provider", "no_quota", "--json",
                                      "--cache", str(cache), "--probe-cmd", ok_cmd,
                                      "--workdir", str(work), "--now", str(int(until) + 1)])
                try:
                    posle = json.loads(out)
                except ValueError:
                    posle = {}
                if posle.get("cached"):
                    fails.append(("cli_probe.py", "после срока отказ все еще из кеша — "
                                                  "квота восстановилась, а мы ее не видим"))
                if posle.get("outcome") != "ok":
                    fails.append(("cli_probe.py", f"после срока проба дала «{posle.get('outcome')}», "
                                                  "хотя CLI отвечает нормально"))
    return fails


# ── 2. Герметичность чужого CLI ─────────────────────────────────────────────
HERMETIC_CONTRACT = """  scripts/foreign_cli.py — вызов чужого CLI за границей процесса
    --provider ИМЯ --prompt ФАЙЛ [--cmd КОМАНДА] [--timeout СЕК] [--out ФАЙЛ]
              [--log ФАЙЛ]
    Порядок обязателен и fail-closed:
      1. обезличивание: `pii_gate --mask`; реквизитов не нашлось — текст проверяется
         `pii_gate --residual`, и грязный текст НЕ УХОДИТ (код 1);
      2. рабочий каталог — временный, и в нем ТОЛЬКО обезличенный файл: ни дела,
         ни репозитория, ни соседних материалов;
      3. окружение вычищено: наших секретов и переменных THEMIS_* там нет, PATH
         фиксирован, stdin закрыт (человеческий гейт не утекает в чужой процесс);
      4. успех по трем сигналам: код 0, ответ непуст, в ответе нет маркеров отказа;
      5. журнал отправок без исходного текста — провайдер, время, длина, отпечаток.
    Возврат: 0 — текст получен; 1 — отказ с причиной. Ответ печатается на stdout
    либо кладется в --out. В каталог дела не пишется НИЧЕГО ни при каком исходе."""


def _tree(root: Path):
    return {str(p.relative_to(root)): (p.stat().st_size if p.is_file() else -1)
            for p in sorted(root.rglob("*"))}


def check_hermetic():
    if not exists("foreign_cli.py"):
        return [("foreign_cli.py", "прибора нет. Контракт:\n" + HERMETIC_CONTRACT)]
    fails = []
    code, out, err = run([tool("foreign_cli.py"), "--selftest"])
    if code != 0:
        fails.append(("foreign_cli.py", f"--selftest вернул {code}: {(out + err).strip()[-300:]}"))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        otchet = td / "chto_uvidel.txt"
        # Подставной CLI записывает все, что ему досталось: каталог, окружение, stdin.
        vidok = stub(td / "vidok.sh", f'''
{{
  echo "CWD=$PWD"
  echo "FILES=$(ls -A . | tr "\\n" ",")"
  echo "ENV=$(env | cut -d= -f1 | tr "\\n" ",")"
  echo "STDIN=[$(cat)]"
  echo "ARGS=$*"
}} > {otchet}
echo "Ответ модели: неустойка снижается по ст. 333 ГК РФ при явной несоразмерности."
exit 0
''')
        vopros = td / "vopros.txt"
        vopros.write_text(S_PD, encoding="utf-8")
        otvet = td / "otvet.txt"
        log = td / "otpravki.log"
        env = {**NO_NET, "THEMIS_PANEL_TOKEN": "sekret-paneli-ne-dolzhen-utech",
               "DADATA_API_KEY": "kluch-dadata-ne-dolzhen-utech"}
        code, out, err = run(po_roli(td, vidok, vopros, out=otvet, log=log),
                             timeout=180, env=env)
        if code != 0:
            fails.append(("foreign_cli.py", f"вызов с подставным CLI вернул {code}: "
                                            f"{(out + err).strip()[-200:]}"))
        if not otchet.is_file():
            fails.append(("foreign_cli.py", "подставной CLI не запускался — проверять нечего"))
            return fails
        vid = otchet.read_text(encoding="utf-8", errors="replace")
        stroki = dict(l.split("=", 1) for l in vid.splitlines() if "=" in l)

        files = [f for f in (stroki.get("FILES") or "").split(",") if f]
        if len(files) != 1:
            fails.append(("foreign_cli.py", f"в рабочем каталоге не один файл, а {files} — "
                                            "чужой CLI видит лишнее"))
        cwd = (stroki.get("CWD") or "")
        # Проверяем принадлежность КОРНЮ ПРОЕКТА, а не вхождение слова: временный
        # каталог прибора законно называется themis-foreign-…, и поиск подстроки
        # краснел на честном имени.
        if cwd and (str(ROOT) in os.path.realpath(cwd)):
            fails.append(("foreign_cli.py", f"рабочий каталог внутри проекта: {cwd}"))

        env_keys = (stroki.get("ENV") or "").split(",")
        for k in env_keys:
            if k.startswith("THEMIS_") or "TOKEN" in k or "API_KEY" in k or "SECRET" in k:
                fails.append(("foreign_cli.py", f"наш секрет утек в окружение чужого CLI: {k}"))
                break
        if not any(k == "PATH" for k in env_keys):
            fails.append(("foreign_cli.py", "в окружении нет PATH — так CLI просто не запустится"))
        if (stroki.get("STDIN") or "").strip() not in ("[]", ""):
            fails.append(("foreign_cli.py", f"stdin не закрыт: {stroki.get('STDIN')[:60]}"))

        # Главное: что именно ушло за границу процесса.
        peredano = "\n".join([vid, (Path(files[0]).name if files else "")])
        soderzhimoe = ""
        for f in files:
            p = Path(cwd) / f if cwd else None
            if p and p.is_file():
                soderzhimoe = p.read_text(encoding="utf-8", errors="replace")
        celikom = peredano + soderzhimoe + (stroki.get("ARGS") or "")
        for utechka in UTECHKI:
            if utechka in celikom:
                fails.append(("foreign_cli.py", f"ЗА ГРАНИЦУ УШЛО «{utechka}» — "
                                                "обезличивание не сработало"))
                break

        # Враждебная проба 19.08.2026: формы ПД, которых не было в первом контракте.
        # Обе прошли границу процесса целиком.
        formy = [
            ("ФИО латиницей",
             "Kuznetsova Maria filed a claim about the penalty clause.\n", "Kuznetsova"),
            ("учреждение ребенка",
             "Ребенок 8 лет учится в гимназии № 7 г. Казани, живет с матерью.\n", "гимнази"),
            ("СНИЛС без метки",
             "Пенсионные права: 112-233-445 95 — спорный период стажа.\n", "112-233-445"),
            ("кадастровый номер объекта",
             "Объект 16:50:011725:1234 — квартира, предмет раздела.\n", "16:50:011725"),
            ("государственный номер автомобиля",
             "Автомобиль А123ВС 116 — предмет раздела имущества.\n", "А123ВС"),
            ("адрес объекта спора",
             "Спор о квартире: г. Казань, ул. Баумана, д. 12, кв. 5, площадь 54 кв. м.\n",
             "Баумана"),
            ("путь к папке дела (фамилия доверителя в имени)",
             "Материалы лежат в cases/kuznetsova-as/razvod-2026, карта готова.\n",
             "kuznetsova-as"),
        ]
        # Второй круг пробы: соседний каталог, размер запроса и подмененный файл.
        sosed = stub(td / "sosed.sh", f'ls -A .. | wc -l > {td / "sosedi.txt"}\necho "ответ"\n')
        chistyy = td / "chistyy.txt"
        chistyy.write_text("Применима ли ст. 333 ГК РФ к неустойке?\n", encoding="utf-8")
        run(po_roli(td, sosed, chistyy), timeout=120)
        sosedi = (td / "sosedi.txt")
        if sosedi.is_file():
            try:
                skolko = int(sosedi.read_text().strip())
            except ValueError:
                skolko = -1
            if skolko > 2:
                fails.append(("foreign_cli.py", f"на уровень выше рабочего каталога видно "
                                                f"{skolko} записей — «только обезличенный "
                                                "каталог» перестает быть правдой"))
        # Запрос уходит аргументом командной строки: у него есть предел ОС, и упереться
        # в него посреди прогона хуже, чем отказать сразу и внятно.
        ogromnyy = td / "ogromnyy.txt"
        ogromnyy.write_text("Вопрос про неустойку. " * 60000, encoding="utf-8")
        code, out, err = run(po_roli(td, vidok, ogromnyy), timeout=180)
        if code == 0:
            fails.append(("foreign_cli.py", "запрос в 1,3 МБ принят — предел аргумента ОС "
                                            "рванет в бою, а не на приемке"))
        elif "предел" not in (out + err).lower() and "велик" not in (out + err).lower():
            fails.append(("foreign_cli.py", f"отказ по размеру без внятной причины: "
                                            f"{(out + err).strip()[:150]}"))
        # Симлинк вместо файла запроса: за ним может стоять что угодно, включая дело.
        ssylka = td / "ssylka.txt"
        if not ssylka.exists():
            ssylka.symlink_to(chistyy)
        code, out, err = run(po_roli(td, vidok, ssylka), timeout=120)
        if code == 0:
            fails.append(("foreign_cli.py", "симлинк принят как файл запроса — за ним "
                                            "может стоять материал дела"))

        for why, text, sled in formy:
            f = td / f"proba_{abs(hash(why))}.txt"
            f.write_text(text, encoding="utf-8")
            o = td / f"o_{abs(hash(why))}.txt"
            run(po_roli(td, vidok, f, out=o), timeout=180)
            vid2 = otchet.read_text(encoding="utf-8", errors="replace")
            if sled.lower() in vid2.lower():
                fails.append(("foreign_cli.py", f"ЗА ГРАНИЦУ УШЕЛ {why}: «{sled}». "
                                                "Адрес — место жительства, имя папки дела — "
                                                "фамилия доверителя; и то и другое ПД"))
        if otvet.is_file() and "333" not in otvet.read_text(encoding="utf-8"):
            fails.append(("foreign_cli.py", "ответ чужого CLI не сохранен"))
        if log.is_file():
            zhurnal = log.read_text(encoding="utf-8", errors="replace")
            for utechka in UTECHKI:
                if utechka in zhurnal:
                    fails.append(("foreign_cli.py", f"журнал отправок содержит «{utechka}»"))
                    break
        else:
            fails.append(("foreign_cli.py", "журнал отправок не ведется"))
    return fails


# ── 3. Отказ не пишет на диск дела ──────────────────────────────────────────
OTKAZ_CONTRACT = """  scripts/foreign_cli.py — отказ
    Чужой CLI отказал (код не 0, пустой ответ, таймаут) либо обезличивание не прошло:
      · код возврата 1 и причина названа;
      · файл --out НЕ создается: половина ответа хуже отсутствия;
      · каталог дела побайтово тот же, что до вызова — ни файла, ни каталога, ни
        временного мусора. Чужой ответ на диск дела не попадает НИКОГДА: пишет
        Claude через наши ворота, а не приборы связи."""


def check_otkaz():
    if not exists("foreign_cli.py"):
        return [("foreign_cli.py", "прибора нет. Контракт:\n" + OTKAZ_CONTRACT)]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        delo = td / "cases" / "ivanov-ivan" / "delo-2026" / ".agent" / "context"
        delo.mkdir(parents=True)
        (delo / "knowledge-map.md").write_text("# карта\n## КАРТА ГОТОВА ✓\n", encoding="utf-8")
        do = _tree(td / "cases")

        padayushchiy = stub(td / "padayushchiy.sh", 'echo "error: upstream refused" >&2; exit 1\n')
        pustoy = stub(td / "pustoy.sh", 'exit 0\n')
        vopros = td / "vopros.txt"
        vopros.write_text(S_PD, encoding="utf-8")
        gryaznyy = td / "gryaznyy.txt"
        gryaznyy.write_text("Дело ведет Сидоров Петр, паспорт 1234 567890.\n", encoding="utf-8")

        for cmd, src, why in ((padayushchiy, vopros, "чужой CLI упал"),
                              (pustoy, vopros, "чужой CLI ответил пустотой")):
            out_file = td / f"otvet_{Path(cmd).stem}.txt"
            code, out, err = run(po_roli(td, cmd, src, out=out_file), timeout=180)
            if code == 0:
                fails.append(("foreign_cli.py", f"{why}: вернулся код 0"))
            if out_file.exists():
                fails.append(("foreign_cli.py", f"{why}: файл ответа все равно создан"))
        posle = _tree(td / "cases")
        if posle != do:
            fails.append(("foreign_cli.py", f"каталог дела изменился при отказе: "
                                            f"{set(posle) ^ set(do) or 'размеры'}"))
    return fails


# ── 4. Сиденья ролей ────────────────────────────────────────────────────────
SEATS_CONTRACT = """  scripts/cli_seats.py — какая роль на каком CLI сидит
    --role ИМЯ [--json]   печатает {"role", "data_class", "chain"}
    --list [--json]       все роли
    Классы данных: `pd` (сырые персональные данные), `text` (обезличенный текст),
    `public` (норма, Пленум, опубликованный акт), `infra` (код самой Фемиды).
    Железные правила, проверяемые прибором:
      · роль класса `pd` сидит ТОЛЬКО на claude — за границей процесса наших гейтов нет;
      · любая цепочка ЗАКАНЧИВАЕТСЯ claude: он сам харнесс и всегда доступен;
      · роль, которая пишет артефакты под маркерами (составитель, председатели советов,
        картограф, сверщик, читатели первички), — класс `pd`;
      · подмена claude чужим CLI при его недоступности запрещена: это смена гарантий,
        а не деградация.
    --selftest дает 0 без сети."""

PD_ROLES = ["case-mapper", "case-reconciler", "doc-drafter", "pdf-reader", "image-reader",
            "docx-reader", "inbox-triage"]
TEXT_ROLES = ["hunter-leaf", "council-reviewer"]


def check_seats():
    if not exists("cli_seats.py"):
        return [("cli_seats.py", "прибора нет. Контракт:\n" + SEATS_CONTRACT)]
    fails = []
    code, out, err = run([tool("cli_seats.py"), "--selftest"])
    if code != 0:
        fails.append(("cli_seats.py", f"--selftest вернул {code}: {(out + err).strip()[-300:]}"))
    code, out, err = run([tool("cli_seats.py"), "--list", "--json"])
    try:
        seats = json.loads(out)
    except ValueError:
        return fails + [("cli_seats.py", f"--list --json не разобран: {(out + err)[:200]}")]
    by_name = {s.get("role"): s for s in seats} if isinstance(seats, list) else {}
    for role in PD_ROLES:
        s = by_name.get(role)
        if not s:
            fails.append(("cli_seats.py", f"роль {role} не описана — сиденье не назначено"))
            continue
        if s.get("data_class") != "pd":
            fails.append(("cli_seats.py", f"{role}: класс «{s.get('data_class')}», а роль видит "
                                          "сырые персональные данные"))
        if s.get("chain") != ["claude"]:
            fails.append(("cli_seats.py", f"{role}: цепочка {s.get('chain')} — роль с ПД обязана "
                                          "сидеть только на claude"))
    for role in TEXT_ROLES:
        s = by_name.get(role)
        if not s:
            fails.append(("cli_seats.py", f"роль {role} не описана"))
            continue
        if s.get("data_class") == "pd":
            fails.append(("cli_seats.py", f"{role}: обезличенный текст помечен классом pd"))
    for s in seats if isinstance(seats, list) else []:
        chain = s.get("chain") or []
        if not chain or chain[-1] != "claude":
            fails.append(("cli_seats.py", f"{s.get('role')}: цепочка {chain} не кончается claude — "
                                          "подмена харнесса чужим CLI запрещена"))
            break
    return fails


CHECKS = [
    ("пять исходов пробы", check_probe, PROBE_CONTRACT),
    ("чужой CLI видит только обезличенное", check_hermetic, HERMETIC_CONTRACT),
    ("отказ не пишет ничего", check_otkaz, OTKAZ_CONTRACT),
    ("сиденья ролей держат границу ПД", check_seats, SEATS_CONTRACT),
]


def selftest():
    global SCRIPTS
    saved = SCRIPTS
    try:
        with tempfile.TemporaryDirectory() as td:
            SCRIPTS = Path(td)
            assert check_probe(), "пропавший cli_probe.py не пойман"
            assert check_hermetic(), "пропавший foreign_cli.py не пойман"
            assert check_seats(), "пропавший cli_seats.py не пойман"
    finally:
        SCRIPTS = saved
    print("selftest: приемка краснеет на отсутствующих приборах — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Приемка этапа 7 (пишет координатор).")
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
        print("✓ ЭТАП 7 ПРИНЯТ")
        return 0
    print("\nчто не сдано:")
    for title, fails in all_fails:
        for name, why in fails:
            print(f"\n· {name} — {title}\n  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
