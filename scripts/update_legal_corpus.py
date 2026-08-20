#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_legal_corpus.py — локальный корпус действующего права в markdown.

ЗАЧЕМ. Фемида должна цитировать нормы и Пленумы дословно из файлов на диске
(scripts/cite.py), а не через модель и не через веб на каждом деле — это
снимает 100-300k токенов с прогона и убирает риск искаженной цитаты
(прецедент: WebFetch исказил текст ст. 683 ГК РФ на боевом деле).

ИСТОЧНИКИ (проверено вручную curl 02.08.2026 — не предполагать, проверять).

  Кодексы. pravo.gov.ru имеет открытый API (/api/Documents, /api/Document,
  /opendata) — проверено, отвечает. Но отдает ОПУБЛИКОВАННЫЕ АКТЫ (исходный
  закон + каждый закон-о-внесении-изменений отдельно), а не консолидированную
  действующую редакцию кодекса. Свести десятки актов о поправках в текущую
  редакцию программно — отдельная и намного более рискованная задача (риск
  собрать редакцию неверно), вне разумного скоупа этого скрипта. Официального
  бесплатного API консолидированного текста в РФ нет ни у одного госоргана.
  Источник консолидированного текста — consultant.ru (КонсультантПлюс):
  каждая статья — отдельная страница по стабильному хеш-URL внутри ToC
  документа (cons_doc_LAW_<ID>), дата актуальной редакции читается прямо
  со страницы («... от ДД.ММ.ГГГГ N ФЗ (ред. от ДД.ММ.ГГГГ)»). legalacts.ru
  зеркалит ЭТИ ЖЕ страницы (проверено — те же хеши в URL), т.е. это тот же
  первоисточник. consultant.ru отвечает на обычный GET без JS и без блокировки
  по UA (проверено на 15+ документах).

  Пленумы и обзоры ВС РФ. vsrf.ru — официальный источник, но отдает документы
  как PDF (проверено: /documents/own/<id>/ и /documents/all/<id>/ — оба PDF),
  а каталог (/documents/own/?category=...) подгружается JS/AJAX и sitemap
  отсутствует (проверено: sitemap.xml и robots.txt пустые/404) — массовый
  обход невозможен без браузера. sudact.ru зеркалит официальный текст как
  обычный серверный HTML с РАБОЧИМ постраничным каталогом
  (/law/authority/plenum-vs-rf/?page=N, проверено постранично) — используется
  как основной канал обхода; уже в белом списке (knowledge/allowed-services.md).
  PDF с vsrf.ru — точечная сверка при заявленном расхождении, не основной путь.

  Текст PDF конвертируется через markitdown — пакет уже установлен в проекте
  для кейс-документов (scripts/markdown_extract.py импортирует его же), новый
  пакет НЕ ставим.

РЕЖИМЫ:
  --init                    первичная выгрузка всего реестра (кодексы + план)
  --update                  перекачать; если текст разошелся — старую версию
                            в knowledge/_corpus_archive/{ДД.ММ.ГГГГ}/, новую записать
  --check                   дешевая сверка: только дата редакции/хэш, без полной
                            перекачки всех статей
  --doc SLUG                ограничиться одним документом реестра кодексов
  --plenums                 обход каталога Пленумов ВС РФ (sudact.ru)
  --plenum-pages N          потолок страниц каталога (обход встает на первой пустой)
  --plenum-slug SLUG        один Пленум по известному URL-слагу sudact.ru
  --demo                    самопроверка парсера без сети

ponytail: только stdlib (urllib, html.parser, re, hashlib, argparse) +
markitdown, который уже установлен в проекте. Новых пакетов нет. Кеш HTTP на
диске — повторный прогон того же документа стоит секунд, не десятков минут.
"""
import argparse
import hashlib
import html
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from html.parser import HTMLParser

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
import pii_gate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KODEKSY_DIR = os.path.join(ROOT, "knowledge", "kodeksy")
PLENUMY_DIR = os.path.join(ROOT, "knowledge", "plenumy")
ARCHIVE_DIR = os.path.join(ROOT, "knowledge", "_corpus_archive")
LOG_FILE = os.path.join(ROOT, "knowledge", "_corpus_log.md")
CACHE_DIR = os.path.expanduser("~/.cache/legal_corpus")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 20
REQUEST_DELAY = 0.35  # вежливая пауза между запросами — не долбить публикатора
TODAY = date.today().strftime("%d.%m.%Y")

# --------------------------------------------------------------------------
# Реестр кодексов. doc_ids — один или несколько cons_doc_LAW_<ID> consultant.ru
# (несколько — для ГК РФ, где части I-IV суть отдельные документы КонсультантПлюс,
# но по правилу "один файл на кодекс" сводятся в один gk-rf.md).
# chapter_scope — если задан, в файл идут статьи ТОЛЬКО из этой главы (и её
# подглав) до начала следующей "Глава N" — используется для НК (глава 25.3).
# Все ID сверены вручную 02.08.2026: заголовок страницы совпал с ожидаемым.
KODEKSY = [
    {"slug": "gk-rf", "title": "Гражданский кодекс Российской Федерации",
     "doc_ids": [5142, 9027, 34154, 64629],
     "part_labels": ["Часть первая", "Часть вторая", "Часть третья", "Часть четвертая"]},
    {"slug": "gpk-rf", "title": "Гражданский процессуальный кодекс Российской Федерации",
     "doc_ids": [39570]},
    {"slug": "sk-rf", "title": "Семейный кодекс Российской Федерации",
     "doc_ids": [8982]},
    {"slug": "kas-rf", "title": "Кодекс административного судопроизводства Российской Федерации",
     "doc_ids": [176147]},
    {"slug": "koap-rf", "title": "Кодекс Российской Федерации об административных правонарушениях",
     "doc_ids": [34661]},
    {"slug": "apk-rf", "title": "Арбитражный процессуальный кодекс Российской Федерации",
     "doc_ids": [37800]},
    {"slug": "nk-rf-gosposhlina", "title": "Налоговый кодекс Российской Федерации "
     "(глава 25.3 «Государственная пошлина»)",
     "doc_ids": [28165], "chapter_scope": "25.3"},
    # Не кодексы, но ходовые федеральные законы: треть живой практики по неустойке —
    # потребительские споры, а ЗоЗПП в реестре не было вовсе (аудит 03.08.2026).
    # doc_id сверены по <title> страницы, не угаданы.
    {"slug": "zozpp", "title": "Закон РФ «О защите прав потребителей» от 07.02.1992 N 2300-1",
     "doc_ids": [305]},
    {"slug": "fz-229-ispolnitelnoe", "title": "Федеральный закон «Об исполнительном производстве» "
     "от 02.10.2007 N 229-ФЗ", "doc_ids": [71450]},
    {"slug": "fz-127-bankrotstvo", "title": "Федеральный закон «О несостоятельности (банкротстве)» "
     "от 26.10.2002 N 127-ФЗ", "doc_ids": [39331]},
    {"slug": "tk-rf", "title": "Трудовой кодекс Российской Федерации",
     "doc_ids": [34683]},
    {"slug": "zhk-rf", "title": "Жилищный кодекс Российской Федерации",
     "doc_ids": [51057]},
    {"slug": "zk-rf", "title": "Земельный кодекс Российской Федерации",
     "doc_ids": [33773]},
    {"slug": "uk-rf", "title": "Уголовный кодекс Российской Федерации",
     "doc_ids": [10699]},
    {"slug": "upk-rf", "title": "Уголовно-процессуальный кодекс Российской Федерации",
     "doc_ids": [34481]},
    # Обращения в органы внутренних дел (розыск имущества, заявления о происшествиях):
    # без этого закона обязанность полиции принять и зарегистрировать обращение
    # приходилось обходить нормами УПК, а прямую норму — не цитировать вовсе
    # (дело 04.08.2026). doc_id сверен по <title> страницы, не угадан.
    {"slug": "fz-3-policiya", "title": "Федеральный закон «О полиции» от 07.02.2011 N 3-ФЗ",
     "doc_ids": [110165]},
    # Утилизационный сбор — ст. 24.1. Дело 10.08.2026: составители дважды сослались
    # на «абзац четвертый пункта 3 статьи 24.1», тогда как таможня во всех уведомлениях
    # пишет «абзац 3». Проверить номер абзаца было нечем — cite.py этот закон не знал,
    # и разночтение ушло в судебный документ. doc_id сверен по <title> страницы
    # 10.08.2026 («Об отходах производства и потребления» от 24.06.1998 N 89-ФЗ), не угадан.
    {"slug": "fz-89-othody", "title": "Федеральный закон «Об отходах производства "
     "и потребления» от 24.06.1998 N 89-ФЗ", "doc_ids": [19109]},
]

SUDACT_PLENUM_CATALOG = "https://sudact.ru/law/authority/plenum-vs-rf/"


# --------------------------------------------------------------------------
# HTTP с диск-кешем. Повторный вызов того же URL — 0 сетевых запросов.
#
# Через curl (subprocess), не urllib: на этой машине urllib.request роняет
# CERTIFICATE_VERIFY_FAILED (свой доверенный корень системной связки не
# подхватывается ssl-модулем Python), а curl те же адреса берет штатно —
# он использует системную связку доверия macOS напрямую. Тот же прием уже
# в проекте (scripts/fetch_url.sh, scripts/verify_act.py) — не новое решение.
# Кеш HTTP был бессрочным: один раз скачав страницу, скрипт больше НИКОГДА не шел
# к источнику, а `--check` при этом рапортовал «без изменений». Проверка актуальности
# по вечному кешу — не проверка. Держим срок жизни и режим обхода.
CACHE_MAX_AGE = 86400  # сутки: внутри одного прогона повторы дешевы, между прогонами — нет


HTTP_MARKER = "\n__HTTP__"          # curl -w дописывает код и тип в конец тела
# Ответ считается страницей корпуса, только если это HTML. JSON капчи, XML ошибки
# и «application/octet-stream» от сбойного прокси прежде уходили в парсер как текст
# закона: он ничего не находил, статья молча оставалась старой, а прогон
# рапортовал «чист».
OK_CONTENT_TYPES = ("text/html", "application/xhtml")


def split_http_tail(raw: bytes) -> tuple[bytes, str, str]:
    """(тело, код, content-type). Маркера нет — старое поведение, код пустой."""
    marker = HTTP_MARKER.encode()
    if marker not in raw:
        return raw, "", ""
    body, _, tail = raw.rpartition(marker)
    parts = tail.decode("ascii", "replace").strip().split("|", 1)
    return body, parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def http_response_bad(code: str, ctype: str) -> str:
    """Пусто — ответ годен. Иначе причина отказа человеческими словами."""
    if code and code.isdigit() and int(code) >= 400:
        return f"HTTP {code}"
    if ctype and not any(ctype.lower().startswith(t) for t in OK_CONTENT_TYPES):
        return f"тип ответа «{ctype}» — это не страница документа"
    return ""


def http_get(url: str, cache_key: str) -> bytes | None:
    """Страница источника. None — не получили; причина уходит в FAILURES.

    ЧТО БЫЛО СЛОМАНО (аудит 03.08.2026). Успехом считалось `returncode == 0 and
    len(stdout) > 200`. HTTP-код и content-type не читались вовсе, поэтому
    страница 404 в 12 КБ, капча и заглушка провайдера кешировались на сутки как
    текст закона. Мутация «curl → несуществующая команда» оставляла --demo
    зелёным: мёртвый канал давал строку на stderr, в FAILURES не попадал,
    report_failures() возвращал 0, а legal-corpus-monthly.sh писал в лог
    «обновлено 0, ошибок 0» и завершался нулём.
    """
    if pii_gate.residual_matches(url):
        FAILURES.append(f"{url}: URL похож на персональные данные — наружу не отправлен")
        return None
    cache_path = os.path.join(CACHE_DIR, cache_key)
    if (CACHE_MAX_AGE > 0 and os.path.exists(cache_path)
            and os.path.getsize(cache_path) > 200
            and time.time() - os.path.getmtime(cache_path) < CACHE_MAX_AGE):
        return open(cache_path, "rb").read()
    data = None
    why = "канал не ответил"
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["curl", "-sL", "--max-time", str(TIMEOUT), "-A", UA,
                 "-w", f"{HTTP_MARKER}%{{http_code}}|%{{content_type}}", url],
                capture_output=True, timeout=TIMEOUT + 5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            r, why = None, f"curl не выполнился ({type(e).__name__})"
        if r is not None and r.returncode == 0:
            body, code, ctype = split_http_tail(r.stdout)
            bad = http_response_bad(code, ctype)
            if bad:
                why = bad
            elif len(body) > 200:
                data = body
                break
            else:
                why = f"ответ короче 200 байт ({len(body)})"
        elif r is not None:
            why = f"curl вернул {r.returncode}"
        if attempt == 2:
            print(f"  ! не удалось получить {url}: {why}", file=sys.stderr)
            FAILURES.append(f"{url}: {why} — источник не прочитан, данные НЕ обновлены")
            return None
        time.sleep(1.5 * (attempt + 1))
    time.sleep(REQUEST_DELAY)
    if data:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(data)
    return data


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Что пошло не так за прогон. Пустой список = честный ноль на выходе; непустой —
# ненулевой код возврата, иначе launchd месяцами считает сломанный прогон удачным.
FAILURES: list[str] = []


def write_atomic(path: str, content: str) -> None:
    """Запись через временный файл в той же папке + os.replace.

    Прямой open(path,"w") усекает файл ДО того, как новое содержимое записано:
    сбой в этот момент уничтожает рабочий корпус безвозвратно."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Извлечение текста статьи с consultant.ru. Разметка проверена вручную на живой
# странице ГПК РФ ст. 131 (02.08.2026): весь блок статьи лежит внутри
# <div class="document-page__content ..."> — а НЕ внутри вложенного
# <div class="document__style doc-style">, как можно было бы ожидать: этот
# внутренний div у КонсультантПлюс закрывается сразу после <h1> (заголовка),
# и сам текст статьи (<p>...) идет уже ПОСЛЕ него как соседний узел того же
# document-page__content. Поэтому точка старта захвата — именно внешний
# document-page__content; "document-page__notes" (комментарии) на той же
# странице лежит СНАРУЖИ этого контейнера как сосед и потому в захват не
# попадает вообще — правило про notes ниже оставлено на случай, если верстка
# станет вложенной на другом типе страниц.
# Служебные блоки консультанта — НЕ часть текста нормы, вырезаем:
#   document__edit    — "(в ред. Федерального закона от ...)" — заметка
#   document__insert  — "(см. текст в предыдущей редакции)" + рекламные
#                        врезки "Перспективы и риски споров..."
#   notes             — "Комментарии к статье" (FAQ КонсультантПлюс)
#   full-text         — кнопка "Открыть полный текст документа" (демо-огрызок)
SKIP_CLASSES = {"document__edit", "document__insert", "notes", "full-text",
                 "document-page__notes"}
CONTENT_CLASS = "document-page__content"
FRAMED_TAGS = {"div", "p", "h1", "h2", "a", "ul", "li", "section"}


class ArticleHTMLParser(HTMLParser):
    """Тянет только текст статьи, без рекламы/сносок КонсультантПлюс.

    Стек кадров (tag, capturing, skip): capturing включается внутри div
    class=document__style (там лежит текст статьи), skip включается внутри
    h1 (дублирует заголовок) и внутри блоков SKIP_CLASSES. Оба флага
    наследуются вниз по дереву — вложенный тег не может "выключить" родительский
    skip. Абзацы разделяются по закрытию <p>, вложенные <a>-ссылки со словами
    (например, "статьей 133") остаются частью предложения.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [("root", False, False)]
        self.paragraphs = []
        self._buf = []

    @staticmethod
    def _classes(attrs):
        for k, v in attrs:
            if k == "class":
                return set((v or "").split())
        return set()

    def handle_starttag(self, tag, attrs):
        parent_cap, parent_skip = self.stack[-1][1], self.stack[-1][2]
        cap, skip = parent_cap, parent_skip
        if tag == "div":
            classes = self._classes(attrs)
            if CONTENT_CLASS in classes:
                cap = True
            if classes & SKIP_CLASSES:
                skip = True
        elif tag == "h1":
            skip = True
        if tag in FRAMED_TAGS:
            self.stack.append((tag, cap, skip))

    def handle_endtag(self, tag):
        if tag == "p" and self.stack[-1][1] and not self.stack[-1][2]:
            text = "".join(self._buf).strip()
            text = re.sub(r"[ \t]+", " ", text).replace("\xa0", " ")
            if text:
                self.paragraphs.append(text)
            self._buf = []
        if tag in FRAMED_TAGS and len(self.stack) > 1 and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data):
        cap, skip = self.stack[-1][1], self.stack[-1][2]
        if cap and not skip:
            self._buf.append(data)

    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


def extract_article_text(html_bytes: bytes) -> str:
    parser = ArticleHTMLParser()
    parser.feed(html_bytes.decode("utf-8", errors="ignore"))
    return parser.text()


# Хвост «, с изм. от ДД.ММ.ГГГГ» после даты редакции ломал прежний шаблон, который
# требовал закрывающую скобку сразу за датой. Из-за этого СК РФ получил в корпусе
# дату 29.12.1995 — день ПРИНЯТИЯ закона вместо редакции от 23.03.2026, и cite.py
# уверенно выдавал устаревшую дату юристу.
TITLE_RE = re.compile(
    r'"([^"]+?)"\s+от\s+(\d{2}\.\d{2}\.\d{4})\s+N\s+([^\s<(]+)'
    r'(?:\s*\(ред\.\s+от\s+(\d{2}\.\d{2}\.\d{4})[^)]*\))?', re.S)

# На странице оглавления некоторых кодексов (ГК) даты редакции нет вовсе — там
# стоит «(последняя редакция)». Она есть на странице ЛЮБОЙ статьи, оттуда и берем.
REDACTION_ONLY_RE = re.compile(r'\(ред\.\s+от\s+(\d{2}\.\d{2}\.\d{4})[^)]*\)')


def parse_doc_meta(html_text: str) -> dict:
    """Название, дата+номер закона, дата актуальной редакции — со страницы ToC."""
    m = TITLE_RE.search(html_text)
    if not m:
        return {}
    red = m.group(4)
    return {"title": m.group(1), "law_date": m.group(2), "law_num": m.group(3),
            "redaction_date": red or "?"}


def redaction_from_article(doc_id: int, art_hash: str) -> str | None:
    """Фолбэк: дата редакции со страницы конкретной статьи."""
    url = f"https://www.consultant.ru/document/cons_doc_LAW_{doc_id}/{art_hash}/"
    raw = http_get(url, f"red_{doc_id}_{art_hash[:12]}.html")
    if raw is None:
        return None
    m = REDACTION_ONLY_RE.search(raw.decode("utf-8", errors="ignore"))
    return m.group(1) if m else None


TOC_ENTRY_TMPL = r'href="/document/cons_doc_LAW_{doc_id}/([a-f0-9]+)/">([^<]+)</a>'
ARTICLE_RE = re.compile(r"^Статья\s+([\d.]+(?:-\d+)?)\.\s*(.*)$")


def fetch_toc(doc_id: int) -> tuple[dict, list[tuple[str, str, str]]]:
    """Возвращает (метаданные документа, [(kind, num_or_none, title, hash)])."""
    url = f"https://www.consultant.ru/document/cons_doc_LAW_{doc_id}/"
    raw = http_get(url, f"toc_{doc_id}.html")
    if raw is None:
        return {}, []
    text = raw.decode("utf-8", errors="ignore")
    meta = parse_doc_meta(text)
    meta["source_url"] = url
    entries = []
    seen = set()
    for m in re.finditer(TOC_ENTRY_TMPL.format(doc_id=doc_id), text):
        href_hash, label = m.group(1), html.unescape(m.group(2))
        key = (href_hash, label)
        if key in seen:
            continue
        seen.add(key)
        am = ARTICLE_RE.match(label)
        if am:
            entries.append(("article", am.group(1), am.group(2), href_hash))
        else:
            entries.append(("heading", None, label, href_hash))

    # ГК на оглавлении пишет «(последняя редакция)» без даты. Молча подставлять
    # дату принятия закона нельзя — это и породило «ред. 29.12.1995» у СК.
    if meta.get("redaction_date") in (None, "?", meta.get("law_date")):
        first_art = next((h for kind, _, _, h in entries if kind == "article"), None)
        if first_art:
            red = redaction_from_article(doc_id, first_art)
            if red:
                meta["redaction_date"] = red
    return meta, entries


CHAPTER_RE = re.compile(r"^Глава\s+([\d.]+)\b")


def scope_to_chapter(entries: list, chapter_num: str) -> list:
    """Оставляет только записи внутри 'Глава {chapter_num}' — для НК гл. 25.3."""
    out, inside = [], False
    for kind, num, title, h in entries:
        cm = CHAPTER_RE.match(title) if kind == "heading" else None
        if cm:
            inside = (cm.group(1) == chapter_num)
            if inside:
                out.append((kind, num, title, h))
            continue
        if inside:
            out.append((kind, num, title, h))
    return out


def build_kodeks_body(doc_id: int, entries: list, doc_id_str: str) -> tuple[str, int, int]:
    """Тянет статьи по одному, возвращает (markdown, ok_count, fail_count)."""
    lines, ok, fail = [], 0, 0
    for kind, num, title, h in entries:
        if kind == "heading":
            lines.append(f"\n## {title}\n")
            continue
        url = f"https://www.consultant.ru/document/cons_doc_LAW_{doc_id}/{h}/"
        cache_key = f"art_{doc_id_str}_{h}.html"
        raw = http_get(url, cache_key)
        if raw is None:
            fail += 1
            lines.append(f"\n### Статья {num}. {title}\n\n_не удалось получить со "
                          f"страницы {url} — требует ручной проверки._\n")
            continue
        # Страница адресуется хешем: сдвинулся хеш в оглавлении или ответил кеш
        # чужой страницы — и под заголовком «Статья N» ляжет чужой текст, а прогон
        # доложит «чист». Сверяем номер статьи с <h1> самой страницы.
        page = raw.decode("utf-8", errors="ignore")
        h1 = re.search(r"<h1>(.*?)</h1>", page, re.S)
        if h1:
            # Номер без «хвостовой» точки: «Статья 1.» дает 1, «Статья 333.19.» — 333.19
            got = re.search(r"Стать[яи]\s+(\d+(?:\.\d+)*(?:-\d+)?)",
                            re.sub(r"<[^>]+>", " ", h1.group(1)))
            if got and got.group(1) != num.rstrip("."):
                fail += 1
                lines.append(f"\n### Статья {num}. {title}\n\n_НЕ СОВПАЛ НОМЕР: страница "
                             f"{url} озаглавлена «Статья {got.group(1)}». Текст не внесен, "
                             f"требует ручной проверки._\n")
                continue
        body = extract_article_text(raw)
        if not body:
            fail += 1
            lines.append(f"\n### Статья {num}. {title}\n\n_текст не распознан на "
                          f"странице {url} — требует ручной проверки._\n")
            continue
        ok += 1
        lines.append(f"\n### Статья {num}. {title}\n\n{body}\n")
    return "".join(lines), ok, fail


def frontmatter(meta: dict, body_sha: str, extra: dict | None = None) -> str:
    fm = {
        "источник": meta.get("source_url", ""),
        "закон": (f"от {meta['law_date']} N {meta.get('law_num') or '?'}"
                   if meta.get("law_date") else ""),
        "дата_редакции": meta.get("redaction_date", "?"),
        "дата_выгрузки": TODAY,
        "sha256": body_sha,
    }
    if extra:
        fm.update(extra)
    lines = ["---"]
    for k, v in fm.items():
        if v == "" or v is None:
            continue
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: \"{v}\"")
    lines.append("---\n")
    return "\n".join(lines)


def archive_old(path: str) -> None:
    if not os.path.exists(path):
        return
    day_dir = os.path.join(ARCHIVE_DIR, TODAY.replace(".", "-"))
    os.makedirs(day_dir, exist_ok=True)
    dest = os.path.join(day_dir, os.path.basename(path))
    if os.path.exists(dest):
        return  # уже заархивировано сегодня — не плодить копии
    with open(path, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())


def read_existing_sha(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    m = re.search(r'^sha256:\s*"([a-f0-9]+)"', text, re.M)
    return m.group(1) if m else None


def read_frontmatter_dates(path: str) -> list[str] | None:
    """Даты редакции как список — 1 элемент для обычного кодекса, N для
    многочастного (ГК): читает даты_частей (список), не составную строку
    дата_редакции (та — только для человека, для сверки не годится)."""
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    m = re.search(r"^даты_частей:\n((?:  - .*\n)+)", text, re.M)
    if m:
        return [line[4:].strip() for line in m.group(1).splitlines()]
    m = re.search(r'^дата_редакции:\s*"([^"]+)"', text, re.M)
    return [m.group(1)] if m else None


def append_log(line: str) -> None:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {stamp} — {line}\n")


# --------------------------------------------------------------------------
def cmd_check_one(entry: dict) -> str:
    metas = []
    for doc_id in entry["doc_ids"]:
        meta, _ = fetch_toc(doc_id)
        if not meta:
            return f"{entry['slug']}: КАНАЛ НЕДОСТУПЕН (doc {doc_id})"
        metas.append(meta)
    path = os.path.join(KODEKSY_DIR, f"{entry['slug']}.md")
    if not os.path.exists(path):
        return f"{entry['slug']}: НЕ ВЫГРУЖЕН (--init для загрузки)"
    old_dates = read_frontmatter_dates(path)
    new_dates = [m.get("redaction_date", "?") for m in metas]
    if old_dates == new_dates:
        return f"{entry['slug']}: без изменений (ред. {', '.join(new_dates)})"
    return f"{entry['slug']}: ИЗМЕНИЛОСЬ ред. {old_dates} -> {new_dates} — нужен --update --doc {entry['slug']}"


def cmd_build_one(entry: dict, mode: str) -> None:
    slug = entry["slug"]
    print(f"=== {slug}: {entry['title']} ===")
    path = os.path.join(KODEKSY_DIR, f"{slug}.md")
    parts_md, metas = [], []
    total_ok, total_fail = 0, 0
    labels = entry.get("part_labels", [None] * len(entry["doc_ids"]))
    for doc_id, label in zip(entry["doc_ids"], labels):
        meta, entries = fetch_toc(doc_id)
        if not meta:
            print(f"  ! doc {doc_id}: канал недоступен, пропуск")
            continue
        if entry.get("chapter_scope"):
            entries = scope_to_chapter(entries, entry["chapter_scope"])
        print(f"  doc {doc_id}: {len(entries)} записей ToC "
              f"(ред. {meta.get('redaction_date','?')})")
        body, ok, fail = build_kodeks_body(doc_id, entries, str(doc_id))
        total_ok += ok
        total_fail += fail
        if label:
            parts_md.append(f"\n# {label}\n{body}")
        else:
            parts_md.append(body)
        metas.append(meta)
    if not metas:
        print(f"  ! {slug}: ни один источник не ответил, файл не пишем")
        return
    full_body = f"# {entry['title']}\n" + "".join(parts_md)
    body_sha = sha256_text(full_body)
    fm_extra = {}
    if len(metas) > 1:
        fm_extra["дата_редакции"] = "; ".join(
            f"{l}: {m.get('redaction_date','?')}" for l, m in zip(labels, metas))
        # даты_частей — машиночитаемый список для --check (дата_редакции выше
        # только для человека, склеенную строку с ней не сверить надежно).
        fm_extra["даты_частей"] = [m.get("redaction_date", "?") for m in metas]
        fm_extra["источник"] = [m.get("source_url", "") for m in metas]
    content = frontmatter(metas[0], body_sha, fm_extra if fm_extra else None) + full_body
    old_sha = read_existing_sha(path)
    changed = old_sha != body_sha
    if mode == "update" and changed and old_sha is not None:
        archive_old(path)
        print(f"  редакция изменилась — старая версия в {ARCHIVE_DIR}/{TODAY.replace('.', '-')}/")
    os.makedirs(KODEKSY_DIR, exist_ok=True)
    # Частичный сбой не должен затирать рабочий корпус: одна сетевая ошибка при
    # прежней прямой записи оставляла на диске обрезанный кодекс вместо целого.
    if total_fail:
        # Условие «только поверх существующего файла» оставляло дыру: ПЕРВЫЙ --init
        # при обрывах писал дырявый корпус и возвращал 0. Дырявый корпус хуже пустого:
        # cite.py отдаст «статья не найдена» там, где она есть.
        where = "рабочий файл НЕ трогаем" if old_sha is not None else "файл НЕ создаем"
        print(f"  ! {slug}: {total_fail} статей не скачалось — {where}. "
              f"Повторить: --update --doc {slug}")
        FAILURES.append(f"{slug}: {total_fail} статей не скачалось, файл не обновлен")
        return
    write_atomic(path, content)
    status = "новый" if old_sha is None else ("обновлен" if changed else "без изменений")
    print(f"  -> {path} ({status}, статей ok={total_ok} fail={total_fail})")
    append_log(f"кодекс {slug}: {status}, статей ok={total_ok} fail={total_fail}, "
               f"ред. {metas[0].get('redaction_date','?')}")


# --------------------------------------------------------------------------
# Пленумы ВС РФ — sudact.ru (см. обоснование источника в шапке файла).
PLENUM_LINK_RE = re.compile(r'href="(/law/postanovlenie-plenuma[^"]*)"[^>]*>([^<]+)')
PLENUM_TITLE_RE = re.compile(
    r"Постановление Пленума Верховного Суда РФ от (\d{2}\.\d{2}\.\d{4})"
    r"(?:\s*N\s*(\S+))?\s*(?:\(ред\. от (\d{2}\.\d{2}\.\d{4})\))?\s*(.*)")


def discover_plenum_catalog(pages: int) -> list[tuple[str, str]]:
    """[(slug_url, link_text)] по первым `pages` страницам каталога sudact.ru."""
    found, seen = [], set()
    for page in range(1, pages + 1):
        url = SUDACT_PLENUM_CATALOG if page == 1 else f"{SUDACT_PLENUM_CATALOG}?page={page}"
        raw = http_get(url, f"plenum_catalog_p{page}.html")
        if raw is None:
            break
        text = raw.decode("utf-8", errors="ignore")
        hits = PLENUM_LINK_RE.findall(text)
        new = 0
        for href, label in hits:
            if href in seen:
                continue
            seen.add(href)
            found.append((href, label))
            new += 1
        print(f"  каталог стр. {page}: {new} новых документов")
        if new == 0:
            break
    return found


def plenum_slug_to_filename(href: str) -> str:
    slug = href.strip("/").split("/")[-1]
    slug = slug.replace("postanovlenie-plenuma-verkhovnogo-suda-rf-", "")
    return f"plenum-{slug}.md"


PUNKT_SPLIT_RE = re.compile(r"\n(\d{1,3}(?:\.\d{1,2})?)\.\s{1,4}(?=[А-ЯЁ(])")


def split_into_punkty(text: str) -> list[tuple[str, str]]:
    """Делит текст постановления на пункты после слова 'постановляет:'.

    Пленум нумерует резолютивную часть простыми '1.', '2.', ... на новой
    строке — проверено на живом тексте (Постановление N 19 от 27.09.2012).
    Преамбула (до 'постановляет') нумерации не имеет и в разбивку не попадает.

    ПОТЕРЯ ТЕКСТА, найденная аудитом 03.08.2026. Цикл шёл `range(1, len(parts), 2)`
    и не забирал `parts[0]` — всё, что стоит МЕЖДУ «постановляет:» и первым
    нумерованным пунктом. Там же оказывался весь текст постановлений, где слова
    «постановляет» нет вовсе (`head` пуст, `rest` — документ целиком). Независимый
    пересчёт 325 кешированных страниц: потеряно 230 552 из 8 997 326 знаков (2,56 %),
    худшие файлы — 74,0 % и 72,6 %. Потерянный кусок — не служебный: там преамбула
    с предметом постановления и правовым основанием, ради которых Пленум и цитируют.
    """
    m = re.search(r"постановля(?:ет|ют)\s*:", text, re.I)
    head = text[: m.end()] if m else ""
    rest = text[m.end():] if m else text
    parts = PUNKT_SPLIT_RE.split(rest)
    if len(parts) < 3:
        return [(None, text)]
    out = [(None, head)] if head.strip() else []
    if parts[0].strip():
        out.append((None, parts[0].strip()))
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1].strip()))
    return out


def fetch_plenum_html(href: str) -> tuple[str, bytes] | None:
    url = f"https://sudact.ru{href}"
    slug = href.strip("/").split("/")[-1]
    raw = http_get(url, f"plenum_{slug}.html")
    if raw is None:
        return None
    return url, raw


class PlenumTextParser(HTMLParser):
    """Текст постановления на sudact.ru лежит в <div id="lawchunkbody_<rand>">
    (проверено вручную на Постановлении N 35 от 13.12.2012, 02.08.2026) —
    id со случайным суффиксом на каждый документ, ловим по префиксу
    "lawchunkbody_", а не по фиксированному id. Документ может состоять из
    нескольких таких чанков подряд (капчур включается заново на каждом).
    Внутри чанка попадаются рекламные врезки AdFox (div class="adv_inside_text",
    содержит <script>) — вырезаем per div-class, и глобально пропускаем
    <script>/<style> в любом месте, иначе их код попадет в текст как данные.
    Абзацы — по закрытию <p>, как в ArticleHTMLParser.
    """

    CONTENT_ID_PREFIX = "lawchunkbody_"
    SKIP_TAGS = {"script", "style"}
    SKIP_CLASSES = {"adv_inside_text"}
    # Кадрируем ТОЛЬКО теги, у которых в этой верстке гарантированно есть
    # закрывающая пара (div/p всегда, script/style — обязательны по спецификации
    # HTML). Прочие теги (hr, br, input, img, a, pre, table...) в этой странице
    # встречаются НЕ всегда закрытыми (напр. `<hr class="hr-h1">` без `</hr>`,
    # `<input ...>` в сайдбаре без `</input>`) — если их тоже кадрировать,
    # непарный open навсегда застревает в стеке и портит все дальнейшее
    # определение глубины. Не кадрированные теги просто наследуют состояние
    # ближайшего кадрированного предка — для инлайновых <a> внутри абзаца
    # это ровно то поведение, что нужно.
    FRAMED_TAGS = {"div", "p", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [("root", False, False)]
        self.paragraphs = []
        self._buf = []

    @staticmethod
    def _attr(attrs, name):
        for k, v in attrs:
            if k == name:
                return v or ""
        return ""

    def handle_starttag(self, tag, attrs):
        parent_cap, parent_skip = self.stack[-1][1], self.stack[-1][2]
        cap, skip = parent_cap, parent_skip
        if tag in self.SKIP_TAGS:
            skip = True
        elif tag == "div":
            if self._attr(attrs, "id").startswith(self.CONTENT_ID_PREFIX):
                cap = True
            if set(self._attr(attrs, "class").split()) & self.SKIP_CLASSES:
                skip = True
        if tag in self.FRAMED_TAGS:
            self.stack.append((tag, cap, skip))

    def handle_endtag(self, tag):
        if tag == "p" and self.stack[-1][1] and not self.stack[-1][2]:
            text = "".join(self._buf).strip()
            text = re.sub(r"[ \t]+", " ", text).replace("\xa0", " ")
            if text:
                self.paragraphs.append(text)
            self._buf = []
        if tag in self.FRAMED_TAGS and len(self.stack) > 1 and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data):
        cap, skip = self.stack[-1][1], self.stack[-1][2]
        if cap and not skip:
            self._buf.append(data)

    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


def build_plenum_markdown(href: str, label: str) -> tuple[str, str] | None:
    got = fetch_plenum_html(href)
    if got is None:
        return None
    url, raw = got
    html_text = raw.decode("utf-8", errors="ignore")
    parser = PlenumTextParser()
    parser.feed(html_text)
    body = parser.text()
    if len(body) < 200:
        return None  # верстка не совпала или страница пустая — не подставлять огрызок
    # <h1> страницы всегда несет "от ДД.ММ.ГГГГ N X (ред. от ДД.ММ.ГГГГ) Название"
    # целиком (проверено на живом документе) — надежнее текста ссылки каталога.
    h1m = re.search(r"<h1>([^<]+)</h1>", html_text)
    h1_title = html.unescape(h1m.group(1)) if h1m else None
    tm = (PLENUM_TITLE_RE.search(h1_title) if h1_title else None) \
        or PLENUM_TITLE_RE.search(label) or PLENUM_TITLE_RE.search(body[:300])
    if tm:
        law_date, num, red_date, subj = tm.groups()
    else:
        # Не разобрали основным шаблоном — обычно совместное постановление
        # Пленума ВС РФ и Пленума ВАС РФ старого образца ("Пленума ВС РФ N 10,
        # Пленума ВАС РФ N 22 от ... (ред. от ...) Название"): другой порядок
        # номер/дата и два номера сразу. Дата и первый номер вытаскиваются
        # отдельным проходом — без этого cite.py не найдет пункт по дате.
        # Не нашли и так — честное "?", а не выдуманная дата.
        src = h1_title or label or ""
        # Постановления Пленума ВС РСФСР 1990-1991 гг. заголовок пишут иначе, и дата
        # у них есть только в slug (…-ot-23041991). Слепить ДДММГГГГ из slug — это
        # чтение источника, а не догадка; без этого файл уходил в корпус с датой «?»
        # и cite.py не находил его по дате никогда.
        if "от" not in src or not re.search(r"\d{2}\.\d{2}\.\d{4}", src):
            sm = re.search(r"-ot-(\d{2})(\d{2})(\d{4})", href)
            if sm:
                src = f"{src} от {sm.group(1)}.{sm.group(2)}.{sm.group(3)}"
        dm = re.search(r"от\s+(\d{2}\.\d{2}\.\d{4})", src)
        nm = re.search(r"N\s*(\S+)", src)
        redm = re.search(r"\(ред\.\s+от\s+(\d{2}\.\d{2}\.\d{4})\)", src)
        law_date = dm.group(1) if dm else "?"
        num = nm.group(1).rstrip(",") if nm else None
        red_date = redm.group(1) if redm else (law_date if dm else None)
        subj = src or "?"
    punkty = split_into_punkty(body)
    lines = [f"# Постановление Пленума Верховного Суда РФ от {law_date}"
             + (f" N {num}" if num else "") + f"\n\n{subj.strip()}\n"]
    for pnum, ptext in punkty:
        if pnum:
            lines.append(f"\n### п. {pnum}\n\n{ptext}\n")
        else:
            lines.append(f"\n{ptext}\n")
    body_md = "".join(lines)
    meta = {"source_url": url, "law_date": law_date, "law_num": num or "?",
            "redaction_date": red_date or law_date}
    body_sha = sha256_text(body_md)
    content = frontmatter(meta, body_sha) + body_md
    return plenum_slug_to_filename(href), content


def cmd_plenums(pages: int, single_slug: str | None) -> None:
    os.makedirs(PLENUMY_DIR, exist_ok=True)
    if single_slug:
        href = single_slug if single_slug.startswith("/") else f"/law/{single_slug}/"
        targets = [(href, "")]
    else:
        targets = discover_plenum_catalog(pages)
    ok, fail = 0, 0
    for href, label in targets:
        result = build_plenum_markdown(href, label)
        if result is None:
            fail += 1
            print(f"  ! пропуск (не удалось разобрать): {href}")
            continue
        filename, content = result
        path = os.path.join(PLENUMY_DIR, filename)
        old_sha = read_existing_sha(path)
        new_sha = re.search(r'^sha256:\s*"([a-f0-9]+)"', content, re.M).group(1)
        if old_sha == new_sha:
            ok += 1
            continue
        if old_sha is not None:
            archive_old(path)
        write_atomic(path, content)
        ok += 1
        print(f"  -> {path}")
    print(f"=== Пленумы: ok={ok} fail={fail} ===")
    append_log(f"пленумы ВС РФ: обработано ok={ok} fail={fail} (каталог, {pages} стр.)")


# --------------------------------------------------------------------------
def demo() -> None:
    """Самопроверка парсеров без сети."""
    sample = (
        '<div role="main" class="content document-page">'
        '<div class="document-page__content document-page_left-padding">'
        '<div class="document__style doc-style" data-style-id="3">'
        '<h1><p>ГПК РФ Статья 131. Форма и содержание искового заявления</p></h1></div>'
        '<div class="document__insert doc-insert doc-insert_roll">'
        '<div class="doc-roll__content"><p>Реклама КонсультантПлюс</p></div></div>'
        '<p>1. Исковое заявление подается в суд на бумажном носителе.</p>'
        '<div class="document__edit doc-edit"><p>(в ред. ФЗ от 30.12.2021 N 440-ФЗ)</p></div>'
        '</div>'
        '<div class="document-page__notes"><div class="notes">'
        '<div class="notes__title">Комментарии к статье</div></div></div>'
        '</div>')
    text = extract_article_text(sample.encode("utf-8"))
    assert "Исковое заявление подается в суд" in text, text
    assert "Реклама" not in text, "реклама КонсультантПлюс не должна попасть в текст"
    assert "в ред. ФЗ" not in text, "сноска-правка не должна попасть в текст статьи"
    assert "Комментарии к статье" not in text, "FAQ-комментарии не должны попасть в текст"
    assert "ГПК РФ Статья 131" not in text, "дублирующий h1 не должен попасть в текст"

    meta = parse_doc_meta(
        '<a href=\'/document/cons_doc_LAW_39570/\'>"Гражданский процессуальный '
        'кодекс Российской Федерации" от 14.11.2002 N 138-ФЗ\n(ред. от 04.07.2026)</a>')
    assert meta["law_date"] == "14.11.2002" and meta["redaction_date"] == "04.07.2026", meta

    entries = [("heading", None, "Глава 25.2. Транспортный налог", "h0"),
               ("article", "356", "Общие положения", "h1"),
               ("heading", None, "Глава 25.3. Государственная пошлина", "h2"),
               ("article", "333.16", "Государственная пошлина", "h3"),
               ("article", "333.17", "Плательщики", "h4"),
               ("heading", None, "Глава 26. Налог на добычу", "h5"),
               ("article", "334", "Понятие", "h6")]
    scoped = scope_to_chapter(entries, "25.3")
    assert [e[3] for e in scoped] == ["h2", "h3", "h4"], scoped

    plenum_sample = (
        "Обеспечение защиты личности... Пленум постановляет:\n\n"
        "1.  Обратить внимание судов на то, что положения статьи 37.\n\n"
        "2.  В части 1 статьи 37 общественно опасное посягательство.\n")
    punkty = split_into_punkty(plenum_sample)
    nums = [p[0] for p in punkty if p[0]]
    assert nums == ["1", "2"], punkty

    # НИ ОДИН ЗНАК НЕ ТЕРЯЕТСЯ. Прежний цикл начинался с parts[1], и текст между
    # «постановляет:» и первым пунктом исчезал молча — 2,56 % корпуса, до 74 %
    # в худших файлах. Сверяем побуквенно, а не «пункты нашлись».
    # Считаем БУКВЫ: номер пункта и точка после него — разметка, они
    # переизлучаются заголовком «### п. N». Пропажа букв — это пропажа текста.
    def _letters(src: str) -> int:
        return len(re.sub(r"[^\w]|\d", "", src, flags=re.UNICODE))

    def _kept(src: str) -> int:
        return sum(_letters(body) for _, body in split_into_punkty(src))

    def _all(src: str) -> int:
        return _letters(src)

    between = ("Пленум Верховного Суда постановляет:\n\n"
               "В связи с вопросами судов о применении законодательства "
               "и в целях единства практики дать следующие разъяснения.\n\n"
               "1.  Обратить внимание судов на положения статьи 37.\n\n"
               "2.  В части 1 статьи 37 общественно опасное посягательство.\n")
    assert _kept(between) == _all(between), (
        f"текст между «постановляет» и пунктом 1 теряется: "
        f"{_all(between) - _kept(between)} знаков")
    assert any("единства практики" in b for _, b in split_into_punkty(between))

    # Постановление без слова «постановляет» — head пуст, и раньше пропадала
    # ВСЯ преамбула целиком (те самые файлы с потерей 74 %).
    no_verb = ("В целях обеспечения единообразного применения судами "
               "законодательства о возмещении издержек Пленум разъясняет.\n\n"
               "1.  Принципом распределения судебных расходов выступает возмещение.\n\n"
               "2.  Перечень судебных издержек не является исчерпывающим.\n")
    assert _kept(no_verb) == _all(no_verb), (
        f"преамбула без слова «постановляет» теряется: "
        f"{_all(no_verb) - _kept(no_verb)} знаков")

    # HTTP-код и content-type. Без них страница 404 в 12 КБ и капча ложились в
    # кеш на сутки как текст закона, а прогон рапортовал «чист».
    body = "<html>тело</html>".encode("utf-8")
    assert split_http_tail(body + b"\n__HTTP__404|text/html") == (body, "404", "text/html")
    assert split_http_tail(body) == (body, "", "")
    assert http_response_bad("404", "text/html") == "HTTP 404"
    assert http_response_bad("500", "text/html").startswith("HTTP 500")
    assert http_response_bad("200", "text/html; charset=utf-8") == ""
    assert http_response_bad("200", "application/json") != "", "капча приходит JSON'ом"
    assert http_response_bad("200", "application/octet-stream") != ""
    assert http_response_bad("", "") == "", "ответ без маркера не ломать"

    # Мёртвый канал обязан попадать в FAILURES, а не только на stderr: иначе
    # report_failures() вернёт 0 и месячный прогон завершится «успешно».
    before = len(FAILURES)
    real_run, real_cache = subprocess.run, CACHE_MAX_AGE
    try:
        globals()["CACHE_MAX_AGE"] = 0

        def dead(*a, **kw):
            raise FileNotFoundError("curl: команды нет")

        subprocess.run = dead
        assert http_get("https://example.invalid/x", "demo_dead.html") is None
    finally:
        subprocess.run, globals()["CACHE_MAX_AGE"] = real_run, real_cache
    assert len(FAILURES) == before + 1, "мёртвый канал не попал в FAILURES"
    assert report_failures() != 0, "изъян есть — код возврата обязан быть ненулевым"
    del FAILURES[before:]

    print("demo: извлечение статьи, метаданные, фильтр главы, разбивка пунктов, "
          "проверка ответа и учёт провалов — корректны")


def report_failures() -> int:
    """Ненулевой код при любом изъяне: launchd и вызывающий скрипт обязаны это видеть."""
    if not FAILURES:
        print("\nпрогон чист: провалов и неизвестных редакций нет")
        return 0
    print(f"\n⚠ изъянов: {len(FAILURES)}")
    for f in FAILURES:
        print(f"   • {f}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--doc", metavar="SLUG", help="ограничиться одним кодексом")
    ap.add_argument("--plenums", action="store_true")
    # Было 3 при каталоге в 15+ страниц: глубина обхода зависела от того, вспомнит ли
    # оператор про флаг. Цикл и так останавливается на первой странице без новых
    # ссылок, поэтому потолок ставим заведомо выше каталога, а не «на глазок».
    ap.add_argument("--plenum-pages", type=int, default=40,
                    help="потолок страниц каталога; обход и так встает на первой пустой")
    ap.add_argument("--plenum-slug", metavar="SLUG")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="игнорировать HTTP-кеш (обязательно для честного --check)")
    ap.add_argument("--selftest", action="store_true", help="то же, что --demo")
    a = ap.parse_args()

    global CACHE_MAX_AGE
    if a.refresh:
        CACHE_MAX_AGE = 0
    if a.check and not a.refresh:
        print("note: --check идет по кешу не старше суток; для сверки с источником "
              "добавьте --refresh", file=sys.stderr)

    if a.demo or a.selftest:
        demo()
        return 0

    os.makedirs(CACHE_DIR, exist_ok=True)

    if a.plenums or a.plenum_slug:
        cmd_plenums(a.plenum_pages, a.plenum_slug)
        return report_failures()

    targets = [e for e in KODEKSY if not a.doc or e["slug"] == a.doc]
    if a.doc and not targets:
        print(f"неизвестный slug: {a.doc}. Доступные: {', '.join(e['slug'] for e in KODEKSY)}",
              file=sys.stderr)
        return 1

    if a.check:
        unknown = 0
        for e in targets:
            line = cmd_check_one(e)
            print(line)
            if "?" in line:
                unknown += 1
                FAILURES.append(f"{e['slug']}: дата редакции неизвестна — сверка невозможна")
            else:
                # Дата старше трех лет у живого кодекса — почти всегда дата принятия,
                # подставленная разбором вслепую (sk-rf: 29.12.1995 — день принятия СК).
                m = re.search(r"ред\. (\d{2})\.(\d{2})\.(\d{4})", line)
                if m:
                    from datetime import date as _d
                    try:
                        got = _d(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                        if (_d.today() - got).days > 3 * 365:
                            FAILURES.append(
                                f"{e['slug']}: редакция от {m.group(0)[5:]} старше трех лет — "
                                "вероятно, разобрана дата принятия, а не редакции")
                    except ValueError:
                        pass
        missing = [e["slug"] for e in targets
                   if not os.path.exists(os.path.join(KODEKSY_DIR, e["slug"] + ".md"))]
        for slug in missing:
            FAILURES.append(f"{slug}: файла нет на диске, кодекс не выгружен")
        return report_failures()

    mode = "update" if a.update else "init"
    if not (a.init or a.update):
        print("укажите режим: --init | --update | --check | --plenums | --demo", file=sys.stderr)
        return 1
    for e in targets:
        cmd_build_one(e, mode)
    return report_failures()


if __name__ == "__main__":
    sys.exit(main())
