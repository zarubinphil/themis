#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight_search.py — проверка каналов поиска ДО запуска охоты за практикой.

Зачем. В прогоне боевого дела (раздел имущества) добор через Tavily стоил 249 950 токенов и вернул
«инструмент недоступен»: сервер числится в конфигурации без ключа, а MCP вообще
не наследуется агентом с явным списком `tools` в frontmatter. Отдельно охотник
уже в процессе обнаружил, что квота веб-поиска исчерпана. Обе проверки —
одна команда.

Использование:
    python3 scripts/preflight_search.py
    python3 scripts/preflight_search.py --json
    python3 scripts/preflight_search.py --selftest

Выход: таблица «канал → статус → что делать». Код возврата 1, если не осталось
ни одного внешнего канала — тогда охота за внешней практикой не запускается,
работаем по knowledge/practice_index.md.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def resolve_case(cli_case: str) -> str:
    """Дело прогона: явный --case важнее $THEMIS_CASE. Переменную в бою никто не
    выставлял — флага не было вовсе (02.09.2026), и мертвый канал не долетал до
    preflight: источник опрашивался повторно 128 раз за прогон 01.09.2026."""
    return cli_case or os.environ.get("THEMIS_CASE", "")


def _sudact_allowed() -> bool:
    """Точка правды — practice_search.search_allowed(). Читаем ее, а не копию условия."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        from practice_search import search_allowed
        return search_allowed()
    except Exception:
        return os.environ.get("THEMIS_SUDACT_SEARCH") == "1"


def probe_url(url: str, timeout: int = 8) -> bool:
    """Живой ли публикатор. ponytail: HEAD часто режут, берем первые байты GET."""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", UA, "-o", "/dev/null",
             "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 4)
        return r.stdout.strip().startswith(("2", "3"))
    except Exception:
        return False


def check_mcp_key(server: str) -> tuple[bool, str]:
    """Есть ли ключ у MCP-сервера в $HOME/.claude.json."""
    cfg = str(Path.home() / ".claude.json")
    if not os.path.exists(cfg):
        return False, f"{cfg} отсутствует"
    try:
        data = json.load(open(cfg, encoding="utf-8"))
    except Exception as e:
        return False, f"конфигурация нечитаема: {e}"

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "mcpServers" and isinstance(v, dict) and server in v:
                    return v[server]
                got = walk(v)
                if got is not None:
                    return got
        elif isinstance(node, list):
            for v in node:
                got = walk(v)
                if got is not None:
                    return got
        return None

    entry = walk(data)
    if entry is None:
        return False, "сервер не зарегистрирован"
    env = entry.get("env") or {}
    if any(env.values()):
        return True, "ключ задан"
    return False, "зарегистрирован, но env пуст — ключа нет"


def probe_sudact(timeout: int = 12) -> bool:
    """Отвечает ли поиск практики на РЕАЛЬНЫЙ запрос. Проба идет по тому же
    маршруту, каким ходит practice_search.py: корень сайта может отвечать 200,
    когда сам поиск лежит с HTTP 500 — так и было 21.08.2026."""
    url = ("https://sudact.ru/regular/doc_ajax/?regular-txt=%D0%B4%D0%BE%D0%BF%D1%80%D0%BE%D1%81"
           "&regular-case_doc=&regular-lawchunkinfo=&regular-date_from=&regular-date_to="
           "&regular-workflow_stage=&regular-area=&regular-court=&regular-judge=")
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", UA, "-o", "/dev/null",
             "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 4)
        return r.stdout.strip().startswith("2")
    except Exception:
        return False


def check_sgai() -> tuple[bool, str]:
    exe = subprocess.run(["which", "sgai"], capture_output=True, text=True)
    if not exe.stdout.strip():
        return False, "CLI не установлен"
    # Баланс спрашиваем у `credits`, а не у `validate`. Прецедент 21.08.2026:
    # `validate` проверяет здоровье КЛЮЧА и при нулевом остатке отвечает успехом —
    # preflight печатал «OK», а два охотника подряд получали «Insufficient credits»
    # на живой охоте. Отчет, расходящийся с фактом, хуже отсутствия отчета:
    # по нему планируют работу.
    try:
        r = subprocess.run(["sgai", "credits", "--json"], capture_output=True,
                           text=True, timeout=25)
        blob = (r.stdout or "") + (r.stderr or "")
        m = re.search(r'"remaining"\s*:\s*(\d+)', blob)
        if m:
            left = int(m.group(1))
            plan = re.search(r'"plan"\s*:\s*"([^"]+)"', blob)
            suffix = f" ({plan.group(1)})" if plan else ""
            if left == 0:
                return False, f"кредиты исчерпаны{suffix}"
            return True, f"кредитов: {left}{suffix}"
        if re.search(r"no credits|insufficient", blob, re.I):
            return False, "баланс исчерпан"
        if r.returncode == 0:
            return True, "доступен, остаток не прочитан"
        return False, f"credits вернул код {r.returncode}"
    except subprocess.TimeoutExpired:
        return False, "таймаут проверки"
    except Exception as e:
        return False, str(e)[:40]


def selftest() -> int:
    """Без сети. Порог путь-резолва (b8086b2): check_mcp_key читал литеральный
    "$HOME/.claude.json" → «отсутствует» при файле в 91 КБ. Фикстура ПО ОБЕ
    стороны порога: HOME с конфигом — ключ найден, НЕ слепое «отсутствует»;
    HOME без конфига — честный отказ."""
    import tempfile
    checks = []
    _home0 = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["HOME"] = tmp
            no_ok, no_note = check_mcp_key("tavily")
            checks.append(("нет конфига → честный отказ, не выдумка",
                           no_ok is False and "отсутствует" in no_note))
            with open(os.path.join(tmp, ".claude.json"), "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {"tavily": {"env": {"TAVILY_API_KEY": "x"}}}}, fh)
            ok, note = check_mcp_key("tavily")
            checks.append(("HOME развернут: конфиг найден, не слепое «отсутствует»",
                           ok is True and "отсутствует" not in note))
        finally:
            if _home0 is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = _home0

    _case0 = os.environ.pop("THEMIS_CASE", None)
    try:
        checks.append(("--case работает без $THEMIS_CASE",
                       resolve_case("cases/klient/delo") == "cases/klient/delo"))
        checks.append(("без --case и без переменной — дело не опознано",
                       resolve_case("") == ""))
        os.environ["THEMIS_CASE"] = "env-delo"
        checks.append(("явный --case важнее $THEMIS_CASE", resolve_case("flag-delo") == "flag-delo"))
        checks.append(("без --case используется $THEMIS_CASE", resolve_case("") == "env-delo"))
    finally:
        os.environ.pop("THEMIS_CASE", None)
        if _case0 is not None:
            os.environ["THEMIS_CASE"] = _case0
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  {'✓' if ok else '✗'} {n}")
    if bad:
        print(f"selftest ПРОВАЛЕН: {len(bad)} из {len(checks)}")
        return 1
    print(f"selftest пройден: {len(checks)}/{len(checks)} — путь-резолв без сети")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    ap.add_argument("--case", default="", help="путь к делу — общий счет каналов и квот; "
                    "иначе $THEMIS_CASE")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    rows = []
    case = resolve_case(a.case)

    ok, note = check_sgai()
    rows.append(("ScrapeGraphAI (sgai)", ok, note,
                 "внешний поиск" if ok else "не поручать охотникам"))

    for srv in ("tavily", "firecrawl"):
        ok, note = check_mcp_key(srv)
        rows.append((f"MCP {srv}", ok, note,
                     "доступен как канал" if ok else "не поручать: MCP не наследуется "
                     "агентом с явным tools"))

    for name, url in (("vsrf.ru", "https://vsrf.ru/"),
                      ("legalacts.ru", "https://legalacts.ru/"),
                      ("eg-online.ru", "https://www.eg-online.ru/")):
        ok = probe_url(url)
        rows.append((f"Публикатор {name}", ok, "отвечает" if ok else "недоступен",
                     "verify_act.py сработает" if ok else "верификация через фолбэк"))

    # Решение по sudact живет в practice_search.py (SUDACT_SEARCH_ALLOWED +
    # THEMIS_SUDACT_SEARCH). Preflight его читает, а не дублирует условие:
    # своя копия условия врала «закрыт» при работающем поиске.
    sudact_on = _sudact_allowed()
    if not sudact_on:
        rows.append(("Поиск практики sudact.ru", False,
                     "выключен явно (THEMIS_SUDACT_SEARCH=0)",
                     "искать в knowledge/practice_index.md; акт по URL — --doc"))
    else:
        # Мертвый канал из общего файла прогона не опрашивается повторно до
        # истечения TTL записи — 01.09.2026 мертвый источник опрашивался 128 раз.
        dead = None
        chan = None
        if case:
            try:
                sys.path.insert(0, os.path.join(ROOT, "scripts"))
                import channels as chan  # noqa: F811 — присваиваем в локальную
                rec = chan.status(case, "sudact")
                if rec and not rec.get("жив", True):
                    dead = rec
            except Exception:
                dead, chan = None, None
        if dead is not None:
            rows.append(("Поиск практики sudact.ru", False,
                         f"мертв по общему состоянию прогона: "
                         f"{dead.get('причина') or 'без причины'}",
                         "не опрашивать до истечения записи (channels.py --show)"))
        else:
            # Флаг владельца говорит «искать РАЗРЕШЕНО», но не «источник ЖИВ».
            # 21.08.2026 источник весь день отдавал HTTP 500, а preflight печатал «OK»,
            # и охотники записали пустой результат как отсутствие практики. Разрешение
            # и живость — разные вопросы, спрашиваем оба.
            alive = probe_sudact()
            if chan is not None:
                try:
                    chan.mark(case, "sudact", alive,
                              "отвечает" if alive else "источник НЕ отвечает")
                except Exception:
                    pass
            rows.append(("Поиск практики sudact.ru", alive,
                         "включен владельцем, источник отвечает" if alive
                         else "включен владельцем, но источник НЕ отвечает",
                         "practice_search.py ищет" if alive
                         else "пустой результат НЕ считать отсутствием практики — повторить позже"))
    # Расход WebSearch — общий счет прогона (scripts/channels.py), не догадка
    # отдельного охотника: раньше поле было советом «спросить в первом отчете», и
    # трое охотников независимо отвечали «квоты много» на одном и том же прогоне.
    ws_used = ws_cap = None
    if case:
        try:
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            import channels as _channels
            ws_used, ws_cap = _channels.quota_status(case, "websearch")
        except Exception:
            ws_used = None
    if ws_used is None:
        rows.append(("WebSearch (квота сессии)", None,
                     "дело не опознано ($THEMIS_CASE/--case) — общий счет недоступен",
                     "передать дело: --case ДЕЛО либо $THEMIS_CASE"))
    else:
        ws_ok = not ws_cap or ws_used < ws_cap
        rows.append(("WebSearch (квота сессии)", ws_ok,
                     f"общий счет прогона: {ws_used}" + (f" из {ws_cap}" if ws_cap else ""),
                     "channels.py ДЕЛО --show" if ws_ok
                     else "КВОТА ИСЧЕРПАНА — не звать WebSearch"))

    if a.json:
        print(json.dumps([{"channel": c, "ok": o, "note": n, "action": act}
                          for c, o, n, act in rows], ensure_ascii=False, indent=2))
    else:
        print(f"{'КАНАЛ':<28}{'СТАТУС':<10}{'ПРИМЕЧАНИЕ':<44}ДЕЙСТВИЕ")
        print("-" * 118)
        for c, o, n, act in rows:
            mark = "?" if o is None else ("OK" if o else "НЕТ")
            print(f"{c:<28}{mark:<10}{n:<44}{act}")
        print("-" * 118)

    external = [r for r in rows if r[0].startswith(("ScrapeGraphAI", "MCP")) and r[1]]
    publishers = [r for r in rows if r[0].startswith("Публикатор") and r[1]]
    if not external and not publishers:
        print("\nВНЕШНИХ КАНАЛОВ НЕТ. Охоту за внешней практикой не запускать: "
              "работать по knowledge/practice_index.md и честно зафиксировать пробел.")
        return 1
    if not external:
        print("\nПоисковых каналов нет, публикаторы отвечают: верификация известных "
              "реквизитов возможна (verify_act.py), поиск новых актов — нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
