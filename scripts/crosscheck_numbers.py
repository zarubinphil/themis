#!/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
"""crosscheck_numbers.py — кросс-сверка ЧИСЕЛ между двумя движками OCR по одной странице.

Идея: движки ошибаются по-разному (Vision калечит структуру, Unlimited — кириллицу),
но ЧИСЛА оба читают сильно. Число, которое видит только один движок, — кандидат на
ошибку распознавания; расхождение по критичной странице (расчет цены иска) — стоп-сигнал
для ручной сверки с PNG. Стоимость — $0, чистая арифметика.

Использование:
  crosscheck_numbers.py vision.txt unlimited.md            # отчет расхождений
  crosscheck_numbers.py vision.txt unlimited.md --min-digits 3
Выход: 0 — множества чисел совпали; 1 — есть расхождения (перечислены).
"""
import re
import sys
from collections import Counter

# числа с группами тысяч (пробел/nbsp/narrow-nbsp) и десятичной , или .
_NUM = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_TAG = re.compile(r"<[^>]+>")


def canon(tok: str) -> str:
    """«2 650,00» и «2 650.00» → «2650.00»; целое остается целым."""
    t = re.sub(r"[   ]", "", tok).replace(",", ".")
    return t


def numbers_of(text: str, min_digits: int = 2) -> Counter:
    text = _TAG.sub(" ", text)  # HTML-таблицы Unlimited → голый текст
    out = Counter()
    for m in _NUM.finditer(text):
        c = canon(m.group(0))
        if sum(ch.isdigit() for ch in c) >= min_digits:
            out[c] += 1
    return out


def crosscheck(text_a: str, text_b: str, min_digits: int = 2):
    """Возвращает (only_a, only_b, common) как Counter-разности мультимножеств."""
    a, b = numbers_of(text_a, min_digits), numbers_of(text_b, min_digits)
    return a - b, b - a, sum((a & b).values())


def selftest():
    """Без сети и без файлов: сверка мультимножеств чисел."""
    a = "Взыскать 1 250 000 руб. по договору 4412, пени 12 500 руб., еще 12 500 руб."
    b = "Договор 4412. Сумма 1250000 руб. Пени 12 500 руб."
    only_a, only_b, common = crosscheck(a, b, 4)
    checks = [
        ("пробелы в числе не мешают сравнению", "1250000" not in only_a),
        ("повтор учитывается как кратность", only_a.get("12500") == 1),
        ("общие числа посчитаны", common >= 2),
        ("лишнего в источнике нет", not only_b),
        ("пустые тексты не роняют", crosscheck("", "", 4)[2] == 0),
        ("порог значащих цифр работает", "44" not in numbers_of("код 44", 4)),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    md = 2
    if "--min-digits" in sys.argv:
        md = int(sys.argv[sys.argv.index("--min-digits") + 1])
        args = args[:2]
    ta = open(args[0], encoding="utf-8", errors="ignore").read()
    tb = open(args[1], encoding="utf-8", errors="ignore").read()
    only_a, only_b, common = crosscheck(ta, tb, md)
    print(f"общих чисел: {common}")
    if only_a:
        print(f"ТОЛЬКО в {args[0]} ({sum(only_a.values())}):",
              ", ".join(sorted(only_a)[:40]))
    if only_b:
        print(f"ТОЛЬКО в {args[1]} ({sum(only_b.values())}):",
              ", ".join(sorted(only_b)[:40]))
    if not only_a and not only_b:
        print("расхождений нет ✓")
        return
    sys.exit(1)


if __name__ == "__main__":
    main()
