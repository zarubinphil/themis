#!/usr/bin/env bash
# _python.sh — выбрать интерпретатор ЯВНО. Подключается через `source`.
#
# ЗАЧЕМ. launchd запускает задания без логин-шелла, и PATH у него системный
# (/usr/bin:/bin:/usr/sbin:/sbin). `python3` там резолвится в /usr/bin/python3 —
# на macOS это 3.9.6, а проект написан под 3.11+. Прецедент 21.08.2026: месячная
# сверка корпуса права падала на импорте `pii_gate` («unsupported operand
# type(s) for |: 'type' and 'NoneType'» — синтаксис 3.10+ в аннотации), корпус
# тихо не обновлялся, а `cite.py` просто отвечал «не найдено». В логе это
# выглядело обычной ошибкой, и никто не связал одно с другим.
#
# Правило: фоновое задание не полагается на PATH. Интерпретатор проверяется
# версией, а не именем; не нашли подходящего — падаем громко, а не работаем
# наполовину.
#
# Использование в скрипте задания:
#     . "$(dirname "$0")/_python.sh"      # выставит $PY либо завершит с кодом 1
#     "$PY" scripts/что-нибудь.py

PY=""
for _kandidat in /usr/local/bin/python3 \
                 /opt/homebrew/bin/python3 \
                 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
                 "$(command -v python3 2>/dev/null)"; do
    [ -x "$_kandidat" ] || continue
    if "$_kandidat" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PY="$_kandidat"
        break
    fi
done
unset _kandidat

if [ -z "$PY" ]; then
    echo "СТОП: не найден python3 версии 3.11+. Проект написан под 3.11+;" >&2
    echo "      на 3.9 падает импорт (аннотации вида 'X | None')." >&2
    echo "      Проверить: python3 -V; поставить 3.11+ либо поправить PATH задания." >&2
    return 1 2>/dev/null || exit 1
fi

export PY
