#!/usr/bin/env python3
"""quality_gate.py — механические проверки качества одной командой.

В проекте лежали четыре готовые проверки, которых никто не звал: аудит полноты
OCR-кеша, детектор потерянных таблиц, сверка чисел документа с источником и
контрольные суммы реквизитов. Пока их не вызывают, ту же работу делает модель —
дороже и хуже: 20-60k токенов рассуждения на то, что арифметика решает за секунду.

Режимы (можно комбинировать):
    quality_gate.py --ocr OCR_DIR          полнота страниц + таблицы (после OCR)
    quality_gate.py --doc ЧЕРНОВИК.md --against ИСТОЧНИК.md [ИСТОЧНИК2.md ...]
    quality_gate.py --requisites FILE.requisites.json [FILE2.requisites.json ...] [--bik БИК]
    quality_gate.py --case cases/К/Д       все, что применимо к делу
    quality_gate.py --rules                напечатать исполняемые правила гейта
    quality_gate.py --json                 тот же результат как JSON
    quality_gate.py --selftest             проверка без сети

Решения владельца лежат не в прозе брифа, а в
`.agent/context/_working/quality_gate.json`. Разобранные ложные замечания — в
`quality_gate.suppressions.jsonl`; причина обязательна. Запрет владельца подавить
нельзя.

Код возврата: 0 — чисто; 1 — есть замечания (блокирующие для приемки).
Замечание не отменяет содержательный ревью: машина ловит числа и структуру,
месяц просрочки и неприменимость нормы ловит только проверяющий.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import case_paths as cp  # noqa: E402


POLICY_NAME = "quality_gate.json"
SUPPRESSIONS_NAME = "quality_gate.suppressions.jsonl"
POLICY_KINDS = frozenset({"remark", "prohibition"})
POLICY_EXPECT = frozenset({"present", "absent"})


def policy_path(case: str) -> str:
    return str(cp.working(case) / POLICY_NAME)


def suppressions_path(case: str) -> str:
    return str(cp.working(case) / SUPPRESSIONS_NAME)


def finding(code: str, subject: str, message: str,
            kind: str = "remark") -> dict:
    """Стабильная машиночитаемая находка; id годится для глушителя."""
    body = "\0".join((kind, code, subject, message)).encode("utf-8")
    return {
        "id": hashlib.sha256(body).hexdigest()[:16],
        "kind": kind,
        "code": code,
        "subject": subject,
        "message": message,
    }


def load_policy(path: str | None, required: bool = False) -> tuple[list[dict], list[dict]]:
    """Вернуть (rules, config_findings). В режиме дела явный файл обязателен."""
    if not path or not os.path.exists(path):
        if not required:
            return [], []
        subject = os.path.basename(path) if path else POLICY_NAME
        return [], [finding(
            "config.policy", subject,
            "нет явного файла решений владельца; создать JSON version=1 с массивом rules",
            "prohibition")]
    try:
        with open(path, encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError) as exc:
        return [], [finding("config.policy", os.path.basename(path),
                            f"машинные правила владельца не прочитаны: {exc}",
                            "prohibition")]
    if not isinstance(data, dict) or data.get("version") != 1 or \
            not isinstance(data.get("rules"), list):
        return [], [finding("config.policy", os.path.basename(path),
                            "нужен JSON-объект version=1 с массивом rules",
                            "prohibition")]

    rules, errors, seen = [], [], set()
    for index, rule in enumerate(data["rules"], 1):
        subject = f"{os.path.basename(path)}:rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(finding("config.policy", subject, "правило должно быть объектом",
                                  "prohibition"))
            continue
        rule_id = rule.get("id")
        kind = rule.get("kind")
        expect = rule.get("expect")
        terms = rule.get("terms")
        reason = rule.get("reason")
        valid_id = isinstance(rule_id, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]+", rule_id))
        valid_terms = isinstance(terms, list) and bool(terms) and all(
            isinstance(term, str) and term.strip() for term in terms)
        if not valid_id or rule_id in seen or kind not in POLICY_KINDS or \
                expect not in POLICY_EXPECT or not valid_terms or \
                not isinstance(reason, str) or not reason.strip():
            errors.append(finding(
                "config.policy", subject,
                "обязательны уникальный id [A-Za-z0-9._-], kind remark|prohibition, "
                "expect present|absent, непустые terms и reason",
                "prohibition"))
            continue
        seen.add(rule_id)
        rules.append({
            "id": rule_id,
            "kind": kind,
            "expect": expect,
            "terms": [term.strip() for term in terms],
            "reason": reason.strip(),
        })
    return rules, errors


def check_owner_rules(doc: str, rules: list[dict], subject: str | None = None) -> list[dict]:
    """Сверить документ с явными решениями владельца, не разбирать прозу брифа."""
    try:
        with open(doc, encoding="utf-8", errors="replace") as source:
            text = source.read().casefold()
    except OSError as exc:
        return [finding("config.document", subject or os.path.basename(doc),
                        f"документ не прочитан: {exc}", "prohibition")]
    out = []
    for rule in rules:
        # ponytail: только буквальные термины; regex добавить, когда появится правило,
        # которое нельзя точно выразить фразой.
        present = [term for term in rule["terms"] if term.casefold() in text]
        bad = present if rule["expect"] == "absent" else [
            term for term in rule["terms"] if term.casefold() not in text]
        if not bad:
            continue
        action = "найдено запрещенное" if rule["expect"] == "absent" else "не найдено обязательное"
        out.append(finding(
            f"owner.{rule['id']}", subject or os.path.basename(doc),
            f"{action}: {', '.join(bad)}; причина: {rule['reason']}",
            rule["kind"]))
    return out


def load_suppressions(path: str | None) -> tuple[dict[str, dict], list[dict]]:
    """JSONL-глушитель. Невалидная строка сама блокирует приемку."""
    if not path or not os.path.exists(path):
        return {}, []
    found, errors = {}, []
    try:
        source = open(path, encoding="utf-8")
    except OSError as exc:
        return {}, [finding("config.suppressions", os.path.basename(path),
                            f"файл-глушитель не прочитан: {exc}", "prohibition")]
    with source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            subject = f"{os.path.basename(path)}:{line_no}"
            try:
                item = json.loads(line)
            except ValueError as exc:
                errors.append(finding("config.suppressions", subject,
                                      f"строка не JSON: {exc}", "prohibition"))
                continue
            fid = item.get("finding_id") if isinstance(item, dict) else None
            reason = item.get("reason") if isinstance(item, dict) else None
            if not isinstance(fid, str) or not re.fullmatch(r"[0-9a-f]{16}", fid) or \
                    not isinstance(reason, str) or not reason.strip():
                errors.append(finding("config.suppressions", subject,
                                      "обязательны finding_id (16 hex) и непустая reason",
                                      "prohibition"))
                continue
            found[fid] = {"finding_id": fid, "reason": reason.strip()}
    return found, errors


def apply_suppressions(findings: list[dict], suppressions: dict[str, dict]) \
        -> tuple[list[dict], list[dict]]:
    active, suppressed = [], []
    for item in findings:
        suppression = suppressions.get(item["id"])
        if not suppression:
            active.append(item)
        elif item["kind"] == "prohibition":
            active.append(item)
            active.append(finding(
                "config.suppressions", item["subject"],
                f"запрет {item['code']} нельзя подавить глушителем",
                "prohibition"))
        else:
            suppressed.append({**item, "suppression_reason": suppression["reason"]})
    return active, suppressed


# Порог доли непохожих на русские слов. Замер по 87 страницам живого OCR-кеша
# 03.08.2026: медиана 0,0%, худшая страница 3,3%. Порог 15% отделяет порченый
# лист от чистого с большим запасом и не дает ложных тревог на нашем материале.
GARBLED_MAX_SHARE = 15.0
# Доля слов, где кириллица и латиница смешаны ВНУТРИ одного слова. Именно так
# выглядит чужой OCR, запеченный в PDF: «Рссn)'6л~1кс». Общая доля латиницы для
# этого не годится — «Volvo XC60» и «KTM 390» в отчете оценщика законны, и по ней
# страница с марками машин давала 42% и ложную тревогу (замер 03.08.2026).
MIXED_MAX_SHARE = 8.0
# Доля без абсолютного минимума слепа к коротким страницам, а доля с минимумом
# ВЫБОРКИ слепа к ним же наоборот: прежнее условие «оценено >= 20 слов» пропускало
# порченый титульный лист, справку и приложение на пол-листа — там слов меньше
# двадцати, и гейт печатал «✓ OCR: чисто» на сплошном мусоре (замер 03.08.2026:
# 15 слов «Росскіской Федерациг… Двадцять… хвалаать» → rc=0).
# Правильный якорь — не размер выборки, а АБСОЛЮТНОЕ число испорченных слов:
# три ломаных слова на странице не бывают шумом ни при какой ее длине, а на
# длинной странице три слова из двухсот не дают превышения доли и тревоги не поднимут.
MIN_BAD_WORDS = 3
_CYR = re.compile(r"[А-Яа-яЁё]")
# Кириллица не русского алфавита. В русском процессуальном документе ее нет:
# і/ї/є (украинская), ў (белорусская), ђ/ћ/џ (сербская), ә/ғ/қ/ң/ө/ұ/һ (казахская).
# Распознаватель ставит их вместо похожих русских — слово остается «русским» на
# вид, доля латиницы не растет, а реквизит уже другой.
_FOREIGN_CYR = re.compile(r"[\u0400-\u04FF]")
_RU_ALPHABET = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
                         "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")
_LAT = re.compile(r"[A-Za-z]")
_VOWELS = set("аееиоуыэюя")


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
    """Пусто — страница в порядке. Иначе причина, по которой ее нельзя читать как текст.

    Две разные поломки, и одна метрика их не ловит:
      • латиница внутри кириллицы — чужой OCR, запеченный в PDF («Рссn)'6л~1кс»);
      • ломаная кириллица — слабый скан, гильош, печать поверх текста.
    Прежний гейт не проверял НИ ОДНУ из них: единственная проверка страницы была
    про таблицы, и флаг «бледная сетка» глушил даже ее.
    """
    mixed, words, mixed_n = mixed_script_share(text)
    if mixed_n >= MIN_BAD_WORDS and mixed > MIXED_MAX_SHARE:
        return (f"{mixed:.0f}% слов смешивают кириллицу с латиницей (порог "
                f"{MIXED_MAX_SHARE:.0f}%, испорчено {mixed_n} из {words}) — признак "
                "чужого OCR, запеченного в файл; страницу перераспознать")
    share, counted, bad = garbled_share(text)
    if bad >= MIN_BAD_WORDS and share > GARBLED_MAX_SHARE:
        return (f"{share:.0f}% слов непохожи на русские (порог {GARBLED_MAX_SHARE:.0f}%, "
                f"испорчено {bad} из {counted}) — распознавание испорчено, читать нельзя")
    # Третья поломка, которую доля не ловит: соседняя кириллица. Распознаватель
    # подменяет русскую «и» украинской «і», «е» — «є», «у» — белорусской «ў».
    # Слово остается кириллическим и «похожим на русское», доля чужих букв
    # остается низкой, а реквизит в нем уже другой. В русском процессуальном
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
            # страницы дает TABLE_STRUCTURE_LOST, и два инструмента проекта расходились
            # в вердикте, причем слабейший стоял на входе.
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
# котел сверки: число, выдуманное для доверителя, «подтверждалось» совпадением с
# числом из чужого дела и замечание молча снималось. Факты дела дают только
# материалы и карта; позиция — производная от них и тоже допустима.
PRACTICE_SOURCES = ("practice.md", "hunter_classic.md", "hunter_skeptic.md",
                    "hunter_tactical.md")


def is_practice_source(path: str) -> bool:
    return os.path.basename(path) in PRACTICE_SOURCES


def check_numbers(draft: str, sources: list[str], min_digits: int = 4) -> list[str]:
    """Числа черновика обязаны быть в источниках ФАКТОВ дела, а не в практике."""
    from crosscheck_numbers import numbers_of
    try:
        with open(draft, encoding="utf-8", errors="ignore") as stream:
            text = stream.read()
    except OSError as exc:
        return [f"черновик не прочитан: {exc}"]
    in_draft = numbers_of(text, min_digits)
    facts = [s for s in sources if not is_practice_source(s)]
    practice = [s for s in sources if is_practice_source(s)]
    unreadable = []
    in_src = None
    for s in facts:
        try:
            with open(s, encoding="utf-8", errors="ignore") as stream:
                n = numbers_of(stream.read(), min_digits)
        except OSError as exc:
            unreadable.append(f"источник {os.path.basename(s)} не прочитан: {exc}")
            continue
        in_src = n if in_src is None else (in_src | n)
    in_practice = None
    for s in practice:
        try:
            with open(s, encoding="utf-8", errors="ignore") as stream:
                n = numbers_of(stream.read(), min_digits)
        except OSError as exc:
            unreadable.append(f"источник {os.path.basename(s)} не прочитан: {exc}")
            continue
        in_practice = n if in_practice is None else (in_practice | n)
    # Разность именно по НАБОРУ чисел, не по счетчикам: у Counter «-» вычитает
    # частоты, и реквизит, названный в договоре трижды, а в источнике однажды,
    # оставался в остатке как «неподтвержденный». Найдено 20.08.2026 на договоре,
    # где ИНН и счета законно повторяются в преамбуле, приложениях и реквизитах.
    known = set(in_src or ())
    orphan = type(in_draft)({t: c for t, c in in_draft.items() if t not in known})
    # Даты и годы выкидываем: «01.02» и «2026» из даты документа законно отсутствуют
    # в карте дела, а в отчете они забивают собой реальные суммы. Даты — зона Кони.
    for tok in [t for t in orphan
                if re.fullmatch(r"\d{2}\.\d{2}", t) or re.fullmatch(r"(19|20)\d{2}", t)]:
        del orphan[tok]
    if not orphan:
        return unreadable
    out = list(unreadable)
    # Число, которого нет в фактах дела, но есть в практике, — отдельный и более
    # опасный случай: оно выглядит подтвержденным, хотя пришло из ЧУЖОГО дела.
    from_practice = sorted(t for t in orphan if in_practice and t in in_practice)
    if from_practice:
        out.append(f"{len(from_practice)} чисел взяты из практики, а не из фактов дела: "
                   f"{', '.join(from_practice[:15])}"
                   f"{'…' if len(from_practice) > 15 else ''} — в чужом судебном акте "
                   "такая сумма есть, у доверителя ее может не быть; сверить с материалами")
    rest = {t: c for t, c in orphan.items() if t not in set(from_practice)}
    if rest:
        items = ", ".join(sorted(rest)[:25])
        out.append(f"в черновике {sum(rest.values())} чисел, которых нет ни в одном источнике: "
                   f"{items}{'…' if len(rest) > 25 else ''} — подтвердить материалами дела "
                   f"или убрать (проверялось от {min_digits} значащих цифр)")
    return out


def check_requisites(path: str, bik: str | None) -> list[str]:
    """ИНН/ОГРН/СНИЛС/счет — контрольные суммы локально, без сети."""
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
    markdown_extract.purge_case: берем sha каждого материала дела и ищем его
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
    ctx = os.path.join(case, ".agent/context")
    drafts = [p for p in sorted(glob.glob(os.path.join(case, ".agent/drafts", "*.md")))
              if "_working" not in p and "_baselines" not in p]
    # Источником числа считается не только канон дела, но и рабочая папка:
    # выписка ЕГРЮЛ, расшифровка, бриф и промпт живут в .agent/context/_working/ и
    # приносят реквизиты (ГРН, ИНН, даты записи, адрес), которых в карте еще нет.
    # Пока их не индексировали, gate три прогона подряд кричал «число не найдено
    # в источниках» на реквизиты, взятые из свежей выписки (04.08.2026), и правил
    # заставляли документ, а не прибор. Черновики из _working/ по-прежнему не в
    # drafts — там сырье, а не выданный документ.
    sources = [p for p in (os.path.join(ctx, n) for n in
                           ("knowledge-map.md", "positions.md", "practice.md"))
               if os.path.isfile(p)]
    sources += sorted(glob.glob(os.path.join(ctx, "_working", "*.md")))
    return {"drafts": drafts, "sources": sources,
            "requisites": case_requisite_files(case)}


def print_rules(json_mode: bool = False) -> int:
    schema = {
        "policy": {
            "path": ".agent/context/_working/quality_gate.json",
            "format": {"version": 1, "rules": [{
                "id": "owner-rule-id",
                "kind": "remark|prohibition",
                "expect": "present|absent",
                "terms": ["буквальная фраза"],
                "reason": "решение владельца и дата",
            }]},
        },
        "suppressions": {
            "path": ".agent/context/_working/quality_gate.suppressions.jsonl",
            "format": {"finding_id": "16 hex", "reason": "почему неприменимо"},
            "limit": "подавляются только remark; prohibition не подавляется",
        },
    }
    if json_mode:
        print(json.dumps(schema, ensure_ascii=False, indent=2))
        return 0
    print("quality_gate.py rules")
    print("- OCR: страницы не должны быть пустыми, смешанными по алфавитам или ломаной кириллицей")
    print("- DOC: каждое значимое число черновика должно быть в источниках дела")
    print("- REQUISITES: ИНН, БИК и расчетные счета проходят контрольные суммы")
    print("- CASE: черновики проверяются против карты, позиции, практики и рабочего контекста")
    print("- OWNER: явные JSON-правила present/absent; проза брифа не разбирается")
    print("- SUPPRESSIONS: JSONL finding_id + обязательная reason; запреты не глушатся")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Механические проверки качества Фемиды")
    ap.add_argument("--ocr", help="папка OCR-кеша (page_*.png + page_*.txt)")
    ap.add_argument("--doc", help="черновик .md для сверки чисел")
    ap.add_argument("--against", nargs="+", default=[], help="источники чисел")
    ap.add_argument("--requisites", nargs="+", help="<sha>.requisites.json от роутера")
    ap.add_argument("--bik", help="БИК для проверки расчетного счета")
    ap.add_argument("--case", help="папка дела — прогнать все применимое")
    ap.add_argument("--policy", help="явный quality_gate.json (по умолчанию из дела)")
    ap.add_argument("--suppressions", help="явный JSONL-глушитель (по умолчанию из дела)")
    ap.add_argument("--subject", help=argparse.SUPPRESS)
    ap.add_argument("--intake-present", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true", help="машиночитаемый результат")
    ap.add_argument("--rules", action="store_true", help="напечатать правила гейта")
    ap.add_argument("--min-digits", type=int, default=4,
                    help="от скольких значащих цифр сверять числа (по умолчанию 4)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.rules:
        return print_rules(args.json)

    report: list[tuple[str, str, list[str]]] = []
    docs: list[str] = []
    paths = case_paths(args.case) if args.case else None
    if args.ocr:
        report.append(("ocr", f"OCR {args.ocr}", check_ocr(args.ocr)))
    if args.doc:
        doc_subject = args.subject or os.path.basename(args.doc)
        sources = list(args.against)
        if paths:
            sources += paths["sources"]
        sources = list(dict.fromkeys(sources))
        if not sources:
            print("--doc требует --against (источники чисел)", file=sys.stderr)
            return 2
        docs.append(args.doc)
        report.append(("doc.numbers", f"числа {doc_subject}",
                       check_numbers(args.doc, sources, args.min_digits)))
    if args.requisites:
        for requisite in args.requisites:
            report.append(("requisites", f"реквизиты {os.path.basename(requisite)}",
                           check_requisites(requisite, args.bik)))
    elif args.intake_present:
        report.append(("case.requisites", "реквизиты материалов дела",
                       ["в кеше роутера нет ни одного <sha>.requisites.json для "
                        "материалов дела — материалы не прогнаны через "
                        "markdown_extract.py, реквизиты НЕ проверены"]))
    if paths:
        if not args.doc and not paths["drafts"]:
            report.append(("case.drafts", f"дело {args.case}",
                           ["черновиков в .agent/drafts/ нет — сверять нечего"]))
        for d in ([] if args.doc else paths["drafts"]):
            docs.append(d)
            if paths["sources"]:
                report.append(("doc.numbers", f"числа {os.path.basename(d)}",
                               check_numbers(d, paths["sources"], args.min_digits)))
            else:
                report.append(("doc.numbers", f"числа {os.path.basename(d)}",
                               ["нет ни карты, ни позиции — числа сверять не с чем"]))
        # Реквизиты материалов дела. Ветка была написана, но не подключена:
        # ИНН/ОГРН/СНИЛС/БИК/счета доверителя проверяла модель на глаз либо никто.
        reqs = paths["requisites"]
        if reqs:
            found: list[str] = []
            for r in reqs:
                found += check_requisites(r, args.bik)
            report.append(("case.requisites",
                           f"реквизиты материалов дела ({len(reqs)} файлов)", found))
        elif any(os.path.isfile(path) for path in glob.glob(
                os.path.join(args.case, "00_intake", "**", "*"), recursive=True)):
            report.append(("case.requisites", f"реквизиты дела {args.case}",
                           ["в кеше роутера нет ни одного <sha>.requisites.json для "
                            "материалов дела — материалы не прогнаны через "
                            "markdown_extract.py, реквизиты НЕ проверены"]))
    if not report:
        print("нечего проверять: задать --ocr / --doc / --requisites / --case", file=sys.stderr)
        return 2

    raw = [finding(code, title, item) for code, title, items in report for item in items]
    selected_policy = args.policy or (policy_path(args.case) if args.case else None)
    rules, policy_errors = load_policy(
        selected_policy, required=bool(args.case or args.policy))
    for doc in docs:
        raw += check_owner_rules(doc, rules, args.subject if doc == args.doc else None)
    raw += policy_errors

    selected_suppressions = args.suppressions or (
        suppressions_path(args.case) if args.case else None)
    suppressions, suppression_errors = load_suppressions(selected_suppressions)
    raw += suppression_errors
    active, suppressed = apply_suppressions(raw, suppressions)

    if args.json:
        print(json.dumps({
            "ok": not active,
            "findings": active,
            "suppressed": suppressed,
            "policy": selected_policy,
            "suppressions": selected_suppressions,
        }, ensure_ascii=False, indent=2))
        return 1 if active else 0

    active_ids = {item["id"] for item in active}
    section_ids = set()
    for code, title, items in report:
        visible = []
        for message in items:
            item = finding(code, title, message)
            section_ids.add(item["id"])
            if item["id"] in active_ids:
                visible.append(item)
        if visible:
            print(f"\n⚠ {title}:")
            for item in visible:
                print(f"   • [{item['id']}] {item['message']}")
        else:
            print(f"✓ {title}: чисто")
    extras = [item for item in active if item["id"] not in section_ids]
    if extras:
        print("\n⚠ явные правила и конфигурация:")
        for item in extras:
            print(f"   • [{item['id']}] {item['message']}")
    for item in suppressed:
        print(f"↷ [{item['id']}] заглушено: {item['suppression_reason']}")
    print(f"\nитого замечаний: {len(active)}; заглушено: {len(suppressed)}")
    return 1 if active else 0


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

    # Реквизит, повторенный в договоре несколько раз, а в источнике названный
    # однажды: разность по счетчикам оставляла его в остатке (баг 20.08.2026).
    draft_rep = os.path.join(tmp, "draft_rep.md")
    src_rep = os.path.join(tmp, "rekvizity.md")
    open(draft_rep, "w", encoding="utf-8").write(
        "ИНН 503612266711 в преамбуле. ИНН 503612266711 в приложении. ИНН 503612266711 в реквизитах.")
    open(src_rep, "w", encoding="utf-8").write("ИНН 503612266711")
    repeated = check_numbers(draft_rep, [src_rep])

    req_ok = os.path.join(tmp, "a.requisites.json")
    json.dump({"inn": ["7707083893"]}, open(req_ok, "w", encoding="utf-8"))
    req_bad = os.path.join(tmp, "b.requisites.json")
    json.dump({"inn": ["7707083894"]}, open(req_bad, "w", encoding="utf-8"))

    empty_ocr = os.path.join(tmp, "ocr")
    os.makedirs(empty_ocr)

    # M06: решения владельца — JSON, замечания — стабильные id, глушитель — JSONL
    # с обязательной причиной. Запрет владельца глушителем не снимается.
    policy_doc = os.path.join(tmp, "policy-doc.md")
    open(policy_doc, "w", encoding="utf-8").write("Кредит пока не оспаривается.")
    policy = os.path.join(tmp, POLICY_NAME)
    json.dump({"version": 1, "rules": [
        {"id": "no-credit", "kind": "prohibition", "expect": "absent",
         "terms": ["кредит"], "reason": "решение владельца 01.09.2026"},
        {"id": "need-agreed", "kind": "remark", "expect": "present",
         "terms": ["согласовано"], "reason": "нужно подтвердить позицию"},
    ]}, open(policy, "w", encoding="utf-8"), ensure_ascii=False)
    owner_rules, owner_config = load_policy(policy)
    owner_findings = check_owner_rules(policy_doc, owner_rules)
    owner_prohibition = next(x for x in owner_findings if x["kind"] == "prohibition")
    owner_remark = next(x for x in owner_findings if x["kind"] == "remark")
    suppress = os.path.join(tmp, SUPPRESSIONS_NAME)
    open(suppress, "w", encoding="utf-8").write(json.dumps({
        "finding_id": owner_remark["id"], "reason": "неприменимо к этому виду документа"
    }, ensure_ascii=False) + "\n")
    loaded_suppressions, suppression_config = load_suppressions(suppress)
    active_owner, suppressed_owner = apply_suppressions(owner_findings, loaded_suppressions)
    blocked_suppression, _ = apply_suppressions(
        [owner_prohibition],
        {owner_prohibition["id"]: {"reason": "попытка снять запрет"}},
    )
    malformed_policy = os.path.join(tmp, "bad-policy.json")
    open(malformed_policy, "w", encoding="utf-8").write("{}")

    checks = [
        ("совпавшие числа замечаний не дают", clean == []),
        ("число из воздуха ловится", len(dirty) == 1 and "9999999" in dirty[0]),
        ("реквизит, повторенный в документе, подтвержденным и остается", repeated == []),
        # Практика — не источник фактов дела. Число из ЧУЖОГО судебного акта
        # раньше входило в общий котел и легализовало выдуманную сумму доверителя.
        ("число, найденное только в практике, замечание не снимает",
         any("из практики" in p for p in check_numbers(draft_pr, [map_pr, practice_pr]))),
        ("число, найденное в карте дела, замечаний не дает",
         check_numbers(draft_fact, [map_fact, practice_pr]) == []),
        ("practice.md опознан как практика", is_practice_source("/x/.agent/context/practice.md")),
        ("hunter-файл опознан как практика",
         is_practice_source("/x/.agent/context/hunter_classic.md")),
        ("карта дела практикой не считается",
         not is_practice_source("/x/.agent/context/knowledge-map.md")),
        ("позиция практикой не считается",
         not is_practice_source("/x/.agent/context/positions.md")),
        ("валидный ИНН проходит", check_requisites(req_ok, None) == []),
        ("битый ИНН ловится", len(check_requisites(req_bad, None)) == 1),
        ("пустая OCR-папка — замечание", len(check_ocr(empty_ocr)) == 1),
        ("нечитаемый requisites не роняет", len(check_requisites(tmp + "/нет.json", None)) == 1),
        ("policy JSON прочитан без прозы", len(owner_rules) == 2 and not owner_config),
        ("owner prohibition дает машиночитаемую находку",
         owner_prohibition["code"] == "owner.no-credit"),
        ("remark заглушен только с причиной",
         active_owner == [owner_prohibition] and len(suppressed_owner) == 1
         and not suppression_config),
        ("запрет владельца глушителем не снимается",
         any(x["code"] == "config.suppressions" for x in blocked_suppression)
         and owner_prohibition in blocked_suppression),
        ("битый policy красит конфигурацию", bool(load_policy(malformed_policy)[1])),
        ("режим дела без policy fail-closed",
         bool(load_policy(os.path.join(tmp, "missing-policy.json"), required=True)[1])),
        ("id находки стабилен", finding("x", "y", "z")["id"]
         == finding("x", "y", "z")["id"]),
        # Качество распознавания страницы. Прежний гейт не проверял его вовсе:
        # единственной проверкой листа была таблица, и «бледная сетка» глушила ее.
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
        ("одно ломаное слово на короткой странице тревоги не дает",
         page_text_bad("Настоящим подтверждается, что гражданин лично явился "
                       "на прием в отдел стрктр") == ""),
        ("два ломаных слова еще не приговор",
         page_text_bad("Настоящим подтверждается, что гражданин лично явился "
                       "на прием в отдел стрктр джквш") == ""),
        ("три ломаных слова на короткой странице ловятся",
         "непохожи" in page_text_bad("Настоящим гражданин явился отдел "
                                     "стрктр джквш првлн")),
        # Соседняя кириллица: слово выглядит русским, доля латиницы не растет,
        # но «і» вместо «и» меняет реквизит. 29 страниц из 132 в живом кеше.
        ("подмена русских букв украинскими ловится",
         "нерусские кириллические" in page_text_bad(
             "Паспорт гражданина Росскіской Федерациї выдан отделом внутренніх дел")),
        ("чистый русский текст чужой кириллицы не дает",
         foreign_cyrillic_letters("Российской Федерации") == {}),
        ("одна чужая буква тревоги не дает",
         page_text_bad("Паспорт гражданина Российской Федерациї выдан "
                       "отделом внутренних дел города Казани") == ""),
        ("короткая смесь алфавитов ловится",
         "смешивают" in page_text_bad("Пpиложение к Cпpaвке Bepxoвного Cyдa")),
        # Смешение алфавитов внутри слова бывает и законным: «SMS-сообщение»,
        # «IP-адрес», «PR-менеджер» — обычная лексика акта. Три таких слова на
        # длинной странице обязаны проходить, иначе гейт краснеет на живом тексте.
        ("законные IT-слова на длинной странице тревоги не дают",
         page_text_bad(" ".join(
             ["Суд установил что ответчик надлежащим образом извещен "
              "о времени и месте судебного заседания однако"] * 10)
             + " SMS-сообщение IP-адрес PR-менеджер") == ""),
        # Обратная сторона абсолютного порога: на длинной странице три ломаных слова
        # — обычный шум распознавания, а не поломка. Доля обязана их прощать, иначе
        # гейт начнет краснеть на каждом втором нормальном листе и его отключат.
        ("три ломаных слова на длинной странице тревоги не дают",
         page_text_bad(" ".join(
             ["Суд установил что ответчик надлежащим образом извещен "
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
