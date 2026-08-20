#!/usr/bin/env python3
"""verdict.py — вердикт Кони, привязанный к редакции. Этап 3 плана FINAL-PLAN-2026-08-18.

Раньше вердикт был словом в чате и строкой в `review_log.md`. Слово не привязано ни к
чему: одобрили редакцию r2, дописали абзац, собрали `.docx` — и в суд ушёл текст,
которого Кони не видел. Вердикт обязан содержать идентификатор документа, номер
редакции и SHA-256 самого `.md`.

Здесь же гейт humanizer-legal — вынесен из `DocBuilder.save()`. На собранном `.docx`
он срабатывал один раз и слишком поздно; прогон по `.md` идёт КАЖДЫЙ раунд, до того
как текст стал документом.

    --scan   ФАЙЛ.md                     проверка humanizer-legal (каждый раунд)
    --record ФАЙЛ.md --verdict "…" [-r N] записать вердикт с отпечатком редакции
    --check  ФАЙЛ.md                     можно ли собирать .docx из этой редакции
    --log    ФАЙЛ.md                     история вердиктов документа

Журнал — `.agent/drafts/_working/verdicts.jsonl` рядом с черновиком, append-only.

Выход: 0 — можно; 1 — нельзя (причина на stdout); 2 — вызов неверен.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_paths as cp  # noqa: E402
from create_docx import DocBuilder  # noqa: E402

READY = "ГОТОВ К ПОДАЧЕ"
SCAN = Path.home() / ".claude/skills/humanizer-legal/scripts/scan_legal.sh"
# Категории scan_legal.sh, при которых документ не выпускается. Источник один:
# DocBuilder.HUMANIZER_BLOCKERS, чтобы verdict и сборка .docx не расходились.
BLOCKING = DocBuilder.HUMANIZER_BLOCKERS


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def journal_path(md):
    """Журнал вердиктов лежит в рабочей папке черновиков — рядом с review_log.md."""
    md = Path(md).resolve()
    for parent in md.parents:
        if parent.name == "drafts" and parent.parent.name == cp.AGENT_DIR:
            return parent / cp.WORKING / "verdicts.jsonl"
    return md.parent / cp.WORKING / "verdicts.jsonl"


def scan(md):
    """Гейт humanizer-legal по `.md`. Возвращает список сработавших блокирующих категорий.

    Скрипта нет → `None` (fail-closed), не пустой список. Скилл живёт вне репозитория
    (`~/.claude/skills/`) — на чужой машине его может не быть, и пустой список
    неотличим от «прогнали и чисто»: анти-AI-гейт молча пропускал бы всё (этап 9).
    """
    if not SCAN.is_file():
        print(f"⛔ {SCAN} не найден — humanizer-legal не проверен, fail-closed", file=sys.stderr)
        return None
    try:
        p = subprocess.run(["bash", str(SCAN), str(md)], capture_output=True,
                           text=True, timeout=300, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"ВНИМАНИЕ: humanizer-legal не отработал ({e})", file=sys.stderr)
        return []
    out = (p.stdout or "") + (p.stderr or "")
    hits = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) > 0:
            if parts[1] in BLOCKING:
                hits.append(f"{parts[1]} ({parts[0]})")
    return hits


# Сумма, за ней (необязательно) круглые скобки, за ними слово валюты. Пропись
# обязана стоять МЕЖДУ числом и словом валюты: «1 000 (одна тысяча) рублей» —
# решение владельца 19.08.2026. Даты, номера статей/дел/страниц, проценты и
# ИНН — не деньги, этот шаблон их не задевает (требует слова «рубл*»/«коп*»).
# «р.» с точкой и евро/EUR — тоже валюты: «100 000 р.» и «100 000 евро»
# без прописи получали «ГОТОВ К ПОДАЧЕ» (проба 20.08.2026, круг 5). «р.»
# требует цифры перед собой и не задевает «стр.»/«г.» — те не идут за суммой.
# Сокращённый разряд — часть суммы, а не текст рядом: «100 тыс. руб.» и
# «1,5 млн руб.» проходили мимо проверки целиком, потому что между числом
# и словом валюты стояло слово (проба 20.08.2026).
_RAZRYAD = r"(?:\s*(?:тыс|млн|млрд|тысяч\w*|миллион\w*|миллиард\w*)\.?)?"
_NUM = r"\d[\d  .]*(?:[,.]\d{1,2})?" + _RAZRYAD
_CUR = r"(?:руб(?:\.|л\w*)?|\u20bd|₽|коп(?:\.|е\w*)?|доллар\w*|долл\.?|usd|\$|р\.|евро|eur|€)"
_CUR_END = r"(?![A-Za-zА-Яа-яЁё])"
_MONEY_AFTER_RE = re.compile(rf"(?<!\d)({_NUM})\s*(\([^()]*\))?\s*({_CUR}){_CUR_END}", re.I)
_MONEY_PREFIX_RE = re.compile(rf"(?<!\w)({_CUR})\s*({_NUM})\s*(\([^()]*\))?", re.I)
_MONEY_PARENS_RE = re.compile(rf"(?<!\d)({_NUM})\s*(\([^()]*{_CUR}[^()]*\))", re.I)
_MONEY_BEFORE_RE = re.compile(
    rf"[а-яё][а-яё\s-]{{2,90}}\(\s*({_NUM})\s*\)\s*({_CUR}){_CUR_END}", re.I)
_PROPIS_WORD_RE = re.compile(
    r"\b(?:ноль|один|одна|два|две|три|четыр|пят|шест|сем|восем|девят|десят|"
    r"сто|ста|сот|тысяч|миллион|миллиард|рубл|копе)\w*",
    re.I)
# Незаполненная вставка живёт не только в скобках. После запрета квадратных
# скобок в проекте составители перешли на подчёркивание, ёлочки и угловые
# скобки — форма сменилась, брак остался (проба 20.08.2026: документ с
# «Взыскать ______ рублей» получал «ГОТОВ К ПОДАЧЕ»).
_PLACEHOLDER_SLOVA = (r"указать|вставить|заполнить|фио|ф\.?\s*и\.?\s*о\.?|"
                      r"сумм[ауеы]?|дата|дату|адрес|инн|огрн|наименование|"
                      r"номер|реквизиты")
_PLACEHOLDER_RE = re.compile(
    # маркеры-слова
    r"\b(?:TODO|FIXME|XXXXX+|XXX+)\b"
    # вставка в скобках любой формы: круглых, квадратных, угловых, ёлочках
    "|[\\[(<\u00ab]\\s*(?:" + _PLACEHOLDER_SLOVA + ")[^)\\]>\u00bb]*[\\])>\u00bb]"
    # линейка подчёркиваний или точек — место для вписывания от руки
    r"|_{4,}|\.{6,}",
    re.I)


def _has_propis_before_number(text, start):
    """Разрешает форму «сто тысяч рублей (100 000 руб.)»."""
    prefix = text[:start].rstrip()
    if not prefix.endswith("("):
        return False
    return bool(_PROPIS_WORD_RE.search(prefix[-140:]))


def format_problems(md):
    """Скобки и наличие прописи — минимальный формат перед финальным вердиктом.

    Точное СОВПАДЕНИЕ прописи с числом — отдельная, более глубокая проверка
    scripts/document_guard.py (этап 9.8); здесь — что расшифровка вообще есть,
    без неё документ уже брак и до сверки дело можно не доводить.
    """
    text = Path(md).read_text(encoding="utf-8", errors="replace")
    problems = []
    square = len(re.findall(r"[\[\]]", text))
    if square:
        problems.append(f"квадратные скобки — {square} шт. (в практике проекта — "
                        "только круглые)")
    for m in _PLACEHOLDER_RE.finditer(text):
        problems.append(f"незаполненная вставка «{m.group(0)}»")
    before_nums = {m.group(1) for m in _MONEY_BEFORE_RE.finditer(text)}
    for m in _MONEY_AFTER_RE.finditer(text):
        num, parens, currency = m.groups()
        if num in before_nums:
            continue
        if _has_propis_before_number(text, m.start()):
            continue
        # «… рублей 00 копеек» — нулевые копейки цифрами после суммы, обиходная
        # форма, а не вторая сумма без прописи (проба круга 6, 20.08.2026).
        if re.fullmatch(r"0+", num.strip()) and \
                currency.lower().startswith("коп"):
            continue
        if not parens or not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{num} {currency}» без прописи в круглых скобках "
                            f"между числом и словом валюты")
    for currency, num, parens in _MONEY_PREFIX_RE.findall(text):
        # «рублей 00 копеек» — тот же хвост нулевых копеек в зеркальной форме.
        if re.fullmatch(r"0+", num.strip()) and \
                currency.lower().startswith(("руб", "коп")):
            continue
        if not parens or not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{currency}{num}» без прописи в круглых скобках")
    for num, parens in _MONEY_PARENS_RE.findall(text):
        if not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{num}» без прописи в круглых скобках")
    return problems


def record(md, verdict, round_no):
    if verdict == READY:
        problems = format_problems(md)
        if problems:
            print("⛔ ВЕРДИКТ «ГОТОВ К ПОДАЧЕ» НЕ ЗАПИСАН — брак формата:", file=sys.stderr)
            for p in problems:
                print(f"   · {p}", file=sys.stderr)
            return None
        # Анти-AI-гейт стоит НА МАРШРУТЕ вердикта, а не рядом отдельной
        # командой: иначе текст, забракованный --scan (HARD BANS), тут же
        # получает «ГОТОВ К ПОДАЧЕ» и допуск к сборке (проба круга 6,
        # 20.08.2026). Недоступный скрипт — fail-closed, как в --scan.
        blockers = scan(md)
        if blockers is None:
            print("⛔ ВЕРДИКТ «ГОТОВ К ПОДАЧЕ» НЕ ЗАПИСАН — humanizer-legal "
                  "недоступен (fail-closed). Поставить скилл humanizer-legal.",
                  file=sys.stderr)
            return None
        if blockers:
            print("⛔ ВЕРДИКТ «ГОТОВ К ПОДАЧЕ» НЕ ЗАПИСАН — анти-AI-гейт "
                  "забраковал текст:", file=sys.stderr)
            for b in blockers:
                print(f"   · {b}", file=sys.stderr)
            print(f"   Прогнать скилл humanizer-legal и повторить. "
                  f"Отчет: bash {SCAN} {md}", file=sys.stderr)
            return None
    md = Path(md)
    entry = {
        "document": md.name,
        "path": str(md),
        "round": round_no,
        "verdict": verdict,
        "sha256": sha(md),
        "at": time.strftime("%d.%m.%Y %H:%M:%S"),
    }
    jp = journal_path(md)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with open(jp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def history(md):
    jp = journal_path(md)
    if not jp.is_file():
        return []
    name = Path(md).name
    out = []
    for line in open(jp, encoding="utf-8"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("document") == name:
            out.append(e)
    return out


def check(md):
    """Причины, по которым из этой редакции нельзя собирать `.docx`. Пусто — можно."""
    md = Path(md)
    if not md.is_file():
        return [f"{md}: файла нет — собирать не из чего"]
    problems = format_problems(md)
    if problems:
        return [f"{md.name}: брак формата — {p}" for p in problems]
    now = sha(md)
    hist = history(md)
    if not hist:
        return [f"{md.name}: вердикта нет вовсе — документ не проходил проверку Кони"]
    ok = [e for e in hist if e.get("verdict") == READY and e.get("sha256") == now]
    if ok:
        # Гейт на маршруте СБОРКИ тоже: журнал append-only, но строку в него
        # можно дописать руками мимо record() — и тогда анти-AI-гейт, стоящий
        # в record(), обходится. Перепроверяем текст перед допуском к сборке
        # (проба круга 6, 20.08.2026). Скрипт недоступен — fail-closed.
        blockers = scan(md)
        if blockers is None:
            return [f"{md.name}: humanizer-legal недоступен — fail-closed, "
                    f"сборка .docx запрещена до установки скилла"]
        if blockers:
            return [f"{md.name}: анти-AI-гейт забраковал текст "
                    f"({', '.join(blockers)}) — сборка .docx запрещена, "
                    f"прогнать humanizer-legal и повторить раунд"]
        return []
    approved = [e for e in hist if e.get("verdict") == READY]
    if approved:
        last = approved[-1]
        return [f"{md.name}: вердикт «{READY}» есть, но выдан на ДРУГУЮ редакцию "
                f"(r{last.get('round')}, отпечаток {last.get('sha256', '')[:12]}…, "
                f"сейчас {now[:12]}…) — текст правился после одобрения, нужен новый раунд"]
    last = hist[-1]
    return [f"{md.name}: последний вердикт «{last.get('verdict')}» (r{last.get('round')}) — "
            f"не «{READY}»"]


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="verdict-selftest-") as tmp:
        d = Path(tmp) / "cases" / "ivanov-ivan" / "delo-2026" / cp.AGENT_DIR / "drafts"
        d.mkdir(parents=True)
        md = d / "isk_v1.md"
        md.write_text("# Иск\n\nТекст первой редакции.\n", encoding="utf-8")

        # resolve с обеих сторон: на macOS /var — симлинк на /private/var
        assert journal_path(md) == (d / cp.WORKING / "verdicts.jsonl").resolve(), \
            journal_path(md)
        assert check(md), "документ без вердикта признан готовым к сборке"
        assert "вердикта нет вовсе" in check(md)[0]

        record(md, "ТРЕБУЕТ ПРАВОК", 1)
        assert check(md), "вердикт ТРЕБУЕТ ПРАВОК пропустил сборку"
        assert "не «ГОТОВ К ПОДАЧЕ»" in check(md)[0]

        record(md, READY, 2)
        assert not check(md), f"одобренная редакция не пропущена: {check(md)}"

        # Ровно тот случай, ради которого всё это: текст правится ПОСЛЕ одобрения
        md.write_text("# Иск\n\nТекст первой редакции.\n\nДописанный абзац.\n", encoding="utf-8")
        problems = check(md)
        assert problems, "изменённый после одобрения текст пропущен к сборке"
        assert "ДРУГУЮ редакцию" in problems[0], problems

        # Новый раунд по новой редакции снова открывает сборку
        record(md, READY, 3)
        assert not check(md), "новый вердикт на новую редакцию не пропустил"

        # Возврат к прежнему тексту не воскрешает прежний вердикт по ошибке:
        # отпечаток совпадает — значит это буквально та самая одобренная редакция
        md.write_text("# Иск\n\nТекст первой редакции.\n", encoding="utf-8")
        assert not check(md), "возврат к ранее одобренному тексту заблокирован зря"

        assert len(history(md)) == 3, history(md)   # r1 правки, r2 и r3 готов
        assert check(d / "net.md"), "несуществующий файл признан готовым"

        # Гейт humanizer — fail-closed: нет скрипта, значит СТОП, а не тихий пропуск.
        global SCAN
        saved, SCAN = SCAN, Path(tmp) / "net-skripta.sh"
        try:
            assert scan(md) is None, "отсутствие скрипта не дало fail-closed сигнала"
        finally:
            SCAN = saved

        # Формат перед финальным вердиктом: скобки и наличие прописи.
        chisto = d / "chisto.md"
        chisto.write_text("# Ходатайство\n\nПрошу суд отложить заседание "
                          "(ст. 158 АПК РФ).\n", encoding="utf-8")
        assert format_problems(chisto) == [], format_problems(chisto)
        assert record(chisto, READY, 1) is not None, "чистый документ не записан"

        skobki = d / "skobki.md"
        skobki.write_text("# Ходатайство\n\nПрошу суд [указать дату] отложить.\n",
                          encoding="utf-8")
        assert format_problems(skobki), "квадратные скобки не пойманы"
        assert record(skobki, READY, 1) is None, "брак со скобками получил вердикт"
        assert record(skobki, "ТРЕБУЕТ ПРАВОК", 1) is not None, \
            "рабочий вердикт заблокирован форматным гейтом"

        summa = d / "summa.md"
        summa.write_text("# Заявление\n\nВзыскать 100 000 рублей неустойки "
                         "(ст. 330 ГК РФ).\n", encoding="utf-8")
        assert format_problems(summa), "сумма без прописи не поймана"
        assert record(summa, READY, 1) is None, "сумма без прописи получила вердикт"

        propisano = d / "propisano.md"
        propisano.write_text("# Заявление\n\nВзыскать 100 000 (сто тысяч) рублей "
                             "неустойки (ст. 330 ГК РФ).\n", encoding="utf-8")
        assert format_problems(propisano) == [], format_problems(propisano)

        # «р.» с точкой и евро/EUR — тоже валюты (проба 20.08.2026, круг 5).
        rtochka = d / "rtochka.md"
        rtochka.write_text("# Заявление\n\nВзыскать 100 000 р. неустойки по договору.\n",
                           encoding="utf-8")
        assert format_problems(rtochka), "сумма с «р.» без прописи не поймана"
        evro = d / "evro.md"
        evro.write_text("# Заявление\n\nВзыскать 100 000 евро по контракту.\n",
                        encoding="utf-8")
        assert format_problems(evro), "сумма в евро без прописи не поймана"
        chistie_valyuty = d / "chistie-valyuty.md"
        chistie_valyuty.write_text("# Заявление\n\nВзыскать 100 000 (сто тысяч) р. и "
                                   "2 000 (две тысячи) евро по контракту.\n",
                                   encoding="utf-8")
        assert format_problems(chistie_valyuty) == [], format_problems(chistie_valyuty)
    print("selftest: журнал рядом с черновиком, отказ без вердикта, отказ на ТРЕБУЕТ ПРАВОК, "
          "детект правки после одобрения, новый раунд, возврат к одобренному тексту, "
          "humanizer fail-closed, формат перед финальным вердиктом, "
          "валюты «р.» и евро — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Вердикт Кони, привязанный к редакции.")
    ap.add_argument("md", nargs="?", help="черновик .md")
    ap.add_argument("--scan", action="store_true", help="гейт humanizer-legal по .md")
    ap.add_argument("--record", action="store_true", help="записать вердикт")
    ap.add_argument("--verdict", help="текст вердикта (с --record)")
    ap.add_argument("-r", "--round", type=int, default=1, help="номер раунда (с --record)")
    ap.add_argument("--check", action="store_true", help="можно ли собирать .docx")
    ap.add_argument("--log", action="store_true", help="история вердиктов")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.md:
        ap.print_help()
        return 2

    if a.scan:
        blockers = scan(a.md)
        if blockers is None:
            print(f"❌ humanizer-legal: скрипт скилла не найден ({SCAN}) — проверка "
                  "не выполнена, fail-closed. Поставить скилл humanizer-legal.")
            return 1
        if blockers:
            print(f"❌ humanizer-legal: сработали блокирующие категории — {', '.join(blockers)}")
            print(f"   Прогнать скилл humanizer-legal по тексту и повторить.")
            print(f"   Полный отчет: bash {SCAN} {a.md}")
            return 1
        print("✓ humanizer-legal: следов автогенерации и незаполненных плейсхолдеров нет")
        return 0
    if a.record:
        if not a.verdict:
            print("--record требует --verdict", file=sys.stderr)
            return 2
        e = record(a.md, a.verdict, a.round)
        if e is None:
            return 1
        print(f"вердикт записан: {e['document']} r{e['round']} «{e['verdict']}» "
              f"отпечаток {e['sha256'][:12]}…")
        return 0
    if a.log:
        h = history(a.md)
        if not h:
            print("вердиктов нет")
            return 1
        for e in h:
            print(f"  {e['at']}  r{e['round']}  {e['sha256'][:12]}…  {e['verdict']}")
        return 0
    if a.check:
        problems = check(a.md)
        if problems:
            print("⛔ СБОРКА .docx ЗАПРЕЩЕНА")
            for p in problems:
                print("  · " + p)
            return 1
        print(f"✓ редакция одобрена Кони — сборка .docx разрешена")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
