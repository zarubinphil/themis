#!/bin/bash
# fetch_url.sh — бесплатное чтение веб-страницы для охотников (каскад $0).
#
# Порядок: 1) прямой curl (10 c, браузерный UA — суд. сайты режут ботов)
#          2) фолбэк Jina Reader (r.jina.ai — рендерит JS, отдает markdown)
# Только ПУБЛИЧНЫЕ URL (судебные акты, реестры). Данные клиентов не передавать.
#
# Использование: scripts/fetch_url.sh "https://vsrf.ru/..." [out.md]
set -o pipefail

URL="$1"
OUT="${2:-/dev/stdout}"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

[[ -z "$URL" ]] && { echo "usage: fetch_url.sh URL [out]" >&2; exit 1; }

body=$(curl -sL --max-time 10 -A "$UA" "$URL" 2>/dev/null)
# страница считается живой, если в ней есть осмысленный текст (>500 байт не-разметки)
text_len=$(printf '%s' "$body" | sed 's/<[^>]*>//g' | tr -d '[:space:]' | wc -c | xargs)

if [[ -n "$body" && "$text_len" -gt 500 ]]; then
    printf '%s\n' "$body" > "$OUT"
    echo "OK direct ($text_len байт текста)" >&2
    exit 0
fi

# Фолбэк: Jina Reader (бесплатно, без ключа; вернет markdown)
body=$(curl -sL --max-time 25 "https://r.jina.ai/$URL" 2>/dev/null)
if [[ -n "$body" && $(printf '%s' "$body" | wc -c) -gt 300 ]]; then
    printf '%s\n' "$body" > "$OUT"
    echo "OK jina" >&2
    exit 0
fi

echo "FAIL: $URL недоступен ни прямым curl, ни через Jina Reader" >&2
exit 2
