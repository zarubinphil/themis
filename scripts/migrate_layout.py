#!/usr/bin/env python3
"""migrate_layout.py — переезд дел на два слоя. Этап 3 плана FINAL-PLAN-2026-08-18.

Раскладку задаёт `case_paths.py`; этот прибор приводит к ней диск и код ОДНИМ заходом.
Разводить по времени нельзя: система окажется в состоянии «новые папки, старая логика».

Что делает:
  1. манифест ДО — контрольные суммы всего дерева `cases/`;
  2. проверка коллизий имён после нормализации NFC (macOS хранит NFD, Linux и git NFC:
     непереведённое имя на сервере становится другим путём);
  3. переезд каталогов дела: `01_context` → `.agent/context`, `03_drafts` → `.agent/drafts`,
     `04_archive` → `.agent/archive`. `00_intake` и `02_hearings` НЕ ТРОГАЮТСЯ;
  4. правка путей в коде и промптах — по списку, посчитанному с диска, а не по памяти;
  5. манифест ПОСЛЕ и сверка: ни один файл не потерян, содержимое не изменилось,
     неприкосновенные зоны совпадают побайтово.

Журнал переезда — `.autoloop/migration.jsonl`; по нему работает `--rollback`.

  --dry-run   (по умолчанию) показать всё, не тронуть ничего
  --apply     выполнить
  --verify    сверить манифесты и раскладку после переезда
  --rollback  вернуть каталоги на место по журналу
  --selftest  проверка на синтетике

Выход: 0 — сделано и сверено; 1 — расхождение или отказ; 2 — переезжать нечего.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_paths as cp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "cases"
JOURNAL = ROOT / ".autoloop" / "migration.jsonl"
UNTOUCHABLE = (cp.INTAKE, cp.HEARINGS)
# Где правятся литералы путей. Производное (.codex, .agents) не трогаем — оно
# генерируется sync_prompts из канона после правки канона.
CODE_GLOBS = ("scripts/*.py", "scripts/*.sh", "cockpit/*.py", "AGENTS.md",
              ".claude/**/*.md", "knowledge/redlines.md")
# Исторические записи НЕ правятся: путь в датированном прецеденте — часть факта.
# Переписать «файл лежал в 01_context» задним числом значит исказить прецедент,
# по которому потом принимают решение. Правится только операционный текст.
HISTORICAL = ("knowledge/lessons-log.md", "knowledge/improvements-backlog.md",
              "knowledge/optimization-plan.md", "knowledge/practice_index.md")
SKIP_FILES = {"scripts/case_paths.py", "scripts/migrate_layout.py"} | set(HISTORICAL)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root=CASES):
    """{относительный путь: sha256}. Ключ нормализован в NFC — иначе манифесты
    macOS и Linux не сойдутся на одних и тех же файлах."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = unicodedata.normalize("NFC", os.path.relpath(full, root))
            try:
                out[rel] = sha256(full)
            except OSError:
                continue
    return out


def all_cases(root=CASES):
    """Каталоги дел: `cases/<клиент>/<дело>`. Служебные (с `_`) и точка-каталоги мимо."""
    out = []
    if not root.is_dir():
        return out
    for client in sorted(root.iterdir()):
        if not client.is_dir() or client.name.startswith((".", "_")):
            continue
        for case in sorted(client.iterdir()):
            if case.is_dir() and not case.name.startswith((".", "_")):
                out.append(case)
    return out


def check_collisions(root=CASES):
    """Имена, схлопывающиеся после нормализации. Непусто — переезд запрещён:
    второе дело затрёт первое молча."""
    bad = []
    for dirpath, dirnames, filenames in os.walk(root):
        c = cp.collisions(dirnames + filenames)
        for key, names in c.items():
            bad.append((os.path.relpath(dirpath, root), key, names))
    return bad


def code_sites(root=ROOT):
    """Файлы кода и промптов со старыми литералами: {путь: число вхождений}."""
    sites = {}
    for pattern in CODE_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = str(path.relative_to(root))
            if not path.is_file() or rel in SKIP_FILES or rel.startswith("knowledge/FINAL-PLAN") \
                    or rel.startswith("knowledge/MASTER-PLAN") or "-plan-" in rel:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            n = sum(text.count(old) for old, _ in cp.LEGACY)
            if n:
                sites[rel] = n
    return sites


def plan_moves(root=CASES):
    """[(дело, [(источник, назначение)])] по всем делам, требующим переезда."""
    out = []
    for case in all_cases(root):
        moves = cp.migration_moves(case)
        if moves:
            out.append((case, moves))
    return out


def backup_moving(moves_by_case, dest):
    """Копия переезжающих каталогов ДО переезда, с проверкой сумм.

    `intake_backup.py` покрывает только `00_intake`. Переезжают другие три каталога —
    карта, практика, позиция, черновики, снимки. `os.rename` в пределах тома не
    копирует данные и потому безопасен, но правило «не менять без бэкапа» держится
    копией, а не рассуждением о безопасности переименования.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied, bad = 0, []
    for case, moves in moves_by_case:
        for src, _ in moves:
            rel = src.relative_to(CASES)
            dst = dest / rel
            for dirpath, _, files in os.walk(src):
                out = dst / Path(dirpath).relative_to(src)
                out.mkdir(parents=True, exist_ok=True)
                for name in files:
                    a, b = os.path.join(dirpath, name), out / name
                    if b.is_file() and b.stat().st_size == os.path.getsize(a):
                        copied += 1
                        continue
                    shutil.copy2(a, b)
                    if sha256(a) != sha256(b):
                        bad.append(str(Path(dirpath, name).relative_to(CASES)))
                    copied += 1
    return copied, bad


def plan_promote(root=CASES):
    """Выданные `.docx` из корня черновиков → `GOTOVO/`: [(источник, назначение)].

    В прежней модели `.docx` пересобирался каждый раунд и лежал в корне `03_drafts/` —
    именно его открывал доверитель, а `_baselines/` хранил снимок. Значит корневой
    `.docx` и есть выданный документ, и его место в слое человека. Снимки, рабочие
    файлы и локи Word остаются в кухне.
    """
    out = []
    for case in all_cases(root):
        d = cp.drafts(case)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.docx")):
            if f.name.startswith("~$"):
                continue
            out.append((f, cp.ready(case) / f.name))
    return out


def do_promote(pairs):
    done = []
    for src, dst in pairs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise RuntimeError(f"в папке готовых уже есть файл: {dst}")
        os.rename(src, dst)
        done.append((str(src), str(dst)))
        journal({"event": "promote", "src": str(src), "dst": str(dst)})
    return done


def journal(entry):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def do_moves(moves_by_case):
    done = []
    for case, moves in moves_by_case:
        cp.agent_root(case).mkdir(parents=True, exist_ok=True)
        cp.ready(case).mkdir(parents=True, exist_ok=True)
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                raise RuntimeError(f"назначение уже занято: {dst}")
            os.rename(src, dst)
            done.append((str(src), str(dst)))
            journal({"event": "move", "src": str(src), "dst": str(dst)})
    return done


def do_code(root=ROOT):
    changed = {}
    for rel, _ in sorted(code_sites(root).items()):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new = cp.modernize(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed[rel] = sum(text.count(old) for old, _ in cp.LEGACY)
            journal({"event": "code", "file": rel, "sites": changed[rel]})
    return changed


def promoted_map(root=CASES):
    """Перенос выданных документов из журнала: {старый относительный путь: новый}.

    Без него `--verify` объявляет потерянными 193 документа, которые лежат в GOTOVO/:
    прибор, врущий о потере, хуже отсутствующего — на него перестают смотреть.
    """
    out = {}
    if not JOURNAL.is_file():
        return out
    for line in open(JOURNAL, encoding="utf-8"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") != "promote":
            continue
        try:
            a = os.path.relpath(e["src"], root)
            b = os.path.relpath(e["dst"], root)
        except (KeyError, ValueError):
            continue
        out[unicodedata.normalize("NFC", a)] = unicodedata.normalize("NFC", b)
    return out


def verify(before, root=CASES, renamed=True):
    """Ни один файл не потерян, содержимое не изменилось, неприкосновенные зоны целы.

    `renamed=False` — сверка ПОСЛЕ ОТКАТА: на диске снова старая раскладка, и ключи
    манифеста брать как есть. Иначе откат всегда выглядит расхождением, и настоящую
    поломку в нём не отличить от нормы.
    """
    after = manifest(root)
    problems = []

    promoted = promoted_map(root) if renamed else {}

    def rename_key(rel):
        key = unicodedata.normalize("NFC", cp.modernize(rel) if renamed else rel)
        return promoted.get(key, key)

    expected = {rename_key(k): v for k, v in before.items()}
    if len(expected) != len(before):
        problems.append("переименование схлопнуло разные файлы в один путь")
    for rel, digest in sorted(expected.items()):
        if rel not in after:
            problems.append(f"{rel}: файл потерян при переезде")
        elif after[rel] != digest:
            problems.append(f"{rel}: содержимое изменилось")
    for rel in sorted(set(after) - set(expected)):
        problems.append(f"{rel}: появился неизвестно откуда")

    for rel, digest in before.items():
        parts = rel.split(os.sep)
        if any(z in parts for z in UNTOUCHABLE):
            n = unicodedata.normalize("NFC", rel)
            if after.get(n) != digest:
                problems.append(f"{rel}: НЕПРИКОСНОВЕННАЯ зона изменена")
    return problems


def rollback():
    if not JOURNAL.is_file():
        return ["журнала переезда нет — откатывать нечего"]
    entries = [json.loads(l) for l in open(JOURNAL, encoding="utf-8")]
    # Откат обязан отменить ВСЁ, что сделал прибор: и переезд каталогов, и перенос
    # выданных документов в слой человека. Половинчатый откат оставляет систему
    # в третьем состоянии, которого не было ни до, ни после.
    steps = [m for m in entries if m.get("event") in ("move", "promote")]
    moves = [m for m in steps if m.get("event") == "move"]
    problems = []
    for m in reversed(steps):
        src, dst = Path(m["src"]), Path(m["dst"])
        if not dst.exists():
            problems.append(f"{dst}: нечего возвращать")
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        os.rename(dst, src)
    # Пустые каркасы, созданные переездом, убрать: оставленный `.agent/` заставит
    # `is_migrated` считать откаченное дело переехавшим.
    for m in moves:
        case = Path(m["src"]).parent
        for leftover in (cp.agent_root(case), cp.ready(case)):
            try:
                for d in sorted((p for p in leftover.rglob("*") if p.is_dir()),
                                key=lambda p: -len(p.parts)):
                    d.rmdir()
                leftover.rmdir()
            except OSError:
                pass          # непусто — значит там что-то живое, не трогаем
    return problems


def _promote_checks():
    """Второй шаг переезда: выданный документ уходит в слой человека, снимок остаётся.

    В своём дереве: блок добавляет файлы, которых нет в манифесте ДО основного
    блока, и смешивать их значит ловить ложное расхождение вместо настоящего.
    """
    import tempfile
    global JOURNAL
    with tempfile.TemporaryDirectory(prefix="migrate-promote-") as tmp:
        root = Path(tmp)
        cases = root / "cases"
        case = cases / "ivanov-ivan" / "delo-2026"
        (case / ".agent" / "drafts" / "_baselines").mkdir(parents=True)
        (case / "GOTOVO").mkdir(parents=True)
        (case / ".agent" / "drafts" / "isk.docx").write_text("документ", encoding="utf-8")
        (case / ".agent" / "drafts" / "~$isk.docx").write_text("лок", encoding="utf-8")
        (case / ".agent" / "drafts" / "_baselines" / "isk.docx").write_text(
            "снимок", encoding="utf-8")

        saved, JOURNAL = JOURNAL, root / "journal.jsonl"
        try:
            before = manifest(cases)
            pairs = plan_promote(cases)
            assert len(pairs) == 1, f"к переносу {len(pairs)} файлов вместо одного"
            assert pairs[0][0].name == "isk.docx", "лок Word попал в перенос"
            do_promote(pairs)
            assert (case / "GOTOVO" / "isk.docx").is_file(), "документ не попал к человеку"
            assert (case / ".agent" / "drafts" / "_baselines" / "isk.docx").is_file(), \
                "снимок утащило из кухни"
            assert not (case / ".agent" / "drafts" / "isk.docx").exists(), \
                "документ остался и в кухне тоже"
            problems = verify(before, cases)
            assert not problems, f"перенос признан потерей файлов: {problems[:3]}"
            assert plan_promote(cases) == [], "перенос не идемпотентен"

            rollback()
            assert (case / ".agent" / "drafts" / "isk.docx").is_file(), \
                "откат не вернул документ в кухню"
            assert not verify(before, cases, renamed=False), \
                "откаченный перенос не совпал с состоянием ДО"
        finally:
            JOURNAL = saved


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="migrate-selftest-") as tmp:
        root = Path(tmp)
        cases = root / "cases"
        case = cases / "ivanov-ivan" / "delo-2026"
        for d in ("01_context/_working", "03_drafts/_baselines", "04_archive",
                  "00_intake", "02_hearings"):
            (case / d).mkdir(parents=True)
        (case / "01_context" / "knowledge-map.md").write_text("карта", encoding="utf-8")
        (case / "03_drafts" / "isk.md").write_text("иск", encoding="utf-8")
        (case / "00_intake" / "скан.pdf").write_bytes(b"%PDF-1.4 nedotroga")
        (case / "02_hearings" / "sobytie.md").write_text("событие", encoding="utf-8")
        (cases / "_templates").mkdir()

        assert len(all_cases(cases)) == 1, "служебный каталог принят за дело"
        moves = plan_moves(cases)
        assert len(moves) == 1 and len(moves[0][1]) == 3
        assert not check_collisions(cases), "ложная коллизия на чистом дереве"

        before = manifest(cases)
        assert len(before) == 4, f"манифест видит {len(before)} файлов вместо 4"
        global JOURNAL
        saved, JOURNAL = JOURNAL, root / "journal.jsonl"
        try:
            do_moves(moves)
            problems = verify(before, cases)
            assert not problems, f"чистый переезд признан сломанным: {problems}"
            assert (case / ".agent" / "context" / "knowledge-map.md").is_file()
            assert (case / "GOTOVO").is_dir(), "папка готовых не создана"
            assert (case / "00_intake" / "скан.pdf").read_bytes() == b"%PDF-1.4 nedotroga"
            assert not (case / "01_context").exists(), "старый каталог остался"

            # Потеря файла обязана быть пойманной
            (case / ".agent" / "drafts" / "isk.md").unlink()
            assert any("потерян" in p for p in verify(before, cases)), "потеря файла не поймана"
            (case / ".agent" / "drafts" / "isk.md").write_text("иск", encoding="utf-8")
            assert not verify(before, cases), "восстановленный файл всё ещё числится потерянным"

            # Подмена содержимого обязана быть пойманной
            (case / ".agent" / "drafts" / "isk.md").write_text("подмена", encoding="utf-8")
            assert any("изменилось" in p for p in verify(before, cases)), "подмена не поймана"
            (case / ".agent" / "drafts" / "isk.md").write_text("иск", encoding="utf-8")

            # Касание неприкосновенной зоны — отдельным диагнозом
            (case / "00_intake" / "скан.pdf").write_bytes(b"podlog")
            assert any("НЕПРИКОСНОВЕННАЯ" in p for p in verify(before, cases)), \
                "правка 00_intake не названа неприкосновенной зоной"
            (case / "00_intake" / "скан.pdf").write_bytes(b"%PDF-1.4 nedotroga")

            assert plan_moves(cases) == [], "переезд не идемпотентен"
            assert not rollback(), "откат сообщил о проблемах на чистом журнале"
            assert (case / "01_context" / "knowledge-map.md").is_file(), "откат не вернул каталог"
            assert not (case / ".agent").exists(), "откат оставил пустой каркас .agent"
            assert not (case / "GOTOVO").exists(), "откат оставил пустую папку готовых"
            assert not verify(before, cases, renamed=False), \
                "откаченное дерево не совпало с состоянием ДО"
        finally:
            JOURNAL = saved

    # Коллизия имён блокирует переезд
    with tempfile.TemporaryDirectory(prefix="migrate-collision-") as tmp:
        d = Path(tmp) / "cases" / "klient" / "delo"
        d.mkdir(parents=True)
        (d / unicodedata.normalize("NFD", "йогурт.md")).write_text("a", encoding="utf-8")
        try:
            (d / unicodedata.normalize("NFC", "йогурт.md")).write_text("b", encoding="utf-8")
        except OSError:
            pass
        names = os.listdir(d)
        if len(names) == 2:
            assert check_collisions(Path(tmp) / "cases"), "коллизия NFD/NFC не поймана"
    assert all(h in SKIP_FILES for h in HISTORICAL), "исторические записи не защищены от правки"
    assert "knowledge/lessons-log.md" not in code_sites(ROOT), \
        "лог уроков попал под автоправку — прецеденты будут искажены"
    _promote_checks()
    print("selftest: план переезда, неприкосновенные зоны, детект потери/подмены, "
          "идемпотентность, откат, коллизии имён, защита исторических записей — ок")
    return 0


def report(root=ROOT):
    moves = plan_moves(CASES)
    sites = code_sites(root)
    coll = check_collisions(CASES)
    total_dirs = sum(len(m) for _, m in moves)
    print(f"ПЕРЕЕЗД НА ДВА СЛОЯ · план\n")
    print(f"  дел к переезду:        {len(moves)}")
    print(f"  каталогов переедет:    {total_dirs}")
    print(f"  файлов кода и промптов:{len(sites):4}  (вхождений {sum(sites.values())})")
    print(f"  коллизий имён:         {len(coll)}")
    print(f"\n  НЕ ТРОГАЕТСЯ: {', '.join(UNTOUCHABLE)}")
    print(f"  НЕ ПРАВИТСЯ (исторические записи, путь в них — часть факта):")
    for h in HISTORICAL:
        print(f"      {h}")
    print(f"  раскладка после: {' · '.join(cp.HUMAN_VISIBLE)} + "
          f"{cp.AGENT_DIR}/{{context,drafts,archive}}")
    if coll:
        print("\n⛔ КОЛЛИЗИИ ИМЁН — переезд запрещён:")
        for d, key, names in coll[:10]:
            print(f"  · {d}: {names} → одно имя {key}")
        return 1
    if not moves and not sites:
        print("\n✓ переезжать нечего — раскладка уже новая")
        return 2
    print("\nфайлы кода и промптов под правку (вхождений):")
    for rel, n in sorted(sites.items(), key=lambda x: -x[1]):
        print(f"  {n:3}  {rel}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Переезд дел на два слоя (этап 3).")
    ap.add_argument("--apply", action="store_true", help="выполнить переезд")
    ap.add_argument("--verify", action="store_true", help="сверить по манифесту ДО")
    ap.add_argument("--rollback", action="store_true", help="вернуть каталоги по журналу")
    ap.add_argument("--promote-ready", action="store_true",
                    help="выданные .docx из черновиков → GOTOVO/ (второй шаг переезда)")
    ap.add_argument("--manifest", metavar="FILE", help="куда положить манифест ДО")
    ap.add_argument("--backup-dir", default=os.path.join(
        os.path.expanduser("~"), "Хранилище", "themis-layout-backup"),
        help="куда копировать переезжающие каталоги до переезда")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.rollback:
        problems = rollback()
        print("откат: " + ("готов" if not problems else f"проблем {len(problems)}"))
        for p in problems:
            print("  · " + p)
        return 1 if problems else 0
    if a.promote_ready:
        pairs = plan_promote()
        if not pairs:
            print("переносить нечего — выданных .docx в корне черновиков нет")
            return 2
        before = manifest()
        print(f"выданных документов к переносу: {len(pairs)}")
        if not a.apply:
            for src, dst in pairs[:10]:
                print(f"  {src.relative_to(CASES)} → {dst.relative_to(CASES)}")
            print(f"  … всего {len(pairs)}. Выполнить: --promote-ready --apply")
            return 0
        done = do_promote(pairs)
        problems = [x for x in verify(before, renamed=False)
                    if "потерян" not in x and "появился" not in x]
        moved_ok = all(Path(d).is_file() for _, d in done)
        if problems or not moved_ok:
            print(f"❌ перенос не сошёлся: {len(problems)}")
            for x in problems[:20]:
                print("  · " + x)
            return 1
        print(f"✓ перенесено в GOTOVO: {len(done)} документов, содержимое не изменилось")
        return 0

    if a.verify:
        path = a.manifest or (ROOT / ".autoloop" / "manifest_before.json")
        if not os.path.isfile(path):
            print(f"манифеста ДО нет: {path}", file=sys.stderr)
            return 1
        before = json.load(open(path, encoding="utf-8"))
        problems = verify(before)
        if problems:
            print(f"❌ расхождений: {len(problems)}")
            for p in problems[:40]:
                print("  · " + p)
            return 1
        print(f"✓ сверено по манифесту: {len(before)} файлов, содержимое и "
              f"неприкосновенные зоны совпадают")
        return 0
    if not a.apply:
        return report()

    coll = check_collisions(CASES)
    if coll:
        print(f"⛔ переезд запрещён: коллизий имён {len(coll)}", file=sys.stderr)
        return 1
    moves = plan_moves(CASES)
    sites = code_sites(ROOT)
    path = a.manifest or (ROOT / ".autoloop" / "manifest_before.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    before = manifest()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(before, f, ensure_ascii=False)
    journal({"event": "start", "cases": len(moves), "sites": len(sites),
             "manifest": str(path), "files": len(before)})
    print(f"манифест ДО: {len(before)} файлов → {path}")
    copied, bad = backup_moving(moves, a.backup_dir)
    if bad:
        print(f"⛔ переезд отменён: копия разошлась с оригиналом на {len(bad)} файлах",
              file=sys.stderr)
        for x in bad[:10]:
            print("  · " + x, file=sys.stderr)
        return 1
    print(f"копия переезжающего: {copied} файлов → {a.backup_dir}")
    journal({"event": "backup", "files": copied, "dest": a.backup_dir})
    done = do_moves(moves)
    print(f"переехало каталогов: {len(done)} в {len(moves)} делах")
    changed = do_code(ROOT)
    print(f"правлено файлов кода и промптов: {len(changed)}")
    problems = verify(before)
    journal({"event": "done", "moved": len(done), "code": len(changed),
             "problems": len(problems)})
    if problems:
        print(f"\n❌ сверка не сошлась: {len(problems)} — откат `--rollback`")
        for p in problems[:40]:
            print("  · " + p)
        return 1
    print(f"\n✓ переезд сверен: {len(before)} файлов на месте, содержимое не изменилось, "
          f"{'/'.join(UNTOUCHABLE)} побайтово те же")
    return 0


if __name__ == "__main__":
    sys.exit(main())
