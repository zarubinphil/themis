#!/bin/bash
# Themis — Setup Script
# Installs Python dependencies for the Themis legal AI system

set -e

echo "=== Themis Setup ==="
echo ""

# Python dependencies
echo "[1/3] Installing Python dependencies..."
pip3 install --quiet \
  pymupdf \
  Pillow \
  markitdown \
  python-docx

echo "      ✓ pymupdf (PDF manipulation + OCR support)"
echo "      ✓ Pillow (image processing)"
echo "      ✓ markitdown (document → Markdown conversion)"
echo "      ✓ python-docx (DOCX reading)"

# Make scripts executable
echo "[2/3] Making scripts executable..."
chmod +x scripts/markdown_extract.py
chmod +x scripts/sign_and_pdf.py
echo "      ✓ scripts/markdown_extract.py"
echo "      ✓ scripts/sign_and_pdf.py"

# Create required directories
echo "[3/3] Creating required directories..."
mkdir -p cases/_assets cases/_logs knowledge
echo "      ✓ cases/_assets/   (place подпись.png here)"
echo "      ✓ cases/_logs/     (session error logs)"
echo "      ✓ knowledge/       (global practice index)"

# Initialize practice index if absent
if [ ! -f "knowledge/practice_index.md" ]; then
  cat > knowledge/practice_index.md << 'INDEXEOF'
# Индекс судебной практики

_Обновлено: — | Записей: 0_

## Содержание

| Категория | Записей | Обновлено |
|-----------|---------|-----------|

---
INDEXEOF
  echo "      ✓ knowledge/practice_index.md (created empty)"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Add your signature PNG: cases/_assets/подпись.png"
echo "     (PNG, ~400×130px, transparent or white background)"
echo "  2. Open a new Claude Code session in this directory"
echo "  3. Run /new-case {client} {case} to start your first case"
echo ""
echo "Requirements:"
echo "  • Microsoft Word (for /finalize PDF export)"
echo "  • Claude Code with claude-sonnet-4-6 or higher"
