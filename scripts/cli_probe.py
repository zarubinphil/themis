#!/usr/bin/env python3
"""cli_probe.py — доступен ли чужой CLI. Пять исходов вместо «работает/нет».

Зачем. `command -v codex` проверяет наличие файла, а не возможность работать:
бинарник на месте, но вход не выполнен, или кончилась квота, или каталог только
на чтение, или инструмент висит. Все четыре случая выглядят как «не сработало»
и лечатся по-разному, поэтому исход называется своим именем.

    --provider ИМЯ --json [--probe-cmd КОМАНДА] [--workdir КАТАЛОГ]
              [--timeout СЕК] [--cache ФАЙЛ] [--now ЭПОХА]
    --selftest

Исходы: ok · no_binary · no_auth · no_quota · no_write · timeout.
Код возврата: 0 только при ok — вызывающий не обязан разбирать JSON, чтобы понять,
идти ли к этому провайдеру.

Кеш. Отказ повторяется при каждом вызове, и без памяти проба долбится в закрытую
дверь на каждом листе роя. Отказ по квоте БЕЗ названной даты сброса протухает через
пять часов: квота восстанавливается сама, и вечная запись «нет квоты» отрезает
провайдера навсегда.

`--now` существует ради приёмки: срок протухания проверяется подстановкой времени,
а не ожиданием пяти часов.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Команда проверки на каждого известного провайдера. Спрашиваем инструмент о его
# собственном состоянии: `--version` о входе не говорит ничего.
KNOWN = {
    "claude": ["claude", "auth", "status"],
    "codex": ["codex", "login", "status"],
    "kimi": ["kimi", "--version"],
    "gemini": ["gemini", "--version"],
}
DEFAULT_CACHE = Path(os.path.expanduser("~/.cache/themis/cli_probe.json"))
QUOTA_TTL = 5 * 3600      # квота восстанавливается сама — запрет не может быть вечным
OTKAZ_TTL = 15 * 60       # прочие отказы: чиниться им человеком, но не каждую секунду
NO_AUTH = ("not logged", "logged out", "login required", "unauthorized", "не выполнен вход",
           '"loggedin": false', "please log in", "authentication required")
NO_QUOTA = ("quota", "usage limit", "rate limit", "too many requests", "limit reached",
            "insufficient", "исчерпан")


def _cache_read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass          # кеш — ускорение, а не условие работы


def _writable(d: Path) -> bool:
    """Может ли чужой CLI вообще работать в этом каталоге."""
    try:
        with tempfile.NamedTemporaryFile(dir=str(d), prefix=".proba-", delete=True):
            return True
    except (OSError, PermissionError):
        return False


def probe(provider: str, cmd=None, workdir=None, timeout=30) -> dict:
    argv = cmd if cmd else KNOWN.get(provider)
    if isinstance(argv, str):
        argv = [argv]
    if not argv:
        return {"outcome": "no_binary", "detail": f"провайдер {provider} неизвестен"}

    exe = argv[0]
    if not (shutil.which(exe) or os.path.isfile(exe)):
        return {"outcome": "no_binary", "detail": f"{exe} не найден"}

    wd = Path(workdir) if workdir else Path(tempfile.gettempdir())
    if not wd.is_dir():
        return {"outcome": "no_write", "detail": f"каталога {wd} нет"}
    if not _writable(wd):
        return {"outcome": "no_write", "detail": f"{wd} недоступен для записи — "
                                                 "чужому CLI негде работать"}
    try:
        p = subprocess.run(argv, cwd=str(wd), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "detail": f"не ответил за {timeout} с"}
    except OSError as e:
        return {"outcome": "no_binary", "detail": str(e)}

    text = ((p.stdout or "") + (p.stderr or "")).lower()
    if any(w in text for w in NO_QUOTA):
        return {"outcome": "no_quota", "detail": text.strip()[:200]}
    if any(w in text for w in NO_AUTH):
        return {"outcome": "no_auth", "detail": text.strip()[:200]}
    if p.returncode != 0:
        return {"outcome": "no_auth", "detail": f"код {p.returncode}: {text.strip()[:200]}"}
    return {"outcome": "ok", "detail": text.strip().splitlines()[0][:120] if text.strip() else ""}


def check(provider: str, cmd=None, workdir=None, timeout=30,
          cache: Path = DEFAULT_CACHE, now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    zapisi = _cache_read(cache)
    zapis = zapisi.get(provider)
    if zapis and zapis.get("until", 0) > now:
        return {**zapis, "provider": provider, "cached": True}

    r = probe(provider, cmd, workdir, timeout)
    r["provider"] = provider
    r["cached"] = False
    r["checked"] = int(now)
    if r["outcome"] != "ok":
        ttl = QUOTA_TTL if r["outcome"] == "no_quota" else OTKAZ_TTL
        r["until"] = int(now + ttl)
        zapisi[provider] = {k: v for k, v in r.items() if k != "cached"}
        _cache_write(cache, zapisi)
    elif provider in zapisi:
        zapisi.pop(provider)          # ожил — запрет снимается сразу
        _cache_write(cache, zapisi)
    return r


def selftest() -> int:
    import stat as _stat
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cache = td / "c.json"
        work = td / "w"
        work.mkdir()

        def sh(name, body):
            p = td / name
            p.write_text("#!/bin/bash\n" + body, encoding="utf-8")
            p.chmod(p.stat().st_mode | _stat.S_IXUSR)
            return str(p)

        ok = sh("ok.sh", 'echo "Logged in"; exit 0\n')
        assert check("t", ok, str(work), cache=cache)["outcome"] == "ok"
        assert check("t", str(td / "netu.sh"), str(work), cache=cache)["outcome"] == "no_binary"
        assert check("t2", sh("a.sh", 'echo "not logged in"; exit 1\n'), str(work),
                     cache=cache)["outcome"] == "no_auth"
        q = check("t3", sh("q.sh", 'echo "usage limit reached"; exit 1\n'), str(work), cache=cache)
        assert q["outcome"] == "no_quota", q
        assert q["until"] - q["checked"] == QUOTA_TTL, "срок протухания квоты не пять часов"
        ro = td / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        assert check("t4", ok, str(ro), cache=cache)["outcome"] == "no_write"
        assert check("t5", sh("s.sh", "sleep 5\n"), str(work), timeout=1,
                     cache=cache)["outcome"] == "timeout"

        # Кеш: отказ помнится, после срока — проба заново, оживший провайдер чистит запись.
        povtor = check("t3", ok, str(work), cache=cache)
        assert povtor["cached"], "отказ не закеширован — рой будет долбиться в закрытую дверь"
        posle = check("t3", ok, str(work), cache=cache, now=q["until"] + 1)
        assert not posle["cached"] and posle["outcome"] == "ok", "после срока квота не проверена заново"
        assert "t3" not in _cache_read(cache), "ожившего провайдера не выпустили из кеша"
    print("selftest пройден: пять исходов различены, отказ по квоте протухает за 5 ч")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Доступность чужого CLI: пять исходов.")
    ap.add_argument("--provider", help="claude | codex | kimi | gemini | своё имя")
    ap.add_argument("--probe-cmd", help="команда проверки (для своих провайдеров и приёмки)")
    ap.add_argument("--workdir", help="каталог, в котором будет работать чужой CLI")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--now", type=float, help="подстановка времени (приёмка протухания)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.provider:
        ap.error("нужен --provider либо --selftest")
    r = check(a.provider, a.probe_cmd, a.workdir, a.timeout, Path(a.cache), a.now)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"{r['provider']}: {r['outcome']}" + (f" — {r['detail']}" if r.get("detail") else "")
              + (" (из кеша)" if r.get("cached") else ""))
    return 0 if r["outcome"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
