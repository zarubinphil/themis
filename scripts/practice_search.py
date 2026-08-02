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


def search(text, section="regular", limit=20, max_wait=25.0, **kw):
    """Найти акты. Возвращает (список, всего_найдено_строкой)."""
    if section not in SECTIONS:
        raise ValueError(f"раздел {section!r} неизвестен; доступны: {', '.join(SECTIONS)}")
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


def get_document(url):
    """Полный текст акта для дословного цитирования."""
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
    if len(text) > 200:
        _cache_put(f"doc:{url}", doc)
    return doc


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
    args = ap.parse_args()

    if args.selftest:
        sample = ('<ul class="results"><li><h4><a href="/regular/doc/AbC123/?x=1">'
                  'Решение № 2-45/2026 от 25 февраля 2026 г. по делу № 2-45/2026</a></h4>'
                  '<div class="b-justice">Вахитовский районный суд (Республика Татарстан)'
                  '</div><p>текст сниппета</p></li></ul>')
        r = parse_results(sample, "regular", 5)
        assert len(r) == 1, r
        assert r[0]["url"] == "https://sudact.ru/regular/doc/AbC123/", r
        assert r[0]["number"] == "2-45/2026", r
        assert "Вахитовский" in r[0]["court"], r
        assert "25 февраля 2026" in r[0]["date"], r
        q = build_query("regular", text="иск", instance="апелляция")
        assert "regular-workflow_stage=2" in q, q
        assert parse_results("", "regular", 5) == []
        print("selftest: OK")
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


if __name__ == "__main__":
    sys.exit(main())
