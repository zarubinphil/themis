#!/usr/bin/env python3
"""Машина состояний протокола Фемиды — детерминированный статус дела.

Использование:
    python3 scripts/themiz_status.py cases/{клиент}/{дело}
    python3 scripts/themiz_status.py cases/{клиент}/{дело} --brief
    python3 scripts/themiz_status.py --selftest

Читает маркеры с ДИСКА (не из памяти модели) и печатает: статус каждого шага
и СЛЕДУЮЩИЙ ШАГ. Фемида обязана работать по этому выводу — это единственный
источник правды о состоянии протокола.

`--brief` добавляет сводку старта сессии и заменяет собой ритуал из шести чтений
(лог, индекс, `_case.md`, профиль, событие, карта). Смысл не в удобстве, а в
деньгах: прочитанный файл остается в контексте до конца сессии и переоплачивается
КАЖДЫМ следующим обращением к инструменту. Индекс дел — 16,9 КБ, карта знаний —
десятки килобайт; вместе ритуал заносил в контекст порядка 30 000 знаков, из
которых для решения нужны полтора десятка строк. Их скрипт и печатает — бесплатно
по токенам, потому что считает python, а не модель.
"""
import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
import sreda  # noqa: E402,F401  переходный период имен переменных

# Кеш роутера извлечения: если файл там есть, он уже распознан и
# перераспознавать его запрещено (конституция, раздел LOCAL-FIRST).
EXTRACT_CACHE = Path(os.environ.get(
    "THEMIZ_EXTRACT_CACHE", Path.home() / ".cache" / "legal_extract"))
SCAN_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".bmp"}
TEXT_EXT = {".docx", ".xlsx", ".pptx", ".rtf", ".txt", ".md", ".html", ".csv"}
# Флаг предписан документами параметризованным — «[ОБНОВИТЬ КЛИЕНТА: поле: значение]»,
# и параметр это и есть его полезная нагрузка. Перечень подстрок с закрывающей
# скобкой сразу за словом не ловил ни одного реального флага (проба круга 9).
FLAG_RE = re.compile(r"\[ОБНОВИТЬ\s+(?:КЛИЕНТА|ИНДЕКС)(?::[^\]]*)?\]", re.I)
FLAGS = ("[ОБНОВИТЬ КЛИЕНТА]", "[ОБНОВИТЬ ИНДЕКС]")   # для селфтестов


# Маркер, названный в отрицании, — не маркер. Прежняя проверка искала вхождение
# подстроки по всему файлу, поэтому строка «Статус: зафиксировано, без маркера
# "СОГЛАСОВАНО СОВЕТОМ"» засчитывалась как пройденный шаг: гейт открывался ровно
# там, где обязан держать (дело 04.08.2026 — positions.md прямо писал «без
# маркера», прибор показывал ✓, а claude_guard.py пускал запись черновиков).
# Проверка идет построчно, потому что все маркеры однострочные.
NEGATED_MARKER_RE = re.compile(r"\b(?:без|нет|не)\s+маркера", re.I)
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def has_marker(f: Path, pattern: str, anchored: bool = True) -> bool:
    """Маркер шага — СТРУКТУРА файла, а не подстрока в строке. Заголовок стоит в
    СВОЕЙ строке: вне блока кода, вне цитаты (`>`), не зачеркнут (`~~`), не в
    HTML-комментарии, не в отрицании. Строка «Маркер ## КАРТА ГОТОВА ✓
    отсутствует» готовой картой не является (проба 20.08.2026). anchored=True
    (шаговые маркеры-заголовки) — паттерн в НАЧАЛЕ строки; anchored=False
    (вердикт, флаги) — паттерн бывает внутри строки, ищем вхождением. Логика
    единая с claude_guard._has_marker: разошедшиеся копии одного гейта проект
    уже проходил (humanizer-гейт)."""
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return False
    rx = re.compile(pattern)
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        s = line.lstrip()
        if s.startswith((">", "~~", "<!--")):
            continue
        if NEGATED_MARKER_RE.search(line):
            continue
        if rx.match(s) if anchored else rx.search(s):
            return True
    return False


def fakty_zamorozheny(case: Path) -> bool:
    """Владелец подтвердил, что материалы дела собраны полностью.

    Охота, запущенная до этого, переискивает по каждой новой порции документов:
    прецедент 15.08.2026 — материалы пришли тремя порциями, шаг практики съел
    51% прогона. Маркер ставится строкой в brief.md, отрицание не засчитывается
    (та же дыра, что закрыта в has_marker).
    """
    return has_marker(case / ".agent/context" / "_working" / "brief.md",
                      r"ФАКТУРА ЗАМОРОЖЕНА", anchored=False)


def age_days(f: Path) -> int:
    try:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        return (datetime.datetime.now() - mtime).days
    except OSError:
        return 10**6


def check_frontmatter() -> list[str]:
    """Сломанный YAML во frontmatter = агент молча не попадает в реестр.

    Прецедент 02.08.2026: у doc-drafter поле description начиналось с двойной
    кавычки без обрамления одинарными — конвейер встал на шаге 4 после трех
    завершенных шагов и полутора миллионов токенов работы. Проверка стоит
    три строки и выполняется перед каждым шагом.
    """
    try:
        import yaml
    except ImportError:
        # Молчаливый return [] отключал проверку сломанного frontmatter — ту самую,
        # что конституция называет главной причиной остановки конвейера. Отсутствие
        # библиотеки обязано быть видно, а не выглядеть как «все чисто».
        return ["pyyaml не установлен — проверка frontmatter агентов НЕ выполнена. "
                "Установить pyyaml либо считать реестр агентов непроверенным"]
    root = Path(__file__).resolve().parent.parent / ".claude"
    bad = []
    for f in sorted(root.glob("agents/*.md")) + sorted(root.glob("skills/**/SKILL.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not m:
            # Шапки нет вовсе — из реестра агент выпадает ровно так же, как со
            # сломанным YAML, а молчаливый continue это скрывал (проба круга 9).
            bad.append(f"{f.name}: frontmatter отсутствует — файл не попадет в реестр")
            continue
        try:
            data = yaml.safe_load(m.group(1))
            if not isinstance(data, dict) or "name" not in data:
                bad.append(f"{f.name}: во frontmatter нет поля name")
        except yaml.YAMLError as e:
            bad.append(f"{f.name}: YAML сломан ({str(e).splitlines()[0][:60]})")
    return bad


def field(text: str, name: str) -> str:
    """Значение поля `- **Имя:** значение` из _case.md. Пусто — прочерк."""
    m = re.search(rf"(?m)^\s*[-*]\s*\*\*{re.escape(name)}:\*\*\s*(.+?)\s*$", text)
    v = m.group(1).strip() if m else ""
    return "" if v in ("—", "-", "") else v


def extracted(files: list[Path]) -> int:
    """Сколько материалов уже лежит в кеше роутера — их не перераспознавать."""
    n = 0
    for f in files:
        try:
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
        if (EXTRACT_CACHE / f"{sha}.md").exists() or (EXTRACT_CACHE / sha).is_dir():
            n += 1
    return n


def _nuzhno_kodeksov() -> int:
    """Сколько актов ЗНАЕТ реестр обновлятора. Считаем оттуда, а не константой
    рядом: добавят акт в реестр — вторая копия числа молча устареет."""
    try:
        import re as _re
        src = (Path(__file__).resolve().parent / "update_legal_corpus.py").read_text(
            encoding="utf-8", errors="ignore")
        return len(_re.findall(r'\{"slug":\s*"[a-z0-9-]+"', src)) or 17
    except OSError:
        return 17


NUZHNO_KODEKSOV = _nuzhno_kodeksov()


def graph_state(case: Path) -> str:
    """Одна строка о графе дела. Граф, о котором молчат, становится тихим артефактом.

    Состояние ЧИТАЕТСЯ, а не чинится: сводка старта сессии не выполняет работу, и
    пересборка графа никогда не является «следующим шагом» — граф производная, он
    не может быть работой. Ошибка прибора гасится: статус дела не смеет упасть
    из-за вспомогательного артефакта.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import case_graph
    except Exception:
        return "прибор case_graph недоступен"
    try:
        graph = Path(case) / case_graph.GRAPH_DIR / "graph.json"
        if not graph.is_file():
            _, refusals = case_graph.build_graph(str(case))
            return ("не построен: " + refusals[0]) if refusals \
                else "не построен (карта по контракту, соберется при первом запросе)"
        bad = case_graph.stale(str(case))
        if not bad:
            import json
            n = json.load(open(graph, encoding="utf-8"))
            return f"{len(n.get('nodes', []))} узлов, свеж"
        return "УСТАРЕЛ, разошлись: " + ", ".join(bad) + " — пересоберется при запросе"
    except Exception as e:
        return f"состояние не определено ({e.__class__.__name__})"


def brief(case: Path, level: str, stage: str = "не определена") -> None:
    """Сводка старта сессии: то, ради чего конституция велела читать шесть файлов."""
    case_txt = read(case / "_case.md")
    client_dir = case.parent
    client_txt = read(client_dir / "_client.md")

    head = " · ".join(x for x in (
        f"стадия: {stage}",
        f"суд: {field(case_txt, 'Суд')}" if field(case_txt, "Суд") else "",
        f"дело № {field(case_txt, 'Номер дела')}" if field(case_txt, "Номер дела") else "",
        f"судья: {field(case_txt, 'Судья')}" if field(case_txt, "Судья") else "",
    ) if x) or "реквизиты в _case.md не заполнены"
    print(f"# Сводка — {client_dir.name}/{case.name} (уровень {level})")
    print(f"  {head}")

    hearing = field(case_txt, "Следующее заседание")
    events = sorted((p for p in (case / "02_hearings").iterdir() if p.is_dir()),
                    reverse=True) if (case / "02_hearings").is_dir() else []
    print(f"  заседание: {hearing or 'не назначено'}"
          f" · последнее событие: {events[0].name if events else 'нет'}")

    fio = field(client_txt, "ФИО") or "профиль пуст"
    print(f"  доверитель: {fio}"
          f"{'' if (client_dir / '_client.md').exists() else ' ⚠ файла _client.md нет'}")
    print(f"  граф дела: {graph_state(case)}")

    intake = case / "00_intake"
    files = [f for f in intake.rglob("*") if f.is_file() and not f.name.startswith((".", "~$"))] \
        if intake.is_dir() else []
    scans = [f for f in files if f.suffix.lower() in SCAN_EXT]
    done = extracted(files)
    print(f"  материалы: {len(files)} шт (сканов {len(scans)}), уже извлечено {done} — "
          f"{'перераспознавать нельзя' if done else 'кеш пуст'}")

    # Флаги живут в файлах дела и в последнем логе сессий: необработанный флаг
    # означает, что реестр или профиль разошлись с делом.
    flagged = []
    for f in list(case.rglob("*.md")) + sorted(
            (case.parents[1] / "_logs").glob("session_*.md"), reverse=True)[:1]:
        t = read(f)
        if FLAG_RE.search(t):
            flagged.append(f.name)
    if flagged:
        print(f"  ⚠ необработанные флаги в: {', '.join(sorted(set(flagged))[:4])}")

    # Трек считается по счетному: объем и природа материалов. Правовой вопрос
    # машине не виден — про него говорится прямо, а не умалчивается.
    if len(files) <= 3 and not scans:
        hint = "MICRO по объему"
    elif len(files) <= 6 and (not scans or done >= len(scans)):
        hint = "FAST по объему"
    else:
        hint = "FULL по объему"
    print(f"  трек: {hint}; новизну правового вопроса оценивает Фемида по practice_index")

    # Состояние корпуса права проверяется прибором, а не обнаруживается случайно
    # на третьем часу работы. Прецедент 21.08.2026: автолуп подменил
    # knowledge/kodeksy/ симлинком, 19 актов исчезли, cite.py молчал «корпус не
    # выгружен» — и ни один шаг протокола у корпуса не спрашивал, жив ли он.
    # Документ без дословной нормы уходит в суд, а заметить это некому.
    korpus_predupredit(case)
    print()


def _korpus_counts(case: Path) -> tuple[int, int]:
    """Сколько кодексов и Пленумов на диске. Один источник счета для предупреждения
    (brief) и для кода возврата main — чтобы «неполный корпус» краснел, а не был фоном."""
    root = case.parents[2] if len(case.parents) > 2 else Path.cwd()
    kod, plen = root / "knowledge" / "kodeksy", root / "knowledge" / "plenumy"
    est = len(list(kod.glob("*.md"))) if kod.is_dir() else 0
    plenumov = len(list(plen.glob("*.md"))) if plen.is_dir() else 0
    return est, plenumov


def korpus_predupredit(case: Path) -> None:
    """Кричит, если корпуса права нет или он поредел. Дешево: считает файлы."""
    est, plenumov = _korpus_counts(case)
    if est == 0:
        print("  ⚠ КОРПУС ПРАВА ПУСТ: дословных цитат статей не будет, cite.py вернет "
              "«не найдено», госпошлина не посчитается. Выгрузить: "
              "python3 scripts/update_legal_corpus.py --init")
    elif est < NUZHNO_KODEKSOV:
        print(f"  ⚠ корпус права неполон: кодексов {est} из {NUZHNO_KODEKSOV}. "
              f"Доложить: python3 scripts/update_legal_corpus.py --init")
    if plenumov == 0:
        print("  ⚠ Пленумов ВС РФ на диске нет: "
              "python3 scripts/update_legal_corpus.py --plenums")


def read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def verdict_checks(drafts: list[Path]) -> list[tuple[Path, int, str]]:
    """Шаг 5 читает только канонический CLI verdict.py --check.

    `review_log.md` остается человеческим протоколом и сюда не попадает. Один
    документ — один ответ прибора, привязанный к sha текущей редакции.
    """
    tool = Path(__file__).resolve().with_name("verdict.py")
    rows = []
    for md in drafts:
        try:
            p = subprocess.run([sys.executable, str(tool), str(md), "--check"],
                               capture_output=True, text=True, timeout=360,
                               stdin=subprocess.DEVNULL)
            rows.append((md, p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()))
        except (OSError, subprocess.TimeoutExpired) as e:
            rows.append((md, 1, f"verdict.py --check не отработал: {e}"))
    return rows


def protocol_stage(s1: bool, s2: bool, pr_fresh: bool, s3_done: bool,
                   drafts: list[Path], approved: bool, published: bool) -> str:
    """Стадия выводится только из маркеров и артефактов на диске."""
    if not s1:
        return "картирование"
    if not s2 or not pr_fresh:
        return "поиск практики"
    if not s3_done:
        return "формирование позиции"
    if not drafts:
        return "составление документа"
    if not approved:
        return "рецензия"
    if not published:
        return "выкладка в GOTOVO"
    return "готово к финализации"


def age_minutes(f: Path) -> float:
    try:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        return (datetime.datetime.now() - mtime).total_seconds() / 60
    except OSError:
        return 10.0**9


def track_hint(case: Path) -> str:
    """Грубый трек по объему материалов — тот же счет, что печатает brief()."""
    intake = case / "00_intake"
    files = [f for f in intake.rglob("*")
             if f.is_file() and not f.name.startswith((".", "~$"))] if intake.is_dir() else []
    scans = [f for f in files if f.suffix.lower() in SCAN_EXT]
    done = extracted(files)
    if len(files) <= 3 and not scans:
        return "MICRO"
    if len(files) <= 6 and (not scans or done >= len(scans)):
        return "FAST"
    return "FULL"


def declared_track(case: Path) -> str:
    """Трек, явно записанный в деле, сильнее грубой оценки по числу файлов."""
    for f in [case / "_case.md", case / ".agent" / "context" / "_working" / "brief.md"]:
        text = re.sub(r"[*_`]", "", read(f))
        m = re.search(r"\b(?:ТРЕК|Трек|track)\s*[:：]\s*(MICRO|FAST|FULL)\b", text, re.I)
        if m:
            return m.group(1).upper()
    return track_hint(case)


def aktivnye_agenty(case: Path) -> str:
    """Кто работает по делу СЕЙЧАС. Надежный сигнал на диске — лок черновиков .owner
    (его пишет/держит claude_guard). Субагенты живут ВНУТРИ процесса claude, отдельными
    процессами ОС их не видно — ceiling: печатаем лок, не ps. Данных нет — так и говорим,
    но строка обязана быть (25.08 статус молчал о том, кто еще пишет в дело)."""
    owner = case / ".agent" / "drafts" / ".owner"
    if owner.is_file():
        try:
            who = " ".join(owner.read_text(encoding="utf-8", errors="ignore").split())
        except OSError:
            who = ""
        who = who or "(лок без имени)"
        stale = " ⚠ лок протух (>45 мин)" if age_minutes(owner) > 45 else ""
        return f"активные агенты: лок черновиков держит {who}{stale}"
    return "активные агенты: нет данных"


def rashod_stroka(case: Path, track: str) -> tuple[str, bool, bool]:
    """(строка, не_измерен, перерасход). Расход считает прибор token_ledger по свежей
    сессии проекта — не глаз и не самоотчет модели. Ledger недоступен/молчит — «не
    измерен» и это ненулевой код (25.08 расход прочли как фон и продолжили)."""
    try:
        import token_ledger as tl
    except Exception as e:  # noqa: BLE001 — любой сбой импорта = не измерено
        return (f"расход: не измерен — token_ledger недоступен ({e})", True, False)
    try:
        path = tl.latest_session(str(case))
    except Exception:
        path = None
    if not path or not os.path.isfile(path):
        return ("расход: не измерен — session.jsonl проекта не найден", True, False)
    try:
        tot = tl.tokens(tl.collect(path)["total"])
    except Exception as e:  # noqa: BLE001
        return (f"расход: не измерен — ledger не разобрал сессию ({e})", True, False)
    if tot <= 0:
        return ("расход: не измерен — ledger дал 0 токенов по делу; "
                "это не подтверждает бюджет", True, False)
    budget = tl.TRACK_BUDGET.get(track) or tl.TRACK_BUDGET["FULL"]
    over = tot > budget
    line = (f"расход: {tot:,} ток. · цель {track} {budget:,} ток."
            .replace(",", " "))
    return (line, False, over)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("case", nargs="?")
    ap.add_argument("--brief", action="store_true",
                    help="сводка старта сессии вместо шести чтений")
    ap.add_argument("--selftest", action="store_true")
    a, _ = ap.parse_known_args()
    if a.selftest:
        return selftest()
    if not a.case:
        print("usage: themiz_status.py cases/{клиент}/{дело} [--brief]", file=sys.stderr)
        return 1
    sys.argv = [sys.argv[0], a.case]

    broken = check_frontmatter()
    if broken:
        print("⛔ СЛОМАН FRONTMATTER — эти агенты и скиллы НЕ попадут в реестр:")
        for b in broken:
            print(f"   {b}")
        print("   Чинить до работы: значение с кавычками либо двоеточием "
              "обернуть в одинарные кавычки.\n")
        # Код возврата отражает СОСТОЯНИЕ, а не «скрипт не упал»: сломанный YAML у
        # doc-drafter молча выкидывает агента из реестра — прецедент 02.08.2026,
        # конвейер встал на шаге 4 после 1,5 млн токенов. Прибор состояния обязан
        # краснеть, чтобы smoke-столб гейта это увидел, а не мигать зеленым.
        return 2
    case = Path(sys.argv[1]).resolve()
    if not case.is_dir():
        print(f"СТОП: {case} не существует. Сначала /new-case.", file=sys.stderr)
        return 1

    ctx = case / ".agent/context"
    km, pr, pos = ctx / "knowledge-map.md", ctx / "practice.md", ctx / "positions.md"
    case_md = case / "_case.md"

    s1 = has_marker(km, r"## КАРТА ГОТОВА ✓")
    # Практика закрывается двумя путями разной силы: FULL — «## СОВЕТ ЗАВЕРШЕН»,
    # FAST — «## FAST-СИНТЕЗ ФЕМИДЫ». Раньше FAST маркера не имел, поэтому FAST и
    # FULL на диске были неотличимы, а агент шел в обход хука. Тот же список — в
    # claude_guard.PRACTICE_MARKER; расходиться им нельзя.
    s2_full = has_marker(pr, r"## СОВЕТ ЗАВЕРШ")
    s2_fast = has_marker(pr, r"## FAST-СИНТЕЗ ФЕМИДЫ")
    s2 = s2_full or s2_fast
    # Порог — от даты попадания практики В НАШУ БАЗУ (mtime practice.md), а не
    # от даты вынесения актов. Решение владельца 02.08.2026: судебная практика
    # так быстро не меняется, 30 дней было необоснованно жестко. Год.
    PRACTICE_TTL_DAYS = 365
    pr_fresh = s2 and age_days(pr) <= PRACTICE_TTL_DAYS
    # Позиция закрывается так же двумя путями, как и практика выше: FULL — совет
    # Ареопага («СОГЛАСОВАНО СОВЕТОМ»), FAST — синтез Фемидой («## FAST-ПОЗИЦИЯ
    # ФЕМИДЫ»). Без второго маркера FAST-прогон упирался в шаг 3 навсегда: совета
    # на нем не бывает, а ставить «СОГЛАСОВАНО СОВЕТОМ» без совета — врать прибору.
    s3_full = has_marker(pos, r"#{0,3}\s*СОГЛАСОВАНО СОВЕТОМ")
    s3_fast = has_marker(pos, r"## FAST-ПОЗИЦИЯ ФЕМИДЫ")
    # M02: фраза «position-council пропущен» в карточке была прозой, которую
    # машина принимала за состояние. Новый формат только добавляется: явный
    # заголовок-маркер живет в positions.md; старые карточки не переписываются.
    s3_skip = has_marker(pos, r"## POSITION-COUNCIL ПРОПУЩЕН")
    s3 = s3_full or s3_fast or s3_skip

    # Старые L1-дела не требуют positions.md. Совместимость держит точное
    # структурное поле-маркер, а не любое упоминание L1 в прозе карточки.
    level = field(read(case_md), "Уровень") or "?"
    s3_not_needed = level == "L1"
    drafts = sorted((case / ".agent/drafts").glob("*.md")) if (case / ".agent/drafts").is_dir() else []
    drafts = [d for d in drafts if "_working" not in d.parts and "_baselines" not in d.parts]
    verdict_rows = verdict_checks(drafts)
    approved_rows = [row for row in verdict_rows if row[1] == 0]
    blocked_rows = [row for row in verdict_rows if row[1] != 0]
    # Один старый зеленый v1 не маскирует новый красный v2: шаг закрыт, только
    # когда verdict.py разрешает КАЖДЫЙ черновик верхнего уровня.
    approved = bool(verdict_rows) and not blocked_rows

    gotovo = case / "GOTOVO"
    gotovo_docx = ([f for f in sorted(gotovo.glob("*.docx")) if not f.name.startswith("~$")]
                   if gotovo.is_dir() else [])
    stage = protocol_stage(s1, s2, pr_fresh, s3 or s3_not_needed,
                           drafts, approved, bool(gotovo_docx))
    if a.brief:
        brief(case, level, stage)

    def mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    print(f"# Статус протокола — {case.name} (уровень: {level}; стадия: {stage})")
    print(f"Шаг 1 Карта:     {mark(s1)}  knowledge-map.md {'с маркером' if s1 else '— нет маркера КАРТА ГОТОВА'}")
    fresh_note = "" if not s2 else (f" (свежая, ≤{PRACTICE_TTL_DAYS} дн.)" if pr_fresh else f" (в базе {age_days(pr)} дн., порог {PRACTICE_TTL_DAYS} — проверить актуальность)")
    track = " [FULL, совет]" if s2_full else (" [FAST, синтез Фемиды]" if s2_fast else "")
    print(f"Шаг 2 Практика:  {mark(s2)}  practice.md "
          f"{'с маркером' + track + fresh_note if s2 else '— нет маркера (нужен СОВЕТ ЗАВЕРШЕН либо FAST-СИНТЕЗ ФЕМИДЫ)'}")
    if s3_not_needed:
        print("Шаг 3 Позиция:   —  L1: не требуется (маркер Уровень в _case.md)")
    elif s3_skip:
        print("Шаг 3 Позиция:   ✓  positions.md: POSITION-COUNCIL ПРОПУЩЕН")
    else:
        s3_how = ("СОГЛАСОВАНО СОВЕТОМ" if s3_full
                  else "FAST-ПОЗИЦИЯ ФЕМИДЫ" if s3_fast else "— нет маркера")
        print(f"Шаг 3 Позиция:   {mark(s3)}  positions.md {s3_how}")
    print(f"Шаг 4 Черновики: {mark(bool(drafts))}  {len(drafts)} файл(ов) в .agent/drafts")
    if approved:
        names = ", ".join(row[0].name for row in approved_rows)
        detail = f"ГОТОВ К ПОДАЧЕ через verdict.py --check: {names}"
    elif blocked_rows:
        lines = [line.strip(" ·") for line in blocked_rows[0][2].splitlines()
                 if line.strip() and "СБОРКА .docx ЗАПРЕЩЕНА" not in line]
        detail = ("verdict.py --check: " + (lines[0] if lines else "сборка запрещена")
                  + (f"; одобрено {len(approved_rows)}, заблокировано {len(blocked_rows)}"
                     if approved_rows else ""))
    else:
        detail = "нет черновиков для verdict.py --check"
    print(f"Шаг 5 Кони:      {mark(approved)}  {detail}")

    # Состояния «артефакт шага N есть, а шага N-1 нет» в модели раньше не было:
    # для дела с готовым документом и пустым конвейером скрипт бодро печатал
    # «СЛЕДУЮЩИЙ ШАГ: Шаг 1», не сказав ни слова о документе вне протокола.
    if drafts and not (s1 and s2):
        missing = ", ".join(x for x, ok in (("карта", s1), ("практика", s2)) if not ok)
        print(f"\n⚠ НАРУШЕН ПОРЯДОК: в .agent/drafts есть {len(drafts)} документ(ов), "
              f"но не пройдено: {missing}. Документ создан вне конвейера — "
              f"проверять реквизиты вручную, к подаче не готов.")

    # Готовый документ, не доехавший до GOTOVO/, для владельца не существует.
    # Прецедент 01.09.2026: вердикт «ГОТОВ К ПОДАЧЕ» стоял, .docx лежал в
    # .agent/drafts/, конвейер отчитался «готово, вот файл» — GOTOVO/ была пуста,
    # и владелец сообщил, что видит это не впервые. Текстовое правило в скилле
    # исполняется вероятностно, поэтому расхождение ловит прибор.
    # Временные файлы Word «~$имя.docx» появляются, когда владелец открыл документ,
    # и за готовый документ не считаются: иначе сторож молчит именно в тот момент,
    # когда папка пуста, а Word открыт на прошлой копии (проба 01.09.2026).
    if approved and not gotovo_docx:
        print("\n⚠ ДОКУМЕНТ НЕ У ВЛАДЕЛЬЦА: вердикт «ГОТОВ К ПОДАЧЕ» есть, "
              "а в GOTOVO/ нет ни одного .docx. Готовый документ живет в GOTOVO/ — "
              "это единственная папка, за которой владелец приходит за результатом. "
              "Положить его туда и только потом докладывать о готовности.")

    if not s1:
        nxt = "Шаг 1 — case-mapper (карта дела)"
    elif not s2:
        nxt = "Шаг 2 — охота за практикой (FAST: 1 охотник; FULL: 3 + /askacouncil)"
    elif not pr_fresh:
        nxt = (f"Шаг 2 — практика в базе {age_days(pr)} дн. (порог {PRACTICE_TTL_DAYS}): подтвердить актуальность "
               f"или обновить охоту")
    elif not (s3 or s3_not_needed):
        nxt = ("Шаг 3 — /position-council (либо добавить явный маркер "
               "## POSITION-COUNCIL ПРОПУЩЕН в positions.md)")
    elif not drafts:
        nxt = "Шаг 4 — /draft (doc-drafter)"
    elif not approved:
        nxt = "Шаг 5 — doc-reviewer (Кони) до вердикта ГОТОВ К ПОДАЧЕ"
    else:
        nxt = "/finalize — пакет в 02_hearings (guard правок доверителя)"
    print(f"\nСЛЕДУЮЩИЙ ШАГ: {nxt}")
    if not fakty_zamorozheny(case) and (not s2 or not pr_fresh):
        print("⚠ ФАКТУРА НЕ ЗАМОРОЖЕНА: в .agent/context/_working/brief.md нет строки "
              "«ФАКТУРА ЗАМОРОЖЕНА». Опросить владельца по чек-листу документов "
              "(редакции договора, допсоглашения, платежные документы по спорным "
              "суммам, доказательства вручения, согласия и разрешения) и записать "
              "строку. Охота, запущенная до заморозки, переискивает по каждой новой "
              "порции материалов: прецедент 15.08.2026 — 51% расхода прогона.")

    # ── Зубы прибора (ход 4 разбора): агенты, расход, корпус, источник проверки ──
    # Раньше main() всегда возвращал 0, а неполный корпус был лишь предупреждением;
    # 25.08 это прочли как фон и продолжили работу. Теперь состояние отражается в коде.
    print()
    print(f"  {aktivnye_agenty(case)}")
    track = declared_track(case)
    rline, ne_izmeren, pererashod = rashod_stroka(case, track)
    print(f"  {rline}")
    if pererashod:
        print("  СТОП: перерасход, доложить владельцу")

    est, plenumov = _korpus_counts(case)
    korpus_nepolon = est < NUZHNO_KODEKSOV or plenumov == 0
    if korpus_nepolon and not a.brief:   # в brief() korpus уже прокричал — не двоить
        korpus_predupredit(case)

    # Прибор обязан назвать, ЧТО он прочитал: вердикт без источника проверки — не вердикт.
    print(f"\n  прочитано с диска: {case}")
    print("  маркеров проверено: 4 (карта · практика · позиция · вердикт Кони)")

    rc = 0
    if pererashod:
        rc = 3
    elif ne_izmeren or korpus_nepolon:
        rc = 2
    return rc


def selftest() -> int:
    """Без сети и без диска проекта. Фикстуры враждебные: каждая метит в ветку,
    которая уже ломалась или может тихо соврать."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    case = tmp / "cases" / "klient" / "delo-2026"
    (case / ".agent/context").mkdir(parents=True)
    (case / "00_intake").mkdir()
    (case / "02_hearings" / "2026-06-29_zasedanie").mkdir(parents=True)
    (case / "02_hearings" / "2026-05-12_beseda").mkdir(parents=True)
    (tmp / "cases" / "_logs").mkdir(parents=True)
    (case / "_case.md").write_text(
        "# дело\n- **Стадия:** Первая инстанция\n- **Уровень:** L2\n"
        "- **Суд:** Советский районный суд\n- **Номер дела:** 2-4590/2026\n"
        "- **Судья:** —\n- **Следующее заседание:** 29.06.2026 в 13:00\n", encoding="utf-8")
    (case.parent / "_client.md").write_text(
        "# профиль\n- **ФИО:** Тестова Тестина Тестовна\n", encoding="utf-8")
    (case / ".agent/context" / "knowledge-map.md").write_text("## КАРТА ГОТОВА ✓", encoding="utf-8")
    # Содержимое разное: у одинаковых файлов один sha, и кеш засчитал бы все три.
    for n, body in (("a.pdf", b"pdf"), ("b.jpg", b"jpeg"), ("c.docx", b"docx")):
        (case / "00_intake" / n).write_bytes(body)
    (case / ".agent/context" / "zametka.md").write_text("надо [ОБНОВИТЬ КЛИЕНТА]", encoding="utf-8")

    txt = read(case / "_case.md")
    cache = Path(tempfile.mkdtemp())
    global EXTRACT_CACHE
    EXTRACT_CACHE = cache
    files = sorted((case / "00_intake").iterdir())
    # Один из трех материалов уже в кеше роутера.
    sha = hashlib.sha256((case / "00_intake" / "a.pdf").read_bytes()).hexdigest()
    (cache / f"{sha}.md").write_text("уже извлечено", encoding="utf-8")

    # Три варианта записи маркера позиции — ровно те, что встречаются на диске.
    neg = case / ".agent/context" / "pos_neg.md"
    neg.write_text('Статус: зафиксировано, без маркера «СОГЛАСОВАНО СОВЕТОМ» '
                   '(исключение по срокам).', encoding="utf-8")
    pos_ok = case / ".agent/context" / "pos_ok.md"
    pos_ok.write_text("СОГЛАСОВАНО СОВЕТОМ", encoding="utf-8")
    mixed = case / ".agent/context" / "pos_mixed.md"
    mixed.write_text('# позиция\nРаунд 1 шел без маркера «СОГЛАСОВАНО СОВЕТОМ».\n'
                     'СОГЛАСОВАНО СОВЕТОМ\n', encoding="utf-8")

    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        brief(case, "L2")
    out = buf.getvalue()

    checks = [
        ("поле читается из _case.md", field(txt, "Номер дела") == "2-4590/2026"),
        # Прочерк — это ОТСУТСТВИЕ значения, а не значение «—»: иначе сводка
        # уверенно печатает «судья: —» и выглядит заполненной.
        ("прочерк считается пустым", field(txt, "Судья") == ""),
        ("несуществующее поле не выдумывается", field(txt, "Кадастровый номер") == ""),
        # Поле берется целиком: обрезка по первому пробелу теряла зал и время.
        ("значение берется до конца строки",
         field(txt, "Следующее заседание") == "29.06.2026 в 13:00"),
        ("кеш роутера опознан", extracted(files) == 1),
        ("не в кеше — не засчитан", extracted(files) != len(files)),
        ("сводка называет суд", "Советский районный суд" in out),
        ("сводка называет доверителя", "Тестова" in out),
        # Граф, о котором сводка молчит, превращается в тихий артефакт: им
        # перестают пользоваться и не замечают, что он отстал.
        ("сводка называет состояние графа дела", "граф дела:" in out),
        ("состояние графа не выдумано: карта фикстуры контракту не отвечает",
         "не построен" in out),
        ("стадия из прозы _case.md не читается", "Первая инстанция" not in out),
        # События сортируются по имени: даты ISO, последнее — старшее.
        ("последнее событие — самое свежее", "2026-06-29_zasedanie" in out
         and "2026-05-12_beseda" not in out),
        ("материалы сосчитаны", "материалы: 3 шт (сканов 2), уже извлечено 1" in out),
        ("необработанный флаг виден", "флаги" in out and "zametka.md" in out),
        # Два скана, извлечен один — по объему это еще не FAST.
        ("трек не занижается при нераспознанных сканах", "FULL по объему" in out),
        ("машина не молчит о правовом вопросе", "practice_index" in out),
        # Гейт обязан падать закрытым: маркер, названный в отрицании, не маркер.
        ("маркер в отрицании не засчитан", not has_marker(neg, r"СОГЛАСОВАНО СОВЕТОМ")),
        ("настоящий маркер по-прежнему виден", has_marker(pos_ok, r"СОГЛАСОВАНО СОВЕТОМ")),
        ("отрицание не глушит маркер в других строках",
         has_marker(mixed, r"СОГЛАСОВАНО СОВЕТОМ")),
    ]
    # Активные агенты по локу черновиков .agent/drafts/.owner (ремонт 25.08).
    drafts = case / ".agent" / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    net_owner = aktivnye_agenty(case)
    (drafts / ".owner").write_text("Мейер · 25.08.2026 14:00", encoding="utf-8")
    est_owner = aktivnye_agenty(case)
    checks += [
        ("нет .owner — активные агенты: нет данных", "нет данных" in net_owner),
        ("есть .owner — назван держатель лока", "Мейер" in est_owner),
        # Трек считается по объему: два скана, извлечен один — еще FULL.
        ("трек по объему — FULL", track_hint(case) == "FULL"),
    ]

    # M02: человеческий протокол может говорить что угодно, шаг 5 отвечает
    # только verdict.py --check. Сначала честное одобрение, затем правка .md:
    # review_log остается зеленым, машинный статус обязан стать красным.
    os.environ["THEMIZ_VERDICT_KEY"] = "selftest-key-do-not-use-in-prod"
    import verdict as vd
    status_md = drafts / "status.md"
    status_md.write_text("# Ходатайство\n\nПрошу суд отложить заседание "
                         "(ст. 158 АПК РФ).\n", encoding="utf-8")
    vd._write_preflight(status_md, vd.sha(status_md), True, [{
        "tool": "selftest", "code": 0, "output_sha256": "0" * 64,
    }])
    assert vd.record(status_md, vd.READY, source="doc-reviewer") is not None
    green = verdict_checks([status_md])
    human_log = drafts / "_working" / ("review" + "_log.md")
    human_log.parent.mkdir(parents=True, exist_ok=True)
    human_log.write_text("r1: ГОТОВ К ПОДАЧЕ — status.md\n", encoding="utf-8")
    status_md.write_text(read(status_md) + "\nНовая редакция после одобрения.\n",
                         encoding="utf-8")
    red = verdict_checks([status_md])
    # Враждебная сквозная проба main(): зеленая запись человеческого протокола
    # не вправе перебить красный verdict.py. Заодно карточная фраза о пропуске
    # шага 3 не считается маркером; новый явный маркер в positions.md считается.
    (case / ".agent/context" / "practice.md").write_text(
        "## FAST-СИНТЕЗ ФЕМИДЫ\n", encoding="utf-8")
    (case / "_case.md").write_text(
        read(case / "_case.md")
        + "\nКоординатор: position-council пропущен; это якобы L1.\n",
        encoding="utf-8")
    old_green = drafts / "old_green.md"
    old_green.write_text("# Ходатайство\n\nПрошу суд приобщить документы.\n",
                         encoding="utf-8")
    vd._write_preflight(old_green, vd.sha(old_green), True, [{
        "tool": "selftest", "code": 0, "output_sha256": "0" * 64,
    }])
    assert vd.record(old_green, vd.READY, source="doc-reviewer") is not None

    old_argv = sys.argv[:]
    old_frontmatter, old_rashod, old_korpus = check_frontmatter, rashod_stroka, _korpus_counts

    def run_status():
        buf = io.StringIO()
        sys.argv = [__file__, str(case)]
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            main()
        return buf.getvalue()

    try:
        globals()["check_frontmatter"] = lambda: []
        globals()["rashod_stroka"] = lambda *_: ("расход: тест", False, False)
        globals()["_korpus_counts"] = lambda *_: (NUZHNO_KODEKSOV, 1)
        card_prose_out = run_status()
        l2_card = read(case / "_case.md")
        (case / "_case.md").write_text(
            l2_card.replace("**Уровень:** L2", "**Уровень:** L1"), encoding="utf-8")
        l1_marker_out = run_status()
        (case / "_case.md").write_text(l2_card, encoding="utf-8")
        (case / ".agent/context" / "positions.md").write_text(
            "## POSITION-COUNCIL ПРОПУЩЕН\n", encoding="utf-8")
        marker_out = run_status()
    finally:
        globals()["check_frontmatter"] = old_frontmatter
        globals()["rashod_stroka"] = old_rashod
        globals()["_korpus_counts"] = old_korpus
        sys.argv = old_argv

    checks += [
        ("честный verdict.py --check закрывает шаг 5", green[0][1] == 0),
        ("расхождение review_log и verdicts.jsonl красит шаг 5 по verdict.py",
         red[0][1] == 1 and "ДРУГУЮ редакцию" in red[0][2]),
        ("main не принимает review_log вместо verdict.py",
         "Шаг 5 Кони:      ✗" in marker_out
         and "СЛЕДУЮЩИЙ ШАГ: Шаг 5" in marker_out),
        ("старый зеленый черновик не маскирует новый красный",
         all(code != 0 for _, code, _ in verdict_checks([old_green, status_md]))),
        ("реплика координатора в _case.md не двигает стадию",
         "Шаг 3 Позиция:   ✗" in card_prose_out
         and "СЛЕДУЮЩИЙ ШАГ: Шаг 3" in card_prose_out),
        ("структурный маркер Уровень: L1 сохраняет старый обход позиции",
         "L1: не требуется" in l1_marker_out
         and "СЛЕДУЮЩИЙ ШАГ: Шаг 5" in l1_marker_out),
        ("аддитивный маркер пропуска в positions.md двигает стадию",
         "POSITION-COUNCIL ПРОПУЩЕН" in marker_out),
        ("стадия считается из маркеров, не карточки",
         protocol_stage(True, True, True, True, [status_md], False, False) == "рецензия"),
    ]
    import token_ledger as tl
    old_latest, old_collect, old_tokens = tl.latest_session, tl.collect, tl.tokens
    seen = []
    try:
        (tmp / "session.jsonl").write_text("", encoding="utf-8")
        tl.latest_session = lambda cwd: seen.append(cwd) or str(tmp / "session.jsonl")
        tl.collect = lambda path: {"total": {"input": 0, "output": 0,
                                             "cache_creation": 0, "cache_read": 0}}
        tl.tokens = lambda u: 0
        line, no_data, over = rashod_stroka(case, "FAST")
        checks += [
            ("расход привязан к пути дела, не cwd", seen == [str(case)]),
            ("нулевой расход — не измерен", no_data and not over and "0 токенов" in line),
        ]
        (case / "_case.md").write_text(read(case / "_case.md") + "- **Трек:** FAST\n",
                                       encoding="utf-8")
        checks.append(("явный трек сильнее объема", declared_track(case) == "FAST"))
    finally:
        tl.latest_session, tl.collect, tl.tokens = old_latest, old_collect, old_tokens

    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
