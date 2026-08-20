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
# Харнесс и то, что делает его харнессом. Оверлей вправе подкрутить model/effort
# claude, но не вправе пересадить его на другой бинарник: pd-роль кончается
# claude, подмена харнесса запрещена (инвариант этапа 7). Иначе строка
# `{"claude": {"invoke": [...]}}` в ~/.themis/ уводит адвокатскую тайну на чужой
# бинарник — доказано пробой скептика 19.08.2026.
HARNESS = "claude"
HARNESS_LOCKED = ("invoke", "probe", "data_classes")
REQUIRED_KEYS = ("probe", "invoke", "model", "effort", "data_classes")
PD_ROLES = {
    "case-mapper", "case-reconciler", "pdf-reader", "image-reader", "docx-reader",
    "inbox-triage", "doc-drafter", "doc-reviewer", "hearing-prep", "archivist",
    "council-chair",
}
ROLE_CLASSES = {
    "hunter-leaf": "text", "council-reviewer": "text", "areopag-role": "text",
    "second-opinion": "text", "norm-lookup": "public", "infra-review": "infra",
}


def _entry_ok(name: str, entry: object) -> dict:
    """Проверяет декларацию до запуска команды из пользовательского слоя."""
    if not isinstance(name, str) or not isinstance(entry, dict):
        raise ValueError("запись реестра должна быть объектом с именем")
    for key in ("probe", "invoke"):
        value = entry.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
            raise ValueError(f"{name}: {key} должен быть непустым списком строк")
    for key in ("model", "effort"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise ValueError(f"{name}: {key} должен быть непустой строкой")
    classes = entry.get("data_classes")
    if not isinstance(classes, list) or not classes or not all(isinstance(v, str) and v for v in classes):
        raise ValueError(f"{name}: data_classes должен быть непустым списком строк")
    return entry


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
    if HARNESS not in base:
        raise ValueError("в базовом реестре нет полного claude")
    _entry_ok(HARNESS, base[HARNESS])
    for name, entry in extra.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: оверлей должен быть объектом")
        if name == HARNESS and name not in base:
            raise ValueError("оверлей не может объявить харнесс без базового claude")
        merged = {**base.get(name, {}), **entry}
        if name == HARNESS:
            for key in HARNESS_LOCKED:
                merged[key] = base[name][key]   # харнесс не пересаживается оверлеем
        else:
            classes = merged.get("data_classes")
            if isinstance(classes, list) and "pd" in classes:
                merged["data_classes"] = [c for c in classes if c != "pd"]
                if not merged["data_classes"]:
                    continue
        base[name] = merged
    for name, entry in base.items():
        _entry_ok(name, entry)
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
        missing = [key for key in REQUIRED_KEYS if key not in entry]
        if missing:
            skipped.append({"name": name, "reason": "нет " + ", ".join(missing)})
            continue
        if data_class == "pd" and name != HARNESS:
            skipped.append({"name": name, "reason": "pd допускает только claude"})
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
        selected = HARNESS if HARNESS in available else None
    else:
        selected = next((name for name in available if name != HARNESS),
                        HARNESS if HARNESS in available else None)
    executor = {"name": selected, **registry[selected]} if selected else None
    if data_class == "pd":
        chain = [HARNESS]
    else:
        chain = [name for name in available if name != HARNESS]
        if HARNESS in available:
            chain.append(HARNESS)
    return {"role": role, "data_class": data_class, "executor": executor,
            "chain": chain, "skipped": skipped}


def selftest() -> int:
    import tempfile
    assert role_class("case-mapper") == "pd"
    assert role_class("hunter-leaf") == "text"
    assert role_class("not-described") == "pd", "неизвестная роль не выходит за границу"

    # Оверлей ~/.themis/ не пересаживает харнесс: invoke/probe claude берутся из базы,
    # что бы ни лежало в пользовательском слое (проба скептика 19.08.2026).
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base = td / "reg.json"
        base.write_text(json.dumps({"claude": {
            "probe": ["real-probe"], "invoke": ["real-claude"],
            "model": "opus", "effort": "max",
            "data_classes": ["pd", "text", "public", "infra"]}}), encoding="utf-8")
        home = td / "home"
        (home / ".themis").mkdir(parents=True)
        (home / ".themis" / "cli_registry.json").write_text(json.dumps({
            "claude": {"invoke": ["evil"], "probe": ["evil"], "effort": "low"}}),
            encoding="utf-8")
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            reg = load_registry(base)
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old
        assert reg["claude"]["invoke"] == ["real-claude"], "оверлей пересадил invoke харнесса"
        assert reg["claude"]["probe"] == ["real-probe"], "оверлей пересадил probe харнесса"
        assert reg["claude"]["effort"] == "low", "оверлей не смог подкрутить effort харнесса"
        assert "pd" in reg["claude"]["data_classes"], "оверлей понизил data_classes харнесса"

        (home / ".themis" / "cli_registry.json").write_text(json.dumps({
            "zloy": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                     "data_classes": ["pd"]}}), encoding="utf-8")
        reg = load_registry(base)
        assert "zloy" not in reg, "чужой провайдер с одним pd принят в цепочку"

        base.write_text(json.dumps({"claude": "повреждено"}), encoding="utf-8")
        try:
            load_registry(base)
        except ValueError as e:
            assert "объект" in str(e), e
        else:
            raise AssertionError("поврежденный реестр принят")
        base.write_text(json.dumps({"alpha": {
            "probe": ["a"], "invoke": ["a"], "model": "a", "effort": "max",
            "data_classes": ["text"]}}), encoding="utf-8")
        (home / ".themis" / "cli_registry.json").write_text(json.dumps({"claude": {
            "probe": ["evil"], "invoke": ["evil"], "model": "c", "effort": "max",
            "data_classes": ["pd"]}}), encoding="utf-8")
        try:
            load_registry(base)
        except ValueError as e:
            assert "claude" in str(e), e
        else:
            raise AssertionError("оверлей объявил харнесс без базы")
    print("selftest пройден: классы ролей fail-closed, харнесс не пересаживается оверлеем")
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
