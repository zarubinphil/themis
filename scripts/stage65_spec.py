#!/usr/bin/env python3
"""stage65_spec.py — приёмка этапа 6.5 «установка на чужую машину». Пишет КООРДИНАТОР.

Инвариант роя: generator ≠ verifier. Контракт снаружи, проверка чёрным ящиком —
через командную строку и чтение того, что скилл обещает человеку. Исполнитель
файл НЕ ПРАВИТ; правку ловит loop_gate (`spec:tampered`).

Честная граница. Живой клон на Windows и Linux отсюда не проверить: под рукой один
Mac. Поэтому платформенное поведение проверяется СИМУЛЯЦИЕЙ (`--platform`), а сам
прогон на чужой машине остаётся за владельцем. Симуляция обязана быть настоящей:
доктор, выдающий на Windows тот же ответ, что на macOS, приёмку не проходит.

Половина этапа хуже нуля (решение владельца): опознать платформу и промолчать о
замене — молчаливая деградация. Отсюда главная проверка: у КАЖДОЙ функции, которой
на платформе нет, названа замена. Обещанной функции без замены нет ни одной.

Пять работ:
  1. доктор называет платформу, версию ОС, архитектуру и список CLI — проверкой,
     а не опросом («есть ли у вас codex» спрашивать запрещено);
  2. для каждой из трёх платформ перечислено недоступное И замена по каждому пункту;
  3. SMLTLK — штатный компонент: на macOS ставится, на прочих честно объявляется
     неработающим с платформенной заменой той же функции;
  4. пустой конфиг = рабочая локальная система, сервер и бот выключены; чужого
     токена и чужого адреса сервера в умолчаниях нет;
  5. скилл themis-setup не только спрашивает, но и ОБЪЯСНЯЕТ устройство конвейера.

Выход: 0 — этап принят; 1 — есть несданное.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SKILL = ROOT / ".claude" / "skills" / "themis-setup" / "SKILL.md"
NO_NET = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1,localhost"}
PLATFORMS = ("darwin", "windows", "linux")


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


def doctor(plat=None, extra=()):
    argv = [tool("setup_doctor.py"), "--json", "--offline", *extra]
    if plat:
        argv += ["--platform", plat]
    code, out, err = run(argv, timeout=600)
    try:
        return code, json.loads(out), err
    except ValueError:
        return code, None, (out + err)


# ── 1. Опознание машины проверкой ───────────────────────────────────────────
PROBE_CONTRACT = """  scripts/setup_doctor.py --json [--platform darwin|windows|linux] [--offline]
    Печатает машинный отчёт, где названы фактом:
      platform     — darwin | windows | linux
      os_version   — версия ОС
      arch         — архитектура (arm64, x86_64)
      cli          — список [{name, present, authorized, how}] по КОМАНДЕ, не по опросу:
                     наличие проверяется запуском, авторизация — фактической проверкой
                     инструмента; «спросить у владельца, есть ли codex» запрещено.
    --platform подставляет чужую платформу для разбора и приёмки: ответ обязан
    отличаться от macOS, иначе разбор платформы — фикция."""


def check_probe():
    if not exists("setup_doctor.py"):
        return [("setup_doctor.py", "доктора нет. Контракт:\n" + PROBE_CONTRACT)]
    fails = []
    code, d, err = doctor()
    if d is None:
        return [("setup_doctor.py", f"--json не дал разбираемый отчёт: {err.strip()[:200]}")]
    for key in ("platform", "os_version", "arch", "cli"):
        if not d.get(key):
            fails.append(("setup_doctor.py", f"в отчёте нет «{key}» — машина не опознана"))
    cli = d.get("cli") or []
    if isinstance(cli, list) and cli:
        names = {c.get("name") for c in cli if isinstance(c, dict)}
        if "claude" not in names:
            fails.append(("setup_doctor.py", f"среди CLI нет claude: {sorted(names)}"))
        for c in cli:
            if not isinstance(c, dict) or "present" not in c or "authorized" not in c:
                fails.append(("setup_doctor.py", f"строка CLI без факта наличия/авторизации: {c}"))
                break
            if not c.get("how"):
                fails.append(("setup_doctor.py", f"не названа КОМАНДА проверки для {c.get('name')} "
                                                 "— значит, это опрос, а не проверка"))
                break
    else:
        fails.append(("setup_doctor.py", "список CLI пуст — проверка не выполнялась"))
    return fails


# ── 2. Недоступное названо с заменой ────────────────────────────────────────
SUBST_CONTRACT = """  scripts/setup_doctor.py --platform ПЛАТФОРМА --json
    Ключ `unavailable`: список [{what, why, replacement}] — что на этой платформе
    не работает, почему и ЧЕМ ЗАМЕНИТЬ. Пустая замена = молчаливая деградация,
    прямо запрещённая владельцем: половина этапа хуже нуля.
    На windows и linux список обязан покрывать как минимум: OCR сканов, расписание
    фоновых заданий, диктовку (SMLTLK), подпись и сборку PDF.
    На darwin список может быть пуст — там работает всё."""

MUST_COVER = ("ocr", "распис", "диктов", "подпис")


def check_substitutes():
    if not exists("setup_doctor.py"):
        return [("setup_doctor.py", "доктора нет. Контракт:\n" + SUBST_CONTRACT)]
    fails = []
    otvety = {}
    for plat in PLATFORMS:
        code, d, err = doctor(plat)
        if d is None:
            fails.append(("setup_doctor.py", f"--platform {plat}: отчёт не разобран: {err.strip()[:150]}"))
            continue
        otvety[plat] = json.dumps(d.get("unavailable", []), ensure_ascii=False, sort_keys=True)
        if d.get("platform") != plat:
            fails.append(("setup_doctor.py", f"--platform {plat} дал platform={d.get('platform')}"))
        un = d.get("unavailable")
        if un is None:
            fails.append(("setup_doctor.py", f"{plat}: нет ключа unavailable"))
            continue
        for item in un:
            if not isinstance(item, dict) or not item.get("replacement"):
                fails.append(("setup_doctor.py", f"{plat}: пункт без замены — {item}"))
        if plat != "darwin":
            svodka = " ".join(f"{i.get('what', '')} {i.get('why', '')}" for i in un).lower()
            for needle in MUST_COVER:
                if needle not in svodka:
                    fails.append(("setup_doctor.py", f"{plat}: не назван пункт «{needle}…» — "
                                                     "функция обещана, а на платформе её нет"))
    if len(set(otvety.values())) == 1 and otvety:
        fails.append(("setup_doctor.py", "все три платформы дали ОДИН ответ — разбор платформы фиктивен"))
    return fails


# ── 3. SMLTLK — штатный компонент ───────────────────────────────────────────
SMLTLK_CONTRACT = """  SMLTLK — штатный компонент, а не отдельный продукт (решение владельца 18.08.2026)
    Ставится тем же онбордингом. Доктор обязан сообщать его состояние ключом `smltlk`
    {available, why, replacement, how}:
      на darwin  — available true, названо КАК ставится и с чем связывается
                   (диктовка → voice-to-brief → бриф задачи);
      на прочих  — available false, сказано ПРЯМО, что приложение строки меню macOS
                   там не запускается, и названа платформенная замена той же функции.
    Скилл themis-setup обязан упоминать SMLTLK: молчание запрещено."""


def check_smltlk():
    if not exists("setup_doctor.py"):
        return [("setup_doctor.py", "доктора нет. Контракт:\n" + SMLTLK_CONTRACT)]
    fails = []
    for plat in PLATFORMS:
        code, d, err = doctor(plat)
        if d is None:
            fails.append(("setup_doctor.py", f"--platform {plat}: отчёт не разобран"))
            continue
        s = d.get("smltlk")
        if not isinstance(s, dict):
            fails.append(("setup_doctor.py", f"{plat}: нет раздела smltlk"))
            continue
        if plat == "darwin":
            if not s.get("available") or not s.get("how"):
                fails.append(("setup_doctor.py", f"darwin: SMLTLK объявлен недоступным либо "
                                                 f"не сказано, как ставится: {s}"))
        else:
            if s.get("available"):
                fails.append(("setup_doctor.py", f"{plat}: SMLTLK объявлен доступным — "
                                                 "это приложение строки меню macOS"))
            if not s.get("replacement"):
                fails.append(("setup_doctor.py", f"{plat}: SMLTLK без замены — молчаливая деградация"))
    if SKILL.is_file() and "smltlk" not in SKILL.read_text(encoding="utf-8").lower():
        fails.append(("themis-setup", "скилл молчит про SMLTLK, хотя он штатный компонент"))
    # Обещание «ставится тем же онбордингом» обязано иметь исполнителя. Установщик,
    # не знающий про SMLTLK, превращает штатный компонент в слова: человек прочтёт,
    # что он ставится, и не получит его.
    ustanovshchik = ROOT / "install.sh"
    if not ustanovshchik.is_file():
        fails.append(("install.sh", "установщика нет, а скилл велит его запускать"))
    else:
        text = ustanovshchik.read_text(encoding="utf-8").lower()
        if "smltlk" not in text:
            fails.append(("install.sh", "установщик не знает про SMLTLK — обещание "
                                        "«ставится тем же онбордингом» некому исполнить"))
        elif "--with-smltlk" not in text:
            fails.append(("install.sh", "шаг SMLTLK без явного согласия: сборка тянет Xcode "
                                        "и ~500 МБ модели, молча этого делать нельзя"))
    # Состояние компонента — факт с диска, а не обещание: доктор обязан сказать,
    # установлен он или нет, и назвать команду установки.
    code, d, err = doctor("darwin")
    if d is not None:
        s = d.get("smltlk") or {}
        if "installed" not in s:
            fails.append(("setup_doctor.py", "smltlk без поля installed — состояние "
                                             "компонента не проверено, а пересказано"))
    return fails


# ── 4. Пустой конфиг = рабочая локальная система ────────────────────────────
CONFIG_CONTRACT = """  scripts/themis_config.py — конфигурация установки
    --show [--config ФАЙЛ]   печатает действующие настройки машинно (JSON)
    --check [--config ФАЙЛ]  проверяет конфиг: код 0 — годен, 1 — назвать беду
    --selftest               без сети, код 0
    Конфига НЕТ либо он пуст → система работает локально: server.enabled false,
    bot.enabled false, инбокс по умолчанию. Это не ошибка и не предупреждение:
    большинству нужен один Mac без сервера и без бота.
    Чужого секрета и чужого адреса сервера в умолчаниях нет НИКОГДА: бот и сервер
    персональные (решение владельца), каждый заводит своего в BotFather.
    Конфиг с непустым server.url либо bot.token, лежащий В РЕПОЗИТОРИИ, — беда:
    --check обязан вернуть 1."""


def check_config():
    if not exists("themis_config.py"):
        return [("themis_config.py", "прибора нет. Контракт:\n" + CONFIG_CONTRACT)]
    fails = []
    code, out, err = run([tool("themis_config.py"), "--selftest"])
    if code != 0:
        fails.append(("themis_config.py", f"--selftest вернул {code}: {(out + err).strip()[-300:]}"))
    with tempfile.TemporaryDirectory() as td:
        pusto = Path(td) / "net-takogo.json"
        code, out, err = run([tool("themis_config.py"), "--show", "--config", str(pusto)])
        if code != 0:
            fails.append(("themis_config.py", f"без конфига --show вернул {code}: {(out + err)[:200]}"))
        else:
            try:
                d = json.loads(out)
            except ValueError:
                d = None
                fails.append(("themis_config.py", f"--show не дал JSON: {out.strip()[:150]}"))
            if d is not None:
                if (d.get("server") or {}).get("enabled"):
                    fails.append(("themis_config.py", "без конфига сервер включён"))
                if (d.get("bot") or {}).get("enabled"):
                    fails.append(("themis_config.py", "без конфига бот включён"))
                if (d.get("server") or {}).get("url"):
                    fails.append(("themis_config.py", "в умолчаниях чужой адрес сервера"))
                if (d.get("bot") or {}).get("token"):
                    fails.append(("themis_config.py", "в умолчаниях чужой токен бота"))
        code, out, err = run([tool("themis_config.py"), "--check", "--config", str(pusto)])
        if code != 0:
            fails.append(("themis_config.py", f"пустой конфиг объявлен негодным (код {code}) — "
                                              "локальная работа без сервера должна быть нормой"))
        chuzhoy = Path(td) / "config.json"
        chuzhoy.write_text(json.dumps({"bot": {"enabled": True, "token": "123456:CHUZHOY"}},
                                      ensure_ascii=False), encoding="utf-8")
        code, out, err = run([tool("themis_config.py"), "--check", "--config", str(chuzhoy)])
        if code == 0:
            fails.append(("themis_config.py", "секрет ВНУТРИ конфига принят — токен обязан "
                                              "жить в ~/.secrets, в конфиге только имя переменной"))
    tracked = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True)
    for name in tracked.stdout.splitlines():
        if name.endswith(("themis.config.json", "config.json")) and "example" not in name:
            fails.append(("themis_config.py", f"конфиг машины в git: {name}"))
    return fails


# ── 5. Онбординг объясняет, а не только спрашивает ──────────────────────────
SKILL_CONTRACT = """  .claude/skills/themis-setup/SKILL.md — разбор по образцу grill-me
    СПРАШИВАЕТ (по одному вопросу, не списком): профиль практики · есть ли сервер
    и какой либо только локально · нужны ли уведомления и токен СОБСТВЕННОГО бота
    из BotFather · куда класть материалы. Наличие CLI НЕ спрашивается, а проверяется
    командой (setup_doctor).
    ОБЪЯСНЯЕТ, а не только спрашивает: как устроен конвейер шагов, что такое маркеры
    и почему без них документ не запишется, почему оригиналы не покидают машину,
    где лежат готовые документы и где рабочие файлы агентов, что делать при первом деле.
    Человек на выходе понимает продукт, а не набор папок."""

SKILL_MUST = [("botfather", "свой бот в BotFather, чужой токен не подставляется"),
              ("сервер", "вопрос про сервер либо локальную работу"),
              ("маркер", "объяснение маркеров конвейера"),
              ("конвейер", "объяснение конвейера шагов"),
              ("gotovo", "где лежат готовые документы"),
              (".agent", "где рабочие файлы агентов"),
              ("первое дело", "что делать при первом деле"),
              ("оригинал", "почему оригиналы не покидают машину"),
              ("setup_doctor.py", "наличие CLI проверяется командой, а не вопросом")]


def check_skill():
    if not SKILL.is_file():
        return [("themis-setup", "скилла нет. Контракт:\n" + SKILL_CONTRACT)]
    text = SKILL.read_text(encoding="utf-8").lower()
    fails = [("themis-setup", f"скилл не даёт: {what}")
             for needle, what in SKILL_MUST if needle not in text]
    code, out, err = run([tool("sync_prompts.py")])
    if code != 0:
        fails.append(("sync_prompts.py", f"канон и производное разошлись: {(out + err).strip()[-200:]}"))
    return fails


CHECKS = [
    ("машина опознана проверкой", check_probe, PROBE_CONTRACT),
    ("недоступное названо с заменой", check_substitutes, SUBST_CONTRACT),
    ("SMLTLK — штатный компонент", check_smltlk, SMLTLK_CONTRACT),
    ("пустой конфиг = локальная работа", check_config, CONFIG_CONTRACT),
    ("онбординг объясняет продукт", check_skill, SKILL_CONTRACT),
]


def selftest():
    global SCRIPTS
    saved = SCRIPTS
    try:
        with tempfile.TemporaryDirectory() as td:
            SCRIPTS = Path(td)
            assert check_config(), "пропавший themis_config.py не пойман"
            assert check_probe(), "пропавший setup_doctor.py не пойман"
    finally:
        SCRIPTS = saved
    print("selftest: приёмка краснеет на отсутствующих приборах — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Приёмка этапа 6.5 (пишет координатор).")
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
        print("✓ ЭТАП 6.5 ПРИНЯТ")
        return 0
    print("\nчто не сдано:")
    for title, fails in all_fails:
        for name, why in fails:
            print(f"\n· {name} — {title}\n  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
