#!/usr/bin/env python3
"""quality_gate.py — механические проверки качества одной командой.

В проекте лежали четыре готовые проверки, которых никто не звал: аудит полноты
OCR-кеша, детектор потерянных таблиц, сверка чисел документа с источником и
контрольные суммы реквизитов. Пока их не вызывают, ту же работу делает модель —
дороже и хуже: 20-60k токенов рассуждения на то, что арифметика решает за секунду.

Режимы (можно комбинировать):
    quality_gate.py --ocr OCR_DIR          полнота страниц + таблицы (после OCR)
    quality_gate.py --doc ЧЕРНОВИК.md --against ИСТОЧНИК.md [ИСТОЧНИК2.md ...]
    quality_gate.py --requisites FILE.requisites.json [--bik БИК]
    quality_gate.py --case cases/К/Д       всё, что применимо к делу
    quality_gate.py --selftest             проверка без сети

Код возврата: 0 — чисто; 1 — есть замечания (блокирующие для приёмки).
Замечание не отменяет содержательный ревью: машина ловит числа и структуру,
месяц просрочки и неприменимость нормы ловит только проверяющий.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Порог доли непохожих на русские слов. Замер по 87 страницам живого OCR-кеша
# 03.08.2026: медиана 0,0%, худшая страница 3,3%. Порог 15% отделяет порченый
# лист от чистого с большим запасом и не даёт ложных тревог на нашем материале.
GARBLED_MAX_SHARE = 15.0
# Доля слов, где кириллица и латиница смешаны ВНУТРИ одного слова. Именно так
# выглядит чужой OCR, запечённый в PDF: «Рссn)'6л~1кс». Общая доля латиницы для
# этого не годится — «Volvo XC60» и «KTM 390» в отчёте оценщика законны, и по ней
# страница с марками машин давала 42% и ложную тревогу (замер 03.08.2026).
MIXED_MAX_SHARE = 8.0
# Доля без абсолютного минимума слепа к коротким страницам, а доля с минимумом
# ВЫБОРКИ слепа к ним же наоборот: прежнее условие «оценено >= 20 слов» пропускало
# порченый титульный лист, справку и приложение на пол-листа — там слов меньше
# двадцати, и гейт печатал «✓ OCR: чисто» на сплошном мусоре (замер 03.08.2026:
# 15 слов «Росскіской Федерациг… Двадцять… хвалаать» → rc=0).
# Правильный якорь — не размер выборки, а АБСОЛЮТНОЕ число испорченных слов:
# три ломаных слова на странице не бывают шумом ни при какой её длине, а на
# длинной странице три слова из двухсот не дают превышения доли и тревоги не поднимут.
MIN_BAD_WORDS = 3
_CYR = re.compile(r"[А-Яа-яЁё]")
# Кириллица не русского алфавита. В русском процессуальном документе её нет:
# і/ї/є (украинская), ў (белорусская), ђ/ћ/џ (сербская), ә/ғ/қ/ң/ө/ұ/һ (казахская).
# Распознаватель ставит их вместо похожих русских — слово остаётся «русским» на
# вид, доля латиницы не растёт, а реквизит уже другой.
_FOREIGN_CYR = re.compile(r"[\u0400-\u04FF]")
_RU_ALPHABET = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
                         "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")
_LAT = re.compile(r"[A-Za-z]")
_VOWELS = set("аеёиоуыэюя")


def word_is_odd(word: str) -> bool | None:
    """Слово непохоже на русское. None — слово не оценивается (цифры, латиница целиком)."""
    w = word.strip("«»\"'()[].,;:!?—-–№")
    if len(w) < 4 or not re.fullmatch(r"[А-Яа-яЁё]+", w):
        return None
    lw = w.lower()
    if not (set(lw) & _VOWELS):
        return True                                    # ни одной гласной
    if re.search(r"[бвгджзйклмнпрстфхцчшщ]{5,}", lw):
        return True                                    # пять согласных подряд
    if re.search(r"[а-яё][А-ЯЁ]", w[1:]):
        return True                                    # заглавная в середине слова
    if re.search(r"(.)\1{3,}", lw):
        return True                                    # четыре одинаковых подряд
    return False


def foreign_cyrillic_letters(text: str) -> dict[str, int]:
    """Кириллические буквы вне русского алфавита: {буква: сколько раз}."""
    out: dict[str, int] = {}
    for c in _FOREIGN_CYR.findall(text):
        if c not in _RU_ALPHABET:
            out[c] = out.get(c, 0) + 1
    return out


def mixed_script_share(text: str) -> tuple[float, int, int]:
    """(доля слов со смешением алфавитов, сколько оценено, сколько испорчено)."""
    words = [w.strip("«»\"'()[].,;:!?—-–№") for w in text.split()]
    words = [w for w in words if len(w) >= 4 and (_CYR.search(w) or _LAT.search(w))]
    if not words:
        return 0.0, 0, 0
    mixed = sum(1 for w in words if _CYR.search(w) and _LAT.search(w))
    return mixed / len(words) * 100, len(words), mixed


def garbled_share(text: str) -> tuple[float, int, int]:
    """(доля непохожих слов в процентах, сколько оценено, сколько испорчено)."""
    marks = [word_is_odd(w) for w in text.split()]
    marks = [m for m in marks if m is not None]
    if not marks:
        return 0.0, 0, 0
    bad = sum(marks)
    return bad / len(marks) * 100, len(marks), bad


def page_text_bad(text: str) -> str:
    """Пусто — страница в порядке. Иначе причина, по которой её нельзя читать как текст.

    Две разные поломки, и одна метрика их не ловит:
      • латиница внутри кириллицы — чужой OCR, запечённый в PDF («Рссn)'6л~1кс»);
      • ломаная кириллица — слабый скан, гильош, печать поверх текста.
    Прежний гейт не проверял НИ ОДНУ из них: единственная проверка страницы была
    про таблицы, и флаг «бледная сетка» глушил даже её.
    """
    mixed, words, mixed_n = mixed_script_share(text)
    if mixed_n >= MIN_BAD_WORDS and mixed > MIXED_MAX_SHARE:
        return (f"{mixed:.0f}% слов смешивают кириллицу с латиницей (порог "
                f"{MIXED_MAX_SHARE:.0f}%, испорчено {mixed_n} из {words}) — признак "
                "чужого OCR, запечённого в файл; страницу перераспознать")
    share, counted, bad = garbled_share(text)
    if bad >= MIN_BAD_WORDS and share > GARBLED_MAX_SHARE:
        return (f"{share:.0f}% слов непохожи на русские (порог {GARBLED_MAX_SHARE:.0f}%, "
                f"испорчено {bad} из {counted}) — распознавание испорчено, читать нельзя")
    # Третья поломка, которую доля не ловит: соседняя кириллица. Распознаватель
    # подменяет русскую «и» украинской «і», «е» — «є», «у» — белорусской «ў».
    # Слово остаётся кириллическим и «похожим на русское», доля чужих букв
    # остаётся низкой, а реквизит в нём уже другой. В русском процессуальном
    # документе таких букв не бывает — три штуки на лист это порча, не цитата.
    alien = foreign_cyrillic_letters(text)
    if sum(alien.values()) >= MIN_BAD_WORDS:
        listed = ", ".join(f"«{c}»×{n}" for c, n in sorted(alien.items(), key=lambda x: -x[1])[:6])
        return (f"нерусские кириллические буквы: {listed} — распознаватель подменил "
                "русские буквы соседними, страницу сверить по PNG")
    return ""


def check_ocr(ocr_dir: str) -> list[str]:
    """Каждая отрисованная страница дошла до текста; таблицы не потеряны."""
    problems: list[str] = []
    pngs = sorted(glob.glob(os.path.join(ocr_dir, "page_*.png")))
    if not pngs:
        return [f"{ocr_dir}: нет отрисованных страниц (page_*.png) — OCR не выполнялся"]

    man_path = os.path.join(ocr_dir, "manifest.json")
    if os.path.isfile(man_path):
        try:
            man = json.load(open(man_path, encoding="utf-8"))
            if not man.get("complete", True):
                problems.append(
                    f"МАНИФЕСТ НЕПОЛОН: missing={man.get('missing')}, "
                    f"за порогом={man.get('beyond_maxp')} — эти страницы НЕ извлечены")
        except (OSError, ValueError) as e:
            problems.append(f"манифест не читается ({e}) — полнота OCR не подтверждена")

    from table_guard import grid_signals, text_has_structure
    lost, empty, faint_lost = [], [], []
    for png in pngs:
        txt_path = os.path.splitext(png)[0] + ".txt"
        page = os.path.basename(png)
        if not os.path.isfile(txt_path):
            problems.append(f"{page}: страница отрисована, но НЕ распознана (нет .txt)")
            continue
        txt = open(txt_path, encoding="utf-8", errors="ignore").read()
        if not txt.strip():
            empty.append(page)
            continue
        bad = page_text_bad(txt)
        if bad:
            problems.append(f"{page}: {bad}")
        g = grid_signals(png)
        if g["grid"] and not text_has_structure(txt):
            # Бледная сетка больше не глушит сигнал целиком: table_guard.py для той же
            # страницы даёт TABLE_STRUCTURE_LOST, и два инструмента проекта расходились
            # в вердикте, причём слабейший стоял на входе.
            (lost if not g["faint"] else faint_lost).append(page)
    if lost:
        problems.append(
            f"TABLE_STRUCTURE_LOST на {len(lost)} стр. ({', '.join(lost[:6])}"
            f"{'…' if len(lost) > 6 else ''}) — сетка на растре есть, в тексте структуры нет. "
            "Читать эти страницы по .md структурного OCR либо сверять по PNG.")
    if faint_lost:
        problems.append(
            f"бледная сетка без структуры в тексте на {len(faint_lost)} стр. "
            f"({', '.join(faint_lost[:6])}{'…' if len(faint_lost) > 6 else ''}) — "
            "слабый сигнал, страницы глянуть глазами либо сверить по PNG.")
    if empty:
        problems.append(
            f"пустой OCR на {len(empty)} стр. ({', '.join(empty[:6])}"
            f"{'…' if len(empty) > 6 else ''}) — рукопись/слабый скан, нужен фолбэк.")
    return problems


# Практика — НЕ источник фактов дела. Числа из чужих судебных актов (суммы
# взысканий, номера дел, годы) лежат в practice.md и раньше входили в общий
# котёл сверки: число, выдуманное для доверителя, «подтверждалось» совпадением с
# числом из чужого дела и замечание молча снималось. Факты дела дают только
# материалы и карта; позиция — производная от них и тоже допустима.
PRACTICE_SOURCES = ("practice.md", "hunter_classic.md", "hunter_skeptic.md",
                    "hunter_tactical.md")


def is_practice_source(path: str) -> bool:
    return os.path.basename(path) in PRACTICE_SOURCES


def check_numbers(draft: str, sources: list[str], min_digits: int = 4) -> list[str]:
    """Числа черновика обязаны быть в источниках ФАКТОВ дела, а не в практике."""
    from crosscheck_numbers import numbers_of
    text = open(draft, encoding="utf-8", errors="ignore").read()
    in_draft = numbers_of(text, min_digits)
    facts = [s for s in sources if not is_practice_source(s)]
    practice = [s for s in sources if is_practice_source(s)]
    in_src = None
    for s in facts:
        n = numbers_of(open(s, encoding="utf-8", errors="ignore").read(), min_digits)
        in_src = n if in_src is None else (in_src | n)
    in_practice = None
    for s in practice:
        n = numbers_of(open(s, encoding="utf-8", errors="ignore").read(), min_digits)
        in_practice = n if in_practice is None else (in_practice | n)
    orphan = in_draft - (in_src or type(in_draft)())
    # Даты и годы выкидываем: «01.02» и «2026» из даты документа законно отсутствуют
    # в карте дела, а в отчёте они забивают собой реальные суммы. Даты — зона Кони.
    for tok in [t for t in orphan
                if re.fullmatch(r"\d{2}\.\d{2}", t) or re.fullmatch(r"(19|20)\d{2}", t)]:
        del orphan[tok]
    if not orphan:
        return []
    out = []
    # Число, которого нет в фактах дела, но есть в практике, — отдельный и более
    # опасный случай: оно выглядит подтверждённым, хотя пришло из ЧУЖОГО дела.
    from_practice = sorted(t for t in orphan if in_practice and t in in_practice)
    if from_practice:
        out.append(f"{len(from_practice)} чисел взяты из практики, а не из фактов дела: "
                   f"{', '.join(from_practice[:15])}"
                   f"{'…' if len(from_practice) > 15 else ''} — в чужом судебном акте "
                   "такая сумма есть, у доверителя её может не быть; сверить с материалами")
    rest = {t: c for t, c in orphan.items() if t not in set(from_practice)}
    if rest:
        items = ", ".join(sorted(rest)[:25])
        out.append(f"в черновике {sum(rest.values())} чисел, которых нет ни в одном источнике: "
                   f"{items}{'…' if len(rest) > 25 else ''} — подтвердить материалами дела "
                   f"или убрать (проверялось от {min_digits} значащих цифр)")
    return out


def check_requisites(path: str, bik: str | None) -> list[str]:
    """ИНН/ОГРН/СНИЛС/счёт — контрольные суммы локально, без сети."""
    from verify_requisites import scan_requisites
    try:
        req = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{path}: не читается ({e})"]
    return list(scan_requisites(req, bik))


EXTRACT_CACHE = os.path.expanduser("~/.cache/legal_extract")


def case_requisite_files(case: str, cache_dir: str = EXTRACT_CACHE) -> list[str]:
    """Файлы <sha>.requisites.json, относящиеся к материалам ЭТОГО дела.

    Кеш роутера адресуется по СОДЕРЖИМОМУ: имя артефакта — sha256 исходника.
    Значит принадлежность делу считается точно, тем же способом, что и в
    markdown_extract.purge_case: берём sha каждого материала дела и ищем его
    артефакт. Никакого сопоставления по именам — материалы дела лежат с
    кириллицей и пробелами, по имени связь не восстанавливается.

    Зачем вообще: до 04.08.2026 ветка `--requisites` не вызывалась НИ ОДНИМ
    агентом (`grep -rn -- "--requisites"` давал попадания только внутри самого
    quality_gate.py), то есть scan_requisites не отработала ни разу ни на одном
    деле — при том, что прецедент 02.08.2026 дал в карте дела несуществующие
    ИНН и ОГРН.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from markdown_extract import IMAGE, OFFICE, ext_of, sha_of

    kinds = OFFICE | IMAGE | {"pdf", "doc", "xls", "ppt"}
    out: list[str] = []
    for root, dirs, files in os.walk(case):
        dirs[:] = [d for d in dirs if d not in ("_baselines", "__pycache__")]
        for name in sorted(files):
            path = os.path.join(root, name)
            if ext_of(path) not in kinds:
                continue
            try:
                req = os.path.join(cache_dir, f"{sha_of(path)}.requisites.json")
            except OSError:
                continue
            if os.path.isfile(req) and req not in out:
                out.append(req)
    return out


def case_paths(case: str) -> dict:
    """Что из дела можно проверить машиной."""
    ctx = os.path.join(case, "01_context")
    drafts = [p for p in sorted(glob.glob(os.path.join(case, "03_drafts", "*.md")))
              if "_working" not in p and "_baselines" not in p]
    sources = [p for p in (os.path.join(ctx, n) for n in
                           ("knowledge-map.md", "positions.md", "practice.md"))
               if os.path.isfile(p)]
    return {"drafts": drafts, "sources": sources,
            "requisites": case_requisite_files(case)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Механические проверки качества Фемиды")
    ap.add_argument("--ocr", help="папка OCR-кеша (page_*.png + page_*.txt)")
    ap.add_argument("--doc", help="черновик .md для сверки чисел")
    ap.add_argument("--against", nargs="+", default=[], help="источники чисел")
    ap.add_argument("--requisites", help="<sha>.requisites.json от роутера")
    ap.add_argument("--bik", help="БИК для проверки расчётного счёта")
    ap.add_argument("--case", help="папка дела — прогнать всё применимое")
    ap.add_argument("--min-digits", type=int, default=4,
                    help="от скольких значащих цифр сверять числа (по умолчанию 4)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    report: list[tuple[str, list[str]]] = []
    if args.ocr:
        report.append((f"OCR {args.ocr}", check_ocr(args.ocr)))
    if args.doc:
        if not args.against:
            print("--doc требует --against (источники чисел)", file=sys.stderr)
            return 2
        report.append((f"числа {os.path.basename(args.doc)}",
                       check_numbers(args.doc, args.against, args.min_digits)))
    if args.requisites:
        report.append((f"реквизиты {os.path.basename(args.requisites)}",
                       check_requisites(args.requisites, args.bik)))
    if args.case:
        paths = case_paths(args.case)
        if not paths["drafts"]:
            report.append((f"дело {args.case}", ["черновиков в 03_drafts/ нет — сверять нечего"]))
        for d in paths["drafts"]:
            if paths["sources"]:
                report.append((f"числа {os.path.basename(d)}",
                               check_numbers(d, paths["sources"], args.min_digits)))
            else:
                report.append((f"числа {os.path.basename(d)}",
                               ["нет ни карты, ни позиции — числа сверять не с чем"]))
        # Реквизиты материалов дела. Ветка была написана, но не подключена:
        # ИНН/ОГРН/СНИЛС/БИК/счета доверителя проверяла модель на глаз либо никто.
        reqs = paths["requisites"]
        if reqs:
            found: list[str] = []
            for r in reqs:
                found += check_requisites(r, args.bik)
            report.append((f"реквизиты материалов дела ({len(reqs)} файлов)", found))
        else:
            report.append((f"реквизиты дела {args.case}",
                           ["в кеше роутера нет ни одного <sha>.requisites.json для "
                            "материалов дела — материалы не прогнаны через "
                            "markdown_extract.py, реквизиты НЕ проверены"]))
    if not report:
        print("нечего проверять: задать --ocr / --doc / --requisites / --case", file=sys.stderr)
        return 2

    problems = 0
    for title, items in report:
        if items:
            problems += len(items)
            print(f"\n⚠ {title}:")
            for it in items:
                print(f"   • {it}")
        else:
            print(f"✓ {title}: чисто")
    print(f"\nитого замечаний: {problems}")
    return 1 if problems else 0


def selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()

    draft = os.path.join(tmp, "draft.md")
    src = os.path.join(tmp, "map.md")
    open(draft, "w", encoding="utf-8").write("Взыскать 1250000 руб. по договору 4412 от 01.02.2026.")
    open(src, "w", encoding="utf-8").write("Договор 4412. Сумма 1250000 руб.")
    clean = check_numbers(draft, [src])

    # Фикстуры для разделения источников: одно и то же число 7654321 лежит либо
    # в карте дела (факт доверителя), либо только в practice.md (чужое дело).
    draft_pr = os.path.join(tmp, "draft_pr.md")
    map_pr = os.path.join(tmp, "knowledge-map.md")
    practice_pr = os.path.join(tmp, "practice.md")
    draft_fact = os.path.join(tmp, "draft_fact.md")
    map_fact = os.path.join(tmp, "map_fact", "knowledge-map.md")
    os.makedirs(os.path.dirname(map_fact), exist_ok=True)
    open(draft_pr, "w", encoding="utf-8").write("Взыскать 7654321 руб.")
    open(map_pr, "w", encoding="utf-8").write("Договор 4412 без сумм.")
    open(practice_pr, "w", encoding="utf-8").write("По делу А65-1/2020 взыскано 7654321 руб.")
    open(draft_fact, "w", encoding="utf-8").write("Взыскать 7654321 руб.")
    open(map_fact, "w", encoding="utf-8").write("Долг доверителя 7654321 руб.")

    open(draft, "w", encoding="utf-8").write("Взыскать 9999999 руб. по договору 4412.")
    dirty = check_numbers(draft, [src])

    req_ok = os.path.join(tmp, "a.requisites.json")
    json.dump({"inn": ["7707083893"]}, open(req_ok, "w", encoding="utf-8"))
    req_bad = os.path.join(tmp, "b.requisites.json")
    json.dump({"inn": ["7707083894"]}, open(req_bad, "w", encoding="utf-8"))

    empty_ocr = os.path.join(tmp, "ocr")
    os.makedirs(empty_ocr)

    checks = [
        ("совпавшие числа замечаний не дают", clean == []),
        ("число из воздуха ловится", len(dirty) == 1 and "9999999" in dirty[0]),
        # Практика — не источник фактов дела. Число из ЧУЖОГО судебного акта
        # раньше входило в общий котёл и легализовало выдуманную сумму доверителя.
        ("число, найденное только в практике, замечание не снимает",
         any("из практики" in p for p in check_numbers(draft_pr, [map_pr, practice_pr]))),
        ("число, найденное в карте дела, замечаний не даёт",
         check_numbers(draft_fact, [map_fact, practice_pr]) == []),
        ("practice.md опознан как практика", is_practice_source("/x/01_context/practice.md")),
        ("hunter-файл опознан как практика",
         is_practice_source("/x/01_context/hunter_classic.md")),
        ("карта дела практикой не считается",
         not is_practice_source("/x/01_context/knowledge-map.md")),
        ("позиция практикой не считается",
         not is_practice_source("/x/01_context/positions.md")),
        ("валидный ИНН проходит", check_requisites(req_ok, None) == []),
        ("битый ИНН ловится", len(check_requisites(req_bad, None)) == 1),
        ("пустая OCR-папка — замечание", len(check_ocr(empty_ocr)) == 1),
        ("нечитаемый requisites не роняет", len(check_requisites(tmp + "/нет.json", None)) == 1),
        # Качество распознавания страницы. Прежний гейт не проверял его вовсе:
        # единственной проверкой листа была таблица, и «бледная сетка» глушила её.
        ("чистый русский текст проходит", page_text_bad(
            "Суд установил, что ответчик не исполнил обязательство по договору "
            "подряда в установленный срок и нарушил условия соглашения сторон, "
            "а также требования действующего законодательства о подряде и сроках") == ""),
        ("смешение алфавитов ловится", "смешивают" in page_text_bad(
            " ".join(["Рссn6лкс", "Тcтpстн", "Bepxoвный", "Cyдoм", "ycтaнoвлeнo"] * 6))),
        ("латинские марки в русском тексте тревоги не дают", page_text_bad(
            "Таблица 10.10 Марка Volvo XC60 II Год выпуска 2021 Пробег KM 109426 "
            "Стоимость рублей 3 730 000 Источник анализ Оценщика по данным рынка "
            "автомобилей марки KTM 390 Duke и прочих транспортных средств") == ""),
        ("ломаная кириллица ловится", "непохожи" in page_text_bad(
            " ".join(["стрктр", "првлн", "джквш", "БуКвА", "оооо" ] * 6))),
        ("короткий обрывок не оценивается", page_text_bad("Суд решил") == ""),
        ("цифры и реквизиты не считаются словами", garbled_share("7707083893 2-45/2026 №")[1] == 0),
        # Порченый ТИТУЛЬНЫЙ ЛИСТ: слов меньше двадцати, и прежний порог выборки
        # объявлял такую страницу чистой. Именно титульные листы, справки и
        # приложения на пол-листа несут реквизиты, ради которых страницу и читают.
        ("порченый титульный лист ловится при 15 словах",
         "непохожи" in page_text_bad(
             "Росскской Фдрциг пспрт грждннн кд пдрзделения Двадцять хвалаать "
             "выдн ГУВД гроду Кзни")),
        ("чистый титульный лист из 12 слов проходит",
         page_text_bad("Паспорт гражданина Российской Федерации выдан отделом "
                       "внутренних дел города Казани двадцатого марта") == ""),
        ("одно ломаное слово на короткой странице тревоги не даёт",
         page_text_bad("Настоящим подтверждается, что гражданин лично явился "
                       "на приём в отдел стрктр") == ""),
        ("два ломаных слова ещё не приговор",
         page_text_bad("Настоящим подтверждается, что гражданин лично явился "
                       "на приём в отдел стрктр джквш") == ""),
        ("три ломаных слова на короткой странице ловятся",
         "непохожи" in page_text_bad("Настоящим гражданин явился отдел "
                                     "стрктр джквш првлн")),
        # Соседняя кириллица: слово выглядит русским, доля латиницы не растёт,
        # но «і» вместо «и» меняет реквизит. 29 страниц из 132 в живом кеше.
        ("подмена русских букв украинскими ловится",
         "нерусские кириллические" in page_text_bad(
             "Паспорт гражданина Росскіской Федерациї выдан отделом внутренніх дел")),
        ("чистый русский текст чужой кириллицы не даёт",
         foreign_cyrillic_letters("Российской Федерации") == {}),
        ("одна чужая буква тревоги не даёт",
         page_text_bad("Паспорт гражданина Российской Федерациї выдан "
                       "отделом внутренних дел города Казани") == ""),
        ("короткая смесь алфавитов ловится",
         "смешивают" in page_text_bad("Пpиложение к Cпpaвке Bepxoвного Cyдa")),
        # Смешение алфавитов внутри слова бывает и законным: «SMS-сообщение»,
        # «IP-адрес», «PR-менеджер» — обычная лексика акта. Три таких слова на
        # длинной странице обязаны проходить, иначе гейт краснеет на живом тексте.
        ("законные IT-слова на длинной странице тревоги не дают",
         page_text_bad(" ".join(
             ["Суд установил что ответчик надлежащим образом извещён "
              "о времени и месте судебного заседания однако"] * 10)
             + " SMS-сообщение IP-адрес PR-менеджер") == ""),
        # Обратная сторона абсолютного порога: на длинной странице три ломаных слова
        # — обычный шум распознавания, а не поломка. Доля обязана их прощать, иначе
        # гейт начнёт краснеть на каждом втором нормальном листе и его отключат.
        ("три ломаных слова на длинной странице тревоги не дают",
         page_text_bad(" ".join(
             ["Суд установил что ответчик надлежащим образом извещён "
              "о времени и месте судебного заседания однако"] * 12)
             + " стрктр джквш првлн") == ""),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
