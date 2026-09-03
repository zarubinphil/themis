#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""case_graph.py — граф ОДНОГО дела: оглавление к карте, а не второй держатель правды.

ЗАЧЕМ. Карта дела `knowledge-map.md` перечитывается целиком на каждом шаге после
картирования: шаг 4 (doc-drafter), шаг 5 (три линзы рецензии), совет (пять ролей),
подготовка к заседанию — и в каждой новой сессии заново. Замер конституции: 55,7%
счета уходит на повторную доставку прочитанного. Граф отвечает на узкий вопрос
якорями (десятки токенов) вместо повторной заливки карты (десятки тысяч).

ЧЕМ ЭТО НЕ ВТОРОЙ ДЕРЖАТЕЛЬ ПРАВДЫ (задача M02 была красной ровно за это).
  • Узел — ЯКОРЬ на строку карты, а не утверждение. Несет source_file,
    source_location (раздел и строка), src_sha и as_of (дата из шапки карты).
  • Ребро строится ТОЛЬКО из одной строки карты: строка и есть утверждение.
    Сшивать узлы из разных строк по совпадению имен — это догадка, а догадка в
    деле стоит дороже пропуска. Поэтому confidence у всех ребер EXTRACTED, а
    INFERRED в графе дела не бывает — проверяется селфтестом.
  • Граф не цитируется: потребитель получает якорь, идет в карту и берет
    формулировку оттуда. Ребро графа нельзя предъявить суду, предъявляют документ.

ЧЕГО ОН НЕ ДЕЛАЕТ. Не строится LLM: карта уже структурирована таблицами, платить
модели за повторное извлечение — тот же класс ошибки, что сжег $1198 на мертвом
источнике 01.09.2026. Ноль токенов, доли секунды.

ОТКАЗ ВМЕСТО ТОНКОГО ГРАФА. Замер 03.09.2026 по 68 живым картам: маркер
«## КАРТА ГОТОВА ✓» несут 47, семь и более нумерованных разделов контракта — 40.
Парсер, достраивающий что получилось, отдал бы на четверти дел куцый граф, и ему
бы верили. Поэтому нет маркера или нет разделов 1-4 — сборка ОТКАЗЫВАЕТ и
называет, чего не хватает.

    case_graph.py build   cases/{клиент}/{дело}
    case_graph.py ask     cases/{клиент}/{дело} "вопрос"   свежесть сверяется сама
    case_graph.py check   cases/{клиент}/{дело}            свеж ли граф
    case_graph.py verify  cases/{клиент}/{дело}            ведет ли каждый якорь в свою строку
    case_graph.py defects cases/{клиент}/{дело}            структурные дефекты карты
    case_graph.py --selftest

Код возврата: 0 — успех; 1 — отказ (карта не по контракту, граф устарел, дефекты);
2 — ошибка вызова.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata

GRAPH_DIR = os.path.join(".agent", "graph")
MAP_NAME = "knowledge-map.md"
MARKER = "## КАРТА ГОТОВА ✓"
# Разделы 1-4 — жесткий контракт case-mapper («не переименовывать»). Без них
# графа дела не существует: не из чего строить стороны, хронологию, требования и
# доказательства. Разделы 5-9 необязательны, их отсутствие идет в отчет сборки.
REQUIRED = (1, 2, 3, 4)
SECTIONS = {
    1: ("party", "Стороны"),
    2: ("event", "Хронология"),
    3: ("claim", "Предмет"),
    4: ("fact", "Ключевые факты"),
    5: ("object", "Финансовые"),
    6: ("document", "Инвентарь"),
    7: ("norm", "Правовые вопросы"),
    8: ("disputed", "Оспоренные"),
    9: ("gap", "Что неизвестно"),
}
# Доля пишется дробью: «1/2», «3/4». Правило .claude/CLAUDE.md:77 требует долю
# ПО КАЖДОМУ ОБЪЕКТУ, поэтому доля живет на ребре party→object, а не на стороне:
# в такой схеме единая доля «на все» физически непредставима.
SHARE_RE = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")
NORM_RE = re.compile(r"\bст(?:атья|\.)\s*\d+[\d.\-]*", re.I)
SOURCES = (MAP_NAME, "practice.md", "positions.md")


def sha(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def ctx_dir(case: str) -> str:
    return os.path.join(case, ".agent", "context")


def out_dir(case: str) -> str:
    return os.path.join(case, GRAPH_DIR)


# ── Разбор карты ──────────────────────────────────────────────────────────────

def split_sections(text: str) -> dict[int, tuple[str, int]]:
    """{номер раздела: (тело, номер строки заголовка)}. Только нумерованные «## N.»."""
    out: dict[int, tuple[str, int]] = {}
    heads = [(m.start(), int(m.group(1)), text[:m.start()].count("\n") + 1)
             for m in re.finditer(r"^##\s*([1-9])\.\s", text, re.M)]
    for i, (pos, num, line) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out[num] = (text[pos:end], line)
    return out


def rows_of_tables(body: str, first_line: int) -> list[tuple[list[str], list[str], int]]:
    """Строки markdown-таблиц: (заголовки, ячейки, номер строки в файле)."""
    out = []
    headers: list[str] = []
    for offset, raw in enumerate(body.splitlines()):
        line = raw.strip()
        if not line.startswith("|"):
            headers = []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells) and cells:
            continue                      # разделитель шапки
        if not headers:
            headers = [c.lower() for c in cells]
            continue
        out.append((headers, cells, first_line + offset))
    return out


def col(headers: list[str], *words: str) -> int:
    for i, h in enumerate(headers):
        if any(w in h for w in words):
            return i
    return -1


def cell(cells: list[str], idx: int) -> str:
    return norm(cells[idx]) if 0 <= idx < len(cells) else ""


class Builder:
    """Собирает узлы и ребра. Ребро — только из ОДНОЙ строки карты."""

    def __init__(self, map_path: str, src_sha: str, as_of: str):
        self.nodes: dict[str, dict] = {}
        self.links: list[dict] = []
        self.map_path = map_path
        self.src_sha = src_sha
        self.as_of = as_of
        self.unparsed: list[str] = []

    def node(self, kind: str, label: str, line: int, **attrs) -> str:
        label = norm(label)
        if not label or label in {"—", "-", "–"}:
            return ""
        nid = f"{kind}:{label}"
        if nid not in self.nodes:
            self.nodes[nid] = {
                "id": nid, "label": label, "kind": kind, "file_type": "case",
                "source_file": self.map_path, "source_location": f"строка {line}",
                "src_sha": self.src_sha, "as_of": self.as_of, **attrs}
        return nid

    def link(self, src: str, dst: str, relation: str, line: int, **attrs) -> None:
        if not src or not dst or src == dst:
            return
        self.links.append({
            "source": src, "target": dst, "relation": relation,
            # EXTRACTED и только он: ребро прочитано из строки, а не выведено.
            "confidence": "EXTRACTED", "source_file": self.map_path,
            "source_location": f"строка {line}", **attrs})


def build_graph(case: str) -> tuple[dict, list[str]]:
    """(граф, отказы). Непустой список отказов означает, что графа нет."""
    map_path = os.path.join(ctx_dir(case), MAP_NAME)
    if not os.path.isfile(map_path):
        return {}, [f"нет карты {map_path}"]
    text = open(map_path, encoding="utf-8", errors="replace").read()
    refusals = []
    if MARKER not in text:
        refusals.append(f"в карте нет маркера «{MARKER}» — шаг 1 не закрыт, "
                        "строить граф не из чего")
    secs = split_sections(text)
    missing = [n for n in REQUIRED if n not in secs]
    if missing:
        refusals.append("нет обязательных разделов карты: "
                        + ", ".join(f"{n}. {SECTIONS[n][1]}" for n in missing))
    if refusals:
        return {}, refusals

    m = re.search(r"Обновлено:\s*([0-9.]{8,10})", text)
    rel = os.path.relpath(map_path, case)
    b = Builder(rel, sha(map_path), m.group(1) if m else "")

    for num, (body, head_line) in sorted(secs.items()):
        kind = SECTIONS.get(num, (None, ""))[0]
        if not kind:
            continue
        rows = rows_of_tables(body, head_line)
        if not rows:
            # Раздел без таблицы: узлы из строк-перечислений, ребер нет.
            for offset, raw in enumerate(body.splitlines()[1:], head_line + 1):
                item = raw.strip().lstrip("-*•").strip()
                if item and not item.startswith("#"):
                    b.node(kind, item[:200], offset)
            continue
        for headers, cells, line in rows:
            _row(b, num, headers, cells, line)

    graph = {
        "directed": True, "multigraph": False,
        "graph": {"case": os.path.basename(os.path.normpath(case)),
                  "as_of": b.as_of, "src_sha": b.src_sha},
        "nodes": list(b.nodes.values()), "links": b.links,
        "unparsed": b.unparsed,
    }
    return graph, []


def _row(b: Builder, num: int, headers: list[str], cells: list[str], line: int) -> None:
    """Одна строка карты → узлы и ребра ЭТОЙ строки. Ничего из соседних строк."""
    kind = SECTIONS[num][0]
    label = cell(cells, 0)
    if num == 1:                                   # Стороны и участники
        # Замер 03.09.2026 по 364 строкам живых карт: шапка почти всегда
        # «роль | имя / наименование | инн | адрес | представитель». Первая
        # колонка — РОЛЬ, а не имя; узел, названный «Истец», бесполезен.
        name = cell(cells, col(headers, "имя", "наименование", "участник", "лицо"))
        role = cell(cells, col(headers, "роль", "статус"))
        b.node("party", name or label, line, role=role or (label if name else ""))
    elif num == 2:                                 # Хронология
        what = cell(cells, col(headers, "событие", "что")) or cell(cells, 1)
        ev = b.node("event", f"{label} {what}".strip(), line, date=label)
        doc = cell(cells, col(headers, "документ", "источник", "файл"))
        if doc:
            b.link(ev, b.node("document", doc, line), "recorded_in", line)
    elif num == 3:                                 # Предмет спора и требования
        # Замер по 115 живым строкам: шапка раздела 3 чаще «дата | событие |
        # источник», чем «требование | объект». Строка-хронология, названная
        # требованием, дает узел claim, который ни на что не опирается — и
        # детектор краснеет на 96% требований. Форму определяем по шапке.
        if col(headers, "требован", "проситель", "предмет") < 0 and \
                col(headers, "дата") >= 0 and col(headers, "событ") >= 0:
            _row(b, 2, headers, cells, line)
            return
        cl = b.node("claim", label, line)
        obj = cell(cells, col(headers, "объект", "имуществ", "предмет"))
        if obj:
            b.link(cl, b.node("object", obj, line), "about", line)
        _share(b, cells, headers, line, obj)
        for nm in NORM_RE.findall(" ".join(cells)):
            b.link(cl, b.node("norm", nm, line), "based_on", line)
    elif num == 4:                                 # Ключевые факты и доказательства
        # Три живые формы (замер по 205 строкам):
        #   «факт → документ | источник | статус» — стрелка ВНУТРИ первой ячейки;
        #   «доказательство | что подтверждает | ограничение» — порядок обратный;
        #   «факт | документ | достоверность» — прямые колонки.
        status = cell(cells, col(headers, "статус", "достоверн"))
        ev_first = col(headers, "доказательств") == 0
        if "→" in label or "->" in label:
            left, _, right = label.replace("->", "→").partition("→")
            ft = b.node("fact", left, line)
            doc = norm(right) or cell(cells, col(headers, "источник", "документ"))
            if doc:
                enode = b.node("evidence", doc, line, status=status)
                b.link(enode, ft, "proves", line, force=status)
                b.link(enode, b.node("document", doc, line), "located_in", line)
            return
        if ev_first:
            enode = b.node("evidence", label, line, status=status)
            what = cell(cells, col(headers, "подтвержда", "что"))
            if what:
                b.link(enode, b.node("fact", what, line), "proves", line, force=status)
            else:
                b.unparsed.append(f"строка {line}: доказательство без факта")
            return
        ft = b.node("fact", label, line)
        ev = cell(cells, col(headers, "доказательств", "подтвержд"))
        doc = cell(cells, col(headers, "документ", "файл", "источник"))
        if ev:
            enode = b.node("evidence", ev, line, status=status)
            b.link(enode, ft, "proves", line, force=status)
            if doc:
                b.link(enode, b.node("document", doc, line), "located_in", line)
        elif doc:
            enode = b.node("evidence", doc, line, status=status)
            b.link(enode, ft, "proves", line, force=status)
            b.link(enode, b.node("document", doc, line), "located_in", line)
        else:
            b.unparsed.append(f"строка {line}: факт без доказательства и документа")
    elif num == 5:                                 # Финансовые показатели — объекты
        obj = b.node("object", label, line,
                     value=cell(cells, col(headers, "сумм", "стоим")),
                     cadastre=cell(cells, col(headers, "кад")))
        src = cell(cells, col(headers, "источник", "документ"))
        if src:
            b.link(obj, b.node("document", src, line), "recorded_in", line)
        _share(b, cells, headers, line, label, obj)
    elif num == 6:
        b.node("document", label, line, where=cell(cells, col(headers, "путь", "файл", "где")))
    elif num == 7:
        for nm in NORM_RE.findall(" ".join(cells)) or [label]:
            b.node("norm", nm, line)
    elif num == 8:
        # Вторая живая форма раздела 8 — таблица оценки: «№ | объект | кад. номер
        # | адрес | метод | рыночная стоимость». Это объекты, а не спор.
        oi = col(headers, "объект", "имуществ")
        if oi >= 0 and cell(cells, oi):
            b.node("object", cell(cells, oi), line,
                   cadastre=cell(cells, col(headers, "кад")),
                   value=cell(cells, col(headers, "стоим", "сумм")))
            _share(b, cells, headers, line, cell(cells, oi))
            return
        b.node("disputed", label, line, note=" · ".join(c for c in cells[1:] if c))
    elif num == 9:
        b.node("gap", label, line, note=" · ".join(c for c in cells[1:] if c))


def _share(b: Builder, cells, headers, line: int, obj_label: str, obj_id: str = "") -> None:
    """Доля — ребро party→object с основанием. Единой доли «на все» тут не бывает."""
    idx = col(headers, "доля")
    raw = cell(cells, idx) if idx >= 0 else ""
    if not raw:
        joined = " ".join(cells)
        m = SHARE_RE.search(joined)
        raw = m.group(0) if m else ""
    if not raw:
        return
    who = cell(cells, col(headers, "сторон", "кому", "за кем", "правообладат"))
    if not who:
        return
    obj = obj_id or (b.node("object", obj_label, line) if obj_label else "")
    if not obj:
        return
    b.link(b.node("party", who, line), obj, "share_in", line,
           share=raw, basis=cell(cells, col(headers, "основани")),
           acquired=cell(cells, col(headers, "приобрет", "дата")))


# ── Свежесть, запись, чтение ─────────────────────────────────────────────────

def manifest_of(case: str) -> dict:
    """{файл-держатель: sha} на момент сборки. Факт на диске для ЛЮБОГО читателя.

    Ленивая сверка внутри этого прибора прикрывает только его собственных
    читателей. `graphify query --graph`, MCP и чужая сессия читают graph.json
    напрямую — им нужен факт, лежащий рядом, а не инвариант в чужом коде.
    """
    return {name: sha(os.path.join(ctx_dir(case), name))
            for name in SOURCES if os.path.isfile(os.path.join(ctx_dir(case), name))}


def stale(case: str) -> list[str]:
    """Файлы-держатели, разошедшиеся с манифестом графа. Пусто — граф свеж."""
    mpath = os.path.join(out_dir(case), "manifest.json")
    if not os.path.isfile(os.path.join(out_dir(case), "graph.json")):
        return ["графа нет"]
    try:
        old = json.load(open(mpath, encoding="utf-8"))
    except (OSError, ValueError):
        return ["манифест графа не читается"]
    if old.get("frozen"):
        return []                                   # дело закрыто, сверять нечего
    now = manifest_of(case)
    return sorted(set(now) ^ set(old.get("files", {}))
                  | {k for k, v in now.items() if old.get("files", {}).get(k) != v})


def foreign(case: str, graph: dict) -> list[str]:
    """Чужие доверители внутри графа дела. Пусто — периметр цел.

    Проверка стоит здесь, а не только в отдельной команде: инвариант, который надо
    ПОМНИТЬ запустить, — это не инвариант. Свой доверитель законен, любой чужой —
    отказ записи. Найденные фамилии не печатаются, как и в pd_guard.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import graph_pd_check
        import pd_guard
    except ImportError:
        return []
    own = graph_pd_check.own_slug(case)
    if not own:
        return []
    names = [n for n in pd_guard.client_names() if n != own]
    pat = pd_guard.name_pattern(names)
    blob = json.dumps(graph, ensure_ascii=False)
    return pd_guard.scan_text(blob, pat, "граф дела")[:5]


def write_graph(case: str, graph: dict) -> str:
    """Атомарная запись: читатель видит либо старый граф, либо новый, но не половину.

    Файлового лока намеренно нет: лок вносит отказ «залипшая блокировка», который
    в доме уже есть (`.agent/drafts/.owner` с таймером). У os.replace его нет.

    Перед записью — сторож периметра: граф с чужим доверителем на диск не ложится.
    """
    alien = foreign(case, graph)
    if alien:
        raise PermissionError(
            "граф дела несет чужого доверителя, запись остановлена: "
            + "; ".join(alien))
    d = out_dir(case)
    os.makedirs(d, exist_ok=True)
    for name, data in (("graph.json", graph),
                       ("manifest.json", {"files": manifest_of(case),
                                          "as_of": graph["graph"].get("as_of", "")})):
        path = os.path.join(d, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return os.path.join(d, "graph.json")


def ensure_fresh(case: str) -> tuple[str, list[str]]:
    """(путь к графу, отказы). Разошелся — пересобираем молча: это стоит ноль."""
    if not stale(case):
        return os.path.join(out_dir(case), "graph.json"), []
    graph, refusals = build_graph(case)
    if refusals:
        return "", refusals
    try:
        return write_graph(case, graph), []
    except PermissionError as e:
        return "", [str(e)]


# ── Структурные дефекты карты ────────────────────────────────────────────────

def measurable(graph: dict) -> tuple[set, list[str]]:
    """Какие классы дефектов эта карта в принципе способна выразить.

    Замер 03.09.2026 по 36 живым картам: колонок «Доля» и «Сторона» в одной
    строке нет НИ В ОДНОЙ, поэтому ребер share_in вышло ноль, а «объект без доли»
    сработал на всех 388 объектах. Детектор, краснеющий всегда, — это не защита,
    его выключают в первый день. Класс, который формат карты выразить не может,
    объявляется НЕИЗМЕРИМЫМ и в дефекты не идет: это претензия к контракту карты,
    а не к делу.
    """
    rels = {l["relation"] for l in graph.get("links", [])}
    every, gaps = {"fact", "claim", "object", "evidence"}, []
    # «Мертвая первичка» (документ инвентаря, ни разу не процитированный) под
    # правилом «ребро только из одной строки» неизмерима принципиально: раздел 6
    # называет файл, а раздел 2 цитирует его другой строкой и часто другим
    # написанием. Сшить их можно только совпадением имен, то есть догадкой, а
    # догадка в деле дороже пропуска. Класс не заявляется вовсе — детектор,
    # который красит 45% документов, выключат в первый день.
    if "share_in" not in rels:
        every.discard("object")
        gaps.append("доля по объектам: в карте нет колонок «Доля»/«Сторона» в одной "
                    "строке — правило пообъектности графом не проверяется")
    if "proves" not in rels:
        every.discard("fact")
        gaps.append("факт → доказательство: раздел 4 не в табличной форме контракта")
    if "located_in" not in rels:
        every.discard("evidence")
        gaps.append("доказательство → документ: колонки документа в разделе 4 нет")
    if not ({"about", "based_on"} & rels):
        every.discard("claim")
        gaps.append("требование → объект/норма: раздел 3 в этой карте не несет "
                    "колонок требования — это хронология под другим заголовком")
    return every, gaps


def defects(graph: dict) -> list[str]:
    """То, ради чего граф и заводится: он видит ОТСУТСТВУЮЩЕЕ.

    Глазами отсутствие строки не видно — вычитывающий видит написанное.
    Список уходит в doc-reviewer и quality_gate.
    """
    kinds, _ = measurable(graph)
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    out_e: dict[str, set] = {}
    in_e: dict[str, set] = {}
    for l in graph.get("links", []):
        out_e.setdefault(l["source"], set()).add(l["relation"])
        in_e.setdefault(l["target"], set()).add(l["relation"])
    res = []
    for nid, n in sorted(nodes.items()):
        k, lab = n["kind"], n["label"][:60]
        if k not in kinds:
            continue
        loc = n.get("source_location", "")
        if k == "fact" and "proves" not in in_e.get(nid, set()):
            res.append(f"факт без доказательства ({loc}): {lab}")
        elif k == "claim" and not out_e.get(nid):
            res.append(f"требование ни на что не опирается ({loc}): {lab}")
        elif k == "object" and "share_in" not in in_e.get(nid, set()):
            res.append(f"объект без доли ({loc}): {lab}")
        elif k == "evidence" and "located_in" not in out_e.get(nid, set()):
            res.append(f"доказательство без документа ({loc}): {lab}")
    res += [f"не разобрано: {u}" for u in graph.get("unparsed", [])]
    return res


# ── Команды ──────────────────────────────────────────────────────────────────

def cmd_build(case: str) -> int:
    graph, refusals = build_graph(case)
    if refusals:
        print("case_graph: ОТКАЗ, граф не построен", file=sys.stderr)
        for r in refusals:
            print(f"  {r}", file=sys.stderr)
        return 1
    try:
        path = write_graph(case, graph)
    except PermissionError as e:
        print(f"case_graph: ОТКАЗ — {e}", file=sys.stderr)
        return 1
    print(f"case_graph: {path} — узлов {len(graph['nodes'])}, ребер {len(graph['links'])}, "
          f"карта от {graph['graph'].get('as_of') or 'даты нет'}")
    if graph["unparsed"]:
        print(f"  не разобрано строк: {len(graph['unparsed'])} — см. `defects`")
    return 0


def cmd_check(case: str) -> int:
    bad = stale(case)
    if not bad:
        print("case_graph: граф свеж")
        return 0
    print("case_graph: граф УСТАРЕЛ, разошлись: " + ", ".join(bad), file=sys.stderr)
    return 1


def cmd_ask(case: str, question: str) -> int:
    path, refusals = ensure_fresh(case)
    if refusals:
        print("case_graph: ОТКАЗ", file=sys.stderr)
        for r in refusals:
            print(f"  {r}", file=sys.stderr)
        return 1
    # Свой запросный движок не пишем: graph.json в схеме graphify, значит
    # query/path/explain работают по нему бесплатно.
    r = subprocess.run(["graphify", "query", question, "--graph", path],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def cmd_defects(case: str) -> int:
    path, refusals = ensure_fresh(case)
    if refusals:
        for r in refusals:
            print(f"  {r}", file=sys.stderr)
        return 1
    graph = json.load(open(path, encoding="utf-8"))
    found = defects(graph)
    _, gaps = measurable(graph)
    for g in gaps:
        print(f"  НЕ ПРОВЕРЕНО — {g}")
    if not found:
        print("case_graph: структурных дефектов не найдено")
        return 1 if gaps else 0
    print(f"case_graph: структурных дефектов {len(found)}")
    for d in found:
        print(f"  {d}")
    return 1



# ── Сверка якорей ────────────────────────────────────────────────────────────

def _flat(text: str) -> str:
    """Пробелы схлопнуты, регистр снят: сравниваем содержание, а не верстку."""
    return re.sub(r"\s+", " ", norm(text)).strip().lower()


def _line_bag(line: str) -> str:
    """Строка карты как содержимое ее ячеек через пробел."""
    body = line.strip()
    cells = [c for c in body.strip("|").split("|")] if body.startswith("|") else [body]
    return _flat(" ".join(cells))


def verify(case: str) -> tuple[int, list[str]]:
    """(проверено якорей, расхождения). Пусто — каждый якорь ведет в свою строку.

    Это единственная проверка, ради которой врезка в `doc-drafter` вообще
    допустима. Составитель получает от графа «loc=строка N», идет в карту и берет
    формулировку ОТТУДА. Если якорь смещен, он возьмет ЧУЖУЮ строку и вставит ее
    в процессуальный документ - и не заметит, потому что текст будет связным.
    Пропуск факта виден, подмена факта - нет. Поэтому: узел годен, только если
    его метка действительно встречается в названной строке карты.
    """
    map_path = os.path.join(ctx_dir(case), MAP_NAME)
    graph_path = os.path.join(out_dir(case), "graph.json")
    if not os.path.isfile(graph_path):
        graph, refusals = build_graph(case)
        if refusals:
            return 0, [f"графа нет и он не строится: {refusals[0]}"]
    else:
        graph = json.load(open(graph_path, encoding="utf-8"))
    if not os.path.isfile(map_path):
        return 0, [f"нет карты {map_path}"]
    lines = open(map_path, encoding="utf-8", errors="replace").read().splitlines()

    # Карта могла уехать после сборки: смещенный якорь на устаревшем графе - это
    # не дефект якоря, а дефект свежести, и лечится он пересборкой.
    if graph.get("graph", {}).get("src_sha") != sha(map_path):
        return 0, ["граф собран по другой редакции карты - сверять якоря нечего, "
                   "сначала пересборка"]

    bad, checked = [], 0
    for n in graph.get("nodes", []):
        m = re.search(r"строка (\d+)", n.get("source_location", ""))
        if not m:
            bad.append(f"{n['kind']}: якоря нет вовсе")
            continue
        checked += 1
        idx = int(m.group(1)) - 1
        if not 0 <= idx < len(lines):
            bad.append(f"{n['kind']} строка {idx + 1}: за пределами карты "
                       f"({len(lines)} строк)")
            continue
        # Метка сверяется с ЦЕЛОЙ строкой, разобранной на ячейки, а не по
        # отдельному слову. Проба по слову ловила смещение только между разными
        # таблицами: соседние строки ОДНОЙ таблицы делят словарь («Сторона А» и
        # «Сторона Б»), и сдвиг на строку проходил мимо - тот самый случай, ради
        # которого сверка и написана (селфтест 03.09.2026).
        if _flat(n["label"]) not in _line_bag(lines[idx]):
            bad.append(f"{n['kind']} строка {idx + 1}: метка в этой строке карты "
                       f"не встречается")
    return checked, bad


def cmd_verify(case: str) -> int:
    checked, bad = verify(case)
    if not bad:
        print(f"case_graph: якоря сходятся, проверено {checked}")
        return 0
    print(f"case_graph: якоря РАСХОДЯТСЯ с картой, {len(bad)} из {checked}",
          file=sys.stderr)
    for b in bad[:20]:
        print(f"  {b}", file=sys.stderr)
    if len(bad) > 20:
        print(f"  ... и еще {len(bad) - 20}", file=sys.stderr)
    return 1


# ── Сторож врезки ────────────────────────────────────────────────────────────
# Врезка живет ТЕКСТОМ в агентах и скиллах, а текст исполняется вероятностно и
# правится кем угодно. Прибор, который никто не зовет, — мертвый груз, и заметить
# это по графу нельзя: он просто перестанет обновляться. Поэтому каждая точка
# врезки названа здесь и проверяется машиной.
VREZKA = (
    (".claude/agents/doc-reviewer.md", "case_graph.py defects",
     "линза полноты не зовет разбор структурных дефектов"),
    (".claude/skills/doc-drafter/SKILL.md", "case_graph.py ask",
     "составитель снова читает карту целиком вместо якорей"),
    (".agents/skills/doc-drafter/SKILL.md", "case_graph.py ask",
     "вторая копия составителя разошлась с первой"),
    (".claude/agents/hearing-prep.md", "case_graph.py ask",
     "подготовка к заседанию снова перечитывает карту целиком"),
    (".claude/commands/new-case.md", "registry_check.py --conflict",
     "дело заводится без проверки конфликта интересов"),
    (".agents/skills/source-command-new-case/SKILL.md", "registry_check.py --conflict",
     "вторая копия команды заводит дело без проверки конфликта"),
    ("scripts/themiz_status.py", "graph_state",
     "сводка старта молчит о графе — он станет тихим артефактом"),
    (".claude/agents/case-mapper.md", "| Позиция | Сумма | Сторона | Доля |",
     "контракт карты потерял колонки доли — правило пообъектности снова неизмеримо"),
)


def vrezka(root: str = ".") -> list[str]:
    """Точки врезки, где вызов прибора пропал. Пусто — врезка на месте."""
    out = []
    for path, needle, why in VREZKA:
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            out.append(f"{path}: файла нет — {why}")
            continue
        if needle not in open(full, encoding="utf-8", errors="replace").read():
            out.append(f"{path}: нет вызова «{needle}» — {why}")
    return out


def cmd_vrezka(root: str) -> int:
    gone = vrezka(root)
    if not gone:
        print(f"case_graph: врезка на месте, точек {len(VREZKA)}")
        return 0
    print(f"case_graph: врезка РАЗЪЕХАЛАСЬ, точек потеряно {len(gone)} из {len(VREZKA)}",
          file=sys.stderr)
    for g in gone:
        print(f"  {g}", file=sys.stderr)
    return 1


def selftest() -> int:
    import shutil
    import tempfile
    ok = fail = 0

    def eq(name, got, want):
        nonlocal ok, fail
        if got == want:
            ok += 1
        else:
            fail += 1
            print(f"  ПРОВАЛ {name}: {got!r} != {want!r}")

    def yes(name, got):
        eq(name, bool(got), True)

    MAP = """# Карта знаний — раздел имущества
Обновлено: 03.09.2026 · Материалов: 4
Сверка: 4 согласовано, 0 оспорено
## КАРТА ГОТОВА ✓
## 1. Стороны и участники
| Участник | Роль |
|---|---|
| Сторона А | Истец |
| Сторона Б | Ответчик |
## 2. Хронология ключевых событий
| Дата | Событие | Документ |
|---|---|---|
| 12.03.2019 | Приобретена квартира | dogovor.pdf |
## 3. Предмет спора и требования
| Требование | Объект | Сторона | Доля | Основание |
|---|---|---|---|---|
| Признать право на долю в квартире по ст. 34 СК РФ | Квартира | Сторона А | 1/2 | приобретено в браке |
| Признать право на долю в доме по ст. 36 СК РФ | Дом | Сторона А | 3/4 | 1/2 супружеская и 1/4 наследство |
## 4. Ключевые факты и доказательства
| Факт | Доказательство | Документ | Статус |
|---|---|---|---|
| Квартира куплена в браке | Выписка ЕГРН | egrn.pdf | Подтверждено |
| Дом получен в наследство | | | Подтверждено |
## 5. Финансовые показатели
| Объект | Сумма | Сторона | Доля |
|---|---|---|---|
| Автомобиль | 900000 | Сторона А | 1/2 |
## 6. Инвентарь документов
| Документ | Путь |
|---|---|
| Лишний документ | 00_intake/lishniy.pdf |
"""
    tmp = tempfile.mkdtemp(prefix="case_graph_")
    try:
        case = os.path.join(tmp, "cases", "klient", "delo-2026")
        os.makedirs(os.path.join(case, ".agent", "context"))
        mp = os.path.join(case, ".agent", "context", MAP_NAME)
        open(mp, "w", encoding="utf-8").write(MAP)

        g, refusals = build_graph(case)
        eq("карта по контракту строится", refusals, [])
        kinds = {}
        for n in g["nodes"]:
            kinds.setdefault(n["kind"], []).append(n["label"])
        yes("стороны есть", "party" in kinds)
        yes("объекты есть", "object" in kinds)
        yes("факты есть", "fact" in kinds)

        shares = [l for l in g["links"] if l["relation"] == "share_in"]
        eq("доля пообъектно: три ребра на три объекта", len(shares), 3)
        eq("доли различаются по объектам",
           sorted(l["share"] for l in shares), ["1/2", "1/2", "3/4"])
        eq("у каждой доли свое основание",
           len({l["target"] for l in shares}), 3)
        yes("основание доли перенесено", any(l["basis"] for l in shares))

        eq("ребер INFERRED не бывает",
           {l["confidence"] for l in g["links"]}, {"EXTRACTED"})
        yes("каждый узел несет якорь",
            all(n["source_file"] and n["source_location"] and n["src_sha"]
                for n in g["nodes"]))
        eq("as_of взят из шапки карты", g["graph"]["as_of"], "03.09.2026")
        yes("норма из текста требования поднята",
            any(l["relation"] == "based_on" for l in g["links"]))

        d = defects(g)
        yes("факт без доказательства пойман",
            any(x.startswith("факт без доказательства") for x in d))
        # «Мертвая первичка» намеренно НЕ заявляется: под правилом одной строки
        # ее не отличить от документа, названного в двух разделах по-разному.
        eq("недоказуемый класс не выдается за дефект",
           any("мертвая первичка" in x for x in d), False)
        eq("объект с долей дефектом не считается",
           any(x.startswith("объект без доли") for x in d), False)

        # ── Живые формы карт (замер 03.09.2026 по 68 картам) ─────────────────
        REAL = """# Карта знаний
Обновлено: 01.09.2026 · Материалов: 3
## КАРТА ГОТОВА ✓
## 1. Стороны и участники
| Роль | Имя / наименование | ИНН | Адрес | Представитель |
|---|---|---|---|---|
| Истец | Сторона Первая | 1650000000 | Казань | — |
## 2. Хронология ключевых событий
| Дата | Событие | Документ-источник | Достоверность |
|---|---|---|---|
| 12.03.2019 | Заключен договор | dogovor.pdf | Подтверждено |
## 3. Предмет спора и требования
| Дата | Событие | Источник |
|---|---|---|
| 01.02.2026 | Подан иск | isk.pdf |
## 4. Ключевые факты и доказательства
| Факт → документ | Источник | Статус |
|---|---|---|
| Договор подписан обеими сторонами → dogovor.pdf | 00_intake/dogovor.pdf | Подтверждено |
## 8. Оспоренные факты
| № | Объект | Кад. номер | Адрес | Метод | Рыночная стоимость, руб. |
|---|---|---|---|---|---|
| 1 | Квартира | 16:50:012345:678 | Казань | сравнительный | 8000000 |
"""
        real = os.path.join(tmp, "cases", "klient", "zhivaya-2026")
        os.makedirs(os.path.join(real, ".agent", "context"))
        open(os.path.join(real, ".agent", "context", MAP_NAME), "w",
             encoding="utf-8").write(REAL)
        gr, rr = build_graph(real)
        eq("живая форма карты строится", rr, [])
        parties = [n for n in gr["nodes"] if n["kind"] == "party"]
        eq("сторона названа именем, а не ролью",
           [n["label"] for n in parties], ["Сторона Первая"])
        eq("роль сохранена атрибутом", parties[0]["role"], "Истец")
        facts = [n["label"] for n in gr["nodes"] if n["kind"] == "fact"]
        yes("стрелка внутри ячейки разобрана",
            facts and facts[0].startswith("Договор подписан"))
        yes("из стрелки вышло ребро proves",
            any(l["relation"] == "proves" for l in gr["links"]))
        objs = {n["label"]: n for n in gr["nodes"] if n["kind"] == "object"}
        yes("таблица оценки в разделе 8 дает объект", "Квартира" in objs)
        eq("кадастровый номер перенесен",
           objs["Квартира"]["cadastre"], "16:50:012345:678")

        # Неизмеримое не выдается за дефект.
        kinds_ok, gaps = measurable(gr)
        yes("отсутствие доли объявлено неизмеримым",
            any("доля по объектам" in g for g in gaps))
        eq("объекты без доли в дефекты не идут",
           any(x.startswith("объект без доли") for x in defects(gr)), False)
        kinds_full, gaps_full = measurable(g)
        yes("там, где доля есть, класс измеряется", "object" in kinds_full)

        # Отказ вместо тонкого графа.
        bad = os.path.join(tmp, "cases", "klient", "bez-markera")
        os.makedirs(os.path.join(bad, ".agent", "context"))
        open(os.path.join(bad, ".agent", "context", MAP_NAME), "w",
             encoding="utf-8").write("# карта\n## 1. Стороны и участники\n| А | Б |\n")
        g2, r2 = build_graph(bad)
        eq("без маркера граф не строится", g2, {})
        yes("отказ называет отсутствующий маркер", any(MARKER in x for x in r2))
        yes("отказ называет отсутствующие разделы",
            any("обязательных разделов" in x for x in r2))

        # Свежесть и атомарность.
        eq("до сборки граф считается устаревшим", stale(case), ["графа нет"])
        write_graph(case, g)
        eq("после сборки граф свеж", stale(case), [])
        open(mp, "a", encoding="utf-8").write("\n| Новая строка | х |\n")
        eq("правка карты ловится по sha", stale(case), [MAP_NAME])
        path, r3 = ensure_fresh(case)
        eq("обращение пересобрало молча", (r3, stale(case)), ([], []))
        yes("временных файлов не осталось",
            not any(f.endswith(".tmp") for f in os.listdir(out_dir(case))))


        # Сторож периметра внутри записи: чужой доверитель на диск не ложится.
        # Отдельное дело, чтобы не трогать граф, на котором проверяли свежесть.
        import pd_guard as _pg
        real = [n for n in _pg.client_names() if n]
        per = os.path.join(tmp, "cases", "klient", "perimetr-2026")
        os.makedirs(os.path.join(per, ".agent", "context"))
        open(os.path.join(per, ".agent", "context", MAP_NAME), "w",
             encoding="utf-8").write(MAP)
        gp, _ = build_graph(per)
        yes("свой граф пишется без помех", bool(write_graph(per, gp)))
        if real:
            spoiled = json.loads(json.dumps(gp))
            spoiled["nodes"].append({"id": "x", "label": f"см. дело {real[0]}",
                                     "kind": "fact", "source_file": "x",
                                     "source_location": "x", "src_sha": "x"})
            refused_write = False
            try:
                write_graph(per, spoiled)
            except PermissionError:
                refused_write = True
            eq("чужой доверитель в файл не пропущен", refused_write, True)
            saved_after = json.load(open(os.path.join(out_dir(per), "graph.json"),
                                         encoding="utf-8"))
            eq("после отказа на диске остался чистый граф",
               len(saved_after["nodes"]), len(gp["nodes"]))

        # Контракт карты и парсер обязаны понимать ОДНИ И ТЕ ЖЕ колонки. Шапки
        # берутся не из головы, а прямо из шаблона case-mapper: разойдутся —
        # тест покраснеет здесь, а не молча на живом деле.
        cm = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", ".claude", "agents", "case-mapper.md")
        if os.path.isfile(cm):
            tpl = open(cm, encoding="utf-8").read()
            h3 = re.search(r"^\| Требование \|.*$", tpl, re.M)
            h5 = re.search(r"^\| Позиция \| Сумма \|.*$", tpl, re.M)
            eq("контракт несет шапку раздела 3", bool(h3), True)
            eq("контракт несет шапку раздела 5", bool(h5), True)
            if h3 and h5:
                def blank(head):
                    n = len([c for c in head.strip().strip("|").split("|")])
                    return "|" + "|".join(["---"] * n) + "|"
                cols3 = [c.strip() for c in h3.group(0).strip().strip("|").split("|")]
                cols5 = [c.strip() for c in h5.group(0).strip().strip("|").split("|")]
                def row(cols, values):
                    return "| " + " | ".join(values.get(c, "—") for c in cols) + " |"
                CONTRACT = "\n".join([
                    "# Карта знаний", "Обновлено: 03.09.2026 · Материалов: 1",
                    MARKER, "## 1. Стороны и участники",
                    "| Роль | Имя / наименование |", "|---|---|", "| Истец | Сторона А |",
                    "## 2. Хронология ключевых событий",
                    "| Дата | Событие |", "|---|---|", "| 12.03.2019 | Куплена квартира |",
                    "## 3. Предмет спора и требования", h3.group(0), blank(h3.group(0)),
                    row(cols3, {"Требование": "Признать право на долю",
                                "Объект": "Квартира", "Норма": "ст. 34 СК РФ"}),
                    "## 4. Ключевые факты и доказательства",
                    "| Факт | Документ | Достоверность |", "|---|---|---|",
                    "| Куплена в браке | egrn.pdf | Подтверждено |",
                    "## 5. Финансовые показатели", h5.group(0), blank(h5.group(0)),
                    row(cols5, {"Позиция": "Квартира", "Сумма": "8000000",
                                "Сторона": "Сторона А", "Доля": "1/2",
                                "Основание": "приобретено в браке"}),
                    row(cols5, {"Позиция": "Дом", "Сумма": "5000000",
                                "Сторона": "Сторона А", "Доля": "3/4",
                                "Основание": "супружеская и наследство"}),
                    ""])
                con = os.path.join(tmp, "cases", "klient", "kontrakt-2026")
                os.makedirs(os.path.join(con, ".agent", "context"))
                open(os.path.join(con, ".agent", "context", MAP_NAME), "w",
                     encoding="utf-8").write(CONTRACT)
                gc, rc_ = build_graph(con)
                eq("карта по НОВОМУ контракту строится", rc_, [])
                sh = [l for l in gc["links"] if l["relation"] == "share_in"]
                eq("новый контракт дает долю пообъектно", len(sh), 2)
                eq("доли по объектам различаются",
                   sorted(l["share"] for l in sh), ["1/2", "3/4"])
                yes("основание доли перенесено из контрактной колонки",
                    all(l["basis"] for l in sh))
                yes("требование связано с объектом",
                    any(l["relation"] == "about" for l in gc["links"]))
                _, gaps_c = measurable(gc)
                eq("на контрактной карте доля становится ИЗМЕРИМОЙ",
                   any("доля по объектам" in x for x in gaps_c), False)

        # Сверка якорей: смещенный якорь опаснее пропущенного. Пропуск факта
        # виден, подмена факта - нет: составитель возьмет чужую строку и вставит
        # ее в процессуальный документ связным текстом.
        ver = os.path.join(tmp, "cases", "klient", "yakorya-2026")
        os.makedirs(os.path.join(ver, ".agent", "context"))
        vmap = os.path.join(ver, ".agent", "context", MAP_NAME)
        open(vmap, "w", encoding="utf-8").write(MAP)
        gv, _ = build_graph(ver)
        write_graph(ver, gv)
        checked, bad = verify(ver)
        eq("на честном графе якоря сходятся", bad, [])
        yes("проверено не вхолостую", checked >= 10)

        # Смещаем ОДИН якорь на строку вниз - сверка обязана это увидеть.
        spoiled = json.load(open(os.path.join(out_dir(ver), "graph.json"),
                                 encoding="utf-8"))
        moved = None
        for n in spoiled["nodes"]:
            m = re.search(r"строка (\d+)", n["source_location"])
            if m and int(m.group(1)) > 1:
                n["source_location"] = f"строка {int(m.group(1)) + 1}"
                moved = n["label"]
                break
        with open(os.path.join(out_dir(ver), "graph.json"), "w",
                  encoding="utf-8") as f:
            json.dump(spoiled, f, ensure_ascii=False)
        yes("смещение якоря на одну строку поймано", moved and verify(ver)[1])

        # Якорь за пределами карты.
        spoiled["nodes"][0]["source_location"] = "строка 99999"
        with open(os.path.join(out_dir(ver), "graph.json"), "w",
                  encoding="utf-8") as f:
            json.dump(spoiled, f, ensure_ascii=False)
        yes("якорь за пределами карты пойман",
            any("за пределами карты" in b for b in verify(ver)[1]))

        # Карта уехала после сборки: это дефект свежести, а не якоря, и сверка
        # обязана сказать именно это, а не сыпать ложными расхождениями.
        write_graph(ver, gv)
        open(vmap, "a", encoding="utf-8").write("\n| хвост | x |\n")
        eq("устаревший граф не выдается за кривые якоря",
           any("другой редакции" in b for b in verify(ver)[1]), True)

        # Сторож врезки: пропавший вызов обязан быть замечен.
        fake = os.path.join(tmp, "dom")
        for rel, needle, _ in VREZKA:
            fp = os.path.join(fake, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w", encoding="utf-8").write(f"текст {needle} текст")
        eq("полная врезка нареканий не дает", vrezka(fake), [])
        victim = os.path.join(fake, VREZKA[0][0])
        open(victim, "w", encoding="utf-8").write("кто-то переписал агента")
        gone = vrezka(fake)
        eq("пропавший вызов замечен", len(gone), 1)
        yes("сказано, чем это грозит", VREZKA[0][2] in gone[0])
        os.remove(victim)
        yes("пропавший файл тоже замечен", vrezka(fake))

        # Схема совместима с graphify: узлы и ребра лежат там, где движок их ждет.
        saved = json.load(open(path, encoding="utf-8"))
        yes("схема graphify: nodes/links на месте",
            isinstance(saved.get("nodes"), list) and isinstance(saved.get("links"), list))
        yes("у каждого ребра есть source и target",
            all(l.get("source") and l.get("target") for l in saved["links"]))

        # Периметр: каждый якорь указывает ВНУТРЬ своего дела. Ссылка наружу —
        # это и есть смешение доверителей, из-за которого весь проект и затеян.
        anchors = {n["source_file"] for n in saved["nodes"]} | \
                  {l["source_file"] for l in saved["links"]}
        eq("все якоря ведут внутрь дела",
           all(not a.startswith(("/", "cases/")) and ".." not in a for a in anchors), True)
        eq("якорь ровно один — карта этого дела", anchors, {MAP_NAME.join((".agent/context/", ""))
           if False else os.path.join(".agent", "context", MAP_NAME)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"case_graph --selftest: {ok} прошло, {fail} провалено")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?",
                    choices=["build", "ask", "check", "defects",
                             "verify", "vrezka"])
    ap.add_argument("case", nargs="?")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "vrezka":
        return cmd_vrezka(a.case or ".")
    if not a.cmd or not a.case:
        ap.error("нужны команда и путь к делу")
    if not os.path.isdir(a.case):
        print(f"case_graph: нет дела {a.case}", file=sys.stderr)
        return 2
    if a.cmd == "build":
        return cmd_build(a.case)
    if a.cmd == "check":
        return cmd_check(a.case)
    if a.cmd == "defects":
        return cmd_defects(a.case)
    if a.cmd == "verify":
        return cmd_verify(a.case)
    if not a.question:
        ap.error("ask требует вопрос")
    return cmd_ask(a.case, a.question)


if __name__ == "__main__":
    sys.exit(main())
