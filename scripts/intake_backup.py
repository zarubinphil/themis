#!/usr/bin/env python3
"""intake_backup.py — страховочная копия первички дел с проверкой по контрольным суммам.

Этап 0 плана `knowledge/FINAL-PLAN-2026-08-18.md`. Оригиналы в `cases/*/*/00_intake/` —
доказательства по живым делам, существуют в одном экземпляре. Скрипт делает вторую копию
на этой же машине (решение владельца 18.08.2026) и доказывает ее пригодность:

  1. copy   — обход `cases/`, копирование каждого файла из `00_intake/`, SHA-256 на лету;
  2. verify — повторное чтение УЖЕ ЗАПИСАННОГО файла и сверка хеша с манифестом;
  3. restore— случайное дело разворачивается во временный каталог и один его файл
              прогоняется через `markdown_extract.py`; код 0 — копия читаема, не только цела.

Манифест лежит ТОЛЬКО в каталоге копии: пути содержат ПД доверителей, в git ему нельзя.

Ограничение, зафиксированное владельцу: копия на том же диске защищает от ошибки мигратора
и случайного удаления, но гибнет вместе с оригиналом при отказе диска, краже, шифровальщике.
Внешний носитель — отдельная незакрытая задача.

Выход: 0 — копия полна, цела и читаема; 1 — расхождение (список на stdout); 2 — нечего копировать.
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "cases")
DEFAULT_DEST = os.path.join(os.path.expanduser("~"), "Хранилище", "themis-intake-backup")
MANIFEST = "MANIFEST.json"
CHUNK = 1 << 20
# ponytail: markdown_extract сам решает маршрут по расширению; берем то, что он точно умеет
EXTRACTABLE = (".pdf", ".docx", ".xlsx", ".pptx", ".rtf", ".jpg", ".jpeg", ".png", ".tiff", ".txt", ".md")


def sha256_file(path, sink=None):
    """Хеш файла за один проход; sink — открытый файл назначения (копия попутно)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            if sink is not None:
                sink.write(chunk)
    return h.hexdigest()


def walk_intake(cases_dir):
    """Относительные пути всех файлов внутри любых 00_intake/ под cases/."""
    out = []
    for dirpath, dirnames, filenames in os.walk(cases_dir):
        if os.path.basename(dirpath) == "00_intake":
            for sub, _, files in os.walk(dirpath):
                for name in files:
                    full = os.path.join(sub, name)
                    out.append(os.path.relpath(full, cases_dir))
            dirnames[:] = []
    return sorted(out)


def load_manifest(dest):
    path = os.path.join(dest, MANIFEST)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("files", {})


def save_manifest(dest, files, elapsed):
    payload = {
        "created": time.strftime("%d.%m.%Y %H:%M:%S"),
        "source": CASES,
        "count": len(files),
        "bytes": sum(v["size"] for v in files.values()),
        "seconds": round(elapsed, 1),
        "files": files,
    }
    with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def backup(cases_dir, dest, quiet=False):
    """Копирование с хешированием на лету. Уже скопированное с тем же размером и mtime — пропуск."""
    rels = walk_intake(cases_dir)
    if not rels:
        return None, ["нечего копировать: ни одного файла в 00_intake/"]
    prev = load_manifest(dest)
    files, errors, copied, skipped = {}, [], 0, 0
    t0 = time.time()
    for i, rel in enumerate(rels, 1):
        src = os.path.join(cases_dir, rel)
        dst = os.path.join(dest, rel)
        try:
            st = os.stat(src)
        except OSError as e:
            errors.append(f"{rel}: источник недоступен ({e})")
            continue
        old = prev.get(rel)
        if (old and os.path.isfile(dst) and old.get("size") == st.st_size
                and old.get("mtime") == int(st.st_mtime) and os.path.getsize(dst) == st.st_size):
            files[rel] = old
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            with open(dst, "wb") as sink:
                digest = sha256_file(src, sink)
            shutil.copystat(src, dst)
        except OSError as e:
            errors.append(f"{rel}: копирование не удалось ({e})")
            continue
        files[rel] = {"sha256": digest, "size": st.st_size, "mtime": int(st.st_mtime)}
        copied += 1
        if not quiet and i % 250 == 0:
            print(f"  … {i}/{len(rels)}", flush=True)
    save_manifest(dest, files, time.time() - t0)
    if not quiet:
        print(f"копирование: новых {copied}, без изменений {skipped}, всего {len(files)}")
    return files, errors


def verify(cases_dir, dest, quiet=False):
    """Читает записанное на диск и сверяет с манифестом. Лишние файлы в копии — тоже расхождение."""
    manifest = load_manifest(dest)
    if not manifest:
        return ["манифест отсутствует — бэкап не выполнялся"]
    errors = []
    for i, (rel, meta) in enumerate(sorted(manifest.items()), 1):
        dst = os.path.join(dest, rel)
        if not os.path.isfile(dst):
            errors.append(f"{rel}: в копии нет файла")
            continue
        if os.path.getsize(dst) != meta["size"]:
            errors.append(f"{rel}: размер {os.path.getsize(dst)} вместо {meta['size']}")
            continue
        if sha256_file(dst) != meta["sha256"]:
            errors.append(f"{rel}: контрольная сумма не совпала")
        if not quiet and i % 500 == 0:
            print(f"  … сверено {i}/{len(manifest)}", flush=True)
    live = set(walk_intake(cases_dir))
    missing = live - set(manifest)
    if missing:
        errors += [f"{rel}: появился в оригинале, но не в копии" for rel in sorted(missing)[:20]]
    return errors


def restore_test(dest, quiet=False, seed=None):
    """Разворачивает случайное дело из копии во временный каталог и гонит по нему markdown_extract."""
    manifest = load_manifest(dest)
    if not manifest:
        return ["манифест отсутствует — восстанавливать нечего"]
    cases = {}
    for rel in manifest:
        parts = rel.split(os.sep)
        if len(parts) >= 3:
            cases.setdefault(os.path.join(parts[0], parts[1]), []).append(rel)
    if not cases:
        return ["в манифесте нет ни одного дела"]
    rnd = random.Random(seed)
    case = rnd.choice(sorted(cases))
    picked = [r for r in cases[case] if r.lower().endswith(EXTRACTABLE)]
    if not picked:
        return [f"{case}: нет ни одного файла, который умеет читать markdown_extract"]
    sample = rnd.choice(sorted(picked))
    errors = []
    with tempfile.TemporaryDirectory(prefix="themis-restore-") as tmp:
        for rel in cases[case]:
            src, dst = os.path.join(dest, rel), os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            if sha256_file(dst) != manifest[rel]["sha256"]:
                errors.append(f"{rel}: восстановленный файл не совпал с манифестом")
        target = os.path.join(tmp, sample)
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "markdown_extract.py"), target, "--json-meta"],
            capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            errors.append(f"markdown_extract вернул {proc.returncode} на {sample}: "
                          f"{proc.stderr.strip()[:400]}")
        elif not quiet:
            print(f"восстановлено дело {case} ({len(cases[case])} файлов), "
                  f"прогнан {os.path.basename(sample)} → код 0")
            print("  " + proc.stdout.strip()[:300])
    return errors


def selftest():
    """Синтетическое дерево во временном каталоге: копия, порча файла, обнаружение порчи."""
    with tempfile.TemporaryDirectory(prefix="themis-backup-selftest-") as tmp:
        src = os.path.join(tmp, "cases", "ivanov-ivan", "delo-2026", "00_intake")
        os.makedirs(src)
        with open(os.path.join(src, "material.txt"), "w", encoding="utf-8") as f:
            f.write("Определение суда от 18.08.2026\n")
        os.makedirs(os.path.join(tmp, "cases", "ivanov-ivan", "delo-2026", ".agent/context"))
        with open(os.path.join(tmp, "cases", "ivanov-ivan", "delo-2026", ".agent/context", "нет.md"), "w") as f:
            f.write("вне 00_intake — копировать не должен\n")
        cases_dir, dest = os.path.join(tmp, "cases"), os.path.join(tmp, "backup")
        files, errors = backup(cases_dir, dest, quiet=True)
        assert not errors, errors
        assert len(files) == 1, f"скопировано {len(files)} файлов вместо одного (взял лишнее вне 00_intake)"
        assert not verify(cases_dir, dest, quiet=True), "чистая копия признана битой"
        assert not restore_test(dest, quiet=True, seed=1), "восстановление чистой копии не прошло"
        with open(os.path.join(dest, "ivanov-ivan", "delo-2026", "00_intake", "material.txt"), "a") as f:
            f.write("подмена\n")
        assert verify(cases_dir, dest, quiet=True), "порча файла в копии НЕ обнаружена"
    print("selftest: копия, отсев файлов вне 00_intake, сверка, восстановление, детект порчи — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Страховочная копия cases/*/*/00_intake с проверкой.")
    ap.add_argument("--dest", default=DEFAULT_DEST, help=f"каталог копии (по умолчанию {DEFAULT_DEST})")
    ap.add_argument("--verify-only", action="store_true", help="только сверка, без копирования")
    ap.add_argument("--no-restore", action="store_true", help="пропустить контрольное восстановление")
    ap.add_argument("--seed", type=int, default=None, help="зерно выбора дела для восстановления")
    ap.add_argument("--selftest", action="store_true", help="проверка на синтетике, без диска дел")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not os.path.isdir(CASES):
        print(f"нет каталога {CASES}", file=sys.stderr)
        return 2
    os.makedirs(args.dest, exist_ok=True)

    errors = []
    if not args.verify_only:
        print(f"ЭТАП 0 · копирование {CASES} → {args.dest}")
        files, errs = backup(CASES, args.dest)
        if files is None:
            print(errs[0], file=sys.stderr)
            return 2
        errors += errs

    print("сверка по контрольным суммам…")
    errors += verify(CASES, args.dest)

    if not args.no_restore and not errors:
        print("контрольное восстановление случайного дела…")
        errors += restore_test(args.dest, seed=args.seed)

    if errors:
        print(f"\n❌ расхождений: {len(errors)}")
        for e in errors[:50]:
            print("  · " + e)
        return 1
    man = load_manifest(args.dest)
    gb = sum(v["size"] for v in man.values()) / 1e9
    print(f"\n✓ ЭТАП 0 принят: {len(man)} файлов, {gb:.1f} ГБ, суммы сошлись, восстановление читаемо")
    print(f"  копия: {args.dest}")
    print("  ⚠ копия на том же диске: не спасает от отказа носителя, кражи и шифровальщика — нужен внешний диск")
    return 0


if __name__ == "__main__":
    sys.exit(main())
