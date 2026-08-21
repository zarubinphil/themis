#!/bin/bash
# redline-watch.sh — еженедельный разбор правок доверителя.
#
# Запускается через launchd по понедельникам в 12:00.
# Логика дешевая: поиск правок делает python (сравнение байтов, ноль токенов),
# модель зовется ТОЛЬКО если правки есть. Тихая неделя стоит ноль.
#
# Ручной запуск:  bash scripts/redline-watch.sh
#                 bash scripts/redline-watch.sh --dry   (только показать, не звать модель)

set -uo pipefail

# launchd запускает с голым PATH (/usr/bin:/bin:/usr/sbin:/sbin) — claude и python3
# из Framework/Homebrew там не резолвятся, и еженедельный разбор молча не стартует.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH"


# Интерпретатор выбирается явно: launchd не даёт логин-шелл, и `python3`
# из системного PATH — это 3.9, на которой падает импорт (прецедент 21.08.2026).
. "$(dirname "$0")/_python.sh"

ROOT="$HOME/Проекты/themis"
LOG="$ROOT/audit.log"
DRY=0
[[ "${1:-}" == "--dry" ]] && DRY=1

cd "$ROOT" || exit 0
ts() { date '+%d.%m.%Y %H:%M'; }

FOUND=$("$PY" scripts/redline_watch.py --days 8 --json)
COUNT=$(echo "$FOUND" | "$PY" -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)

if [[ "$COUNT" -eq 0 ]]; then
    echo "$(ts) | REDLINE | правок за неделю нет" >> "$LOG"
    exit 0
fi

# Список дел без повторов — разбираем по делу, а не по документу:
# правки одного дела разбираются одним проходом, это дешевле.
CASES=$(echo "$FOUND" | "$PY" -c "
import json,sys
print('\n'.join(sorted({d['case'] for d in json.load(sys.stdin)})))")

echo "$(ts) | REDLINE | документов с правками: $COUNT, дел: $(echo "$CASES" | wc -l | tr -d ' ')" >> "$LOG"

osascript -e "display notification \"Правок доверителя за неделю: $COUNT\" with title \"Фемида — разбор правок\" sound name \"Ping\"" 2>/dev/null

if [[ "$DRY" -eq 1 ]]; then
    "$PY" scripts/redline_watch.py --days 8
    exit 0
fi

# Модель зовется по одному делу за раз: короткий контекст дешевле одного длинного.
# Sonnet, не Opus: сравнение двух версий документа и запись правила — не синтез.
while IFS= read -r CASE; do
    [[ -z "$CASE" ]] && continue
    echo "$(ts) | REDLINE | разбор $CASE" >> "$LOG"
    claude -p --model sonnet \
        "Изучи правки доверителя по делу $CASE. Строго по knowledge/redlines.md: сравни выданные .docx из cases/$CASE/.agent/drafts/ с их снимками в .agent/drafts/_baselines/ через scripts/markdown_extract.py по двум осям — содержание и форматирование (эталон .claude/skills/doc-drafter/DOCX_FORMATTING.md). Уроки допиши в knowledge/redlines.md строками формата '- [ДД.ММ.ГГГГ · дело] правило', системные — в knowledge/lessons-log.md. Документы НЕ менять, снимки НЕ трогать. Если правки чисто косметические и правила из них не выводится — так и напиши одной строкой, пустых уроков не плодить." \
        >> "$LOG" 2>&1 || echo "$(ts) | REDLINE | сбой разбора $CASE" >> "$LOG"
done <<< "$CASES"

echo "$(ts) | REDLINE | готово" >> "$LOG"
