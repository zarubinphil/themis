#!/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
"""table_guard.py — детекция «таблица на странице БЫЛА, а в выходе OCR ее НЕТ».

Двухсторонняя проверка, $0, без модели:
  1. РАСТР: линии сетки в PNG на ОБЕИХ осях (таблицы бывают повернуты на 90°).
     Полное разрешение, поля срезаны, линия = почти сплошной темный ход >=30%
     размера страницы; группы толще ~16 px отбрасываются (полоса сгиба фото,
     тени скана — не линии). Сетка = >=3 линии по одной оси и >=2 по другой.
  2. ТЕКСТ: сохранил ли артефакт структуру — <table>/markdown-|/выровненные
     колонки (>=3 строк с >=3 колонками через 2+ пробела или табы).

Сетка есть + структуры в тексте нет → TABLE_STRUCTURE_LOST: страницу отправлять
на структурный движок (Unlimited-OCR по диапазону) или сверять по PNG.

Использование:
  table_guard.py page.png page.txt   # пара растр+текст (код 3 при потере)
  table_guard.py page.png            # только растровый сигнал
"""
import sys

import numpy as np
from PIL import Image

LINE_FRAC = 0.30    # длина линии — от 30% размера страницы
DENSITY = 0.85      # доля темного в окне, чтобы ход считался сплошным
POOL = 4            # блок-максимум поперек линии: лечит перекос скана
MAX_THICK = 4       # толщина линии в пуленых рядах (=16 px) — толще не линия
MARGIN = 0.04       # срез полей: тени и кромки скана
# Порог темного — адаптивный: на чистом скане (медиана ~255) линии сетки
# светло-серые (~170-190) и при жестком 128 невидимы; на фото бумага сама серая,
# и завышенный порог зальет страницу «темным». min(200, медиана-45).


def _lines_along(a: np.ndarray) -> int:
    """Число тонких почти сплошных темных линий вдоль оси 1 массива a."""
    h, w = a.shape
    k = POOL
    a = a[: h - h % k].reshape(h // k, k, w).max(axis=1)  # пул поперек линии
    L = max(20, int(w * LINE_FRAC))
    s = np.cumsum(a, axis=1, dtype=np.int32)
    if w <= L:
        return 0
    dens = (s[:, L:] - s[:, :-L]).max(axis=1) / L
    hit = np.flatnonzero(dens >= DENSITY)
    if hit.size == 0:
        return 0
    # группировка подряд идущих рядов; толстые группы (сгиб, тень) — мимо
    splits = np.flatnonzero(np.diff(hit) > 1)
    groups = np.split(hit, splits + 1)
    return sum(1 for g in groups if len(g) <= MAX_THICK)


def _pass(g: np.ndarray, dark: int) -> tuple[int, int]:
    a = g < dark
    mh, mw = int(a.shape[0] * MARGIN), int(a.shape[1] * MARGIN)
    a = a[mh:-mh or None, mw:-mw or None]
    return _lines_along(a), _lines_along(a.T)


def grid_signals(png_path: str) -> dict:
    g = np.asarray(Image.open(png_path).convert("L"))
    med = int(np.median(g))
    # проход 1 — нормальный контраст
    h_lines, v_lines = _pass(g, min(200, med - 45))
    grid = ((h_lines >= 3 and v_lines >= 2) or (v_lines >= 3 and h_lines >= 2))
    faint = False
    if not grid:
        # проход 2 — бледная/полупропечатанная сетка (призрак печати, стр. 83
        # заключения): порог у самой медианы, но правило строже (обе оси >=3),
        # иначе шум бумаги дает ложные линии
        h2, v2 = _pass(g, med - 8)
        if h2 >= 3 and v2 >= 3:
            h_lines, v_lines, grid, faint = h2, v2, True, True
    return {"h_lines": h_lines, "v_lines": v_lines, "grid": grid, "faint": faint}


def text_has_structure(text: str) -> bool:
    if "<table" in text or "</td>" in text:
        return True
    if sum(1 for ln in text.splitlines() if ln.count("|") >= 2) >= 3:
        return True
    aligned = sum(1 for ln in text.splitlines()
                  if len([g for g in ln.split("  ") if g.strip()]) >= 3 or ln.count("\t") >= 2)
    return aligned >= 3


def main():
    png = sys.argv[1]
    g = grid_signals(png)
    print(f"растр: h_lines={g['h_lines']} v_lines={g['v_lines']}"
          f"{' (бледная печать)' if g['faint'] else ''} → "
          f"{'ТАБЛИЦА ЕСТЬ' if g['grid'] else 'сетки нет'}")
    if len(sys.argv) < 3:
        sys.exit(0)
    txt = open(sys.argv[2], encoding="utf-8", errors="ignore").read()
    st = text_has_structure(txt)
    print(f"текст: структура {'сохранена' if st else 'ОТСУТСТВУЕТ'}")
    if g["grid"] and not st:
        print("⚠ TABLE_STRUCTURE_LOST — страницу на структурный движок или сверять PNG")
        sys.exit(3)
    print("потери структуры нет ✓")


if __name__ == "__main__":
    main()
