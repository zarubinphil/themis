#!/bin/bash
# morning-briefing.sh — утренняя сводка: ближайшие заседания и inbox
#
# Запускается через launchd каждый день в 9:00.
# Вывод попадает в уведомление macOS через osascript.


# Интерпретатор выбирается явно: launchd не даёт логин-шелл, и `python3`
# из системного PATH — это 3.9, на которой падает импорт (прецедент 21.08.2026).

# Корень репозитория берём от расположения самого скрипта, а не жёсткой
# строкой: у другого юриста Фемида лежит не в $ROOT, и фоновые
# задания молча ломались бы на несуществующем пути (аудит 21.08.2026).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

. "$(dirname "$0")/_python.sh"

CASES="$ROOT/cases"
INBOX="$HOME/Desktop/inbox"
LOG="$ROOT/audit.log"
DAYS_AHEAD=7

TODAY=$(date +%Y-%m-%d)
HORIZON=$(date -v+${DAYS_AHEAD}d +%Y-%m-%d)

echo "$(date '+%Y-%m-%d %H:%M') | BRIEFING | Старт" >> "$LOG"

# --- Собрать ближайшие заседания ---
# Ищем папки 02_hearings/ГГГГ-ММ-ДД_* и 02_hearings/ДД-ММ-ГГГГ_* во всех делах:
# второй формат пишется руками по шаблону события, без него часть заседаний терялась.
HEARINGS=""
while IFS= read -r -d '' dir; do
    DIRNAME=$(basename "$dir")
    DATE_PART=$(echo "$DIRNAME" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
    if [[ -z "$DATE_PART" ]]; then
        # Формат ДД-ММ-ГГГГ_ — приводим к ГГГГ-ММ-ДД для сравнения с горизонтом
        RU_DATE=$(echo "$DIRNAME" | grep -o '^[0-9]\{2\}-[0-9]\{2\}-[0-9]\{4\}')
        [[ -n "$RU_DATE" ]] && DATE_PART="${RU_DATE:6:4}-${RU_DATE:3:2}-${RU_DATE:0:2}"
    fi
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
done < <(find "$CASES" -type d \( -name "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*" -o -name "[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]_*" \) -print0 2>/dev/null)

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

# --- Напоминание в Telegram (этап 8) ---
# Единственный внешний канал уведомлений. Наружу уходят только даты и счёт:
# имена доверителей и номера дел остаются в локальном уведомлении выше.
# Секрет читается из ~/.secrets и в лог не попадает. Бот выключен либо секрета
# нет — молча пропускаем: система от бота не зависит.
BOT="$ROOT/scripts/themis_bot.py"
BOT_SECRET="$HOME/.secrets/themis-telegram.env"
if [[ -f "$BOT_SECRET" ]]; then
    set -a; . "$BOT_SECRET"; set +a
    if "$PY" "$BOT" --check >/dev/null 2>&1; then
        "$PY" "$BOT" --notify-hearings --days "$DAYS_AHEAD" >> "$LOG" 2>&1
    fi
fi

# --- Если есть входящие — запустить inbox-triage автоматически ---
if [[ "$INBOX_COUNT" -gt 0 ]]; then
    echo "$(date '+%Y-%m-%d %H:%M') | BRIEFING | Запускаю inbox-triage для $INBOX_COUNT файлов" >> "$LOG"
    "$ROOT/scripts/inbox-watcher.sh"
fi
