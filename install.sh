#!/bin/bash
# Themis — установка «под ключ». Запуск из корня проекта: bash install.sh
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
echo "════════════════════════════════════════════"
echo "  Themis — установка"
echo "════════════════════════════════════════════"

# ── 0. Платформа ─────────────────────────────────────────────────────────────
if [ "$(uname)" != "Darwin" ]; then
  echo "⚠  Apple Vision OCR работает только на macOS."
  echo "   На других ОС OCR-движок недоступен (текст/документы будут работать через markitdown)."
fi

# ── 1. Python-зависимости ────────────────────────────────────────────────────
echo ""
echo "[1/6] Python-пакеты…"
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
echo "[2/6] Apple Vision OCR…"
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
echo "[3/6] ffmpeg (для расшифровки медиа)…"
if command -v ffmpeg >/dev/null 2>&1; then
  echo "      ✓ ffmpeg есть"
elif command -v brew >/dev/null 2>&1; then
  brew install ffmpeg >/dev/null 2>&1 && echo "      ✓ ffmpeg установлен" || echo "      ⚠ поставь вручную: brew install ffmpeg"
else
  echo "      ⚠ нет brew — поставь ffmpeg вручную (нужен только для аудио/видео)"
fi

# ── 4. Права на скрипты ──────────────────────────────────────────────────────
echo ""
echo "[4/6] Права на скрипты…"
chmod +x scripts/*.py 2>/dev/null || true
echo "      ✓ scripts/*.py исполняемы"

# ── 5. Директории рантайма ───────────────────────────────────────────────────
echo ""
echo "[5/6] Директории…"
mkdir -p cases/_logs cases/_assets knowledge "$HOME/Desktop/inbox"
echo "      ✓ cases/_logs, cases/_assets, knowledge, ~/Desktop/inbox"

# ── 6. Проверка Claude Code CLI ──────────────────────────────────────────────
echo ""
echo "[6/6] Claude Code CLI…"
if command -v claude >/dev/null 2>&1; then
  echo "      ✓ claude найден: $(command -v claude)"
else
  echo "      ⚠ claude CLI не найден. Установи Claude Code: https://claude.com/claude-code"
  echo "        Themis работает поверх него (агенты, протокол, cockpit запускает claude -p)."
fi

echo ""
echo "════════════════════════════════════════════"
echo "  Готово. Дальше:"
echo "  • Cockpit (UI):   python3 cockpit/app.py  → http://localhost:8800"
echo "  • Или в Claude Code: открой проект, скажи «новое дело …»"
echo "  • Обновление:     /themis-update  (тянет последнюю версию логики)"
echo "  Данные дел в cases/ остаются ЛОКАЛЬНО и не публикуются."
echo "════════════════════════════════════════════"
