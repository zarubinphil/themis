#!/usr/bin/env python3
"""themiz_metiz.py - питонский фасад врезки Метиды. Одна строка на стороне дома.

Зачем отдельный файл. Дом Фемиды питонский, ядро Метиды - TypeScript; мост через node
неизбежен. Он стоит здесь ОДИН раз, чтобы врезка в прибор дома оставалась одной строкой,
а откат - обратной заменой той же одной строки. Логики сжатия тут нет ни одной: она вся
в `scripts/themiz-metiz.mjs` и дальше в Метиде.

СОСТОЯНИЕ: врезка сделана, и она ровно в двух точках scripts/markdown_extract.py - там, где дом
отдает модели не превью, а тело. Обе ветки зовут ОДИН фасад с одним профилем; разведены только
метки шагов, потому что тела у веток разные.

    ВРЕЗКА 1 (ветка --inline, тело документа целиком до --max-chars):
        было:  out = body[: a.max_chars]
        стало: out = __import__("themiz_metiz").squeeze_text(body[: a.max_chars], p, "inline")

    ВРЕЗКА 2 (ветка --grep, список совпадений; потолка символов у нее нет вовсе):
        было:  print("\n".join(hits[:400]))
        стало: print(__import__("themiz_metiz").squeeze_text("\n".join(hits[:400]), p, "grep"))

    ОТКАТ (обратная замена тех же двух строк; любую можно откатить отдельно):
        python3 - <<'PY'
        import pathlib
        f = pathlib.Path("scripts/markdown_extract.py"); s = f.read_text(encoding="utf-8")
        s = s.replace(
            'out = __import__("themiz_metiz").squeeze_text(body[: a.max_chars], p, "inline")',
            'out = body[: a.max_chars]')
        s = s.replace(
            'print(__import__("themiz_metiz").squeeze_text("\\n".join(hits[:400]), p, "grep"))',
            'print("\\n".join(hits[:400]))')
        f.write_text(s, encoding="utf-8")
        PY

Fail-open в трафике (доктрина 5). Этот модуль НЕ БРОСАЕТ никогда и НИЧЕГО не печатает в
stdout: stdout прибора дома - это текст для модели. Нет node, нет моста, нет Метиды, мост
упал, мост завис - возвращается исходный текст байт в байт, а причина уходит в stderr.

Fail-closed в данных живет НЕ ЗДЕСЬ, а в мосте: дело клиента выводится из пути к материалу,
и без дела мост не сжимает вовсе (изоляция памяти дороже экономии). Делом при этом признается
только путь под КАНОНИЧЕСКИМ корнем дерева дел, а не любой путь с сегментом cases.

Граница процесса. Путь к материалу подается мосту ЗАКРЫТЫМ КАНАЛОМ - отдельным дескриптором,
а не в argv: argv виден любому процессу владельца через ps и уезжает в диагностику запуска,
а имя доверителя за границу процесса не выходит. В argv остается только номер дескриптора.

    --selftest   проверка без сети и без Метиды
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOST = HERE / "themiz-metiz.mjs"
# Потолок ожидания. Сжатие 200 КБ идет доли секунды; минута - это уже зависший мост, и ждать
# его дольше значит держать заложником работу по делу.
TIMEOUT = 60


def _tiho(why: str) -> None:
    """Причина - в stderr, и только туда. В stdout идет документ дела, служебной строке там
    не место: модель приняла бы ее за часть материала."""
    try:
        print(f"themiz-metiz: {why}", file=sys.stderr)
    except Exception:
        pass


def squeeze_text(text: str, put: str, label: str = "inline") -> str:
    """Сжатый текст либо исходный байт в байт. Исключений не бросает ни при каких входах.

    `put` - путь к материалу дела: из него мост выводит `cases/<клиент>/<дело>`, а из дела -
    свой каталог хранилища. Путь вне дерева дел означает «дела нет», и мост не сжимает.
    """
    if not isinstance(text, str) or not text:
        return text
    if not MOST.is_file():
        _tiho(f"моста нет ({MOST}), текст отдан как есть")
        return text

    # Путь к материалу дела идет ЗАКРЫТЫМ каналом - отдельным дескриптором, а НЕ в argv.
    # В argv он стоял до этой правки, и живой прогон поймал имя доверителя в `ps -ww -A -o args`:
    # 11 попаданий за 0.56 с работы моста. argv виден любому процессу владельца и попадает в
    # диагностику запуска, то есть уходит за границу процесса, а туда имя клиента не выходит.
    # Окружение вместо argv не спасает: `ps -E` показывает и его.
    #
    # Труба заполняется и закрывается ДО запуска моста: тогда мост сразу читает готовый путь и
    # видит конец потока, и взаимной блокировки двух процессов быть не может. Плата за такой
    # порядок в том, что в момент записи читателя еще НЕТ, и блокирующая запись ждала бы его
    # вечно - причем ждала бы ВЫШЕ по коду, чем TIMEOUT, то есть потолок времени фасада на нее
    # не распространялся бы вовсе. Здесь стояло обещание «путь короче буфера трубы (64 КБ)», но
    # оно было на бумаге: длину никто не проверял, а емкость буфера не гарантирована никем.
    # POSIX обещает только PIPE_BUF, и это 512 байт [замер] - вдвое меньше, чем PATH_MAX
    # 1024 [замер], так что в гарантированную часть буфера не помещается даже законный путь.
    # Поэтому ожидание не оценивается числом, а исключается режимом дескриптора: на
    # неблокирующем конце ядро вместо ожидания отдает EAGAIN или короткую запись, и оба исхода
    # уводят в fail-open. stdin занят телом документа и остается занят им.
    #
    # surrogateescape, а не голый encode: имя файла в POSIX - это БАЙТЫ, и os.listdir отдает
    # негодную для utf-8 последовательность суррогатами вида \udcff. Голый encode на таком пути
    # БРОСАЛ UnicodeEncodeError - мимо fail-open и вопреки обещанию докстроки "исключений не
    # бросает ни при каких входах". Отказ сжимать тут был бы вторым способом ошибиться: путь,
    # который система читает без вопросов, обязан доехать до моста как есть, а решать по нему -
    # мосту. Точные байты и уезжают; что с ними делать, решает читающая сторона.
    dannye = str(put).encode("utf-8", "surrogateescape")
    chitat, pisat = os.pipe()
    try:
        try:
            os.set_blocking(pisat, False)
            leglo = os.write(pisat, dannye)
        except OSError as e:
            leglo = -1
            _tiho(f"закрытый канал не принял путь ({e.strerror}), текст отдан как есть")
        finally:
            os.close(pisat)
        # Дописать остаток некуда: свой конец уже закрыт, а моста еще нет. И обрезанный путь
        # опаснее отсутствия сжатия - из огрызка мост вывел бы ЧУЖОЕ дело и сложил бы материал
        # в чужое хранилище. Длина в stderr не идет: она тоже про путь.
        if leglo != len(dannye):
            if leglo >= 0:
                _tiho("путь не встал в закрытый канал целиком, текст отдан как есть")
            return text

        try:
            p = subprocess.run(
                ["node", str(MOST), "squeeze", "--path-fd", str(chitat), "--label", str(label)],
                input=text, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
                pass_fds=(chitat,),
            )
        except FileNotFoundError:
            _tiho("node не найден, текст отдан как есть")
            return text
        except subprocess.TimeoutExpired:
            _tiho(f"мост не ответил за {TIMEOUT} с, текст отдан как есть")
            return text
        except Exception as e:                              # noqa: BLE001 - fail-open без исключений
            _tiho(f"мост не запустился ({e}), текст отдан как есть")
            return text
    finally:
        # Свой конец трубы закрываем всегда: иначе дескрипторы копятся на каждом вызове прибора.
        # Этот finally накрывает и ранние возвраты по неудавшейся записи пути выше.
        try:
            os.close(chitat)
        except OSError:
            pass

    if p.stderr:
        _tiho(p.stderr.strip()[:400])
    # Пустой вывод при коде 0 - это не «сжали до нуля», это сломанный мост. Половина текста
    # дела хуже отсутствия сжатия, поэтому берем выдачу только целиком и только при коде 0.
    if p.returncode != 0 or not p.stdout:
        _tiho(f"мост вернул код {p.returncode}, текст отдан как есть")
        return text
    return p.stdout


# --- САМОПРОВЕРКА ---------------------------------------------------------------------------


def selftest() -> int:
    """Проверяет ровно то, ради чего фасад существует: он не бросает и не портит текст,
    когда сжать нечем. Каждый случай - отдельный процесс и отдельное окружение."""
    import tempfile

    obrazec = "Определение суда от 12.03.2026. Срок исковой давности истек.\n" * 200
    plohih = 0

    def proba(imya, fn):
        nonlocal plohih
        try:
            fn()
            print(f"  ✓ {imya}")
        except AssertionError as e:
            plohih += 1
            print(f"  ✗ {imya}: {e}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        delo = td / "cases" / "ivanov-ai" / "dolg-2026" / "00_intake" / "akt.pdf"
        delo.parent.mkdir(parents=True, exist_ok=True)

        def bez_node():
            # PATH без node - ровно то, что бывает у чужого юриста без установленной ноды.
            chistyy = dict(os.environ, PATH=str(td / "pusto"))
            p = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); import themiz_metiz as m;"
                 "t = sys.stdin.read();"
                 "sys.stdout.write('RAVNO' if m.squeeze_text(t, sys.argv[2]) == t else 'RAZOSHLOS')",
                 str(HERE), str(delo)],
                input=obrazec, capture_output=True, text=True, env=chistyy)
            assert p.returncode == 0, f"фасад бросил без node: {p.stderr[:300]}"
            assert p.stdout == "RAVNO", "без node текст изменился, а обязан вернуться как есть"

        def bez_mosta():
            # Копия фасада без соседа-моста: мост мог не доехать при частичной установке.
            odin = td / "odin"
            odin.mkdir(exist_ok=True)
            (odin / "themiz_metiz.py").write_text(
                Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
            p = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); import themiz_metiz as m;"
                 "t = sys.stdin.read();"
                 "sys.stdout.write('RAVNO' if m.squeeze_text(t, sys.argv[2]) == t else 'RAZOSHLOS')",
                 str(odin), str(delo)],
                input=obrazec, capture_output=True, text=True)
            assert p.returncode == 0, f"фасад бросил без моста: {p.stderr[:300]}"
            assert p.stdout == "RAVNO", "без моста текст изменился"

        def musor_na_vhode():
            for plohoy in (None, 0, b"bytes", ""):
                assert squeeze_text(plohoy, str(delo)) == plohoy, f"мусор {plohoy!r} не вернулся как есть"

        def vne_dela():
            # Файл вне дерева дел: изоляции не на чем стоять, значит сжатия быть не должно.
            vne = td / "inbox" / "skan.md"
            vne.parent.mkdir(parents=True, exist_ok=True)
            assert squeeze_text(obrazec, str(vne)) == obrazec, "материал вне дела был изменен"

        SHABLON = "руб|договор"

        def _telo_grep(vyvod: str) -> str:
            """Тело выдачи ветки --grep: все, что напечатано после строки-заголовка."""
            assert "--- grep " in vyvod, f"ветка --grep не напечатала заголовок: {vyvod[:200]}"
            i = vyvod.index("--- grep ")
            telo = vyvod[vyvod.index("\n", i) + 1:]
            return telo[:-1] if telo.endswith("\n") else telo

        def _etalon(kesh: Path) -> str:
            """Что ветка напечатала бы БЕЗ врезки. Считается из того же кеша, тем же
            выражением, что стоит в приборе: иначе проба сверяла бы выдачу сама с собой."""
            import re
            md = sorted(kesh.glob("*.md"))
            assert len(md) == 1, f"в кеше пробы ожидался один .md, лежит {len(md)}"
            body = md[0].read_text(encoding="utf-8")
            rx = re.compile(SHABLON, re.IGNORECASE)
            return "\n".join(f"{i}: {ln}" for i, ln in enumerate(body.splitlines(), 1) if rx.search(ln))

        def _fikstura(kuda: Path) -> Path:
            """Табличный материал: совпадений много и они однообразны - на таком входе
            свертка сработала бы, если бы могла сработать вообще."""
            kuda.parent.mkdir(parents=True, exist_ok=True)
            stroki = []
            for i in range(1, 120):
                stroki.append(f"| {i} | договор аренды | госпошлина 2000 руб | срок истек |")
                stroki.append("строка без совпадений")
            kuda.write_text("# Выписка\n\n" + "\n".join(stroki) + "\n", encoding="utf-8")
            return kuda

        def _proekt(koren: Path) -> Path:
            """Отдельный КОРЕНЬ ПРОЕКТА для пробы: прибор, фасад и мост в его scripts/.

            Корень дерева дел мост выводит из своего расположения ({корень}/scripts/), и это
            не придирка проб, а сама починка: подставной путь с сегментом cases делом больше не
            признается. Значит и проба обязана завести СВОЙ корень, а не выдавать временный
            каталог за дерево дел. Настоящие дела владельца проба при этом не трогает.
            """
            skripty = koren / "scripts"
            skripty.mkdir(parents=True, exist_ok=True)
            for imya in ("markdown_extract.py", "themiz_metiz.py", "themiz-metiz.mjs",
                         "sreda.py"):
                (skripty / imya).write_bytes((HERE / imya).read_bytes())
            return skripty

        def _progon(dom: Path, material: Path):
            # Кеш прибора выводится из HOME, дом состояния - из THEMIZ_HOME с откатом на HOME.
            # Подменяются ОБА: одного HOME мало, потому что унаследованный THEMIZ_HOME (его
            # ставит, например, селфтест моста) увел бы журнал мимо пробы, и проба покраснела
            # бы на пустом месте. С обоими проба не трогает ни журнал владельца, ни его кеш.
            # THEMIZ_METIZ_DIR назван прямо: у корня пробы нет соседа-Метиды, а без него проба
            # проверяла бы только откат. Нет Метиды на диске - проба все равно годна: журнал
            # замеров обязан лечь и на выключенном сжатии (доктрина 3).
            okr = dict(os.environ, HOME=str(dom), THEMIZ_HOME=str(dom / ".themiz"),
                       THEMIZ_METIZ_DIR=str(HERE.parent.parent / "metiz"))
            p = subprocess.run(
                [sys.executable, str(_proekt(dom) / "markdown_extract.py"), str(material),
                 "--grep", SHABLON],
                capture_output=True, text=True, env=okr)
            assert p.returncode == 0, f"прибор дома не отработал: {p.stderr[-300:]}"
            return p

        def vetka_grep_zovet_metidu():
            # Единственная проба, которая гоняет ПРИБОР ДОМА, а не фасад: до нее врезка была
            # покрыта только с той стороны, где ее нет.
            dom = td / "dom-delo"
            mat = _fikstura(dom / "cases" / "ivanov-ai" / "dolg-2026" / "00_intake" / "vypiska.md")
            p = _progon(dom, mat)
            telo = _telo_grep(p.stdout)
            etalon = _etalon(dom / ".cache" / "legal_extract")

            # Доктрина 2 и 4 разом: либо выигрыша не было и вход вернулся байт в байт, либо
            # выход изменен - и тогда в нем обязан стоять маркер, которым он восстановим.
            assert telo == etalon or "[metiz:fold" in telo or "[themiz:ccr" in telo, (
                "выдача --grep изменена, но маркера восстановления в ней нет")

            # Доктрина 3: замер обязан лечь в журнал, и лечь именно по этому телу.
            zhurnal = dom / ".themiz" / "state" / "squeeze.jsonl"
            assert zhurnal.is_file(), "врезки в ветке --grep нет: журнал замеров пуст"
            import json as _json
            zapisi = [_json.loads(x) for x in zhurnal.read_text(encoding="utf-8").splitlines() if x]
            grep = [z for z in zapisi if z.get("label") == "grep"]
            assert len(grep) == 1, f"записей с меткой grep ожидалась одна, легло {len(grep)}"
            assert grep[0]["before"] == len(etalon.encode("utf-8")), (
                f"замер снят не с тела ветки: before={grep[0]['before']}, "
                f"тело={len(etalon.encode('utf-8'))} байт")
            assert grep[0]["profile"] == "audit", f"профиль разошелся с --inline: {grep[0]['profile']}"

        def vetka_grep_vne_dela():
            # Fail-closed по делу на боевом пути: материал инбокса не сжимается и в журнал не
            # попадает вовсе - слота дела для записи нет.
            dom = td / "dom-inbox"
            mat = _fikstura(dom / "inbox" / "skan.md")
            p = _progon(dom, mat)
            assert _telo_grep(p.stdout) == _etalon(dom / ".cache" / "legal_extract"), (
                "материал вне дерева дел был изменен")
            assert not (dom / ".themiz" / "state" / "squeeze.jsonl").is_file(), (
                "материал вне дела попал в журнал замеров")

        def imya_ne_uhodit_v_argv():
            """Имя доверителя не выходит за границу процесса через argv моста.

            До починки путь стоял в argv (`--from-path /.../cases/<КЛИЕНТ>/<дело>/...`), и живой
            прогон поймал имя в `ps -ww -A -o args`: 11 попаданий за 0.56 с работы моста. Проба
            берет то же самое без гонки: node подменяется заглушкой, которая записывает свой argv
            на диск. Что лежит в argv - то и видно в ps любому процессу владельца.
            """
            shim = td / "shim"
            shim.mkdir(exist_ok=True)
            ulov = td / "argv-mosta.txt"
            (shim / "node").write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "' + str(ulov) + '"\nexit 1\n',
                encoding="utf-8")
            (shim / "node").chmod(0o700)

            klient = "sekretnyy-klient-ivanov"
            put = td / "argv-proba" / "cases" / klient / "dolg-2026" / "00_intake" / "akt.pdf"
            put.parent.mkdir(parents=True, exist_ok=True)
            p = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); import themiz_metiz as m;"
                 "t = sys.stdin.read();"
                 "sys.stdout.write('RAVNO' if m.squeeze_text(t, sys.argv[2]) == t else 'RAZOSHLOS')",
                 str(HERE), str(put)],
                input=obrazec, capture_output=True, text=True,
                env=dict(os.environ, PATH=str(shim)))
            assert p.returncode == 0, f"фасад бросил на заглушке моста: {p.stderr[:300]}"
            assert p.stdout == "RAVNO", "мост отказал, а текст все равно изменился"

            argv = ulov.read_text(encoding="utf-8")
            assert klient not in argv, f"ИМЯ ДОВЕРИТЕЛЯ В ARGV МОСТА, его видно в ps: {argv!r}"
            assert "cases/" not in argv, f"путь дела в argv моста: {argv!r}"
            assert "--path-fd" in argv, f"путь подан не закрытым каналом: {argv!r}"

        def dlinnyy_put_ne_veshaet():
            """Путь длиннее буфера трубы не подвешивает фасад навсегда.

            Путь пишется в трубу ДО запуска моста, то есть в момент записи читателя еще нет.
            Пока запись была блокирующей, длинный путь вешал фасад НАСМЕРТЬ, и вешал выше
            потолка времени: живой прогон дал путь 66560 байт при емкости трубы 65536 байт
            [замер] - процесс висел до убийства снаружи, а до subprocess.run с его timeout не
            доходил ни разу. Проба держит СВОЮ стену времени и гоняет фасад отдельным
            процессом: регресс здесь выглядит как зависание, а зависание внутри самопроверки
            остановило бы и саму самопроверку.
            """
            STENA = 15                       # честный отказ идет доли секунды [замер]
            shim = td / "shim-dliny"
            shim.mkdir(exist_ok=True)
            ulov = td / "mosta-zvali.txt"
            (shim / "node").write_text(
                '#!/bin/sh\nprintf zvali > "' + str(ulov) + '"\nexit 1\n', encoding="utf-8")
            (shim / "node").chmod(0o700)

            # Длину строит сам ребенок: гигантский аргумент уперся бы в ARG_MAX, да и класть
            # путь в argv - ровно то, от чего уходит этот канал.
            try:
                p = subprocess.run(
                    [sys.executable, "-c",
                     "import sys; sys.path.insert(0, sys.argv[1]); import themiz_metiz as m;"
                     "t = sys.stdin.read();"
                     "put = '/' + 'a' * int(sys.argv[2]);"
                     "sys.stdout.write('RAVNO' if m.squeeze_text(t, put) == t else 'RAZOSHLOS')",
                     str(HERE), "300000"],
                    input=obrazec, capture_output=True, text=True,
                    env=dict(os.environ, PATH=str(shim)), timeout=STENA)
            except subprocess.TimeoutExpired:
                raise AssertionError(
                    f"фасад завис на длинном пути дольше {STENA} с, потолок времени его не ловит"
                ) from None
            assert p.returncode == 0, f"фасад бросил на длинном пути: {p.stderr[:300]}"
            assert p.stdout == "RAVNO", "длинный путь изменил текст, а обязан вернуть его как есть"
            # Второй способ ошибиться тут - дописать сколько влезло и пойти дальше. Мост тогда
            # вывел бы дело из огрызка пути, то есть сложил бы материал в ЧУЖОЕ хранилище.
            assert not ulov.exists(), "мост позвали с обрезанным путем: дело вывелось бы из огрызка"

        proba("без node фасад не бросает и отдает текст как есть", bez_node)
        def put_s_negodnym_baytom_ne_brosaet():
            """Путь с негодной для utf-8 последовательностью не роняет фасад.

            Имя файла в POSIX - это БАЙТЫ, и os.listdir отдает негодные суррогатами вида
            \\udcff. Голый .encode("utf-8") на таком пути БРОСАЛ UnicodeEncodeError - мимо
            fail-open и вопреки докстроке squeeze_text "исключений не бросает ни при каких
            входах". Бросок шел ДО subprocess.run, то есть и мимо потолка времени.

            ЧЕГО ЭТА ПРОБА НЕ ПРОВЕРЯЕТ, сказано вслух. Сжать такой материал все равно нельзя,
            и это не выбор фасада, а замер файловой системы: APFS отказывает создавать имя с
            негодным байтом (errno 92, EILSEQ) [замер], то есть на этой машине такого файла не
            существует, мост не выведет из него дела и вернет текст как есть. Байты уезжают
            мосту точными нарочно: появится том, где такое имя законно (внешний диск, сетевая
            шара), и решать по ним будет мост, а не потерявший их фасад.
            """
            plohoy = str(td / "cases" / "ivanov-ai" / "dolg-2026" / "00_intake") + "/akt-\udcff.pdf"
            telo = "текст документа"
            out = squeeze_text(telo, plohoy, "proba-surrogat")
            assert out == telo, "негодный байт в имени листа изменил текст"

        proba("путь с негодным байтом не роняет фасад", put_s_negodnym_baytom_ne_brosaet)
        proba("без моста фасад не бросает и отдает текст как есть", bez_mosta)
        proba("мусор на входе возвращается как есть", musor_na_vhode)
        proba("материал вне дерева дел не сжимается", vne_dela)
        proba("имя доверителя не уходит в argv моста", imya_ne_uhodit_v_argv)
        proba("длинный путь не подвешивает фасад навсегда", dlinnyy_put_ne_veshaet)
        proba("ветка --grep прибора дома зовет Метиду и пишет замер", vetka_grep_zovet_metidu)
        proba("ветка --grep не сжимает материал вне дерева дел", vetka_grep_vne_dela)

    print("themiz_metiz: КРАСНЫЙ" if plohih else "themiz_metiz: зеленый")
    return 1 if plohih else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else
             (print(__doc__.strip(), file=sys.stderr) or 2))
