#!/bin/bash
# Themis — Setup Script
# Run from the project root: bash setup.sh

set -e
echo "=== Themis Setup ==="
echo ""

# ── 1. Python packages ────────────────────────────────────────────────────────
echo "[1/4] Installing Python packages..."
pip3 install --quiet pymupdf Pillow markitdown python-docx markitdown-mcp 2>/dev/null || \
  pip3 install pymupdf Pillow markitdown python-docx

echo "      ✓ pymupdf       (PyMuPDF — PDF rendering, signature overlay)"
echo "      ✓ Pillow        (image processing)"
echo "      ✓ markitdown    (Microsoft — DOCX/XLSX/PDF → Markdown, zero tokens)"
echo "      ✓ python-docx   (DOCX reading)"
echo "      ✓ markitdown-mcp (optional MCP server for markitdown)"

# ── 2. Scripts executable ─────────────────────────────────────────────────────
echo ""
echo "[2/4] Making scripts executable..."
chmod +x scripts/markdown_extract.py scripts/sign_and_pdf.py
echo "      ✓ scripts/markdown_extract.py"
echo "      ✓ scripts/sign_and_pdf.py"

# ── 3. Directories ────────────────────────────────────────────────────────────
echo ""
echo "[3/4] Creating required directories..."
mkdir -p cases/_assets cases/_logs knowledge
echo "      ✓ cases/_assets/   → place подпись.png here for /finalize"
echo "      ✓ cases/_logs/     → session error analysis logs"
echo "      ✓ knowledge/       → global practice index (auto-maintained)"

if [ ! -f "knowledge/practice_index.md" ]; then
  cat > knowledge/practice_index.md << 'EOF'
# Индекс судебной практики

_Обновлено: — | Записей: 0_

## Содержание

| Категория | Записей | Обновлено |
|-----------|---------|-----------|

---
EOF
  echo "      ✓ knowledge/practice_index.md (initialized empty)"
fi

# ── 4. Community skills check ─────────────────────────────────────────────────
echo ""
echo "[4/4] Checking community skills..."

CAVEMAN_PATH="$HOME/.claude/skills/caveman"
HUMANIZER_PATH="$HOME/.claude/skills/humanizer"

if [ -d "$CAVEMAN_PATH" ]; then
  echo "      ✓ caveman skill found at $CAVEMAN_PATH"
else
  echo "      ✗ caveman skill NOT found"
  echo "        Install: npx skills add caveman"
  echo "        Source:  https://github.com/eastcoastcode/ecc"
fi

if [ -d "$HUMANIZER_PATH" ]; then
  echo "      ✓ humanizer skill found at $HUMANIZER_PATH"
else
  echo "      ✗ humanizer skill NOT found"
  echo "        Install: npx skills add humanizer"
  echo "        Source:  https://github.com/eastcoastcode/ecc"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "Remaining manual steps:"
echo ""
echo "  1. Install community skills (if missing above):"
echo "     npx skills add caveman"
echo "     npx skills add humanizer"
echo ""
echo "  2. Configure MCP servers in ~/.claude/mcp.json:"
echo "     - firecrawl-mcp  (npx -y firecrawl-mcp@latest, needs FIRECRAWL_API_KEY)"
echo "     - scrapegraph    (pip install scrapegraphai, needs API key)"
echo "     See README.md for full config."
echo ""
echo "  3. Microsoft Word must be installed (macOS) for /finalize PDF export."
echo "     Linux/Windows: use LibreOffice (see README.md for substitution)."
echo ""
echo "  4. Add signature for /finalize:"
echo "     cases/_assets/подпись.png  (~400×130px PNG, transparent background)"
echo ""
echo "  5. Open Claude Code in this directory:"
echo "     claude"
echo ""
