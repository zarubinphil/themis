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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


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
    cfg = os.path.expanduser("$HOME/.claude.json")
    if not os.path.exists(cfg):
        return False, "$HOME/.claude.json отсутствует"
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = []

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
        # Флаг владельца говорит «искать РАЗРЕШЕНО», но не «источник ЖИВ».
        # 21.08.2026 источник весь день отдавал HTTP 500, а preflight печатал «OK»,
        # и охотники записали пустой результат как отсутствие практики. Разрешение
        # и живость — разные вопросы, спрашиваем оба.
        alive = probe_sudact()
        rows.append(("Поиск практики sudact.ru", alive,
                     "включен владельцем, источник отвечает" if alive
                     else "включен владельцем, но источник НЕ отвечает",
                     "practice_search.py ищет" if alive
                     else "пустой результат НЕ считать отсутствием практики — повторить позже"))
    rows.append(("WebSearch (квота сессии)", None, "программно не проверяется",
                 "лимит 200 запросов на сессию — спросить у охотника в первом отчете"))

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
