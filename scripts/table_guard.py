#!/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
"""table_guard.py — детекция «таблица на странице БЫЛА, а в выходе OCR ее НЕТ».

Двухсторонняя проверка, $0, без модели:
  1. РАСТР: сетка таблицы в PNG — длинные горизонтальные/вертикальные линии
     (бинаризация + проекции numpy). >=3 горизонтальных штриха на >=50% ширины
     ЛИБО >=2 горизонтальных + >=3 вертикальных → на странице таблица.
  2. ТЕКСТ: сохранил ли артефакт структуру — есть <table>/markdown-| /
     выровненные колонки (>=3 строк с >=2 разрывами из 2+ пробелов или табов).

Сетка есть + структуры в тексте нет → TABLE_STRUCTURE_LOST: страницу отправлять
на структурный движок (Unlimited-OCR по диапазону) или читать по PNG.

Использование:
  table_guard.py page.png page.txt        # проверить пару
  table_guard.py page.png                 # только растровый сигнал
Выход: 0 — потери нет; 3 — TABLE_STRUCTURE_LOST.
"""
import sys

import numpy as np
from PIL import Image

H_MIN_FRAC = 0.50   # горизонтальная линия — темный ход >=50% ширины
V_MIN_FRAC = 0.35   # вертикальная — >=35% высоты (в таблицах колонки короче строк)
DARK = 128


def grid_signals(png_path: str) -> dict:
    im = Image.open(png_path).convert("L")
    # даунскейл до ширины 1000 — быстрее на порядок, линии сетки не пропадают
    w0, h0 = im.size
    if w0 > 1000:
        im = im.resize((1000, int(h0 * 1000 / w0)))
    a = np.asarray(im) < DARK
    h, w = a.shape

    def runs(axis_arr, min_len):
        """Число строк/столбцов, где максимальный непрерывный темный ход >= min_len."""
        cnt = 0
        for row in axis_arr:
            best = cur = 0
            for v in row:
                cur = cur + 1 if v else 0
                best = max(best, cur)
            if best >= min_len:
                cnt += 1
        return cnt

    h_lines = runs(a, int(w * H_MIN_FRAC))
    v_lines = runs(a.T, int(h * V_MIN_FRAC))
    has_grid = h_lines >= 3 or (h_lines >= 2 and v_lines >= 3)
    return {"h_lines": h_lines, "v_lines": v_lines, "grid": has_grid}


def text_has_structure(text: str) -> bool:
    if "<table" in text or "</td>" in text:
        return True
    md_rows = sum(1 for ln in text.splitlines() if ln.count("|") >= 2)
    if md_rows >= 3:
        return True
    aligned = sum(1 for ln in text.splitlines()
                  if len([g for g in ln.split("  ") if g.strip()]) >= 3 or ln.count("\t") >= 2)
    return aligned >= 3


def main():
    png = sys.argv[1]
    g = grid_signals(png)
    print(f"растр: h_lines={g['h_lines']} v_lines={g['v_lines']} → "
          f"{'ТАБЛИЦА ЕСТЬ' if g['grid'] else 'сетки нет'}")
    if len(sys.argv) < 3:
        sys.exit(0)
    txt = open(sys.argv[2], encoding="utf-8", errors="ignore").read()
    st = text_has_structure(txt)
    print(f"текст: структура {'сохранена' if st else 'ОТСУТСТВУЕТ'}")
    if g["grid"] and not st:
        print("⚠ TABLE_STRUCTURE_LOST — страницу на структурный движок или читать PNG")
        sys.exit(3)
    print("потери структуры нет ✓")


if __name__ == "__main__":
    main()
