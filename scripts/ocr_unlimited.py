#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_unlimited.py — основной OCR-движок Фемиды: baidu/Unlimited-OCR локально.

Решение владельца 02.08.2026: Unlimited-OCR — основа, Apple Vision и markitdown
работают в дополнение.

Зачем именно он. Apple Vision распознаёт страницу построчно и разрушает
структуру: таблица остатков счетов на стр. 82 заключения эксперта выходит
россыпью «Эмитент / Номер гос. рег-ии / ... / 10 11 12» — колонки перемешаны,
связь ячейки со строкой потеряна. А в деле о разделе имущества таблица не
оформление, а доказательство.

Unlimited-OCR разбирает ВЕСЬ документ одним проходом (one-shot long-horizon):
таблица, переходящая с 82-й страницы на 83-ю, остаётся одной таблицей, и
«продолжение табл. 83» не теряет заголовков. Постраничный OCR этого не умеет
в принципе.

Разделение труда:
    Unlimited-OCR  → `ocr_dir/document.md`   — структура, таблицы, сквозной контекст
    Apple Vision   → `ocr_dir/page_NNN.txt`  — постраничная адресация, читают агенты
Оба остаются: сквозной разбор не заменяет умения сослаться на конкретную страницу.

Локально, $0, ничего не покидает машину. Код Baidu прибит к CUDA — здесь шим на MPS.

Использование:
    python3 scripts/ocr_unlimited.py OCR_DIR                 # все page_*.png в каталоге
    python3 scripts/ocr_unlimited.py OCR_DIR --pages 80 85   # только диапазон
    python3 scripts/ocr_unlimited.py --selftest              # проверка окружения без модели

Выход: OCR_DIR/document.md + строка JSON в stdout со статистикой.
Ненулевой код возврата = движок недоступен; вызывающий обязан откатиться
на Apple Vision, а НЕ молча продолжить без текста.
"""
import argparse
import glob
import json
import os
import sys
import time

MODEL_DIR = os.environ.get("THEMIS_OCR_MODEL",
                           os.path.expanduser("~/.cache/themis-ocr/official"))
VENV_PY = os.environ.get("THEMIS_OCR_PYTHON",
                         os.path.expanduser("~/.cache/themis-ocr/.venv/bin/python"))
PROMPT = "<image>Multi page parsing."
IMAGE_SIZE = 1024
MAX_LENGTH = 32768


def env_report() -> dict:
    """Что есть на машине. Вызывается и при сбое — чтобы причина была видна."""
    weights = os.path.join(MODEL_DIR, "model-00001-of-000001.safetensors")
    return {
        "model_dir": MODEL_DIR,
        "model_dir_exists": os.path.isdir(MODEL_DIR),
        "weights_exists": os.path.isfile(weights),
        "weights_gb": round(os.path.getsize(weights) / 2**30, 2) if os.path.isfile(weights) else 0,
        "venv_python": VENV_PY,
        "venv_exists": os.path.isfile(VENV_PY),
    }


def available() -> bool:
    e = env_report()
    return e["model_dir_exists"] and e["weights_exists"] and e["venv_exists"]


def run(ocr_dir: str, first: int | None = None, last: int | None = None) -> dict:
    """Сквозной разбор страниц каталога. Запускается в отдельном интерпретаторе:
    torch/transformers весят сотни мегабайт импорта, тянуть их в роутер нельзя."""
    pages = sorted(glob.glob(os.path.join(ocr_dir, "page_*.png")))
    if first or last:
        def num(p):
            return int(os.path.basename(p).split("_")[1].split(".")[0])
        pages = [p for p in pages if (not first or num(p) >= first) and (not last or num(p) <= last)]
    if not pages:
        return {"ok": False, "reason": "нет page_*.png в каталоге", "pages": 0}

    out_md = os.path.join(ocr_dir, "document.md")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ocr_unlimited_worker.py")
    import subprocess
    t0 = time.time()
    r = subprocess.run([VENV_PY, script, MODEL_DIR, out_md, *pages],
                       capture_output=True, text=True, timeout=None)
    dt = time.time() - t0
    if r.returncode != 0 or not os.path.isfile(out_md):
        return {"ok": False, "reason": (r.stderr or "").strip()[-400:] or "движок не отдал результат",
                "pages": len(pages), "seconds": round(dt, 1)}
    chars = os.path.getsize(out_md)
    return {"ok": True, "pages": len(pages), "seconds": round(dt, 1),
            "sec_per_page": round(dt / len(pages), 1), "md_path": out_md, "md_bytes": chars}


def main() -> int:
    ap = argparse.ArgumentParser(description="Unlimited-OCR: сквозной разбор документа")
    ap.add_argument("ocr_dir", nargs="?", help="каталог с page_NNN.png")
    ap.add_argument("--pages", nargs=2, type=int, metavar=("FIRST", "LAST"))
    ap.add_argument("--selftest", action="store_true", help="проверить окружение без запуска модели")
    a = ap.parse_args()

    if a.selftest:
        e = env_report()
        e["available"] = available()
        print(json.dumps(e, ensure_ascii=False, indent=2))
        return 0 if e["available"] else 1

    if not a.ocr_dir:
        ap.error("нужен OCR_DIR либо --selftest")
    if not available():
        print(json.dumps({"ok": False, "reason": "движок не установлен",
                          "env": env_report()}, ensure_ascii=False))
        return 2

    first, last = (a.pages if a.pages else (None, None))
    res = run(a.ocr_dir, first, last)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
