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
# Сумма в круглых скобках с валютой сразу за ними: «(100 000) рублей». Паттерн
# намеренно БЕЗ префикса слов — прежняя форма требовала перед скобками минимум
# три буквы, и вторая сумма в «… (100 000) рублей и (5 000) руб.» не матчилась
# вовсе: после съеденного первым матчем «рублей» оставалась одна буква «и».
# Пропись судится отдельно по последнему слову перед скобками (этап 9.20,
# круг 8 — вторая половина оси дыры «сумма в скобках без прописи»).
_MONEY_BEFORE_RE = re.compile(
    rf"\(\s*({_NUM})\s*\)\s*({_CUR}){_CUR_END}", re.I)
_PROPIS_WORD_RE = re.compile(
    r"\b(?:ноль|один|одна|два|две|три|четыр|пят|шест|сем|восем|девят|десят|"
    r"сто|ста|сот|тысяч|миллион|миллиард|рубл|копе)\w*",
    re.I)
# Одно числительное ЦЕЛИКОМ (для проверки «последнее слово перед скобками»):
# шире _PROPIS_WORD_RE — «тридцать», «сорок», «пятьдесят» тоже пропись, иначе
# верная форма «сто тридцать (130) рублей» браковалась бы (этап 9.20, круг 8).
_NUMERAL_WORD_RE = re.compile(
    r"(?:ноль?|нул|оди?н|одна|одной|дв[ауе]|три|четыр|пят|шест|сем|восем|восьм|"
    r"девят|десят|[а-яё]*надцат|[а-яё]*дцат|[а-яё]*десят|сорок|"
    r"ст[аоие]|сот|тысяч|миллион|миллиард|рубл|копе)\w*",
    re.I)
# Незаполненная вставка живёт не только в скобках. После запрета квадратных
# скобок в проекте составители перешли на подчёркивание, ёлочки и угловые
# скобки — форма сменилась, брак остался (проба 20.08.2026: документ с
# «Взыскать ______ рублей» получал «ГОТОВ К ПОДАЧЕ»).
_PLACEHOLDER_RE = re.compile(
    # маркеры-слова
    r"\b(?:TODO|FIXME|XXXXX+|XXX+)\b"
    # линейка подчёркиваний или точек — место для вписывания от руки
    r"|_{4,}|\.{6,}",
    re.I)
# Вставка в скобках любой формы: круглых, квадратных, угловых, ёлочках.
# Содержимое разбирается отдельно (_empty_slot): круглая скобка в иске —
# основная форма пояснения (реквизиты сторон, адреса, номера дел, ссылки на
# нормы), и считать её браком по одному слову-маркеру нельзя (этап 9.19,
# круг 7: «(ИНН 7712345678, ОГРН 1027700132195)» в шапке иска против
# организации не давало выдать вердикт вовсе).
_BRACKET_SPAN_RE = re.compile(r"[\[(<«]\s*([^)\]>»]{1,80}?)\s*[\])>»]")
_PLACEHOLDER_KEY_RE = re.compile(
    r"указать|укажите|вставить|вписать|заполнить|прописать|подставить|фио|"
    r"сумм[ауеы]?|дата|дату|адрес|инн|огрн|"
    r"наименование|номер|реквизиты|паспорт",
    re.I)
# Слова, из которых состоит подсказка «что вписать» — и ничего больше.
# Связки («и», «от») и слова-наполнители («сюда», «нужное») дыру не
# заполняют: «(вставить сюда наименование суда)» — та же дыра, что
# «(наименование суда)» (этап 9.20, круг 8).
_PLACEHOLDER_VOCAB = frozenset(
    "указать укажите вставить вписать заполнить прописать подставить фио ф и о "
    "сумма сумму суммы дата дату даты "
    "адрес инн огрн наименование номер реквизиты паспорт суда дела истца "
    "ответчика стороны организации заявителя подписанта г "
    "и или а от по в на со без для при сюда нужное нужную необходимое данные "
    "значение текст".split())


def _empty_slot(content):
    """Содержимое скобок — настоящая незаполненная вставка?

    Брак — скобка, где вместо значения стоит УКАЗАНИЕ, что вписать:
    «(указать дату)», «(ФИО)», «(сумма)», «(наименование суда)». Не брак —
    вписанное значение: «(ИНН 7712345678, ОГРН 1027700132195)»,
    «(адрес: г. Казань, ул. Баумана, д. 5)», «(номер дела А65-12345/2026)»,
    «(ст. 309 ГК РФ)». Отличие: вставка состоит ТОЛЬКО из слов-подсказок —
    короткая, без цифр и двоеточия; как только в скобках появилось значение
    (цифра реквизита, двоеточие с адресом), скобка перестаёт быть дырой.
    Токены без единой буквы («…», «—», «...») словами не считаются: иначе
    «(указать …)» проходило вердикт, и документ из одних таких скобок уходил
    в суд (этап 9.20, круг 8).
    """
    # Точки не учитываются: «(Ф.И.О.)» — та же дыра, что «(ФИО)».
    if not _PLACEHOLDER_KEY_RE.search(content.lower().replace(".", "")):
        return False
    if re.search(r"\d", content) or ":" in content:
        return False          # значение вписано — это реквизит, а не дыра
    words = [w for w in content.split()
             if re.search(r"[A-Za-zА-Яа-яЁё]", w)]
    if not 1 <= len(words) <= 6:
        return False
    return all(w.lower().replace(".", "") in _PLACEHOLDER_VOCAB for w in words)


# Опознанная пропись целиком: число + круглые скобки с кириллической
# расшифровкой (валюта может жить внутри скобок — форма прибора calc395
# «38 998,29 (тридцать восемь тысяч … рублей 29 копеек)»). Всё внутри такого
# блока — часть прописи, второй раз не судится: иначе правило ищет пару
# «валюта + число» ВНУТРИ уже опознанной прописи и печатает несуществующую
# сумму «рублей29», по которой юрист не поймет, что править (этап 9.20,
# круг 8).
_PROPIS_BLOCK_RE = re.compile(rf"(?<!\d){_NUM}\s*\([^()]*[а-яёА-ЯЁ][^()]*\)")


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
    for m in _BRACKET_SPAN_RE.finditer(text):
        if _empty_slot(m.group(1)):
            problems.append(f"незаполненная вставка «{m.group(0)}»")
    covered = [m.span() for m in _PROPIS_BLOCK_RE.finditer(text)]

    def in_covered(pos):
        return any(a <= pos < b for a, b in covered)

    before_nums = set()
    for m in _MONEY_BEFORE_RE.finditer(text):
        # Пропись ПЕРЕД числом — «двести тысяч (200 000) рублей» — верная
        # форма, только зеркальная. Но слово перед скобками обязано быть
        # ЧИСЛИТЕЛЬНЫМ: «взыскать (100 000) рублей» — сумма в круглых
        # скобках вообще без прописи, и оба прибора её пропускали
        # (этап 9.20, круг 8). Судит ПОСЛЕДНЕЕ слово перед скобками: проверка
        # вхождением по всему префиксу давала обход — в «сто тысяч (100 000)
        # рублей и (5 000) руб.» вторая сумма проходила за счёт слова «рублей»
        # от первой (та же дыра, круг 8, вторая половина оси).
        if in_covered(m.start()):
            continue          # внутри уже опознанной прописи — не судим второй раз
        tail = re.findall(r"[A-Za-zА-Яа-яЁё-]+", text[:m.start()])
        if tail and _NUMERAL_WORD_RE.fullmatch(tail[-1]):
            before_nums.add(m.group(1))
        else:
            problems.append(f"сумма «({m.group(1)}) {m.group(2)}» в круглых скобках "
                            f"без прописи перед ними — пропись обязана стоять перед "
                            f"скобками: «сто тысяч (100 000) рублей»")
    for m in _MONEY_AFTER_RE.finditer(text):
        num, parens, currency = m.groups()
        if in_covered(m.start()):
            continue          # внутри уже опознанной прописи — не судим второй раз
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
    for m in _MONEY_PREFIX_RE.finditer(text):
        currency, num, parens = m.groups()
        if in_covered(m.start()):
            continue          # «рублей 29» внутри прописи — не вторая сумма
        # «рублей 00 копеек» — тот же хвост нулевых копеек в зеркальной форме.
        if re.fullmatch(r"0+", num.strip()) and \
                currency.lower().startswith(("руб", "коп")):
            continue
        if not parens or not re.search(r"[а-яёА-ЯЁ]", parens):
            problems.append(f"сумма «{currency}{num}» без прописи в круглых скобках")
    for m in _MONEY_PARENS_RE.finditer(text):
        num, parens = m.groups()
        if in_covered(m.start()):
            continue
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
    # Судит ПОСЛЕДНИЙ вердикт по этой редакции, а не любой одобрительный в
    # истории: иначе «ТРЕБУЕТ ПРАВОК», записанный ПОСЛЕ «ГОТОВ К ПОДАЧЕ» на ту
    # же редакцию, ничего не отзывает — найденная Кони ошибка не останавливала
    # выдачу (этап 9.19, круг 7). Возврат к тексту ранее одобренной редакции
    # по-прежнему собирается: последняя запись по её отпечатку — одобрение.
    same = [e for e in hist if e.get("sha256") == now]
    if same:
        if same[-1].get("verdict") == READY:
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
        v = same[-1]
        if any(e.get("verdict") == READY for e in same):
            return [f"{md.name}: одобрение этой редакции ОТОЗВАНО — последний вердикт "
                    f"по ней «{v.get('verdict')}» (r{v.get('round')}), не «{READY}»; "
                    f"закрыть замечания Кони и провести новый раунд"]
        return [f"{md.name}: последний вердикт по этой редакции «{v.get('verdict')}» "
                f"(r{v.get('round')}) — не «{READY}»"]
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

        # Зеркальная верная форма — пропись ПЕРЕД числом, и суммы с десятками
        # («сто тридцать»): судится последнее слово перед скобками (круг 8).
        pered = d / "pered.md"
        pered.write_text("# Заявление\n\nВзыскать двести тысяч (200 000) рублей "
                         "долга и сто тридцать (130) рублей расходов.\n",
                         encoding="utf-8")
        assert format_problems(pered) == [], format_problems(pered)
        # Обратная ось: слово «рублей» от ПЕРВОЙ суммы не покрывает вторую —
        # «и (5 000) руб.» без своей прописи остаётся браком.
        vtorym = d / "vtoraya-summa.md"
        vtorym.write_text("# Заявление\n\nВзыскать сто тысяч (100 000) рублей "
                          "и (5 000) руб. процентов.\n", encoding="utf-8")
        assert format_problems(vtorym), \
            "вторая сумма в скобках без прописи прошла за счет чужой прописи"

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

        # Круглые скобки с реквизитами — НЕ пустая вставка (этап 9.19, круг 7:
        # иск против организации не мог получить вердикт вовсе).
        rekv = d / "rekvizity.md"
        rekv.write_text("# ИСКОВОЕ ЗАЯВЛЕНИЕ\n\nОтветчик: ООО «Ромашка» "
                        "(ИНН 7712345678, ОГРН 1027700132195), проживает "
                        "(адрес: г. Казань, ул. Баумана, д. 5), спор рассмотрен "
                        "(номер дела А65-12345/2026) по норме (ст. 309 ГК РФ).\n",
                        encoding="utf-8")
        assert format_problems(rekv) == [], format_problems(rekv)
        # Обратная ось: настоящие дыры ловятся во всех формах.
        dyra = d / "dyra.md"
        dyra.write_text("# Заявление\n\nДоговор заключён (указать дату). "
                        "Истец: (ФИО) обратился. В (наименование суда) подано. "
                        "Подписант: (Ф.И.О.).\n", encoding="utf-8")
        assert len(format_problems(dyra)) == 4, format_problems(dyra)

        # Вердикт отзывается: «ТРЕБУЕТ ПРАВОК» на ту же редакцию закрывает
        # сборку; повторное одобрение той же редакции — открывает (9.19).
        otzyv = d / "otzyv.md"
        otzyv.write_text("# Заявление\n\nТекст без брака и вставок.\n",
                         encoding="utf-8")
        assert record(otzyv, READY, 1) is not None
        assert not check(otzyv), f"одобренная редакция не пропущена: {check(otzyv)}"
        record(otzyv, "ТРЕБУЕТ ПРАВОК", 2)
        assert check(otzyv), "«ТРЕБУЕТ ПРАВОК» не отозвал одобрение той же редакции"
        record(otzyv, READY, 3)
        assert not check(otzyv), "повторное одобрение той же редакции не пропустило"
    print("selftest: журнал рядом с черновиком, отказ без вердикта, отказ на ТРЕБУЕТ ПРАВОК, "
          "детект правки после одобрения, новый раунд, возврат к одобренному тексту, "
          "humanizer fail-closed, формат перед финальным вердиктом, "
          "валюты «р.» и евро, скобки-реквизиты не дыры, отзыв вердикта — ок")
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
