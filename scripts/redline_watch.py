#!/usr/bin/env python3
"""redline_watch.py — найти документы, которые доверитель правил после выдачи.

База «ДО» — неизменяемый снимок в `03_drafts/_baselines/<имя>.docx`, его кладет
`create_docx.py save()`. Если выданный файл отличается от снимка, значит его
правил человек, и правки надо разобрать: чему-то они учат (`knowledge/redlines.md`),
иначе `doc-drafter` повторит тот же огрех в следующем документе.

Обнаружение полностью детерминированное — сравнение байтов, ноль токенов.
Модель зовется только когда есть что разбирать (см. `scripts/redline-watch.sh`).

    python3 scripts/redline_watch.py               # правки за последние 8 дней
    python3 scripts/redline_watch.py --days 30     # другое окно
    python3 scripts/redline_watch.py --all         # без ограничения по дате
    python3 scripts/redline_watch.py --json        # для скриптов

Код возврата: 0 — правки найдены, 1 — правок нет. Так `if` в shell читается прямо.
"""

import argparse
import filecmp
import json
import sys
import time
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent / "cases"
DEFAULT_DAYS = 8  # неделя плюс сутки запаса: понедельник видит всю прошлую неделю


def find_edited(days=None):
    """Документы, отличающиеся от своего baseline. Возвращает список словарей."""
    cutoff = None if days is None else time.time() - days * 86400
    found = []

    for doc in sorted(CASES.glob("*/*/03_drafts/*.docx")):
        if doc.parent.name == "_baselines" or doc.name.startswith("~$"):
            continue
        baseline = doc.parent / "_baselines" / doc.name
        if not baseline.exists():
            continue  # выдан до появления механизма снимков — сравнивать не с чем
        try:
            if filecmp.cmp(doc, baseline, shallow=False):
                continue  # не тронут
            mtime = doc.stat().st_mtime
        except OSError as e:
            print(f"ВНИМАНИЕ: {doc} не прочитан ({e})", file=sys.stderr)
            continue
        if cutoff is not None and mtime < cutoff:
            continue

        case_dir = doc.parent.parent
        found.append({
            "document": doc.name,
            "case": f"{case_dir.parent.name}/{case_dir.name}",
            "case_path": str(case_dir),
            "edited": time.strftime("%d.%m.%Y %H:%M", time.localtime(mtime)),
            "mtime": mtime,
            "path": str(doc),
            "baseline": str(baseline),
        })

    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"окно в днях (по умолчанию {DEFAULT_DAYS})")
    ap.add_argument("--all", action="store_true", help="без ограничения по дате")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    args = ap.parse_args()

    found = find_edited(None if args.all else args.days)

    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
    elif not found:
        window = "за все время" if args.all else f"за {args.days} дн."
        print(f"Правок доверителя {window} не найдено.")
    else:
        print(f"Документов с правками доверителя: {len(found)}\n")
        for f in found:
            print(f"  {f['edited']}  {f['case']}")
            print(f"      {f['document']}")
        print("\nРазобрать: «изучи мои правки по {дело}» для каждого.")

    return 0 if found else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # Проверка логики отбора на временных файлах, без обращения к делам.
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cases" / "client" / "case-2026" / "03_drafts"
            (root / "_baselines").mkdir(parents=True)
            edited, untouched = root / "a.docx", root / "b.docx"
            edited.write_text("ПОСЛЕ правок доверителя", encoding="utf-8")
            (root / "_baselines" / "a.docx").write_text("ДО правок", encoding="utf-8")
            untouched.write_text("не тронут", encoding="utf-8")
            shutil.copy2(untouched, root / "_baselines" / "b.docx")
            globals()["CASES"] = Path(td) / "cases"
            res = find_edited(None)
            assert len(res) == 1, f"ожидался 1 документ, получено {len(res)}"
            assert res[0]["document"] == "a.docx", res
            assert res[0]["case"] == "client/case-2026", res
            assert find_edited(0) == [], "окно 0 дней должно отсекать все"
        print("selftest: OK")
        sys.exit(0)
    sys.exit(main())
