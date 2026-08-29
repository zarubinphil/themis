#!/usr/bin/env python3
"""Единая точка выбора CLI: реестр → проба → класс данных → решение."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

# Имя записи реестра — только ASCII [a-z0-9._-]. Греческая «α» в «clαude», как и
# любой не-латинский символ, в журнале неотличима от настоящего имени, а работал бы
# чужой invoke: тождество инструмента описано перечнем подмен, а перечень всех
# гомоглифов не закрыть. Все, что не из разрешенного класса, — отказ (запись молча
# выкидывается, как двойник харнесса).
_ASCII_NAME_RE = re.compile(r"^[a-z0-9._-]+$")

HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = HERE / "cli_registry.json"
# Харнесс и то, что делает его харнессом. Оверлей вправе подкрутить model/effort
# claude, но не вправе пересадить его на другой бинарник: pd-роль кончается
# claude, подмена харнесса запрещена (инвариант этапа 7). Иначе строка
# `{"claude": {"invoke": [...]}}` в ~/.themis/ уводит адвокатскую тайну на чужой
# бинарник — доказано пробой скептика 19.08.2026.
HARNESS = "claude"
HARNESS_LOCKED = ("invoke", "probe", "data_classes")
# То, что исполняет pd и что решает допуск pd, — только из эталонного реестра
# рядом со скриптом: реестр из произвольного пути (--registry) иначе подменяет
# команду харнесса, и роль класса pd исполняет чужой бинарник, а журнал пишет
# «claude» (проба круга 4 этапа 9). Проба доступности — декларация реестра,
# как у любого провайдера: ложная проба вредит доступностью, не тайной.
HARNESS_CANONICAL = ("invoke", "data_classes")
REQUIRED_KEYS = ("probe", "invoke", "model", "effort", "data_classes")

# Двойник харнесса: «Claude» по регистру, «clаude» с кириллической «а», «cláude»
# с диакритикой, «claude » с хвостовым пробелом или знаком нулевой ширины — в
# журнале неотличим от настоящего claude, в реестр такое имя не принимается ни из
# какого слоя. Невидимый двойник (пробел, U+200B) опаснее гомоглифа: диакритику
# глаз еще ловит, пустоту — нет (пробы скептика, круг 4 этапа 9).
_CONFUSABLES = str.maketrans({
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x",
    "у": "y", "н": "h", "т": "t", "м": "m", "в": "b", "к": "k",
    "з": "3", "ч": "4", "и": "u",
})


def _fold_double(name: str) -> str:
    """Имя, сведенное к канону харнесса: NFKD снимает диакритику («cláude»→claude),
    выкидываются пробелы и невидимые знаки нулевой ширины (категория Cf), гомоглифы
    складываются в латиницу, регистр снимается. Все, чем «claude» подделывают в
    журнале, не отличив глазом."""
    nfkd = unicodedata.normalize("NFKD", name)
    bare = "".join(ch for ch in nfkd if not unicodedata.combining(ch)
                   and not ch.isspace() and unicodedata.category(ch) != "Cf")
    return bare.lower().translate(_CONFUSABLES)


def _is_harness_double(name: str) -> bool:
    """Имя не равно харнессу, но складывается в «claude» после снятия регистра,
    гомоглифов, диакритики, пробелов и невидимых знаков нулевой ширины."""
    return name != HARNESS and _fold_double(name) == HARNESS


def _canonical_harness() -> dict:
    """Харнесс (invoke и классы допуска) — только из эталона рядом со скриптом.

    Реестр, переданный --registry из произвольного пути, вправе описать свои
    провайдеры, но не вправе пересадить харнесс: иначе роль класса pd исполняет
    чужой бинарник, а журнал пишет «claude» (проба скептика, круг 4 этапа 9).
    """
    try:
        canonical = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"эталонный реестр харнесса не прочитан: {e}") from e
    if not isinstance(canonical, dict):
        raise ValueError("эталонный реестр харнесса должен быть объектом")
    entry = canonical.get(HARNESS)
    if not isinstance(entry, dict):
        raise ValueError("в эталонном реестре нет claude — харнессу негде жить")
    return _entry_ok(HARNESS, entry)
PD_ROLES = {
    "case-mapper", "case-reconciler", "pdf-reader", "image-reader", "docx-reader",
    "inbox-triage", "doc-drafter", "doc-reviewer", "hearing-prep", "archivist",
    "council-chair",
}
ROLE_CLASSES = {
    "hunter-leaf": "text", "council-reviewer": "text", "areopag-role": "text",
    "second-opinion": "text", "norm-lookup": "public", "infra-review": "infra",
}
# Классы данных допуска. Роль, названная прямо классом («text»), объявляет этот
# класс сама — так вызывающий указывает данные напрямую, не заводя роль-синоним.
# Все, что не роль и не класс, остается pd (fail-closed).
DATA_CLASSES = ("pd", "text", "public", "infra")


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
    # Реестр БЕЗ claude валиден: pd-роль тогда остается без исполнителя (fail-closed
    # в decide), а text/public/infra идут на объявленных провайдерах. Харнесс не
    # обязан жить в переданном реестре — он берется из эталона (ниже). Требовать
    # его в base значило бы, что вызов с реестром одного провайдера обязан
    # переобъявлять claude, а он этого не делает (проба круга 9, --role text).
    if HARNESS in base:
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
    # Имя не из ASCII [a-z0-9._-] — отказ: не-латинский символ (греческая «α»)
    # неотличим в журнале, а перечень гомоглифов не закрыть. Судим по классу имени,
    # а не по таблице подмен. Отбрасываем молча — имя нельзя повторять в журнале.
    for name in [n for n in base if not (isinstance(n, str) and _ASCII_NAME_RE.match(n))]:
        del base[name]
    # Двойник харнесса по регистру или гомоглифу отбрасывается молча: имя
    # нельзя даже повторять в журнале — человек примет его за настоящий claude.
    for name in [n for n in base if isinstance(n, str) and _is_harness_double(n)]:
        del base[name]
    # Харнесс (invoke и классы) — из эталона рядом со скриптом: что бы ни
    # лежало в реестре из произвольного пути и в оверлее, pd исполняет только
    # эталонный invoke, и журнал не лжет. Только если реестр вообще объявил claude:
    # харнесс не привносится в реестр, который его не называл (иначе чужой claude
    # из оверлея нельзя было бы отличить от эталонного — оверлей его и не объявит,
    # это проверено выше).
    if HARNESS in base:
        harness = _canonical_harness()
        for key in HARNESS_CANONICAL:
            base[HARNESS][key] = harness[key]
    for name, entry in base.items():
        _entry_ok(name, entry)
    return base


def role_class(role: str) -> str:
    if role in PD_ROLES:
        return "pd"
    if role in ROLE_CLASSES:
        return ROLE_CLASSES[role]
    if role in DATA_CLASSES:
        return role          # роль названа прямо классом данных
    return "pd"              # неизвестная роль — fail-closed


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
    # claude может отсутствовать: тогда pd-роль остается без исполнителя (ниже
    # selected=None — fail-closed), а не-pd идут на объявленных провайдерах.
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
    assert role_class("text") == "text", "класс данных, названный ролью, не распознан"
    assert role_class("infra") == "infra" and role_class("public") == "public"

    # Оверлей ~/.themis/ не пересаживает харнесс: invoke и классы claude берутся
    # из эталонного реестра рядом со скриптом, проба — из реестра, что бы ни
    # лежало в пользовательском слое (пробы скептика 19.08.2026, круг 4).
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
        harness = _canonical_harness()
        assert reg["claude"]["invoke"] == harness["invoke"], \
            "invoke харнесса не из эталона рядом со скриптом"
        assert reg["claude"]["data_classes"] == harness["data_classes"], \
            "классы харнесса не из эталона рядом со скриптом"
        assert reg["claude"]["probe"] == ["real-probe"], \
            "проба харнесса — декларация реестра, оверлей ее не пересаживает"
        assert reg["claude"]["effort"] == "low", "оверлей не смог подкрутить effort харнесса"
        assert "pd" in reg["claude"]["data_classes"], "оверлей понизил data_classes харнесса"

        # Двойник харнесса по регистру и гомоглифу в реестр не принимается.
        (home / ".themis" / "cli_registry.json").write_text(json.dumps({
            "Claude": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                       "data_classes": ["text"]},
            "clаude": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                       "data_classes": ["text"]}}), encoding="utf-8")
        reg = load_registry(base)
        assert "Claude" not in reg, "двойник харнесса по регистру принят в реестр"
        assert "clаude" not in reg, "двойник харнесса по гомоглифу принят в реестр"
        assert HARNESS in reg, "настоящий харнесс потерян при отсеве двойников"

        # Хвостовой пробел, знак нулевой ширины и диакритика — тот же двойник,
        # в журнале он неотличим от «claude» вернее гомоглифа. Двойник класса pd
        # исполнял бы чужой invoke под именем харнесса (проба скептика, круг 4).
        (home / ".themis" / "cli_registry.json").write_text(json.dumps({
            "claude ": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                        "data_classes": ["pd", "text"]},
            "cla\u200bude": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                             "data_classes": ["text"]},
            "cláude": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                       "data_classes": ["text"]}}, ensure_ascii=False), encoding="utf-8")
        reg = load_registry(base)
        assert "claude " not in reg, "двойник харнесса по хвостовому пробелу принят"
        assert "cla\u200bude" not in reg, "двойник по знаку нулевой ширины принят"
        assert "cláude" not in reg, "двойник по диакритике принят в реестр"
        assert HARNESS in reg, "настоящий харнесс потерян при отсеве невидимых двойников"
        # Ложная тревога: провайдер с ВИДИМЫМИ добавочными знаками — не двойник,
        # иначе легитимный claude-fast/claude-code выпал бы из реестра.
        assert not _is_harness_double("claude-fast"), "легитимный claude-fast принят за двойника"
        assert not _is_harness_double("claude-code"), "легитимный claude-code принят за двойника"

        # Имя с не-ASCII символом (греческая «α») — отказ по классу имени, а не по
        # таблице подмен: гомоглиф-двойник харнесса не попадает в реестр вовсе.
        (home / ".themis" / "cli_registry.json").write_text(json.dumps({
            "clαude": {"probe": ["z"], "invoke": ["z"], "model": "z", "effort": "max",
                            "data_classes": ["text"]}}, ensure_ascii=False), encoding="utf-8")
        reg = load_registry(base)
        assert "clαude" not in reg, "имя с греческой «α» принято в реестр"
        assert HARNESS in reg, "настоящий харнесс потерян при отсеве не-ASCII имени"
        assert _ASCII_NAME_RE.match("claude-fast") and not _ASCII_NAME_RE.match("clαude"), \
            "класс имени судит не по ASCII"

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
        # Реестр БЕЗ claude валиден: харнесс не привносится в реестр, который его
        # не называл; pd-роль тогда остается без исполнителя (fail-closed), а не-pd
        # идут на объявленных провайдерах. HOME ведем к пустому оверлею явно —
        # иначе читался бы боевой ~/.themis.
        base.write_text(json.dumps({"alpha": {
            "probe": ["a"], "invoke": ["a"], "model": "a", "effort": "max",
            "data_classes": ["text"]}}), encoding="utf-8")
        (home / ".themis" / "cli_registry.json").write_text("{}", encoding="utf-8")
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            reg = load_registry(base)
            assert "claude" not in reg, "claude привнесен в реестр, где его не объявляли"
            assert "alpha" in reg, "провайдер без claude потерян"
            # Оверлей НЕ вправе объявить харнесс, когда база его не определяет.
            (home / ".themis" / "cli_registry.json").write_text(json.dumps({"claude": {
                "probe": ["evil"], "invoke": ["evil"], "model": "c", "effort": "max",
                "data_classes": ["pd"]}}), encoding="utf-8")
            try:
                load_registry(base)
            except ValueError as e:
                assert "claude" in str(e), e
            else:
                raise AssertionError("оверлей объявил харнесс без базы")
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old
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
