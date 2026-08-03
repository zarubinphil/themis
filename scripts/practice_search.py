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
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse

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
    """Справочники инстанций и регионов из формы поиска. Кеш на 30 суток."""
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
    """GET через curl. Возвращает тело ответа строкой либо None."""
    cmd = ["curl", "-s", "-A", UA, "-b", cookie_jar, "-c", cookie_jar,
           "--max-time", str(timeout), "-H", "Accept-Language: ru,en;q=0.8"]
    if ajax:
        cmd += ["-H", "X-Requested-With: XMLHttpRequest"]
    if referer:
        cmd += ["-e", referer]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


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


def build_query(section, text="", instance="", area="", court="", judge="",
                law="", case_doc="", date_from="", date_to="", page=1):
    """Строка параметров поиска. Имена полей у раздела свои: regular-txt, vsrf-txt и т.д."""
    p = section
    q = {
        f"{p}-txt": text,
        f"{p}-case_doc": case_doc,
        f"{p}-lawchunkinfo": law,
        f"{p}-date_from": date_from,
        f"{p}-date_to": date_to,
        f"{p}-workflow_stage": _resolve("workflow_stage", instance, section),
        f"{p}-area": _resolve("area", area, section),
        f"{p}-court": court,
        f"{p}-judge": judge,
    }
    if page > 1:
        q["page"] = str(page)
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
# РЕШЕНИЕ ВЛАДЕЛЬЦА 03.08.2026: поиск разрешён, риск принят осознанно.
# Единственная точка правды — константа ниже; её же читает PreToolUse-хук
# scripts/claude_guard.py, чтобы два гейта не разъехались.
# Выключить обратно: SUDACT_SEARCH_ALLOWED = False либо THEMIS_SUDACT_SEARCH=0.
SUDACT_SEARCH_ALLOWED = True
ROBOTS_BLOCKED_HINT = (
    "ПОИСК ВЫКЛЮЧЕН (SUDACT_SEARCH_ALLOWED=False либо THEMIS_SUDACT_SEARCH=0). "
    "Напоминание: sudact.ru в robots.txt запрещает роботам путь "
    "/{раздел}/doc_ajax/, а асинхронный поиск идет только через него "
    "(разрешенный /doc/ выдачи не отдает — проверено 03.08.2026).\n"
    "Что можно без ограничений: открыть известный акт — practice_search.py --doc URL.\n"
    "Что делать с поиском: решение владельца. Разрешил — "
    "THEMIS_SUDACT_SEARCH=1 либо --i-accept-robots-risk, и строку в "
    "knowledge/allowed-services.md. Не разрешил — практику брать из "
    "knowledge/practice_index.md и договорного канала."
)


def search_allowed() -> bool:
    env = os.environ.get("THEMIS_SUDACT_SEARCH")
    if env is not None:
        return env == "1"          # переменная перекрывает решение в обе стороны
    return SUDACT_SEARCH_ALLOWED


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

    start_page = int(kw.pop("page", 1) or 1)
    if limit > PAGE_SIZE:
        out, total, seen = [], "", set()
        pages = (limit + PAGE_SIZE - 1) // PAGE_SIZE
        for i in range(pages):
            got, tot = _search_page(text, section, limit, max_wait,
                                    page=start_page + i, **kw)
            total = total or tot
            fresh = [r for r in got if r["url"] not in seen]
            seen.update(r["url"] for r in fresh)
            out += fresh
            if not fresh or len(out) >= limit:
                break
        return out[:limit], total
    return _search_page(text, section, limit, max_wait, page=start_page, **kw)


def _search_page(text, section="regular", limit=20, max_wait=25.0, **kw):
    """Одна страница выдачи."""
    qs = build_query(section, text=text, **kw)
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
        return [], "ПОИСК НЕ ЗАВЕРШЕН (таймаут либо источник недоступен)"
    results = parse_results(content, section, limit)
    _cache_put(ckey, {"results": results, "total": total})
    return results, total


# Акт цитируется дословно в судебный документ. Значит источник страницы обязан
# быть тем, за кого себя выдаёт: чужой хост, редирект на форму входа, страница
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
    # Страница ошибки/капчи отдаётся с кодом 200 и выглядит как обычный HTML.
    if re.search(r"(?i)<title>[^<]*(404|не найдена|ошибка|captcha|доступ огранич)", page):
        return {"url": url, "error": "страница не является актом (ошибка, капча либо заглушка)"}
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
        doc["error"] = ("текста меньше 200 знаков — вёрстка источника изменилась либо "
                        "страница пустая. Цитировать нельзя, сверить глазами по URL")
    if len(text) > 200:
        _cache_put(f"doc:{url}", doc)
    return doc


# ─────────── Партиционирование выдачи по регионам ───────────
# Техника взята из mynka999/sudact-mcp-server (MIT, 30.07.2026) и переписана под
# наш поиск. Суть, которую сами мы не увидели: sudact отдаёт ограниченное число
# результатов на ОДИН запрос, поэтому широкий запрос показывает верхушку выдачи и
# ничего больше. Замер 03.08.2026: «раздел совместно нажитого имущества» —
# «более 100 000 документов» в счётчике, достать можно десятки. Фильтр по региону
# режет корпус на 85 частей, у каждой свой предел, и охват растёт на порядок.
#
# Для охотника это не про объём, а про смещение: выборка из верхушки одной выдачи
# выдаётся за «практику по вопросу». Партиционирование делает выборку честной, а
# то, что всё равно не достали, попадает в отчёт явно — как непокрытое.
CAP_HINT = 500  # практический предел одной выдачи sudact


def _total_num(total_str):
    digits = re.sub(r"[^\d]", "", total_str or "")
    return int(digits) if digits else 0


def partitioned_search(text, section="regular", per_region=30, max_regions=0,
                       cascade=True, log=lambda s: None, **kw):
    """Обойти выдачу по регионам. Возвращает (акты, отчёт о покрытии)."""
    regions = sorted((_load_dicts(section).get("area") or {}))
    if not regions:
        return [], {"error": f"у раздела «{section}» нет справочника регионов"}
    scanned = regions[:max_regions] if max_regions else regions

    # Пользователь мог сам задать инстанцию — тогда дробить по ней бессмысленно
    # и вдобавок падало на дубле аргумента.
    user_instance = kw.pop("instance", "") or ""
    if user_instance:
        cascade = False
    seen, out, capped, done = set(), [], [], 0
    for i, reg in enumerate(scanned, 1):
        got, total = search(text, section=section, limit=per_region, area=reg,
                            instance=user_instance, **kw)
        fresh = [r for r in got if r["url"] not in seen]
        seen.update(r["url"] for r in fresh)
        out += fresh
        done += 1
        hit_cap = _total_num(total) > len(got) >= min(per_region, CAP_HINT)
        log(f"  [{i}/{len(scanned)}] {reg}: в счётчике {total or '—'}, взято {len(fresh)}"
            + (" — УПЁРЛОСЬ В ПРЕДЕЛ" if hit_cap else ""))
        if not (hit_cap and cascade):
            if hit_cap:
                capped.append(reg)
            continue
        # регион всё равно упёрся — дробим его по инстанциям
        split_ok = False
        for inst in ("первая инстанция", "апелляция", "кассация"):
            sub, sub_total = search(text, section=section, limit=per_region,
                                    area=reg, instance=inst, **kw)
            fresh = [r for r in sub if r["url"] not in seen]
            seen.update(r["url"] for r in fresh)
            out += fresh
            if _total_num(sub_total) > len(sub) >= min(per_region, CAP_HINT):
                capped.append(f"{reg} / {inst}")
            else:
                split_ok = True
        if not split_ok:
            capped.append(reg)

    report = {
        "актов": len(out),
        "регионов обойдено": done,
        "регионов всего": len(regions),
        "регионов пропущено лимитом обхода": len(regions) - done,
        "непокрытые части": capped,
    }
    return out, report


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

        _search_page = lambda text, section="regular", limit=20, max_wait=25.0, **kw: (
            [{"url": f"same-{i}"} for i in range(PAGE_SIZE)], "всего 10")
        dup, _ = search("иск", limit=30)

        checks += [
            ("адрес акта принимается",
             doc_url_ok("https://sudact.ru/regular/doc/AbC123/")),
            ("чужой хост отбивается",
             not doc_url_ok("https://evil.example/regular/doc/AbC123/")),
            ("file:// отбивается", not doc_url_ok("file:///etc/passwd")),
            ("http вместо https отбивается",
             not doc_url_ok("http://sudact.ru/regular/doc/AbC123/")),
            ("путь /doc/print/ отбивается (запрещён robots)",
             not doc_url_ok("https://sudact.ru/regular/doc/print/AbC123/")),
            ("хвост с параметрами отбивается",
             not doc_url_ok("https://sudact.ru/regular/doc/AbC123/?x=1")),
            ("limit 25 догружает три страницы", one_page == [1, 2, 3] and len(many) == 25),
            ("limit 5 одной страницей", len(few) == PAGE_SIZE and pages_asked == [1]),
            ("повтор той же выдачи не дублируется", len(dup) == PAGE_SIZE),
        ]

        # Партиционирование: упёршийся регион дробится, непокрытое попадает в отчёт
        real_search, real_dicts2 = search, _load_dicts
        _load_dicts = lambda section="regular": {
            "workflow_stage": {"апелляция": "20"},
            "area": {"москва": "1077", "татарстан": "1060"}}
        seen_calls = []

        def part_fake(text, section="regular", limit=20, max_wait=25.0, **kw):
            seen_calls.append((kw.get("area"), kw.get("instance")))
            if kw.get("area") == "москва" and not kw.get("instance"):
                # регион упёрся в предел выдачи
                return ([{"url": f"m{i}"} for i in range(limit)], "Найдено 9 000 документов")
            if kw.get("area") == "москва":
                # после дробления по инстанциям предел уже не достигается
                return ([{"url": f"m-{kw.get('instance')}-{i}"} for i in range(4)],
                        "Найдено 4 документа")
            return ([{"url": f"t{i}"} for i in range(3)], "Найдено 3 документа")

        globals()["search"] = part_fake
        acts, rep = partitioned_search("иск", per_region=20)
        # Второй случай: дробление НЕ помогает — предел обязан попасть в отчёт,
        # иначе агент примет верхушку выдачи за полную практику по вопросу.
        def always_capped(text, section="regular", limit=20, max_wait=25.0, **kw):
            return ([{"url": f"c-{kw.get('area')}-{kw.get('instance')}-{i}"}
                     for i in range(limit)], "Найдено 9 000 документов")

        globals()["search"] = always_capped
        _, hard = partitioned_search("иск", per_region=20)

        def _unsolvable_reported():
            return "москва" in hard["непокрытые части"]

        globals()["search"], _load_dicts = real_search, real_dicts2

        checks += [
            ("обойдены все регионы справочника", rep["регионов обойдено"] == 2),
            ("упёршийся регион раздроблен по инстанциям",
             sum(1 for c in seen_calls if c[0] == "москва" and c[1]) == 3),
            ("не упёршийся регион не дробится",
             sum(1 for c in seen_calls if c[0] == "татарстан") == 1),
            ("дубли между партициями сняты", len(acts) == len({a["url"] for a in acts})),
            ("дробление сняло предел — непокрытого нет", rep["непокрытые части"] == []),
            ("нерешаемый предел не замалчивается", _unsolvable_reported()),
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
                         "верхушку, партиционирование даёт честную выборку")
    ap.add_argument("--per-region", type=int, default=20, help="актов с региона (с --partition)")
    ap.add_argument("--max-regions", type=int, default=0, help="ограничить обход (0 — все 85)")
    ap.add_argument("--i-accept-robots-risk", action="store_true",
                    help="включить поиск, запрещенный robots.txt источника (решение владельца)")
    args = ap.parse_args()
    if getattr(args, "i_accept_robots_risk", False):
        os.environ["THEMIS_SUDACT_SEARCH"] = "1"

    if args.selftest:
        return selftest()

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
            instance=args.instance, court=args.court, law=args.law,
            date_from=args.date_from, date_to=args.date_to)
        if args.json:
            print(json.dumps({"results": acts, "coverage": rep}, ensure_ascii=False, indent=2))
            return 0
        print(f"\nСобрано актов: {rep.get('актов', 0)} "
              f"(регионов {rep.get('регионов обойдено')} из {rep.get('регионов всего')})")
        if rep.get("непокрытые части"):
            print(f"⚠ НЕ ПОКРЫТО ПОЛНОСТЬЮ: {len(rep['непокрытые части'])} частей — "
                  f"{', '.join(rep['непокрытые части'][:6])}"
                  f"{'…' if len(rep['непокрытые части']) > 6 else ''}. "
                  f"Выборка по ним неполная, сузить запрос или добавить фильтры.")
        for i, r in enumerate(acts[:args.limit], 1):
            print(f"\n{i}. {r['title']}\n   {r['court']}\n   {r['url']}")
        return 0

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


if __name__ == "__main__":
    sys.exit(_main_guarded())
