# Themis

Themis works a Russian court case beside a lawyer: it reads the file, hunts case law, and checks its own documents.

[Русский](README.ru.md) · [中文](README.zh.md)

[![License](https://img.shields.io/badge/license-community%201.0-blue.svg)](LICENSE) [![Stars](https://img.shields.io/github/stars/zarubinvibe/themis?style=flat&color=C9A87A)](https://github.com/zarubinvibe/themis/stargazers) [![Status](https://img.shields.io/badge/status-in%20development-brightgreen.svg)](https://github.com/zarubinvibe/themis) [![Olympuz](https://img.shields.io/badge/olympuz-family-B8D6EA.svg)](https://github.com/zarubinvibe/athena#olympuz-family)

<p align="center"><img src="docs/assets/pantheon/hero.png" alt="Themis in white marble with scales and sword beside the classical column, legal documents and agent review cards laid out in daylight" width="100%"></p>

<!-- owner-welcome:start -->

> Hello. I am a practising lawyer, and too much of my time went into mechanics: two hundred pages of scans, dates to reconcile, citations to check, one detail buried somewhere in the file.
>
> Themis takes that work and leaves me the part that needs judgement. She does not decide anything, and neither should she.
>
> — Filipp Zarubin

<!-- owner-welcome:end -->

## Contents

- [What This Is](#what-this-is)
- [Why It Helps](#why-it-helps)
- [The Main Advantage](#the-main-advantage)
- [How It Works](#how-it-works)
- [Quickstart](#quickstart)
- [Simple Comparison](#simple-comparison)
- [Simple Words](#simple-words)
- [Safety And Privacy](#safety-and-privacy)
- [Limits](#limits)
- [Star And Contribute](#star-and-contribute)

<!-- beginner-readme:start -->

## What This Is

Themis is a multi-agent assistant for Russian litigation. It reads the case file on your computer, builds a case map, hunts case law for and against you, and drafts documents that another agent reviews. The thinking and the decisions stay with the lawyer.

## Why It Helps

Too much of a good lawyer's time goes into mechanics: two hundred pages of scans, dates to reconcile, citations to verify, one detail buried in the file. Themis takes that work and leaves you the part that needs judgement.

## The Main Advantage

**Main advantage:** the numbers and the wording of the law are produced by code, not by a model.

**Why this is better:** Interest under article 395, procedural deadlines, court fees, and amounts in words are calculated by programs. Statutes are quoted from the corpus on your disk, so a paraphrase cannot silently replace the text.

## How It Works

A case moves through named stages. Each stage has its own agents, and a document never leaves without a second pair of eyes.

<!-- workflow-diagram:start -->

```text
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ Intake   │ ▶ │ Extract  │ ▶ │ Case map │
  └──────────┘   └──────────┘   └──────────┘
        ▼
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ Research │ ▶ │ Council  │ ▶ │ Draft    │
  └──────────┘   └──────────┘   └──────────┘
        ▼
  ┌──────────┐
  │ Hearing  │
  └──────────┘
```

<!-- workflow-diagram:end -->

| Stage | What happens |
|---|---|
| 1. Intake | Scans, photos, and documents land in the case folder |
| 2. Extract | Apple Vision recognition, direct text, checksum-verified details |
| 3. Case map | Parties, dates, claims, and evidence in one picture |
| 4. Research | Support, procedural moves, and the opponent's best argument |
| 5. Council | A position assembled from an argument, not from one opinion |
| 6. Draft | A separate reviewer, format checks, and a personal-data guard |
| 7. Hearing | Deadlines, a local dashboard, optional Telegram reminders |

### Step 1: Hand over the case files

You drop the file into the case folder. Client folders are closed from publication at the repository level, so material stays local.

**You get:** one folder per case, with everything the work will need.

### Step 2: Scans are read on your Mac

Recognition runs locally, about a second and a half per page. Registration numbers, case numbers, and amounts are pulled out and checked by their control digit without going online.

**You get:** readable text with the key details already verified.

### Step 3: The case map is built

Facts move from the documents into a single map: who, when, what is claimed, and what proves it. A separate agent cross-checks the readers against each other.

**You get:** one place to look instead of rereading the whole file.

### Step 4: Case law for and against

One agent looks for practice that supports your position, another for procedural moves, and a third deliberately hunts practice against you. The search request is depersonalised.

**You get:** both sides of the argument before the other side makes it.

### Step 5: Five jurists argue it out

Five reviewer agents take the position apart from different angles and put it back together. Disagreement is the point: a weak argument is meant to fall here, not in court.

**You get:** a position with its weak points already named.

### Step 6: One writes, another checks

The document is written by one agent and reviewed by another that did not write it. Assembly before review is refused, the format is checked before filing, and a guard runs over personal data on every commit.

**You get:** a draft you edit as a lawyer, not a text you have to re-verify line by line.

### Step 7: Hearing prep and reminders

Deadlines are calculated with the working calendar and a reference to the rule. A local dashboard shows the state of the work. Reminders go to your own bot, carrying dates only.

**You get:** the hearing prepared, and edits you make teach the next document.

## Quickstart

You need a Mac for local scan recognition, Python 3.11 or newer, Xcode Command Line Tools, and Claude Code.

```bash
git clone https://github.com/zarubinvibe/themis.git
cd themis
claude
# inside Claude Code run: /themis-setup
```

Prefer the plain installer? Run `bash install.sh` in the same folder; it installs dependencies after one confirmation but skips the interview about your practice. No Git? Take [the ZIP](https://github.com/zarubinvibe/themis/archive/refs/heads/main.zip). The local dashboard starts with `python3 cockpit/app.py` on port 8800. First time here? Open the project in Claude Code and run `/themis-setup`: the install goes as a conversation, one question at a time, and nothing is installed without your yes.

Never done this before? [The onboarding](docs/ONBOARDING.md) walks the whole first run step by step and says what you see after every command.

**You get:** the setup asks about your practice one question at a time, downloads the codes you need first, and finishes by testing itself on a real document of yours.

## Simple Comparison

| Choice | Best when | What you get | Trade-off |
|---|---|---|---|
| **Themis** | A real case with scans, deadlines, and documents | Local reading, case map, both sides of the practice, reviewed drafts | Mac for recognition, and a lawyer still decides |
| Doing it by hand | A small case | Full control | Hours of mechanics per case |
| A general chat assistant | A quick question about the law | Instant answers | Quotes from memory, no case file, no deadline maths |
| A commercial legal platform | A firm with a budget | Support and integrations | Case material leaves your machine, and you pay per seat |

## Simple Words

| Word | Simple meaning |
|---|---|
| Repository | The project folder that Git stores and versions |
| Terminal | The window where you type commands |
| Command | One instruction you give the computer |
| Branch | A separate line of changes that does not touch `main` |
| Pull Request | A request to review your change and accept it |
| Case map | One file that holds parties, dates, claims, and evidence |
| Agent | One assistant with a narrow job, such as reading scans or hunting case law |

## Safety And Privacy

- Reading and recognition run on your computer; client folders are closed from publication at the repository level.
- Case law search sends a depersonalised request, never the case file.
- A cloud check of a single page is allowed only when local recognition returns nothing or a critical detail must be confirmed. There is no silent switch to the cloud.
- Telegram reminders use your own bot and carry dates and the word "done": no names, case numbers, or amounts.
- A personal-data guard runs on every commit, and a separate guard refuses to erase case material.
- Document format is checked before filing, and assembly before review is refused.

Read [SECURITY.md](SECURITY.md) before putting real client material on a shared machine.

## Limits

Status: in active development, built for work under a lawyer's control. The main workflow runs in Claude Code.

- Local scan recognition depends on Apple Vision and works on macOS only. Text PDF, DOCX, and XLSX are read on other systems too.
- Case law search depends on an external source and is sometimes unavailable.
- A red gate or a missing agent stops the workflow instead of guessing.
- Themis does not represent you, does not sign anything, and does not replace a lawyer's judgement.
- A clean install on Windows and Linux has not been verified yet.

Deeper reading: [how it really works](docs/HOW-IT-WORKS.ru.md), in Russian and without advertising, and [the full reference](docs/DETAILS.md) with the agent roster and the setup interview.

## Star And Contribute

Useful? Give Themis a star: [https://github.com/zarubinvibe/themis](https://github.com/zarubinvibe/themis). It takes a second and it decides whether other people ever find the project.

Want to change something? The path is short: fork the repository, create a branch, commit your change, push the branch, then open a Pull Request. Do not push directly to `main`; the release gate rejects it.

Found a problem instead? Open an issue at [https://github.com/zarubinvibe/themis/issues](https://github.com/zarubinvibe/themis/issues) and say what you ran and what happened.

<!-- beginner-readme:end -->

<!-- pantheon-family:start -->
## Olympuz family

This is one of the public [Olympuz projects](https://github.com/zarubinvibe/athena#olympuz-family). Each row opens the repository or downloads its source as a ZIP.

| Type | Name | What it does | Source |
|---|---|---|---|
| project | Athena | Portable agent OS that restores a complete Claude and Codex setup on a new Mac. | [Repository](https://github.com/zarubinvibe/athena) · [ZIP](https://github.com/zarubinvibe/athena/archive/refs/heads/main.zip) |
| project | Helioz | 24/7 agent work conveyor with verified completion markers and goal-based overnight decisions. | [Repository](https://github.com/zarubinvibe/helioz) · [ZIP](https://github.com/zarubinvibe/helioz/archive/refs/heads/main.zip) |
| project | Mnemazine | Local-first memory system that turns raw inputs into verified reusable knowledge. | [Repository](https://github.com/zarubinvibe/mnemazine) · [ZIP](https://github.com/zarubinvibe/mnemazine/archive/refs/heads/main.zip) |
| project | Themis | Multi-agent assistant for Russian litigation with local OCR and review by a five-jurist council. | [Repository](https://github.com/zarubinvibe/themis) · [ZIP](https://github.com/zarubinvibe/themis/archive/refs/heads/main.zip) |
| project | Zeuz | Factory that turns an idea into a governed multi-agent workflow with gates, observability, and replay. | [Repository](https://github.com/zarubinvibe/zeuz) · [ZIP](https://github.com/zarubinvibe/zeuz/archive/refs/heads/main.zip) |
<!-- pantheon-family:end -->

## License

Themis Community Licence 1.0: free for an individual lawyer, including private practice. Organisations need a commercial licence. See [LICENSE](LICENSE) and [LICENSE.ru.md](LICENSE.ru.md).
