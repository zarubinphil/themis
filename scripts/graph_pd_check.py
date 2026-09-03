#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pd_check.py — данные одного доверителя не лежат в графе, доступном по делу другого.

ЗАЧЕМ. Периметр графа держался предположением «собрали по правильному каталогу —
значит чисто». Замер 03.09.2026 показал, что предположение ошиблось дважды:
  • корневой `graphify-out/` (97 МБ) нес 3278 узлов из cases/ по 73 доверителям,
    22,8 МБ сконвертированного текста документов в converted/ и 5446 записей о
    файлах дел в manifest.json — включая 3551 JPG и 69 MP4 из 00_intake;
  • `knowledge/practice_index.md`, который считался обезличенным корпусом права,
    несет 135 вхождений 14 имен папок дел в поле «Источник: … дело {слаг}».
Оба проекта внедрения графов считали периметр верным по построению и детектора не
предусмотрели. Инвариант, который проверяет только намерение, — это не инвариант.

ЧТО ПРОВЕРЯЕТСЯ. Имена папок доверителей читаются С ДИСКА в момент запуска
(pd_guard.client_names), сам сторож их не хранит. Ищутся в ПУТИ и в СОДЕРЖИМОМ
каждого файла графа: graph.json, manifest.json, GRAPH_REPORT.md, converted/,
cache/ — потому что `graphify update` переписывает только первые два, а остальные
переживают пересборку и делают отчет «утечка закрыта» ложным.

ДВА РЕЖИМА.
  --graph ПУТЬ   общий граф: ЛЮБОЙ доверитель внутри — отказ.
  --case ПУТЬ    граф дела: свой доверитель законен, ЛЮБОЙ ЧУЖОЙ — отказ.

ПРИМЕНЕНИЕ:
    python3 scripts/graph_pd_check.py --graph graphify-out
    python3 scripts/graph_pd_check.py --graph knowledge/graphify-out
    python3 scripts/graph_pd_check.py --case cases/{клиент}/{дело}
    python3 scripts/graph_pd_check.py --selftest

Код возврата: 0 — чисто; 1 — найдены данные дел; 2 — ошибка вызова.
Сами найденные фамилии в вывод НЕ печатаются: сторож не должен становиться вторым
каналом утечки. Печатается файл, смещение и длина совпадения — как в pd_guard.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pd_guard  # noqa: E402  — переиспользуем разбор имен, а не пишем второй

# Читается кусками: graph.json бывает 34 МБ, а отказ нужен на первом попадании.
CHUNK = 1 << 20
# Перекрытие между кусками: имя, разрезанное границей чтения, иначе теряется.
OVERLAP = 256
# Каталоги графа, которые `graphify update` НЕ переписывает и которые поэтому
# переживают пересборку (замер 03.09.2026: converted/ 22,8 МБ, cache/ 38 МБ).
SURVIVES_REBUILD = ("converted", "cache")
# Печатать все грязные файлы 97-мегабайтного каталога незачем: решение то же.
LIST_CAP = 20


def graph_files(root: str) -> list[str]:
    """Все файлы графа. Каталог или одиночный graph.json."""
    if os.path.isfile(root):
        return [root]
    out = []
    for base, _dirs, files in os.walk(root):
        out.extend(os.path.join(base, f) for f in files)
    return sorted(out)


def scan_file(path: str, pat, rel: str) -> list[str]:
    """Путь и содержимое одного файла. Находки без раскрытия фамилии."""
    hits = pd_guard.scan_text(rel, pat, f"{rel} (имя файла)")
    if hits:
        return hits
    try:
        with open(path, "rb") as f:
            tail = ""
            while True:
                block = f.read(CHUNK)
                if not block:
                    break
                text = tail + block.decode("utf-8", errors="ignore")
                found = pd_guard.scan_text(text, pat, rel)
                if found:
                    return found
                tail = text[-OVERLAP:]
    except OSError as e:
        return [f"{rel}: не прочитан ({e.__class__.__name__}) — считаем грязным"]
    return []


def own_slug(case_dir: str) -> str:
    """Слаг доверителя из пути дела: cases/{клиент}/{дело}."""
    parts = os.path.normpath(os.path.abspath(case_dir)).split(os.sep)
    if "cases" not in parts:
        return ""
    i = parts.index("cases")
    return parts[i + 1] if len(parts) > i + 1 else ""


def check(root: str, own: str = "", cases_dir: str = pd_guard.CASES) -> tuple[int, list[str]]:
    """(число проверенных файлов, находки). own — свой доверитель, ему тут место.

    По одной находке на файл, но обход не прерывается: очистку планируют по
    ПОЛНОМУ списку грязных файлов, а гейту довольно и первой строки. Ранний выход
    на первом файле заставлял бы чистить каталог по одному файлу за прогон.
    """
    names = [n for n in pd_guard.client_names(cases_dir) if n != own]
    pat = pd_guard.name_pattern(names)
    files = graph_files(root)
    base = root if os.path.isdir(root) else os.path.dirname(root)
    hits: list[str] = []
    for p in files:
        found = scan_file(p, pat, os.path.relpath(p, base))
        if found:
            hits.append(found[0])
    return len(files), hits


def report(root: str, n: int, hits: list[str], own: str) -> int:
    scope = f"граф дела (свой доверитель разрешен)" if own else "общий граф"
    if not hits:
        print(f"graph_pd_check: чисто. {scope}: {root}, файлов проверено {n}")
        return 0
    print(f"graph_pd_check: ОТКАЗ. {scope}: {root}. "
          f"Грязных файлов {len(hits)} из {n}", file=sys.stderr)
    for h in hits[:LIST_CAP]:
        print(f"  {h}", file=sys.stderr)
    if len(hits) > LIST_CAP:
        print(f"  ... и еще {len(hits) - LIST_CAP} файлов", file=sys.stderr)
    survives = [d for d in SURVIVES_REBUILD if os.path.isdir(os.path.join(root, d))]
    if survives:
        print(f"  ВНИМАНИЕ: {', '.join(survives)} переживают `graphify update` — "
              f"пересборка утечку не закроет, каталог надо удалить целиком",
              file=sys.stderr)
    return 1


def selftest() -> int:
    import json
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

    tmp = tempfile.mkdtemp(prefix="graph_pd_")
    try:
        cases = os.path.join(tmp, "cases")
        for d in ("testfamiliya-ab", "vtorayafam-cd", "_logs"):
            os.makedirs(os.path.join(cases, d))
        g = os.path.join(tmp, "graphify-out")
        os.makedirs(os.path.join(g, "converted"))

        clean = {"nodes": [{"label": "ГК РФ ст. 256", "source_file": "knowledge/kodeksy/gk.md"}]}
        with open(os.path.join(g, "graph.json"), "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False)
        n, hits = check(g, cases_dir=cases)
        eq("чистый граф проходит", hits, [])
        eq("чистый граф прочитан не вхолостую", n, 1)

        # 1. Слаг в СОДЕРЖИМОМ graph.json.
        dirty = {"nodes": [{"label": "иск", "source_file": "cases/testfamiliya-ab/delo-2026/x.md"}]}
        with open(os.path.join(g, "graph.json"), "w", encoding="utf-8") as f:
            json.dump(dirty, f, ensure_ascii=False)
        _, hits = check(g, cases_dir=cases)
        eq("слаг в содержимом ловится", bool(hits), True)
        eq("фамилия не печатается", any("testfamiliya" in h for h in hits), False)

        # 2. Слаг в ИМЕНИ файла converted/ — замер 03.09.2026 нашел ровно такие.
        with open(os.path.join(g, "graph.json"), "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False)
        name_hit = os.path.join(g, "converted", "vozrazheniya_vtorayafam-cd_1a73.md")
        with open(name_hit, "w", encoding="utf-8") as f:
            f.write("нормы и практика, фамилий в тексте нет")
        _, hits = check(g, cases_dir=cases)
        eq("слаг в имени файла ловится", bool(hits), True)
        os.remove(name_hit)

        # 3. Разрез имени границей чтения не теряет находку.
        big = os.path.join(g, "converted", "big.md")
        with open(big, "w", encoding="utf-8") as f:
            f.write("a" * (CHUNK - 5) + " testfamiliya-ab " + "b" * 100)
        _, hits = check(g, cases_dir=cases)
        eq("имя на границе куска ловится", bool(hits), True)
        os.remove(big)

        # 4. Режим графа дела: свой доверитель законен, чужой — нет.
        case_g = os.path.join(tmp, "case-graph")
        os.makedirs(case_g)
        with open(os.path.join(case_g, "graph.json"), "w", encoding="utf-8") as f:
            json.dump({"nodes": [{"source_file":
                                  "cases/testfamiliya-ab/delo-2026/knowledge-map.md"}]},
                      f, ensure_ascii=False)
        _, hits = check(case_g, own="testfamiliya-ab", cases_dir=cases)
        eq("свой доверитель в своем графе разрешен", hits, [])
        _, hits = check(case_g, own="vtorayafam-cd", cases_dir=cases)
        eq("чужой доверитель в графе дела ловится", bool(hits), True)

        # 5. Нечитаемый файл считается грязным, а не чистым (fail-closed).
        bad = os.path.join(g, "converted", "nedostupno.md")
        with open(bad, "w") as f:
            f.write("x")
        os.chmod(bad, 0o000)
        _, hits = check(g, cases_dir=cases)
        os.chmod(bad, 0o644)
        eq("нечитаемый файл = отказ", bool(hits), True)
        os.remove(bad)

        # 6. Обход не прерывается: два грязных файла дают две находки.
        for i, nm in enumerate(("a.md", "b.md")):
            with open(os.path.join(g, "converted", nm), "w", encoding="utf-8") as f:
                f.write("см. cases/testfamiliya-ab/delo-2026/x.md")
        _, hits = check(g, cases_dir=cases)
        eq("оба грязных файла названы", len(hits), 2)
        eq("одна строка на файл", len(hits), len(set(h.split(":")[0] for h in hits)))
        for nm in ("a.md", "b.md"):
            os.remove(os.path.join(g, "converted", nm))

        # 7. own_slug вытаскивает доверителя из пути дела.
        eq("own_slug из пути", own_slug("cases/testfamiliya-ab/delo-2026"), "testfamiliya-ab")
        eq("own_slug вне cases пуст", own_slug("/tmp/chuzhoy/graph"), "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"graph_pd_check --selftest: {ok} прошло, {fail} провалено")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--graph", help="общий граф: каталог graphify-out или graph.json")
    ap.add_argument("--case", help="граф дела: каталог cases/{клиент}/{дело}")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if bool(a.graph) == bool(a.case):
        ap.error("нужен ровно один из --graph / --case")
    if a.case:
        own = own_slug(a.case)
        if not own:
            print(f"graph_pd_check: путь не похож на дело: {a.case}", file=sys.stderr)
            return 2
        root = os.path.join(a.case, ".agent", "graph")
    else:
        own, root = "", a.graph
    if not os.path.exists(root):
        print(f"graph_pd_check: нет пути {root}", file=sys.stderr)
        return 2
    n, hits = check(root, own)
    return report(root, n, hits, own)


if __name__ == "__main__":
    sys.exit(main())
