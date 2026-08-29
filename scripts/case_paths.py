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
import os
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


def selftest():
    import tempfile
    assert CONTEXT == ".agent/context" and DRAFTS == ".agent/drafts"
    assert READY == "GOTOVO" and READY.isascii(), "имя папки готовых обязано быть латиницей"
    assert AGENT_DIR.startswith("."), "кухня обязана быть скрытой"
    assert all(p.isascii() for p in (AGENT_DIR, INTAKE, HEARINGS, READY, CONTEXT, DRAFTS))

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
    print("selftest: контракт латиницей, скрытая кухня, порядок замен, неприкосновенные "
          "каталоги, идемпотентность переезда, NFD/NFC — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Контракт раскладки дела.")
    ap.add_argument("--show", metavar="CASE", help="показать пути конкретного дела")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
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
