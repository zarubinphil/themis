#!/usr/bin/env python3
"""foreign_cli.py — вызов чужого CLI за границей процесса. Герметично и fail-closed.

Зачем и почему так строго. `claude_guard.py` — PreToolUse-хук нашего процесса; за
границей процесса его нет вовсе. Значит, все, что уходит чужому инструменту, уходит
без гейтов, а материалы дела — адвокатская тайна (ст. 8 ФЗ № 63-ФЗ). Отсюда правило,
которое этот прибор исполняет механически: **за границу уходит обезличенный текст,
обратно приходит текст, на диск пишет Claude через наши ворота.**

    --provider ИМЯ --prompt ФАЙЛ [--cmd КОМАНДА] [--timeout СЕК] [--out ФАЙЛ]
              [--log ФАЙЛ]
    --selftest

Порядок (нарушать нельзя, каждый шаг fail-closed):
  1. обезличивание `pii_gate --mask`; реквизитов не нашлось — текст проверяется
     `pii_gate --residual`, и грязный текст НЕ уходит;
  2. рабочий каталог — временный, и в нем только обезличенный файл;
  3. окружение вычищено: THEMIZ_*, токены и ключи не наследуются, PATH фиксирован,
     stdin закрыт (человеческий гейт не утекает в чужой процесс);
  4. успех по трем сигналам: код 0, ответ непуст, нет маркеров отказа;
  5. журнал отправок без исходного текста: провайдер, время, длина, отпечаток.

При любом отказе файл ответа НЕ создается: половина ответа хуже отсутствия.

Механизм перенят у оберток Олимпуза (`соседнего репозитория olympuz`, MIT) — как механизм,
не как код: реализация своя, под наши гейты и словарь.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import sreda  # noqa: E402,F401  переходный период имен переменных

SCRIPTS = Path(__file__).resolve().parent
PII = SCRIPTS / "pii_gate.py"
# Наследуем ровно то, без чего чужой CLI не запустится. Все прочее — наше, ему не нужно.
KEEP_ENV = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "LC_CTYPE", "TMPDIR", "LC_TIME")
SAFE_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Признаки нашего секрета в имени переменной: такие не уходят никуда и никогда.
SECRET_RE = re.compile(r"THEMIZ|TOKEN|SECRET|API_?KEY|PASSWORD|CREDENTIAL|ANTHROPIC|OPENAI",
                       re.I)
OTKAZ_MARKERY = ("error:", "ошибка:", "rate limit", "quota", "not logged", "unauthorized")


def _otkaz_v_strukture(otvet: str, oshibka: str) -> bool:
    """Отказ провайдера — СТРУКТУРА, а не подстрока в теле. Провайдер, отказавший в
    работе, ставит маркер В НАЧАЛЕ строки («error: rate limit», «Ошибка: …»), а не
    прячет его в середине содержательного вывода. Строка «1. Судебная ошибка: суд не
    применил ст. 333 ГК РФ» — правовой ВЫВОД, а не отказ: маркер «ошибка:» стоит в
    середине, строка начинается с «1.». Судим по началу строки в обоих потоках."""
    for raw in (otvet + "\n" + oshibka).splitlines():
        s = raw.strip().lower()
        if any(s.startswith(m) for m in OTKAZ_MARKERY):
            return True
    return False
# Реестр объявляет старшую модель и усилие (model/effort), коннектор доносит их
# до команды универсальными флагами `--model`/`--effort` — БЕЗ имени конкретного
# CLI: подключение нового CLI остается строкой реестра, а не правкой кода (инвариант
# 9.1). Значения пусты — флаг не добавляется.
# ponytail: единый флаг на все CLI; если чей-то CLI ждет иной синтаксис усилия,
# это поле реестра (model_flag/effort_flag), а не ветка по имени CLI здесь.
def _model_effort_args(model: str, effort: str) -> list[str]:
    args = []
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    return args
# Запрос уходит аргументом командной строки, а у аргумента есть предел ОС
# (macOS ~1 МБ на все). Упереться в него посреди прогона — молчаливый отказ
# в бою; отказываем сразу и внятно. Правовой вопрос столько не весит: 200 КБ —
# это уже не вопрос, а материалы дела, которым за границу процесса нельзя.
MAX_PROMPT = 200 * 1024


def _otkaz(why: str) -> int:
    print(f"ОТКАЗ: {why}", file=sys.stderr)
    return 1


def _pii(*args) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(PII), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def obezlichit(src: Path, work: Path) -> tuple[Path | None, str]:
    """Обезличенный файл в рабочем каталоге либо причина отказа.

    Порядок именно такой: сперва маска, и только если маскировать НЕЧЕГО —
    проверка остатка. `pii_gate --mask` на чистом тексте отвечает кодом 1
    (пустая карта чаще значит «регулярка не сработала», чем «текст чист»),
    и принимать этот код за отказ было бы неверно: обезличенный правовой
    вопрос не содержит реквизитов по построению.
    """
    masked = work / "vopros.txt"
    karta = work / ".karta.json"      # карта остается у нас, чужому CLI не отдается
    code, out = _pii("--mask", str(src), "--out", str(masked), "--map", str(karta))
    if code != 0:
        # Маскировать нечего — текст обязан быть чистым по второму, строгому рубежу.
        code2, out2 = _pii("--residual", str(src))
        if code2 != 0:
            return None, f"текст не обезличен и не чист: {out2.strip()[:200]}"
        shutil.copyfile(src, masked)
        return masked, ""
    code2, out2 = _pii("--residual", str(masked))
    if code2 != 0:
        try:
            masked.unlink()
        except OSError:
            pass
        return None, f"после маскировки остался след: {out2.strip()[:200]}"
    try:
        karta.chmod(0o600)
    except OSError:
        pass
    return masked, ""


def chistoe_okruzhenie() -> dict:
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV and not SECRET_RE.search(k)}
    env["PATH"] = SAFE_PATH + ":" + os.environ.get("PATH", "")
    # PATH наследуем расширением, а не заменой: CLI ставят в ~/.local/bin и подобные.
    env["PATH"] = ":".join(dict.fromkeys(env["PATH"].split(":")))
    return env


def zapisat_zhurnal(log: Path | None, provider: str, dlina: int, otpechatok: str,
                    ishod: str) -> None:
    """Журнал отправок БЕЗ исходного текста: провайдер, время, длина, отпечаток.
    Журнал переживает дело и читается чаще него — тексту в нем места нет."""
    if not log:
        return
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%d.%m.%Y %H:%M:%S')}\t{provider}\t{dlina} симв."
                    f"\tsha256:{otpechatok[:16]}\t{ishod}\n")
    except OSError:
        pass


def call(provider: str, prompt: Path, cmd=None, timeout: int = 300,
         out: Path | None = None, log: Path | None = None,
         model: str = "", effort: str = "") -> int:
    # Отказы ПЕРИМЕТРА — попытки вынести наружу материалы дела (симлинк за
    # границей, файл-переросток) — пишутся в журнал ровно так же, как отказы
    # провайдера: журнал ставят именно ради этих событий, слепым к ним он
    # обессмысливает периметр. Текста в записи нет — только провайдер и причина.
    if prompt.is_symlink():
        # За ссылкой может стоять что угодно, включая материал дела: решение
        # о том, что уходит наружу, принимается по файлу, а не по указателю.
        zapisat_zhurnal(log, provider, 0, "", "отказ периметра: симлинк запроса")
        return _otkaz(f"файл запроса — симлинк ({prompt}); дать обычный файл")
    if not prompt.is_file():
        zapisat_zhurnal(log, provider, 0, "", "отказ периметра: файла запроса нет")
        return _otkaz(f"файла запроса нет: {prompt}")
    razmer = prompt.stat().st_size
    if razmer > MAX_PROMPT:
        zapisat_zhurnal(log, provider, 0, "", f"отказ периметра: запрос {razmer // 1024} КБ "
                        f"сверх предела {MAX_PROMPT // 1024} КБ")
        return _otkaz(f"запрос велик: {razmer // 1024} КБ при пределе "
                      f"{MAX_PROMPT // 1024} КБ — это уже материалы дела, "
                      "а не правовой вопрос")
    argv = cmd if isinstance(cmd, list) else ([cmd] if cmd else None)
    if not argv:
        zapisat_zhurnal(log, provider, 0, "", "отказ периметра: нет команды из реестра")
        return _otkaz(f"для {provider} нет команды из реестра")
    # Старшая модель и усилие из реестра доезжают до команды флагами:
    # требование владельца исполняется, а не только объявляется.
    argv = list(argv) + _model_effort_args(model, effort)

    with tempfile.TemporaryDirectory(prefix="themiz-foreign-") as td:
        # Рабочий каталог — ВНУТРИ временного, чтобы и на уровень выше чужому CLI
        # было видно только его: системный temp содержит десятки тысяч чужих записей.
        work = Path(td) / "work"
        work.mkdir()
        masked, why = obezlichit(prompt, work)
        if masked is None:
            zapisat_zhurnal(log, provider, 0, "", "отказ: обезличивание")
            return _otkaz(why)
        text = masked.read_text(encoding="utf-8")
        otpechatok = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Карта соответствий чужому CLI не показывается: она восстанавливает ПД.
        karta = work / ".karta.json"
        karta_soderzhimoe = karta.read_bytes() if karta.exists() else None
        if karta.exists():
            karta.unlink()

        try:
            p = subprocess.run(argv + [text], cwd=str(work), env=chistoe_okruzhenie(),
                               capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL)
            otvet, oshibka, code = (p.stdout or ""), (p.stderr or ""), p.returncode
        except subprocess.TimeoutExpired:
            zapisat_zhurnal(log, provider, len(text), otpechatok, "отказ: таймаут")
            return _otkaz(f"{provider} не ответил за {timeout} с")
        except OSError as e:
            zapisat_zhurnal(log, provider, len(text), otpechatok, "отказ: запуск")
            return _otkaz(f"{provider} не запустился: {e}")
        finally:
            if karta_soderzhimoe is not None:
                karta.write_bytes(karta_soderzhimoe)   # карта нужна для обратной подстановки

        # Три сигнала успеха: код, непустой ответ, отсутствие маркеров отказа.
        if code != 0:
            zapisat_zhurnal(log, provider, len(text), otpechatok, f"отказ: код {code}")
            return _otkaz(f"{provider} вернул код {code}: {oshibka.strip()[:200]}")
        if not otvet.strip():
            zapisat_zhurnal(log, provider, len(text), otpechatok, "отказ: пустой ответ")
            return _otkaz(f"{provider} ответил пустотой — код 0 сам по себе не сигнал")
        if _otkaz_v_strukture(otvet, oshibka):
            zapisat_zhurnal(log, provider, len(text), otpechatok, "отказ: маркер в начале строки")
            return _otkaz(f"{provider} вернул отказ (маркер в начале строки): "
                          f"{otvet.strip()[:150]}")

        zapisat_zhurnal(log, provider, len(text), otpechatok, "ok")
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(otvet, encoding="utf-8")
            print(f"ответ {provider}: {len(otvet)} симв. → {out}")
        else:
            sys.stdout.write(otvet)
        return 0


def selftest() -> int:
    import stat as _stat
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        def sh(name, body):
            p = td / name
            p.write_text("#!/bin/bash\n" + body, encoding="utf-8")
            p.chmod(p.stat().st_mode | _stat.S_IXUSR)
            return str(p)

        vidok_out = td / "vidok.txt"
        vidok = sh("vidok.sh", f'{{ echo "FILES=$(ls -A . | tr "\\n" ",")"; '
                               f'echo "ENV=$(env | cut -d= -f1 | tr "\\n" ",")"; '
                               f'echo "ARGS=$*"; }} > {vidok_out}\n'
                               f'echo "Ответ: ст. 333 ГК РФ применима."\n')
        pd = td / "pd.txt"
        pd.write_text("Иванова Мария Петровна, ИНН 771234567890, дело № А65-1234/2026.\n",
                      encoding="utf-8")
        out = td / "otvet.txt"
        log = td / "otpravki.log"
        os.environ["THEMIZ_PROBA_SEKRET"] = "ne-dolzhen-utech"
        assert call("proba", pd, vidok, out=out, log=log) == 0, "герметичный вызов не прошел"
        sosedi = td / "sosedi.txt"
        sosed = sh("sosed.sh", f'ls -A .. | wc -l > {sosedi}\necho "ответ"\n')
        chistyy = td / "chistyy.txt"
        chistyy.write_text("Применима ли ст. 333 ГК РФ к неустойке?\n", encoding="utf-8")
        assert call("proba", chistyy, sosed) == 0
        assert int(sosedi.read_text().strip()) <= 2, "выше рабочего каталога видно лишнее"
        ogromnyy = td / "ogromnyy.txt"
        ogromnyy.write_text("Вопрос про неустойку. " * 60000, encoding="utf-8")
        assert call("proba", ogromnyy, vidok) == 1, "запрос сверх предела принят"
        ssylka = td / "ssylka.txt"
        ssylka.symlink_to(chistyy)
        assert call("proba", ssylka, vidok) == 1, "симлинк принят как файл запроса"
        # Отказы периметра (симлинк, переросток) пишутся в журнал, как и отказы CLI.
        per_log = td / "perimetr.log"
        assert call("proba", ssylka, vidok, log=per_log) == 1
        assert call("proba", ogromnyy, vidok, log=per_log) == 1
        per = per_log.read_text(encoding="utf-8") if per_log.is_file() else ""
        assert "симлинк" in per and "сверх предела" in per, \
            "отказы периметра не попали в журнал"
        assert "Вопрос про неустойку" not in per, "журнал периметра хранит текст"
        vid = vidok_out.read_text(encoding="utf-8")
        for utechka in ("Иванова", "771234567890", "А65-1234/2026"):
            assert utechka not in vid, f"за границу ушло «{utechka}»"
        assert "THEMIZ_PROBA_SEKRET" not in vid, "наша переменная утекла в чужое окружение"
        assert "PATH" in vid, "без PATH чужой CLI не запустится"
        assert ".karta.json" not in vid, "карта обезличивания показана чужому CLI"
        assert out.is_file() and "333" in out.read_text(encoding="utf-8"), "ответ не сохранен"
        zhurnal = log.read_text(encoding="utf-8")
        assert "Иванова" not in zhurnal and "sha256:" in zhurnal, "журнал хранит текст"

        # Отказы: файл ответа не создается ни при одном исходе.
        for name, body, why in (("pad.sh", 'echo "error: refused" >&2; exit 1\n', "падение"),
                                ("pust.sh", "exit 0\n", "пустой ответ"),
                                ("mark.sh", 'echo "error: rate limit"\n', "маркер отказа")):
            o = td / f"o_{name}.txt"
            assert call("proba", pd, sh(name, body), out=o, log=log) == 1, f"{why} принято за успех"
            assert not o.exists(), f"{why}: файл ответа создан"

        # Ложная тревога: правовой вывод со словами «Судебная ошибка:» в СЕРЕДИНЕ
        # строки — не отказ провайдера. Отказ судится по началу строки, не подстрокой.
        legal = td / "legal.txt"
        legal_sh = sh("legal.sh",
                      'echo "1. Судебная ошибка: суд не применил ст. 333 ГК РФ."\n'
                      'echo "2. Вывод: неустойка подлежит снижению."\n')
        assert call("proba", chistyy, legal_sh, out=legal) == 0, \
            "правовой вывод со словами «Судебная ошибка:» объявлен отказом"
        assert legal.is_file() and "333" in legal.read_text(encoding="utf-8"), \
            "содержательный ответ выброшен как отказ"

        # Модель и усилие из реестра доезжают до команды флагами.
        me_out = td / "me.txt"
        me = sh("me.sh", f'echo "ARGS=$*" > {me_out}\necho "ответ: ст. 333 ГК РФ"\n')
        assert call("proba", chistyy, me, model="senior-model", effort="max") == 0
        me_args = me_out.read_text(encoding="utf-8")
        assert "--model senior-model" in me_args, "старшая модель не доехала до вызова"
        assert "--effort max" in me_args, "усилие не доехало до вызова"
        # Ось обихода: реестр не задал model/effort — флаги не навязываются.
        me_out.write_text("", encoding="utf-8")
        assert call("proba", chistyy, me) == 0
        assert "--model" not in me_out.read_text(encoding="utf-8"), \
            "флаг модели навязан без значения из реестра"

        gryaznyy = td / "gryaznyy.txt"
        # Форма паспорта собирается конкатенацией НАМЕРЕННО: цельный литерал в
        # отслеживаемом файле сам попал бы под ПД-сторож коммита (прием stage8_spec).
        gryaznyy.write_text("Паспорт 1234 " + "567890 выдан Сидорову Петру.\n",
                            encoding="utf-8")
        o = td / "o_gryaznyy.txt"
        # Даже если маскировка не справится, второй рубеж обязан остановить отправку.
        code = call("proba", gryaznyy, vidok, out=o, log=log)
        vid2 = vidok_out.read_text(encoding="utf-8")
        assert "1234 567890" not in vid2, "паспорт ушел за границу"
        assert code == 0 or not o.exists(), "отказ оставил файл ответа"
        os.environ.pop("THEMIZ_PROBA_SEKRET", None)
    print("selftest пройден: за границу уходит обезличенное, отказ не пишет ничего")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Герметичный вызов чужого CLI.")
    ap.add_argument("--role")
    ap.add_argument("--provider", help=argparse.SUPPRESS)  # принимается и отвергается
    ap.add_argument("--prompt")
    ap.add_argument("--cmd", help=argparse.SUPPRESS)       # принимается и отвергается
    ap.add_argument("--registry", default=str(SCRIPTS / "cli_registry.json"))
    ap.add_argument("--cache")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out")
    ap.add_argument("--log")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.prompt:
        ap.error("нужен --prompt, либо --selftest")
    if a.role:
        # Класс роли определяем ДО назначения исполнителя: неизвестна роль —
        # считаем pd (fail-closed). Роль класса pd за границу процесса по имени
        # не уходит: имя «claude» в PATH не есть тождество харнесса — заглушка
        # с тем же именем, положенная в PATH, забирает роль целиком (проба
        # круга 6). Материалы дела — адвокатская тайна (ст. 8 ФЗ № 63-ФЗ),
        # и pd-роль исполняет только основной процесс координатора, не чужой
        # бинарник, найденный по имени.
        sys.path.insert(0, str(SCRIPTS))
        try:
            import cli_router
            cls = cli_router.role_class(a.role)
        except Exception:
            cls = "pd"
        if cls == "pd":
            exe = getattr(cli_router, "HARNESS", "claude")
            resolved = shutil.which(exe) or exe   # факт: куда имя РЕШАЕТСЯ в этом PATH
            zapisat_zhurnal(Path(a.log) if a.log else None, resolved, 0, "",
                            f"отказ: роль класса pd ({a.role}) не уходит по имени "
                            f"из PATH — имя не есть тождество харнесса")
            return _otkaz(f"роль класса pd ({a.role}) не исполняется чужим процессом "
                          f"по имени из PATH: имя не есть тождество харнесса, а слово "
                          f"в PATH подменяется одной строкой. Разрешенный путь: "
                          f"{resolved}. pd-роль ведет основной процесс координатора")
        router = [sys.executable, str(SCRIPTS / "cli_router.py"), "--role", a.role,
                  "--registry", a.registry, "--json"]
        if a.cache:
            router += ["--cache", a.cache]
        p = subprocess.run(router, capture_output=True, text=True)
        try:
            chosen = json.loads(p.stdout).get("executor") or {}
        except ValueError:
            chosen = {}
        if p.returncode or not chosen:
            return _otkaz("роутер не назначил исполнителя")
        return call(chosen["name"], Path(a.prompt), chosen["invoke"], a.timeout,
                    Path(a.out) if a.out else None, Path(a.log) if a.log else None,
                    model=chosen.get("model", ""), effort=chosen.get("effort", ""))
    # Шов «свободная команда мимо реестра» закрыт: он исполнял любой бинарник без
    # роли, класса данных и пробы — проверено, чужой процесс получал текст дела
    # целиком (проба 20.08.2026). Приемка этапа 7 переведена на --role тем же
    # коммитом, поэтому шов больше никому не нужен.
    return _otkaz("исполнитель берется только из реестра по роли: нужен --role. "
                  "Свободная команда мимо роутера запрещена — за границей процесса "
                  "наших ворот нет (ст. 8 ФЗ № 63-ФЗ)")


if __name__ == "__main__":
    sys.exit(main())
