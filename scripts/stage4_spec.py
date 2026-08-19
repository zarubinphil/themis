#!/usr/bin/env python3
"""stage4_spec.py — приёмка этапа 4 «дисциплина и деньги». Пишет КООРДИНАТОР, не исполнитель.

Инвариант роя: generator ≠ verifier. Контракт задаётся снаружи и проверяется чёрным
ящиком — только через командную строку и сетевой запрос к панели, без импорта потрохов.
Исполнитель этот файл НЕ ПРАВИТ: прибор подстраивается под приёмку, не наоборот.
Правку ловит loop_gate (`spec:tampered`) сверкой с git.

Четыре работы этапа:
  1. запись кода (.py/.sh) под cases/ блокируется хуком;
  2. растровые рендеры под cases/ вне первички блокируются, существующие вывезены
     в кеш обратимо (первичка неприкосновенна);
  3. модель шага выводится прибором из уровня и сверяется по брифу;
  4. загрузка в панель ограничена по числу файлов, размеру файла и объёму запроса,
     отказ громкий (413), а не молчаливый пропуск.
Плюс инвариант денег: независимый счётчик сходится с ledger в пределах 2%.

Выход: 0 — этап принят; 1 — есть несданное (список с контрактами).
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CASES = ROOT / "cases"
NO_NET = {**os.environ, "HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
          "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1,localhost"}
RASTER = ("png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp")
INTAKE = "00_" + "intake"   # склейкой: строка-триггер собственного сторожа в теле приёмки


def run(argv, cwd=ROOT, timeout=300, env=None, stdin_text=None):
    try:
        p = subprocess.run([sys.executable, *argv], cwd=str(cwd), capture_output=True,
                           text=True, timeout=timeout, env=env or NO_NET,
                           input=(stdin_text if stdin_text is not None else ""))
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "", f"таймаут {timeout} с"
    except OSError as e:
        return 127, "", str(e)


def tool(name):
    return str(SCRIPTS / name)


def exists(name):
    return (SCRIPTS / name).is_file()


def guard(payload):
    """Прогнать вход PreToolUse через сторожа. Возврат: код (2 — блок, 0 — пропуск)."""
    code, _, err = run([tool("claude_guard.py")], stdin_text=json.dumps(payload, ensure_ascii=False))
    return code, err


def probes(name, cases_):
    """cases_: [(описание, payload, ожидаемый код)]. Возврат — список провалов."""
    fails = []
    for what, payload, want in cases_:
        got, err = guard(payload)
        if got != want:
            fails.append((name, f"{what}: ждали код {want}, вышло {got}. {err.strip()[:200]}"))
    return fails


# ── 1. Код под cases/ ───────────────────────────────────────────────────────
PY_CONTRACT = """  scripts/claude_guard.py — PreToolUse-хук
    Блокирует (код 2) запись исполняемого кода (.py, .sh) в любое место под cases/:
    Write/Edit по такому пути и Bash-команду, кладущую туда файл (редирект, heredoc,
    cp/mv/tee). Причина: генератор документа внутри дела обходит DocBuilder и гейты
    формата — так под cases/ завелись 84 скрипта, 15 из них с запрещённым шрифтом.
    НЕ блокирует (код 0): .py под scripts/, чтение и ЗАПУСК существующего файла,
    слово «cases» и имя .py внутри прозы команды (сообщение коммита, grep, python3
    scripts/... по материалам дела). Сторож, срабатывающий на обиходе, будет снят."""


def check_py_under_cases():
    if not exists("claude_guard.py"):
        return [("claude_guard.py", "сторожа нет. Контракт:\n" + PY_CONTRACT)]
    c = str(CASES / "ivanov-ivan" / "razdel-imushchestva-2026")
    return probes("guard:py", [
        ("Write build_isk.py в кухню дела",
         {"tool_name": "Write", "tool_input": {"file_path": c + "/.agent/context/_working/build_isk.py",
                                               "content": "x"}}, 2),
        ("Write .sh в дело",
         {"tool_name": "Write", "tool_input": {"file_path": c + "/.agent/context/_working/gen.sh",
                                               "content": "x"}}, 2),
        ("Edit существующего .py в деле",
         {"tool_name": "Edit", "tool_input": {"file_path": c + "/build_docx.py",
                                              "old_string": "a", "new_string": "b"}}, 2),
        ("Bash heredoc кладёт .py в дело",
         {"tool_name": "Bash", "tool_input": {"command":
          "cat > " + c + "/.agent/context/_working/build.py <<'EOF'\nprint(1)\nEOF"}}, 2),
        ("Bash cp .py в дело",
         {"tool_name": "Bash", "tool_input": {"command": "cp /tmp/gen.py " + c + "/gen.py"}}, 2),
        ("прибор в scripts/ пишется свободно",
         {"tool_name": "Write", "tool_input": {"file_path": str(SCRIPTS / "novyy_pribor.py"),
                                               "content": "x"}}, 0),
        ("запуск существующего скрипта по делу не трогается",
         {"tool_name": "Bash", "tool_input": {"command":
          "python3 scripts/markdown_extract.py " + c + "/" + INTAKE + "/isk.pdf --json-meta"}}, 0),
        ("проза со словами cases и .py не блокируется",
         {"tool_name": "Bash", "tool_input": {"command":
          "git commit -m 'запрет .py-генераторов под cases/ хуком'"}}, 0),
        ("рабочая заметка .md в кухне пишется",
         {"tool_name": "Write", "tool_input": {"file_path": c + "/.agent/context/_working/note.md",
                                               "content": "x"}}, 0),
    ])


# ── 2. Рендеры вне дела ─────────────────────────────────────────────────────
PNG_CONTRACT = """  scripts/claude_guard.py + scripts/render_gc.py + scripts/markdown_extract.py
    Хук блокирует (код 2) запись растра (png/jpg/jpeg/tif/tiff/bmp/webp) под cases/
    ВНЕ первички: рендер страницы — производный кеш, его место вне дела. Первичка
    не трогается вовсе, доказательство-картинка кладётся туда как новый файл
    (уже разрешено правилом пополнения).
    НЕ блокирует: растр в /tmp и в ~/.cache, ЧТЕНИЕ картинки под cases (облачный
    фолбэк vision обязан работать), .md/.txt-сайдкары рядом с рендерами.
    markdown_extract.py FILE --render-dir ПУТЬ_ПОД_cases — отказ с ненулевым кодом
    и без создания каталога; тот же вызов с каталогом вне cases/ такого отказа не даёт.
    scripts/render_gc.py — вывоз уже накопленного, обратимо:
      --dry-run КОРЕНЬ            печатает число найденных, диск НЕ меняет
      --move КОРЕНЬ --manifest F  переносит растр вне первички в кеш, пишет манифест
      --restore F                 возвращает файлы на место побайтово
      первичка не трогается ни в одном режиме; --selftest даёт 0 без сети.
    Итог на боевом дереве: растровых файлов под cases/ вне первички — ноль."""


def check_png_guard():
    if not exists("claude_guard.py"):
        return [("claude_guard.py", "сторожа нет. Контракт:\n" + PNG_CONTRACT)]
    c = str(CASES / "ivanov-ivan" / "razdel-imushchestva-2026")
    fails = probes("guard:png", [
        ("Write png в кухню дела",
         {"tool_name": "Write", "tool_input": {"file_path": c + "/.agent/context/_working/ocr/page_001.png",
                                               "content": "x"}}, 2),
        ("Bash cp jpg в дело",
         {"tool_name": "Bash", "tool_input": {"command":
          "cp /tmp/a.jpg " + c + "/.agent/context/_practice/foto.jpg"}}, 2),
        ("Bash редирект tiff в дело",
         {"tool_name": "Bash", "tool_input": {"command": "convert a.pdf > " + c + "/scan.tiff"}}, 2),
        ("рендер в /tmp разрешён",
         {"tool_name": "Bash", "tool_input": {"command":
          "python3 scripts/markdown_extract.py " + c + "/" + INTAKE
          + "/isk.pdf --render-dir /tmp/ivanov/isk"}}, 0),
        ("чтение картинки под cases разрешено (фолбэк vision)",
         {"tool_name": "Read", "tool_input": {"file_path": c + "/" + INTAKE + "/foto.png"}}, 0),
        ("сайдкар .txt рядом с рендером пишется",
         {"tool_name": "Write", "tool_input": {"file_path": c + "/.agent/context/_working/ocr/page_001.txt",
                                               "content": "x"}}, 0),
    ])
    # markdown_extract обязан отказать до работы, если каталог рендера ведёт в дело
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "obrazec.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        bad = (CASES / "ivanov-ivan" / "razdel-imushchestva-2026" / ".agent" / "context"
               / "_working" / "spec_render")
        code, out, err = run([tool("markdown_extract.py"), str(src), "--render-dir", str(bad)],
                             timeout=120)
        text = out + err
        if code == 0:
            fails.append(("markdown_extract.py", "--render-dir под cases/ принят (ждали отказ)"))
        if bad.exists():
            fails.append(("markdown_extract.py", f"каталог рендера под cases/ создан: {bad}"))
        if "cases" not in text.lower():
            fails.append(("markdown_extract.py", f"отказ не назвал причину cases/: {text.strip()[:200]}"))
        ok_dir = Path(td) / "render"
        code2, out2, err2 = run([tool("markdown_extract.py"), str(src), "--render-dir", str(ok_dir)],
                                timeout=120)
        if "под cases" in (out2 + err2):
            fails.append(("markdown_extract.py", "каталог вне cases/ тоже объявлен запретным"))
    return fails


def check_render_gc():
    if not exists("render_gc.py"):
        return [("render_gc.py", "прибора нет. Контракт:\n" + PNG_CONTRACT)]
    fails = []
    code, out, err = run([tool("render_gc.py"), "--selftest"])
    if code != 0:
        fails.append(("render_gc.py", f"--selftest вернул {code}: {(out + err).strip()[-400:]}"))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "cases" / "petrov-petr" / "delo-2026"
        work = root / ".agent" / "context" / "_working" / "ocr"
        intake = root / INTAKE
        work.mkdir(parents=True)
        intake.mkdir(parents=True)
        (work / "page_001.png").write_bytes(b"RENDER-1")
        (work / "page_002.png").write_bytes(b"RENDER-2")
        (work / "page_001.txt").write_text("текст страницы", encoding="utf-8")
        (intake / "skan.png").write_bytes(b"PERVICHKA")
        manifest = Path(td) / "manifest.json"

        code, out, err = run([tool("render_gc.py"), "--dry-run", str(Path(td) / "cases")])
        if code != 0:
            fails.append(("render_gc.py", f"--dry-run вернул {code}: {(out + err).strip()[-300:]}"))
        if "2" not in out:
            fails.append(("render_gc.py", f"--dry-run не назвал число найденных (2): {out.strip()[:200]}"))
        if not (work / "page_001.png").exists():
            fails.append(("render_gc.py", "--dry-run тронул диск"))

        code, out, err = run([tool("render_gc.py"), "--move", str(Path(td) / "cases"),
                              "--manifest", str(manifest)])
        if code != 0:
            fails.append(("render_gc.py", f"--move вернул {code}: {(out + err).strip()[-300:]}"))
        left = [p for p in (Path(td) / "cases").rglob("*")
                if p.is_file() and p.suffix.lower().lstrip(".") in RASTER and INTAKE not in p.parts]
        if left:
            fails.append(("render_gc.py", f"после --move растр остался: {[str(p) for p in left][:3]}"))
        if not (intake / "skan.png").exists() or (intake / "skan.png").read_bytes() != b"PERVICHKA":
            fails.append(("render_gc.py", "первичка тронута — запрещено"))
        if not (work / "page_001.txt").exists():
            fails.append(("render_gc.py", "сайдкар OCR .txt удалён вместе с рендером"))
        if not manifest.is_file():
            fails.append(("render_gc.py", "манифест не записан — откат невозможен"))

        code, out, err = run([tool("render_gc.py"), "--restore", str(manifest)])
        if code != 0:
            fails.append(("render_gc.py", f"--restore вернул {code}: {(out + err).strip()[-300:]}"))
        if not (work / "page_001.png").is_file() or not (work / "page_002.png").is_file():
            fails.append(("render_gc.py", "--restore не вернул файлы на место"))
        elif (work / "page_001.png").read_bytes() != b"RENDER-1" or \
                (work / "page_002.png").read_bytes() != b"RENDER-2":
            fails.append(("render_gc.py", "--restore вернул файлы с изменённым содержимым"))
    return fails


def check_tree_clean():
    """Боевое дерево: растр под cases/ вне первички — ноль."""
    if not CASES.is_dir():
        return []
    left = [p for p in CASES.rglob("*")
            if p.is_file() and p.suffix.lower().lstrip(".") in RASTER and INTAKE not in p.parts]
    if left:
        return [("cases/", f"растровых файлов вне первички: {len(left)}. "
                           f"Первые: {[p.name for p in left[:3]]}. "
                           "Вывезти: python3 scripts/render_gc.py --move cases --manifest ...")]
    return []


# ── 3. Модель по уровню ─────────────────────────────────────────────────────
MODEL_CONTRACT = """  scripts/model_policy.py — модель шага выводится из уровня, а не из пина
    --level MICRO|L1|L2|L3 --step ШАГ  → печатает алиас (haiku|sonnet|opus), код 0
        draft  review : MICRO,L1 → sonnet ; L2,L3 → opus
        hunt          : sonnet ; на MICRO запрещён (код 1, причина со словом «запрещ»)
        council-role  : sonnet ; council-chair: opus ; оба запрещены на MICRO и L1
        read-text classify : haiku ; read-scan : sonnet
    --brief ФАЙЛ — сверка плана брифа: уровень берётся из строки КЛАССИФИКАЦИЯ,
        каждая строка таблицы ПЛАН сверяется с политикой. Код 0 — сходится;
        код 1 — назвать нарушителя (Opus на L1 — пятикратная цена типового документа).
        Fail-closed: нет уровня либо пустая колонка «Модель» — код 1, не 0.
    --selftest даёт 0 без сети. Скилл task-brief обязан звать этот прибор:
    «модель автоматом из брифа» значит из прибора, а не из головы."""

MATRIX = [("MICRO", "draft", "sonnet"), ("L1", "draft", "sonnet"),
          ("L2", "draft", "opus"), ("L3", "draft", "opus"),
          ("L1", "review", "sonnet"), ("L3", "review", "opus"),
          ("L2", "hunt", "sonnet"), ("L2", "council-role", "sonnet"),
          ("L3", "council-chair", "opus"), ("L2", "read-text", "haiku"),
          ("L2", "classify", "haiku"), ("L2", "read-scan", "sonnet")]
FORBIDDEN = [("MICRO", "hunt"), ("MICRO", "council-role"), ("L1", "council-chair")]

BRIEF_OK = """## БРИФ — типовое ходатайство                Дата: 19.08.2026

КЛАССИФИКАЦИЯ  Дело: cases/ivanov-ivan/razdel-imushchestva-2026 · Уровень: L1 · Трек: FAST · Результат: документ

ПЛАН
| Шаг | Исполнитель | Модель | Прогноз |
|---|---|---|---|
| 4 | doc-drafter | sonnet | 40k |
| 5 | doc-reviewer | sonnet | 20k |
"""
BRIEF_BAD = BRIEF_OK.replace("| 4 | doc-drafter | sonnet | 40k |", "| 4 | doc-drafter | opus | 40k |")
BRIEF_NOLEVEL = "\n".join(l for l in BRIEF_OK.splitlines() if "КЛАССИФИКАЦИЯ" not in l)
BRIEF_NOMODEL = BRIEF_OK.replace("| 4 | doc-drafter | sonnet | 40k |", "| 4 | doc-drafter |  | 40k |")


def check_model_policy():
    if not exists("model_policy.py"):
        return [("model_policy.py", "прибора нет. Контракт:\n" + MODEL_CONTRACT)]
    fails = []
    code, out, err = run([tool("model_policy.py"), "--selftest"])
    if code != 0:
        fails.append(("model_policy.py", f"--selftest вернул {code}: {(out + err).strip()[-400:]}"))
    for level, step, want in MATRIX:
        code, out, err = run([tool("model_policy.py"), "--level", level, "--step", step])
        got = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if code != 0 or got != want:
            fails.append(("model_policy.py", f"{level}/{step}: ждали «{want}» код 0, "
                                             f"вышло «{got}» код {code}"))
    for level, step in FORBIDDEN:
        code, out, err = run([tool("model_policy.py"), "--level", level, "--step", step])
        if code == 0:
            fails.append(("model_policy.py", f"{level}/{step} обязан быть запрещён, вышел код 0"))
        elif "запрещ" not in (out + err).lower():
            fails.append(("model_policy.py",
                          f"{level}/{step}: отказ не назвал причину: {(out + err).strip()[:150]}"))
    with tempfile.TemporaryDirectory() as td:
        for name, text, want_code, why in (
                ("ok.md", BRIEF_OK, 0, "верный бриф отвергнут"),
                ("bad.md", BRIEF_BAD, 1, "Opus на L1 пропущен — пятикратная цена"),
                ("nolevel.md", BRIEF_NOLEVEL, 1, "бриф без уровня принят (нужен fail-closed)"),
                ("nomodel.md", BRIEF_NOMODEL, 1, "пустая колонка модели принята")):
            p = Path(td) / name
            p.write_text(text, encoding="utf-8")
            code, out, err = run([tool("model_policy.py"), "--brief", str(p)])
            if code != want_code:
                fails.append(("model_policy.py", f"--brief {name}: ждали код {want_code}, "
                                                 f"вышло {code} — {why}. {(out + err).strip()[:200]}"))
    skill = ROOT / ".claude" / "skills" / "task-brief" / "SKILL.md"
    if not skill.is_file() or "model_policy.py" not in skill.read_text(encoding="utf-8"):
        fails.append(("task-brief", "скилл не зовёт model_policy.py — «модель автоматом из брифа» "
                                    "осталась обещанием"))
    code, out, err = run([tool("sync_prompts.py")])
    if code != 0:
        fails.append(("sync_prompts.py", f"канон и производное разошлись (код {code}): "
                                         f"{(out + err).strip()[-300:]}"))
    return fails


# ── 4. Лимиты загрузки в панель ─────────────────────────────────────────────
UPLOAD_CONTRACT = """  cockpit/app.py — POST /api/upload
    Ограничен по трём осям, отказ ГРОМКИЙ (413 + причина), а не молчаливый пропуск:
      · размер одного файла      (по умолчанию 50 МБ)
      · число файлов в запросе   (по умолчанию 30)
      · суммарный объём запроса  (по умолчанию 200 МБ)
    Пороги задаются переменными окружения THEMIS_UPLOAD_MAX_BYTES,
    THEMIS_UPLOAD_MAX_FILES, THEMIS_UPLOAD_MAX_TOTAL — иначе приёмка вынуждена
    гонять сотни мегабайт, чтобы проверить лимит.
    При отказе в инбокс не попадает НИ ОДИН файл запроса (частичная запись хуже
    отказа: юрист видит половину дела и считает, что загрузил всё).
    Каталог инбокса берётся из THEMIS_INBOX, если задан, — иначе приёмка пишет
    в боевой инбокс владельца. Нормальная загрузка по-прежнему даёт 200 и список."""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def check_upload_limits():
    app = ROOT / "cockpit" / "app.py"
    if not app.is_file():
        return [("cockpit/app.py", "панели нет. Контракт:\n" + UPLOAD_CONTRACT)]
    try:
        import httpx
    except ImportError:
        return [("cockpit/app.py", "для приёмки нужен httpx (уже стоит в окружении панели)")]
    fails = []
    with tempfile.TemporaryDirectory() as td:
        inbox = Path(td) / "inbox"
        port = _free_port()
        env = {**os.environ, "THEMIS_INBOX": str(inbox),
               "THEMIS_UPLOAD_MAX_BYTES": "2048", "THEMIS_UPLOAD_MAX_FILES": "3",
               "THEMIS_UPLOAD_MAX_TOTAL": "4096"}
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(port),
                                 "--host", "127.0.0.1", "--log-level", "warning"],
                                cwd=str(ROOT / "cockpit"), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        base = f"http://127.0.0.1:{port}"
        try:
            up = False
            for _ in range(100):
                if proc.poll() is not None:
                    break
                try:
                    httpx.get(base + "/", timeout=1.0)
                    up = True
                    break
                except Exception:
                    time.sleep(0.2)
            if not up:
                out = (proc.stdout.read() if proc.stdout else "")[-400:]
                return [("cockpit/app.py", f"панель не поднялась на порту {port}: {out}")]

            def post(files, timeout=30):
                return httpx.post(base + "/api/upload", files=files, timeout=timeout)

            r = post([("files", ("a.txt", b"x" * 100, "text/plain")),
                      ("files", ("b.txt", b"y" * 100, "text/plain"))])
            if r.status_code != 200:
                fails.append(("upload", f"нормальная загрузка отбита: {r.status_code} {r.text[:150]}"))
            elif len(r.json().get("saved", [])) != 2:
                fails.append(("upload", f"нормальная загрузка сохранила не два файла: {r.text[:150]}"))
            if inbox.is_dir() and len(list(inbox.iterdir())) != 2:
                fails.append(("upload", f"THEMIS_INBOX не соблюдён: {[p.name for p in inbox.iterdir()][:3]}"))

            before = sorted(p.name for p in inbox.iterdir()) if inbox.is_dir() else []
            r = post([("files", ("big.bin", b"z" * 5000, "application/octet-stream"))])
            if r.status_code != 413:
                fails.append(("upload", f"файл сверх лимита размера: ждали 413, вышло {r.status_code}"))
            r = post([("files", (f"f{i}.txt", b"q" * 10, "text/plain")) for i in range(5)])
            if r.status_code != 413:
                fails.append(("upload", f"число файлов сверх лимита: ждали 413, вышло {r.status_code}"))
            r = post([("files", (f"g{i}.txt", b"w" * 1500, "text/plain")) for i in range(3)])
            if r.status_code != 413:
                fails.append(("upload", f"объём запроса сверх лимита: ждали 413, вышло {r.status_code}"))
            after = sorted(p.name for p in inbox.iterdir()) if inbox.is_dir() else []
            if after != before:
                fails.append(("upload", f"при отказе файлы всё же записаны: было {before}, стало {after}"))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return fails


# ── 5. Деньги: независимый счётчик ──────────────────────────────────────────
MONEY_CONTRACT = """  scripts/token_audit.py --compare
    Независимый счётчик расхода сходится с token_ledger в пределах 2%.
    Расхождение = баг в одном из двух: цифра, на которой стоят бюджетные гейты,
    перестаёт быть основанием для решения."""


def check_money():
    if not exists("token_audit.py"):
        return [("token_audit.py", "прибора нет. Контракт:\n" + MONEY_CONTRACT)]
    code, out, err = run([tool("token_audit.py"), "--compare"], timeout=600)
    if code != 0:
        return [("token_audit.py", f"--compare вернул {code}: {(out + err).strip()[-300:]}")]
    return []


CHECKS = [
    ("код (.py/.sh) под cases/ блокируется", check_py_under_cases, PY_CONTRACT),
    ("растр под cases/ блокируется", check_png_guard, PNG_CONTRACT),
    ("вывоз рендеров обратим", check_render_gc, PNG_CONTRACT),
    ("боевое дерево чисто от рендеров", check_tree_clean, PNG_CONTRACT),
    ("модель выводится из уровня", check_model_policy, MODEL_CONTRACT),
    ("загрузка в панель ограничена", check_upload_limits, UPLOAD_CONTRACT),
    ("счёт денег сходится", check_money, MONEY_CONTRACT),
]


def selftest():
    """Приёмка обязана краснеть на отсутствующем приборе — иначе она не приёмка."""
    global SCRIPTS
    saved = SCRIPTS
    try:
        with tempfile.TemporaryDirectory() as td:
            SCRIPTS = Path(td)
            assert check_render_gc(), "пропавший render_gc.py не пойман"
            assert check_model_policy(), "пропавший model_policy.py не пойман"
            assert check_money(), "пропавший token_audit.py не пойман"
    finally:
        SCRIPTS = saved
    print("selftest: приёмка краснеет на отсутствующих приборах — ок")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Приёмка этапа 4 (пишет координатор).")
    ap.add_argument("--contracts", action="store_true", help="напечатать контракты и выйти")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.contracts:
        for title, _, contract in CHECKS:
            print(f"\n{title}:\n{contract}")
        return 0

    all_fails, done = [], 0
    for title, fn, _ in CHECKS:
        fails = fn()
        if fails:
            all_fails.append((title, fails))
        else:
            done += 1
        print(f"  {'✓' if not fails else '✗'} {title}")
    print(f"\nсдано проверок: {done}/{len(CHECKS)}")
    if not all_fails:
        print("✓ ЭТАП 4 ПРИНЯТ")
        return 0
    print("\nчто не сдано:")
    for title, fails in all_fails:
        for name, why in fails:
            print(f"\n· {name} — {title}\n  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
