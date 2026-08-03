#!/usr/bin/env python3
"""Нарезка фрагментов образцов для презентации.

Берет полноразмерные рендеры из `_qa`, обрезает по границам реального
контента и режет на именованные фрагменты по долям высоты. Доли выверены
глазами по контактному листу — если образец переверстают, их надо
пересмотреть, автоматики тут нет и не нужно.
"""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
QA = HERE / "_qa"
OUT = HERE / "_deck"
OUT.mkdir(exist_ok=True)


def content_box(im):
    """Границы непустой области: обрезаем поля и хвост пустой страницы."""
    g = im.convert("L")
    w, h = g.size
    px = g.load()
    top, bottom = None, 0
    for y in range(h):
        row_min = min(px[x, y] for x in range(0, w, 3))
        if row_min < 200:
            bottom = y
            if top is None:
                top = y
    left, right = w, 0
    for x in range(0, w, 2):
        col_min = min(px[x, y] for y in range(top or 0, bottom, 3))
        if col_min < 200:
            left = min(left, x)
            right = max(right, x)
    return max(0, left - 25), max(0, (top or 0) - 25), min(w, right + 25), min(h, bottom + 25)


def cut(src_name, spec):
    im = Image.open(QA / src_name)
    box = content_box(im)
    body = im.crop(box)
    bw, bh = body.size
    for name, (a, b) in spec.items():
        frag = body.crop((0, int(bh * a), bw, int(bh * b)))
        frag.save(OUT / f"{name}.png")
        print(f"{name}.png {frag.size}")
    return body


def contact_sheet(body, name, rows=8):
    """Контактный лист с делением на доли — чтобы выверить границы глазами."""
    bw, bh = body.size
    thumb_w = 320
    sheet = Image.new("RGB", (thumb_w * rows, int(thumb_w * (bh / rows) / bw)), "white")
    for i in range(rows):
        part = body.crop((0, int(bh * i / rows), bw, int(bh * (i + 1) / rows)))
        part = part.resize((thumb_w, sheet.size[1]), Image.LANCZOS)
        sheet.paste(part, (thumb_w * i, 0))
    sheet.save(OUT / f"_sheet_{name}.png")
    print(f"_sheet_{name}.png {sheet.size}")


if __name__ == "__main__":
    isk = cut("tall_obrazets_isk_ru.docx.png", {
        "isk_head": (0.000, 0.125),
        "isk_summary": (0.126, 0.190),
        "isk_toc_num": (0.188, 0.295),
        "isk_timeline": (0.340, 0.412),
        "isk_parties": (0.5585, 0.609),
        "isk_calc": (0.610, 0.714),
    })
    contact_sheet(isk, "isk")

    dog = cut("tall_obrazets_dogovor.docx.png", {
        "dog_layer": (0.010, 0.140),
        "dog_oblig": (0.209, 0.336),
        "dog_attention": (0.805, 0.905),
    })
    contact_sheet(dog, "dog")

    # Образец «до» — сплошной текст без единого приема. Нужен слайду «до и после»
    # и слайду про расчет прозой вместо таблицы. Раньше эти три картинки
    # резались руками и в скрипт не попали: сборка деки падала на пустом месте.
    do = cut("tall_obrazets_isk_do.docx.png", {
        "do_page1": (0.000, 0.310),
        "do_calc": (0.640, 0.712),
    })
    contact_sheet(do, "do")

    cut("tall_obrazets_isk_ru.docx.png", {
        "after_page1": (0.000, 0.243),
    })
