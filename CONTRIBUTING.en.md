# Helping Themiz

[Русский](CONTRIBUTING.md) · [中文](CONTRIBUTING.zh.md)

<p align="center"><img src="docs/assets/pantheon/contributing.png" alt="A marble workshop: a blank sheet on the table, a chisel and mallet beside it, a blue thread carrying a change in from outside, and the closed gold client folder standing apart, out of the work" width="100%"></p>

Changes are welcome. There are few rules, and they are all about one thing:
somebody's case must not suffer from your help.

## Never

**Client data does not leave.** Nothing from `cases/` is ever saved except the
made-up example. A proposal carrying a real surname or a real identifier is closed
without discussion — that is not strictness, that is legal privilege.

**Case material is never erased.** Not even by accident: a separate guard watches
for that, and it is not there to be worked around.

## Where things live

The logic of the system lives in three places: `.claude/`, `scripts/`, `cockpit/`.
Everything else is the user's own data — their cases and the practice they have
gathered. Leave it alone.

## How we work

- **Whatever can run on the machine, runs on the machine.** Reading scans,
  transcribing voice, parsing documents — locally. The cloud is added narrowly, and
  only where the local tool honestly returned nothing.
- **Every claim in the code carries a check.** Not "I tested it", but an instrument
  that turns red when the claim stops being true.
- **Mechanical work is written as a script,** not done by hand and not handed to an
  agent. If the work can be repeated, it is written in code.

## Small things that save time

- Python follows PEP8, dates are written `DD.MM.YYYY`.
- Before proposing a change, run `bash install.sh` on a clean copy: the system has
  to come up ready, with no manual fixing afterwards.

Bring ideas and breakages to Issues. A short account of what you did and what
happened instead of what you expected helps more than a long analysis.
