#!/bin/bash
# Themis self-update — тянет последнюю ЛОГИКУ из GitHub.
# НЕ трогает пользовательские данные: cases/ (дела) и knowledge/ (накопленная практика, redlines, уроки).
# Обновляет только систему: агенты, скиллы, команды, скрипты, cockpit, протокол, инсталлятор.
# Доустанавливает новые зависимости и пересобирает OCR при изменении исходника.
set -e
cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  echo "✗ Это не git-клон Themis. Обнови вручную: git clone и перенеси cases/ + knowledge/."
  exit 1
fi

echo "Проверяю обновления Themis на GitHub…"
git fetch origin --quiet
BR="$(git remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')"; BR="${BR:-main}"
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BR" 2>/dev/null || echo "$LOCAL")"

if [ "$LOCAL" = "$REMOTE" ]; then
  echo "✓ Уже последняя версия ($(git rev-parse --short HEAD))."
  exit 0
fi

echo "Есть обновление: $(git rev-parse --short "$LOCAL") → $(git rev-parse --short "$REMOTE")."
echo "Запоминаю исходник OCR (для пересборки при изменении)…"
OCR_BEFORE="$(shasum bin/vision-ocr.swift 2>/dev/null | awk '{print $1}')"

# Только СИСТЕМНЫЕ пути. cases/ и knowledge/ НЕ перечислены → не трогаются.
SYS=(.claude AGENTS.md scripts cockpit bin install.sh README.md .mcp.json .gitignore LICENSE CONTRIBUTING.md docs)
echo "Обновляю логику (данные дел и базу знаний не трогаю)…"
for p in "${SYS[@]}"; do
  git checkout "origin/$BR" -- "$p" 2>/dev/null || true
done

# Новые/изменённые зависимости и OCR — доустановить
OCR_AFTER="$(shasum bin/vision-ocr.swift 2>/dev/null | awk '{print $1}')"
echo "Доустанавливаю новые компоненты…"
bash install.sh

echo ""
echo "✓ Логика обновлена до $(git rev-parse --short "$REMOTE")."
echo "  Данные дел (cases/) и база знаний (knowledge/) НЕ тронуты."
[ "$OCR_BEFORE" != "$OCR_AFTER" ] && echo "  OCR-движок пересобран (исходник изменился)."
