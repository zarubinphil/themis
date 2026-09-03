#!/usr/bin/env python3
"""case_paths.py — контракт раскладки дела. Этап 3 плана FINAL-PLAN-2026-08-18.

Единственный источник правды о том, где что лежит внутри дела. До этого модуля
раскладка жила строковыми литералами в 46 файлах (203 вхождения, замер 19.08.2026):
любая правка требовала найти их все, а пропущенный литерал ломался молча.

## Два слоя (решение владельца)

Дело смотрит на человека одной стороной, а на агента — другой.

    cases/<клиент>/<дело>/
      _case.md            карточка дела — человек читает
      00_intake/          исходники доверителя, неприкосновенны
      02_hearings/        события и ПОДАННЫЕ документы
      GOTOVO/             готовые документы — то, за чем человек приходит
      .agent/             рабочая кухня, в Finder не видна
        context/            карта, практика, позиция, рабочие файлы роя
        drafts/             черновики .md + _baselines/ (снимки ДО правок доверителя)
        archive/            отработанное

`GOTOVO` латиницей: кириллица в имени папки нарушает `AGENTS.md` (латиница, цифры,
дефисы) и ломает пути на чужой файловой системе. «ГОТОВО» — подпись в панели.

`.agent/` скрыта точкой: человек не должен видеть кухню. Обратная сторона — она
невидима и в Finder, и одним `git add -A` уезжает в публичный репозиторий; это
закрывается `.gitignore`, а не памятью.

## Жизненный цикл документа

Документ живет в `.agent/drafts/<имя>.md` весь цикл правок. `.docx` собирается
ОДИН раз — после вердикта Кони «ГОТОВ К ПОДАЧЕ» — и кладется в `GOTOVO/`.
Раньше `.docx` пересобирался на каждом раунде, и папка готовых наполнялась
недоделанным.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

# ── Контракт ─────────────────────────────────────────────────────────────────
AGENT_DIR = ".agent"
INTAKE = "00_intake"
HEARINGS = "02_hearings"
READY = "GOTOVO"
READY_LABEL = "ГОТОВО"          # подпись в панели; в путях — только латиница
CONTEXT = f"{AGENT_DIR}/context"
DRAFTS = f"{AGENT_DIR}/drafts"
ARCHIVE = f"{AGENT_DIR}/archive"
BASELINES = f"{DRAFTS}/_baselines"
WORKING = "_working"
DRAFT_NAME = "{document}.md"
READY_MD_NAME = "{document}.md"
READY_DOCX_NAME = "{document}.docx"
MAX_REVIEW_ROUNDS = 2
REVIEW_STOP_ROUND = MAX_REVIEW_ROUNDS + 1
# Журнал вердиктов Кони. Имя — ОДНО на систему; лежит прямо в drafts, НЕ в _working:
# сторож (claude_guard) освобождает _working/_baselines от гейта протокола, и журнал
# внутри них — слепое пятно (D03, 01.09.2026: дописанная строка открыла сборку .docx).
VERDICTS_NAME = "verdicts.jsonl"

# Что человеку видно в корне дела. Все прочее — кухня.
HUMAN_VISIBLE = ("_case.md", INTAKE, HEARINGS, READY)

# Старое → новое. Порядок важен: длинные ключи раньше коротких, иначе
# «03_drafts/_baselines» починится как «.agent/drafts» + хвост от старого имени.
LEGACY = (
    ("01_context", CONTEXT),
    ("03_drafts", DRAFTS),
    ("04_archive", ARCHIVE),
)


def _p(case):
    return case if isinstance(case, Path) else Path(case)


def intake(case):
    return _p(case) / INTAKE


def hearings(case):
    return _p(case) / HEARINGS


def ready(case):
    """Папка готовых документов — единственное, за чем человек приходит в дело."""
    return _p(case) / READY


def document_contract(case):
    """Канонические файлы документа и потолок содержательной рецензии."""
    case = _p(case)
    return {
        "draft_md": drafts(case) / DRAFT_NAME,
        "ready_md": ready(case) / READY_MD_NAME,
        "ready_docx": ready(case) / READY_DOCX_NAME,
        "review_rounds": MAX_REVIEW_ROUNDS,
    }


def agent_root(case):
    return _p(case) / AGENT_DIR


def context(case):
    return _p(case) / AGENT_DIR / "context"


def drafts(case):
    return _p(case) / AGENT_DIR / "drafts"


def archive(case):
    return _p(case) / AGENT_DIR / "archive"


def baselines(case):
    """Неизменяемые снимки выданного — база сравнения для разбора правок доверителя."""
    return drafts(case) / "_baselines"


def working(case):
    return context(case) / WORKING


def knowledge_map(case):
    return context(case) / "knowledge-map.md"


def practice(case):
    return context(case) / "practice.md"


def positions(case):
    return context(case) / "positions.md"


def brief(case):
    return working(case) / "brief.md"


def review_log(case):
    return drafts(case) / WORKING / "review_log.md"


def verdicts(case):
    """Журнал вердиктов — прямо в drafts, ВНЕ освобожденного сторожем _working (D03)."""
    return drafts(case) / VERDICTS_NAME


# ── Машинное состояние прогона (M01) ──────────────────────────────────────────
# ЕДИНСТВЕННЫЙ адрес файла прогона. Сторож (claude_guard) читает его, проводник
# (themis-pipeline) и владелец пишут через CLI ниже — второго адреса не заводить.
# Лежит в .agent/context/: сторож освобождает эту папку от гейта протокола (кроме
# практики/позиции), человеку кухня не видна. Ключи: guide (проводник запущен),
# preflight_code (последний код preflight_search), preflight_override (решение
# владельца работать при упавших каналах).
RUN_STATE_NAME = "run.json"


def run_state(case):
    return context(case) / RUN_STATE_NAME


def run_read(case):
    """Состояние прогона как dict; отсутствует/битый — пустой dict (fail-open чтения)."""
    try:
        data = json.loads(run_state(case).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run_write(case, **updates):
    """Аддитивно слить updates в файл прогона. Возвращает новое состояние."""
    st = run_read(case)
    st.update(updates)
    p = run_state(case)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return st


def verdicts_legacy(case):
    """Старый адрес журнала (внутри _working). Только чтение, как запасной, с
    предупреждением: живые дела не переписываем — формат меняем аддитивно."""
    return drafts(case) / WORKING / VERDICTS_NAME


def modernize(text):
    """Старые пути в тексте → новые. Для промптов и разовой правки кода."""
    for old, new in LEGACY:
        text = text.replace(old, new)
    return text


# ── Переезд ──────────────────────────────────────────────────────────────────

def nfc(name):
    """Имя в NFC.

    macOS хранит имена файлов в NFD (разложенной) форме, Linux и git — в NFC.
    Непереведенное имя на сервере превращается в другой путь, и дело просто
    не находится. На диске уже есть дело с турецкой «ı» — на нем это видно.
    """
    return unicodedata.normalize("NFC", name)


def collisions(names):
    """Имена, схлопывающиеся в одно после нормализации. Переезжать с ними нельзя:
    второе дело затрет первое молча."""
    seen, bad = {}, {}
    for n in names:
        key = nfc(n)
        if key in seen and seen[key] != n:
            bad.setdefault(key, {seen[key]}).add(n)
        seen.setdefault(key, n)
    return {k: sorted(v) for k, v in bad.items()}


def migration_moves(case):
    """Что и куда переезжает в одном деле: [(источник, назначение)].

    Пусто, если дело уже в новой раскладке. `00_intake` и `02_hearings` не
    фигурируют вовсе — они неприкосновенны.
    """
    case = _p(case)
    moves = []
    for old, new in LEGACY:
        src = case / old
        if src.is_dir():
            moves.append((src, case / new))
    return moves


def is_migrated(case):
    case = _p(case)
    return agent_root(case).is_dir() and not any(
        (case / old).is_dir() for old, _ in LEGACY)


CONTRACT_DOCS = (
    "AGENTS.md",
    ".claude/CLAUDE.md",
    ".claude/commands/draft.md",
    ".claude/commands/finalize.md",
    ".claude/commands/new-case.md",
    ".claude/agents/doc-drafter.md",
    ".claude/agents/doc-reviewer.md",
    ".claude/skills/doc-drafter/SKILL.md",
    ".claude/skills/themis-setup/SKILL.md",
)
REVIEW_CONTRACT_DOCS = (
    ".claude/commands/draft.md",
    ".claude/agents/doc-reviewer.md",
    ".claude/skills/doc-drafter/SKILL.md",
)
ROUND_WORDS = {
    "один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3,
    "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5,
}
ROUND_VALUE_RE = r"\d+|один|одного|два|двух|три|трех|четыре|четырех|пять|пяти"
ROUND_LIMIT_RE = re.compile(
    rf"(?:(?:до|после|не более|лимит\s*[-—:]?)\s*"
    rf"(?P<leading>{ROUND_VALUE_RE})\s+(?:раунд\w*|круг\w*)|"
    rf"(?P<trailing>{ROUND_VALUE_RE})\s+(?:раунд\w*|круг\w*)\s+"
    rf"(?:без одобрения|максимум))",
    re.IGNORECASE,
)
DIRECT_DRAFT_DOCX_RE = re.compile(
    r"\.agent/drafts/(?!_baselines/|_working/)[^`\s\"')]+\.docx",
    re.IGNORECASE,
)
DOCX_IN_DRAFTS_RE = re.compile(
    r"`?\.docx`?\s+(?:лежит|живет|хранится|сохраняется|в|→|->)"
    r"[^\n]{0,40}`?\.agent/drafts/",
    re.IGNORECASE,
)
VERSIONED_DRAFT_RE = re.compile(
    r"\.agent/drafts/[^`\s\"')]*_v\d+\.(?:md|docx)", re.IGNORECASE)
WRONG_HOME_RE = re.compile(
    r"(?:готов\w*|итог\w*|на выходе|свежайш\w*|за результат\w*)"
    r"[^\n]{0,120}\.agent/drafts|"
    r"\.agent/drafts[^\n]{0,120}(?:готов\w*|итог\w*|на выходе|за результат\w*)",
    re.IGNORECASE,
)
READY_CLAIM_RE = re.compile(
    r"(?:готов(?:ый|ого|ые|ых)\s+(?:`?\.docx`?|документ\w*|файл\w*)|"
    r"дом\s+готов\w*|на выходе[^\n]{0,40}\.docx|свежайш\w*[^\n]{0,30}\.docx|"
    r"за результат\w*)",
    re.IGNORECASE,
)
HOME_AFTER_CLAIM_RE = re.compile(
    r"(?:\b(?:в|из)\s+(?:(?:папк|каталог|директори)\w*\s+)?|→\s*|:\s*)"
    r"`?[^`\s,;)]+/",
    re.IGNORECASE,
)


def _instruction_files(root):
    """Живые инструкции; исторические разборы и очередь намеренно не входят."""
    root = Path(root)
    files = {root / "AGENTS.md", root / "CLAUDE.md", root / ".claude/CLAUDE.md"}
    # `.agents/` и `.codex/` — механическое производное `sync_prompts.py`;
    # здесь проверяется канон, а побайтовый дрейф производного держит его --check.
    for pattern in (".claude/commands/*.md", ".claude/agents/*.md",
                    ".claude/skills/*/*.md"):
        files.update(root.glob(pattern))
    return sorted(p for p in files if p.is_file())


def _contract_text_errors(path, text):
    errors = []
    for number, line in enumerate(text.splitlines(), 1):
        if (DIRECT_DRAFT_DOCX_RE.search(line) or DOCX_IN_DRAFTS_RE.search(line)
                or VERSIONED_DRAFT_RE.search(line)):
            errors.append(f"{path}:{number}: готовый файл назван в служебной папке")
        elif WRONG_HOME_RE.search(line) and READY not in line and "case_paths.py" not in line:
            errors.append(f"{path}:{number}: назван другой дом готового документа")
        else:
            claim = READY_CLAIM_RE.search(line)
            tail = line[claim.end():claim.end() + 120] if claim else ""
            if HOME_AFTER_CLAIM_RE.search(tail) and READY not in tail:
                errors.append(
                    f"{path}:{number}: дом готового документа объявлен вне case_paths.py")
    return errors


def instruction_contract_errors(root):
    """Расхождения живых инструкций с машинным контрактом документа."""
    root = Path(root)
    errors = []
    for rel in CONTRACT_DOCS:
        path = root / rel
        if not path.is_file():
            errors.append(f"{rel}: файл контракта не найден")
            continue
        text = path.read_text(encoding="utf-8")
        if "case_paths.py" not in text:
            errors.append(f"{rel}: нет ссылки на scripts/case_paths.py")
    for path in _instruction_files(root):
        errors.extend(_contract_text_errors(path.relative_to(root),
                                            path.read_text(encoding="utf-8")))
    for rel in REVIEW_CONTRACT_DOCS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in ROUND_LIMIT_RE.finditer(text):
            raw = (match.group("leading") or match.group("trailing")).lower()
            value = int(raw) if raw.isdigit() else ROUND_WORDS[raw]
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{rel}:{line}: число раундов {value} повторено вместо ссылки на case_paths.py")
    return errors


def selftest():
    import tempfile
    assert CONTEXT == ".agent/context" and DRAFTS == ".agent/drafts"
    assert READY == "GOTOVO" and READY.isascii(), "имя папки готовых обязано быть латиницей"
    assert AGENT_DIR.startswith("."), "кухня обязана быть скрытой"
    assert all(p.isascii() for p in (AGENT_DIR, INTAKE, HEARINGS, READY, CONTEXT, DRAFTS))
    assert document_contract("cases/x/y") == {
        "draft_md": Path("cases/x/y/.agent/drafts/{document}.md"),
        "ready_md": Path("cases/x/y/GOTOVO/{document}.md"),
        "ready_docx": Path("cases/x/y/GOTOVO/{document}.docx"),
        "review_rounds": MAX_REVIEW_ROUNDS,
    }

    root = Path(__file__).resolve().parent.parent
    verdict_config_path = root / "config/verdict.json"
    if verdict_config_path.is_file():
        verdict_config = json.loads(verdict_config_path.read_text(encoding="utf-8"))
        assert "round_limit" not in verdict_config, \
            "базовый лимит повторен в config/verdict.json вместо case_paths.py"
    errors = instruction_contract_errors(root)
    assert not errors, "один дом документа нарушен:\n" + "\n".join(errors)
    hostile = _contract_text_errors(
        "hostile.md", "Готовый .docx живет в .agent/drafts/chuzhoy.docx")
    assert hostile, "враждебная инструкция с другим домом не поймана"
    hostile = _contract_text_errors("hostile.md", "Готовый .docx живет в CHUZHOY-DOM/")
    assert hostile, "произвольный чужой дом готового документа не пойман"
    hostile = _contract_text_errors("hostile.md", "Свежайший .docx брать из tmp/out")
    assert hostile, "чужой дом после предлога «из» не пойман"
    hostile = _contract_text_errors(
        "hostile.md", "Готовый документ в tmp/out, см. scripts/case_paths.py")
    assert hostile, "ссылка на case_paths.py замаскировала чужой дом"
    round_match = ROUND_LIMIT_RE.search("До 5 раундов")
    assert round_match
    raw_rounds = (round_match.group("leading") or round_match.group("trailing")).lower()
    hostile_rounds = int(raw_rounds) if raw_rounds.isdigit() else ROUND_WORDS[raw_rounds]
    assert hostile_rounds != MAX_REVIEW_ROUNDS, \
        "чужой лимит рецензии не распознан"
    for rules in root.rglob("AGENTS.md"):
        if ".git" not in rules.parts:
            assert len(rules.read_text(encoding="utf-8").splitlines()) <= 200, \
                f"{rules.relative_to(root)}: превышен лимит 200 строк"
    for rules in root.rglob("CLAUDE.md"):
        if ".git" not in rules.parts:
            assert len(rules.read_text(encoding="utf-8").splitlines()) <= 200, \
                f"{rules.relative_to(root)}: превышен лимит 200 строк"

    # Порядок замен: длинный ключ раньше короткого
    assert modernize("cases/x/y/03_drafts/_baselines/a.docx") == \
        "cases/x/y/.agent/drafts/_baselines/a.docx"
    assert modernize("01_context/_working/brief.md") == ".agent/context/_working/brief.md"
    assert modernize("00_intake/скан.pdf") == "00_intake/скан.pdf", "интейк тронут"
    assert modernize("02_hearings/x") == "02_hearings/x", "заседания тронуты"

    with tempfile.TemporaryDirectory(prefix="casepaths-selftest-") as tmp:
        case = Path(tmp) / "delo-2026"
        for d in ("01_context/_working", "03_drafts/_baselines", "04_archive",
                  "00_intake", "02_hearings"):
            (case / d).mkdir(parents=True)
        moves = migration_moves(case)
        assert len(moves) == 3, f"переезжать должны ровно три каталога, а не {len(moves)}"
        assert not any("00_intake" in str(s) or "02_hearings" in str(s) for s, _ in moves), \
            "неприкосновенный каталог попал в переезд"
        assert not is_migrated(case), "непереехавшее дело объявлено переехавшим"
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        assert is_migrated(case), "переехавшее дело не опознано"
        assert migration_moves(case) == [], "повторный переезд не идемпотентен"
        assert baselines(case) == case / ".agent/drafts/_baselines"
        assert ready(case).name == "GOTOVO"
        # Журнал вердиктов — вне _working (D03): новый адрес прямо в drafts,
        # старый (в _working) отдельным helper-ом для чтения-запаса.
        assert verdicts(case) == case / ".agent/drafts/verdicts.jsonl"
        assert verdicts_legacy(case) == case / ".agent/drafts/_working/verdicts.jsonl"
        assert WORKING not in verdicts(case).parts, "журнал остался в слепом пятне сторожа"

        # Файл прогона (M01): один адрес в .agent/context, аддитивная запись
        assert run_state(case) == case / ".agent/context/run.json"
        assert run_read(case) == {}, "пустое состояние не пусто"
        run_write(case, guide="themis-pipeline")
        run_write(case, preflight_code=0)
        st = run_read(case)
        assert st == {"guide": "themis-pipeline", "preflight_code": 0}, \
            f"аддитивная запись прогона сломана: {st}"

    # Unicode: NFD и NFC одного имени обязаны считаться одним делом
    nfd_name = unicodedata.normalize("NFD", "кузнецова-йогурт")
    nfc_name = unicodedata.normalize("NFC", "кузнецова-йогурт")
    assert nfd_name != nfc_name, "тестовое имя не различается по формам — проверка пустая"
    assert nfc(nfd_name) == nfc(nfc_name), "нормализация не сводит формы"
    assert collisions([nfd_name, nfc_name]), "коллизия NFD/NFC не поймана"
    assert not collisions(["ivanov-ivan", "petrov-petr"]), "ложная коллизия на разных именах"
    # Турецкая «ı» без точки — не латинская «i»: разные буквы, коллизией не считаются.
    # Такое имя на диске есть; фикстура вымышленная — сторож ПД поймал реальное 19.08.2026.
    assert not collisions(["demidov-ab", "demıdov-ab"]), "разные буквы объявлены коллизией"
    print("selftest: один дом готового документа, один лимит рецензии, инструкции ≤200 строк; "
          "контракт латиницей, скрытая кухня, порядок замен, неприкосновенные каталоги, "
          "идемпотентность переезда, NFD/NFC — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Контракт раскладки дела.")
    ap.add_argument("--show", metavar="CASE", help="показать пути конкретного дела")
    ap.add_argument("--document-contract", nargs="?", const="cases/{client}/{case}",
                    metavar="CASE", help="показать дом, имена файлов и лимит рецензии")
    ap.add_argument("--run-get", nargs="+", metavar=("CASE", "KEY"),
                    help="прочитать файл прогона (CASE [KEY])")
    ap.add_argument("--run-set", nargs=3, metavar=("CASE", "KEY", "VALUE"),
                    help="записать ключ в файл прогона (VALUE как JSON, иначе строка)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.document_contract is not None:
        for key, value in document_contract(a.document_contract).items():
            print(f"{key}={value}")
        return 0
    if a.run_set:
        case, key, value = a.run_set
        try:
            value = json.loads(value)      # число/булево/строка-в-кавычках
        except ValueError:
            pass                            # голая строка
        run_write(case, **{key: value})
        print(f"прогон {case}: {key} = {value!r}")
        return 0
    if a.run_get:
        case = a.run_get[0]
        st = run_read(case)
        if len(a.run_get) > 1:
            print(json.dumps(st.get(a.run_get[1]), ensure_ascii=False))
        else:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    if a.show:
        c = Path(a.show)
        for label, p in (("интейк", intake(c)), ("заседания", hearings(c)),
                         ("ГОТОВО", ready(c)), ("контекст", context(c)),
                         ("черновики", drafts(c)), ("снимки", baselines(c)),
                         ("архив", archive(c))):
            print(f"  {label:12} {p}")
        print(f"  переехало:   {'да' if is_migrated(c) else 'нет'}")
        return 0
    print(f"раскладка дела: {' · '.join(HUMAN_VISIBLE)} + {AGENT_DIR}/"
          f"{{context,drafts,archive}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
