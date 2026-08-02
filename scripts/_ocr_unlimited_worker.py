#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Воркер Unlimited-OCR. Запускается ТОЛЬКО из ocr_unlimited.py в отдельном
интерпретаторе (venv ~/.cache/themis-ocr/.venv): torch и transformers нельзя
тащить в роутер, который вызывается на каждый файл дела.

Аргументы: MODEL_DIR OUT_MD page1.png page2.png ...

Шим CUDA→MPS. Официальный код Baidu вызывает .cuda() в десятке мест и
torch.autocast("cuda"); на Apple Silicon это плумбинг устройства, а не логика,
поэтому подменяется снаружи и сама модель остаётся нетронутой.
"""
import contextlib
import pathlib
import sys

import torch

MODEL_DIR, OUT_MD, PAGES = sys.argv[1], sys.argv[2], sys.argv[3:]

DEV = "mps" if torch.backends.mps.is_available() else "cpu"
# Код Baidu кастует вход в bfloat16; веса должны быть того же типа, иначе
# conv2d падает на «Input type BFloat16 and bias type Half».
DTYPE = torch.bfloat16 if DEV == "mps" else torch.float32

torch.Tensor.cuda = lambda self, *a, **k: self.to(DEV)
torch.nn.Module.cuda = lambda self, *a, **k: self.to(DEV)
_orig_autocast = torch.autocast


def _autocast(device_type="cuda", **kw):
    return contextlib.nullcontext() if device_type == "cuda" else _orig_autocast(device_type, **kw)


torch.autocast = _autocast

from transformers import AutoModel, AutoTokenizer  # noqa: E402

tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
model = AutoModel.from_pretrained(MODEL_DIR, trust_remote_code=True,
                                  use_safetensors=True, torch_dtype=DTYPE)
model = model.eval().to(DEV)

outdir = pathlib.Path(OUT_MD).parent
res = model.infer_multi(tok, prompt="<image>Multi page parsing.",
                        image_files=PAGES, output_path=str(outdir),
                        image_size=1024, max_length=32768, temperature=0.0)

txt = res if isinstance(res, str) else str(res)
pathlib.Path(OUT_MD).write_text(txt, encoding="utf-8")
print(f"страниц {len(PAGES)}, символов {len(txt)}", file=sys.stderr)
