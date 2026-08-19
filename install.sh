#!/bin/bash
# Themis — установка «под ключ». Запуск из корня проекта: bash install.sh
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
echo "════════════════════════════════════════════"
echo "  Themis — установка"
echo "════════════════════════════════════════════"

# SMLTLK — штатный компонент (диктовка → voice-to-brief → бриф задачи), но его
# сборка тянет Xcode и ~500 МБ модели распознавания. Молча этого не делаем:
# ставится только по явному флагу. Без флага — говорим, что компонент есть
# и как его получить; молчание запрещено.
WITH_SMLTLK=0
for arg in "$@"; do
  case "$arg" in
    --with-smltlk) WITH_SMLTLK=1 ;;
    --help|-h)
      echo "bash install.sh [--with-smltlk]"
      echo "  --with-smltlk   собрать и поставить SMLTLK (диктовка, только macOS;"
      echo "                  нужен Xcode со Swift 6 и ~500 МБ под модель)"
      exit 0 ;;
  esac
done

# ── 0. Платформа ─────────────────────────────────────────────────────────────
if [ "$(uname)" != "Darwin" ]; then
  echo "⚠  Apple Vision OCR работает только на macOS."
  echo "   На других ОС OCR-движок недоступен (текст/документы будут работать через markitdown)."
fi

# ── 1. Python-зависимости ────────────────────────────────────────────────────
echo ""
echo "[1/7] Python-пакеты…"
PIP="pip3"
$PIP install --quiet --upgrade \
  pymupdf Pillow markitdown python-docx markitdown-mcp \
  fastapi uvicorn openai-whisper 2>/dev/null || \
$PIP install pymupdf Pillow markitdown python-docx markitdown-mcp fastapi uvicorn openai-whisper
echo "      ✓ извлечение: pymupdf, Pillow, markitdown, python-docx"
echo "      ✓ cockpit:    fastapi, uvicorn"
echo "      ✓ медиа:      openai-whisper (расшифровка аудио/видео)"

# ── 2. Apple Vision OCR (сборка из исходника) ────────────────────────────────
echo ""
echo "[2/7] Apple Vision OCR…"
if [ "$(uname)" = "Darwin" ] && command -v swiftc >/dev/null 2>&1; then
  mkdir -p bin
  swiftc -O bin/vision-ocr.swift -o bin/vision-ocr && chmod +x bin/vision-ocr
  echo "      ✓ собран bin/vision-ocr (локальный OCR, \$0)"
else
  echo "      ⚠ swiftc не найден — поставь Xcode CLT: xcode-select --install"
  echo "        затем: swiftc -O bin/vision-ocr.swift -o bin/vision-ocr"
fi

# ── 3. ffmpeg (для whisper) ──────────────────────────────────────────────────
echo ""
echo "[3/7] ffmpeg (для расшифровки медиа)…"
if command -v ffmpeg >/dev/null 2>&1; then
  echo "      ✓ ffmpeg есть"
elif command -v brew >/dev/null 2>&1; then
  brew install ffmpeg >/dev/null 2>&1 && echo "      ✓ ffmpeg установлен" || echo "      ⚠ поставь вручную: brew install ffmpeg"
else
  echo "      ⚠ нет brew — поставь ffmpeg вручную (нужен только для аудио/видео)"
fi

# ── 4. Права на скрипты ──────────────────────────────────────────────────────
echo ""
echo "[4/7] Права на скрипты…"
chmod +x scripts/*.py 2>/dev/null || true
echo "      ✓ scripts/*.py исполняемы"

# ── 5. Директории рантайма ───────────────────────────────────────────────────
echo ""
echo "[5/7] Директории…"
mkdir -p cases/_logs cases/_assets knowledge "$HOME/Desktop/inbox"
echo "      ✓ cases/_logs, cases/_assets, knowledge, ~/Desktop/inbox"

# ── 6. Проверка Claude Code CLI ──────────────────────────────────────────────
echo ""
echo "[6/7] Claude Code CLI…"
if command -v claude >/dev/null 2>&1; then
  echo "      ✓ claude найден: $(command -v claude)"
else
  echo "      ⚠ claude CLI не найден. Установи Claude Code: https://claude.com/claude-code"
  echo "        Themis работает поверх него (агенты, протокол, cockpit запускает claude -p)."
fi

# ── 6.5. Сторож персональных данных ──────────────────────────────────────────
# Инвариант «ПД не покидают cases/» держался текстом в конституции, а текст
# исполняется вероятностно. 04.08.2026 фамилии двух доверителей ушли в публичный
# репозиторий через комментарий в коде и сообщение коммита. Теперь это блокирует
# git-хук — детерминированно, а не по памяти.
echo ""
echo "[6.5] Сторож персональных данных…"
python3 scripts/pd_guard.py --install

# ── 7. Проверка фактом ───────────────────────────────────────────────────────
# Установщик не имеет права печатать «готово», не проверив. Доктор гоняет
# КОМАНДЫ (версии, импорты, запуск движка OCR, шрифты, каналы, десять selftest),
# а не предположения, и возвращает 1, если чего-то критичного нет.
echo ""
echo "[7/7] Проверка окружения…"
# ── SMLTLK (диктовка) ────────────────────────────────────────────────────────
echo ""
echo "[SMLTLK] диктовка задач…"
SMLTLK_SRC="$HOME/Проекты/smltlk"
if [ "$(uname)" != "Darwin" ]; then
  echo "      ⚠ SMLTLK — приложение строки меню macOS, здесь не запускается."
  echo "        Замена: локальный распознаватель речи (на Linux — whisper, он уже"
  echo "        поставлен выше); текст класть в скилл voice-to-brief как обычно."
elif [ "$WITH_SMLTLK" != "1" ]; then
  echo "      пропущено (нужен флаг --with-smltlk: сборка тянет Xcode и ~500 МБ модели)."
  echo "      Поставить позже: bash install.sh --with-smltlk"
elif [ ! -d "$SMLTLK_SRC" ]; then
  echo "      ⚠ исходников $SMLTLK_SRC нет — взять их у владельца проекта."
else
  bash "$SMLTLK_SRC/scripts/build_app.sh" && echo "      ✓ SMLTLK собран" || \
    echo "      ⚠ сборка SMLTLK не удалась — Фемида работает и без диктовки"
fi

python3 scripts/setup_doctor.py || DOCTOR_RC=$?

echo ""
echo "════════════════════════════════════════════"
if [ "${DOCTOR_RC:-0}" = "1" ]; then
  echo "  УСТАНОВКА НЕ ЗАВЕРШЕНА: доктор нашёл критичное (список выше)."
  echo "  Каждая красная строка несёт готовую команду починки."
else
  echo "  Готово. Дальше:"
fi
echo "  • Cockpit (UI):   python3 cockpit/app.py  → http://localhost:8800"
echo "  • Или в Claude Code: открой проект, скажи «новое дело …»"
echo "  • Обновление:     /themis-update  (тянет последнюю версию логики)"
echo "  Данные дел в cases/ остаются ЛОКАЛЬНО и не публикуются."
echo "════════════════════════════════════════════"
