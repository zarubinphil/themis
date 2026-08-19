#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_doctor.py — проверка окружения Фемиды ФАКТОМ, а не предположением.

ЗАЧЕМ. Репозиторий публичный, ставить его будут на чужие машины. Установщик,
который печатает «готово» и оставляет систему наполовину рабочей, хуже
отсутствующего: юрист узнает о том, что OCR не собран, когда карта дела выйдет
пустой, и спишет это на «модель не справилась».

ПРИНЦИП. Каждая проверка — КОМАНДА, а не догадка. Не «на macOS Vision есть», а
запустить bin/vision-doc и посмотреть код возврата. Не «markitdown стоит», а
`python3 -c "import markitdown"`. Не «сеть работает», а curl к конкретному
адресу с проверкой HTTP-кода.

МОЛЧАЛИВАЯ ДЕГРАДАЦИЯ ЗАПРЕЩЕНА. Проверка либо зелёная, либо красная с точной
командой починки. Нет промежуточного «вроде бы работает»: у каждой красной
строки есть поле «как починить» с командой, которую можно скопировать.

ПЛАТФОРМЫ. Apple Vision (bin/vision-doc) — только macOS. На Windows и Linux его
нет и не будет: это системный фреймворк Apple. Доктор обязан это НАЗВАТЬ, а не
промолчать, и указать, что именно на этой платформе не работает и чем заменить.
Затронуты: OCR-маршрут markdown_extract.py, фоновые агенты (launchd на macOS,
Планировщик задач на Windows, systemd-таймеры на Linux), пути с кириллицей.

    python3 scripts/setup_doctor.py            # отчёт человеку
    python3 scripts/setup_doctor.py --json     # машинный вывод для скилла setup
    python3 scripts/setup_doctor.py --offline  # без сетевых проверок
    python3 scripts/setup_doctor.py --selftest # проверка самого доктора

Код возврата: 0 — всё критичное на месте; 1 — есть КРИТИЧНОЕ; 2 — только
предупреждения (система работает, часть возможностей недоступна).
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CRIT, WARN, OK = "КРИТИЧНО", "ПРЕДУПРЕЖДЕНИЕ", "ок"

# Минимальная версия Python. Проект использует `X | None` в аннотациях (PEP 604)
# и match-выражения — на 3.9 файлы не импортируются вовсе.
PY_MIN = (3, 10)

# Сетевые каналы: адрес, зачем нужен, критичен ли. Проверяются ОДНИМ curl с
# чтением HTTP-кода — «сайт открывается в браузере» доказательством не считается.
NET_CHECKS = [
    ("ЦБ РФ (ключевая ставка, ст. 395 ГК)",
     "https://www.cbr.ru/scripts/XML_daily.asp", False),
    ("Производственный календарь (сроки)",
     "https://isdayoff.ru/api/getdata?year=2026&cc=ru", False),
    ("sudact.ru (поиск практики)", "https://sudact.ru/robots.txt", False),
    ("publication.pravo.gov.ru (сверка НПА)",
     "http://publication.pravo.gov.ru/document/0001202411300011", False),
]

# Шрифт стандарта оформления (DOCX_FORMATTING.md §1) — ОДИН, решение владельца
# 04.08.2026. Без него .docx соберётся, но Word подставит свой — документ уйдёт
# в суд не в том виде, в каком проверялся.
FONTS = ["PT Serif"]


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """(код возврата, вывод). Команды нет — код 127, как в оболочке."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "команда не найдена"
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, f"{type(e).__name__}"
    out = (r.stdout or b"").decode("utf-8", "replace") + \
          (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out.strip()


def check(name: str, status: str, detail: str = "", fix: str = "") -> dict:
    return {"проверка": name, "статус": status, "что видно": detail, "как починить": fix}


def platform_id() -> str:
    """Один словарь платформ на весь проект: darwin | windows | linux.

    Раньше macOS звалась здесь «macos», а в отчётах и промптах — «darwin»; два имени
    одного и того же расходятся ровно тогда, когда по ним что-то сравнивают.
    Подстановка чужой платформы (--platform) нужна и разбору с владельцем, и приёмке:
    доктор, выдающий на Windows тот же ответ, что на Маке, ничего не проверил.
    """
    forced = (os.environ.get("THEMIS_PLATFORM") or "").strip().lower()
    if forced:
        return {"macos": "darwin", "mac": "darwin", "osx": "darwin"}.get(forced, forced)
    s = platform.system()
    return {"Darwin": "darwin", "Windows": "windows", "Linux": "linux"}.get(s, s.lower())


def scheduler_of(plat: str) -> str:
    return {"darwin": "launchd (scripts/*.plist)",
            "windows": "Планировщик задач (schtasks /create)",
            "linux": "systemd-таймеры (systemctl --user enable --now)"}.get(plat, "неизвестен")


def check_python() -> dict:
    v = sys.version_info[:3]
    if v[:2] < PY_MIN:
        return check("Python", CRIT, f"{v[0]}.{v[1]}.{v[2]}, нужен ≥ "
                     f"{PY_MIN[0]}.{PY_MIN[1]}",
                     "поставить Python 3.10+ (python.org либо brew install python@3.12); "
                     "на 3.9 скрипты проекта не импортируются — синтаксис аннотаций новее")
    return check("Python", OK, f"{v[0]}.{v[1]}.{v[2]}")


def check_binary(name: str, cmd: list[str], why: str, fix: str,
                 critical: bool = True) -> dict:
    if not shutil.which(cmd[0]):
        return check(name, CRIT if critical else WARN, f"{cmd[0]} не найден в PATH ({why})", fix)
    rc, out = run(cmd)
    if rc not in (0, 1):
        return check(name, CRIT if critical else WARN,
                     f"{cmd[0]} есть, но не отвечает (код {rc})", fix)
    return check(name, OK, out.splitlines()[0][:60] if out else cmd[0])


def check_module(mod: str, why: str, critical: bool = True) -> dict:
    rc, out = run([sys.executable, "-c", f"import {mod}"])
    if rc != 0:
        return check(f"пакет {mod}", CRIT if critical else WARN, f"не импортируется ({why})",
                     f"{os.path.basename(sys.executable)} -m pip install {mod}")
    return check(f"пакет {mod}", OK, "импортируется")


def check_ocr(plat: str) -> list[dict]:
    """OCR-движок. На macOS обязан быть собран; на других платформах его нет."""
    doc = os.path.join(ROOT, "bin", "vision-doc")
    if plat != "darwin":
        return [check(
            "OCR сканов (Apple Vision)", CRIT,
            f"платформа {plat}: bin/vision-doc — системный фреймворк Apple, здесь его НЕТ. "
            "Текстовые PDF, DOCX и XLSX читаются через markitdown и работают; "
            "СКАНЫ И ФОТО ДОКУМЕНТОВ не читаются вовсе",
            "варианта два: (1) вести дела со сканами на macOS; (2) подключить свой "
            "движок OCR — реализовать интерфейс bin/vision-doc (вход: файл, "
            "выход: page_NNN.txt и page_NNN.md с таблицами) и указать путь в "
            "THEMIS_VISION_DOC. Прежде чем ставить чужую модель, сравнить её на "
            "реальных сканах дела с текущим движком: PaddleOCR, Surya, MinerU и "
            "Unlimited-OCR уже отклонены по замеру (knowledge/lessons-log.md)")]
    out = []
    if not (os.path.isfile(doc) and os.access(doc, os.X_OK)):
        out.append(check("OCR сканов (Apple Vision)", CRIT,
                         "bin/vision-doc отсутствует или не исполняемый",
                         "swiftc -O bin/vision-doc.swift -o bin/vision-doc && "
                         "chmod +x bin/vision-doc  (нужен Xcode CLT: xcode-select --install)"))
    else:
        rc, o = run([doc, "--help"], timeout=25)
        out.append(check("OCR сканов (Apple Vision)", OK if rc in (0, 1) else CRIT,
                         f"bin/vision-doc отвечает (код {rc})" if rc in (0, 1)
                         else f"bin/vision-doc не запускается (код {rc}): {o[:80]}",
                         "" if rc in (0, 1) else "пересобрать: swiftc -O bin/vision-doc.swift "
                                                 "-o bin/vision-doc"))
    if not shutil.which("swiftc"):
        out.append(check("swiftc (сборка OCR)", WARN, "не найден — пересобрать движок нечем",
                         "xcode-select --install"))
    return out


def check_fonts(plat: str) -> dict:
    """Шрифты стандарта. Читаются из системы, а не предполагаются."""
    if plat == "darwin":
        rc, out = run(["system_profiler", "SPFontsDataType"], timeout=60)
        haystack = out
    elif plat == "linux":
        rc, haystack = run(["fc-list"], timeout=30)
    else:
        rc, haystack = run(["powershell", "-NoProfile", "-Command",
                            "(New-Object System.Drawing.Text.InstalledFontCollection)"
                            ".Families.Name"], timeout=40)
    if rc not in (0, 1) or not haystack:
        return check("шрифты .docx", WARN, "список шрифтов системы не прочитан — проверка не выполнена",
                     "проверить вручную наличие: " + ", ".join(FONTS))
    missing = [f for f in FONTS if f.lower() not in haystack.lower()]
    if missing:
        return check("шрифты .docx", WARN, "не установлен: " + ", ".join(missing),
                     "поставить PT Serif (бесплатный, SIL OFL: fonts.google.com или "
                     "paratype.ru); без него Word подставит свой, и документ уйдёт "
                     "в суд не в том виде, в каком проверялся")
    return check("шрифты .docx", OK, "PT Serif на месте")


def check_net(offline: bool) -> list[dict]:
    if offline:
        return [check("сетевые каналы", WARN, "проверка пропущена (--offline)", "")]
    out = []
    for name, url, critical in NET_CHECKS:
        rc, body = run(["curl", "-sL", "-o", os.devnull, "-m", "20",
                        "-w", "%{http_code}", url], timeout=30)
        code = (body or "").strip().splitlines()[-1] if body else ""
        ok = code.isdigit() and int(code) < 400
        out.append(check(name, OK if ok else (CRIT if critical else WARN),
                         f"HTTP {code or '—'}",
                         "" if ok else "канал недоступен: проверить интернет и "
                                       "выход в сеть (часть госресурсов не отвечает "
                                       "с иностранных IP)"))
    return out


def check_corpus() -> list[dict]:
    out = []
    for sub, what, need in (("kodeksy", "кодексы", 5), ("plenumy", "Пленумы ВС РФ", 50)):
        d = os.path.join(ROOT, "knowledge", sub)
        n = len([f for f in os.listdir(d)]) if os.path.isdir(d) else 0
        out.append(check(f"корпус права: {what}", OK if n >= need else CRIT,
                         f"{n} файлов",
                         "" if n >= need else
                         "выгрузить: python3 scripts/update_legal_corpus.py --init "
                         "(кодексы) и --plenums (Пленумы). Без корпуса cite.py не "
                         "отдаёт дословных норм, а doc-drafter обязан их цитировать"))
    return out


def check_selftests() -> dict:
    """Проверки самих скриптов. Красный selftest на чистой машине — это дефект установки."""
    names = ["cite.py", "gosposhlina.py", "quality_gate.py", "document_guard.py",
             "practice_search.py", "verify_requisites.py", "sroki.py",
             "token_ledger.py", "registry_check.py", "practice_harvest.py"]
    bad = []
    for n in names:
        path = os.path.join(ROOT, "scripts", n)
        if not os.path.isfile(path):
            bad.append(f"{n}: файла нет")
            continue
        rc, _ = run([sys.executable, path, "--selftest"], timeout=180)
        if rc != 0:
            bad.append(f"{n}: код {rc}")
    if bad:
        return check("самопроверки скриптов", CRIT, "; ".join(bad),
                     "запустить проваленный selftest вручную и прочитать вывод — "
                     "он называет конкретную сломанную проверку")
    return check("самопроверки скриптов", OK, f"{len(names)}/{len(names)} зелёные")


# ── Опознание машины и CLI фактом (этап 6.5) ────────────────────────────────
# «Есть ли у вас codex» — вопрос, а не проверка: человек ответит по памяти,
# а установка пойдёт по его памяти. Спрашиваем машину, не владельца.
# Авторизация проверяется командой самого инструмента; из ответа берём ТОЛЬКО
# признак «вошёл», без почты и идентификаторов — это чужие персональные данные.
# (имя, команда, зачем, сообщает ли команда САМУ авторизацию)
CLI_PROBES = [
    ("claude", ["claude", "auth", "status"], "Фемида работает поверх Claude Code", True),
    ("codex", ["codex", "login", "status"], "второе мнение и сиденья ролей", True),
    ("gemini", ["gemini", "--version"], "второе мнение", False),
    ("kimi", ["kimi", "--version"], "второе мнение", False),
]


def probe_cli() -> list[dict]:
    out = []
    for name, cmd, why, govorit_ob_avторizacii in CLI_PROBES:
        how = " ".join(cmd)
        if not shutil.which(cmd[0]):
            out.append({"name": name, "present": False, "authorized": False,
                        "how": f"which {cmd[0]} → не найден", "why": why})
            continue
        rc, text = run(cmd, timeout=30)
        low = text.lower()
        if not govorit_ob_avторizacii:
            # Команда только подтверждает, что инструмент установлен. Выдавать это
            # за проверку входа нельзя: «authorized: true» по коду --version — ложь.
            out.append({"name": name, "present": True, "authorized": None,
                        "how": f"{how} (вход этой командой не проверяется)", "why": why})
            continue
        voshel = rc == 0 and not any(w in low for w in
                                     ("not logged", "login required", "не выполнен вход",
                                      '"loggedin": false', "logged out"))
        out.append({"name": name, "present": True, "authorized": bool(voshel),
                    "how": how, "why": why})
    return out


# Что на платформе не работает и ЧЕМ ЗАМЕНИТЬ. Пункт без замены — молчаливая
# деградация: опознали платформу, промолчали о последствиях. Владелец запретил
# это прямо: половина этапа хуже нуля.
def unavailable_on(plat: str) -> list[dict]:
    if plat == "darwin":
        return []
    obshchee = [
        {"what": "OCR сканов и фотографий документов",
         "why": "bin/vision-doc — системный фреймворк Apple Vision, вне macOS его нет",
         "replacement": "текстовые PDF, DOCX и XLSX читаются через markitdown полностью; "
                        "сканы — либо вести такие дела на macOS, либо подключить свой движок "
                        "по интерфейсу bin/vision-doc (вход файл, выход page_NNN.txt и "
                        "page_NNN.md с таблицами) и указать путь в THEMIS_VISION_DOC"},
        {"what": "расписание фоновых заданий (launchd)",
         "why": "launchd есть только в macOS",
         "replacement": f"{scheduler_of(plat)}: перенести обновление корпуса права "
                        "(scripts/legal-corpus-monthly.sh, раз в месяц) и слежение за "
                        "правками доверителя (scripts/redline-watch.sh)"},
        {"what": "диктовка задач (SMLTLK)",
         "why": "SMLTLK — приложение строки меню macOS, на этой платформе не запускается",
         "replacement": ("Планировщик и «Голосовой ввод» Windows либо любой локальный "
                         "распознаватель речи; текст класть в voice-to-brief как обычно"
                         if plat == "windows" else
                         "локальный whisper (ставится обязательно: единственный движок "
                         "распознавания речи на Linux, звук не уходит на сторону); "
                         "текст класть в voice-to-brief как обычно")},
        {"what": "подпись документа и сборка PDF",
         "why": "sign_and_pdf.py идёт через Word и AppleScript; способ подписи на Linux "
                "не разрабатывается (решение владельца 18.08.2026)",
         "replacement": ("Word через COM-автоматизацию вместо AppleScript"
                         if plat == "windows" else
                         "результат работы — .docx; подпись и PDF делаются на Маке")},
    ]
    if plat == "windows":
        obshchee.append(
            {"what": "пути с кириллицей в консоли",
             "why": "кодовая страница по умолчанию ломает имена дел в subprocess",
             "replacement": "PowerShell с UTF-8 (chcp 65001) и git config core.autocrlf input"})
    else:
        obshchee.append(
            {"what": "пути с кириллицей при не-UTF-8 локали",
             "why": "os.listdir отдаёт битые имена дел",
             "replacement": "выставить локаль UTF-8 (LANG=ru_RU.UTF-8 либо C.UTF-8)"})
    return obshchee


SMLTLK_SRC = os.path.expanduser("~/Проекты/smltlk")


def _smltlk_installed() -> bool:
    """Факт с диска, а не пересказ: приложение собрано и лежит в Программах."""
    for d in ("/Applications", os.path.expanduser("~/Applications")):
        try:
            if any(n.lower().startswith("smltlk") for n in os.listdir(d)):
                return True
        except OSError:
            continue
    return False


def smltlk_on(plat: str) -> dict:
    """SMLTLK — штатный компонент Фемиды, а не отдельный продукт (владелец, 18.08.2026)."""
    if plat == "darwin":
        est_istochnik = os.path.isdir(SMLTLK_SRC)
        return {"available": True,
                "installed": _smltlk_installed(),
                "source": SMLTLK_SRC if est_istochnik else "",
                "how": ("bash install.sh --with-smltlk (собирает из ~/Проекты/smltlk "
                        "скриптом scripts/build_app.sh: нужен Xcode со Swift 6 и ~500 МБ "
                        "под модель распознавания). Связка: диктовка → скилл voice-to-brief "
                        "→ бриф задачи; голосовые из бота расшифровываются той же локальной "
                        "моделью" if est_istochnik else
                       "исходников ~/Проекты/smltlk на этой машине нет — взять их у владельца "
                       "проекта, затем bash install.sh --with-smltlk"),
                "why": "приложение строки меню macOS, Neural Engine распознаёт локально",
                "replacement": ""}
    zamena = ("«Голосовой ввод» Windows либо локальный распознаватель речи"
              if plat == "windows" else
              "локальный whisper — на Linux он ставится обязательно, им же "
              "расшифровываются голосовые из бота")
    return {"available": False,
            "installed": False,
            "how": "",
            "why": "SMLTLK — приложение строки меню macOS, здесь оно не запускается",
            "replacement": f"{zamena}; дальше текст идёт в voice-to-brief как обычно"}


def collect(offline: bool = False) -> dict:
    plat = platform_id()
    checks = [check_python(),
              check_binary("curl", ["curl", "--version"], "весь сетевой доступ проекта",
                           "macOS: есть из коробки · Linux: apt install curl · "
                           "Windows: winget install curl"),
              check_binary("git", ["git", "--version"], "обновление и история",
                           "git-scm.com/downloads"),
              check_module("markitdown", "текст из PDF/DOCX/XLSX"),
              check_module("fitz", "рендер страниц PDF под OCR (пакет pymupdf)"),
              check_module("docx", "сборка .docx (пакет python-docx)"),
              check_binary("claude", ["claude", "--version"],
                           "Фемида работает поверх Claude Code", "claude.com/claude-code",
                           critical=False)]
    checks += check_ocr(plat)
    checks.append(check_fonts(plat))
    checks += check_corpus()
    checks += check_net(offline)
    checks.append(check_selftests())

    crit = [c for c in checks if c["статус"] == CRIT]
    warn = [c for c in checks if c["статус"] == WARN]
    return {"платформа": plat, "планировщик задач": scheduler_of(plat),
            "python": sys.version.split()[0], "корень": ROOT,
            # Машинные ключи: их читает приёмка и онбординг, поэтому имена латиницей
            # и значения фактами, а не пересказом.
            "platform": plat,
            "os_version": platform.mac_ver()[0] or platform.release() or platform.version(),
            "arch": platform.machine(),
            "simulated": bool(os.environ.get("THEMIS_PLATFORM")),
            "cli": probe_cli(),
            "unavailable": unavailable_on(plat),
            "smltlk": smltlk_on(plat),
            "проверок": len(checks), "критично": len(crit), "предупреждений": len(warn),
            "проверки": checks}


def platform_notes(plat: str) -> list[str]:
    """Что на этой платформе работает иначе. Молчать об этом нельзя."""
    if plat == "darwin":
        return ["OCR сканов: bin/vision-doc (Apple Vision), локально и бесплатно.",
                "Фоновые задания: launchd, файлы scripts/*.plist "
                "(legal.corpus.update, legal.redline.watch)."]
    common = [
        "OCR СКАНОВ НЕДОСТУПЕН: Apple Vision — системный фреймворк macOS. Текстовые "
        "PDF, DOCX, XLSX читаются через markitdown; сканы и фото документов — нет.",
        f"Фоновые задания: {scheduler_of(plat)} вместо launchd. Перенести надо два: "
        "обновление корпуса права (scripts/legal-corpus-monthly.sh, раз в месяц) и "
        "слежение за правками доверителя (scripts/redline-watch.sh).",
    ]
    if plat == "windows":
        common += [
            "ПУТИ С КИРИЛЛИЦЕЙ: имена дел русские. Запускать из PowerShell с UTF-8 "
            "(`chcp 65001`), иначе subprocess ломает имена файлов.",
            "Перевод строк: держать core.autocrlf=input, иначе .md файлов дела "
            "разъедутся в git.",
        ]
    else:
        common += ["ПУТИ С КИРИЛЛИЦЕЙ: проверить локаль (`locale`) — нужна UTF-8, "
                   "иначе os.listdir отдаёт битые имена дел."]
    return common


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка окружения Фемиды")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--offline", action="store_true", help="без сетевых проверок")
    ap.add_argument("--platform", choices=("darwin", "windows", "linux", "macos"),
                    help="разбирать чужую платформу (для онбординга и приёмки)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.platform:
        os.environ["THEMIS_PLATFORM"] = a.platform
    if a.selftest:
        return selftest()

    rep = collect(a.offline)
    if a.json:
        rep["платформенные особенности"] = platform_notes(rep["платформа"])
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 1 if rep["критично"] else (2 if rep["предупреждений"] else 0)

    print(f"Фемида — проверка окружения\nПлатформа: {rep['платформа']} · "
          f"Python {rep['python']} · планировщик: {rep['планировщик задач']}\n")
    for c in rep["проверки"]:
        mark = {OK: "✓", WARN: "⚠", CRIT: "✗"}[c["статус"]]
        print(f"{mark} {c['проверка']}: {c['что видно']}")
        if c["как починить"]:
            print(f"    → {c['как починить']}")
    print("\nЧто на этой платформе иначе:")
    for n in platform_notes(rep["платформа"]):
        print(f"  • {n}")
    print(f"\nИтого: критично {rep['критично']}, предупреждений {rep['предупреждений']}, "
          f"проверок {rep['проверок']}.")
    if rep["критично"]:
        print("СИСТЕМА НЕ ГОТОВА. Красные строки выше — не рекомендации: без них "
              "часть конвейера молча выдаст пустой результат.")
        return 1
    if rep["предупреждений"]:
        print("Система работает, часть возможностей недоступна — см. ⚠ выше.")
        return 2
    print("Готово: всё критичное на месте.")
    return 0


def selftest() -> int:
    checks = [
        ("платформа опознаётся", platform_id() in ("darwin", "windows", "linux")),
        ("планировщик назван для каждой платформы",
         all("неизвестен" not in scheduler_of(p) for p in ("darwin", "windows", "linux"))),
        # Молчаливая деградация запрещена: у каждой красной строки есть команда починки.
        ("критичная строка несёт команду починки",
         bool(check_binary("нетакого", ["нетакогобинаря", "--version"], "зачем",
                           "поставить так-то")["как починить"])),
        ("отсутствие команды даёт КРИТИЧНО",
         check_binary("нетакого", ["нетакогобинаря", "--version"], "зачем", "fix")["статус"] == CRIT),
        ("некритичное отсутствие даёт ПРЕДУПРЕЖДЕНИЕ",
         check_binary("нетакого", ["нетакогобинаря", "--version"], "зачем", "fix",
                      critical=False)["статус"] == WARN),
        ("существующая команда проходит",
         check_binary("python", [sys.executable, "--version"], "зачем", "fix")["статус"] == OK),
        ("несуществующий пакет ловится",
         check_module("нет_такого_пакета_вообще", "зачем")["статус"] == CRIT),
        ("существующий пакет проходит", check_module("json", "зачем")["статус"] == OK),
        ("run() возвращает 127 на отсутствующей команде", run(["нетакогобинаря"])[0] == 127),
        # ГЛАВНОЕ ПРО ПЛАТФОРМЫ: на не-macOS отсутствие OCR обязано быть НАЗВАНО
        # критичным и объяснённым, а не пропущено молча.
        ("на Windows отсутствие OCR — критично и объяснено",
         check_ocr("windows")[0]["статус"] == CRIT
         and "НЕТ" in check_ocr("windows")[0]["что видно"]),
        ("на Linux отсутствие OCR — критично",
         check_ocr("linux")[0]["статус"] == CRIT),
        ("на не-macOS предложен путь замены движка",
         "THEMIS_VISION_DOC" in check_ocr("linux")[0]["как починить"]),
        ("на не-macOS сказано, что именно перестаёт работать",
         "СКАНЫ" in check_ocr("windows")[0]["что видно"]),
        ("платформенные особенности непусты на каждой платформе",
         all(platform_notes(p) for p in ("darwin", "windows", "linux"))),
        ("на Windows назван Планировщик задач",
         any("Планировщик" in n for n in platform_notes("windows"))),
        ("на Linux названы systemd-таймеры",
         any("systemd" in n for n in platform_notes("linux"))),
        ("про кириллические пути сказано на обеих чужих платформах",
         all(any("КИРИЛЛИЦЕЙ" in n for n in platform_notes(p))
             for p in ("windows", "linux"))),
        ("порог версии Python не ниже 3.10", PY_MIN >= (3, 10)),
        ("стандарт держится на одной гарнитуре", FONTS == ["PT Serif"]),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
