#!/usr/bin/env python3
"""Общее основание приборов: пути, SHA-256, CLI и коды возврата."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

KOD_OK = 0
KOD_OSHIBKA = 1
KOD_NE_RABOTAL = 2
KOD_STOP = 3

PRAVILO_TRUBY = (
    "код прибора читать без внешней трубы; для показа вывода звать scripts/gate.sh "
    "и читать код самой обертки"
)

# ponytail: слой подключен к трем приборам M04; остальные переходят сюда
# по одному при своей следующей точечной починке.


def dom_proekta() -> Path:
    """Корень репозитория независимо от cwd и HOME."""
    return Path(__file__).resolve().parent.parent


def dom_sessij(project: str | os.PathLike[str] | None = None) -> Path:
    """Каталог сессий Claude Code для проекта; HOME читается при каждом вызове."""
    project_path = Path.cwd() if project is None else Path(project)
    key = re.sub(r"[^A-Za-z0-9]", "-", str(project_path.resolve()))
    return Path.home() / ".claude" / "projects" / key


def kluch_kesha(path: str | os.PathLike[str]) -> str:
    """Полный SHA-256 файла, без обрезки."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parser(opisanie: str) -> argparse.ArgumentParser:
    """Единая основа CLI прибора с обязательным режимом самопроверки."""
    result = argparse.ArgumentParser(description=opisanie)
    result.add_argument("--selftest", action="store_true", help="проверка без сети")
    return result


def zavershit(main: Callable[[], int]) -> NoReturn:
    """Передать код main ОС; правило внешней трубы задано в PRAVILO_TRUBY."""
    raise SystemExit(main())
