#!/usr/bin/env python3
"""voice_local.py — расшифровка голосового ТОЛЬКО на этой машине.

Зачем. Владелец надиктовывает боту задачу голосом, и в надиктовке звучат фамилии
доверителей, суммы и номера дел. Отправить такой звук в облачную расшифровку —
разгласить адвокатскую тайну (ст. 8 ФЗ № 63-ФЗ) целиком и разом, потому что в звуке
нет ни маркеров, ни обезличивания: маскировать нечего, пока не расшифровано.
Отсюда правило: звук не покидает машину НИКОГДА.

Fail-closed. Локального движка нет — прибор отказывает, а не уходит в облако.
Молчаливая деградация здесь опаснее отказа: отказ владелец увидит и починит,
а тихий уход в чужой сервис заметить нечем.

    --transcribe ФАЙЛ [--json] [--language ru]   текст расшифровки на stdout
    --engines                                    какие движки видны на этой машине
    --selftest                                   без сети

Движок берется в таком порядке: переменная THEMIZ_STT_CMD → `voice.stt_cmd` из конфига
(scripts/themiz_config.py) → платформенное умолчание. На macOS это SMLTLK (Neural Engine,
штатный компонент Фемиды), на сервере — whisper. Своя команда получает ОДИН аргумент —
путь к файлу — и печатает текст в stdout.

Модель whisper подкачивается из сети при первом запуске, поэтому ее отсутствие — тоже
отказ, а не «сейчас докачаю»: докачка и есть то самое обращение наружу, которого здесь
быть не должно. Модель кладет владелец заранее.

Текст расшифровки НЕ ЛОГИРУЕТСЯ. Он уходит только в stdout вызвавшему.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import sreda  # noqa: E402,F401  переходный период имен переменных

SCRIPTS_DIR = Path(__file__).resolve().parent
# Умолчания по платформам. Первый найденный в PATH и выигрывает.
DVIZHKI = {
    "Darwin": ["smltlk-transcribe", "smltlk", "whisper-cli", "whisper-cpp", "whisper"],
    "Linux": ["whisper-cli", "whisper-cpp", "whisper"],
    "Windows": ["whisper-cli.exe", "whisper.exe", "whisper"],
}
ZVUK = (".oga", ".ogg", ".opus", ".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".mov")
TAYMAUT = 900


def _config_cmd() -> str:
    """Команда из конфига установки. Конфига нет — это норма, а не ошибка."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import themiz_config
        return str((themiz_config.load(themiz_config.DEFAULT_PATH).get("voice") or {})
                   .get("stt_cmd") or "")
    except Exception:                                          # noqa: BLE001
        return ""
    finally:
        if sys.path and sys.path[0] == str(SCRIPTS_DIR):
            sys.path.pop(0)


def _ispolnyaemyy(cmd: str) -> str:
    """Путь к исполняемому файлу или пусто. Имя без каталога ищется в PATH."""
    if not cmd:
        return ""
    p = Path(os.path.expanduser(cmd))
    if p.is_file() and os.access(p, os.X_OK):
        return str(p)
    return shutil.which(cmd) or ""


def engines() -> list:
    """Что видно на этой машине: свой, из конфига, платформенные."""
    out = []
    for istochnik, cmd in (("THEMIZ_STT_CMD", os.environ.get("THEMIZ_STT_CMD", "")),
                           ("конфиг voice.stt_cmd", _config_cmd())):
        if cmd:
            out.append({"source": istochnik, "cmd": cmd, "path": _ispolnyaemyy(cmd)})
    for name in DVIZHKI.get(platform.system(), DVIZHKI["Linux"]):
        put = shutil.which(name)
        if put:
            out.append({"source": "платформа", "cmd": name, "path": put})
    return out


def vybrat() -> dict:
    for e in engines():
        if e["path"]:
            return e
    return {}


def _sreda() -> dict:
    """Окружение движка без наших секретов: расшифровщику они не нужны никогда."""
    gryaz = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APIKEY")
    return {k: v for k, v in os.environ.items()
            if not any(g in k.upper() for g in gryaz)}


def _whisper_model_est(put: str) -> bool:
    """У whisper модель качается из сети при первом запуске. Нет модели — нет расшифровки:
    докачка и есть то обращение наружу, которого тут быть не должно."""
    if "whisper" not in Path(put).name.lower():
        return True
    for d in (os.environ.get("WHISPER_MODEL_DIR"), Path.home() / ".cache" / "whisper",
              Path.home() / ".cache" / "whisper.cpp"):
        if d and Path(d).is_dir() and any(Path(d).iterdir()):
            return True
    return False


def transcribe(src: str, language: str = "ru") -> dict:
    put = Path(os.path.expanduser(src))
    if not put.is_file():
        raise SystemExit(f"ОТКАЗ: файла нет: {put}")
    e = vybrat()
    if not e:
        vidno = ", ".join(sorted({x["cmd"] for x in engines()})) or "ни одного"
        raise SystemExit(
            "ОТКАЗ: локального движка расшифровки на этой машине нет "
            f"(искали: {vidno}). Звук с материалами дела за пределы машины не уходит, "
            "поэтому замены нет: поставить SMLTLK (macOS) или whisper (сервер) — "
            "`bash install.sh --with-smltlk`, — либо назвать свою команду в THEMIZ_STT_CMD.")
    if not _whisper_model_est(e["path"]):
        raise SystemExit(
            "ОТКАЗ: whisper найден, а модель на диске отсутствует — первый запуск полез бы "
            "за ней в сеть. Скачать модель заранее и повторить "
            "(каталог ~/.cache/whisper либо WHISPER_MODEL_DIR).")

    imya = Path(e["path"]).name.lower()
    # Вывод движка — во временный каталог, не рядом со звуком: материалы дела
    # не обрастают побочными файлами (и не попадают под запрет растра в cases/).
    with tempfile.TemporaryDirectory(prefix="themiz-stt-") as td:
        if imya.startswith("whisper") and "cli" not in imya and "cpp" not in imya:
            argv = [e["path"], str(put), "--language", language, "--output_format", "txt",
                    "--output_dir", td, "--fp16", "False"]
        else:
            argv = [e["path"], str(put)]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=TAYMAUT,
                               cwd=td, env=_sreda(), input="")
        except subprocess.TimeoutExpired:
            raise SystemExit(f"ОТКАЗ: движок {imya} не ответил за {TAYMAUT} с")
        except OSError as ex:
            raise SystemExit(f"ОТКАЗ: движок {imya} не запустился: {ex}")
        text = (p.stdout or "").strip()
        if not text:
            for f in sorted(Path(td).glob("*.txt")):
                text = f.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    break
    if p.returncode != 0 and not text:
        # stderr движка может нести куски расшифровки — наружу его не выносим.
        raise SystemExit(f"ОТКАЗ: движок {imya} вернул {p.returncode} и пустую расшифровку")
    if not text:
        raise SystemExit(f"ОТКАЗ: движок {imya} вернул пустую расшифровку")
    return {"text": text, "engine": imya, "local": True, "source": e["source"]}


def selftest() -> int:
    import stat as _st
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        zvuk = td / "golos.oga"
        zvuk.write_bytes(b"OggS" + b"\x00" * 32)
        dvizhok = td / "stt.sh"
        dvizhok.write_text("#!/bin/bash\necho 'проба расшифровки'\n", encoding="utf-8")
        dvizhok.chmod(dvizhok.stat().st_mode | _st.S_IXUSR)

        saved = dict(os.environ)
        try:
            os.environ["THEMIZ_STT_CMD"] = str(dvizhok)
            do = sorted(p.name for p in td.iterdir())
            d = transcribe(str(zvuk))
            assert "проба" in d["text"], f"расшифровка потеряна: {d}"
            assert d["local"] is True, "расшифровка не объявлена локальной"
            posle = sorted(p.name for p in td.iterdir())
            assert do == posle, f"движок насорил рядом со звуком: {set(posle) - set(do)}"

            # Движка нет — отказ, а не тихий уход в облако.
            os.environ["THEMIZ_STT_CMD"] = str(td / "net-takogo")
            os.environ["PATH"] = str(td)
            try:
                transcribe(str(zvuk))
                raise AssertionError("без движка вернулась расшифровка")
            except SystemExit as ex:
                msg = str(ex).lower()
                assert "отказ" in msg, f"отказ не назван отказом: {msg[:80]}"
                for oblako in ("openai", "google", "yandex", "яндекс", "облачн"):
                    assert oblako not in msg, f"отказ предлагает облако: {oblako}"

            # Секреты в окружение движка не попадают.
            os.environ["THEMIZ_PANEL_TOKEN"] = "ne-dolzhen-uyti"
            assert "THEMIZ_PANEL_TOKEN" not in _sreda(), "секрет ушел бы движку в окружение"
        finally:
            os.environ.clear()
            os.environ.update(saved)
    print("selftest: расшифровка локальна, без движка — отказ, секреты движку не видны")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Локальная расшифровка голосового.")
    ap.add_argument("--transcribe", metavar="ФАЙЛ")
    ap.add_argument("--language", default="ru")
    ap.add_argument("--engines", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.engines:
        print(json.dumps(engines(), ensure_ascii=False, indent=1))
        return 0
    if a.transcribe:
        d = transcribe(a.transcribe, a.language)
        print(json.dumps(d, ensure_ascii=False) if a.json else d["text"])
        return 0
    ap.error("нужен --transcribe, --engines или --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
