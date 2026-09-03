#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""channels.py — ОБЩИЙ счет каналов и квот на прогон дела.

Зачем. Прогон 01.09.2026: preflight в 16:40 честно сказал «источник sudact.ru НЕ
отвечает» — и это знание умерло в выводе одного вызова. Дальше рой сделал 271
сетевой вызов, мертвый источник опрашивался 128 раз, счет — 62,6 млн токенов
(≈119 $). Квота веб-поиска при этом была догадкой КАЖДОГО охотника по отдельности:
общего счета не существовало, и «осталось много» отвечали трое разом.

Здесь состояние каналов — один файл на прогон, рядом с run.json:
`.agent/context/channels.json`. Его читают ВСЕ агенты перед выходом наружу.

  • Канал помечен мертвым → до истечения TTL записи он не опрашивается. Не
    «не рекомендуется», а код возврата 2 у --check и отказ в practice_search.
  • Квота — ОБЩИЙ счетчик прогона, а не память отдельного охотника.
  • У каждой записи есть время жизни: мертвый источник через час проверяется
    заново, устаревшее знание не превращается в вечный запрет.

Использование:
    python3 scripts/channels.py cases/{клиент}/{дело} --show
    python3 scripts/channels.py {дело} --check sudact          # код 2 = мертв, не ходить
    python3 scripts/channels.py {дело} --dead sudact --reason "HTTP 500" --ttl 3600
    python3 scripts/channels.py {дело} --alive sudact
    python3 scripts/channels.py {дело} --spend websearch --n 1 # код 3 = квота исчерпана
    python3 scripts/channels.py {дело} --json
    python3 scripts/channels.py --selftest                     # без сети и без дела

Путь к делу можно не писать: берется из $THEMIZ_CASE.
"""
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
import sreda  # noqa: E402,F401  переходный период имен переменных

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_paths  # noqa: E402

STATE_NAME = "channels.json"

# Время жизни отметки «мертв». Час: в пределах прогона повторно не ходим, но
# через час источник получает честный второй шанс — вечный запрет по разовому
# HTTP 500 хуже отсутствия отметки.
DEAD_TTL = 3600
# Отметка «жив» протухает быстрее: живость меняется чаще смерти, и просроченная
# «жив» должна означать «проверь заново», а не «ходи спокойно».
ALIVE_TTL = 900

# Квоты прогона. Число — не догадка агента, а объявленный потолок.
QUOTAS = {
    "websearch": 200,   # лимит запросов WebSearch на сессию (CLAUDE.md, «Внешние сервисы»)
}


def state_path(case) -> Path:
    return case_paths.context(case) / STATE_NAME


def _now() -> float:
    return time.time()


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(case, fn):
    """Читать-изменить-записать под флагом блокировки.

    Охотники ходят роем и параллельно: без блокировки два инкремента квоты
    затирают друг друга, и общий счет снова становится догадкой.
    """
    p = state_path(case)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            st = _read(p)
            out = fn(st)
            p.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
            return out
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def mark(case, name, alive: bool, reason: str = "", ttl: int = 0) -> dict:
    """Записать состояние канала со временем жизни записи."""
    ttl = ttl or (ALIVE_TTL if alive else DEAD_TTL)

    def apply(st):
        chans = st.setdefault("каналы", {})
        rec = chans.setdefault(name, {})
        rec.update({"жив": bool(alive), "причина": reason,
                    "проверен": round(_now()), "годен до": round(_now() + ttl)})
        return rec
    return _update(case, apply)


def status(case, name) -> dict:
    """Запись о канале. Протухшая по TTL — как отсутствующая (fail-open):
    незнание не запрещает попытку, запрещает только свежая отметка «мертв»."""
    rec = (_read(state_path(case)).get("каналы") or {}).get(name)
    if not isinstance(rec, dict):
        return {}
    if _now() > rec.get("годен до", 0):
        return {}
    return rec


def is_dead(case, name) -> bool:
    rec = status(case, name)
    return bool(rec) and not rec.get("жив", True)


def spend(case, name, n: int = 1) -> tuple:
    """Списать n единиц общей квоты. Возвращает (потрачено, потолок, хватило)."""
    cap = QUOTAS.get(name, 0)

    def apply(st):
        q = st.setdefault("квоты", {})
        used = int(q.get(name, 0)) + n
        q[name] = used
        return used
    used = _update(case, apply)
    return used, cap, (not cap or used <= cap)


def quota_status(case, name) -> tuple:
    """Сколько потрачено и каков потолок — без списания.

    Читающая половина spend(): preflight и любой другой отчет обязаны видеть тот
    же общий счет, что списывает claude_guard.py на каждом WebSearch, а не
    повторять чтение файла состояния своей копией.
    """
    used = int((_read(state_path(case)).get("квоты") or {}).get(name, 0))
    return used, QUOTAS.get(name, 0)



# --- указатель текущего дела прогона -----------------------------------------
# Зачем он есть. Общий счет квоты и отметка мертвого канала живут В ДЕЛЕ
# (state_path = context(case)/channels.json), а знать дело обязаны трое: preflight,
# practice_search и хук claude_guard. Проводник дело ЗНАЕТ (themiz-pipeline.js:16,
# аргумент обязателен), но хук запускается харнессом и среды проводника не видит —
# $THEMIZ_CASE до него не долетает никогда. Указатель и есть тот штатный путь:
# пишет его проводник в начале прогона, читают все трое.
# Держатель ОДИН — этот прибор: чей общий счет, того и указатель на дело.
TEKUSHCHEE = (Path(__file__).resolve().parent.parent
              / ".cache" / "tekushchee-delo")


def zapomnit_delo(case) -> None:
    """Отметить дело текущего прогона. Зовет проводник, один раз на прогон."""
    TEKUSHCHEE.parent.mkdir(parents=True, exist_ok=True)
    TEKUSHCHEE.write_text(str(case).strip() + "\n", encoding="utf-8")


def tekushchee_delo() -> str:
    """Дело текущего прогона или пусто. Пусто — честный ответ «прогон не начат»."""
    try:
        return TEKUSHCHEE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def rows(case) -> list:
    st = _read(state_path(case))
    out = []
    for name, rec in sorted((st.get("каналы") or {}).items()):
        left = int(rec.get("годен до", 0) - _now())
        out.append((name, "жив" if rec.get("жив") else "МЕРТВ",
                    rec.get("причина", ""),
                    f"{left} с" if left > 0 else "запись протухла"))
    for name, used in sorted((st.get("квоты") or {}).items()):
        cap = QUOTAS.get(name, 0)
        out.append((f"квота {name}", f"{used}/{cap or '—'}", "", ""))
    return out


def selftest() -> int:
    """Без сети. Порог — TTL: свежая отметка «мертв» запрещает поход, протухшая нет."""
    import tempfile
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        case = Path(tmp) / "дело-2026"
        mark(case, "sudact", False, "HTTP 500", ttl=60)
        checks.append(("свежая отметка «мертв» запрещает поход", is_dead(case, "sudact")))
        mark(case, "sudact", False, "HTTP 500", ttl=-1)
        checks.append(("протухшая отметка не запрещает (fail-open)",
                       not is_dead(case, "sudact")))
        mark(case, "sudact", True, "отвечает")
        checks.append(("живой канал не мертв", not is_dead(case, "sudact")))
        checks.append(("канал без записи не мертв", not is_dead(case, "нет-такого")))
        u1, cap, ok1 = spend(case, "websearch", 199)
        u2, _, ok2 = spend(case, "websearch", 2)
        checks.append(("квота общая и накопительная", (u1, u2) == (199, 201)))
        checks.append(("до потолка — хватило, за потолком — нет",
                       ok1 and not ok2 and cap == 200))
        checks.append(("quota_status читает то же, что списал spend",
                       quota_status(case, "websearch") == (201, 200)))
        checks.append(("quota_status непотраченной квоты — ноль, не ошибка",
                       quota_status(case, "sgai") == (0, 0)))
        checks.append(("состояние лежит рядом с run.json",
                       state_path(case).parent == case_paths.run_state(case).parent))
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'✓' if ok else '✗'} {n}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Общий счет каналов и квот прогона")
    ap.add_argument("case", nargs="?", default=os.environ.get("THEMIZ_CASE", ""),
                    help="путь к делу (или $THEMIZ_CASE)")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", metavar="КАНАЛ", help="код 2, если канал помечен мертвым")
    ap.add_argument("--dead", metavar="КАНАЛ")
    ap.add_argument("--alive", metavar="КАНАЛ")
    ap.add_argument("--reason", default="")
    ap.add_argument("--ttl", type=int, default=0, help="время жизни записи, секунд")
    ap.add_argument("--spend", metavar="КВОТА", help="списать из общей квоты прогона")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.case:
        ap.error("нужен путь к делу (аргумент или $THEMIZ_CASE)")
    case = Path(a.case)

    if a.dead or a.alive:
        name = a.dead or a.alive
        rec = mark(case, name, alive=bool(a.alive), reason=a.reason, ttl=a.ttl)
        print(f"{name}: {'жив' if rec['жив'] else 'МЕРТВ'} "
              f"({a.reason or 'без причины'}), запись годна "
              f"{int(rec['годен до'] - _now())} с")
        return 0

    if a.check:
        rec = status(case, a.check)
        if rec and not rec.get("жив", True):
            print(f"КАНАЛ {a.check} ПОМЕЧЕН МЕРТВЫМ: {rec.get('причина') or 'без причины'}. "
                  f"Не опрашивать еще {int(rec['годен до'] - _now())} с. "
                  f"Работать по knowledge/practice_index.md либо ждать истечения записи.",
                  file=sys.stderr)
            return 2
        print(f"{a.check}: запрета нет" + ("" if rec else " (записи нет)"))
        return 0

    if a.spend:
        used, cap, ok = spend(case, a.spend, a.n)
        print(f"квота {a.spend}: потрачено {used}" + (f" из {cap}" if cap else ""))
        if not ok:
            print(f"КВОТА {a.spend} ИСЧЕРПАНА ({used}/{cap}) — канал закрыт на прогон.",
                  file=sys.stderr)
            return 3
        return 0

    data = _read(state_path(case))
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    r = rows(case)
    if not r:
        print("состояние каналов пусто — прогон еще ничего не проверял")
        return 0
    print(f"{'КАНАЛ':<24}{'СОСТОЯНИЕ':<12}{'ПРИЧИНА':<40}ЗАПИСЬ ГОДНА")
    print("-" * 96)
    for name, st, why, left in r:
        print(f"{name:<24}{st:<12}{why[:38]:<40}{left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
