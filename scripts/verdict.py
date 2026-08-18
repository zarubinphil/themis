#!/usr/bin/env python3
"""verdict.py — вердикт Кони, привязанный к редакции. Этап 3 плана FINAL-PLAN-2026-08-18.

Раньше вердикт был словом в чате и строкой в `review_log.md`. Слово не привязано ни к
чему: одобрили редакцию r2, дописали абзац, собрали `.docx` — и в суд ушёл текст,
которого Кони не видел. Вердикт обязан содержать идентификатор документа, номер
редакции и SHA-256 самого `.md`.

Здесь же гейт humanizer-legal — вынесен из `DocBuilder.save()`. На собранном `.docx`
он срабатывал один раз и слишком поздно; прогон по `.md` идёт КАЖДЫЙ раунд, до того
как текст стал документом.

    --scan   ФАЙЛ.md                     проверка humanizer-legal (каждый раунд)
    --record ФАЙЛ.md --verdict "…" [-r N] записать вердикт с отпечатком редакции
    --check  ФАЙЛ.md                     можно ли собирать .docx из этой редакции
    --log    ФАЙЛ.md                     история вердиктов документа

Журнал — `.agent/drafts/_working/verdicts.jsonl` рядом с черновиком, append-only.

Выход: 0 — можно; 1 — нельзя (причина на stdout); 2 — вызов неверен.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_paths as cp  # noqa: E402

READY = "ГОТОВ К ПОДАЧЕ"
SCAN = Path.home() / ".claude/skills/humanizer-legal/scripts/scan_legal.sh"
# Категории scan_legal.sh, при которых документ не выпускается. Совпадает с
# прежним списком в DocBuilder — перенесено, а не переизобретено.
BLOCKING = ("ПЛЕЙСХОЛДЕР", "AI-ПАТТЕРН", "КАНЦЕЛЯРИТ-ШТАМП")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def journal_path(md):
    """Журнал вердиктов лежит в рабочей папке черновиков — рядом с review_log.md."""
    md = Path(md).resolve()
    for parent in md.parents:
        if parent.name == "drafts" and parent.parent.name == cp.AGENT_DIR:
            return parent / cp.WORKING / "verdicts.jsonl"
    return md.parent / cp.WORKING / "verdicts.jsonl"


def scan(md):
    """Гейт humanizer-legal по `.md`. Возвращает список сработавших блокирующих категорий.

    Скрипта нет → пустой список и громкое предупреждение: молча считать документ
    проверенным нельзя, но и держать всю систему заложником одного скилла тоже.
    """
    if not SCAN.is_file():
        print(f"ВНИМАНИЕ: {SCAN} не найден — humanizer-legal НЕ проверен", file=sys.stderr)
        return []
    try:
        p = subprocess.run(["bash", str(SCAN), str(md)], capture_output=True,
                           text=True, timeout=300, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"ВНИМАНИЕ: humanizer-legal не отработал ({e})", file=sys.stderr)
        return []
    out = (p.stdout or "") + (p.stderr or "")
    return [c for c in BLOCKING if c in out]


def record(md, verdict, round_no):
    md = Path(md)
    entry = {
        "document": md.name,
        "path": str(md),
        "round": round_no,
        "verdict": verdict,
        "sha256": sha(md),
        "at": time.strftime("%d.%m.%Y %H:%M:%S"),
    }
    jp = journal_path(md)
    jp.parent.mkdir(parents=True, exist_ok=True)
    with open(jp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def history(md):
    jp = journal_path(md)
    if not jp.is_file():
        return []
    name = Path(md).name
    out = []
    for line in open(jp, encoding="utf-8"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("document") == name:
            out.append(e)
    return out


def check(md):
    """Причины, по которым из этой редакции нельзя собирать `.docx`. Пусто — можно."""
    md = Path(md)
    if not md.is_file():
        return [f"{md}: файла нет — собирать не из чего"]
    now = sha(md)
    hist = history(md)
    if not hist:
        return [f"{md.name}: вердикта нет вовсе — документ не проходил проверку Кони"]
    ok = [e for e in hist if e.get("verdict") == READY and e.get("sha256") == now]
    if ok:
        return []
    approved = [e for e in hist if e.get("verdict") == READY]
    if approved:
        last = approved[-1]
        return [f"{md.name}: вердикт «{READY}» есть, но выдан на ДРУГУЮ редакцию "
                f"(r{last.get('round')}, отпечаток {last.get('sha256', '')[:12]}…, "
                f"сейчас {now[:12]}…) — текст правился после одобрения, нужен новый раунд"]
    last = hist[-1]
    return [f"{md.name}: последний вердикт «{last.get('verdict')}» (r{last.get('round')}) — "
            f"не «{READY}»"]


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="verdict-selftest-") as tmp:
        d = Path(tmp) / "cases" / "ivanov-ivan" / "delo-2026" / cp.AGENT_DIR / "drafts"
        d.mkdir(parents=True)
        md = d / "isk_v1.md"
        md.write_text("# Иск\n\nТекст первой редакции.\n", encoding="utf-8")

        # resolve с обеих сторон: на macOS /var — симлинк на /private/var
        assert journal_path(md) == (d / cp.WORKING / "verdicts.jsonl").resolve(), \
            journal_path(md)
        assert check(md), "документ без вердикта признан готовым к сборке"
        assert "вердикта нет вовсе" in check(md)[0]

        record(md, "ТРЕБУЕТ ПРАВОК", 1)
        assert check(md), "вердикт ТРЕБУЕТ ПРАВОК пропустил сборку"
        assert "не «ГОТОВ К ПОДАЧЕ»" in check(md)[0]

        record(md, READY, 2)
        assert not check(md), f"одобренная редакция не пропущена: {check(md)}"

        # Ровно тот случай, ради которого всё это: текст правится ПОСЛЕ одобрения
        md.write_text("# Иск\n\nТекст первой редакции.\n\nДописанный абзац.\n", encoding="utf-8")
        problems = check(md)
        assert problems, "изменённый после одобрения текст пропущен к сборке"
        assert "ДРУГУЮ редакцию" in problems[0], problems

        # Новый раунд по новой редакции снова открывает сборку
        record(md, READY, 3)
        assert not check(md), "новый вердикт на новую редакцию не пропустил"

        # Возврат к прежнему тексту не воскрешает прежний вердикт по ошибке:
        # отпечаток совпадает — значит это буквально та самая одобренная редакция
        md.write_text("# Иск\n\nТекст первой редакции.\n", encoding="utf-8")
        assert not check(md), "возврат к ранее одобренному тексту заблокирован зря"

        assert len(history(md)) == 3, history(md)   # r1 правки, r2 и r3 готов
        assert check(d / "net.md"), "несуществующий файл признан готовым"

        # Гейт humanizer не должен молча считать документ чистым при отсутствии скрипта
        global SCAN
        saved, SCAN = SCAN, Path(tmp) / "net-skripta.sh"
        try:
            assert scan(md) == [], "отсутствие скрипта дало ложные блокировки"
        finally:
            SCAN = saved
    print("selftest: журнал рядом с черновиком, отказ без вердикта, отказ на ТРЕБУЕТ ПРАВОК, "
          "детект правки после одобрения, новый раунд, возврат к одобренному тексту — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Вердикт Кони, привязанный к редакции.")
    ap.add_argument("md", nargs="?", help="черновик .md")
    ap.add_argument("--scan", action="store_true", help="гейт humanizer-legal по .md")
    ap.add_argument("--record", action="store_true", help="записать вердикт")
    ap.add_argument("--verdict", help="текст вердикта (с --record)")
    ap.add_argument("-r", "--round", type=int, default=1, help="номер раунда (с --record)")
    ap.add_argument("--check", action="store_true", help="можно ли собирать .docx")
    ap.add_argument("--log", action="store_true", help="история вердиктов")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.md:
        ap.print_help()
        return 2

    if a.scan:
        blockers = scan(a.md)
        if blockers:
            print(f"❌ humanizer-legal: сработали блокирующие категории — {', '.join(blockers)}")
            print(f"   Прогнать скилл humanizer-legal по тексту и повторить.")
            print(f"   Полный отчет: bash {SCAN} {a.md}")
            return 1
        print("✓ humanizer-legal: следов автогенерации и незаполненных плейсхолдеров нет")
        return 0
    if a.record:
        if not a.verdict:
            print("--record требует --verdict", file=sys.stderr)
            return 2
        e = record(a.md, a.verdict, a.round)
        print(f"вердикт записан: {e['document']} r{e['round']} «{e['verdict']}» "
              f"отпечаток {e['sha256'][:12]}…")
        return 0
    if a.log:
        h = history(a.md)
        if not h:
            print("вердиктов нет")
            return 1
        for e in h:
            print(f"  {e['at']}  r{e['round']}  {e['sha256'][:12]}…  {e['verdict']}")
        return 0
    if a.check:
        problems = check(a.md)
        if problems:
            print("⛔ СБОРКА .docx ЗАПРЕЩЕНА")
            for p in problems:
                print("  · " + p)
            return 1
        print(f"✓ редакция одобрена Кони — сборка .docx разрешена")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
