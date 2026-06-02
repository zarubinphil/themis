# Themis — AI-Powered Legal Case Management System

Themis is a multi-agent AI system for managing litigation in Russian courts, built on Claude Code. It automates the full pipeline from document ingestion to court-ready submissions — case mapping, legal research, council deliberation, drafting, review, and PDF signing.

---

## What Themis Does

```
Incoming files
     ↓
Грузенберг    mv → 00_intake/
     ↓
Мейер (case-mapper)    parallel readers → knowledge-map.md
  ├── Покровский  (DOCX, text-PDF → markitdown)
  ├── Гольмстен  (scan-PDF → OCR)
  └── Буринский  (images → vision)
       ↓ Шершеневич (reconciler, 2 rounds)
     ↓
Three practice hunters (parallel, L2/L3):
  ├── Спасович   (authoritative: Пленум ВС → Коллегии → округа)
  ├── Карабчевский (adversarial: practice against us)
  └── Плевако    (procedural: deadlines, evidence, jurisdiction)
       ↓ /askacouncil (anonymous cross-review → practice.md)
     ↓
/council — Ареопаг (5 jurists, 3 rounds → positions.md):
  ├── Муравьев   (Prosecutor — max aggression)
  ├── Маклаков   (Devil's Advocate — find weaknesses)
  ├── Фойницкий  (Proceduralist — form & deadlines)
  ├── Владимиров (Evidence specialist — what's proven)
  └── Урусов     (Tactician, chair — final word)
     ↓
Сперанский (doc-drafter)    → .md + .docx in 02_hearings/
     ↓
Кони (doc-reviewer)    → 7-block verification
     ↓
/finalize    → PDF + signature overlay
```

---

## Case Levels

| Level | When | Hunters | /askacouncil | /council | Model |
|-------|------|---------|--------------|----------|-------|
| L1 | Simple, 1 issue | Tactical only | — | — | Sonnet |
| L2 | Standard claim, 2-3 issues | All three | ✓ | ✓ (skippable) | Opus |
| L3 | Cassation, КАС, lost case | All three | ✓ | Required | Opus |

---

## Agents (13)

| Pseudonym | Agent | Model | Role |
|-----------|-------|-------|------|
| Грузенберг | inbox-triage | Haiku | mv files from Desktop/inbox/ → 00_intake/ |
| Мейер | case-mapper | Sonnet | Orchestrates parallel reading + reconciliation |
| Покровский | docx-reader | Haiku | DOCX, XLSX, text-PDF via markitdown |
| Гольмстен | pdf-reader | Sonnet | Scan-PDF: render → OCR via vision |
| Буринский | image-reader | Sonnet | JPG/PNG/TIFF via vision |
| Шершеневич | case-reconciler | Sonnet | Conflict resolution, up to 2 rounds |
| Спасович | practice-hunter-classic | Sonnet | Authoritative practice in our favor |
| Карабчевский | practice-hunter-skeptic | Opus | Adversarial — practice against us |
| Плевако | practice-hunter-tactical | Sonnet | Procedural: deadlines, evidence, jurisdiction |
| Рождественский | archivist | Haiku | Maintains global practice index + client profiles |
| Сперанский | doc-drafter | Opus/Sonnet | Drafts court documents |
| Кони | doc-reviewer | Sonnet | 7-block pre-submission verification |
| Андреевский | hearing-prep | Sonnet | Prepares for court hearings |

---

## Knowledge Base

Themis builds a cross-case practice database that grows over time:

```
knowledge/
  practice_index.md    ← global index, auto-updated by Рождественский
                         after each hunt and /askacouncil

cases/{client}/
  _client.md           ← live client profile (family, business, assets, all cases)

cases/
  _clients.md          ← lightweight routing registry
  _relationships.md    ← cross-client connection matrix
```

**Practice search order (within each hunter leaf):**
1. `practice_context.md` — per-case extract from global index
2. `grep knowledge/practice_index.md` — direct global search
3. External: Firecrawl → ScrapeGraphAI → WebSearch

---

## Document Pipeline

```
03_drafts/               ← working files only (v1, v2, iterations)
  {document}_v1.md
  {document}_v1.docx

02_hearings/{date}/      ← final files only (after Кони: ГОТОВ К ПОДАЧЕ)
  {document}.md
  {document}.docx
  {document}.pdf         ← after /finalize (Word export + signature overlay)
```

---

## Commands

| Command | Function |
|---------|----------|
| `/new-case {client} {case}` | Create case structure, move inbox, run case-mapper |
| `/new-event` | Create hearing folder in 02_hearings/ |
| `/draft {type} for {client}/{case}` | Draft court document (checks consensus markers) |
| `/finalize {docx_path}` | Export to PDF + overlay signature |
| `/askacouncil {case_path}` | Anonymous cross-review of hunter reports → practice.md |
| `/council {case_path}` | Five-jurist council → positions.md |
| `/init-cases` | Build knowledge maps for unmapped cases |
| `/init-practice` | Run hunters for cases without practice.md |
| `/close-case` | Archive case, update indices |

---

## Case Structure

```
cases/
  {client}/
    _client.md                  ← live client profile
    {case}/
      00_intake/                ← source files (immutable)
      01_context/
        _working/               ← reader fragments, reconcile reports
        _practice/              ← hunter_*.md
        _council/               ← council round files
        knowledge-map.md        ← [КАРТА ГОТОВА ✓]
        practice_context.md     ← per-case extract from global index
        practice.md             ← [СОВЕТ ЗАВЕРШЁН ✓]
        positions.md            ← [СОГЛАСОВАНО СОВЕТОМ]
      02_hearings/
        {date}_name/
          _event.md
          {document}.md
          {document}.docx
          {document}.pdf
      03_drafts/                ← working versions
      04_archive/
```

---

## Consensus Markers (enforced by doc-drafter)

| Marker | File | Required for |
|--------|------|--------------|
| `## КАРТА ГОТОВА ✓` | knowledge-map.md | Always |
| `## СОВЕТ ЗАВЕРШЁН ✓` | practice.md | Always |
| `СОГЛАСОВАНО СОВЕТОМ` | positions.md | L2/L3 only |

---

## Full Dependency Stack

Themis builds on top of several tools. Everything below is required for full functionality.

---

### 1. Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

Models used:
| Agent | Model |
|-------|-------|
| doc-drafter (L2/L3), practice-hunter-skeptic | `claude-opus-4-8` |
| case-mapper, hunters, reviewers | `claude-sonnet-4-6` |
| readers, archivist (leaf workers) | `claude-haiku-4-5` |

---

### 2. Python Packages

```bash
pip install pymupdf Pillow markitdown python-docx
```

| Package | Source | Used for |
|---------|--------|---------|
| [pymupdf](https://github.com/pymupdf/PyMuPDF) | pip | Scan-PDF rendering, signature overlay |
| [Pillow](https://github.com/python-pillow/Pillow) | pip | Signature image processing |
| [markitdown](https://github.com/microsoft/markitdown) | pip (Microsoft) | DOCX/XLSX/PPTX/PDF/HTML → Markdown, zero tokens |
| [python-docx](https://github.com/python-openxml/python-docx) | pip | DOCX reading |

---

### 3. Claude Code Community Skills

Two skills control how all 13 agents communicate and write. **Required.**

#### [caveman](https://github.com/eastcoastcode/ecc) — token-efficient communication
Compresses agent output ~65–75% while keeping full technical accuracy. Used for all inter-agent and user communication.

```bash
npx skills add caveman
```

#### humanizer — professional legal language
Transforms AI output into authentic professional prose. Used when writing court documents, practice reports, and case maps.

```bash
npx skills add humanizer
```

> Both skills are part of the **ECC (Engineering Claude Code)** skill pack.
> If `npx skills add` is unavailable, copy the skill folders manually to `~/.claude/skills/`.

---

### 4. MCP Servers

Configure in `~/.claude/mcp.json`.

#### Firecrawl — primary web search (required for practice hunters)

```json
{
  "mcpServers": {
    "firecrawl-mcp": {
      "command": "npx",
      "args": ["-y", "firecrawl-mcp@latest"],
      "env": { "FIRECRAWL_API_KEY": "YOUR_KEY_HERE" }
    }
  }
}
```

Get API key: [firecrawl.dev](https://firecrawl.dev)

#### ScrapeGraphAI — fallback search

```bash
pip install scrapegraphai
# CLI: sgai validate --json
```

Get API key: [scrapegraphai.com](https://scrapegraphai.com) → store in `~/.scrapegraphai/config.json`

#### markitdown MCP (optional — adds MCP-based document conversion)

```bash
pip install markitdown-mcp
```

Add to `~/.claude/mcp.json`:
```json
"markitdown": { "command": "python3", "args": ["-m", "markitdown_mcp"] }
```

---

### 5. Microsoft Word (macOS)

Required for `/finalize` — exports `.docx` → `.pdf` via AppleScript.
`Microsoft Word.app` must be installed in `/Applications/`.

**Linux/Windows alternative** — replace AppleScript block in `scripts/sign_and_pdf.py`:
```python
subprocess.run(["libreoffice", "--headless", "--convert-to", "pdf", docx_path])
```

---

### 6. Anthropic Built-in Skill

`anthropic-skills:docx` is used by the doc-drafter to generate `.docx` files.
Bundled with Claude Code — no separate install.

---

## Installation

```bash
git clone https://github.com/your-username/themis.git my-legal-practice
cd my-legal-practice

# 1. Python dependencies + directory setup
bash setup.sh

# 2. Community skills
npx skills add caveman
npx skills add humanizer

# 3. Configure MCP servers in ~/.claude/mcp.json (see above)

# 4. Signature for /finalize (optional)
#    Photograph your signature → crop → save as PNG:
#    cases/_assets/подпись.png  (~400×130px, transparent background)

# 5. Start
claude
```

---

## Enforcement

Themis enforces the full workflow. At session start it reads the last error log and reports pending fixes. At session end it analyzes errors, formatting issues, and failed steps — and proposes specific file-level corrections.

Doc formatting is strictly enforced: Times New Roman, 14/13/12pt, margins 30/15/20/20mm, justified body, right-aligned signature. Кони verifies formatting as block 6 before any document is declared final.

---

## License

MIT
