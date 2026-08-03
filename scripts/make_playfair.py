#!/usr/bin/env python3
"""Статические начертания Playfair Display для титульного блока документов.

В репозитории google/fonts Playfair Display лежит ТОЛЬКО вариативным файлом.
Word вариативные шрифты берет по умолчанию в Regular, а полужирный синтезирует
сам — получается размазанный псевдожир вместо настоящего начертания. Поэтому
Regular и Bold нарезаются заранее и ставятся в систему как обычные ttf.

    python3 scripts/make_playfair.py

Повторный запуск безопасен: перезаписывает те же два файла.
Требуется fonttools (уже стоит в системе).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = ("https://raw.githubusercontent.com/google/fonts/main/ofl/"
       "playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf")
FAMILY = "Playfair Display"
# Стиль: (вес, fsSelection, macStyle). Биты fsSelection по OS/2:
# 0x0001 ITALIC, 0x0020 BOLD, 0x0040 REGULAR. macStyle: бит 0 — Bold.
STYLES = {
    "Regular": (400, 0x0040, 0),
    "Bold": (700, 0x0020, 1),
}


def main() -> int:
    try:
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
    except ImportError:
        print("нужен fonttools: python3 -m pip install fonttools", file=sys.stderr)
        return 2

    dest = Path.home() / "Library" / "Fonts"
    if not dest.is_dir():                      # не macOS — кладем рядом со скриптом
        dest = Path(__file__).resolve().parent / "_fonts"
        dest.mkdir(exist_ok=True)
        print(f"каталог шрифтов системы не найден, кладу в {dest}")

    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "PlayfairDisplay-variable.ttf"
    if not tmp.exists() or tmp.stat().st_size < 100_000:
        print(f"качаю {SRC}")
        # curl, а не urllib: у системного python на macOS не настроен корневой
        # набор сертификатов, urlopen падает на CERTIFICATE_VERIFY_FAILED.
        rc = subprocess.run(["curl", "-sSL", "--fail", "-o", str(tmp), SRC]).returncode
        if rc != 0 or not tmp.exists():
            print(f"не удалось скачать {SRC}", file=sys.stderr)
            return 1

    for style, (weight, fs_selection, mac_style) in STYLES.items():
        font = TTFont(str(tmp))
        instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True)

        full = FAMILY if style == "Regular" else f"{FAMILY} {style}"
        for platform_id, encoding_id, lang_id in ((3, 1, 0x409), (1, 0, 0)):
            font["name"].setName(FAMILY, 1, platform_id, encoding_id, lang_id)
            font["name"].setName(style, 2, platform_id, encoding_id, lang_id)
            font["name"].setName(full, 4, platform_id, encoding_id, lang_id)
            font["name"].setName(f"PlayfairDisplay-{style}", 6,
                                 platform_id, encoding_id, lang_id)
        # Без согласованных OS/2 и head система считает оба файла одним и тем же
        # начертанием и показывает только один из них.
        font["OS/2"].usWeightClass = weight
        font["OS/2"].fsSelection = fs_selection
        font["head"].macStyle = mac_style

        out = dest / f"PlayfairDisplay-{style}.ttf"
        font.save(str(out))
        print(f"{out.name}: вес {weight}, fsSelection {fs_selection:#06x}")

    print("готово. Word подхватит после перезапуска.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
