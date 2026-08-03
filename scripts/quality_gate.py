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
_CYR = re.compile(r"[А-Яа-яЁё]")
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


def mixed_script_share(text: str) -> tuple[float, int]:
    """(доля слов со смешением алфавитов, сколько слов оценено)."""
    words = [w.strip("«»\"'()[].,;:!?—-–№") for w in text.split()]
    words = [w for w in words if len(w) >= 4 and (_CYR.search(w) or _LAT.search(w))]
    if not words:
        return 0.0, 0
    mixed = sum(1 for w in words if _CYR.search(w) and _LAT.search(w))
    return mixed / len(words) * 100, len(words)


def garbled_share(text: str) -> tuple[float, int]:
    """(доля непохожих слов в процентах, сколько слов оценено)."""
    marks = [word_is_odd(w) for w in text.split()]
    marks = [m for m in marks if m is not None]
    if not marks:
        return 0.0, 0
    return sum(marks) / len(marks) * 100, len(marks)


def page_text_bad(text: str) -> str:
    """Пусто — страница в порядке. Иначе причина, по которой её нельзя читать как текст.

    Две разные поломки, и одна метрика их не ловит:
      • латиница внутри кириллицы — чужой OCR, запечённый в PDF («Рссn)'6л~1кс»);
      • ломаная кириллица — слабый скан, гильош, печать поверх текста.
    Прежний гейт не проверял НИ ОДНУ из них: единственная проверка страницы была
    про таблицы, и флаг «бледная сетка» глушил даже её.
    """
    mixed, words = mixed_script_share(text)
    if words >= 20 and mixed > MIXED_MAX_SHARE:
        return (f"{mixed:.0f}% слов смешивают кириллицу с латиницей (порог "
                f"{MIXED_MAX_SHARE:.0f}%, оценено {words}) — признак чужого OCR, "
                "запечённого в файл; страницу перераспознать")
    share, counted = garbled_share(text)
    if counted >= 20 and share > GARBLED_MAX_SHARE:
        return (f"{share:.0f}% слов непохожи на русские (порог {GARBLED_MAX_SHARE:.0f}%, "
                f"оценено {counted}) — распознавание испорчено, читать нельзя")
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


def check_numbers(draft: str, sources: list[str], min_digits: int = 4) -> list[str]:
    """Числа черновика обязаны быть в источниках: сумма из воздуха — цена документа."""
    from crosscheck_numbers import numbers_of
    text = open(draft, encoding="utf-8", errors="ignore").read()
    in_draft = numbers_of(text, min_digits)
    in_src = None
    for s in sources:
        n = numbers_of(open(s, encoding="utf-8", errors="ignore").read(), min_digits)
        in_src = n if in_src is None else (in_src | n)
    orphan = in_draft - (in_src or type(in_draft)())
    # Даты и годы выкидываем: «01.02» и «2026» из даты документа законно отсутствуют
    # в карте дела, а в отчёте они забивают собой реальные суммы. Даты — зона Кони.
    for tok in [t for t in orphan
                if re.fullmatch(r"\d{2}\.\d{2}", t) or re.fullmatch(r"(19|20)\d{2}", t)]:
        del orphan[tok]
    if not orphan:
        return []
    items = ", ".join(sorted(orphan)[:25])
    return [f"в черновике {sum(orphan.values())} чисел, которых нет ни в одном источнике: "
            f"{items}{'…' if len(orphan) > 25 else ''} — подтвердить материалами дела "
            f"или убрать (проверялось от {min_digits} значащих цифр)"]


def check_requisites(path: str, bik: str | None) -> list[str]:
    """ИНН/ОГРН/СНИЛС/счёт — контрольные суммы локально, без сети."""
    from verify_requisites import scan_requisites
    try:
        req = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"{path}: не читается ({e})"]
    return list(scan_requisites(req, bik))


def case_paths(case: str) -> dict:
    """Что из дела можно проверить машиной."""
    ctx = os.path.join(case, "01_context")
    drafts = [p for p in sorted(glob.glob(os.path.join(case, "03_drafts", "*.md")))
              if "_working" not in p and "_baselines" not in p]
    sources = [p for p in (os.path.join(ctx, n) for n in
                           ("knowledge-map.md", "positions.md", "practice.md"))
               if os.path.isfile(p)]
    return {"drafts": drafts, "sources": sources}


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
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
