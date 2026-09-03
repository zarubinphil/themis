# Security policy

Themiz works with real case material: scans, client documents, deadlines, and drafts. The repository holds the agent prompts, the calculators, the guards, and the workflow. Your machine holds the case files.

## Trust boundary

- Reading, recognition, and the mechanical calculators run locally. Client folders are blocked from publication at the repository level.
- Case law search sends a depersonalised query to an external source. The case file itself is never sent.
- A cloud check of a single page is allowed only when local recognition returns nothing, or when a critical detail has to be confirmed. There is no silent switch to the cloud.
- Telegram reminders go through your own bot and carry dates and a completion word. No names, case numbers, or amounts.
- Agent output is untrusted model output, including case maps, found practice, and draft documents. A lawyer decides.
- Themiz does not implement a sandbox. File access, shell commands, and network access are governed by the agent runtime you start it in.

Keep client material on an encrypted disk, run Themiz under an account only you use, and review `git status` before any commit in a case repository.

## Personal data

- A personal-data guard runs on every commit and blocks names, addresses, and identifiers from leaving the working tree.
- A separate guard refuses to erase case material.
- Never commit client documents, exports, or recognition caches to a public repository.
- Anything you paste into an issue or a discussion is public. Redact it first.

## Supported versions

Security fixes target the current `main` branch. There is no tagged stable release line yet.

## Reporting a vulnerability

Do not publish exploit details, and never attach real client material to a report. If the repository Security tab offers private vulnerability reporting, use it. Otherwise open a minimal issue asking the maintainer for a private contact channel, and keep the sensitive part out until that channel exists.

Include the affected revision, the operating system, the reproduction steps on synthetic data, the impact, and any mitigation you tested.
