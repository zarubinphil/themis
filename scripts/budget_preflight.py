#!/usr/bin/env python3
"""budget_preflight.py — проверка бюджета ПЕРЕД запуском дорогого трека.

Зачем. FULL-прогон (полный рой охотников, советы, reconciler) стоит на порядок
дороже FAST и в разы дороже MICRO. Узнать о перерасходе ПОСЛЕ прогона (как это
делает token_ledger на чекпойнтах) — значит уже сжечь деньги. Этот прибор
считает наперед: хватит ли остатка лимита на трек, и если нет — FULL не стартует
(код 3), а владелец решает, что делать. Дешевая страховка перед дорогой работой.

Почему расход берется с диска, а не из самоотчета. Модель не знает, сколько уже
потрачено этой сессией: поле `usage` субагента видит только последнюю итерацию и
занижает расход в десятки раз (прецедент token_ledger). Поэтому уже-потраченное
берем тем же прибором, что и чекпойнты — token_ledger.collect по session-JSONL с
диска. Один источник правды на весь учет токенов.

Оценка стоимости трека. TRACK_BUDGET из token_ledger задает типовой объем трека в
токенах (измеренная база проекта). Перевод токен→доллар — по МЕДИАНЕ смешанного
тарифа ЗАВЕРШЕННЫХ сессий проекта, не по текущей. Текущая сессия то читает кеш
(дешево), то пишет его (дорого), и ее тариф гуляет 1.9→15 — оценка по ней
невоспроизводима (R02: три прогона одного трека дали $374 / $685 / $3044). Медиана
завершенных сессий устойчива к разовому выбросу и не меняется между прогонами без
новой работы. Завершенных сессий мало → берем запасной тариф и ВСЛУХ помечаем
оценку грубой. Три исхода — три кода: 0 хватает, 3 не хватает лимита, 4 расход
этой сессии не измерен.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import subprocess
import sys
import tempfile

# Прибор лежит рядом с token_ledger в scripts/ — его каталог первым в sys.path,
# поэтому прямой импорт работает без возни с путями.
import token_ledger

# Фактический смешанный тариф проекта, $/млн токенов (замер 19.08.2026: $112.32 /
# 60.0 млн ток = 1.87). Кеш-чтение доминирует, оттого дешево. Это ЗАПАСНОЙ тариф:
# берется, только когда завершенных сессий на диске мало и медиане верить нельзя,
# и тогда оценка ВСЛУХ помечается грубой. При достатке сессий тариф — их медиана.
# ponytail: одна калибровочная константа; путь апгрейда — уже основной (медиана с диска).
DEFAULT_BLEND_PER_MTOK = 1.87

# Тариф считается по ЗАВЕРШЕННЫМ сессиям, а не по текущей. Причина: текущая сессия
# то читает кеш (дешево ~1.9), то пишет (дорого ~15), и ее деньги/токены растут на
# ходу — оценка по ней невоспроизводима (три прогона R02 дали $374 / $685 / $3044 на
# одном треке). Завершенный журнал больше не дописывается, значит его тариф стабилен.
#   COMPARABLE_WINDOW — сколько самых свежих завершенных сессий берем на медиану.
#     20: достаточно, чтобы медиана устоялась, и не тянем весь архив проекта.
#   MIN_COMPARABLE — ниже этого числа достоверных сессий медиане не верим → грубо.
#   MIN_SESSION_TOKENS — порог «сопоставимого класса»: сессия в пару реплик дает
#     шумный тариф (blend скачет на малом объеме), рабочий прогон — устойчивый.
# ponytail: линейный проход по журналам проекта на каждый preflight; потолок —
# COMPARABLE_WINDOW (ранний выход). Тяжелее станет — кешировать медиану по mtime.
COMPARABLE_WINDOW = 20
MIN_COMPARABLE = 3
MIN_SESSION_TOKENS = 1_000_000

# Три исхода — РАЗНЫЕ коды: неизвестный расход и нехватка лимита чинятся по-разному
# (ровно то различие, ради которого делалась R02 — не сваливать их в один код 3).
EXIT_OK = 0
EXIT_OVER = 3        # лимита не хватает на трек
EXIT_UNMEASURED = 4  # расход этой сессии не измерен

# Умолчание лимита — в ОДНОМ месте политики. Без --limit гейт не отключается («трек
# всегда зеленый»), а стережет по этому потолку: дорогой трек без явного лимита-
# разрешения не стартует при крупном уже-потраченном. Явный --limit его перебивает.
# 02.09.2026, решение владельца: потолок поднят 500 -> 600. Причина замером, не на глаз:
# прибор воспроизводимо меряет полный трек дела в $517,72, то есть трек честно дороже
# заложенного, а не перерасходует. Откат - вернуть 500.0.
DEFAULT_LIMIT = 600.0


def track_estimate(track: str, blend_per_mtok: float) -> float:
    """Оценка полной стоимости трека в долларах."""
    return token_ledger.TRACK_BUDGET[track] / 1e6 * blend_per_mtok


def decide(track: str, limit: float, spent: float, blend_per_mtok: float) -> int:
    """Код возврата: 0 — остатка лимита хватает на трек, 3 — не хватает.

    Остаток = лимит − уже потрачено. Трек стартует, только если остаток покрывает
    его оценку целиком: начать и упереться в потолок посреди роя — худший исход,
    чем не начать.
    """
    remaining = limit - spent
    return EXIT_OK if remaining >= track_estimate(track, blend_per_mtok) else EXIT_OVER


def _collect_resilient(path: str) -> dict:
    """token_ledger.collect, но одна битая ЗАПИСЬ не обнуляет весь файл.

    collect падает на строке-не-объекте (валидный JSON чужой формы, напр. список):
    `entry.get(...)` бросает AttributeError. Фолбэк — пересобрать по копии без битых
    строк, сохранив сайдкар субагентов симлинком (счет роя остается полным). Так
    разбор пропускает битую ЗАПИСЬ, а не весь файл: неизвестный расход хуже
    известного большого."""
    try:
        return token_ledger.collect(path)
    except Exception:
        pass
    good = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue          # оборванная строка — не считаем и не падаем
            if isinstance(obj, dict):
                good.append(line)
    base = os.path.basename(path)
    sidecar = os.path.join(os.path.dirname(path), base[: -len(".jsonl")])
    with tempfile.TemporaryDirectory(prefix="budget-resilient-") as td:
        dst = os.path.join(td, base)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write("\n".join(good) + "\n")
        if os.path.isdir(sidecar):
            try:
                os.symlink(sidecar, os.path.join(td, base[: -len(".jsonl")]))
            except OSError:
                pass              # без сайдкара счет роя неполон, но не нулевой
        return token_ledger.collect(dst)


def live_session_spend(cwd: str) -> tuple[float | None, str | None, str | None]:
    """С диска: (уже потрачено этой сессией $, причина отказа, путь живой сессии).

    Живая сессия = свежайший журнал проекта: в него пишется текущий прогон, и
    именно его деньги — «уже потрачено». Существующий пустой каталог → честный
    ноль. Нет каталога/пути либо журнал не разбирается → расход НЕИЗВЕСТЕН и
    причина названа."""
    try:
        sessions_dir = token_ledger.project_dir(cwd)
    except Exception as e:
        return None, f"путь каталога сессий не вычислен: {e}", None
    if not sessions_dir:
        return None, "путь каталога сессий не вычислен", None
    if not os.path.isdir(sessions_dir):
        return None, f"каталог сессий не найден по пути {sessions_dir}", None
    try:
        path = token_ledger.latest_session(cwd)
    except Exception as e:
        return None, f"путь журнала сессии не вычислен для каталога {sessions_dir}: {e}", None
    if not path:
        try:
            with os.scandir(sessions_dir) as entries:
                empty = next(entries, None) is None
        except OSError as e:
            return None, f"каталог сессий не прочитан по пути {sessions_dir}: {e}", None
        if empty:
            return 0.0, None, None
        return None, f"журнал сессии не найден в непустом каталоге {sessions_dir}", None
    if not os.path.isfile(path):
        return None, f"файл сессии не найден по пути {path}", None
    try:
        rep = _collect_resilient(path)
    except Exception as e:
        return None, f"журнал сессии не разобран по пути {path}: {e}", None
    return rep["money"], None, path


def completed_blends(cwd: str, exclude_path: str | None) -> list[float]:
    """Тарифы завершенных сессий ($/млн ток), свежие первыми, до COMPARABLE_WINDOW.

    Живая сессия (exclude_path) выкинута: ее деньги/токены растут на ходу и делают
    оценку невоспроизводимой. Завершенные журналы не дописываются — их набор и
    медиана стабильны между двумя прогонами без новой работы. Крошечные сессии
    отсеяны порогом MIN_SESSION_TOKENS: пара реплик дает шумный тариф."""
    try:
        files = glob.glob(os.path.join(token_ledger.project_dir(cwd), "*.jsonl"))
    except Exception:
        return []
    ex = os.path.abspath(exclude_path) if exclude_path else None
    done = sorted((f for f in files if os.path.abspath(f) != ex),
                  key=os.path.getmtime, reverse=True)
    blends: list[float] = []
    for path in done:
        try:
            rep = _collect_resilient(path)
        except Exception:
            continue          # битый журнал одной сессии не рушит медиану остальных
        tok = token_ledger.tokens(rep["total"])
        if tok < MIN_SESSION_TOKENS:
            continue
        blends.append(rep["money"] / tok * 1e6)
        if len(blends) >= COMPARABLE_WINDOW:
            break
    return blends


def disk_blend(cwd: str, exclude_path: str | None) -> tuple[float, bool, str | None]:
    """(тариф $/млн ток, груб ли тариф, причина грубости).

    Медиана завершенных сессий — устойчивее среднего к разовому дорогому прогону.
    Достоверных сессий меньше MIN_COMPARABLE → медиане не верим: берем умолчание и
    ВСЛУХ помечаем оценку грубой. Молча подставлять умолчание запрещено."""
    blends = completed_blends(cwd, exclude_path)
    if len(blends) < MIN_COMPARABLE:
        return (DEFAULT_BLEND_PER_MTOK, True,
                f"завершенных сессий сопоставимого класса {len(blends)} < {MIN_COMPARABLE}, "
                f"тариф взят умолчальный ${DEFAULT_BLEND_PER_MTOK:.2f}/млн")
    return statistics.median(blends), False, None


def run(track: str, limit: float | None, cwd: str) -> int:
    spent, spend_reason, live_path = live_session_spend(cwd)
    blend, coarse, coarse_reason = disk_blend(cwd, live_path)
    est = track_estimate(track, blend)
    note = f" (оценка ГРУБАЯ: {coarse_reason})" if coarse else ""
    if spent is None:
        # Отказ 1 из 2 — РАСХОД НЕ ИЗМЕРЕН (код 4), отличен от «не хватает лимита»
        # (код 3): неизвестный расход хуже известного большого, «$700 остатка →
        # хватает» на пустом месте проезжает потолок. Неизвестное и плохое — разное.
        print(f"budget_preflight: трек {track} ≈ ${est:.2f}{note}; РАСХОД НЕ ИЗМЕРЕН: "
              f"{spend_reason or 'причина не определена'} — трек не стартует "
              f"(код {EXIT_UNMEASURED}).", file=sys.stderr)
        return EXIT_UNMEASURED
    if limit is None:
        # Лимита нет — берем умолчание политики (в одном месте): гейт не отключается
        # отсутствием флага, иначе документированная форма зелена при любом расходе.
        limit = DEFAULT_LIMIT
    rc = decide(track, limit, spent, blend)
    remaining = limit - spent
    # Отказ 2 из 2 — НЕ ХВАТАЕТ ЛИМИТА (код 3): расход известен, но остаток меньше оценки.
    verdict = ("хватает" if rc == EXIT_OK
               else "НЕ ХВАТАЕТ ЛИМИТА — трек не стартует, доложить владельцу")
    print(f"budget_preflight: трек {track} ≈ ${est:.2f}{note}; лимит ${limit:.2f} − потрачено "
          f"${spent:.2f} = ${remaining:.2f} остатка → {verdict}.")
    return rc


def selftest() -> int:
    # Решение — чистая функция, проверяется на синтетике без сети и без диска.
    d = DEFAULT_BLEND_PER_MTOK
    checks = [
        ("FULL при грошовом лимите не стартует", decide("FULL", 0.01, 0.0, d) == 3),
        ("FAST при огромном лимите стартует", decide("FAST", 100000.0, 0.0, d) == 0),
        # Управляемая арифметика: blend 0.1 → FAST (40 млн ток) стоит ровно $4.
        ("оценка трека = объем × тариф", abs(track_estimate("FAST", 0.1) - 4.0) < 1e-9),
        ("на границе остатка трек стартует", decide("FAST", 5.0, 1.0, 0.1) == 0),   # остаток 4 == оценка 4
        ("потраченное съедает остаток", decide("FAST", 5.0, 2.0, 0.1) == 3),        # остаток 3 < оценка 4
        ("дороже трек — больше оценка",
         track_estimate("FULL", d) > track_estimate("FAST", d) > track_estimate("MICRO", d)),
    ]
    # Тариф действительно берется с диска: собираем крошечную сессию и считаем blend.
    with tempfile.TemporaryDirectory() as tmp:
        sess = os.path.join(tmp, "s.jsonl")
        with open(sess, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "requestId": "r1", "message": {
                "model": "claude-sonnet-5",
                "usage": {"input_tokens": 1000, "output_tokens": 0,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}) + "\n")
        rep = token_ledger.collect(sess)
        tok = token_ledger.tokens(rep["total"])
        blend = rep["money"] / tok * 1e6
        # sonnet input $3/млн → 1000 ток = $0.003, тариф = $3/млн.
        checks.append(("тариф считается с диска", abs(blend - 3.0) < 1e-6))

        # Битая ЗАПИСЬ (валидный JSON чужой формы) не обнуляет весь файл: разбор
        # пропускает ее, а не падает и не считает потраченное нулем.
        bityy = os.path.join(tmp, "b.jsonl")
        good_line = json.dumps({"type": "assistant", "requestId": "r1", "message": {
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 1000, "output_tokens": 0,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}})
        with open(bityy, "w", encoding="utf-8") as fh:
            fh.write(good_line + "\n" + json.dumps(["битая", "запись"]) + "\n")
        rep_b = _collect_resilient(bityy)
        checks.append(("битая запись не обнуляет расход",
                       token_ledger.tokens(rep_b["total"]) == 1000))

    # Три состояния источника: нет каталога, каталог пуст, журнал не читается.
    # Первое и третье — отказ; второе — единственный честный ноль.
    home0 = os.environ.get("HOME")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="budget-sessions-") as tmp:
        try:
            missing_home = os.path.join(tmp, "missing")
            os.makedirs(missing_home)
            os.environ["HOME"] = missing_home
            missing_spent, missing_reason, _ = live_session_spend(cwd)
            missing_cli = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--track", "FULL"],
                cwd=cwd, env={**os.environ, "HOME": missing_home},
                text=True, capture_output=True, check=False)
            missing_output = missing_cli.stdout + missing_cli.stderr

            empty_home = os.path.join(tmp, "empty")
            os.environ["HOME"] = empty_home
            os.makedirs(token_ledger.project_dir(cwd))
            empty_spent, empty_reason, _ = live_session_spend(cwd)
            empty_cli = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--track", "FULL"],
                cwd=cwd, env={**os.environ, "HOME": empty_home},
                text=True, capture_output=True, check=False)

            broken_home = os.path.join(tmp, "broken")
            os.environ["HOME"] = broken_home
            broken_dir = token_ledger.project_dir(cwd)
            os.makedirs(broken_dir)
            with open(os.path.join(broken_dir, "broken.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("{}\n")
            collect0 = globals()["_collect_resilient"]

            def unreadable(_path: str) -> dict:
                raise OSError("синтетически нечитаемый журнал")

            globals()["_collect_resilient"] = unreadable
            try:
                broken_spent, broken_reason, _ = live_session_spend(cwd)
            finally:
                globals()["_collect_resilient"] = collect0
        finally:
            if home0 is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = home0
    checks += [
        ("нет каталога сессий → расход не измерен с причиной",
         missing_spent is None and "каталог сессий не найден по пути" in (missing_reason or "")),
        # Отказ «расход не измерен» — свой код 4, не 3: неизвестное и плохое различимы.
        ("HOME без каталога → CLI код 4 «РАСХОД НЕ ИЗМЕРЕН», без «хватает»",
         missing_cli.returncode == EXIT_UNMEASURED and "хватает" not in missing_output
         and "РАСХОД НЕ ИЗМЕРЕН" in missing_output
         and "каталог сессий не найден по пути" in missing_output),
        ("пустой каталог сессий → честный ноль и код 0",
         empty_spent == 0.0 and empty_reason is None and empty_cli.returncode == 0),
        ("нечитаемый журнал → старый fail-closed сохранен",
         broken_spent is None and "не разобран" in (broken_reason or "")),
    ]

    # Тариф — МЕДИАНА завершенных сессий, живая исключена; оценка воспроизводима.
    # Строим проект: три завершенные сессии тарифов 1/3/15 (haiku/sonnet/opus,
    # только input) + свежайшая живая. Медиана = 3, живая на нее не влияет, и рост
    # живой сессии оценку не двигает (враждебная проба R02).
    home0b = os.environ.get("HOME")
    with tempfile.TemporaryDirectory(prefix="budget-median-") as tmp:
        try:
            home = os.path.join(tmp, "h")
            os.environ["HOME"] = home
            proj = token_ledger.project_dir(cwd)
            os.makedirs(proj)

            def write_session(name: str, model: str, in_tok: int, mtime: int) -> str:
                p = os.path.join(proj, name)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps({"type": "assistant", "requestId": name, "message": {
                        "model": model, "usage": {
                            "input_tokens": in_tok, "output_tokens": 0,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0}}}) + "\n")
                os.utime(p, (mtime, mtime))
                return p

            write_session("done_haiku.jsonl", "claude-haiku-4-5", 2_000_000, 1000)   # blend 1.0
            write_session("done_sonnet.jsonl", "claude-sonnet-5", 2_000_000, 1100)   # blend 3.0
            write_session("done_opus.jsonl", "claude-opus-5", 2_000_000, 1200)       # blend 15.0
            live = write_session("live.jsonl", "claude-opus-5", 2_000_000, 9000)     # свежайшая

            live_path = token_ledger.latest_session(cwd)
            blend1, coarse1, _ = disk_blend(cwd, live_path)
            est1 = track_estimate("FULL", blend1)

            # Живая сессия дорожает на ходу — оценка обязана остаться прежней.
            with open(live, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "assistant", "requestId": "live2", "message": {
                    "model": "claude-opus-5", "usage": {
                        "input_tokens": 50_000_000, "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0}}}) + "\n")
            blend2, _, _ = disk_blend(cwd, live_path)
            est2 = track_estimate("FULL", blend2)

            # Меньше MIN_COMPARABLE завершенных сессий → грубо, вслух, умолчание.
            with tempfile.TemporaryDirectory(prefix="budget-scarce-") as tmp2:
                os.environ["HOME"] = os.path.join(tmp2, "h2")
                proj2 = token_ledger.project_dir(cwd)
                os.makedirs(proj2)
                scarce_blend, scarce_coarse, scarce_reason = disk_blend(cwd, None)
        finally:
            if home0b is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = home0b
    checks += [
        ("тариф = медиана завершенных сессий (1/3/15 → 3)", abs(blend1 - 3.0) < 1e-6),
        ("медиана — не грубо при достатке сессий", coarse1 is False),
        ("живая сессия в медиану не входит", abs(blend1 - 15.0) > 1e-6),
        ("рост живой сессии не двигает оценку (воспроизводимость)", est1 == est2),
        ("мало сессий → тариф умолчальный", abs(scarce_blend - DEFAULT_BLEND_PER_MTOK) < 1e-9),
        ("мало сессий → оценка помечена грубой вслух с причиной",
         scarce_coarse is True and "< " in (scarce_reason or "")),
    ]

    # Два отказа — два кода, оба названы: 3 не хватает лимита, 4 расход не измерен.
    checks += [
        ("не хватает лимита → код 3", decide("FULL", 1.0, 0.0, d) == EXIT_OVER),
        ("коды отказов различны", EXIT_OVER != EXIT_UNMEASURED),
    ]

    # Без --limit гейт не отключается: умолчание политики стережет перерасход.
    checks.append(("умолчание лимита гейтит перерасход",
                   decide("FULL", DEFAULT_LIMIT, DEFAULT_LIMIT + 9999.0, d) == 3))
    checks.append(("умолчание лимита пропускает свежий прогон",
                   decide("MICRO", DEFAULT_LIMIT, 0.0, 0.1) == 0))

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'✓' if ok else '✗'} {n}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)} — тариф = медиана завершенных сессий, "
          "оценка воспроизводима, коды отказа различны")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка бюджета перед треком "
                                 "(exit 3 не хватает лимита, exit 4 расход не измерен)")
    ap.add_argument("--track", choices=sorted(token_ledger.TRACK_BUDGET),
                    help="трек прогона: MICRO | FAST | FULL")
    ap.add_argument("--limit", type=float, metavar="ДОЛЛАРЫ", help="потолок расхода в долларах")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.track:
        ap.error("нужен --track (или --selftest)")
    return run(a.track, a.limit, os.getcwd())


if __name__ == "__main__":
    sys.exit(main())
