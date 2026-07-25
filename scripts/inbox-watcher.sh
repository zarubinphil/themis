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

# Метки времени в помощь группировке (урок 19.07.2026: мультидельный инбокс)
FILES_TS=$(echo "$FILES" | while IFS= read -r f; do
    stat -f '%Sm  %N' -t '%Y-%m-%d %H:%M' "$f" 2>/dev/null || echo "????-??-?? ??:??  $f"
done)

PROMPT="Используй агент inbox-triage. Разложи входящие файлы из папки: $INBOX
Индекс дел: $CASES/_index.md
Папка дел: $CASES
Папка unsorted: $UNSORTED

ЖЕСТКИЕ ПРАВИЛА (уроки боевых прогонов, нарушение = авария):
1. Инбокс может содержать файлы РАЗНЫХ дел. Сгруппируй по времени поступления
   (метки ниже, окно ~1 час) и тематике (первая страница через
   scripts/markdown_extract.py). НЕ переносить весь инбокс в одно дело.
2. Переносить только группы, уверенно привязанные к СУЩЕСТВУЮЩЕМУ делу
   из _index.md. Новое дело в автоматическом режиме НЕ создавать —
   такие файлы в $UNSORTED + заметка *.note.md рядом с причиной.
3. Сомнение в принадлежности → $UNSORTED. Смешивать дела запрещено.
4. В 00_intake/ существующих дел только добавлять, ничего не менять.

Файлы (время поступления · путь):
$FILES_TS"

"$CLAUDE" --print "$PROMPT" 2>> "$LOG"

echo "$(date '+%Y-%m-%d %H:%M') | INBOX | Обработка завершена" >> "$LOG"
