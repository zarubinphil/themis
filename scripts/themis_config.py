#!/usr/bin/env python3
"""themis_config.py — настройки установки. Пустой конфиг = рабочая локальная система.

Зачем. Фемиду ставит себе другой юрист, и у него нет ни нашего сервера, ни нашего
бота. Значит, умолчание обязано быть таким: один Mac, ничего наружу. Сервер и бот —
персональные (решение владельца 18.08.2026): каждый заводит своего бота в BotFather
и привязывается к своему серверу, общего по умолчанию нет никогда.

    --show  [--config ФАЙЛ]   действующие настройки машинно (JSON)
    --check [--config ФАЙЛ]   годен ли конфиг: 0 — да, 1 — назвать беду
    --selftest                без сети

Где лежит: `~/.themis/config.json` (вне репозитория — там нет ни чужих настроек,
ни персональных данных). Путь можно задать переменной `THEMIS_CONFIG`.

**Секретов в конфиге нет.** Токен бота и токен панели живут в `$HOME/.secrets`, а конфиг
называет только ИМЯ переменной окружения. Конфиг лежит рядом с проектом, попадает в
резервные копии и в чужие руки заметно легче, чем каталог секретов.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("THEMIS_CONFIG") or Path.home() / ".themis" / "config.json")

# Умолчания = локальная работа. Ни адреса сервера, ни токена бота: чужое сюда
# не подставляется, своё владелец вписывает сам на онбординге.
DEFAULTS = {
    "inbox": str(Path.home() / "Desktop" / "inbox"),
    "server": {"enabled": False, "url": "", "token_env": "THEMIS_PANEL_TOKEN"},
    "bot": {"enabled": False, "token_env": "THEMIS_TELEGRAM_BOT_TOKEN", "chat_id_env":
            "THEMIS_TELEGRAM_CHAT_ID"},
    "practice": {"categories": [], "region": "", "arbitrazh": False},
    # Своя команда расшифровки голосового, если платформенная не подходит.
    # Пусто — движок выбирает scripts/voice_local.py по платформе.
    "voice": {"stt_cmd": ""},
}
# Ключи, в которых значение секрета быть НЕ ДОЛЖНО ни при каких обстоятельствах.
SECRET_KEYS = ("token", "secret", "password", "api_key", "apikey")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load(path: Path = DEFAULT_PATH) -> dict:
    """Настройки с диска поверх умолчаний. Файла нет — это норма, а не ошибка."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError):
        return dict(DEFAULTS)     # битый конфиг разбирает --check, --show не падает
    return _merge(DEFAULTS, raw if isinstance(raw, dict) else {})


def _secrets_inside(node, put: str = "") -> list:
    """Значения секретов, вписанные прямо в конфиг. Имя переменной (…_env) — не секрет."""
    beda = []
    if isinstance(node, dict):
        for k, v in node.items():
            beda += _secrets_inside(v, f"{put}.{k}" if put else k)
    elif isinstance(node, str) and node.strip():
        last = put.split(".")[-1].lower()
        if last.endswith("_env"):
            return []
        if any(s in last for s in SECRET_KEYS):
            beda.append(put)
    return beda


def check(path: Path) -> int:
    if not path.exists():
        print(f"конфига нет ({path}) — локальный режим: сервер и бот выключены, "
              "система работает на этой машине")
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"ОТКАЗ: конфиг не читается: {e}", file=sys.stderr)
        return 1
    if not isinstance(raw, dict):
        print("ОТКАЗ: конфиг должен быть объектом JSON", file=sys.stderr)
        return 1
    beda = []
    for put in _secrets_inside(raw):
        beda.append(f"секрет вписан прямо в конфиг ({put}) — токены живут в $HOME/.secrets, "
                    "в конфиге только имя переменной (…_env)")
    cfg = _merge(DEFAULTS, raw)
    if cfg["server"]["enabled"] and not cfg["server"]["url"]:
        beda.append("сервер включён, но адрес не назван")
    if cfg["bot"]["enabled"] and not cfg["bot"].get("token_env"):
        beda.append("бот включён, но не названа переменная с его токеном")
    if beda:
        print(f"конфиг негоден: {len(beda)}", file=sys.stderr)
        for b in beda:
            print("  · " + b, file=sys.stderr)
        return 1
    rezhim = "сервер включён" if cfg["server"]["enabled"] else "локальный режим"
    print(f"конфиг годен: {rezhim}, бот {'включён' if cfg['bot']['enabled'] else 'выключен'}")
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        net = td / "net.json"
        d = load(net)
        assert d["server"]["enabled"] is False, "без конфига сервер включён"
        assert d["bot"]["enabled"] is False, "без конфига бот включён"
        assert not d["server"]["url"], "в умолчаниях чужой адрес сервера"
        assert "token" not in json.dumps(DEFAULTS).replace("token_env", ""), \
            "в умолчаниях осталось поле секрета"
        assert check(net) == 0, "отсутствие конфига объявлено бедой"

        s = td / "secret.json"
        s.write_text(json.dumps({"bot": {"enabled": True, "token": "123:ABC"}}), encoding="utf-8")
        assert check(s) == 1, "секрет внутри конфига принят"

        b = td / "bad.json"
        b.write_text("{это не json", encoding="utf-8")
        assert check(b) == 1, "битый конфиг принят"
        assert load(b)["server"]["enabled"] is False, "битый конфиг сломал --show"

        ok = td / "ok.json"
        ok.write_text(json.dumps({"server": {"enabled": True, "url": "https://svoy.example"},
                                  "practice": {"region": "Республика Татарстан"}},
                                 ensure_ascii=False), encoding="utf-8")
        assert check(ok) == 0, "годный конфиг отвергнут"
        assert load(ok)["practice"]["region"] == "Республика Татарстан", "слияние потеряло своё"
        assert load(ok)["bot"]["enabled"] is False, "слияние потеряло умолчание"
        assert load(net)["voice"]["stt_cmd"] == "", "движок расшифровки задан по умолчанию"

        no_url = td / "nourl.json"
        no_url.write_text(json.dumps({"server": {"enabled": True}}), encoding="utf-8")
        assert check(no_url) == 1, "сервер без адреса принят"
    print("selftest пройден: умолчание локальное, секрет в конфиге отвергнут")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Настройки установки Фемиды.")
    ap.add_argument("--config", help=f"путь к конфигу (по умолчанию {DEFAULT_PATH})")
    ap.add_argument("--show", action="store_true", help="действующие настройки JSON")
    ap.add_argument("--check", action="store_true", help="проверить конфиг")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    path = Path(a.config) if a.config else DEFAULT_PATH
    if a.show:
        print(json.dumps(load(path), ensure_ascii=False, indent=2))
        return 0
    if a.check:
        return check(path)
    ap.error("нужен --show, --check или --selftest")


if __name__ == "__main__":
    sys.exit(main())
