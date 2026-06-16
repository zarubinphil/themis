#!/bin/bash
# inbox-watcher.sh — обрабатывает новые файлы в ~/Desktop/inbox/
#
# Запускается автоматически через launchd (WatchPaths) при появлении файлов.
# Для ручного запуска: ~/Проекты/themis/scripts/inbox-watcher.sh

INBOX="$HOME/Desktop/inbox"
UNSORTED="$INBOX/unsorted"
CASES="$HOME/Проекты/themis/cases"
LOG="$HOME/Проекты/themis/audit.log"
CLAUDE="$HOME/.local/bin/claude"
LOCK="$HOME/Проекты/themis/scripts/.inbox.lock"

mkdir -p "$INBOX" "$UNSORTED"

# Защита от параллельного запуска
if [[ -f "$LOCK" ]]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$LOCK") ))
    if [[ "$AGE" -lt 120 ]]; then
        exit 0
    fi
fi
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# Найти файлы в inbox (не в подпапках, не скрытые, не заметки)
FILES=$(find "$INBOX" -maxdepth 1 -type f \
    ! -name ".*" \
    ! -name "*.note.md" \
    ! -name "*.lock" \
    2>/dev/null)

if [[ -z "$FILES" ]]; then
    exit 0
fi

COUNT=$(echo "$FILES" | wc -l | xargs)
echo "$(date '+%Y-%m-%d %H:%M') | INBOX | Найдено файлов: $COUNT" >> "$LOG"

PROMPT="Используй агент inbox-triage. Разложи входящие файлы из папки: $INBOX
Индекс дел: $CASES/_index.md
Папка дел: $CASES
Папка unsorted: $UNSORTED

Файлы для обработки:
$FILES"

"$CLAUDE" --print "$PROMPT" 2>> "$LOG"

echo "$(date '+%Y-%m-%d %H:%M') | INBOX | Обработка завершена" >> "$LOG"
