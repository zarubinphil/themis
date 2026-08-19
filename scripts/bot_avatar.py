#!/usr/bin/env python3
"""bot_avatar.py — аватар бота Фемиды: весы правосудия на тёмном поле.

Зачем прибором, а не картинкой в репозитории. Растр под `cases/` запрещён отдельным
правилом, а держать бинарник в публичном репозитории ради одной картинки незачем:
код короче файла и правится глазами. Прогон повторяем — два запуска дают побайтово
одинаковый PNG, поэтому «обновил аватар» видно диффом пути, а не размером блоба.

    --out ФАЙЛ [--size N]   кладёт PNG (по умолчанию 512×512) и печатает путь

Установка картинки самому боту — за владельцем: Bot API менять фото профиля бота
не умеет, это делается в BotFather вручную. Наша часть — отдать готовый файл.

Своим кодом и без внешних пакетов: PNG собирается из zlib+struct стандартной
библиотеки (формат — спецификация PNG 1.2, W3C REC-PNG-20031110, общедоступный
стандарт, не чужой исходник).
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

FON = (16, 20, 28)          # тёмный графит
ZOLOTO = (198, 164, 96)     # латунь весов
TEN = (120, 98, 56)


def _smes(a, b, k):
    """k=0 — цвет a, k=1 — цвет b. Сглаживает край без библиотек."""
    k = 0.0 if k < 0 else (1.0 if k > 1 else k)
    return tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _kroem(px, n, forma, cvet, myagkost=1.2):
    """Заливка по функции расстояния: forma(x, y) < 0 внутри фигуры.
    Мягкая граница шириной myagkost пикселя — иначе на аватарке видна лесенка."""
    for y in range(n):
        row = px[y]
        for x in range(n):
            d = forma(x + 0.5, y + 0.5)
            if d < myagkost:
                row[x] = _smes(cvet, row[x], 0.0 if d <= 0 else d / myagkost)


def narisovat(n: int = 512) -> bytes:
    px = [[FON] * n for _ in range(n)]
    c = n / 2.0
    ed = n / 512.0                      # единица масштаба: рисунок задан для 512

    # Кольцо — граница пульта: внутри тайна, снаружи Telegram.
    def kolco(x, y):
        r = math.hypot(x - c, y - c)
        return max(r - (n * 0.47), (n * 0.44) - r)
    _kroem(px, n, kolco, TEN)

    # Стойка весов
    def stoyka(x, y):
        return max(abs(x - c) - 5 * ed, abs(y - c) - 150 * ed)
    _kroem(px, n, stoyka, ZOLOTO)

    # Основание
    def osnovanie(x, y):
        return max(abs(x - c) - 92 * ed, abs(y - (c + 150 * ed)) - 9 * ed)
    _kroem(px, n, osnovanie, ZOLOTO)

    def nozhka(x, y):
        return max(abs(x - c) - 40 * ed, abs(y - (c + 130 * ed)) - 22 * ed)
    _kroem(px, n, nozhka, ZOLOTO)

    # Коромысло
    def koromyslo(x, y):
        return max(abs(x - c) - 165 * ed, abs(y - (c - 120 * ed)) - 6 * ed)
    _kroem(px, n, koromyslo, ZOLOTO)

    # Чаши: дуга снизу и подвес сверху — обе на одной высоте (равновесие).
    for znak in (-1, 1):
        cx = c + znak * 150 * ed
        cy = c - 20 * ed

        def podves(x, y, cx=cx):
            return max(abs(x - cx) - 3 * ed, abs(y - (c - 72 * ed)) - 48 * ed)
        _kroem(px, n, podves, ZOLOTO)

        def chasha(x, y, cx=cx, cy=cy):
            r = math.hypot(x - cx, (y - cy) * 1.55)
            vnutri = max(r - 62 * ed, (52 * ed) - r)
            return max(vnutri, cy - y)          # нижняя половина кольца
        _kroem(px, n, chasha, ZOLOTO)

    # Навершие
    def navershie(x, y):
        return math.hypot(x - c, y - (c - 150 * ed)) - 16 * ed
    _kroem(px, n, navershie, ZOLOTO)

    syroe = b"".join(b"\x00" + b"".join(bytes(px[y][x]) for x in range(n)) for y in range(n))
    return _png(n, n, syroe)


def _chunk(tip: bytes, telo: bytes) -> bytes:
    return (struct.pack(">I", len(telo)) + tip + telo
            + struct.pack(">I", zlib.crc32(tip + telo) & 0xFFFFFFFF))


def _png(w: int, h: int, syroe: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)      # 8 бит, truecolor
    # mtime в zlib-заголовок не пишется, поэтому файл повторяем байт в байт.
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(syroe, 9)) + _chunk(b"IEND", b""))


def selftest() -> int:
    import tempfile
    a = narisovat(64)
    b = narisovat(64)
    assert a == b, "два прогона дали разные файлы — картинка пляшет"
    assert a[:8] == b"\x89PNG\r\n\x1a\n", "заголовок не PNG"
    assert int.from_bytes(a[16:20], "big") == 64, "ширина в IHDR не та"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "proba.png"
        f.write_bytes(narisovat(512))
        raw = f.read_bytes()
        assert int.from_bytes(raw[16:20], "big") == 512, "размер по умолчанию мельче 512"
        assert len(raw) > 1000, "файл подозрительно пуст"
    print("selftest: PNG собран своим кодом, повторяем байт в байт")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Аватар бота Фемиды (ставит в BotFather владелец).")
    ap.add_argument("--out", help="куда положить PNG")
    ap.add_argument("--size", type=int, default=512, help="сторона в пикселях (минимум 512)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.out:
        ap.error("нужен --out или --selftest")
    if a.size < 512:
        print("ОТКАЗ: Telegram сжимает аватар, мельче 512 брать нечего", file=sys.stderr)
        return 1
    put = Path(a.out).expanduser()
    # Под cases/ растр запрещён отдельным правилом проекта. Правило держит хук
    # Claude, но хук стоит на инструменте, а не на цели записи: скрипт, запущенный
    # руками, прошёл бы мимо него. Гейт ставится на цель.
    cases = (Path(__file__).resolve().parent.parent / "cases").resolve()
    if cases in put.resolve().parents:
        print("ОТКАЗ: под cases/ растр не пишется — там живут материалы дел. "
              "Аватар кладётся в ~/.themis/ либо любой каталог вне дел.", file=sys.stderr)
        return 1
    put.parent.mkdir(parents=True, exist_ok=True)
    put.write_bytes(narisovat(a.size))
    print(put)
    return 0


if __name__ == "__main__":
    sys.exit(main())
