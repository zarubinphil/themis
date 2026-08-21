#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pd_history_audit.py — персональные данные во ВСЕЙ истории, а не только в вершине.

ЗАЧЕМ. `pd_guard --tree` ходит по `git ls-files`, а `check_push_content` на
pre-push читает blob по `commit:path` — оба смотрят ВЕРШИНУ. Публикация, которая
переписывает общую ветку, выкладывает наружу КАЖДЫЙ промежуточный объект, и это
разошлось с интуицией 21.08.2026: дерево было чисто, а имена доверителей лежали
в 39 исторических объектах, из которых 15 к тому дню были публичны уже два
месяца. Правило записано уроком в `knowledge/lessons-log.md`; здесь оно держится
прибором, потому что текст исполняется вероятностно, а код возврата — нет.

ЧТО СЧИТАЕТСЯ. Два рубежа считаются РАЗДЕЛЬНО, и это принципиально:

  · имена доверителей (`pd_guard.name_pattern` с диска) — ради них всё и
    затевается, допуск строго нулевой, в содержимом объектов, в путях файлов и
    в сообщениях коммитов (там дополнительно кириллическая транслитерация);
  · шаблоны второго рубежа (паспорт/СНИЛС/кадастр/госномер/дата рождения) —
    эвристика с намеренно избыточной маскировкой, она даёт ложные тревоги на
    обиходе. Каждая печатается адресно и сверяется с разобранным списком
    `RAZOBRANO`. Находка вне списка — красный, разбирать руками.

Исключения берутся у самого сторожа, а не выдумываются здесь:
`_is_test_fixture_code` (валидаторы реквизитов в `scripts/*.py` штатно несут
синтетические образцы — ими код и проверяют) и `_skip_strong_pii_scan`
(демо-дело и статические ассеты).

ЗНАЧЕНИЯ НЕ ПЕЧАТАЮТСЯ. Находки идут через `pd_guard.scan_text`/`scan_pii`,
которые по построению отдают «путь:строка — категория, длина». Сторож не имеет
права стать вторым каналом утечки — это то же правило, что и в `pd_guard`.

    python3 scripts/pd_history_audit.py [РЕПОЗИТОРИЙ] [--ref REF] [--json]
    python3 scripts/pd_history_audit.py --selftest

Код возврата: 0 — чисто; 1 — есть имена либо неразобранная находка второго
рубежа. Прогон полный и небыстрый (сотни объектов, минуты) — это цена того,
чтобы не публиковать вслепую.

ponytail: только stdlib и уже написанный pd_guard; своих регулярок на ПД здесь
нет — две копии правила разошлись бы, как разошлись деньги в этапе 9.22.
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pd_guard as G  # noqa: E402

# Разобранные вручную ложные тревоги второго рубежа: путь → почему это не ПД.
# Список ведётся руками: молчаливое расширение превратило бы гейт в украшение.
RAZOBRANO = {
    ".claude/commands/new-event.md":
        "строки примера вымышлены целиком (клиенты «ромашка-ооо», «техстрой-ао»), "
        "а записанная там дата в обратном порядке через дефис попадает под "
        "паспортный шаблон. Реальных данных в этих строках нет",
}


def git(repo: str, *args: str) -> bytes:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True).stdout


def audit(repo: str, ref: str = "HEAD") -> dict:
    """Полный проход по объектам и сообщениям. Значения не возвращаются."""
    names = G.client_names()
    pat_lat = G.name_pattern(names)
    pat_cyr = G.name_pattern(names, cyrillic=True)
    if not pat_lat:
        # Пустой шаблон означает «имён не нашлось на диске», а не «всё чисто».
        # Тот же fail-closed, что в pii_gate: молчаливый ноль опаснее отказа.
        return {"ok": False, "prichina": "пустой шаблон имён — fail-closed",
                "imena_commits": 0, "imena_paths": {}, "pii": {}, "nerazobrano": []}

    commits = git(repo, "rev-list", ref).decode().split()
    imena_commits = [
        c for c in commits
        if G.scan_text(git(repo, "cat-file", "-p", c).decode("utf-8", "replace"), pat_cyr, c)
    ]

    imena_paths: dict[str, list[str]] = collections.defaultdict(list)
    pii: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    objects = 0
    for line in git(repo, "rev-list", "--objects", ref).decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue                       # коммиты и корневые деревья — без пути
        sha, path = parts
        if git(repo, "cat-file", "-t", sha).decode().strip() != "blob":
            continue
        objects += 1
        text = G._visible_blob_text(path, git(repo, "cat-file", "-p", sha))
        if G.scan_text(path, pat_lat, "путь") or G.scan_text(text, pat_lat, path):
            imena_paths[path].append(sha)
        if not G._skip_strong_pii_scan(path) and not G._is_test_fixture_code(path):
            hits = G.scan_pii(text, path)
            if hits:
                pii[path].append((sha, len(hits)))

    nerazobrano = sorted(p for p in pii if p not in RAZOBRANO)
    return {
        "ok": not imena_commits and not imena_paths and not nerazobrano,
        "familiy_na_diske": len(names),
        "commits": len(commits),
        "objects": objects,
        "imena_commits": len(imena_commits),
        "imena_paths": {p: len(v) for p, v in imena_paths.items()},
        "pii": {p: sum(n for _, n in v) for p, v in pii.items()},
        "nerazobrano": nerazobrano,
    }


def report(r: dict) -> int:
    if "prichina" in r:
        print(f"✗ {r['prichina']}")
        return 1
    print(f"папок доверителей: {r['familiy_na_diske']} (значения не печатаются)")
    print(f"коммитов: {r['commits']} · с именем: {r['imena_commits']}")
    print(f"объектов: {r['objects']} · с именем: {sum(r['imena_paths'].values())}")

    if r["pii"]:
        print("\n── рубеж 2 (паспорт/СНИЛС/кадастр), разбирается адресно ──")
        for path, n in sorted(r["pii"].items()):
            if path in RAZOBRANO:
                print(f"  ЛОЖНАЯ ТРЕВОГА · {path}: {n} срабат. — {RAZOBRANO[path]}")
            else:
                print(f"  ⚠ НЕ РАЗОБРАНО · {path}: {n} срабат.")

    print()
    if r["imena_commits"] or r["imena_paths"]:
        print(f"✗ ИМЕНА В ИСТОРИИ · сообщений {r['imena_commits']}, путей {len(r['imena_paths'])}")
        for path, n in sorted(r["imena_paths"].items()):
            print(f"    {path}: {n} редакций")
        print("\nПубликовать нельзя. Чистить историю, затем прогнать снова.")
        return 1
    if r["nerazobrano"]:
        print("✗ второй рубеж дал находку вне разобранного списка — разобрать руками,")
        print("  и либо вычистить, либо внести в RAZOBRANO с причиной.")
        return 1
    print("✓ ЧИСТО: имён доверителей в истории нет; второй рубеж — только разобранные")
    return 0


def selftest() -> int:
    """Прибор обязан краснеть на подложенном имени, иначе он украшение."""
    import tempfile
    proverki = []

    def gg(repo, *a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)

    names = G.client_names()
    if not names:
        print("selftest пропущен: на диске нет папок доверителей, шаблон пуст")
        return 0
    fam = sorted(names, key=len, reverse=True)[0]

    with tempfile.TemporaryDirectory() as td:
        gg(td, "init", "-q")
        gg(td, "config", "user.email", "t@t"); gg(td, "config", "user.name", "t")
        Path(td, "a.md").write_text("обычный текст без имён\n", encoding="utf-8")
        gg(td, "add", "-A"); gg(td, "commit", "-qm", "чисто")
        proverki.append(("чистая история — зелёный", audit(td)["ok"] is True))

        # имя живёт ТОЛЬКО в промежуточном коммите и убрано следующим —
        # ровно тот случай, который вершинные проверки не видят
        Path(td, "a.md").write_text(f"дело {fam} разобрано\n", encoding="utf-8")
        gg(td, "add", "-A"); gg(td, "commit", "-qm", "промежуточный")
        Path(td, "a.md").write_text("обычный текст без имён\n", encoding="utf-8")
        gg(td, "add", "-A"); gg(td, "commit", "-qm", "вычищено")
        r = audit(td)
        proverki.append(("имя в промежуточном объекте ловится", r["ok"] is False))
        proverki.append(("вершина при этом чиста",
                         gg(td, "grep", "-q", fam, "HEAD").returncode != 0))
        proverki.append(("значение имени не печатается",
                         all(fam not in str(v) for v in r["imena_paths"])))

    for name, ok in proverki:
        print(f"  {'✓' if ok else '✗'} {name}")
    plohih = [n for n, ok in proverki if not ok]
    if plohih:
        print(f"selftest ПРОВАЛЕН: {', '.join(plohih)}")
        return 1
    print(f"selftest пройден: {len(proverki)}/{len(proverki)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ПД во всей истории git, не только в вершине")
    ap.add_argument("repo", nargs="?", default=str(Path(__file__).resolve().parent.parent),
                    help="репозиторий (по умолчанию — этот проект)")
    ap.add_argument("--ref", default="HEAD", help="что считать историей (по умолчанию HEAD)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    r = audit(a.repo, a.ref)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    return report(r)


if __name__ == "__main__":
    sys.exit(main())
