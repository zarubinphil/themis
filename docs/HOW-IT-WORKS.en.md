# How Themis actually works

This is the engine room, not the showroom. Every claim below was checked by running
it on a real machine on 21 August 2026, and the command sits next to the claim so
you can repeat it instead of trusting me. Where something is broken, missing, or
needs your hands, it says so. There is a whole section for that near the end.

The reader I have in mind is a working litigator, not a developer.

One practical note before we start: the tools speak Russian. The command output
quoted throughout this document is translated so it reads, but what appears in your
terminal is the Russian original. Flag names and file paths are shown verbatim.

A note on jurisdiction: Themis is built for Russian civil litigation. A few local
terms show up below, and I explain them where they appear rather than leaving them
transliterated.

---

## 1. What happens when you hand it a case

A case is a folder. It has two faces: one turned toward you, one toward the machine.

```
cases/<client>/<case>/
  _case.md         the case card — you read this one
  00_intake/       what the client brought in. Untouchable
  02_hearings/     events, and documents already filed with the court
  GOTOVO/          finished documents — the reason you came here
  .agent/          the kitchen: case map, case law, legal position, drafts
```

`.agent/` is hidden behind a dot on purpose. Watching a draft being drafted is not
your job. The layout lives in exactly one file
([`scripts/case_paths.py`](../scripts/case_paths.py)) rather than scattered across
the codebase, so every agent understands it the same way.

From there the work moves in steps, and the order is not a suggestion.

**Step 0, check what we already know.** Before going anywhere, the system looks at
its own index of previously researched questions. If the question is already
answered, it reuses the answer. On a fresh install that index is empty. It is not
shipped; it accumulates as you work, and the longer you use the system, the more
often step 2 is skipped outright.

**Step 1, map the case.** A cartographer agent reads the materials and reduces
them to one document: facts, parties, subject matter, chronology, evidence. No map,
no further movement.

**Step 2, the case law.** Hunters go looking for precedent. On a hard case there are
three of them, and they argue (more on that below).

**Step 3, the legal position.** A council of jurists works out the position.

**Step 4, the document.** A drafter writes it, working from the map, the case law
and the position.

**Step 5, review.** A separate reviewer takes the document apart across seven
checkpoints and issues a verdict. The `.docx` is built exactly once, after the
verdict says "ready to file", and lands in `GOTOVO/`.

Where you currently stand is answered by one command rather than by reading half a
dozen files:

```bash
python3 scripts/themis_status.py cases/<client>/<case> --brief
```

It prints the case details, the next hearing, how much material is in, the state of
every step, and a line that says NEXT STEP. That line is not advice. It is the only
step the system will let you take.

**Skipping a step is physically impossible.** Not discouraged. Impossible. Trying
to write the legal position before the case law is ready:

```
$ echo '{"tool_name":"Write","tool_input":{"file_path":".../positions.md"}}' | python3 scripts/claude_guard.py
PROTOCOL BLOCK: positions.md may only be written after Step 2 — practice.md
carries neither consensus marker.
```

The reasoning is dull and important. A model follows written instructions
probabilistically; a guard follows them every time. So anything declared an
iron rule here is enforced by code, not by good intentions.

---

## 2. How the material gets read, and why it costs nothing

All extraction happens on your machine. Nobody is billed for reading a scan and
nothing is uploaded to read it.

**Scans go through Apple Vision**, the same engine macOS uses to pull text out of
an image. It is compiled into [`bin/vision-doc`](../bin) and called locally.
Measured on three A4 pages of Russian text at 200 dpi:

```
$ /usr/bin/time -p bin/vision-doc page-1.png
real 1.62      # then 1.31 and 1.33
```

Call it a second and a half per page. Accuracy on Russian is good: on a control
page of forty lines, forty matched. The only casualty was the `№` sign, which came
back as `Nº`.

**Text documents are never OCR'd at all.** If a PDF, DOCX or XLSX already carries a
text layer, the text is simply lifted out. Faster and more accurate than any
recognition.

Which of the two paths applies is decided by a router
([`scripts/markdown_extract.py`](../scripts/markdown_extract.py)), not by a person:

```
$ python3 scripts/markdown_extract.py contract.pdf --json-meta
{"route": "text-pdf", "pages": 3, "cache": "miss", ...}
```

```
$ python3 scripts/markdown_extract.py scan.pdf --json-meta --render-dir /tmp/case
{"route": "scan", "ocr_pages": 3, ...
 "note": "Apple Vision OCR (local, $0): 3 pages → page_NNN.txt (0 empty)"}
```

Every scanned page becomes its own file: `page_001.txt`, `page_002.txt`, and so on.
That is not fussiness. It means a brief can honestly cite "page 82" and page 82 is
really there.

**Mixed PDFs do not lose pages.** When half a document is real text and half is a
pasted-in scan, the text pages are read as text and the scanned pages are recognised.
Nothing quietly falls out.

**Details are picked up automatically.** Alongside the extracted text the router
writes down what it found: tax numbers, company registration numbers, case numbers,
sums, passport and account numbers.

```
"requisites": {"inn": ["7707083893"], "case_soyu": ["2-45/2026"]}
```

(`INN` is the Russian taxpayer number, the thing you check a counterparty by.)
Nobody has to hunt for them by eye.

**Reading the same file again is free, and that is measurable.** Results are cached
under `~/.cache/legal_extract`, keyed by the file's fingerprint:

```
first call:   real 2.04    "cache": "miss"
second call:  real 0.19    "cache": "hit"
```

No model call, two tenths of a second. Which is why re-reading case materials is
cheap here. What is expensive is recognising them twice, and the system refuses to.

### When it does reach for the cloud

Three situations, all narrow:

1. recognition came back empty or obviously garbled;
2. a critical detail is in dispute — a case number, a tax number, a sum, a name —
   and wants a second pair of eyes;
3. handwriting, stamps, faded text.

Everything else stays local. And if the local engine is unavailable, the system
stops and says so rather than silently switching to the cloud. Quiet degradation is
worse than a refusal: you would never learn that the bill went up while the quality
went down.

---

## 3. Why there are thirteen agents rather than one

An agent here is a narrow role with its own tools. There are thirteen of them
(`ls .claude/agents/` returns exactly thirteen), and the split is functional.

**Intake** moves new material from the inbox into the right case.

**The cartographer** builds the case map, delegating to readers when the case is big.

**Three readers**, one per material type: scanned PDF, photograph, text document.
They are separate because each has a different source of truth and a different cost
of being wrong.

**The reconciler** compares the readers' reports against each other: details, dates,
names, amounts. If two readers saw different sums in the same document, nothing
moves forward until that is settled.

**Three case-law hunters, and this is where it gets useful.**
The classicist builds the argument for you. The sceptic hunts for precedent
*against* you: the brief is to think like the other side's best counsel and come
back with a risk map, each risk paired with its counter-argument. The tactician
works the procedural angles: limitation periods, admissibility, jurisdiction,
interim relief.

The point is that they are not asked to agree. A single model told to "find
supporting case law" will find supporting case law; that is what searching for a
conclusion does. Three roles with genuinely opposed briefs hand you what opposing
counsel will say, before you hear it in the courtroom.

**A council of five jurists** argues the position over several rounds and reduces
the argument to one document.

**The drafter** writes. **The reviewer is a different agent**, deliberately: whoever
wrote the document does not get to mark their own work. The reviewer reads it cold
against a seven-part checklist and issues a verdict.

**The hearing preparer** assembles the pack: objective, what to bring, arguments
with sources, likely questions from the bench, weak spots. **The archivist** files
what was learned so the next similar case does not start from zero.

---

## 4. Where the system checks itself

This is the least familiar part. Most AI tools answer and stop. Here there are
several guards standing between the answer and you, and each one is code rather
than intention.

**An acceptance suite.** The system carries its own test of 117 checks:

```
$ python3 scripts/stage9_spec.py
checks passed: 117/117
```

It does not ask "does the script run". It asks whether the promises hold: that the
documentation does not describe tools which do not exist, that rules declared
iron are actually enforced by something, that a fresh install can produce a
document.

**A path guard** refuses to destroy client material:

```
$ rm -rf cases/<case>/00_intake
BLOCKED: deletion touches cases/, 00_intake/ or _baselines/.
Case materials are untouchable. If you truly need this — do it by hand.
```

The same guard refuses to open a binary document directly, bypassing the router and
the cache. Otherwise the same page would get recognised ten times over.

**A personal-data guard** sits on every commit and every push. It reads the client
folder names off the disk at the moment it runs and stores none of them. When it
finds something it reports "file, line, length of match" and never prints the value
itself: a guard must not become the second leak.

This is not hypothetical. On 4 August 2026 a case folder name, which is to say a
real person's surname, reached a public repository through a code comment. The
guard was written afterwards, because of that.

**A format guard** signs off the `.docx` instead of a human eye:

```
$ python3 scripts/document_guard.py claim.docx --md claim.md
```

Margins, point sizes, line spacing, a single typeface throughout, page numbers,
justification, exhibit numbering, and whether every exhibit is actually referenced
in the body. Exit 0 means accepted; exit 1 comes with the list of what to fix.
This comparison used to be done by the model, which spent reasoning on arithmetic
and raised false alarms doing it.

**Amounts in words are checked against the figure.** "350,000 (three hundred and
fifty thousand) roubles". The words have to match the number, not merely be
present. The numerals are generated by dedicated code, with the grammatical gender
and case Russian requires.

**Prompt injection hidden in case material.** Evidence is data, not instructions.
If a scan contains something like "ignore your previous instructions", a separate
guard catches it. What it looks for is not the imperative mood — legal writing is
full of imperatives — but a form of address to the executor. A court document
never calls the model by name, and that turns out to be a reliable axis.

**A document cannot be assembled around the review.** Until the reviewer rules,
there is no `.docx`:

```
$ python3 scripts/verdict.py claim.md --check
BUILD REFUSED — claim.md has no verdict at all; it never went through review
```

The verdict is bound to a specific revision of the text. Edit the document after
approval and the approval lapses.

**Every guard tests itself, and they are all green:**

| Guard | What it holds | Self-test |
|---|---|---|
| `claude_guard.py` | paths, step order, reads that bypass the router | 126/126 |
| `document_guard.py` | `.docx` formatting, `.docx` matches `.md` | 48/48 |
| `pd_guard.py` | client data never reaches a public repository | 43/43 |
| `quality_gate.py` | OCR completeness, tables, figures, checksums | 30/30 |
| `instruction_guard.py` | injection inside case material | 30/30 |
| `money_rule.py` | money, currencies, verbatim quotations | 18/18 |
| `table_guard.py` | tables lost during extraction | 5/5 |
| `pii_gate.py` | redaction before anything leaves the case | fail-closed |

`pii_gate` deserves its own sentence. It redacts a fragment of the case before that
fragment goes anywhere external. And if it finds nothing, it does not report
"clean", it refuses. Legal text almost always contains at least one name; an empty
result more often means the search failed than that there was nothing to find.

---

## 5. Why it is cheap to run

Not because the model is cheap. Because it gets called less.

**Cases are triaged by difficulty before any work starts.** Three modes. A routine
document on a question already answered is not run like a Supreme Court appeal: no
map is built, details are taken from what was already extracted, the hunters do not
run at all. A straightforward case gets one hunter instead of three and no councils.
A hard case gets the full cast. When in doubt the cheaper mode wins, because
escalating later is always possible.

**The model for a step follows from the difficulty of the case, not from habit.**
The drafter defaults to the strongest model, because on a serious case drafting is
the most consequential work in the pipeline. On a routine document that same default
would mean paying five times over for the same page. So
[`scripts/model_policy.py`](../scripts/model_policy.py) decides:

| Work | Routine case | Hard case |
|---|---|---|
| drafting | mid-tier model | top-tier |
| review | mid-tier | top-tier |
| hunting case law | does not run | mid-tier |
| council members | does not run | mid-tier; chair is top-tier |
| reading text, sorting | cheap tier | cheap tier |

Gathering and mechanics go to the cheap model. Synthesis, drafting and the final
pass go to the strong one.

**Anything an ordinary program can compute is never given to a model.** This is the
largest saving and the dullest.

Statutory interest under article 395 of the Civil Code is computed in code, with the
central bank rate table, a period-by-period breakdown and the total spelled out in
words:

```
$ python3 scripts/calc395.py --dolg 350000 --s 01.02.2026 --po 01.08.2026
  01.02.2026 — 15.02.2026    15 d.   16.00%  /365    2 301.37
  ...
TOTAL INTEREST: 25 911,99 (twenty-five thousand nine hundred eleven roubles
ninety-nine kopecks)
```

Procedural deadlines are code too, and they cite the provision they rely on:

```
$ python3 scripts/sroki.py --ot 21.08.2026 --mesyacev 1
  time starts running 22.08.2026 [art. 107(3) Code of Civil Procedure]
  plus 1 month → 21.09.2026 [art. 108(1); Supreme Court Plenum ruling
  of 22.06.2021 no. 16, para. 16]
LAST DAY: 21.09.2026 (Monday)
```

(A *Plenum ruling* is guidance issued by the Supreme Court on how lower courts must
apply a provision. In practice you cite it the way a common-law lawyer cites binding
authority.)

Taxpayer numbers are validated by checksum, locally, for nothing. A digit garbled
during recognition almost never produces a valid checksum:

```
$ python3 scripts/verify_inn.py 7707083893
7707083893: valid
$ python3 scripts/verify_inn.py 7707083894
7707083894: INVALID — no such taxpayer number exists; re-check against the scan
```

Plenum rulings are quoted verbatim from disk, without going online:

```
$ python3 scripts/cite.py "para. 21 of Plenum ruling of 19.06.2012 no. 13"
### para. 21
Courts must bear in mind that, within the meaning of art. 327...
Source: knowledge/plenumy/plenum-ot-19062012.md
Ready to paste: (Supreme Court Plenum ruling of 19.06.2012 no. 13, para. 21)
```

The corpus does not ship with the repository. You fetch it yourself with one
command (`python3 scripts/update_legal_corpus.py --plenums`), after which it lives
on your disk and refreshes monthly via a scheduled job. On the machine these
measurements were taken, it holds 342 rulings.

Cost is not the whole of it. A web lookup once mangled the text of article 683 of
the Civil Code, and since then provisions come from disk or they do not come at all.

**What has been read is not read again.** An opened file stays in play for the rest
of the session and is paid for again with every subsequent model call. So the state
of a case is asked of a program rather than reconstructed by eye: the `--brief`
summary on the bundled example case is 1,868 characters and is computed locally,
which is to say free. The owner's measurement on a live case, 4 August 2026: the
old start-of-session ritual pulled in roughly 34,900 characters, and paid for them
again on every following call.

---

## 6. What it does not do, and where you are still required

Without this section everything above would be advertising.

**It does not replace a lawyer and it decides nothing about your case.** It reads,
cross-checks, researches, drafts and checks form. What position to take, what to
file and when, remains yours. No document leaves for a court on its own.

**It stops and asks.** If an agent is missing, if the acceptance suite is red, if a
guard blocked a write, if spending ran past the expected envelope, work halts and
you are told. Quietly substituting a general-purpose model for a specialist is
forbidden.

**Scan recognition is macOS-only.** Apple Vision is an Apple system framework; on
Linux and Windows it does not exist. Text PDFs, DOCX and XLSX still read fine there;
scans and photographs of documents do not. The environment check does not paper over
this. It names the gap and the substitutions out loud, for instance moving scheduled
jobs from launchd to systemd timers and checking that the locale is UTF-8, without
which Cyrillic folder names arrive mangled.

**After installation the system is not ready yet, and it tells you so:**

```
$ python3 scripts/setup_doctor.py
✓ scan OCR (Apple Vision): bin/vision-doc responds
✓ legal corpus: Supreme Court Plenum rulings: 342 files
✗ legal corpus: codes: 0 files
    → fetch: python3 scripts/update_legal_corpus.py --init
Total: critical 1, warnings 0, checks 17.
SYSTEM NOT READY.
```

That is real output from a real machine, not an illustration. The corpus of statutes
has to be fetched once, by hand. Without it verbatim quotation of code provisions is
unavailable, and the court-fee calculator — which reads its rates from chapter 25.3
of the Tax Code — honestly answers "article 333.19 is not in the corpus" instead of
computing from memory.

**Case-law search depends on somebody else's website.** The free search hits an
external resource, and that resource goes down. On the day of this measurement it
was returning errors:

```
$ python3 scripts/practice_search.py "division of marital property" --law "38 SK RF"
... responded HTTP 500 — response discarded, not cached.
Nothing found. SEARCH DID NOT COMPLETE (timeout or source unavailable)
```

Notice what matters there: the tool said it had failed and invented nothing. Russia
has no free government API for court decisions. This was tested; the endpoints
either return errors or do not answer.

**Things you have to do yourself:** install Claude Code, build the recogniser during
setup, fetch the legal corpus, register your own Telegram bot if you want one, and —
above all — read the document before you file it. The reviewer catches form, figures
and citations. A missed limitation period and a provision that does not apply are
caught by a lawyer.

---

## 7. Privacy

**Case material never leaves your machine.** Client folders are excluded from
publication at the repository level; what goes up is templates and one synthetic
example. Extraction and recognition are local.

**The Telegram bot is a remote control, not a shop window.** It is optional: no
configuration, no bot, and the system runs regardless. When it is on, what goes out
is dates, counts and the word "done". Everything else is held back by an outbound
guard, and that is testable:

```
$ python3 scripts/themis_bot.py --check-out message.txt     # "Hearing 25.09.2026 at 10:00, document ready"
clean — the bot will send this

$ python3 scripts/themis_bot.py --check-out other.txt       # with a name, a case number and a sum
will not go out: PD (CASE), PD (NAME), PD (NAME?), sum (350 000 rub.)
```

The principle is worth saying plainly: anything sent to Telegram has been disclosed
to Telegram. So the bot does not forward documents. It hands you a link back into
your own network. Voice notes are transcribed on your machine, because dictation is
full of names and amounts.

**What goes out for research goes out redacted**, through the gate described above,
the one that refuses to report "clean" when it found nothing.

**Working logs stay local.** Case names appear in them as a matter of course, which
is fine; what would not be fine is those logs reaching the repository, and a separate
check watches exactly that.

---

## Further reading

- [README](../README.md) — the short version, and how to install.
- [`scripts/setup_doctor.py`](../scripts/setup_doctor.py) — an honest answer to
  "what is broken on my machine".
- [`scripts/themis_status.py`](../scripts/themis_status.py) — where am I in this case.
- [Русская версия](HOW-IT-WORKS.ru.md).
