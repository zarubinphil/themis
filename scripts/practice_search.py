#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""practice_search.py — бесплатный поиск судебной практики по судам общей юрисдикции.

ЗАЧЕМ. Охота за практикой — самая дорогая статья конвейера: 1 027 878 токенов на
одном прогоне, один охотник 72-81k. Большая часть этого расхода — не рассуждение,
а механика: перебрать выдачу, открыть акты, вытащить реквизиты. Механику делает
этот скрипт за ноль токенов, модели остается правовая оценка найденного.

ИСТОЧНИК. sudact.ru («Судебные и нормативные акты РФ») — в белом списке проекта
(knowledge/allowed-services.md, чтение публичных страниц). Покрывает СОЮ, мировых
судей, ВС РФ и арбитраж. Официального бесплатного API у государства нет:
api.sudrf.ru отдает 404, банк решений bsr.sudrf.ru не отвечает с марта 2024
(проверено 03.08.2026 — таймаут), у vsrf.ru документы отдаются PDF, а каталог
подгружается скриптами.

МЕХАНИКА (установлена опытным путем 03.08.2026, не предполагалась).
Поиск асинхронный, в два шага. Обычный GET на /{раздел}/doc/ возвращает ТОЛЬКО
форму — результатов там нет, и именно на этом ломаются лобовые парсеры:

    1. GET /{раздел}/doc/?{параметры}       — ставит задачу, выдает сессионную куку
    2. GET /{раздел}/doc_ajax/?{параметры}  — опрос до search_status == finished
       с заголовком X-Requested-With: XMLHttpRequest и Referer шага 1

Первые опросы отдают статус «new»; выдача приходит обычно с третьей попытки.

Сеть — через curl: в проекте это штатный сетевой инструмент (scripts/fetch_url.sh),
и у системного python не настроены корневые сертификаты.

ПРИМЕРЫ

    # найти практику по разделу имущества, апелляция, Татарстан
    python3 scripts/practice_search.py "раздел совместно нажитого имущества" \\
        --instance апелляция --area Татарстан --limit 10

    # только Верховный Суд РФ, за последние два года
    python3 scripts/practice_search.py "срок исковой давности раздел имущества" \\
        --section vsrf --date-from 01.01.2024

    # вытащить полный текст акта для цитирования
    python3 scripts/practice_search.py --doc https://sudact.ru/regular/doc/4zgXwp3gCBLh/

    # машинный вывод для агента
    python3 scripts/practice_search.py "алименты твердая сумма" --json

Повторный запрос отдается из кеша мгновенно и без обращения к сайту.
"""
import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
import sreda  # noqa: E402,F401  переходный период имен переменных

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
import pii_gate  # noqa: E402
import channels  # noqa: E402  # общий счет каналов и квот прогона (.agent/context/channels.json)

BASE = "https://sudact.ru"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".cache", "practice")
CACHE_TTL = 14 * 86400  # практика живет долго; две недели — компромисс со свежестью

SECTIONS = {
    "regular": "Суды общей юрисдикции",
    "magistrate": "Мировые судьи",
    "vsrf": "Верховный Суд РФ",
    "arbitral": "Арбитражные суды",
}

# Инстанция и регион задаются числовыми кодами сайта. Коды не угадываются:
# инстанция идет десятками (10/20/30/60/40), регион — идентификатором вида 1018.
# Справочники читаются из самой формы поиска и кешируются, чтобы принимать
# человеческие слова: --instance апелляция --area Татарстан.
def _load_dicts(section="regular"):
    """Справочники инстанций и регионов из формы поиска. Кеш — общий TTL (14 суток)."""
    key = f"dicts:{section}"
    cached = _cache_get(key)
    if cached:
        return cached
    with tempfile.NamedTemporaryFile(suffix=".cookies", delete=False) as tf:
        jar = tf.name
    try:
        page = _curl(f"{BASE}/{section}/", jar) or _curl(f"{BASE}/{section}/doc/", jar)
    finally:
        os.unlink(jar)
    dicts = {"workflow_stage": {}, "area": {}}
    for field in dicts:
        m = re.search(r'name="%s-%s".*?</select>' % (section, field), page or "", re.S)
        if not m:
            continue
        for value, title in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>',
                                       m.group(0)):
            title = html.unescape(title).strip()
            if value and title:
                dicts[field][title.lower()] = value
    if dicts["workflow_stage"] or dicts["area"]:
        _cache_put(key, dicts)
    return dicts


def _resolve(field, value, section="regular"):
    """Слово пользователя → код сайта. Уже код или пусто — вернуть как есть."""
    if not value or value.isdigit():
        return value
    table = _load_dicts(section).get(field, {})
    v = value.strip().lower()
    if v in table:
        return table[v]
    hits = [(k, code) for k, code in table.items() if v in k]
    if len(hits) == 1:
        return hits[0][1]
    if not hits:
        raise SystemExit(f"{field}: значение {value!r} не найдено в справочнике сайта. "
                         f"Доступны, например: {', '.join(sorted(table)[:5])}")
    raise SystemExit(f"{field}: {value!r} подходит нескольким — уточните: "
                     + ", ".join(k for k, _ in hits[:6]))


def _curl(url, cookie_jar, referer=None, ajax=False, timeout=30):
    """GET через curl. Возвращает тело ответа строкой либо None.

    HTTP-код проверяется ОБЯЗАТЕЛЬНО. Без этого страница ошибки приходит как обычный
    ответ: 03.08.2026 запрос несуществующего акта отдавал HTTP 500, а скрипт
    возвращал «500. Произошла ошибка» как ТЕКСТ СУДЕБНОГО АКТА с кодом 0 и клал его
    в кеш на две недели. Такой текст уходит в документ дословно.
    """
    marker = "__HTTPSTATUS__"
    cmd = ["curl", "-s", "-A", UA, "-b", cookie_jar, "-c", cookie_jar,
           "--max-time", str(timeout), "-H", "Accept-Language: ru,en;q=0.8",
           "-w", f"{marker}%{{http_code}}"]
    if ajax:
        cmd += ["-H", "X-Requested-With: XMLHttpRequest"]
    if referer:
        cmd += ["-e", referer]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    body, code = split_status(r.stdout.decode("utf-8", "replace"), marker)
    if http_failed(code):
        print(f"ВНИМАНИЕ: {url} ответил HTTP {code} — ответ отброшен, не кеширую.",
              file=sys.stderr)
        return None
    return body


def split_status(raw: str, marker: str = "__HTTPSTATUS__"):
    """Отделить тело ответа от кода, дописанного curl -w."""
    if marker in raw:
        body, _, code = raw.rpartition(marker)
        return body, code.strip()
    return raw, ""


def http_failed(code: str) -> bool:
    """Ответ считать неудачей. Пустой код — старое поведение curl, не трогаем."""
    return bool(code) and code.isdigit() and int(code) >= 400


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\xa0", " ").strip()


def _strip_keep_lines(s):
    """Снять теги, сохранив переводы строк — для текста акта, где абзацы значимы."""
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\xa0", " ")


def _cache_path(key):
    return os.path.join(CACHE_DIR, hashlib.sha256(key.encode()).hexdigest()[:20] + ".json")


def _cache_get(key):
    p = _cache_path(key)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < CACHE_TTL:
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def _cache_put(key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
    except OSError as e:
        print(f"ВНИМАНИЕ: кеш не записан ({e})", file=sys.stderr)


# Карта полей формы ПО РАЗДЕЛАМ. Прочитана из самих форм поиска 04.08.2026
# (`curl https://sudact.ru/{раздел}/doc/` → имена input и select), не предположена.
# Наблюдение о том, что набор полей у разделов РАЗНЫЙ, взято у mynka999/sudact-mcp-server
# (MIT); реализация своя.
#
# ПОЧЕМУ ЭТО ВАЖНО. Сайт молча ИГНОРИРУЕТ неизвестный параметр: ответ приходит
# 200, выдача есть, счетчик есть — и выглядит это как отфильтрованный результат.
# Практические последствия, которые были у нас:
#   • arbitral не знает поля `area` (у него `region`) — партиционирование по
#     регионам там не фильтровало НИЧЕГО, а отчет об охвате считался как обычно;
#   • magistrate, vsrf и arbitral не знают `workflow_stage` — каскад дробления по
#     инстанциям выполнял три лишних запроса с одинаковой выдачей;
#   • vsrf не знает ни `area`, ни `court` — партиционирование по регионам там
#     бессмысленно в принципе.
# Отправленный, но не поддержанный фильтр теперь называется в отчете (filters_ignored),
# а не растворяется в «мы все отфильтровали».
SECTION_FIELDS = {
    "regular": {"txt", "case_doc", "lawchunkinfo", "date_from", "date_to",
                "workflow_stage", "area", "court", "judge"},
    "magistrate": {"txt", "case_doc", "lawchunkinfo", "date_from", "date_to",
                   "area", "court", "judge"},
    "vsrf": {"txt", "case_doc", "lawchunkinfo", "date_from", "date_to", "judge"},
    "arbitral": {"txt", "case_doc", "lawchunkinfo", "date_from", "date_to",
                 "region", "court", "judge"},
}
# Регион у арбитража называется иначе. Одно и то же понятие, разное имя поля.
REGION_FIELD = {"arbitral": "region"}

# Потолок страниц выдачи. Наблюдение mynka999/sudact-mcp-server: page > 50
# зацикливает выдачу — сайт отдает ту же страницу, и наивный обход крутится вечно.
# Проверено 04.08.2026: страницы 50 и 55 одного запроса дают одинаковый набор URL.
MAX_PAGES = 50


class PiiOutboundError(ValueError):
    pass


def outbound_text(value: str, label: str = "запрос") -> str:
    """Текст перед внешним сервисом: маска PII либо отказ, если остаток грязный."""
    if not value:
        return value
    masked, _ = pii_gate.mask_text(value)
    clean = masked if masked is not None else value
    if pii_gate.residual_matches(clean):
        raise PiiOutboundError(f"{label}: текст содержит персональные данные и не очищен")
    return clean


def section_fields(section: str) -> set:
    return SECTION_FIELDS.get(section, SECTION_FIELDS["regular"])


def region_field(section: str) -> str:
    return REGION_FIELD.get(section, "area")


def build_query(section, text="", instance="", area="", court="", judge="",
                law="", case_doc="", date_from="", date_to="", page=1,
                ignored=None):
    """Строка параметров поиска. Имена полей у раздела свои: regular-txt, vsrf-txt и т.д.

    Поля, которых у раздела НЕТ, не отправляются вовсе, а их имена складываются в
    `ignored` — вызывающий обязан сказать вслух, что фильтр не применен.
    """
    p = section
    known = section_fields(section)
    wanted = {
        "txt": outbound_text(text, "текст поиска"),
        "case_doc": outbound_text(case_doc, "номер дела"),
        "lawchunkinfo": law,
        "date_from": date_from,
        "date_to": date_to,
        "workflow_stage": _resolve("workflow_stage", instance, section),
        region_field(section): _resolve("area", area, section),
        "court": outbound_text(court, "суд"),
        "judge": outbound_text(judge, "судья"),
    }
    q = {}
    for name, value in wanted.items():
        if name in known:
            q[f"{p}-{name}"] = value
        elif value and ignored is not None:
            ignored.add(name)
    if page > 1:
        q["page"] = str(min(page, MAX_PAGES))
    return urllib.parse.urlencode(q)


def parse_results(content, section, limit):
    """Разобрать HTML выдачи в список актов."""
    out = []
    if not content:
        return out
    m = re.search(r'<ul class="results">(.*?)</ul>', content, re.S)
    block = m.group(1) if m else content
    for li in re.findall(r"<li.*?</li>", block, re.S):
        a = re.search(r'<h4>.*?<a href="([^"]+)"[^>]*>(.*?)</a>', li, re.S)
        if not a:
            continue
        href = html.unescape(a.group(1)).split("?")[0]
        title = _strip(a.group(2))
        court = re.search(r'<div class="b-justice">(.*?)</div>', li, re.S)
        body = re.sub(r"(?s)<h4>.*?</h4>", " ", li)
        body = re.sub(r'(?s)<div class="b-justice">.*?</div>', " ", body)
        # Реквизиты из заголовка: «Решение № 2-123/2026 от 25 февраля 2026 г.»
        num = re.search(r"№\s*([^\s]+)", title)
        date = re.search(r"от\s+(\d{1,2}\s+\S+\s+\d{4})", title)
        out.append({
            "title": title,
            "number": num.group(1) if num else "",
            "date": date.group(1) if date else "",
            "court": _strip(court.group(1)) if court else "",
            "section": SECTIONS.get(section, section),
            "url": urllib.parse.urljoin(BASE, href),
            "snippet": _strip(body)[:600],
        })
        if len(out) >= limit:
            break
    return out


# ─────────── Соответствие robots.txt источника ───────────
# На 03.08.2026 https://sudact.ru/robots.txt для User-Agent: * содержит
#   Disallow: /vsrf/doc_ajax/   /regular/doc_ajax/   /arbitral/doc_ajax/   /magistrate/doc_ajax/
# то есть ровно тот путь, которым идет асинхронный поиск. Проверено: обычный
# GET на разрешенный /{раздел}/doc/ результатов не отдает ни при каком числе
# опросов — разрешенного пути к поиску у сайта нет.
#
# Открытие КОНКРЕТНОГО акта по известному URL (/{раздел}/doc/<id>/) под запрет
# не подпадает и работает без всякого гейта.
#
# РЕШЕНИЕ ВЛАДЕЛЬЦА 03.08.2026: поиск разрешен, риск принят осознанно.
# Единственная точка правды — константа ниже; ее же читает PreToolUse-хук
# scripts/claude_guard.py, чтобы два гейта не разъехались.
# Выключить обратно: SUDACT_SEARCH_ALLOWED = False либо THEMIZ_SUDACT_SEARCH=0.
SUDACT_SEARCH_ALLOWED = True
ROBOTS_BLOCKED_HINT = (
    "ПОИСК ВЫКЛЮЧЕН (SUDACT_SEARCH_ALLOWED=False либо THEMIZ_SUDACT_SEARCH=0). "
    "Напоминание: sudact.ru в robots.txt запрещает роботам путь "
    "/{раздел}/doc_ajax/, а асинхронный поиск идет только через него "
    "(разрешенный /doc/ выдачи не отдает — проверено 03.08.2026).\n"
    "Что можно без ограничений: открыть известный акт — practice_search.py --doc URL.\n"
    "Что делать с поиском: решение владельца. Разрешил — "
    "THEMIZ_SUDACT_SEARCH=1 либо --i-accept-robots-risk, и строку в "
    "knowledge/allowed-services.md. Не разрешил — практику брать из "
    "knowledge/practice_index.md и договорного канала."
)


def search_allowed() -> bool:
    env = os.environ.get("THEMIZ_SUDACT_SEARCH")
    if env is not None:
        return env == "1"          # переменная перекрывает решение в обе стороны
    return SUDACT_SEARCH_ALLOWED


_CASE_OVERRIDE = ""  # выставляется main() из явного --case


def _run_case() -> str:
    """Дело прогона: явный --case (main()) важнее $THEMIZ_CASE. Переменную в бою
    никто не выставлял — флага не было вовсе, и мертвый канал не долетал до
    прибора (128 опросов мертвого источника, прогон 01.09.2026)."""
    delo = _CASE_OVERRIDE or os.environ.get("THEMIZ_CASE", "")
    if delo:
        return delo
    # Третий источник — указатель прогона, который ставит проводник. Он и есть
    # единственный работающий в бою: рой ходит из корня, флага ему никто не дает.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import channels
        return channels.tekushchee_delo()
    except Exception:
        return ""


def dead_channel_note() -> str:
    """Свежая отметка «мертв» из общего файла состояния каналов прогона.

    Прогон 01.09.2026: preflight в 16:40 знал, что источник не отвечает, — и это
    знание умерло в выводе одного вызова; дальше рой опросил мертвый источник
    128 раз (271 сетевой вызов, 62,6 млн токенов). Теперь отметка живет в
    .agent/context/channels.json и до истечения TTL записи запрещает поход.
    """
    case = _run_case()
    if not case:
        return ""
    try:
        rec = channels.status(case, "sudact")
    except Exception:
        return ""
    if rec and not rec.get("жив", True):
        left = max(int(rec.get("годен до", 0) - time.time()), 0)
        return (f"КАНАЛ sudact ПОМЕЧЕН МЕРТВЫМ в общем состоянии каналов прогона "
                f"({rec.get('причина') or 'без причины'}). Не опрашивать еще {left} с — "
                "работать по knowledge/practice_index.md либо ждать истечения записи.")
    return ""


def note_source_failure(reason: str) -> None:
    """Сбой источника — в общий файл прогона, чтобы рой не опрашивал мертвый канал."""
    case = _run_case()
    if not case:
        return
    try:
        channels.mark(case, "sudact", False, reason)
    except Exception as e:
        print(f"ВНИМАНИЕ: состояние канала не записано ({e})", file=sys.stderr)


RC_COVERAGE_INCOMPLETE = 5  # неполный охват лечится нашей стороной: --per-region, снять --max-regions
RC_COVERAGE_CAPPED = 6      # упор в предел самого источника — числом не лечится, сузить запрос


def partition_exit_code(report: dict) -> int:
    """Код возврата --partition. Недобор нашей стороны и упор в потолок источника —
    РАЗНЫЕ коды: вызывающий скрипт читает код возврата, а не прозу поля «причина».
    Прежде оба сообщали одним RC_COVERAGE_INCOMPLETE, и по коду нельзя было
    отличить лечимое (поднять --per-region, снять --max-regions) от нелечимого
    (сузить запрос: статья, даты, суд)."""
    if not report.get("error"):
        return 0
    thin = report.get("части с неполным охватом") or []
    if any(t.get("причина") == "упор в потолок выдачи" for t in thin):
        return RC_COVERAGE_CAPPED
    return RC_COVERAGE_INCOMPLETE


def apply_risk_flag(env_value: str | None, flag: bool) -> str | None:
    """Каким станет THEMIZ_SUDACT_SEARCH после флага --i-accept-robots-risk.

    Возвращает '1' (разрешить), None (флага нет — ничего не менять) либо строку
    'ОТКАЗ' (явный запрет перебивать нельзя). Вынесено отдельной функцией, чтобы
    решение проверялось selftest'ом без сети и argparse.
    """
    if not flag:
        return None
    if env_value == "0":
        return "ОТКАЗ"
    return "1"


PAGE_SIZE = 10  # sudact отдает по 10 карточек на страницу


def search(text, section="regular", limit=20, max_wait=25.0, **kw):
    """Найти акты. Возвращает (список, всего_найдено_строкой).

    limit больше размера страницы честно догружает следующие страницы. Раньше
    `--limit 20` молча возвращал 10: разбиралась одна страница, а флаг обещал больше.
    """
    if section not in SECTIONS:
        raise ValueError(f"раздел {section!r} неизвестен; доступны: {', '.join(SECTIONS)}")
    if not search_allowed():
        raise PermissionError(ROBOTS_BLOCKED_HINT)
    dead = dead_channel_note()
    if dead:
        raise PermissionError(dead)

    start_page = int(kw.pop("page", 1) or 1)
    if limit > PAGE_SIZE:
        out, total, seen = [], "", set()
        empty = 0  # подряд страниц без новых актов
        pages = (limit + PAGE_SIZE - 1) // PAGE_SIZE
        for i in range(pages):
            page = start_page + i
            if page > MAX_PAGES:
                # За 50-й страницей выдача зацикливается: сайт отдает ту же
                # страницу, и наивный обход крутится вечно, наращивая дубли.
                print(f"ВНИМАНИЕ: достигнут потолок {MAX_PAGES} страниц выдачи — "
                      "дальше источник повторяет ту же страницу. Сузить запрос "
                      "(регион, инстанция, даты) либо использовать --partition.",
                      file=sys.stderr)
                break
            got, tot = _search_page(text, section, limit, max_wait,
                                    page=page, **kw)
            total = total or tot
            fresh = [r for r in got if r["url"] not in seen]
            seen.update(r["url"] for r in fresh)
            out += fresh
            # Одна пустая страница — еще не конец выдачи (источник отдает
            # страницы неравномерно). Остановка — по ДВУМ пустым подряд.
            empty = empty + 1 if not fresh else 0
            if empty >= 2 or len(out) >= limit:
                break
        return out[:limit], total
    return _search_page(text, section, limit, max_wait, page=start_page, **kw)


# Фильтры, отправленные последним запросом, но не поддержанные разделом. Сайт их
# молча игнорирует и отвечает 200 — без этой записи «отфильтровано» и «не
# отфильтровано» для вызывающего неразличимы.
LAST_IGNORED: set = set()


def _search_page(text, section="regular", limit=20, max_wait=25.0, **kw):
    """Одна страница выдачи."""
    ignored: set = set()
    qs = build_query(section, text=text, ignored=ignored, **kw)
    if ignored:
        LAST_IGNORED.update(ignored)
    ckey = f"search:{section}:{qs}:{limit}"
    cached = _cache_get(ckey)
    if cached:
        return cached["results"], cached.get("total", "")

    doc_url = f"{BASE}/{section}/doc/?{qs}"
    with tempfile.NamedTemporaryFile(suffix=".cookies", delete=False) as tf:
        jar = tf.name
    try:
        _curl(doc_url, jar)  # прайминг: ставит задачу и выдает куку
        ajax_url = f"{BASE}/{section}/doc_ajax/?{qs}"
        deadline = time.time() + max_wait
        content = total = None
        while time.time() < deadline:
            body = _curl(ajax_url, jar, referer=doc_url, ajax=True)
            if body:
                try:
                    d = json.loads(body)
                except ValueError:
                    time.sleep(0.8)
                    continue
                if (d.get("search_status") or d.get("status")) == "finished" and d.get("content"):
                    content, total = d["content"], _strip(d.get("total_found") or "")
                    break
            time.sleep(0.8)
    finally:
        os.unlink(jar)

    if content is None:
        note_source_failure("поиск не завершен: таймаут либо источник недоступен")
        return [], "ПОИСК НЕ ЗАВЕРШЕН (таймаут либо источник недоступен)"
    results = parse_results(content, section, limit)
    # Пустой разбор при ненулевом счетчике — это сбой опроса или смена верстки,
    # а не «ничего не найдено». Закешировав его на две недели, скрипт потом честно
    # отвечает «Ничего не найдено. Найдено 183 документа» — противоречие само себе.
    if not cacheable(results, total):
        print(f"ВНИМАНИЕ: источник заявляет «{total}», а разобрано 0 карточек — "
              "вероятна смена верстки или сбой опроса. Результат НЕ кеширую.",
              file=sys.stderr)
        return results, total
    _cache_put(ckey, {"results": results, "total": total})
    return results, total


# Акт цитируется дословно в судебный документ. Значит источник страницы обязан
# быть тем, за кого себя выдает: чужой хост, редирект на форму входа, страница
# ошибки с кодом 200 или локальный файл не должны попасть в цитату молча.
DOC_URL_RE = re.compile(
    r"^https://sudact\.ru/(vsrf|regular|arbitral|magistrate|law)/doc/[A-Za-z0-9_-]+/?$")


def doc_url_ok(url: str) -> bool:
    return bool(DOC_URL_RE.match((url or "").strip()))


def get_document(url):
    """Полный текст акта для дословного цитирования."""
    if not doc_url_ok(url):
        return {"url": url, "error": (
            "адрес не похож на страницу акта sudact. Ожидается "
            "https://sudact.ru/{vsrf|regular|arbitral|magistrate|law}/doc/<id>/ — "
            "чужой хост, схема file:// и произвольный путь запрещены: текст отсюда "
            "идет в судебный документ дословно")}
    cached = _cache_get(f"doc:{url}")
    if cached:
        return cached
    with tempfile.NamedTemporaryFile(suffix=".cookies", delete=False) as tf:
        jar = tf.name
    try:
        page = _curl(url, jar, timeout=45)
    finally:
        os.unlink(jar)
    if not page:
        return {"url": url, "error": "документ не получен"}
    # Страница ошибки/капчи отдается с кодом 200 и выглядит как обычный HTML.
    # Маркер ищем и в <title>, и в <h1>: у sudact страница сбоя несет родовой
    # заголовок «500. Произошла ошибка», по одному <title> он ловился не всегда.
    head = " ".join(re.findall(r"(?is)<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", page)[:3])
    if _error_page(page):
        return {"url": url,
                "error": f"страница не является актом: заголовок «{_strip(head)[:80]}» — "
                         "ошибка, капча либо заглушка. Цитировать нельзя"}
    # Тело акта лежит между <hr class="hr-h1"> и блоком «поделиться» в подвале.
    # Отдельного контейнера у него нет — рамки установлены по разметке страницы.
    start = page.find('<hr class="hr-h1">')
    raw = page[start:] if start >= 0 else page
    ends = [raw.find(x) for x in ('class="b-email-wrap', 'id="box_send_doc',
                                  'qa_on_mainpage', '<footer')]
    ends = [e for e in ends if e > 0]
    if ends:
        raw = raw[:min(ends)]
    raw = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = raw.replace("</p>", "</p>\n")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(
        ln.strip() for ln in _strip_keep_lines(raw).splitlines())).strip()
    title = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    doc = {"url": url, "title": _strip(title.group(1)) if title else "", "text": text}
    if len(text) <= 200:
        doc["error"] = ("текста меньше 200 знаков — верстка источника изменилась либо "
                        "страница пустая. Цитировать нельзя, сверить глазами по URL")
    if len(text) > 200:
        _cache_put(f"doc:{url}", doc)
    return doc


# ─────────── Партиционирование выдачи по регионам ───────────
# Техника взята из mynka999/sudact-mcp-server (MIT, 30.07.2026) и переписана под
# наш поиск. Суть, которую сами мы не увидели: sudact отдает ограниченное число
# результатов на ОДИН запрос, поэтому широкий запрос показывает верхушку выдачи и
# ничего больше. Замер 03.08.2026: «раздел совместно нажитого имущества» —
# «более 100 000 документов» в счетчике, достать можно десятки. Фильтр по региону
# режет корпус на 85 частей, у каждой свой предел, и охват растет на порядок.
#
# Для охотника это не про объем, а про смещение: выборка из верхушки одной выдачи
# выдается за «практику по вопросу». Партиционирование делает выборку честной, а
# то, что все равно не достали, попадает в отчет явно — как непокрытое.
CAP_HINT = 500  # практический предел одной выдачи sudact
# Ниже этой доли собранная выборка не может выдаваться за практику по вопросу.
# Порог не «чем больше, тем лучше»: у sudact счетчик считает вхождения слов, а не
# релевантные акты, поэтому 100% недостижимы на широком запросе — и именно поэтому
# результат обязан помечаться неполным, а не молча уходить в hunter-файл.
COVERAGE_OK_PERCENT = 80.0

# Параллельность обхода регионов — объявленная константа, а не «до отказа
# источника». Откуда число: решение владельца 02.09.2026 (задача M07) —
# «параллельность до объявленной константы, а не до отказа источника»;
# измеренного порога отказов у sudact нет, поэтому консервативный минимум
# больше одного. Вежливость к источнику важнее скорости прогона: 01.09.2026
# рой сделал 271 сетевой вызов, и мертвый источник опрашивался 128 раз.
# ponytail: потолок числа не измерен; апгрейд — только замером отказов
# источника и правкой этой константы, не рутиной отдельного прогона.
MAX_PARALLEL_REGIONS = 2


def cacheable(results, total_str) -> bool:
    """Класть ли выдачу в кеш на две недели.

    Пустой разбор при ненулевом счетчике — сбой опроса или смена верстки, а не
    «ничего не найдено». В кеше такое отравляет ответ на 14 суток: скрипт потом
    выдает «Ничего не найдено. Найдено 183 документа». Найдено 14 таких записей
    в живом кеше 03.08.2026.
    """
    return bool(results) or not _total_num(total_str)


def _total_num(total_str):
    digits = re.sub(r"[^\d]", "", total_str or "")
    return int(digits) if digits else 0


def partitioned_search(text, section="regular", per_region=30, max_regions=0,
                       cascade=True, log=lambda s: None, **kw):
    """Обойти выдачу по регионам. Возвращает (акты, отчет об охвате).

    Охват меряется ДОЛЕЙ, а не флагом «уперлись». Прежняя формула требовала, чтобы
    источник отдал не меньше per_region карточек — а sudact на живом запросе отдает
    десяток-полтора при счетчике в тысячи. Условие не выполнялось никогда, «непокрытые
    части» оставались пустыми, и механизм честности сообщал о полном покрытии там, где
    реально видел проценты. Замер 03.08.2026: счетчик 3 743, отдано 24.
    """
    # Раздел может не знать самого понятия «регион»: у vsrf такого фильтра нет
    # вовсе. Партиционирование там не сужает выдачу, а лишь повторяет один и тот
    # же запрос 85 раз — и отчет об охвате при этом считался как настоящий.
    if region_field(section) not in section_fields(section):
        return [], {"error": f"раздел «{section}» не поддерживает фильтр по региону "
                             f"(поля формы: {', '.join(sorted(section_fields(section)))}) "
                             "— партиционирование по регионам здесь невозможно"}
    regions = sorted((_load_dicts(section).get("area") or {}))

    # --area с --partition — не потерянный фильтр и не дубль аргумента, а периметр:
    # обход сужается до заданного региона, и это НЕ «пропуск остальных 84».
    user_area = (kw.pop("area", "") or "").strip()
    if user_area:
        universe = scanned = [user_area]
    else:
        if not regions:
            return [], {"error": f"у раздела «{section}» нет справочника регионов"}
        universe = regions
        scanned = regions[:max_regions] if max_regions else regions

    # Пользователь сам задал инстанцию — дробить по ней бессмысленно (и падало на
    # дубле аргумента).
    user_instance = kw.pop("instance", "") or ""
    if user_instance:
        cascade = False
    # Каскад по инстанциям работает только там, где инстанция вообще фильтруется.
    # У magistrate, vsrf и arbitral поля workflow_stage нет: три подзапроса
    # возвращали одну и ту же выдачу и «добирали» ноль, тратя три обращения на регион.
    if "workflow_stage" not in section_fields(section):
        cascade = False

    seen, out, thin, done = set(), [], [], 0
    declared_total = 0
    lock = threading.Lock()

    def take(label, **flt):
        """Один запрос: добрать новые акты, вернуть (сколько заявлено, сколько взято)."""
        got, total = search(text, section=section, limit=per_region, **flt, **kw)
        with lock:  # обход параллелен (MAX_PARALLEL_REGIONS) — seen/out общие
            fresh = [r for r in got if r["url"] not in seen]
            seen.update(r["url"] for r in fresh)
            out.extend(fresh)
        return _total_num(total), len(got)

    def visit(reg):
        """Регион целиком: основной запрос + каскад по инстанциям при недоборе."""
        total, taken = take(reg, area=reg, instance=user_instance)
        best = taken
        if total > taken and cascade:
            # Регион отдал меньше, чем заявил — дробим по инстанциям: каждая часть
            # корпуса имеет собственный предел выдачи, и суммарно достаем больше.
            sub_taken = 0
            for inst in ("первая инстанция", "апелляция", "кассация"):
                _, k = take(f"{reg}/{inst}", area=reg, instance=inst)
                sub_taken += k
            best = max(taken, sub_taken)
        return reg, total, best

    if MAX_PARALLEL_REGIONS > 1 and len(scanned) > 1:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=MAX_PARALLEL_REGIONS) as pool:
            visited = list(pool.map(visit, scanned))  # порядок = порядок scanned
    else:
        visited = [visit(reg) for reg in scanned]

    for i, (reg, total, best) in enumerate(visited, 1):
        declared_total += total
        done += 1
        share = (best / total * 100) if total else 100.0
        log(f"  [{i}/{len(scanned)}] {reg}: заявлено {total or '—'}, взято {best}"
            + (f" ({share:.1f}%)" if total else ""))
        if total > best:
            # Недобор — не одно и то же, что упор в потолок: в первом случае
            # источник отдал меньше запрошенного (стоит сузить запрос), во
            # втором мы уперлись в собственный/источниковый предел выдачи.
            thin.append({"часть": reg, "заявлено": total, "взято": best,
                         "причина": ("упор в потолок выдачи"
                                     if best >= per_region or best >= CAP_HINT
                                     else "недобор")})

    reachable = len(out)
    coverage = (reachable / declared_total * 100) if declared_total else 100.0
    skipped = len(universe) - done
    report = {
        "актов": reachable,
        "заявлено источником": declared_total,
        "охват процентов": round(coverage, 2),
        "регионов обойдено": done,
        "регионов всего": len(universe),
        "регионов пропущено лимитом обхода": skipped,
        "части с неполным охватом": thin,
        "фильтры без поддержки разделом": sorted(LAST_IGNORED),
    }
    # Неполный охват — это ошибка прогона, а не примечание к нему. Раньше отчет
    # печатал «⚠ ОХВАТ НЕПОЛОН» и возвращал 0: автоматика (охотник, скрипт, хук)
    # видела успех и писала верхушку выдачи в hunter-файл как «всю практику».
    why = []
    if thin:
        why.append(f"частей с неполным охватом: {len(thin)}")
    if skipped:
        why.append(f"регионов не обойдено: {skipped}")
    if declared_total and coverage < COVERAGE_OK_PERCENT:
        why.append(f"охват {round(coverage, 2)}% ниже порога {COVERAGE_OK_PERCENT}%")
    if LAST_IGNORED:
        why.append("раздел не поддерживает фильтры: " + ", ".join(sorted(LAST_IGNORED))
                   + " — они НЕ применялись, выдача шире заявленной")
    if why:
        report["error"] = "охват неполон: " + "; ".join(why)
    return out, report


# Поле «суд» — обычный текстовый input без datalist и без autocomplete (проверено
# по самой форме 04.08.2026: на странице ровно два select, и суд не из них).
# Значит подсказок сайт не дает и фильтр срабатывает только на ТОЧНОЕ каноническое
# название: «Мосгорсуд» и «Московский городской» не находят ничего, и отличить это
# от «практики нет» невозможно. Канон лежит в самой выдаче — в поле b-justice
# формата «Московский городской суд (Город Москва) - Гражданское».
# Наблюдение о необходимости добывать канон из выдачи взято у
# mynka999/sudact-mcp-server (MIT); реализация своя.
COURT_LINE_RE = re.compile(r"^(.*?)\s*\((.*?)\)\s*-\s*(.*)$")


def parse_court_line(line: str) -> dict:
    """«Московский городской суд (Город Москва) - Гражданское» → части."""
    m = COURT_LINE_RE.match((line or "").strip())
    if not m:
        return {"court": (line or "").strip(), "area": "", "category": ""}
    return {"court": " ".join(m.group(1).split()),
            "area": " ".join(m.group(2).split()),
            "category": " ".join(m.group(3).split())}


def court_names_from(results: list) -> list[str]:
    """Канонические названия судов из выдачи, без повторов и в порядке встречи."""
    out = []
    for r in results:
        name = parse_court_line(r.get("court", ""))["court"]
        if name and name not in out:
            out.append(name)
    return out


def match_court(query: str, names: list[str]) -> list[str]:
    """Названия, подходящие под запрос юриста. Пусто — точного канона не нашли."""
    q = " ".join((query or "").lower().split())
    if not q:
        return []
    exact = [n for n in names if n.lower() == q]
    if exact:
        return exact
    words = [w for w in re.split(r"[\s,]+", q) if len(w) > 3]
    return [n for n in names if all(w in n.lower() for w in words)] if words else []


def find_court_name(query: str, section: str = "regular", probe: str = "",
                    limit: int = 100) -> list[str]:
    """Найти каноническое название суда, опросив выдачу. Сеть — один запрос."""
    results, _ = search(probe or query, section=section, limit=limit)
    return match_court(query, court_names_from(results))


def _error_page(head_html: str) -> bool:
    """Тот же детектор страницы-заглушки, что в get_document, — вынесен для проверки."""
    head = " ".join(re.findall(r"(?is)<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", head_html)[:3])
    return bool(re.search(r"(?i)\b[45]\d\d\b|не найдена|произошла ошибка|captcha|доступ огранич", head))


def _ignored_of(section, **kw) -> set:
    """Какие фильтры раздел не поддержал. Обертка для проверок."""
    got: set = set()
    build_query(section, text="иск", ignored=got, **kw)
    return got


def selftest():
    """Разбор выдачи и сборка запроса — БЕЗ СЕТИ.

    Раньше проверка дергала справочники сайта и потому зеленела только при живом
    интернете, а на отключенном канале падала SystemExit. Справочник подменяется
    статическим слепком реальных кодов (они читаются из формы сайта, см. _load_dicts).
    """
    global _load_dicts, _search_page, search_allowed
    real_dicts, real_page, real_allowed = _load_dicts, _search_page, search_allowed
    _load_dicts = lambda section="regular": {
        "workflow_stage": {"первая инстанция": "10", "апелляция": "20",
                           "кассация": "30", "надзор": "60", "пересмотр": "40"},
        "area": {"республика татарстан": "1060", "москва": "1077"},
    }
    try:
        sample = ('<ul class="results"><li><h4><a href="/regular/doc/AbC123/?x=1">'
                  'Решение № 2-45/2026 от 25 февраля 2026 г. по делу № 2-45/2026</a></h4>'
                  '<div class="b-justice">Вахитовский районный суд (Республика Татарстан)'
                  '</div><p>текст сниппета</p></li></ul>')
        r = parse_results(sample, "regular", 5)
        q = build_query("regular", text="иск", instance="апелляция", area="Татарстан")
        params = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
        checks = [
            ("одна карточка разобрана", len(r) == 1),
            ("URL абсолютный", r and r[0]["url"] == "https://sudact.ru/regular/doc/AbC123/"),
            ("номер дела извлечен", r and r[0]["number"] == "2-45/2026"),
            ("суд извлечен", r and "Вахитовский" in r[0]["court"]),
            ("дата извлечена", r and "25 февраля 2026" in r[0]["date"]),
            # именно ПОЛНОЕ значение: подстрочная проверка "=2" совпадала и с "=20",
            # из-за чего угаданные коды 1/2/3 когда-то прошли тест
            ("код инстанции точный (20, не 2)", params.get("regular-workflow_stage") == "20"),
            ("код региона точный", params.get("regular-area") == "1060"),
            ("пустая выдача не выдумывает результатов", parse_results("", "regular", 5) == []),
            ("мусорный HTML не роняет разбор", parse_results("<html>oops", "regular", 5) == []),
        ]
        # Пагинация: limit больше страницы обязан догрузить следующие и не дублировать
        pages_asked = []

        def fake_page(text, section="regular", limit=20, max_wait=25.0, **kw):
            pg = kw.get("page", 1)
            pages_asked.append(pg)
            return ([{"url": f"u{pg}-{i}"} for i in range(PAGE_SIZE)], "всего 25")

        _search_page = fake_page
        search_allowed = lambda: True
        many, _ = search("иск", limit=25)
        one_page = list(pages_asked)
        pages_asked.clear()
        few, _ = search("иск", limit=5)
        few_pages = list(pages_asked)

        _search_page = lambda text, section="regular", limit=20, max_wait=25.0, **kw: (
            [{"url": f"same-{i}"} for i in range(PAGE_SIZE)], "всего 10")
        dup, _ = search("иск", limit=30)

        # Остановка — по ДВУМ пустым страницам подряд: одна пустая (стр. 2) обход
        # не глушит, вторая подряд (стр. 4-5) глушит.
        pages_asked.clear()

        def gaps(text, section="regular", limit=20, max_wait=25.0, **kw):
            pg = kw.get("page", 1)
            pages_asked.append(pg)
            if pg in (2, 4, 5):
                return ([], "всего 50")
            return ([{"url": f"g{pg}-{i}"} for i in range(PAGE_SIZE)], "всего 50")

        _search_page = gaps
        gapped, _ = search("иск", limit=50)

        checks += [
            ("адрес акта принимается",
             doc_url_ok("https://sudact.ru/regular/doc/AbC123/")),
            ("чужой хост отбивается",
             not doc_url_ok("https://evil.example/regular/doc/AbC123/")),
            ("file:// отбивается", not doc_url_ok("file:///etc/passwd")),
            ("http вместо https отбивается",
             not doc_url_ok("http://sudact.ru/regular/doc/AbC123/")),
            ("путь /doc/print/ отбивается (запрещен robots)",
             not doc_url_ok("https://sudact.ru/regular/doc/print/AbC123/")),
            ("хвост с параметрами отбивается",
             not doc_url_ok("https://sudact.ru/regular/doc/AbC123/?x=1")),
            ("limit 25 догружает три страницы", one_page == [1, 2, 3] and len(many) == 25),
            ("limit 5 одной страницей", len(few) == PAGE_SIZE and few_pages == [1]),
            ("повтор той же выдачи не дублируется", len(dup) == PAGE_SIZE),
            ("остановка по двум пустым страницам подряд, не по одной",
             pages_asked == [1, 2, 3, 4, 5] and len(gapped) == 2 * PAGE_SIZE),
        ]

        # Партиционирование: упершийся регион дробится, непокрытое попадает в отчет
        real_search, real_dicts2 = search, _load_dicts
        _load_dicts = lambda section="regular": {
            "workflow_stage": {"апелляция": "20"},
            "area": {"москва": "1077", "татарстан": "1060"}}
        seen_calls = []

        def part_fake(text, section="regular", limit=20, max_wait=25.0, **kw):
            seen_calls.append((kw.get("area"), kw.get("instance")))
            if kw.get("area") == "москва" and not kw.get("instance"):
                # источник заявляет тысячи, отдает горсть — так ведет себя живой sudact
                return ([{"url": f"m{i}"} for i in range(12)], "Найдено 9 000 документов")
            if kw.get("area") == "москва":
                # дробление по инстанциям добирает, но все равно не все
                return ([{"url": f"m-{kw.get('instance')}-{i}"} for i in range(4)],
                        "Найдено 4 документа")
            return ([{"url": f"t{i}"} for i in range(3)], "Найдено 3 документа")

        globals()["search"] = part_fake
        acts, rep = partitioned_search("иск", per_region=20)
        # Второй случай: дробление НЕ помогает — предел обязан попасть в отчет,
        # иначе агент примет верхушку выдачи за полную практику по вопросу.
        def always_capped(text, section="regular", limit=20, max_wait=25.0, **kw):
            return ([{"url": f"c-{kw.get('area')}-{kw.get('instance')}-{i}"}
                     for i in range(limit)], "Найдено 9 000 документов")

        globals()["search"] = always_capped
        _, hard = partitioned_search("иск", per_region=20)
        # та же проверка без каскада — инстанция задана пользователем
        _, hard_nc = partitioned_search("иск", per_region=20, instance="апелляция")

        def _no_cascade_reports():
            return any(t["часть"] == "москва" for t in hard_nc["части с неполным охватом"])

        def _unsolvable_reported():
            return any(t["часть"] == "москва" for t in hard["части с неполным охватом"])

        # Третий случай: источник отдал ВСЕ заявленное — отчет обязан быть чистым,
        # иначе честный прогон тоже красится и сигнал обесценивается.
        def full_take(text, section="regular", limit=20, max_wait=25.0, **kw):
            return ([{"url": f"f-{kw.get('area')}-{i}"} for i in range(3)],
                    "Найдено 3 документа")

        globals()["search"] = full_take
        _, full = partitioned_search("иск", per_region=20)
        globals()["search"] = always_capped
        _, limited = partitioned_search("иск", per_region=20, max_regions=1)

        # Четвертый случай: каждая часть отдала все заявленное, но части пересекаются
        # — после снятия дублей уникальных актов вдвое меньше заявленного. Ни одна
        # часть не «тонкая», а выборка все равно неполна: ловится только порогом доли.
        def overlapping(text, section="regular", limit=20, max_wait=25.0, **kw):
            return ([{"url": f"same{i}"} for i in range(3)], "Найдено 3 документа")

        globals()["search"] = overlapping
        _, overlap = partitioned_search("иск", per_region=20)

        # Пятый случай: доля высокая (95%), но одна часть заведомо недобрана.
        # Порог доли такое пропускает — ловится только перечнем тонких частей.
        def near_full(text, section="regular", limit=20, max_wait=25.0, **kw):
            if kw.get("area") == "москва" and not kw.get("instance"):
                return ([{"url": f"n{i}"} for i in range(95)], "Найдено 100 документов")
            if kw.get("area") == "москва":
                return ([], "Ничего не найдено")
            return ([{"url": f"t{i}"} for i in range(3)], "Найдено 3 документа")

        globals()["search"] = near_full
        _, near = partitioned_search("иск", per_region=100)

        # Шестой случай: раздел без фильтра инстанции. Каскад по инстанциям там
        # выполнял три лишних запроса на регион с одинаковой выдачей — источник
        # молча игнорировал workflow_stage и отвечал 200.
        mag_calls = []

        def mag_fake(text, section="regular", limit=20, max_wait=25.0, **kw):
            mag_calls.append((kw.get("area"), kw.get("instance")))
            return ([{"url": f"m-{kw.get('area')}-{i}"} for i in range(2)],
                    "Найдено 9 000 документов")

        globals()["search"] = mag_fake
        partitioned_search("иск", section="magistrate", per_region=20)

        # --partition не теряет --area и --case-doc: область сужает обход до
        # одного региона (периметр запроса, а не пропуск остальных), номер дела
        # доезжает до каждого подзапроса.
        pcalls = []

        def cap_fake(text, section="regular", limit=20, max_wait=25.0, **kw):
            pcalls.append(dict(kw))
            return ([{"url": f"a-{kw.get('area')}-{kw.get('instance')}-{i}"}
                     for i in range(3)], "Найдено 3 документа")

        globals()["search"] = cap_fake
        _, arep = partitioned_search("иск", per_region=20, area="москва",
                                     case_doc="2-45/2026")

        globals()["search"], _load_dicts = real_search, real_dicts2

        # Общий файл состояния каналов: свежая отметка «мертв» закрывает источник
        # до истечения TTL — рой не опрашивает мертвый канал 128 раз (01.09.2026).
        _case_env = os.environ.get("THEMIZ_CASE")
        with tempfile.TemporaryDirectory() as _tmp:
            os.environ["THEMIZ_CASE"] = _tmp
            note_source_failure("HTTP 500")
            marked = channels.is_dead(_tmp, "sudact")
            refused = False
            try:
                search("иск", limit=5)
            except PermissionError as e:
                refused = "МЕРТВ" in str(e)
        if _case_env is None:
            os.environ.pop("THEMIZ_CASE", None)
        else:
            os.environ["THEMIZ_CASE"] = _case_env

        # Штатный путь узнать дело — явный --case, а не только $THEMIZ_CASE: в бою
        # переменную никто не выставлял (флага --case не было вовсе), и мертвый
        # канал оставался невидимым для повторного опроса.
        global _CASE_OVERRIDE
        _override_before = _CASE_OVERRIDE
        _env_gone = os.environ.pop("THEMIZ_CASE", None)
        with tempfile.TemporaryDirectory() as _tmp2:
            _CASE_OVERRIDE = _tmp2
            note_source_failure("HTTP 500 без переменной окружения")
            marked_no_env = channels.is_dead(_tmp2, "sudact")
            refused_no_env = False
            try:
                search("иск", limit=5)
            except PermissionError as e:
                refused_no_env = "МЕРТВ" in str(e)
        os.environ["THEMIZ_CASE"] = "не-эта-переменная"
        _CASE_OVERRIDE = "эта-переменная"
        override_wins = _run_case() == "эта-переменная"
        os.environ.pop("THEMIZ_CASE", None)
        _CASE_OVERRIDE = _override_before
        if _env_gone is not None:
            os.environ["THEMIZ_CASE"] = _env_gone

        checks += [
            ("обойдены все регионы справочника", rep["регионов обойдено"] == 2),
            ("регион с недобором раздроблен по инстанциям",
             sum(1 for c in seen_calls if c[0] == "москва" and c[1]) == 3),
            ("регион без недобора не дробится",
             sum(1 for c in seen_calls if c[0] == "татарстан") == 1),
            ("дубли между партициями сняты", len(acts) == len({a["url"] for a in acts})),
            # ГЛАВНОЕ: живой источник отдает горсть при счетчике в тысячи, и прежняя
            # формула объявляла это полным покрытием. Недобор обязан попадать в отчет.
            ("недобор при большом счетчике назван",
             any(t["часть"] == "москва" for t in rep["части с неполным охватом"])),
            ("охват посчитан долей, а не флагом",
             0 < rep["охват процентов"] < 100),
            ("полный охват не объявляется при недоборе", rep["охват процентов"] != 100),
            ("нерешаемый недобор не замалчивается", _unsolvable_reported()),
            # Ветка без каскада: пользователь сам задал инстанцию. Раньше она была
            # не покрыта тестом, и мутация в ней проходила незамеченной.
            ("недобор попадает в отчет и без каскада", _no_cascade_reports()),
            # Недобор (источник отдал меньше запрошенного) и упор в потолок
            # (собственный/источниковый предел выдачи) называются по-разному.
            ("недобор назван недобором, а не потолком",
             any(t.get("причина") == "недобор" for t in rep["части с неполным охватом"])),
            ("упор в потолок назван потолком",
             hard["части с неполным охватом"] and all(
                 t.get("причина") == "упор в потолок выдачи"
                 for t in hard["части с неполным охватом"])),
            ("--partition не теряет --area: обход сужен до заданного региона",
             arep["регионов обойдено"] == 1 and pcalls
             and all(c.get("area") == "москва" for c in pcalls)),
            ("сужение регионом не считается пропуском остальных",
             arep["регионов пропущено лимитом обхода"] == 0 and not arep.get("error")),
            ("--partition не теряет --case-doc",
             pcalls and all(c.get("case_doc") == "2-45/2026" for c in pcalls)),
            ("сбой источника пишется в общий файл каналов прогона", marked),
            ("меченый мертвым канал не опрашивается до истечения TTL", refused),
            ("--case работает БЕЗ $THEMIZ_CASE: сбой пишется в общий файл", marked_no_env),
            ("--case работает БЕЗ $THEMIZ_CASE: мертвый канал закрывает опрос", refused_no_env),
            ("явный --case важнее $THEMIZ_CASE", override_wins),
            ("пустая выдача при ненулевом счетчике не кешируется",
             not cacheable([], "Найдено 183 документа")),
            ("пустая выдача при нулевом счетчике кешируется",
             cacheable([], "Ничего не найдено")),
            ("непустая выдача кешируется", cacheable([{"url": "x"}], "Найдено 5 документов")),
            # Страница сбоя источника не должна становиться «текстом акта»: она уходит
            # в судебный документ дословно. Проверяем детектор на живых заголовках sudact.
            ("страница 500 опознана как не-акт", _error_page("<title>500. Произошла ошибка</title>")),
            ("страница 404 опознана как не-акт", _error_page("<h1>404. Страница не найдена</h1>")),
            ("капча опознана как не-акт", _error_page("<title>Captcha</title>")),
            ("нормальный заголовок акта не отбрасывается",
             not _error_page("<h1>Решение № 2-45/2026 от 25 февраля 2026 г.</h1>")),
            ("код 500 считается неудачей", http_failed("500")),
            ("код 404 считается неудачей", http_failed("404")),
            ("код 200 проходит", not http_failed("200")),
            ("код 302 проходит", not http_failed("302")),
            ("тело отделяется от кода",
             split_status("<html>тело</html>__HTTPSTATUS__500") == ("<html>тело</html>", "500")),
            ("ответ без маркера не портится", split_status("<html>тело</html>")[0] == "<html>тело</html>"),
            # Неполный охват обязан быть машиночитаемой ошибкой, а не примечанием:
            # без этого «⚠ ОХВАТ НЕПОЛОН» + rc=0 читается автоматикой как успех.
            ("неполный охват помечен ошибкой в отчете", bool(rep.get("error"))),
            ("нерешаемый недобор помечен ошибкой", bool(hard.get("error"))),
            ("полный охват ошибкой не помечен", not full.get("error")),
            ("необойденные регионы попадают в ошибку",
             "не обойдено" in (limited.get("error") or "")),
            ("порог охвата назван в тексте ошибки",
             "охват" in (hard.get("error") or "")),
            ("низкая доля ловится даже без тонких частей",
             not overlap["части с неполным охватом"]
             and bool(overlap.get("error"))
             and "ниже порога" in overlap["error"]),
            ("неполный охват дает ненулевой код возврата",
             partition_exit_code(rep) != 0 and partition_exit_code(near) != 0),
            ("полный охват дает нулевой код возврата", partition_exit_code(full) == 0),
            ("недобор дает RC_COVERAGE_INCOMPLETE, не код потолка",
             partition_exit_code(rep) == RC_COVERAGE_INCOMPLETE
             and partition_exit_code(near) == RC_COVERAGE_INCOMPLETE),
            ("упор в потолок дает отдельный код, не код недобора",
             partition_exit_code(hard) == RC_COVERAGE_CAPPED
             and partition_exit_code(hard_nc) == RC_COVERAGE_CAPPED
             and RC_COVERAGE_CAPPED != RC_COVERAGE_INCOMPLETE),
            ("тонкая часть ловится даже при высокой доле",
             near["охват процентов"] >= COVERAGE_OK_PERCENT
             and bool(near.get("error"))
             and "частей с неполным охватом" in near["error"]),
            # Аварийный выключатель: флаг риска — согласие ЗА молчание проекта,
            # а не право снять явный запрет владельца.
            # Поле «суд» — текстовый input без подсказок: фильтр берет только
            # точное каноническое название, а канон лежит в выдаче (b-justice).
            ("строка суда разбирается на части",
             parse_court_line("Московский городской суд (Город Москва) - Гражданское")
             == {"court": "Московский городской суд", "area": "Город Москва",
                 "category": "Гражданское"}),
            ("двойные пробелы в названии схлопываются",
             parse_court_line("Аксайский  районный суд (Ростовская область) - Гражданское")
             ["court"] == "Аксайский районный суд"),
            ("строка без скобок не теряется",
             parse_court_line("Верховный Суд РФ")["court"] == "Верховный Суд РФ"),
            ("канонические названия собираются без повторов",
             court_names_from([{"court": "Московский городской суд (Город Москва) - Гражданское"},
                               {"court": "Московский городской суд (Город Москва) - Административное"},
                               {"court": "Аксайский районный суд (Ростовская область) - Гражданское"}])
             == ["Московский городской суд", "Аксайский районный суд"]),
            ("точное название находится",
             match_court("Московский городской суд",
                         ["Московский городской суд", "Московский районный суд"])
             == ["Московский городской суд"]),
            # Точное совпадение обязано БИТЬ подстроку: иначе «Московский городской
            # суд» вернет заодно все суды, чье название начинается так же, и юрист
            # подставит в фильтр не тот из них.
            ("точное совпадение бьет подстроку",
             match_court("Московский городской суд",
                         ["Московский городской суд",
                          "Московский городской суд апелляционной инстанции"])
             == ["Московский городской суд"]),
            ("без точного совпадения выдаются все кандидаты",
             len(match_court("московский городской",
                             ["Московский городской суд",
                              "Московский городской суд апелляционной инстанции"])) == 2),
            ("неполное название сводится к кандидатам",
             match_court("московский городской",
                         ["Московский городской суд", "Аксайский районный суд"])
             == ["Московский городской суд"]),
            ("сокращение канона не дает — и это честный пустой ответ",
             match_court("Мосгорсуд", ["Московский городской суд"]) == []),
            # Карта полей формы: прочитана из самих форм разделов 04.08.2026.
            ("у арбитража регион зовется region", region_field("arbitral") == "region"),
            ("у СОЮ регион зовется area", region_field("regular") == "area"),
            ("ВС РФ не знает фильтра по региону",
             "area" not in section_fields("vsrf") and "region" not in section_fields("vsrf")),
            ("мировые судьи не знают инстанции",
             "workflow_stage" not in section_fields("magistrate")),
            ("арбитраж не знает инстанции",
             "workflow_stage" not in section_fields("arbitral")),
            ("раздел без фильтра инстанции не дробится по инстанциям",
             mag_calls and not any(c[1] for c in mag_calls)),
            ("раздел без фильтра инстанции все равно обходится по регионам",
             len({c[0] for c in mag_calls}) == 2),
            ("СОЮ знает инстанцию", "workflow_stage" in section_fields("regular")),
            # Неподдержанное поле НЕ отправляется и называется вызывающему.
            ("неподдержанный фильтр не уходит в запрос",
             "vsrf-area" not in build_query("vsrf", text="иск", area="1077")),
            ("неподдержанный фильтр назван",
             _ignored_of("vsrf", area="1077") == {"area"}),
            ("поддержанный фильтр не считается игнорированным",
             _ignored_of("regular", area="1077") == set()),
            ("пустое значение неподдержанного поля тревоги не дает",
             _ignored_of("vsrf", area="") == set()),
            ("партиционирование ВС РФ отклоняется с причиной",
             "не поддерживает фильтр по региону"
             in (partitioned_search("иск", section="vsrf")[1].get("error") or "")),
            # Потолок страниц: за 50-й выдача зацикливается.
            ("страница за потолком срезается",
             "page=50" in build_query("regular", text="иск", page=99)),
            ("страница в пределах потолка не трогается",
             "page=7" in build_query("regular", text="иск", page=7)),
            ("флаг риска не перебивает явное выключение",
             apply_risk_flag("0", True) == "ОТКАЗ"),
            ("флаг риска включает поиск при молчании переменной",
             apply_risk_flag(None, True) == "1"),
            ("флаг риска не мешает уже включенному",
             apply_risk_flag("1", True) == "1"),
            ("без флага переменная не трогается", apply_risk_flag("0", False) is None),
            ("ПД в запросе обезличиваются до внешнего URL",
             "Кузнецова" not in build_query("regular",
                                             text="Кузнецова Мария Петровна, раздел имущества")),
            ("номер дела в case_doc обезличивается",
             "А65-12345/2026" not in build_query("regular", case_doc="А65-12345/2026")),
            ("чистый правовой вопрос не искажается",
             "333" in build_query("regular", text="ст. 333 ГК РФ")),
        ]
    finally:
        _load_dicts, _search_page, search_allowed = real_dicts, real_page, real_allowed
    for name, ok in checks:
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"selftest {'пройден' if not bad else 'ПРОВАЛЕН'}: {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        description="Бесплатный поиск судебной практики (sudact.ru): СОЮ, мировые, ВС РФ, арбитраж.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Разделы: " + ", ".join(f"{k} — {v}" for k, v in SECTIONS.items()))
    ap.add_argument("query", nargs="?", default="", help="текст для поиска по документам")
    ap.add_argument("--section", default="regular", help="раздел (по умолчанию regular — СОЮ)")
    ap.add_argument("--instance", default="", help="инстанция: первая, апелляция, кассация, надзор")
    ap.add_argument("--area", default="", help="регион (код региона sudact)")
    ap.add_argument("--court", default="", help="код суда")
    ap.add_argument("--law", default="", help="статья закона, напр. «38 СК РФ»")
    ap.add_argument("--case-doc", default="", help="номер дела")
    ap.add_argument("--date-from", default="", help="дата с, ДД.ММ.ГГГГ")
    ap.add_argument("--date-to", default="", help="дата по, ДД.ММ.ГГГГ")
    ap.add_argument("--page", type=int, default=1, help="страница выдачи")
    ap.add_argument("--limit", type=int, default=20, help="сколько актов вернуть")
    ap.add_argument("--doc", default="", help="URL акта — выдать полный текст")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--selftest", action="store_true", help="проверка разбора без сети")
    ap.add_argument("--partition", action="store_true",
                    help="обойти выдачу по регионам: широкий запрос показывает только "
                         "верхушку, партиционирование дает честную выборку")
    ap.add_argument("--per-region", type=int, default=20, help="актов с региона (с --partition)")
    ap.add_argument("--max-regions", type=int, default=0, help="ограничить обход (0 — все 85)")
    ap.add_argument("--find-court", metavar="НАЗВАНИЕ",
                    help="найти каноническое название суда для --court "
                         "(автокомплита у источника нет, фильтр требует точного)")
    ap.add_argument("--i-accept-robots-risk", action="store_true",
                    help="включить поиск, запрещенный robots.txt источника (решение владельца)")
    ap.add_argument("--case", default="", help="путь к делу — общий счет каналов и квот "
                    "прогона (.agent/context/channels.json); иначе $THEMIZ_CASE")
    args = ap.parse_args()
    global _CASE_OVERRIDE
    _CASE_OVERRIDE = args.case
    # Флаг риска — согласие ЗА молчание, а не поверх явного запрета. Прежняя
    # безусловная запись env="1" делала аварийный выключатель бесполезным: тот, кто
    # выставил THEMIZ_SUDACT_SEARCH=0, получал поиск. Хук claude_guard.py при env=0
    # сырой curl не пускает — скрипт и хук расходились.
    decided = apply_risk_flag(os.environ.get("THEMIZ_SUDACT_SEARCH"),
                              getattr(args, "i_accept_robots_risk", False))
    if decided == "ОТКАЗ":
        print("ОТКАЗ: THEMIZ_SUDACT_SEARCH=0 — поиск выключен явно. Флаг "
              "--i-accept-robots-risk запрет не снимает: снимите переменную "
              "или выставьте THEMIZ_SUDACT_SEARCH=1.", file=sys.stderr)
        return 4
    if decided:
        os.environ["THEMIZ_SUDACT_SEARCH"] = decided

    if args.selftest:
        return selftest()

    if args.find_court:
        names = find_court_name(args.find_court, section=args.section,
                                probe=args.query or "суд", limit=args.limit or 100)
        if not names:
            print(f"каноническое название для «{args.find_court}» в выдаче не найдено. "
                  "Фильтр --court принимает ТОЛЬКО точное название: подберите запрос "
                  "пошире (--limit 100) либо задайте регион через --area.",
                  file=sys.stderr)
            return 1
        for n in names:
            print(n)
        return 0

    if args.doc:
        d = get_document(args.doc)
        if args.json:
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(d.get("title", ""))
            print(d.get("text", d.get("error", ""))[:20000])
        return 0 if "error" not in d else 1

    if not args.query and not args.law and not args.case_doc:
        ap.error("нужен текст запроса, --law или --case-doc")

    if args.partition:
        acts, rep = partitioned_search(
            args.query, section=args.section, per_region=args.per_region,
            max_regions=args.max_regions, log=lambda m: print(m, file=sys.stderr),
            instance=args.instance, area=args.area, court=args.court, law=args.law,
            case_doc=args.case_doc,
            date_from=args.date_from, date_to=args.date_to)
        if args.json:
            out = {"results": acts, "coverage": rep}
            if rep.get("error"):
                out["error"] = rep["error"]
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return partition_exit_code(rep)
        if rep.get("актов") is None or rep.get("регионов всего") is None:
            # Раздел отказал до обхода — печатать сводку «0 из 0» не о чем.
            print(f"\n❌ {rep.get('error', 'партиционирование не выполнено')}",
                  file=sys.stderr)
            return partition_exit_code(rep)
        print(f"\nСобрано актов: {rep.get('актов', 0)} из заявленных источником "
              f"{rep.get('заявлено источником', 0):,}".replace(",", " ")
              + f" — охват {rep.get('охват процентов', 0)}% "
                f"(регионов {rep.get('регионов обойдено')} из {rep.get('регионов всего')})")
        thin = rep.get("части с неполным охватом") or []
        if thin:
            worst = sorted(thin, key=lambda t: -t["заявлено"])[:5]
            print(f"⚠ ОХВАТ НЕПОЛОН в {len(thin)} частях. Крупнейшие: "
                  + "; ".join(f"{t['часть']} — взято {t['взято']} из {t['заявлено']}"
                              for t in worst))
            print("  Это НЕ вся практика по вопросу. Сузить запрос (статья, даты, суд) "
                  "либо честно писать в hunter-файле, какая доля выдачи просмотрена.")
        for i, r in enumerate(acts[:args.limit], 1):
            print(f"\n{i}. {r['title']}\n   {r['court']}\n   {r['url']}")
        if rep.get("error"):
            print(f"\n❌ {rep['error']}", file=sys.stderr)
        return partition_exit_code(rep)

    results, total = search(
        args.query, section=args.section, limit=args.limit, instance=args.instance,
        area=args.area, court=args.court, law=args.law, case_doc=args.case_doc,
        date_from=args.date_from, date_to=args.date_to, page=args.page)

    if args.json:
        print(json.dumps({"total": total, "results": results}, ensure_ascii=False, indent=2))
        return 0 if results else 1

    if not results:
        print(f"Ничего не найдено. {total}")
        return 1
    print(f"{total or ''}  (показано {len(results)})\n")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['title']}")
        print(f"   {r['court']}")
        print(f"   {r['url']}")
        if r["snippet"]:
            print(f"   {r['snippet'][:220]}...")
        print()
    return 0


def _main_guarded():
    """Отказ по robots — это решение, а не авария: печатаем объяснение, не трейс."""
    try:
        return main()
    except PermissionError as e:
        print(str(e), file=sys.stderr)
        return 4
    except PiiOutboundError as e:
        print(f"ОТКАЗ: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(_main_guarded())
