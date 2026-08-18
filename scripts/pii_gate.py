#!/usr/bin/env python3
"""pii_gate.py — обезличивание текста перед выходом за периметр дела.

Зачем. Этап 6/7 отправляет фрагменты дела наружу (внешний поиск практики,
LLM-вызов без доступа к материалам дела) — и туда не должны уйти ФИО, ИНН,
ОГРН, номер дела, паспорт, адрес доверителя. Ручная проверка «на глаз» перед
каждым вызовом не масштабируется и рано или поздно пропустит реквизит.

Fail-closed — ядро контракта, не деталь. Если прибор ничего не нашёл и молча
вернул 0, вызывающий код решит, что текст уже чист, и отправит его как есть —
а на деле это чаще значит «регулярка не сработала», чем «в тексте правда нет
персональных данных» (юридический текст почти всегда содержит хотя бы одно
ФИО или реквизит). Поэтому пустая карта = код 1 и файл на выходе НЕ создаётся:
вызывающий обязан заметить отказ, а не проглотить пустой успех.

Токены — не квадратные скобки. Квадратные скобки в файлах дела запрещены
отдельным правилом (их не пишут в российских процессуальных документах и
держит document_guard.py) — здесь тот же символ был бы и не по стилю, и
конфликтовал бы с реальными купюрами «[...]» внутри цитат. Формат токена —
`{{PII:КАТЕГОРИЯ:N}}`: фигурные скобки в юридическом тексте не встречаются,
N — порядковый номер вхождения (не значения — одинаковое ФИО, встреченное
дважды, получает два разных токена), что делает обратную подстановку через
буквальный `str.replace` однозначной без риска зацепить чужой фрагмент.

Разбор регулярками, не NER-моделью: без сети и без зависимостей, детерминирован,
и для строго форматированных юридических реквизитов (ИНН, ОГРН, паспорт, номер
дела) регулярка надёжнее вероятностной модели. ФИО — единственная категория без
жёсткого формата, поэтому она поймана на надёжном морфологическом признаке:
отчество оканчивается на -ович/-евич/-овна/-евна/-ична почти без исключений в
русском языке, что режет ложные срабатывания на случайных парах заглавных слов
(«Истец Кузнецова» само по себе не пройдёт — «Кузнецова» не отчество).
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Заглавное слово + заглавное слово + заглавное слово-отчество. Отчество как якорь
# отсекает случайные пары заглавных слов в начале предложения («Истец Кузнецова...»
# без отчества дальше не считается ФИО) при этом отчество почти всегда однозначно
# опознаётся суффиксом.
FIO_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+"
    r"[А-ЯЁ][а-яё]*(?:ович|евич|ьевич|овна|евна|ьевна|инична)\b"
)
# Фамилия + инициалы: «Иванов И.И.» — вторая частая форма в судебных документах.
FIO_INITIALS_RE = re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")
# Значение всегда рядом с меткой — это отсекает произвольные 10/12/13-значные
# числа (суммы, счета) от настоящего реквизита; без метки регулярка на голых
# числах утонула бы в ложных находках.
INN_RE = re.compile(r"ИНН[:\s№]{0,5}(\d{10}|\d{12})\b")
OGRN_RE = re.compile(r"ОГРН(?:ИП)?[:\s№]{0,5}(\d{13}|\d{15})\b")
PASSPORT_RE = re.compile(r"[Пп]аспорт[:\s№]{0,5}(\d{4}\s?\d{6})\b")
CASE_NO_RE = re.compile(r"[Дд]ело\s*№\s*([А-ЯA-Z]\d{1,3}-\d{1,6}/\d{4})")
# Адрес не имеет строгого формата — берём весь хвост строки после метки: это
# избыточно (заденет и соседний текст на той же строке), но избыточность здесь
# безопаснее недомаскировки, а обратимость от ширины захвата не зависит.
ADDRESS_RE = re.compile(r"адрес:\s*([^\n]+)")

# Порядок категорий — приоритет при перекрытии совпадений (см. _find_matches).
CATEGORIES = (
    ("ФИО", (FIO_RE, FIO_INITIALS_RE)),
    ("ИНН", (INN_RE,)),
    ("ОГРН", (OGRN_RE,)),
    ("ДЕЛО", (CASE_NO_RE,)),
    ("ПАСПОРТ", (PASSPORT_RE,)),
    ("АДРЕС", (ADDRESS_RE,)),
)


def _find_matches(text: str) -> list[tuple[int, int, str]]:
    """Непересекающиеся находки (start, end, категория), отсортированные по тексту.

    Regex с группой маскирует только группу (значение), без вводной метки
    («ИНН» остаётся читаемым, число — нет). При перекрытии дольше совпадение
    выигрывает: так «Дело № А65-.../2026» не разваливается на кусок ФИО-подобной
    находки внутри.
    """
    raw = []
    for cat, patterns in CATEGORIES:
        for pat in patterns:
            for m in pat.finditer(text):
                span = m.span(1) if m.lastindex else m.span(0)
                raw.append((span[0], span[1], cat))
    raw.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    claimed: list[tuple[int, int]] = []
    chosen = []
    for start, end, cat in raw:
        if any(start < e and end > s for s, e in claimed):
            continue
        claimed.append((start, end))
        chosen.append((start, end, cat))
    chosen.sort(key=lambda t: t[0])
    return chosen


def mask_text(text: str) -> tuple[str | None, dict[str, str]]:
    """Маскированный текст + обратимая карта. (None, {}) — ничего не найдено."""
    matches = _find_matches(text)
    if not matches:
        return None, {}
    counters: dict[str, int] = {}
    mapping: dict[str, str] = {}
    out = []
    pos = 0
    for start, end, cat in matches:
        out.append(text[pos:start])
        counters[cat] = counters.get(cat, 0) + 1
        token = f"{{{{PII:{cat}:{counters[cat]}}}}}"
        mapping[token] = text[start:end]
        out.append(token)
        pos = end
    out.append(text[pos:])
    return "".join(out), mapping


def unmask_text(text: str, mapping: dict[str, str]) -> str:
    """Буквальная подстановка токен → исходный фрагмент. Токены не пересекаются
    по построению (нумерация по вхождению), порядок замен не важен."""
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text


def _read(path: str) -> str:
    # newline="" — без универсальной трансляции переводов строк, иначе восстановление
    # не будет побайтовым на файле с \r\n. Path.read_text(newline=...) появился
    # только в 3.13 — на боевом 3.11 используем open() напрямую.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def cmd_mask(src: str, out: str, map_path: str) -> int:
    text = _read(src)
    masked, mapping = mask_text(text)
    if masked is None:
        print("pii_gate: реквизитов не найдено — fail-closed, файл не создаётся "
              "(пустая карта чаще значит «регулярка не сработала», чем «текст чист»)",
              file=sys.stderr)
        return 1
    _write(out, masked)
    Path(map_path).write_text(json.dumps(mapping, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"pii_gate: обезличено находок: {len(mapping)} → {out}")
    return 0


def cmd_unmask(src: str, map_path: str, out: str) -> int:
    mapping = json.loads(Path(map_path).read_text(encoding="utf-8"))
    text = _read(src)
    _write(out, unmask_text(text, mapping))
    print(f"pii_gate: восстановлено → {out}")
    return 0


def selftest() -> int:
    original = (
        "Истец Кузнецова Мария Петровна, ИНН 771234567890, обратилась в суд.\n"
        "Дело № А65-12345/2026. Ответчик ООО «Ромашка», ОГРН 1157746123456.\n"
        "Паспорт 9203 456789, адрес: г. Казань, ул. Баумана, д. 5, кв. 12.\n"
    )
    masked, mapping = mask_text(original)
    assert masked is not None and mapping, "боевой текст обязан дать находки"
    for leak in ("Кузнецова", "771234567890", "А65-12345/2026",
                 "1157746123456", "9203 456789", "Баумана"):
        assert leak not in masked, f"утечка в маске: {leak!r}"
    assert unmask_text(masked, mapping) == original, "восстановление не побайтовое"

    clean = "Обзор практики по неустойке. Норм права достаточно.\n"
    masked_clean, mapping_clean = mask_text(clean)
    assert masked_clean is None and not mapping_clean, \
        "чистый текст обязан давать fail-closed (None), а не пустой успех"

    print("selftest: маскирует боевой текст без утечек, восстанавливает побайтово, "
          "fail-closed на чистом тексте — ок")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Обезличивание текста перед выходом за периметр дела")
    ap.add_argument("--mask", metavar="ВХОД")
    ap.add_argument("--unmask", metavar="ВХОД")
    ap.add_argument("--out", metavar="ВЫХОД")
    ap.add_argument("--map", metavar="КАРТА.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if a.mask:
        if not a.out or not a.map:
            ap.error("--mask требует --out и --map")
        return cmd_mask(a.mask, a.out, a.map)

    if a.unmask:
        if not a.out or not a.map:
            ap.error("--unmask требует --out и --map")
        return cmd_unmask(a.unmask, a.map, a.out)

    ap.error("нужен --mask, --unmask или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
