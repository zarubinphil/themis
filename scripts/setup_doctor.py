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

МОЛЧАЛИВАЯ ДЕГРАДАЦИЯ ЗАПРЕЩЕНА. Проверка либо зеленая, либо красная с точной
командой починки. Нет промежуточного «вроде бы работает»: у каждой красной
строки есть поле «как починить» с командой, которую можно скопировать.

ПЛАТФОРМЫ. Apple Vision (bin/vision-doc) — только macOS. На Windows и Linux его
нет и не будет: это системный фреймворк Apple. Доктор обязан это НАЗВАТЬ, а не
промолчать, и указать, что именно на этой платформе не работает и чем заменить.
Затронуты: OCR-маршрут markdown_extract.py, фоновые агенты (launchd на macOS,
Планировщик задач на Windows, systemd-таймеры на Linux), пути с кириллицей.

    python3 scripts/setup_doctor.py            # отчет человеку
    python3 scripts/setup_doctor.py --json     # машинный вывод для скилла setup
    python3 scripts/setup_doctor.py --offline  # без сетевых проверок
    python3 scripts/setup_doctor.py --quick    # только selftest доктора, с предупреждением
    python3 scripts/setup_doctor.py --selftest # проверка самого доктора
    python3 scripts/setup_doctor.py --licenses # состав, гейт копилефта, сборка
                                               # requirements.txt/NOTICE/THIRD-PARTY-LICENSES

Код возврата: 0 — все критичное на месте; 1 — есть КРИТИЧНОЕ; 2 — только
предупреждения (система работает, часть возможностей недоступна).
"""
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

import _obshee as obs

ROOT = str(obs.dom_proekta())
REGISTRY = Path(ROOT) / "scripts" / "cli_registry.json"

CRIT, WARN, OK = "КРИТИЧНО", "ПРЕДУПРЕЖДЕНИЕ", "ок"

# Минимальная версия Python. Проект использует `X | None` в аннотациях (PEP 604)
# и match-выражения — на 3.9 файлы не импортируются вовсе.
PY_MIN = (3, 10)

# Имя импорта не обязано совпадать с именем дистрибутива на PyPI.
MODULE_DISTRIBUTIONS = {
    "fitz": "pymupdf",
    "docx": "python-docx",
}
SAME_NAME_DISTRIBUTIONS = {"markitdown"}

# ponytail: shell-entrypoint добавляется по имени, чтобы доктор не запустил
# полную приемку priemka_remont.sh рекурсивно.
SELFTEST_RUNNERS = {".py": (sys.executable,), ".mjs": ("node",), ".sh": ("bash",)}
SHELL_SELFTESTS = {"gate.sh"}

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
# 04.08.2026. Без него .docx соберется, но Word подставит свой — документ уйдет
# в суд не в том виде, в каком проверялся.
FONTS = ["PT Serif"]

# ── Лицензионный контур (M11) ────────────────────────────────────────────────
# Правило владельца 02.09.2026: лицензия решает «использовать или переписывать»,
# вкус не решает. Разрешительная (MIT, BSD, Apache-2.0, MIT-CMU) — используем
# как объявленную зависимость. Копилефт, бьющий по продаже (GPL/AGPL), —
# переписываем или заменяем: гейт роняет прогон с именем пакета и местом
# импорта. MPL-2.0 — пофайловый копилефт: неизмененная зависимость допустима,
# но объявляется в NOTICE. LGPL — только как внешняя зависимость: копировать
# код пакета внутрь файлов проекта нельзя. Лицензия, которую определить
# машинно нельзя, — тоже отказ: неизвестная хуже плохой, решение по ней
# принимает владелец.

SKAN_PAPKI = ("scripts", "cockpit", "tests")

# Зависимости, которые код зовет бинарем, а не import: AST-скан их не видит.
# бинарь → (дистрибутив, файл вызова).
CLI_ZAVISIMOSTI = {"whisper": ("openai-whisper", "scripts/markdown_extract.py")}

_RAZRESHITELNYE = ("MIT-CMU", "MIT", "BSD", "Apache", "ISC", "PSF", "0BSD",
                   "Zlib", "CC0", "Python-2.0", "Unlicense")
_RAZRESHITELNYE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t.upper()) for t in _RAZRESHITELNYE) + r")\b")

# Записанные исключения гейта. Молчком копилефт не пропускается — только с
# причиной и датой решения владельца; ронять им сборку тоже нельзя: и то и
# другое соврет.
LICENZIONNYE_ISKLJUCHENIJA = {
    "pymupdf": {
        "причина": "AGPL входит в состав осознанно: pymupdf — явный запасной "
                   "путь markdown_extract (_mupdf_perpage) для материалов, где "
                   "pypdfium2 читает около нуля (2 из 608 на корпусе кеша); "
                   "задача M09 возвращает его этим маршрутом",
        "дата": "02.09.2026",
    },
}


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

    Раньше macOS звалась здесь «macos», а в отчетах и промптах — «darwin»; два имени
    одного и того же расходятся ровно тогда, когда по ним что-то сравнивают.
    Подстановка чужой платформы (--platform) нужна и разбору с владельцем, и приемке:
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
        distribution = MODULE_DISTRIBUTIONS.get(
            mod, mod if mod in SAME_NAME_DISTRIBUTIONS else None)
        fix = (f"{os.path.basename(sys.executable)} -m pip install {distribution}"
               if distribution else
               f"сверить PyPI-дистрибутив модуля {mod} и добавить его в MODULE_DISTRIBUTIONS")
        return check(f"пакет {mod}", CRIT if critical else WARN, f"не импортируется ({why})",
                     fix)
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
            "THEMIS_VISION_DOC. Прежде чем ставить чужую модель, сравнить ее на "
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
                     "paratype.ru); без него Word подставит свой, и документ уйдет "
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
                         "отдает дословных норм, а doc-drafter обязан их цитировать"))
    return out


# Скилл едет внутри репозитория; домашняя копия — запасной путь (у владельца
# он живет и правится там). Ищем в обоих местах, иначе свежая установка на
# другом устройстве краснеет на ровном месте (прецедент 21.08.2026).
_SCAN_REL = ".claude/skills/humanizer-legal/scripts/scan_legal.sh"
_SCAN_V_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _SCAN_REL)
_SCAN_V_HOME = os.path.expanduser("~/" + _SCAN_REL)
HUMANIZER_SCAN = _SCAN_V_REPO if os.path.isfile(_SCAN_V_REPO) \
    else _SCAN_V_HOME


def check_humanizer() -> dict:
    """Гейт humanizer-legal едет в репозитории; домашняя копия только резерв."""
    if os.path.isfile(HUMANIZER_SCAN):
        if os.path.isfile(_SCAN_V_REPO) and os.path.isfile(_SCAN_V_HOME):
            try:
                drift = Path(_SCAN_V_REPO).read_bytes() != Path(_SCAN_V_HOME).read_bytes()
            except OSError as e:
                return check("humanizer-legal (анти-AI гейт)", CRIT,
                             f"копии не сверены ({type(e).__name__}) — "
                             f"выпуск остановлен",
                             "проверить доступ к репозиторной и домашней копиям")
            if drift:
                return check("humanizer-legal (анти-AI гейт)", CRIT,
                             f"резерв {_SCAN_V_HOME} отличается от канона — "
                             f"scan_legal.sh отказывает (код 2), выпуск стоит",
                             f"синхронизировать {_SCAN_V_HOME} с {_SCAN_V_REPO} "
                             f"или удалить резерв")
        return check("humanizer-legal (анти-AI гейт)", OK, "scan_legal.sh на месте")
    return check(
        "humanizer-legal (анти-AI гейт)", CRIT,
        f"{HUMANIZER_SCAN} не найден — verdict.py --scan работает fail-closed и "
        "блокирует вердикт «ГОТОВ К ПОДАЧЕ» на любом документе",
        "скилл едет внутри репозитория (.claude/skills/humanizer-legal/) — "
        "обновиться: bash scripts/update.sh")


def find_selftests() -> list[Path]:
    """Selftest-entrypoint с диска, без списка имен в коде."""
    scripts = Path(ROOT) / "scripts"
    paths = [path for path in sorted(scripts.iterdir())
             if path.is_file() and path.suffix in SELFTEST_RUNNERS
             and not path.name.startswith("_")
             and (path.suffix != ".sh" or path.name in SHELL_SELFTESTS)
             and "--selftest" in path.read_text(encoding="utf-8", errors="ignore")]
    scan = Path(HUMANIZER_SCAN)
    if scan.is_file() and "--selftest" in scan.read_text(encoding="utf-8", errors="ignore"):
        paths.append(scan)
    return paths


def check_selftests(quick: bool = False) -> dict:
    """Проверки самих скриптов. Красный selftest на чистой машине — это дефект установки."""
    try:
        paths = find_selftests()
    except OSError as e:
        return check("самопроверки скриптов", CRIT,
                     f"список скриптов не прочитан ({type(e).__name__})",
                     "проверить доступ к scripts/")
    total = len(paths)
    if quick:
        paths = [path for path in paths if path.resolve() == Path(__file__).resolve()]
        if not paths:
            return check("самопроверки скриптов", CRIT,
                         "setup_doctor.py --selftest не найден на диске",
                         "вернуть selftest-entrypoint доктора")
    bad = []
    for path in paths:
        runner = ("bash",) if path.resolve() == Path(HUMANIZER_SCAN).resolve() \
            else SELFTEST_RUNNERS[path.suffix]
        rc, _ = run([*runner, str(path), "--selftest"], timeout=180)
        if rc != 0:
            bad.append(f"{path.name}: код {rc}")
    if bad:
        return check("самопроверки скриптов", CRIT,
                     f"{len(paths) - len(bad)}/{len(paths)} зеленые; " + "; ".join(bad),
                     "запустить проваленный selftest вручную и прочитать вывод — "
                     "он называет конкретную сломанную проверку")
    if quick:
        return check("самопроверки скриптов", WARN,
                     f"быстрый режим: проверен 1/{total}; остальные не запускались",
                     "запустить без --quick для полной проверки")
    return check("самопроверки скриптов", OK, f"{total}/{total} зеленые")


def probe_cli() -> list[dict]:
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [{"name": "registry", "present": False, "authorized": False,
                 "how": str(e), "why": "реестр CLI не прочитан"}]
    out = []
    for name, entry in registry.items():
        cmd = entry.get("probe") or []
        how = " ".join(cmd)
        if not cmd or not shutil.which(cmd[0]):
            out.append({"name": name, "present": False, "authorized": False,
                        "how": f"which {cmd[0] if cmd else '?'} → не найден", "why": "из реестра"})
            continue
        rc, text = run(cmd, timeout=30)
        low = text.lower()
        authorized = rc == 0 and not any(w in low for w in
                                         ("not logged", "login required", "не выполнен вход",
                                          '"loggedin": false', "logged out"))
        out.append({"name": name, "present": True, "authorized": bool(authorized),
                    "how": how, "why": "из реестра"})
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
         "why": "sign_and_pdf.py идет через Word и AppleScript; способ подписи на Linux "
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
             "why": "os.listdir отдает битые имена дел",
             "replacement": "выставить локаль UTF-8 (LANG=ru_RU.UTF-8 либо C.UTF-8)"})
    return obshchee


SMLTLK_SRC = os.environ.get("SMLTLK_SRC", os.path.join(os.path.dirname(ROOT), "smltlk"))


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
                "how": ("bash install.sh --with-smltlk (собирает из соседнего каталога smltlk "
                        "скриптом scripts/build_app.sh: нужен Xcode со Swift 6 и ~500 МБ "
                        "под модель распознавания). Связка: диктовка → скилл voice-to-brief "
                        "→ бриф задачи; голосовые из бота расшифровываются той же локальной "
                        "моделью" if est_istochnik else
                       "исходников smltlk на этой машине нет — взять их у владельца "
                       "проекта, затем bash install.sh --with-smltlk"),
                "why": "приложение строки меню macOS, Neural Engine распознает локально",
                "replacement": ""}
    zamena = ("«Голосовой ввод» Windows либо локальный распознаватель речи"
              if plat == "windows" else
              "локальный whisper — на Linux он ставится обязательно, им же "
              "расшифровываются голосовые из бота")
    return {"available": False,
            "installed": False,
            "how": "",
            "why": "SMLTLK — приложение строки меню macOS, здесь оно не запускается",
            "replacement": f"{zamena}; дальше текст идет в voice-to-brief как обычно"}


def kategoriya_licenzii(syroe: str) -> str:
    """Категория по правилу владельца: permissive | mpl | lgpl | kopolleft | neizvestnaya.

    Строка — из метаданных пакета (License-Expression, License, классификатор).
    Порядок проверок важен: «LGPL» содержит «GPL», поэтому слабый копилефт
    разбирается раньше сильного.
    """
    s = " ".join((syroe or "").split())
    if not s:
        return "neizvestnaya"
    up = s.upper()
    if "AGPL" in up or "AFFERO" in up:
        return "kopolleft"
    if "LGPL" in up or "LESSER GENERAL" in up:
        return "lgpl"
    if "MPL" in up or "MOZILLA PUBLIC" in up:
        return "mpl"
    if "GPL" in up or "GNU GENERAL" in up:
        return "kopolleft"
    if _RAZRESHITELNYE_RE.search(up):
        return "permissive"
    return "neizvestnaya"


def _lokalnyj_modul(imya: str) -> bool:
    """Модуль проекта, а не сторонний пакет: файл рядом в сканируемых папках."""
    koren = Path(ROOT)
    if (koren / imya).is_dir():
        return True
    return any((koren / p / f"{imya}.py").is_file() for p in SKAN_PAPKI)


def paketi_iz_requirements(put: Path) -> set[str]:
    """Имена пакетов из requirements-файла: версии и столбец лицензий отброшены."""
    try:
        stroki = put.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    imena = (re.match(r"\s*([A-Za-z0-9_.-]+)", s.split("#")[0]) for s in stroki)
    return {m.group(1).lower() for m in imena if m}


def deklarirovano_v_install() -> set[str]:
    """Что объявляет установка: строка pip в install.sh и подключенные ею через
    -r файлы. Сегодня объявление одно — requirements.txt; читать его, а не имена
    пакетов в строке, обязательно: иначе объявление снова разойдется с составом."""
    try:
        tekst = (Path(ROOT) / "install.sh").read_text(encoding="utf-8")
    except OSError:
        return set()
    skleeno = re.sub(r"\\\n", " ", tekst)
    names: set[str] = set()
    for m in re.finditer(r"\$PIP\s+install\s+(.+?)(?:2>|\|\||$)", skleeno, re.S | re.M):
        toks = m.group(1).split()
        for i, tok in enumerate(toks):
            if i and toks[i - 1] in ("-r", "--requirement"):
                names |= paketi_iz_requirements(Path(ROOT) / tok)
            elif not tok.startswith("-") and re.fullmatch(r"[A-Za-z0-9_.-]+", tok):
                names.add(tok.lower())
    return names


def sostav_zavisimostej() -> dict:
    """Состав сторонних зависимостей С ДИСКА, а не по памяти.

    Три источника, все фактические: AST-импорты кода проекта, CLI-вызовы
    (whisper зовется бинарем), строка pip в install.sh. Рукой результат не
    править: после первой ручной правки объявление разойдется с составом —
    именно так AGPL прожил в ядре незамеченным до M11.
    """
    stdlib = set(sys.stdlib_module_names)
    dist_modul = importlib_metadata.packages_distributions()
    mesta: dict[str, set[str]] = {}
    ne_razresheno: dict[str, str] = {}  # имя импорта → первый файл

    for papka in SKAN_PAPKI:
        for f in sorted((Path(ROOT) / papka).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            try:
                derevo = ast.parse(f.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            rel = str(f.relative_to(ROOT))
            for node in ast.walk(derevo):
                imena: list[str] = []
                if isinstance(node, ast.Import):
                    imena = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    imena = [node.module.split(".")[0]]
                for imya in imena:
                    if imya in stdlib:
                        continue
                    dist = MODULE_DISTRIBUTIONS.get(imya)
                    if not dist:
                        found = dist_modul.get(imya)
                        dist = found[0] if found else None
                    if dist:
                        mesta.setdefault(dist.lower(), set()).add(rel)
                    elif not _lokalnyj_modul(imya) and imya not in ne_razresheno:
                        ne_razresheno[imya] = rel

    for bin_, (dist, mesto) in CLI_ZAVISIMOSTI.items():
        try:
            importlib_metadata.version(dist)
        except importlib_metadata.PackageNotFoundError:
            continue
        mesta.setdefault(dist.lower(), set()).add(f"{mesto} (CLI {bin_})")

    deklarirovano = deklarirovano_v_install()
    sostav = []
    for dist in sorted(set(mesta) | deklarirovano):
        try:
            meta = importlib_metadata.metadata(dist)
            versiya = importlib_metadata.version(dist)
            licenziya = (meta.get("License-Expression") or meta.get("License") or "").strip()
            if not licenziya:
                kl = [c for c in meta.get_all("Classifier") or []
                      if "License" in c and "OSI" in c]
                licenziya = kl[0].split("::")[-1].strip() if kl else ""
        except importlib_metadata.PackageNotFoundError:
            versiya, licenziya = "НЕ УСТАНОВЛЕН", ""
        sostav.append({"dist": dist, "версия": versiya,
                       "лицензия": " ".join(licenziya.split()),
                       "категория": kategoriya_licenzii(licenziya),
                       "места": sorted(mesta.get(dist, set())),
                       "в_install": dist in deklarirovano})
    return {"sostav": sostav, "ne_razresheno": ne_razresheno}


def gate_licenzij(sostav: list[dict]) -> list[dict]:
    """Гейт M11: копилефт и неизвестная лицензия роняют прогон с именем пакета
    и местом импорта. Исключение — только записанное решением владельца."""
    out = []
    for pkg in sostav:
        dist, kat = pkg["dist"], pkg["категория"]
        mesta = ", ".join(pkg["места"]) or "место импорта кодом не найдено"
        if pkg["версия"] == "НЕ УСТАНОВЛЕН":
            out.append(check(f"лицензия: {dist}", CRIT,
                             "объявлен в install.sh, но не установлен — "
                             "лицензию проверить нельзя",
                             f"{os.path.basename(sys.executable)} -m pip install {dist}, "
                             "затем python3 scripts/setup_doctor.py --licenses"))
        elif kat == "kopolleft":
            isk = LICENZIONNYE_ISKLJUCHENIJA.get(dist)
            if isk:
                out.append(check(f"лицензия: {dist}", OK,
                                 f"копилефт ({pkg['лицензия']}) — ИЗВЕСТНОЕ ИСКЛЮЧЕНИЕ: "
                                 f"{isk['причина']} (решение владельца {isk['дата']})"))
            else:
                out.append(check(f"лицензия: {dist}", CRIT,
                                 f"копилефт в составе: {pkg['лицензия']}; "
                                 f"импортируется: {mesta}",
                                 "копилефт, бьющий по продаже, переписывается или "
                                 "заменяется (правило владельца 02.09.2026). Осознанное "
                                 "исключение оформить в LICENZIONNYE_ISKLJUCHENIJA с "
                                 "причиной и датой — молчком пропускать нельзя"))
        elif kat == "neizvestnaya":
            out.append(check(f"лицензия: {dist}", CRIT,
                             f"лицензию определить машинно нельзя (метаданные: "
                             f"{pkg['лицензия']!r}); импортируется: {mesta}",
                             "СТОП: неизвестная лицензия хуже плохой — решение по этому "
                             "пакету принимает владелец. После решения внести категорию "
                             "в классификатор либо исключение с причиной и датой"))
        elif kat == "mpl":
            out.append(check(f"лицензия: {dist}", OK,
                             f"{pkg['лицензия']}: пофайловый копилефт, неизмененная "
                             "зависимость допустима; объявлен в NOTICE (обязательно)"))
        elif kat == "lgpl":
            out.append(check(f"лицензия: {dist}", WARN,
                             f"{pkg['лицензия']}: допустим только как внешняя "
                             "зависимость — копировать его код внутрь файлов проекта "
                             "нельзя",
                             "если код пакета уже скопирован в файлы проекта — убрать"))
        else:
            out.append(check(f"лицензия: {dist}", OK, pkg["лицензия"]))
    return out


def raskhozhdenie_s_install(sostav: list[dict]) -> list[dict]:
    """Расхождение состава с install.sh в обе стороны — предупреждение, не гейт."""
    out = []
    for pkg in sostav:
        if pkg["в_install"] and not pkg["места"]:
            out.append(check(f"состав: {pkg['dist']}", WARN,
                             "ставится install.sh, но кодом не зовется ни разу",
                             "убрать из install.sh либо вернуть вызов — решение владельца"))
        if not pkg["в_install"] and pkg["места"]:
            out.append(check(f"состав: {pkg['dist']}", WARN,
                             f"код зовет ({', '.join(pkg['места'][:3])}), но install.sh "
                             "НЕ ставит — свежая машина получит сломанную Фемиду",
                             "добавить в строку pip в install.sh либо ставить из "
                             "requirements.txt (pip install -r requirements.txt)"))
    return out


_SHAPKA_GENERITA = (
    "ГЕНЕРИРУЕТСЯ: python3 scripts/setup_doctor.py --licenses\n"
    "Рукой не править: собирается из фактического состава (импорты кода,\n"
    "CLI-вызовы, install.sh) — ручная правка разойдется с составом."
)


def generit_requirements(sostav: list[dict]) -> str:
    """requirements.txt со столбцом лицензий; pip читает его как обычно."""
    linii = [f"# {_SHAPKA_GENERITA}".replace("\n", "\n# "), "",
             "# Формат: пакет==версия  # лицензия (из метаданных пакета)", ""]
    for p in sostav:
        vers = f"=={p['версия']}" if p["версия"] != "НЕ УСТАНОВЛЕН" else ""
        lic = p["лицензия"] or "ЛИЦЕНЗИЯ НЕ ОПРЕДЕЛЕНА — СТОП, решение владельца"
        linii.append(f"{p['dist']}{vers}  # {lic}")
    return "\n".join(linii) + "\n"


def generit_notice(sostav: list[dict]) -> str:
    """NOTICE: состав и обязательные объявления (MPL — всегда, исключения — с датой)."""
    linii = ["NOTICE — Фемида", "", _SHAPKA_GENERITA, "",
             "Продукт включает сторонние компоненты:", ""]
    for p in sostav:
        linii.append(f"  {p['dist']} {p['версия']} — {p['лицензия'] or 'лицензия не определена'}")
    linii += ["", "Объявления, требуемые лицензиями:", ""]
    mpl = [p["dist"] for p in sostav if p["категория"] == "mpl"]
    if mpl:
        linii.append(f"* MPL-2.0 ({', '.join(mpl)}): пофайловый копилефт. Компоненты "
                     "используются без изменений; тексты лицензий — в "
                     "THIRD-PARTY-LICENSES; исходный код каждого пакета доступен "
                     "на PyPI по его имени.")
        linii.append("")
    linii.append("* Разрешительные лицензии (MIT, BSD, Apache-2.0, MIT-CMU и др.): "
                 "уведомления об авторских правах и тексты лицензий всех компонентов "
                 "перенесены в THIRD-PARTY-LICENSES.")
    lgpl = [p["dist"] for p in sostav if p["категория"] == "lgpl"]
    if lgpl:
        linii += ["", f"* LGPL ({', '.join(lgpl)}): используются только как внешние "
                  "зависимости; код этих пакетов в файлы проекта не копируется."]
    zapisannye = [(p, LICENZIONNYE_ISKLJUCHENIJA[p["dist"]]) for p in sostav
                  if p["dist"] in LICENZIONNYE_ISKLJUCHENIJA]
    if zapisannye:
        linii += ["", "Записанные решения владельца:", ""]
        for p, isk in zapisannye:
            linii.append(f"* {p['dist']} {p['версия']} — {p['лицензия']}: "
                         f"{isk['причина']} (решение от {isk['дата']}).")
    return "\n".join(linii) + "\n"


def _teksty_licenzij(dist: str) -> list[tuple[str, str]]:
    """Файлы лицензий из dist-info установленного пакета: (имя файла, текст)."""
    out = []
    try:
        fajly = importlib_metadata.files(dist) or []
    except importlib_metadata.PackageNotFoundError:
        return out
    for f in fajly:
        if not any(part.endswith(".dist-info") for part in f.parts):
            continue
        if f.parts[-1].lower().startswith(("licen", "copying", "notice")):
            try:
                out.append(("/".join(f.parts),
                            f.locate().read_text(encoding="utf-8", errors="replace").strip()))
            except OSError:
                continue
    return out


def generit_third_party(sostav: list[dict]) -> str:
    """THIRD-PARTY-LICENSES: полные тексты лицензий из установленных пакетов.

    MIT и BSD требуют переносить уведомления при распространении — здесь они
    и переносятся, машинно, из dist-info каждого пакета состава.
    """
    chasti = ["THIRD-PARTY-LICENSES — Фемида", "", _SHAPKA_GENERITA, ""]
    for p in sostav:
        chasti.append("=" * 78)
        chasti.append(f"{p['dist']} {p['версия']}")
        chasti.append(f"Лицензия (метаданные пакета): {p['лицензия'] or 'НЕ ОПРЕДЕЛЕНА'}")
        teksty = _teksty_licenzij(p["dist"])
        if not teksty:
            chasti.append("Текст лицензии в дистрибутив не вложен; идентификатор "
                          "взят из метаданных пакета. Канонический текст — на "
                          "https://spdx.org/licenses/ по идентификатору выше.")
        for imya, tekst in teksty:
            chasti += ["-" * 78, imya, "", tekst]
    return "\n".join(chasti) + "\n"


def check_licenzii_agregat() -> dict:
    """Сводка лицензионного гейта в общий прогон; детали и сборка — --licenses."""
    try:
        sostav = sostav_zavisimostej()["sostav"]
    except Exception as e:  # состав не читается — само по себе красное
        return check("лицензионный гейт", CRIT,
                     f"состав не собран ({type(e).__name__})",
                     "python3 scripts/setup_doctor.py --licenses — читать вывод")
    crit = [c for c in gate_licenzij(sostav) if c["статус"] == CRIT]
    if crit:
        return check("лицензионный гейт", CRIT,
                     "; ".join(c["проверка"] for c in crit),
                     "python3 scripts/setup_doctor.py --licenses — гейт называет "
                     "пакет, место импорта и путь починки")
    return check("лицензионный гейт", OK,
                 f"{len(sostav)} пакетов: копилефта вне записанных исключений "
                 "и неизвестных лицензий нет")


def rezhim_licenzij() -> int:
    """--licenses: состав с диска, гейт копилефта, сборка трех файлов.

    requirements.txt, NOTICE и THIRD-PARTY-LICENSES пересобираются ВСЕГДА —
    это единственный способ не дать им разойтись с составом. Код 1 — на
    копилефте вне записанных исключений и на лицензии, которую определить
    машинно нельзя; предупреждения (расхождение с install.sh) прогон не роняют.
    """
    svedeniya = sostav_zavisimostej()
    sostav = svedeniya["sostav"]
    proverki = gate_licenzij(sostav) + raskhozhdenie_s_install(sostav)

    for imya, tekst in (("requirements.txt", generit_requirements(sostav)),
                        ("NOTICE", generit_notice(sostav)),
                        ("THIRD-PARTY-LICENSES", generit_third_party(sostav))):
        (Path(ROOT) / imya).write_text(tekst, encoding="utf-8")

    print(f"Состав зависимостей с диска: {len(sostav)} пакетов\n")
    for p in sostav:
        mesta = ", ".join(p["места"][:3]) if p["места"] else "кодом не зовется (install.sh)"
        print(f"  {p['dist']:16s} {p['версия']:12s} {p['лицензия'][:44]:46s} {mesta}")
    if svedeniya["ne_razresheno"]:
        print("\nИмпортируется опционально, дистрибутив не установлен (в состав не входит):")
        for imya, f in sorted(svedeniya["ne_razresheno"].items()):
            print(f"  · {imya} ({f})")
    print("\nГейт и расхождения:")
    for c in proverki:
        mark = {OK: "✓", WARN: "⚠", CRIT: "✗"}[c["статус"]]
        print(f"{mark} {c['проверка']}: {c['что видно']}")
        if c["как починить"]:
            print(f"    → {c['как починить']}")
    crit = [c for c in proverki if c["статус"] == CRIT]
    warn = [c for c in proverki if c["статус"] == WARN]
    print("\nrequirements.txt, NOTICE, THIRD-PARTY-LICENSES пересобраны из состава.")
    print(f"Итого: критично {len(crit)}, предупреждений {len(warn)}.")
    if crit:
        print("ГЕЙТ КРАСНЫЙ: строки ✗ выше — не рекомендации.")
        return obs.KOD_OSHIBKA
    print("Гейт зеленый: копилефта вне записанных исключений и неизвестных лицензий нет.")
    return obs.KOD_OK


def collect(offline: bool = False, quick: bool = False) -> dict:
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
    checks.append(check_selftests(quick))
    checks.append(check_humanizer())
    checks.append(check_licenzii_agregat())

    crit = [c for c in checks if c["статус"] == CRIT]
    warn = [c for c in checks if c["статус"] == WARN]
    return {"платформа": plat, "планировщик задач": scheduler_of(plat),
            "python": sys.version.split()[0], "корень": ROOT,
            # Машинные ключи: их читает приемка и онбординг, поэтому имена латиницей
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
                   "иначе os.listdir отдает битые имена дел."]
    return common


def main() -> int:
    ap = obs.parser("Проверка окружения Фемиды")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--offline", action="store_true", help="без сетевых проверок")
    ap.add_argument("--quick", action="store_true",
                    help="не гонять все selftest; доктор вернет предупреждение")
    ap.add_argument("--platform", choices=("darwin", "windows", "linux", "macos"),
                    help="разбирать чужую платформу (для онбординга и приемки)")
    ap.add_argument("--licenses", action="store_true",
                    help="состав зависимостей, гейт копилефта и сборка "
                         "requirements.txt/NOTICE/THIRD-PARTY-LICENSES")
    a = ap.parse_args()
    if a.platform:
        os.environ["THEMIS_PLATFORM"] = a.platform
    if a.selftest:
        return selftest()
    if a.licenses:
        return rezhim_licenzij()

    rep = collect(a.offline, a.quick)
    if a.json:
        rep["платформенные особенности"] = platform_notes(rep["платформа"])
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return (obs.KOD_OSHIBKA if rep["критично"] else
                (obs.KOD_NE_RABOTAL if rep["предупреждений"] else obs.KOD_OK))

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
        return obs.KOD_OSHIBKA
    if rep["предупреждений"]:
        print("Система работает, часть возможностей недоступна — см. ⚠ выше.")
        return obs.KOD_NE_RABOTAL
    print("Готово: все критичное на месте.")
    return obs.KOD_OK


def selftest() -> int:
    global HUMANIZER_SCAN
    saved_scan = HUMANIZER_SCAN
    HUMANIZER_SCAN = "/нет/такого/файла/scan_legal.sh"
    humanizer_missing = check_humanizer()
    HUMANIZER_SCAN = saved_scan

    source = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    module_calls = {
        call.args[0].value
        for root in source.body
        if not (isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef))
                and root.name == "selftest")
        for call in ast.walk(root)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id == "check_module" and call.args
        and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
    }
    missing_distributions = (module_calls - MODULE_DISTRIBUTIONS.keys()
                             - SAME_NAME_DISTRIBUTIONS)
    discovered_selftests = {path.resolve() for path in find_selftests()}

    def _paket(dist, lic, mesta=("x.py",), v_install=True):
        return {"dist": dist, "версия": "1.0", "лицензия": lic,
                "категория": kategoriya_licenzii(lic), "места": list(mesta),
                "в_install": v_install}

    real = sostav_zavisimostej()
    real_dists = {p["dist"] for p in real["sostav"]}
    real_notice = generit_notice(real["sostav"])
    real_req = generit_requirements(real["sostav"])
    real_tpl = generit_third_party(real["sostav"])
    gate_agpl = gate_licenzij([_paket("fakelib", "GPL-3.0-only")])
    gate_neizv = gate_licenzij([_paket("mysterylib", "Custom closed")])
    gate_iskl = gate_licenzij([_paket(
        "pymupdf", "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License")])

    checks = [
        ("humanizer-legal без скрипта — КРИТИЧНО", humanizer_missing["статус"] == CRIT),
        ("humanizer-legal без скрипта несет команду починки",
         bool(humanizer_missing["как починить"])),
        ("платформа опознается", platform_id() in ("darwin", "windows", "linux")),
        ("планировщик назван для каждой платформы",
         all("неизвестен" not in scheduler_of(p) for p in ("darwin", "windows", "linux"))),
        # Молчаливая деградация запрещена: у каждой красной строки есть команда починки.
        ("критичная строка несет команду починки",
         bool(check_binary("нетакого", ["нетакогобинаря", "--version"], "зачем",
                           "поставить так-то")["как починить"])),
        ("отсутствие команды дает КРИТИЧНО",
         check_binary("нетакого", ["нетакогобинаря", "--version"], "зачем", "fix")["статус"] == CRIT),
        ("некритичное отсутствие дает ПРЕДУПРЕЖДЕНИЕ",
         check_binary("нетакого", ["нетакогобинаря", "--version"], "зачем", "fix",
                      critical=False)["статус"] == WARN),
        ("существующая команда проходит",
         check_binary("python", [sys.executable, "--version"], "зачем", "fix")["статус"] == OK),
        ("несуществующий пакет ловится",
         check_module("нет_такого_пакета_вообще", "зачем")["статус"] == CRIT),
        ("существующий пакет проходит", check_module("json", "зачем")["статус"] == OK),
        ("fitz чинится дистрибутивом pymupdf",
         MODULE_DISTRIBUTIONS.get("fitz") == "pymupdf"),
        ("docx чинится дистрибутивом python-docx",
         MODULE_DISTRIBUTIONS.get("docx") == "python-docx"),
        ("карта дистрибутивов покрывает все check_module"
         + (f" (нет: {', '.join(sorted(missing_distributions))})"
            if missing_distributions else ""), not missing_distributions),
        ("поиск selftest с диска видит loop_gate.py",
         (Path(ROOT) / "scripts" / "loop_gate.py").resolve() in discovered_selftests),
        ("поиск selftest с диска видит gate.sh",
         (Path(ROOT) / "scripts" / "gate.sh").resolve() in discovered_selftests),
        ("приватный общий модуль не считается CLI-прибором",
         (Path(ROOT) / "scripts" / "_obshee.py").resolve() not in discovered_selftests),
        ("поиск selftest с диска видит канонический scan_legal.sh",
         Path(HUMANIZER_SCAN).resolve() in discovered_selftests),
        ("run() возвращает 127 на отсутствующей команде", run(["нетакогобинаря"])[0] == 127),
        # ГЛАВНОЕ ПРО ПЛАТФОРМЫ: на не-macOS отсутствие OCR обязано быть НАЗВАНО
        # критичным и объясненным, а не пропущено молча.
        ("на Windows отсутствие OCR — критично и объяснено",
         check_ocr("windows")[0]["статус"] == CRIT
         and "НЕТ" in check_ocr("windows")[0]["что видно"]),
        ("на Linux отсутствие OCR — критично",
         check_ocr("linux")[0]["статус"] == CRIT),
        ("на не-macOS предложен путь замены движка",
         "THEMIS_VISION_DOC" in check_ocr("linux")[0]["как починить"]),
        ("на не-macOS сказано, что именно перестает работать",
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
        # Лицензионный контур (M11): классификатор, гейт, генераторы, состав.
        ("AGPL опознается копилефтом",
         kategoriya_licenzii("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex "
                             "Commercial License") == "kopolleft"),
        ("GPL опознается копилефтом",
         kategoriya_licenzii("GPL-3.0-only") == "kopolleft"),
        ("LGPL не путается с GPL",
         kategoriya_licenzii("LGPL-2.1-only") == "lgpl"),
        ("MPL-2.0 — своя категория", kategoriya_licenzii("MPL-2.0") == "mpl"),
        ("MIT — разрешительная", kategoriya_licenzii("MIT") == "permissive"),
        ("составное разрешительное выражение — разрешительная",
         kategoriya_licenzii("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0")
         == "permissive"),
        ("мусорная строка — неизвестная лицензия",
         kategoriya_licenzii("Custom closed") == "neizvestnaya"),
        ("пустая строка — неизвестная лицензия",
         kategoriya_licenzii(" ") == "neizvestnaya"),
        ("чужой копилефт роняет гейт с именем пакета и местом импорта",
         any(c["статус"] == CRIT and "fakelib" in c["проверка"]
             and "x.py" in c["что видно"] for c in gate_agpl)),
        ("неизвестная лицензия роняет гейт (стоп к владельцу)",
         any(c["статус"] == CRIT and "mysterylib" in c["проверка"]
             and "владелец" in c["как починить"] for c in gate_neizv)),
        ("записанное исключение не роняет гейт и названо исключением",
         all(c["статус"] != CRIT for c in gate_iskl)
         and any("ИЗВЕСТНОЕ ИСКЛЮЧЕНИЕ" in c["что видно"] for c in gate_iskl)),
        ("у каждого исключения есть причина и дата",
         all(v.get("причина") and v.get("дата")
             for v in LICENZIONNYE_ISKLJUCHENIJA.values())),
        ("исключение сторожит реальный копилефт, а не воздух",
         all(p["категория"] == "kopolleft" for p in real["sostav"]
             if p["dist"] in LICENZIONNYE_ISKLJUCHENIJA)),
        ("состав с диска видит живые зависимости",
         {"pypdf", "httpx", "pymupdf", "reportlab", "pypdfium2"} <= real_dists),
        ("расхождение с install.sh ловится в обе стороны",
         {c["проверка"] for c in raskhozhdenie_s_install(
             [_paket("lishnij", "MIT", mesta=(), v_install=True),
              _paket("nedostajushchij", "MIT", mesta=("y.py",), v_install=False)])
          if c["статус"] == WARN}
         == {"состав: lishnij", "состав: nedostajushchij"}),
        ("объявление читается из requirements.txt, а не из имен в строке pip",
         {"pypdf", "reportlab", "httpx", "pypdfium2"} <= deklarirovano_v_install()),
        ("requirements-файл дает имена без версий и столбца лицензий",
         paketi_iz_requirements(Path(ROOT) / "requirements.txt")
         and not any(any(c in imya for c in "=<>#")
                     for imya in paketi_iz_requirements(Path(ROOT) / "requirements.txt"))),
        ("неустановленный опциональный импорт назван, а не пропущен",
         "pikepdf" in real["ne_razresheno"]),
        ("MPL не роняет гейт, но объявляется в NOTICE",
         all(c["статус"] != CRIT for c in gate_licenzij([_paket("certifi", "MPL-2.0")]))
         and "certifi" in real_notice),
        ("requirements собирается из состава со столбцом лицензий",
         "pymupdf==" in real_req and "AFFERO" in real_req),
        ("NOTICE несет записанное решение владельца с датой",
         "02.09.2026" in real_notice and "pymupdf" in real_notice),
        ("THIRD-PARTY-LICENSES несет полные тексты лицензий из dist-info",
         "Redistribution and use" in real_tpl),
        ("тексты лицензий читаются из dist-info пакета",
         any("licenses" in imya or "COPYING" in imya
             for imya, _ in _teksty_licenzij("pypdf"))),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    obs.zavershit(main)
