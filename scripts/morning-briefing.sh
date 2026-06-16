#!/bin/bash
# morning-briefing.sh — утренняя сводка: ближайшие заседания и inbox
#
# Запускается через launchd каждый день в 9:00.
# Вывод попадает в уведомление macOS через osascript.

CASES="$HOME/Проекты/themis/cases"
INBOX="$HOME/Desktop/inbox"
LOG="$HOME/Проекты/themis/audit.log"
DAYS_AHEAD=7

TODAY=$(date +%Y-%m-%d)
HORIZON=$(date -v+${DAYS_AHEAD}d +%Y-%m-%d)

echo "$(date '+%Y-%m-%d %H:%M') | BRIEFING | Старт" >> "$LOG"

# --- Собрать ближайшие заседания ---
# Ищем папки 02_hearings/ГГГГ-ММ-ДД_* во всех делах
HEARINGS=""
while IFS= read -r -d '' dir; do
    DIRNAME=$(basename "$dir")
    DATE_PART=$(echo "$DIRNAME" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
    [[ -z "$DATE_PART" ]] && continue
    # Только если дата >= сегодня и <= горизонт
    if [[ "$DATE_PART" > "$TODAY" || "$DATE_PART" == "$TODAY" ]] && \
       [[ "$DATE_PART" < "$HORIZON" || "$DATE_PART" == "$HORIZON" ]]; then
        # Определить дело по пути
        CASE_PATH=$(dirname "$dir")  # .../cases/client/case/02_hearings
        CASE_PATH=$(dirname "$CASE_PATH")  # .../cases/client/case
        CLIENT=$(basename "$(dirname "$CASE_PATH")")
        CASE=$(basename "$CASE_PATH")
        LABEL=$(echo "$DIRNAME" | sed 's/^[0-9-]*_//' | tr '-' ' ')
        HEARINGS="$HEARINGS\n  • $DATE_PART — $CLIENT / $CASE ($LABEL)"
    fi
done < <(find "$CASES" -type d -name "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*" -print0 2>/dev/null)

# --- Inbox ---
INBOX_COUNT=$(find "$INBOX" -maxdepth 1 -type f ! -name ".*" ! -name "*.note.md" 2>/dev/null | wc -l | xargs)
UNSORTED_COUNT=$(find "$INBOX/unsorted" -type f 2>/dev/null | wc -l | xargs)

# --- Формируем текст ---
MSG="ЮРИДИЧЕСКАЯ ПРАКТИКА — $(date '+%d.%m.%Y')"
BODY=""

if [[ -n "$HEARINGS" ]]; then
    BODY="Заседания (${DAYS_AHEAD} дней):$(echo -e "$HEARINGS")\n"
else
    BODY="Заседаний в ближайшие ${DAYS_AHEAD} дней нет.\n"
fi

if [[ "$INBOX_COUNT" -gt 0 ]]; then
    BODY="${BODY}Входящих файлов: $INBOX_COUNT"
    [[ "$UNSORTED_COUNT" -gt 0 ]] && BODY="${BODY} (нераспознанных: $UNSORTED_COUNT)"
fi

echo "$(date '+%Y-%m-%d %H:%M') | BRIEFING | $MSG" >> "$LOG"
echo -e "$BODY" >> "$LOG"

# --- Уведомление macOS ---
osascript -e "display notification \"$(echo -e "$BODY")\" with title \"$MSG\" sound name \"default\""

# --- Если есть входящие — запустить inbox-triage автоматически ---
if [[ "$INBOX_COUNT" -gt 0 ]]; then
    echo "$(date '+%Y-%m-%d %H:%M') | BRIEFING | Запускаю inbox-triage для $INBOX_COUNT файлов" >> "$LOG"
    "$HOME/Проекты/themis/scripts/inbox-watcher.sh"
fi
