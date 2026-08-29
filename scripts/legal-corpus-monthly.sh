#!/bin/bash
# legal-corpus-monthly.sh — ежемесячная сверка+обновление корпуса права.
#
# Запускается через launchd 1 числа каждого месяца в 12:00
# (legal.corpus.update.plist). Логика: --check (дешево, без полной
# перекачки) -> если у кодекса поменялась дата редакции -> --update
# --doc <slug> только для него. Одна итоговая строка — в knowledge/_corpus_log.md
# (построчный лог отдельных документов пишет сам update_legal_corpus.py).
set -o pipefail
cd "$(dirname "$0")/.." || exit 1

# Интерпретатор выбирается явно, общим правилом (см. scripts/_python.sh).
. "$(dirname "$0")/_python.sh"
echo "интерпретатор: $PY ($("$PY" -V 2>&1))"

LOG="knowledge/_corpus_log.md"
STAMP=$(date '+%d.%m.%Y %H:%M')

CHECK_OUT=$("$PY" scripts/update_legal_corpus.py --check 2>&1)
echo "$CHECK_OUT"

# Берем и ИЗМЕНИЛОСЬ, и НЕ ВЫГРУЖЕН. Прецедент 21.08.2026: автолуп подменил
# knowledge/kodeksy/ симлинком, корпус исчез, и месячная сверка его НЕ вернула —
# она грепала только «ИЗМЕНИЛОСЬ» и пропавшие акты не видела вовсе. Задание,
# которое обновляет, но не лечит, оставляет систему без дословных цитат закона
# на неопределенный срок: cite.py молчит «корпус не выгружен», а это ловится
# только случайно.
CHANGED=$(echo "$CHECK_OUT" | grep -E "ИЗМЕНИЛОСЬ|НЕ ВЫГРУЖЕН" | sed -E 's/^([a-z0-9-]+):.*/\1/')
UPDATED=0
FAILED=0
if [[ -n "$CHANGED" ]]; then
    while IFS= read -r slug; do
        [[ -z "$slug" ]] && continue
        # Пропавший акт поднимается --init, изменившийся — --update.
        if [ -f "knowledge/kodeksy/$slug.md" ]; then REZHIM="--update"; else REZHIM="--init"; fi
        if "$PY" scripts/update_legal_corpus.py "$REZHIM" --doc "$slug"; then
            UPDATED=$((UPDATED + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    done <<< "$CHANGED"
fi

mkdir -p "$(dirname "$LOG")"
echo "- $STAMP — ежемесячная сверка корпуса: обновлено $UPDATED, ошибок $FAILED (детали — построчно выше в этом же логе)" >> "$LOG"

# Кадровые формы стареют вместе с корпусом: изменилась статья ТК — устарел
# шаблон, который ее цитирует. Проверка идет ПОСЛЕ обновления кодексов, иначе
# сверялась бы со вчерашним текстом закона. Код 1 = расхождение: скрипт называет
# затронутые шаблоны поименно. Автоматически ничего не фиксируется — «--stamp»
# означает «человек пересмотрел форму», и машина такого решения не принимает.
KADRY_OUT=$("$PY" scripts/kadry.py --check 2>&1)
KADRY_RC=$?
echo "$KADRY_OUT"
if [ "$KADRY_RC" -ne 0 ]; then
    echo "- $STAMP — кадровые формы: ТРЕБУЕТСЯ ПЕРЕСМОТР" >> "knowledge/kadry/_kadry_log.md"
    echo "$KADRY_OUT" | sed 's/^/    /' >> "knowledge/kadry/_kadry_log.md"
    echo "- $STAMP — кадровые формы разошлись с корпусом права, детали в knowledge/kadry/_kadry_log.md" >> "$LOG"
else
    echo "- $STAMP — кадровые формы: расхождений с корпусом нет" >> "knowledge/kadry/_kadry_log.md"
fi

# Последней командой был echo в лог, и скрипт всегда завершался нулем: launchd
# считал провальный прогон успешным. Код возврата обязан отражать результат.
if [ "${FAILED:-0}" -gt 0 ]; then
    echo "- $STAMP — ПРОВАЛ: ошибок $FAILED, требуется разбор" >> "$LOG"
    exit 1
fi
exit 0
