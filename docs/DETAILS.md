# Themiz in detail

[Русский](DETAILS.ru.md) · [中文](DETAILS.zh.md) · [the short page](../README.md)

<p align="center"><img src="assets/pantheon/hero.png" alt="Themis in white marble with balanced scales and a lowered sword beside the classical column, case documents and review cards laid out in daylight" width="100%"></p>

This is the long page, for anyone who wants the whole picture. The short one is the
[README](../README.md); the inside view is [How it works](HOW-IT-WORKS.en.md).

## What it does

<p align="center"><img src="assets/pantheon/workflow/03-case-map.png" alt="Case map: the glass plates laid into one connected map, blue threads joining parties, dates and claims" width="100%"></p>

Themiz takes the mechanical part of a case — the part that eats evenings and needs
no legal judgement.

- **Reads the material on your own computer.** PDF, Word, Excel, photographs and
  scans. Recognition is local; the pages do not travel to someone else's cloud.
- **Builds a case map.** Parties, amounts, dates, documents, risks and procedural
  steps in one place, each with a link back to the page it came from.
- **Counts with a program, not by eye.** Interest, court fees, procedural deadlines,
  the check digits of company identifiers.
- **Hunts practice for you and against you.** The two sides separately, so you see
  the weak spot before your opponent does.
- **Drafts a document and hands it to a different agent for review.** The one who
  wrote it does not get to approve it.

The decision stays with the lawyer. That is how the system is built, not a line of
small print at the bottom.

## How it goes, step by step

<p align="center"><img src="assets/pantheon/takt-en.png" alt="The Themiz workflow in one wide marble scene: seven labelled steps from Intake to Hearing, linked by a blue thread beside the classical column" width="100%"></p>

Seven steps in order: intake, reading, the case map, practice, a council of five
jurists, the document, the hearing. Each one is taken apart in
[How it works](HOW-IT-WORKS.en.md).

## Quick start

<p align="center"><img src="assets/pantheon/workflow/01-intake.png" alt="Intake: the closed gold client folder under Themis's flat hand, the blue thread of outside sources stopping at the table edge" width="100%"></p>

```bash
git clone https://github.com/zarubinvibe/themiz.git
cd themiz
bash install.sh

# then open it with whatever you already use:
claude                   # Claude Code
codex                    # Codex CLI
code .                   # VS Code: your agent opens inside the editor
python3 cockpit/app.py   # just the dashboard in a browser, no agent
```

`bash install.sh` is the whole install. It sets everything up and asks before it
installs anything; it needs no agent at all, a plain terminal is enough.

**Claude Code.** Run `claude` in the folder and say `/themiz-setup`: the setup goes
as a conversation, one question at a time.

**Codex CLI.** Run `codex` in the same folder — the same agents and the same rules
are already inside the project.

**VS Code or Cursor.** Open the folder with `code .` and start your agent inside the
editor.

**No agent at all.** `python3 cockpit/app.py` opens the dashboard at
`http://127.0.0.1:8800`, where you can read a case, follow deadlines and assemble a
document by hand.

To update an existing copy: `bash scripts/update.sh`.

## What comes out

<p align="center"><img src="assets/pantheon/workflow/06-draft.png" alt="Draft: one desk writes, another checks, a marble screen and a gold lock between them" width="100%"></p>

The finished document lands in the `GOTOVO/` folder of your case in two forms:
`.md` to edit and `.docx` to file. Beside it stay the case map, the practice that was
found with its links, and the calculations with every intermediate number — so you
can check any figure instead of trusting it.

## Where to go next

- [How it works](HOW-IT-WORKS.en.md) — the inside view, without marketing.
- [First steps](ONBOARDING.md) — what to do on day one.
- [Security](../SECURITY.md) — what stays with you and what leaves.
- [Helping](../CONTRIBUTING.en.md) — if you want to fix or add something.

## Security and privacy

<p align="center"><img src="assets/pantheon/security.png" alt="The closed gold client folder under Themis's flat hand on the marble table, the blue thread of outside sources stopping at the table edge" width="100%"></p>

Case material stays on your machine. What leaves is a depersonalised question for the
case-law search — the rule of law, the category of dispute, the region, with no names
and no case number. A personal-data guard checks every save and will not let a name,
an address or a document number through. The details are on the
[security page](../SECURITY.md).

## Status, and what not to expect

<p align="center"><img src="assets/pantheon/workflow/07-hearing.png" alt="Hearing: the finished document in a gold sleeve at the table edge, the scales balanced, a marble calendar block beside them" width="100%"></p>

The system runs on live cases, but it is still growing, and it is fairer to say so
plainly:

- Scan recognition is built around a Mac. On other systems some of the paths differ.
- Practice is searched in open sources: what is not there, the system will not find.
- No output is a legal position until a lawyer has accepted it.
- There is no separate stable release line yet; fixes land on `main`.

## Licence

Themiz Community Licence 1.0: free for an individual lawyer, including private
practice. Organisations need a commercial licence. The texts are in
[LICENSE](../LICENSE) and [LICENSE.ru.md](../LICENSE.ru.md).
