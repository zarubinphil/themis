#!/usr/bin/env python3
"""sync_prompts.py — единый источник промптов. Этап 2 плана FINAL-PLAN-2026-08-18.

Канон — `.claude/`. Всё остальное ПРОИЗВОДНОЕ и генерируется отсюда:

  .claude/agents/<n>.md      → .codex/agents/<n>.toml
  .claude/skills/<n>/…       → .agents/skills/<n>/…                (побайтовая копия)
  .claude/commands/<n>.md    → .agents/skills/source-command-<n>/SKILL.md

Зачем: три набора правились руками и разошлись. Замер 19.08.2026 до генерации —
16 расхождений в 10 агентах из 13, включая запрещённые владельцем квадратные скобки
в `.codex`, которых в каноне уже не было. Разошедшийся промпт хуже отсутствующего:
агент исполняет устаревшее правило уверенно.

TOML пишется ЛИТЕРАЛЬНЫМИ строками `'''…'''`, а не базовыми `\"\"\"…\"\"\"`: тела агентов
содержат grep-паттерны с обратными слешами (`grep "А\\|Б"`), и базовая строка на них
падает с «Unescaped '\\' in a string». Проверено tomllib 19.08.2026.

Каждый сгенерированный `.toml` тут же разбирается обратно и сверяется со своим
источником: генератор, который молча исказил промпт, хуже отсутствующего генератора.

  --check   (по умолчанию) сгенерировать в память и сверить с диском; расхождение → код 1
  --apply   записать
  --selftest проверка на синтетике без сети

Выход: 0 — производное совпадает с каноном; 1 — расхождение (список на stdout).
"""
import argparse
import difflib
import os
import shutil
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = """---
name: "source-command-{name}"
description: "{desc}"
---

# source-command-{name}

Use this skill when the user asks to run the migrated source command `{name}`.

## Command Template

{body}
"""


# `.agents/` — не «другой текст», а платформенный вариант канона. Разница между
# наборами была ровно механической подстановкой: замер 19.08.2026 показал 13 строк
# расхождения, из них 11 — эти подстановки, 2 — устаревшее (квадратные скобки,
# запрещённые владельцем 10.08.2026, и абсолютный путь автора). Держать вариант
# руками — значит снова разойтись; держать таблицей — воспроизводимо.
AGENTS_SUBS = (
    ("Claude Code", "Codex"),
    ("CLAUDE.md", "AGENTS.md"),
)


def to_agents(text):
    """Канон → платформенный вариант для `.agents/`."""
    for src, dst in AGENTS_SUBS:
        text = text.replace(src, dst)
    return text


def unquote(value):
    """Снять кавычки ТОЛЬКО с целиком закавыченного скаляра.

    Описания агентов начинаются с прозвища в кавычках («"Кони" — проверка…»), и
    слепой strip('"') съедал открывающую кавычку, молча меняя промпт.
    """
    for q in ('"', "'"):
        if len(value) >= 2 and value[0] == q and value[-1] == q and q not in value[1:-1]:
            return value[1:-1]
    return value


def split_md(text):
    """Frontmatter → dict, тело → строка. Парсер намеренно плоский: вложенного YAML тут нет."""
    if not text.startswith("---"):
        return {}, text.strip()
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.splitlines():
        if line.strip() and ":" in line and not line.startswith((" ", "\t", "#")):
            k, v = line.split(":", 1)
            meta[k.strip()] = unquote(v.strip())
    return meta, body.strip()


def toml_basic(value):
    """Однострочная базовая строка TOML с экранированием. Для name и description."""
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + out.replace("\n", "\\n").replace("\r", "") + '"'


def toml_literal_block(value):
    """Многострочная ЛИТЕРАЛЬНАЯ строка: содержимое не экранируется вовсе.

    Годится, пока внутри нет `'''` и текст не кончается апострофом — оба случая
    проверяются вызывающим и являются отказом генерации, а не тихой порчей.
    """
    return "'''\n" + value + "\n'''"


def render_toml(name, meta, body):
    if "'''" in body or body.rstrip().endswith("'"):
        raise ValueError(f"{name}: тело содержит ''' или кончается апострофом — "
                         f"литеральная строка TOML его не удержит")
    lines = [
        f"name = {toml_basic(meta.get('name', name))}",
        f"description = {toml_basic(meta.get('description', ''))}",
        f"developer_instructions = {toml_literal_block(body)}",
        "",
    ]
    text = "\n".join(lines)
    # Обратная сверка: сгенерированное обязано разбираться в ровно то, из чего сделано
    got = tomllib.loads(text)
    if got.get("name") != meta.get("name", name):
        raise ValueError(f"{name}: имя исказилось при генерации")
    if got.get("description", "") != meta.get("description", ""):
        raise ValueError(f"{name}: описание исказилось при генерации")
    if got.get("developer_instructions", "").strip() != body.strip():
        raise ValueError(f"{name}: ТЕЛО ПРОМПТА исказилось при генерации")
    return text


def plan(root=ROOT):
    """Что должно лежать на диске: {относительный путь: содержимое}. Только производное."""
    out = {}
    agents = os.path.join(root, ".claude", "agents")
    if os.path.isdir(agents):
        for fn in sorted(os.listdir(agents)):
            if not fn.endswith(".md"):
                continue
            name = fn[:-3]
            meta, body = split_md(open(os.path.join(agents, fn), encoding="utf-8").read())
            out[os.path.join(".codex", "agents", f"{name}.toml")] = render_toml(name, meta, body)

    skills = os.path.join(root, ".claude", "skills")
    if os.path.isdir(skills):
        for name in sorted(os.listdir(skills)):
            src = os.path.join(skills, name)
            if not os.path.isdir(src):
                continue
            for dirpath, _, files in os.walk(src):
                for fn in sorted(files):
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, skills)
                    out[os.path.join(".agents", "skills", rel)] = \
                        to_agents(open(full, encoding="utf-8").read())

    cmds = os.path.join(root, ".claude", "commands")
    if os.path.isdir(cmds):
        for fn in sorted(os.listdir(cmds)):
            if not fn.endswith(".md"):
                continue
            name = fn[:-3]
            meta, body = split_md(open(os.path.join(cmds, fn), encoding="utf-8").read())
            rel = os.path.join(".agents", "skills", f"source-command-{name}", "SKILL.md")
            out[rel] = to_agents(
                WRAPPER.format(name=name, desc=meta.get("description", ""), body=body))
    return out


def compare(root=ROOT):
    """Расхождения производного с каноном. Пустой список — diff пуст."""
    want = plan(root)
    diffs = []
    for rel, text in sorted(want.items()):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            diffs.append((rel, "отсутствует — канон его требует"))
            continue
        have = open(path, encoding="utf-8").read()
        if have != text:
            d = list(difflib.unified_diff(have.splitlines(), text.splitlines(),
                                          "на диске", "из канона", lineterm="", n=0))
            hint = next((line for line in d[2:] if line[:1] in "+-"), "")
            diffs.append((rel, f"разошлось ({len(d) - 2} строк), первое: {hint.strip()[:110]}"))
    # Лишнее: производное, у которого не осталось источника в каноне
    for base in (os.path.join(".codex", "agents"), os.path.join(".agents", "skills")):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            continue
        for dirpath, _, files in os.walk(d):
            for fn in files:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                if rel not in want and not fn.startswith("."):
                    diffs.append((rel, "лишнее — в каноне источника нет"))
    return diffs


def apply(root=ROOT):
    want = plan(root)
    written = []
    for rel, text in sorted(want.items()):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and open(path, encoding="utf-8").read() == text:
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(rel)
    return written


def selftest():
    """Синтетика: генерация, обратная сверка, детект расхождения и лишнего файла."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="syncprompts-selftest-") as tmp:
        for d in (".claude/agents", ".claude/skills/proba", ".claude/commands"):
            os.makedirs(os.path.join(tmp, d))
        # Тело с обратным слешем в grep — ровно то, на чём падает базовая строка TOML
        with open(os.path.join(tmp, ".claude/agents/chitatel.md"), "w", encoding="utf-8") as f:
            f.write('---\nname: chitatel\ndescription: "Петров" — читатель\ntools: Read\n'
                    'model: haiku\n---\n\n# Петров\n\n`grep -n "А\\|Б" файл.md` и «кавычки».\n')
        with open(os.path.join(tmp, ".claude/skills/proba/SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: proba\n---\n\nтело скилла\n")
        with open(os.path.join(tmp, ".claude/commands/delat.md"), "w", encoding="utf-8") as f:
            f.write("---\ndescription: Делать дело\nargument-hint: x\n---\n\n# /delat\n\nтекст\n")

        want = plan(tmp)
        assert ".codex/agents/chitatel.toml" in want, "агент не сгенерирован"
        assert ".agents/skills/proba/SKILL.md" in want, "скилл не скопирован"
        assert ".agents/skills/source-command-delat/SKILL.md" in want, "команда не обёрнута"

        got = tomllib.loads(want[".codex/agents/chitatel.toml"])
        assert 'grep -n "А\\|Б"' in got["developer_instructions"], \
            "обратный слеш в grep-паттерне исказился при генерации"
        assert got["description"] == '"Петров" — читатель', "кавычки в описании исказились"
        assert "source command `delat`" in want[".agents/skills/source-command-delat/SKILL.md"]

        # Платформенная подстановка: канон говорит Claude Code, вариант для .agents — Codex
        with open(os.path.join(tmp, ".claude/skills/proba/SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: proba\n---\n\nперезапуск Claude Code по CLAUDE.md\n")
        want = plan(tmp)
        got = want[".agents/skills/proba/SKILL.md"]
        assert "перезапуск Codex по AGENTS.md" in got, f"подстановка не сработала: {got!r}"
        assert "Claude Code" not in got and "CLAUDE.md" not in got, "канон протёк в вариант"
        canon = open(os.path.join(tmp, ".claude/skills/proba/SKILL.md"), encoding="utf-8").read()
        assert "Claude Code" in canon, "подстановка испортила сам канон"
        assert ".codex/agents/chitatel.toml" in want and \
            "Codex" not in want[".codex/agents/chitatel.toml"], \
            "подстановка для .agents применена к .codex — она только для .agents"

        assert len(compare(tmp)) == len(want), "отсутствующее производное не поймано"
        assert apply(tmp), "apply ничего не записал"
        assert compare(tmp) == [], f"после apply остались расхождения: {compare(tmp)}"

        # Ручная правка производного обязана быть пойманной
        p = os.path.join(tmp, ".codex/agents/chitatel.toml")
        with open(p, "a", encoding="utf-8") as f:
            f.write("\nlishnee = 1\n")
        assert any("chitatel.toml" in r for r, _ in compare(tmp)), \
            "ручная правка производного НЕ поймана — генератор бесполезен"
        apply(tmp)

        # Осиротевшее производное: источник в каноне удалён
        os.remove(os.path.join(tmp, ".claude/agents/chitatel.md"))
        assert any("лишнее" in w for _, w in compare(tmp)), \
            "производное без источника в каноне НЕ поймано"

        # Тело, которое литеральная строка не удержит, — отказ, а не тихая порча
        try:
            render_toml("x", {"name": "x", "description": ""}, "текст с ''' внутри")
            raise AssertionError("тройной апостроф в теле не отвергнут")
        except ValueError:
            pass
    print("selftest: генерация, обратная сверка TOML, слеши в grep, кавычки в описании, "
          "детект ручной правки и осиротевшего файла, отказ на неудержимом теле — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Единый источник промптов: канон .claude/ → производное.")
    ap.add_argument("--apply", action="store_true", help="записать производное")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    try:
        if a.apply:
            written = apply()
            print(f"записано файлов: {len(written)}")
            for rel in written:
                print("  · " + rel)
            left = compare()
            if left:
                print(f"\n❌ после записи осталось расхождений: {len(left)}")
                for rel, why in left:
                    print(f"  · {rel}: {why}")
                return 1
            print("\n✓ производное совпадает с каноном, diff пуст")
            return 0
        diffs = compare()
    except (ValueError, OSError) as e:
        print(f"генерация невозможна: {e}", file=sys.stderr)
        return 1
    if not diffs:
        print("✓ производное совпадает с каноном, diff пуст")
        return 0
    print(f"❌ расхождений с каноном: {len(diffs)} — чинится `--apply`")
    for rel, why in diffs:
        print(f"  · {rel}: {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
