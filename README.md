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

## Requirements

- **Claude Code** with claude-sonnet-4-6 or higher
- **Python 3.9+** with: `pymupdf`, `Pillow`, `markitdown`, `python-docx`
- **Microsoft Word** (macOS) — for `/finalize` PDF export
- **Firecrawl MCP** (recommended) or ScrapeGraphAI for legal research

---

## Installation

```bash
git clone https://github.com/your-username/themis.git my-legal-practice
cd my-legal-practice
bash setup.sh

# Add your signature (optional, for /finalize)
# Place PNG file: cases/_assets/подпись.png

# Open Claude Code
claude
```

---

## Enforcement

Themis enforces the full workflow. At session start it reads the last error log and reports pending fixes. At session end it analyzes errors, formatting issues, and failed steps — and proposes specific file-level corrections.

Doc formatting is strictly enforced: Times New Roman, 14/13/12pt, margins 30/15/20/20mm, justified body, right-aligned signature. Кони verifies formatting as block 6 before any document is declared final.

---

## License

MIT
