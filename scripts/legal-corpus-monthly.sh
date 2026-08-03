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

LOG="knowledge/_corpus_log.md"
STAMP=$(date '+%d.%m.%Y %H:%M')

CHECK_OUT=$(python3 scripts/update_legal_corpus.py --check 2>&1)
echo "$CHECK_OUT"

CHANGED=$(echo "$CHECK_OUT" | grep "ИЗМЕНИЛОСЬ" | sed -E 's/^([a-z0-9-]+):.*/\1/')
UPDATED=0
FAILED=0
if [[ -n "$CHANGED" ]]; then
    while IFS= read -r slug; do
        [[ -z "$slug" ]] && continue
        if python3 scripts/update_legal_corpus.py --update --doc "$slug"; then
            UPDATED=$((UPDATED + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    done <<< "$CHANGED"
fi

mkdir -p "$(dirname "$LOG")"
echo "- $STAMP — ежемесячная сверка корпуса: обновлено $UPDATED, ошибок $FAILED (детали — построчно выше в этом же логе)" >> "$LOG"

# Последней командой был echo в лог, и скрипт всегда завершался нулём: launchd
# считал провальный прогон успешным. Код возврата обязан отражать результат.
if [ "${FAILED:-0}" -gt 0 ]; then
    echo "- $STAMP — ПРОВАЛ: ошибок $FAILED, требуется разбор" >> "$LOG"
    exit 1
fi
exit 0
