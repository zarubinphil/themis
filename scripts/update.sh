#!/bin/bash
# Themiz self-update — тянет последнюю ЛОГИКУ из GitHub.
# НЕ трогает пользовательские данные: cases/ (дела) и knowledge/ (накопленная практика, redlines, уроки).
# Обновляет только систему: агенты, скиллы, команды, скрипты, cockpit, протокол, инсталлятор.
# Доустанавливает новые зависимости и пересобирает OCR при изменении исходника.
set -e
cd "$(dirname "$0")/.."
. scripts/sreda.sh
# Переходный период имени: состояние прошлой установки переезжает один раз.
PREZHNIJ_DOM="$HOME/.$(printf '%s' 'themiz' | tr 'z' 's')"
if [ -d "$PREZHNIJ_DOM" ] && [ ! -d "$HOME/.themiz" ]; then
  mv "$PREZHNIJ_DOM" "$HOME/.themiz"
  echo "состояние прошлой установки переехало: $PREZHNIJ_DOM → $HOME/.themiz"
fi

if [ ! -d .git ]; then
  echo "✗ Это не git-клон Themiz. Обнови вручную: git clone и перенеси cases/ + knowledge/."
  exit 1
fi

# Переходный период имени: состояние и кеш прошлой установки переезжают один раз.
# Прежнее имя получается заменой буквы, а не литералом — иначе следующая массовая
# замена имени съест этот переезд вместе со всем остальным.
pereezd_prezhnego() {
  prezhnij="$1"; novyj="$2"
  [ -d "$prezhnij" ] || return 0
  # Пустой новый каталог переездом не считается: его мог создать любой прибор
  # до первого запуска, и тогда данные прошлой установки осиротели бы молча.
  if [ -d "$novyj" ] && [ -n "$(ls -A "$novyj" 2>/dev/null)" ]; then
    echo "  ⚠ $novyj не пуст — $prezhnij оставлен как есть, перенести вручную"
    return 0
  fi
  rmdir "$novyj" 2>/dev/null
  mv "$prezhnij" "$novyj" && echo "  ✓ переехало: $prezhnij → $novyj"
  return 0
}
IMYA_PREZHNEE="$(printf '%s' 'themiz' | tr 'z' 's')"
pereezd_prezhnego "$HOME/.$IMYA_PREZHNEE" "$HOME/.themiz"
pereezd_prezhnego "$HOME/.cache/$IMYA_PREZHNEE" "$HOME/.cache/themiz"

echo "Проверяю обновления Themiz на GitHub…"
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
SYS=(.claude AGENTS.md scripts cockpit bin install.sh README.md .mcp.json .gitignore LICENSE LICENSE.ru.md CONTRIBUTING.md docs)
echo "Обновляю логику (данные дел и базу знаний не трогаю)…"
for p in "${SYS[@]}"; do
  git checkout "origin/$BR" -- "$p" 2>/dev/null || true
done

# Новые/измененные зависимости и OCR — доустановить
OCR_AFTER="$(shasum bin/vision-ocr.swift 2>/dev/null | awk '{print $1}')"
echo "Доустанавливаю новые компоненты…"
bash install.sh

echo ""
echo "✓ Логика обновлена до $(git rev-parse --short "$REMOTE")."
echo "  Данные дел (cases/) и база знаний (knowledge/) НЕ тронуты."
if [ "$OCR_BEFORE" != "$OCR_AFTER" ]; then
  echo "  OCR-движок пересобран (исходник изменился)."
fi

# Код возврата — вердикт обновления, а не результат последней проверки:
# скилл themiz-update на код != 0 предписывает ОТКАТ, и удачное обновление
# откатывалось всегда (проба круга 9).
exit 0
