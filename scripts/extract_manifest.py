#!/usr/bin/env python3
"""extract_manifest.py — аудит полноты OCR-кеша: каждая страница дошла до артефакта?

Проверяет ~/.cache/legal_extract/*_ocr:
  • manifest.json есть → верит ему: complete=false → сигнал (missing / beyond_maxp);
  • manifest.json нет (легаси-дыра) → сверка png против txt: png без txt = страница
    отрисована, но НЕ распознана — текст молча потерян для постраничной адресации.

--fix: до-OCR-ить дыры Apple Vision (локально, $0, ~1.1 с/стр) и записать манифест.

Выход: 0 — всё полно; 1 — есть дыры (список на stdout).
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from markdown_extract import CACHE, ocr_pages, write_manifest  # noqa: E402


def audit(fix=False):
    holes = []
    for odir in sorted(glob.glob(os.path.join(CACHE, "*_ocr"))):
        man_path = os.path.join(odir, "manifest.json")
        pngs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(odir, "page_*.png")))
        if os.path.isfile(man_path):
            man = json.load(open(man_path, encoding="utf-8"))
            if not man.get("complete"):
                holes.append((odir, f"манифест неполон: missing={man.get('missing')}, "
                                    f"beyond_maxp={man.get('beyond_maxp')}"))
            continue
        no_txt = [n for n in pngs
                  if not os.path.isfile(os.path.join(odir, n[:-4] + ".txt"))]
        if not no_txt:
            continue
        if fix:
            done, empty = ocr_pages(odir, no_txt)
            # легаси-дыры — смешанные PDF: страницы без png имели текст-слой
            nums = {int(n[5:8]) for n in pngs}
            total = max(nums)
            write_manifest(odir, total,
                           text_pages=[i for i in range(total) if i + 1 not in nums])
            print(f"✚ {os.path.basename(odir)}: до-OCR-ено {done} стр. ({empty} пустых)")
        else:
            holes.append((odir, f"png без txt: {len(no_txt)} из {len(pngs)} стр."))
    return holes


def main():
    fix = "--fix" in sys.argv
    holes = audit(fix=fix)
    for odir, why in holes:
        print(f"ДЫРА {os.path.basename(odir)}: {why}")
    if holes:
        print(f"\nитого дыр: {len(holes)}" + ("" if fix else " — запусти с --fix для до-OCR"))
        sys.exit(1)
    print("кеш полон ✓")


if __name__ == "__main__":
    main()
