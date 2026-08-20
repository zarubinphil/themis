#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pd_guard.py — фамилия доверителя не уходит в публичный репозиторий. Детерминированно.

ЗАЧЕМ. Инвариант «персональные данные не покидают cases/» держался только текстом
в конституции, а текст исполняется вероятностно. 04.08.2026 это и случилось:
документируя починку реестра, я записал имена двух папок дел в комментарии
scripts/registry_check.py и в тело сообщения коммита. **Имя папки дела — это
фамилия человека.** Коммит ушёл фоновым auto-sync в публичный репозиторий, и
правка файла историю уже не чистит.

ЧЕМ ЭТО ХУЖЕ ОБЫЧНОЙ ОПЕЧАТКИ. Фамилия + предмет дела («раздел имущества»,
«алименты», «банкротство») — это специальная категория сведений о частной жизни.
Опубликованный коммит индексируется и остаётся в форках и зеркалах даже после
удаления. Восстановить положение задним числом нельзя — можно только не допустить.

ЧТО ПРОВЕРЯЕТСЯ. Имена папок доверителей читаются С ДИСКА в момент запуска
(cases/*/), сам сторож их не хранит. Ищутся:
  • в содержимом файлов, попадающих в коммит;
  • в тексте сообщения коммита;
  • в путях добавляемых файлов.

Демо-дело (cases/ivanov-ivan) исключено намеренно: оно заведено как пример для
публичного репозитория и в .gitignore прописано белым списком.

УСТАНОВКА (делает install.sh, можно и руками):
    python3 scripts/pd_guard.py --install

ПРИМЕНЕНИЕ:
    python3 scripts/pd_guard.py --staged           # что уходит в коммит
    python3 scripts/pd_guard.py --msg FILE         # сообщение коммита
    python3 scripts/pd_guard.py --tree             # всё, что уже под контролем git
    python3 scripts/pd_guard.py --selftest

Код возврата: 0 — чисто; 1 — найдены персональные данные, коммит остановлен.
Сами найденные фамилии в вывод НЕ печатаются: сторож не должен становиться
вторым каналом утечки. Печатается файл, строка и длина совпадения.
"""
import argparse
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "cases")
# Заведено как публичный пример, прописано белым списком в .gitignore.
DEMO = {"ivanov-ivan"}
# Служебные каталоги внутри cases/ — не доверители.
SERVICE_PREFIX = ("_", ".")
# Совсем короткие имена дают ложные срабатывания на обычных словах.
MIN_NAME = 5


def client_names(cases_dir: str = CASES) -> list[str]:
    """Имена папок доверителей С ДИСКА. Сторож их не хранит и не печатает."""
    if not os.path.isdir(cases_dir):
        return []
    return sorted(
        d for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
        and not d.startswith(SERVICE_PREFIX)
        and d not in DEMO
        and len(d) >= MIN_NAME)


# Обратная транслитерация (латиница → кириллица), самые длинные сочетания первыми:
# фамилия папки пишется латиницей, но в прозе (сообщение коммита, комментарий)
# её могут написать кириллицей — «Тестфама» вместо «testfam-ab».
_LAT2CYR = (
    ("shch", "щ"), ("sch", "щ"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"),
    ("sh", "ш"), ("zh", "ж"), ("yu", "ю"), ("iu", "ю"), ("ya", "я"), ("ia", "я"),
    ("yo", "ё"), ("ye", "е"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"),
    ("z", "з"), ("i", "и"), ("y", "ы"), ("j", "й"), ("k", "к"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
    ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "к"), ("q", "к"),
    ("w", "в"), ("x", "кс"),
)


def _translit_to_cyrillic(latin: str) -> str:
    """Не точный ГОСТ — только чтобы поймать характерную часть фамилии в кириллице."""
    s = latin.lower()
    out, i = [], 0
    while i < len(s):
        for seq, cyr in _LAT2CYR:
            if s.startswith(seq, i):
                out.append(cyr)
                i += len(seq)
                break
        else:
            i += 1
    return "".join(out)


def _owner_stems() -> set[str]:
    """Кириллические стемы фамилии владельца из git config — его фамилия это
    публичный бренд фирмы (README, титул, подпись документов), не тайна доверителя.
    Латинское имя его папок из-под защиты НЕ выходит — исключение только для
    кириллической прозы."""
    r = subprocess.run(["git", "config", "user.name"], capture_output=True,
                       text=True, cwd=ROOT)
    stems = set()
    for word in (r.stdout or "").split():
        w = word.strip().lower()
        if len(w) >= 5:
            stems.add(w[:5])
    return stems


def name_pattern(names: list[str], cyrillic: bool = False) -> re.Pattern | None:
    """Один шаблон на все имена. Границы — чтобы `ivan` не ловился внутри `ivanov`.

    Регистронезависим, разделители `-`/`_`/пробел взаимозаменяемы (04.08.2026 —
    `Testfam-Ab`/`TESTFAM-AB`/`testfam_ab` проходили мимо).

    Кириллическая транслитерация (cyrillic=True) включается ТОЛЬКО для сообщения
    коммита: там фамилию пишут по-русски («по делу Тестфама»). К содержимому
    файлов кириллические стемы не применяются — транслит-стемы неизбежно
    совпадают со словами языка («индикатор», «печатает» — 16 ложных тревог по
    дереву за прогон 19.08.2026), а сторож с ложной тревогой на обиходе не живёт."""
    if not names:
        return None
    owner = _owner_stems()
    lat_bodies, cyr_bodies = [], []
    for n in sorted(names, key=len, reverse=True):
        parts = [p for p in re.split(r"[-_ ]+", n) if p]
        if not parts:
            continue
        lat_bodies.append(r"[-_ ]".join(re.escape(p) for p in parts))
        # Транслитерируется только ФАМИЛЬНАЯ часть (первая): вторая — имя или
        # инициалы, их кириллические стемы коротки и совпадают с обиходом.
        # Стем короче 5 букв в шаблон не идёт: «sud»→«суд» с хвостом [а-яё]{0,3}
        # ловил «суда», «судом», «судебн» — 97 ложных тревог по дереву за один
        # прогон (19.08.2026), а сторож с ложной тревогой на обиходе не живёт.
        fam = parts[0]
        if cyrillic and len(fam) >= MIN_NAME:
            cyr = _translit_to_cyrillic(fam)
            if len(cyr) >= 5 and cyr[:5] not in owner:
                cyr_bodies.append(re.escape(cyr) + r"[а-яё]{0,3}")
    body = "|".join(lat_bodies + cyr_bodies)
    if not body:
        return None
    # Граница — БУКВА, не «дефис/цифра». Имя папки дела в живой форме почти всегда
    # несёт хвост: «testfam-ab-2026.zip», «session-testfam-ab-19-08.md», «testfam-ab2».
    # Прежний класс держал дефис и цифру в границе, поэтому читал такой хвост
    # продолжением слова и пропускал имя целиком (проба 20.08.2026: три формы разом).
    # Буква слева/справа по-прежнему рвёт совпадение — «xfamiliya-abx» и
    # «familiya-abcd» продолжают молчать, иначе сторож краснел бы там, где имя лишь
    # кусок другого слова, и его выключили бы в первый день.
    return re.compile(rf"(?<![A-Za-zА-Яа-яЁё])({body})(?![A-Za-zА-Яа-яЁё])",
                      re.IGNORECASE)


def scan_text(text: str, pat: re.Pattern | None, where: str) -> list[str]:
    """Находки без раскрытия самой фамилии: файл, строка, длина совпадения."""
    if not pat or not text:
        return []
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in pat.finditer(line):
            out.append(f"{where}:{i} — имя папки доверителя ({len(m.group(1))} знаков). "
                       "Само значение не печатается: сторож не должен стать вторым "
                       "каналом утечки")
    return out


# Только категории со строгим форматом или явной меткой. pii_gate.residual_matches
# целиком (ФИО-эвристика, «cases/…», детские учреждения) написан для ДРУГОЙ
# задачи — обезличивания извлечённого текста дела перед отправкой наружу, где
# «слишком грубо» безопаснее «слишком мягко». Здесь сканируется код и документация
# ЭТОГО репозитория, где «cases/…» и упоминание суда — обиход через строку. Взятый
# сюда набор не пересекается с обиходом предметной области: паспорт/СНИЛС/кадастр/
# госномер/дата рождения не появляются в прозе о самом Фемиде НИКОГДА не как ПД.
_STRONG_PII_CATEGORIES = ("ПАСПОРТ", "СНИЛС", "КАДАСТР", "АВТОНОМЕР", "ДАТАРОЖД")


def scan_pii(text: str, where: str) -> list[str]:
    """Второй рубеж на пути коммита: паспорт/СНИЛС/кадастр/госномер/дата рождения
    без метки папки дела. pii_gate живёт своей жизнью (стадии 6/7), здесь его
    структурные шаблоны читаются как модуль этого же каталога — не второй канал."""
    if not text:
        return []
    try:
        import pii_gate
    except ImportError:
        return []
    try:
        cats = dict(pii_gate.CATEGORIES_STATIC)
        raw = [(m.start(), cat) for cat in _STRONG_PII_CATEGORIES
               for pat in cats.get(cat, ()) for m in pat.finditer(text)]
    except Exception:
        return []
    out = []
    for start, cat in raw:
        line_no = text.count("\n", 0, start) + 1
        out.append(f"{where}:{line_no} — похоже на персональные данные ({cat}), "
                   "не имя папки дела. Само значение не печатается")
    return out


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, cwd=ROOT)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def staged_files() -> list[str]:
    return [f for f in git("diff", "--cached", "--name-only", "--diff-filter=ACMRT").split("\n") if f]


def _is_test_fixture_code(path: str) -> bool:
    """`scripts/*.py` — валидаторы реквизитов (ИНН/СНИЛС/паспорт…), их `--selftest`
    штатно несёт СИНТЕТИЧЕСКИЕ примеры нужной формы («СНИЛС 123-456-789 64») —
    именно такими фикстурами код и проверяют. Реальная утечка ПД идёт другим
    каналом (имя папки дела в прозе/пути — `name_pattern` его ловит независимо
    от расширения файла); `scripts/` клиентских данных не несёт по правилам
    проекта. Найдено прогоном 19.08.2026: pii_gate.py и markdown_extract.py —
    оба заведомо чистый код — красили коммит по СВОИМ ЖЕ тестовым фикстурам."""
    return path.startswith("scripts/") and path.endswith(".py")


def check_staged(pat: re.Pattern | None) -> list[str]:
    problems = []
    for f in staged_files():
        problems += scan_text(f, pat, "путь файла")
        blob = git("show", f":{f}")
        if blob:
            problems += scan_text(blob, pat, f)
            if not _is_test_fixture_code(f):
                problems += scan_pii(blob, f)
    return problems


def check_push_refs(pat: re.Pattern | None, stdin: str | None = None) -> list[str]:
    """pre-push канал: имя ветки/тега тоже публичная ссылка."""
    text = sys.stdin.read() if stdin is None else stdin
    problems = []
    rows = [line.split() for line in text.splitlines() if line.strip()]
    refs = []
    for row in rows:
        if row:
            refs.append(row[0])
        if len(row) >= 3:
            refs.append(row[2])
    if not refs:
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD").strip()
        refs.extend([branch] if branch else [])
        refs.extend(x for x in git("tag", "--points-at", "HEAD").splitlines() if x)
    for ref in refs:
        problems += scan_text(ref, pat, "имя ветки/тега")
    return problems


def check_tree(pat: re.Pattern | None) -> list[str]:
    problems = []
    for f in [x for x in git("ls-files").split("\n") if x]:
        path = os.path.join(ROOT, f)
        if not os.path.isfile(path):
            continue
        problems += scan_text(f, pat, "путь файла")
        try:
            problems += scan_text(open(path, encoding="utf-8", errors="ignore").read(), pat, f)
        except OSError:
            continue
    return problems


def local_log_files(root: str = ROOT) -> list[str]:
    """Рабочие логи, где имена дел лежат законно: корневые *.log и всё под cases/_logs/."""
    out = list(glob.glob(os.path.join(root, "*.log")))
    out += [p for p in glob.glob(os.path.join(root, "cases", "_logs", "**", "*"), recursive=True)
            if os.path.isfile(p)]
    return sorted(set(out))


def check_local_logs(root: str = ROOT) -> list[str]:
    """Рабочие логи ОБЯЗАНЫ оставаться вне git.

    В `audit.log` и `cases/_logs/` имя дела пишется по делу — это работа, вычищать нечего.
    Опасность другая: правило `.gitignore` сломали или файл добавили `git add -f`, и вся
    история прогонов уезжает в публичный репозиторий разом. Сторож проверяет не текст,
    а статус: игнорируется и не отслеживается.
    """
    problems = []
    for path in local_log_files(root):
        rel = os.path.relpath(path, root)
        if subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=root).returncode != 0:
            problems.append(f"{rel} — рабочий лог НЕ покрыт .gitignore")
        tracked = subprocess.run(["git", "ls-files", "--", rel], cwd=root,
                                 capture_output=True, text=True).stdout.strip()
        if tracked:
            problems.append(f"{rel} — рабочий лог ОТСЛЕЖИВАЕТСЯ git")
    return problems


HOOK = """#!/bin/sh
# Поставлен scripts/pd_guard.py --install. Фамилия доверителя не уходит наружу.
exec python3 "$(git rev-parse --show-toplevel)/scripts/pd_guard.py" %s
"""


def install() -> int:
    hooks = git("rev-parse", "--git-path", "hooks").strip() or ".git/hooks"
    hooks = hooks if os.path.isabs(hooks) else os.path.join(ROOT, hooks)
    os.makedirs(hooks, exist_ok=True)
    for name, arg in (("pre-commit", "--staged"),
                      ("commit-msg", '--msg "$1"'),
                      ("pre-push", "--push")):
        path = os.path.join(hooks, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(HOOK % arg)
        os.chmod(path, 0o755)
        print(f"поставлен {path}")
    print("Теперь коммит с фамилией доверителя не пройдёт ни содержимым, ни сообщением.")
    return 0


def report(problems: list[str], what: str) -> int:
    if not problems:
        print(f"✓ ПД-сторож: {what} — чисто")
        return 0
    print(f"\n⛔ ПЕРСОНАЛЬНЫЕ ДАННЫЕ В {what.upper()}: находок {len(problems)}",
          file=sys.stderr)
    for p in problems[:20]:
        print(f"   • {p}", file=sys.stderr)
    if len(problems) > 20:
        print(f"   … и ещё {len(problems) - 20}", file=sys.stderr)
    print("\nИмя папки дела — это фамилия человека. Репозиторий публичный, а "
          "опубликованный коммит остаётся в форках и зеркалах после удаления.\n"
          "Что делать: описать прецедент обезличенно (что произошло, а не с кем), "
          "в фикстурах использовать вымышленные фамилии.\n"
          "Обойти осознанно (демо-дело, ложное совпадение): PD_GUARD=0 git commit …",
          file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Сторож персональных данных")
    ap.add_argument("--staged", action="store_true", help="проверить индекс коммита")
    ap.add_argument("--msg", metavar="FILE", help="проверить сообщение коммита")
    ap.add_argument("--tree", action="store_true", help="проверить всё дерево git")
    ap.add_argument("--local-logs", action="store_true",
                    help="рабочие логи (audit.log, cases/_logs/) вне git")
    ap.add_argument("--push", action="store_true", help="проверить имена веток/тегов pre-push")
    ap.add_argument("--install", action="store_true", help="поставить git-хуки")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.install:
        return install()
    if os.environ.get("PD_GUARD") == "0":
        print("ПД-сторож выключен переменной PD_GUARD=0 — под ответственность автора коммита",
              file=sys.stderr)
        return 0

    pat = name_pattern(client_names())
    if a.msg:
        pat = name_pattern(client_names(), cyrillic=True)
        try:
            text = open(a.msg, encoding="utf-8", errors="ignore").read()
        except OSError as e:
            print(f"сообщение коммита не прочитано ({e})", file=sys.stderr)
            return 0
        return report(scan_text(text, pat, "сообщение коммита"), "сообщении коммита")
    if a.local_logs:
        return report(check_local_logs(), "рабочих логах")
    if a.push:
        return report(check_push_refs(pat), "имени ветки или тега")
    if a.tree:
        return report(check_tree(pat), "дереве git")
    if a.staged:
        return report(check_staged(pat), "коммите")
    ap.print_help()
    return 2


def _local_logs_probe(force_add: bool = False) -> int:
    """Синтетический репозиторий: рабочий лог игнорируется, а насильно добавленный — ловится."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pdguard-logs-") as tmp:
        os.makedirs(os.path.join(tmp, "cases", "_logs"))
        with open(os.path.join(tmp, "audit.log"), "w", encoding="utf-8") as f:
            f.write("прогон по делу\n")
        with open(os.path.join(tmp, "cases", "_logs", "session_18-08-2026.md"), "w",
                  encoding="utf-8") as f:
            f.write("разбор\n")
        with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*.log\ncases/_logs/\n")
        for cmd in (["init", "-q"], ["add", ".gitignore"]):
            subprocess.run(["git", *cmd], cwd=tmp, capture_output=True)
        if force_add:
            subprocess.run(["git", "add", "-f", "audit.log"], cwd=tmp, capture_output=True)
        return len(check_local_logs(tmp))


def selftest() -> int:
    import tempfile
    tmp = tempfile.mkdtemp()
    cases = os.path.join(tmp, "cases")
    for d in ("familiya-ab", "drugoy-vg", "ivanov-ivan", "_templates", "ab"):
        os.makedirs(os.path.join(cases, d))
    names = client_names(cases)
    pat = name_pattern(names)
    pat_msg = name_pattern(names, cyrillic=True)

    checks = [
        # Кириллица: пара «утечка в сообщении коммита + обиход в содержимом».
        ("кириллическая фамилия в сообщении коммита ловится",
         len(scan_text("fix: возражения по делу Фамилияна", pat_msg, "msg")) >= 1),
        ("кириллический стем НЕ применяется к содержимому файлов",
         scan_text("возражения по делу Фамилияна", pat, "f.py") == []),
        ("обиход в сообщении коммита молчит",
         scan_text("docs: Постановление и Апелляционное определение разобраны",
                   pat_msg, "msg") == []),
        ("регистр и разделитель нормализованы",
         all(len(scan_text(v, pat, "f")) == 1
             for v in ("Familiya-Ab", "FAMILIYA-AB", "familiya_ab", "familiya ab"))),
        ("имена доверителей прочитаны с диска", set(names) == {"familiya-ab", "drugoy-vg"}),
        # Демо-дело заведено как публичный пример — оно не ПД.
        ("демо-дело исключено", "ivanov-ivan" not in names),
        ("служебная папка не считается доверителем", "_templates" not in names),
        ("слишком короткое имя не берётся", "ab" not in names),
        # Ровно тот случай, который и произошёл 04.08.2026.
        ("фамилия в комментарии кода ловится",
         len(scan_text("# прецедент: familiya-ab и drugoy-vg", pat, "f.py")) == 2),
        ("фамилия в сообщении коммита ловится",
         len(scan_text("fix: развёл familiya-ab и её двойника", pat, "msg")) == 1),
        ("фамилия в пути файла ловится",
         len(scan_text("cases/familiya-ab/delo-2026/x.md", pat, "путь")) == 1),
        ("фамилия в имени ветки ловится pre-push",
         len(check_push_refs(pat, "refs/heads/autoloop/familiya-ab abc "
                             "refs/heads/autoloop/familiya-ab abc\n")) == 2),
        ("обычное имя ветки проходит pre-push",
         check_push_refs(pat, "refs/heads/fix-guard abc refs/heads/fix-guard abc\n") == []),
        ("чистый текст проходит", scan_text("обычный комментарий про реестр", pat, "f") == []),
        # Границы: имя не должно ловиться внутри другого слова, иначе сторож
        # начнёт краснеть на ровном месте и его выключат.
        ("имя внутри длинного слова не ловится",
         scan_text("xfamiliya-abx", pat, "f") == []),
        ("имя с дефисом внутри длинного не ловится",
         scan_text("familiya-abcd", pat, "f") == []),
        ("имя в кавычках ловится", len(scan_text('"familiya-ab"', pat, "f")) == 1),
        ("имя в конце строки ловится", len(scan_text("папка familiya-ab", pat, "f")) == 1),
        # Сторож не печатает саму фамилию — иначе он второй канал утечки.
        ("находка не раскрывает фамилию",
         all("familiya-ab" not in p for p in scan_text("familiya-ab", pat, "f"))),
        ("находка называет файл и строку",
         "f.py:1" in scan_text("familiya-ab", pat, "f.py")[0]),
        # Рабочие логи: сторож смотрит не на текст, а на статус в git.
        ("рабочий лог под .gitignore проходит", _local_logs_probe() == 0),
        ("рабочий лог, добавленный в git, ловится", _local_logs_probe(force_add=True) > 0),
        ("пустой список имён никого не ловит",
         scan_text("familiya-ab", name_pattern([]), "f") == []),
        ("отчёт по находкам даёт код 1", report(["x"], "тесте") == 1),
        ("отчёт без находок даёт код 0", report([], "тесте") == 0),
        # Пара «утечка + обиход» для scan_pii (найдено прогоном 19.08.2026:
        # pii_gate.py/markdown_extract.py красили СВОИМИ ЖЕ тестовыми фикстурами
        # СНИЛС-формы «123-456-789 64» — валидатор не должен ловить собственные
        # примеры формата, но обязан по-прежнему ловить тот же литерал в прозе.
        ("scripts/*.py опознаётся как тестовый код",
         _is_test_fixture_code("scripts/pii_gate.py")),
        ("cases/…/x.py тестовым кодом НЕ считается — не тот канал",
         not _is_test_fixture_code("cases/klient/delo/x.py")),
        ("knowledge/x.md тестовым кодом не считается",
         not _is_test_fixture_code("knowledge/x.md")),
        ("тот же литерал в .md по-прежнему ловится scan_pii (утечка не потеряна)",
         len(scan_pii("СНИЛС 123-456-789 64", "note.md")) >= 1),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
