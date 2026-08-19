#!/usr/bin/env python3
"""Единая точка выбора CLI: реестр → проба → класс данных → решение."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE / "cli_registry.json"
PD_ROLES = {
    "case-mapper", "case-reconciler", "pdf-reader", "image-reader", "docx-reader",
    "inbox-triage", "doc-drafter", "doc-reviewer", "hearing-prep", "archivist",
    "council-chair",
}
ROLE_CLASSES = {
    "hunter-leaf": "text", "council-reviewer": "text", "areopag-role": "text",
    "second-opinion": "text", "norm-lookup": "public", "infra-review": "infra",
}


def load_registry(path: Path) -> dict:
    try:
        base = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"реестр не прочитан: {e}") from e
    overlay = Path.home() / ".themis" / "cli_registry.json"
    try:
        extra = json.loads(overlay.read_text(encoding="utf-8"))
    except FileNotFoundError:
        extra = {}
    except (OSError, ValueError) as e:
        raise ValueError(f"оверлей не прочитан: {e}") from e
    if not isinstance(base, dict) or not isinstance(extra, dict):
        raise ValueError("реестр и оверлей должны быть объектами")
    for name, entry in extra.items():
        base[name] = {**base.get(name, {}), **entry}
    return base


def role_class(role: str) -> str:
    return "pd" if role in PD_ROLES else ROLE_CLASSES.get(role, "pd")


def probe(name: str, entry: dict, cache: str | None) -> tuple[bool, str]:
    cmd = [sys.executable, str(HERE / "cli_probe.py"), "--provider", name,
           "--probe-cmd", json.dumps(entry["probe"]), "--json"]
    if cache:
        cmd += ["--cache", cache]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        data = json.loads(p.stdout)
        return p.returncode == 0, data.get("outcome", "неизвестно")
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        return False, str(e)


def decide(role: str, registry: dict, cache: str | None) -> dict:
    data_class = role_class(role)
    if "claude" not in registry:
        raise ValueError("в реестре нет claude")
    skipped, available = [], []
    for name, entry in registry.items():
        missing = [key for key in ("probe", "invoke", "model", "effort", "data_classes")
                   if key not in entry]
        if missing:
            skipped.append({"name": name, "reason": "нет " + ", ".join(missing)})
            continue
        if data_class not in entry["data_classes"]:
            skipped.append({"name": name, "reason": f"не допускает класс {data_class}"})
            continue
        ok, reason = probe(name, entry, cache)
        if ok:
            available.append(name)
        else:
            skipped.append({"name": name, "reason": reason})
    if data_class == "pd":
        selected = "claude" if "claude" in available else None
    else:
        selected = next((name for name in available if name != "claude"),
                        "claude" if "claude" in available else None)
    executor = {"name": selected, **registry[selected]} if selected else None
    chain = [name for name in available if name != "claude"]
    if "claude" in available:
        chain.append("claude")
    return {"role": role, "data_class": data_class, "executor": executor,
            "chain": chain, "skipped": skipped}


def selftest() -> int:
    assert role_class("case-mapper") == "pd"
    assert role_class("hunter-leaf") == "text"
    assert role_class("not-described") == "pd", "неизвестная роль не выходит за границу"
    print("selftest пройден: классы ролей fail-closed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Выбор CLI по роли и факту доступности.")
    ap.add_argument("--role")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--cache")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.role:
        ap.error("нужен --role либо --selftest")
    try:
        answer = decide(a.role, load_registry(Path(a.registry)), a.cache)
    except ValueError as e:
        print(f"ОТКАЗ: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(answer, ensure_ascii=False))
    else:
        ex = answer["executor"] or {}
        print(f"{a.role}: {answer['data_class']} → {ex.get('name', 'нет исполнителя')}")
    return 0 if answer["executor"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
