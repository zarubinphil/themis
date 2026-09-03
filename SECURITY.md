# Security

[Русский](SECURITY.ru.md) · [中文](SECURITY.zh.md)

<p align="center"><img src="docs/assets/pantheon/security.png" alt="The closed gold client folder under Themis's flat hand on the marble table, with the blue thread of outside sources stopping at the table edge and going no further" width="100%"></p>

Themiz works with a live case: scans, client documents, deadlines, drafts.
This page is about one thing — what stays with you, what leaves your computer,
and what to do if something goes wrong. No technical words.

## What stays on your computer

Case material goes nowhere. Reading scans, counting deadlines and fees, assembling
the document — all of it happens on your machine. Case folders are blocked from
publication by the repository itself: they cannot end up in a public copy even if
someone tries.

## What leaves, and in what form

- **Case-law search.** What leaves is a depersonalised question: the rule of law,
  the category of dispute, the region. No names, no case number, no amounts. The
  case file is not sent.
- **Cloud reading of a single page.** Only when local recognition returned nothing,
  or when one disputed detail has to be confirmed. The system never slips into the
  cloud quietly — you see it happen.
- **Telegram reminders.** Through your own bot, carrying only dates, a count and
  the word "done". No names, no case numbers, no amounts.

## What the system does not decide for you

Themiz does not decide. The case map, the practice it found, the finished draft —
that is an assistant's work, not a court's conclusion. The lawyer decides, and that
is how the system is built, not a disclaimer in small print.

One more thing: Themiz does not build an isolated room around itself. Its access to
files, commands and the network is exactly the access of the program you started it in.

## How to keep this in order

- Keep case material on an encrypted disk.
- Run Themiz under an account only you use.
- Before every save into a case repository, look at what is actually being saved.
- A personal-data guard checks every save by itself and will not let a name, an
  address or a document number through. A second guard refuses to erase case material.
- Anything you paste into a public discussion on GitHub becomes public. Take the
  personal details out before you send it.

## If you find a vulnerability

Do not publish how to use it, and never attach real case material. If the Security
tab of the repository offers a private report form, write there. If it does not,
open a short issue asking for a private channel and keep the details until that
channel exists.

Useful to include: the version, the operating system, what you did step by step on
made-up data, what it threatens, and anything you already tried as a fix.

## Which versions we fix

Fixes land on the current `main` branch. There is no separate stable release line yet.
