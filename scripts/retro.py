#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retro.py — разбор работы: где ушли токены и какой урок из этого следует.

ЗАЧЕМ. Система растет только если каждая работа заканчивается разбором, а не
словом «готово». Иначе одна и та же ошибка повторяется, а расход остается
загадкой: «дорого получилось» — не диагноз и не повод ничего менять.

ПОЧЕМУ ПРИБОРОМ, А НЕ ПАМЯТЬЮ. Самоотчет модели о собственном расходе
недостоверен: поле `usage` субагента показывает последнюю итерацию и не видит
основной поток, занижая расход в 40-100 раз (замер 03.08.2026). И тот же урок
шире: правило, исполняемое по памяти, исполняется вероятностно. Поэтому разбор
не «положено делать», а МЕХАНИЧЕСКИ НЕ ЗАКРЫВАЕТСЯ, пока урок не записан:
без свежей записи в knowledge/lessons-log.md скрипт возвращает 1.

ЧТО ОТВЕЧАЕТ. Ровно три вопроса, ради которых разбор и нужен:
  1. Куда ушли токены — по статьям, шагам протокола и агентам.
  2. Почему именно туда — доминанта названа, с объяснением и что с ней делать.
  3. Что из этого следует — записан ли урок, и если нет, то работа не закрыта.

    python3 scripts/retro.py                       # разбор текущей сессии
    python3 scripts/retro.py --track FAST          # + сверка с нормой трека
    python3 scripts/retro.py --lesson "текст"      # записать урок и закрыть разбор
    python3 scripts/retro.py --json
    python3 scripts/retro.py --selftest

Код возврата: 0 — разбор закрыт (урок записан сегодня); 1 — урок не записан,
работа НЕ закончена; 2 — журнал расхода не найден либо не разобран.
"""
import argparse
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LESSONS = os.path.join(ROOT, "knowledge", "lessons-log.md")

# Доля cache-read, выше которой расход объясняется НЕ работой, а длиной контекста:
# каждое обращение к инструменту заново оплачивает весь накопленный контекст.
# Замер по сессиям проекта: 97-99% — обычное дело для длинной сессии.
CACHE_READ_DOMINANT = 90.0
# Доля «прочего», выше которой разбивке по шагам верить нельзя.
OTHER_ALERT = 5.0
# Доля одного шага, выше которой он и есть ответ на вопрос «куда ушли токены».
STEP_DOMINANT = 35.0


def lesson_written(path: str = LESSONS, today: str | None = None) -> bool:
    """Записан ли урок СЕГОДНЯ. Дата берется из заголовка урока, не из mtime:
    файл трогают и правкой опечатки, а урок — это новая запись."""
    today = today or date.today().strftime("%d.%m.%Y")
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return False
    return bool(re.search(rf"^##\s*{re.escape(today)}\s*[—-]", text, re.M))


def headline(text: str, limit: int = 80) -> str:
    """Заголовок урока: до конца мысли, а не до восьмидесятого знака.

    Резать по числу знаков — значит обрывать слово посреди («…попадает в ко»),
    и заголовок в журнале приходится править руками, то есть правило теряет
    вид правила. Берем первую мысль до разделителя (→, точка, точка с запятой,
    тире), а если она длиннее предела — режем по границе слова.
    """
    first = text.strip().split("\n")[0].strip()
    for sep in ("→", ". ", "; ", " — "):
        if sep in first:
            candidate = first.split(sep)[0].strip(" .;—")
            if candidate:
                first = candidate
                break
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0].rstrip(" ,:;—-")
    return cut or first[:limit]


def append_lesson(text: str, path: str = LESSONS, today: str | None = None) -> str:
    """Дописать урок с датой в заголовке. Возвращает записанный заголовок."""
    today = today or date.today().strftime("%d.%m.%Y")
    head = headline(text)
    body = f"\n## {today} — {head}\n\n{text.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(body)
    return f"{today} — {head}"


def share(part: int, whole: int) -> float:
    return (part / whole * 100) if whole else 0.0


def diagnose(rep: dict, track: str | None = None) -> list[str]:
    """Почему токены ушли именно туда. Наблюдение + что с ним делать."""
    from token_ledger import TRACK_BUDGET, blank, tokens

    total = tokens(rep["total"])
    out: list[str] = []
    if not total:
        return ["данных о расходе нет — разобран не тот файл сессии либо логи "
                "еще не сброшены на диск"]

    t = rep["total"]
    cr = share(t.get("cr", 0), total)
    if cr > CACHE_READ_DOMINANT:
        calls = sum(v.get("calls", 0) for v in rep["by_step"].values())
        out.append(
            f"cache-read {cr:.1f}% расхода при {calls} обращениях — платили не за "
            "работу, а за ДЛИНУ КОНТЕКСТА: каждое обращение к инструменту заново "
            "оплачивает все накопленное. Лечится не краткостью ответов, а границей "
            "сессии: одна тема — одна сессия, между несвязанными задачами /clear.")
    outp = share(t.get("out", 0), total)
    if outp > 3.0:
        out.append(f"output {outp:.1f}% — необычно много для этого проекта; проверить, "
                   "не переписывались ли целиком файлы, которые правятся точечно "
                   "(Edit-патч вместо перегенерации).")

    steps = {k: tokens(v) for k, v in rep["by_step"].items() if k != "основной поток"}
    if steps:
        top, val = max(steps.items(), key=lambda kv: kv[1])
        s = share(val, total)
        if s > STEP_DOMINANT:
            out.append(f"доминанта — шаг «{top}»: {val:,} токенов ({s:.1f}%). "
                       "Это и есть ответ на «куда ушло»; оптимизировать надо его, "
                       "а не все подряд.".replace(",", " "))
    other = share(tokens(rep["by_step"].get("прочее") or blank()), total)
    if other > OTHER_ALERT:
        out.append(f"в «прочее» упало {other:.1f}% — разбивке по шагам верить нельзя, "
                   "пока вызовы не получат внятные описания.")

    if track and track in TRACK_BUDGET:
        limit = TRACK_BUDGET[track]
        if total > limit:
            out.append(f"трек {track}: перерасход ×{total / limit:.2f} против измеренной "
                       "нормы — назвать причину поименно, а не списать на «сложное дело».")
        else:
            out.append(f"трек {track}: {share(total, limit):.0f}% нормы — в пределах.")
    if not out:
        out.append("аномалий в расходе нет: ни доминанты шага, ни перекоса статей.")
    return out


def session_path(session: str | None, cwd: str) -> tuple[str | None, str | None]:
    """Найти журнал либо назвать точную причину, почему разбор невозможен."""
    from token_ledger import latest_session, project_dir

    if session:
        if os.path.isfile(session):
            return session, None
        return None, f"файл сессии не найден по пути {session}"
    try:
        sessions_dir = project_dir(cwd)
    except Exception as e:
        return None, f"путь каталога сессий не вычислен: {e}"
    if not sessions_dir:
        return None, "путь каталога сессий не вычислен"
    if not os.path.isdir(sessions_dir):
        return None, f"каталог сессий не найден по пути {sessions_dir}"
    try:
        path = latest_session(cwd)
    except Exception as e:
        return None, f"путь журнала сессии не вычислен для каталога {sessions_dir}: {e}"
    if not path:
        try:
            with os.scandir(sessions_dir) as entries:
                empty = next(entries, None) is None
        except OSError as e:
            return None, f"каталог сессий не прочитан по пути {sessions_dir}: {e}"
        if empty:
            return None, f"каталог сессий пуст по пути {sessions_dir}"
        return None, f"журнал сессии не найден в непустом каталоге {sessions_dir}"
    if not os.path.isfile(path):
        return None, f"файл сессии не найден по пути {path}"
    return path, None


def collect_retro(session: str | None = None, track: str | None = None) -> dict:
    from token_ledger import collect, tokens

    path, error = session_path(session, os.getcwd())
    if error:
        return {"ошибка": error, "итого": 0}
    try:
        rep = collect(path)
    except Exception as e:
        return {"ошибка": f"журнал сессии не разобран по пути {path}: {e}", "итого": 0}
    total = tokens(rep["total"])
    steps = sorted(((k, tokens(v)) for k, v in rep["by_step"].items()),
                   key=lambda kv: -kv[1])
    agents = sorted(((k, tokens(v)) for k, v in rep["by_agent"].items()),
                    key=lambda kv: -kv[1])[:5]
    return {
        "сессия": os.path.basename(path),
        "итого": total,
        "деньги": round(rep.get("money", 0.0), 2),
        "статьи": {k: rep["total"].get(k, 0) for k in ("in", "out", "cw", "cr")},
        "шаги": [{"шаг": k, "токенов": v, "доля": round(share(v, total), 1)}
                 for k, v in steps if v],
        "агенты": [{"агент": k, "токенов": v} for k, v in agents if v],
        "диагноз": diagnose(rep, track),
        "урок записан сегодня": lesson_written(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Разбор работы: расход и урок")
    ap.add_argument("session", nargs="?", help="файл сессии (по умолчанию последний)")
    ap.add_argument("--track", choices=["MICRO", "FAST", "FULL"])
    ap.add_argument("--lesson", metavar="ТЕКСТ",
                    help="записать урок в knowledge/lessons-log.md и закрыть разбор")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if a.lesson is not None and not a.lesson.strip():
        # Пустой урок разбор не закрывает: иначе `--lesson " "` ставит заголовок с
        # сегодняшней датой, и любая работа дня считается разобранной. Отказ — ДО
        # записи, журнал не остается полуписаным.
        print("⛔ урок пуст: разбор не закрывается пустой записью. Нужен текст правила: "
              "что было не так → почему → какое правило это закрывает.", file=sys.stderr)
        return 1
    r = collect_retro(a.session, a.track)
    if r.get("ошибка"):
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        print(f"⛔ РАЗБОР НЕ ЗАКРЫТ: расход не измерен: {r['ошибка']}.", file=sys.stderr)
        return 2
    if a.lesson:
        print("урок записан:", append_lesson(a.lesson))
        r["урок записан сегодня"] = True
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"РАЗБОР РАБОТЫ · сессия {r['сессия']}")
        print(f"итого {r['итого']:,} токенов ≈ ${r['деньги']}".replace(",", " "))
        st = r["статьи"]
        print(f"  вход {st['in']:,} · выход {st['out']:,} · "
              f"запись кеша {st['cw']:,} · чтение кеша {st['cr']:,}".replace(",", " "))
        if len(r["шаги"]) > 1:
            print("\nкуда ушло:")
            for s in r["шаги"][:6]:
                print(f"  {s['шаг']:<20}{s['токенов']:>14,}{s['доля']:>7.1f}%".replace(",", " "))
        print("\nпочему:")
        for d in r["диагноз"]:
            print(f"  • {d}")
    if not r["итого"]:
        return 2
    if not r["урок записан сегодня"]:
        print("\n⛔ РАЗБОР НЕ ЗАКРЫТ: урока за сегодня в knowledge/lessons-log.md нет.\n"
              "   Работа не заканчивается словом «готово». Записать:\n"
              '   python3 scripts/retro.py --lesson "что было не так → почему → '
              'какое правило это закрывает"\n'
              "   Урок — это ПРАВИЛО на будущее, а не пересказ сделанного.",
              file=sys.stderr)
        return 1
    print("\n✓ разбор закрыт: урок за сегодня записан.")
    return 0


def selftest() -> int:
    import shutil
    import subprocess
    import tempfile
    import token_ledger

    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "lessons.md")
    open(log, "w", encoding="utf-8").write("# Уроки\n\n## 01.01.2020 — старый урок\n\nтекст\n")

    def rep_of(total_parts, steps, agents=None):
        by_step = {}
        for k, v in steps.items():
            by_step[k] = {"in": 0, "out": 0, "cw": 0, "cr": v, "calls": 3}
        return {"total": total_parts, "by_step": by_step,
                "by_agent": agents or {}, "money": 1.0}

    # Длинная сессия: платили за длину контекста, а не за работу.
    long_ctx = rep_of({"in": 1000, "out": 2000, "cw": 3000, "cr": 994000, "calls": 100},
                      {"основной поток": 994000})
    # Дорогой шаг практики — доминанта.
    hunt = rep_of({"in": 10, "out": 10, "cw": 10, "cr": 970, "calls": 5},
                  {"2 практика": 700, "1 карта": 200, "4 составление": 100})
    # Нераспределенный расход.
    murky = rep_of({"in": 10, "out": 10, "cw": 10, "cr": 970, "calls": 5},
                   {"1 карта": 500, "прочее": 500})

    checks = [
        # Урок ищется по ДАТЕ В ЗАГОЛОВКЕ, а не по времени правки файла:
        # опечатку правят тоже, и mtime врет.
        ("урок за сегодня не найден в старом логе", not lesson_written(log)),
        ("урок за сегодня находится после записи",
         bool(append_lesson("новое правило", log)) and lesson_written(log)),
        ("урок за чужую дату не засчитывается",
         not lesson_written(log, today="02.02.2019")),
        ("заголовок урока несет дату и суть",
         append_lesson("правило про кеш", log).startswith(
             date.today().strftime("%d.%m.%Y"))),
        # Заголовок дважды за сессию 04.08.2026 обрывался посреди слова, и его
        # правили руками. Правило, которое надо править руками, — не правило.
        # Свойство, а не совпадение с конкретной строкой: срез обязан кончаться
        # там, где в оригинале пробел. Проверка «не заканчивается на "ко"» ловила
        # одну фразу и пропускала мутацию — сама была бесполезной.
        ("длинный урок режется по границе слова, а не по знаку",
         (lambda t, h: t.startswith(h) and (len(h) == len(t) or t[len(h)] == " "))(
             "Экономят не краткостью ответов а тем что вообще попадает "
             "в контекст сессии и остается там до конца",
             headline("Экономят не краткостью ответов а тем что вообще попадает "
                      "в контекст сессии и остается там до конца"))),
        ("заголовок берет мысль до стрелки",
         headline("правило про кеш → потому что контекст дорог") == "правило про кеш"),
        ("заголовок берет мысль до точки",
         headline("Не читать заранее. Прочитанное платится до конца сессии.")
         == "Не читать заранее"),
        ("короткий урок не калечится", headline("правило про кеш") == "правило про кеш"),
        ("предел соблюдается", len(headline("слово " * 40)) <= 80),
        ("урок без разделителей все равно дает заголовок",
         headline("сплошнойтекстбезпробеловидлиннее" * 4) != ""),
        # Диагноз обязан НАЗЫВАТЬ причину, а не констатировать сумму.
        ("длина контекста опознана как причина расхода",
         any("ДЛИНУ КОНТЕКСТА" in d for d in diagnose(long_ctx))),
        ("совет по длине контекста — про границу сессии, а не про краткость",
         any("одна тема — одна сессия" in d for d in diagnose(long_ctx))),
        ("доминанта шага названа", any("2 практика" in d for d in diagnose(hunt))),
        ("доминанта названа с долей", any("%" in d for d in diagnose(hunt))),
        ("нераспределенный расход поднимает тревогу",
         any("прочее" in d for d in diagnose(murky))),
        # Ровный прогон: ни одна статья не перекошена, ни один шаг не съел
        # больше трети. Прибор обязан молчать, а не выдумывать проблему — иначе
        # его вывод обесценится и разбор станет ритуалом.
        ("чистая сессия не выдумывает аномалий",
         any("аномалий в расходе нет" in d
             for d in diagnose(rep_of({"in": 500, "out": 20, "cw": 10, "cr": 470},
                                      {"1 карта": 300, "2 практика": 250,
                                       "3 позиция": 250, "4 составление": 200})))),
        ("пустой расход назван прямо",
         any("данных о расходе нет" in d
             for d in diagnose(rep_of({"in": 0, "out": 0, "cw": 0, "cr": 0}, {})))),
        # Сверка с нормой трека.
        ("перерасход трека назван",
         any("перерасход" in d for d in diagnose(
             rep_of({"in": 0, "out": 0, "cw": 0, "cr": 500_000_000},
                    {"2 практика": 500_000_000}), track="FAST"))),
        ("расход в пределах нормы назван",
         any("в пределах" in d for d in diagnose(
             rep_of({"in": 0, "out": 0, "cw": 0, "cr": 1_000_000},
                    {"1 карта": 1_000_000}), track="FULL"))),
        ("доли считаются от целого", share(25, 100) == 25.0),
        ("деление на ноль не роняет", share(5, 0) == 0.0),
    ]

    # Нет каталога, пустой каталог и нечитаемый журнал — разные причины отказа.
    home0 = os.environ.get("HOME")
    cwd = os.getcwd()
    sessions_tmp = os.path.join(tmp, "sessions")
    os.makedirs(sessions_tmp)
    try:
        missing_home = os.path.join(sessions_tmp, "missing")
        os.makedirs(missing_home)
        os.environ["HOME"] = missing_home
        missing = collect_retro()
        missing_cli = subprocess.run(
            [sys.executable, os.path.abspath(__file__)], cwd=cwd,
            env={**os.environ, "HOME": missing_home}, text=True,
            capture_output=True, check=False)
        missing_output = missing_cli.stdout + missing_cli.stderr
        isolated = os.path.join(tmp, "isolated")
        isolated_scripts = os.path.join(isolated, "scripts")
        os.makedirs(isolated_scripts)
        os.makedirs(os.path.join(isolated, "knowledge"))
        # Копия ПОЛНАЯ: все модули scripts/ верхнего уровня, а не список имен.
        # Список разошелся сразу: после M04 token_ledger импортирует _obshee,
        # неполная копия умирала от окружения, не дойдя до проверяемого условия.
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        for f in os.listdir(scripts_dir):
            if f.endswith(".py"):
                shutil.copy2(os.path.join(scripts_dir, f),
                             os.path.join(isolated_scripts, f))
        isolated_retro = os.path.join(isolated_scripts, "retro.py")
        close_log = os.path.join(isolated, "knowledge", "lessons-log.md")
        before = "# Уроки\n"
        open(close_log, "w", encoding="utf-8").write(before)
        blocked = subprocess.run(
            [sys.executable, isolated_retro, "--lesson", "не писать урок без журнала"],
            cwd=isolated, env={**os.environ, "HOME": missing_home},
            text=True, capture_output=True, check=False)
        blocked_lesson = open(close_log, encoding="utf-8").read() != before

        empty_home = os.path.join(sessions_tmp, "empty")
        os.environ["HOME"] = empty_home
        os.makedirs(token_ledger.project_dir(cwd))
        empty = collect_retro()

        broken_home = os.path.join(sessions_tmp, "broken")
        os.environ["HOME"] = broken_home
        broken_dir = token_ledger.project_dir(cwd)
        os.makedirs(broken_dir)
        with open(os.path.join(broken_dir, "broken.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        collect0 = token_ledger.collect

        def unreadable(_path: str) -> dict:
            raise OSError("синтетически нечитаемый журнал")

        token_ledger.collect = unreadable
        try:
            broken = collect_retro()
        finally:
            token_ledger.collect = collect0
    finally:
        if home0 is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = home0
    checks += [
        ("нет каталога → отказ называет путь",
         "каталог сессий не найден по пути" in missing.get("ошибка", "")),
        ("retro без каталога не закрывает прогон и называет причину",
         missing_cli.returncode == 2 and "РАЗБОР НЕ ЗАКРЫТ" in missing_output
         and "каталог сессий не найден по пути" in missing_output),
        ("retro --lesson без журнала не пишет урок",
         blocked.returncode == 2 and not blocked_lesson),
        ("пустой каталог назван отдельно",
         "каталог сессий пуст по пути" in empty.get("ошибка", "")),
        ("нечитаемый журнал назван отдельно",
         "не разобран" in broken.get("ошибка", "")),
    ]
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
