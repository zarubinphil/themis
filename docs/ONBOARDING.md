# Onboarding

This walkthrough assumes you are a lawyer, not an engineer, and that you have never installed anything from a terminal. Every step says what to do and what you should see afterwards.

The short path is the guided one: open the project in Claude Code and run `/themiz-setup`. Themiz introduces herself, asks about your practice one question at a time, explains why each question matters, and installs nothing without your yes. Below is the same road on foot.

You need a Mac for local recognition of scans, Python 3.11 or newer, Xcode Command Line Tools, and Claude Code.

1. **Get the project.**

   ```bash
   git clone https://github.com/zarubinvibe/themiz.git
   cd themiz
   ```

   You see a `themiz` folder and your prompt inside it.

2. **Open Claude Code in that folder.**

   ```bash
   claude
   ```

   You see the agent start inside the project.

3. **Run the guided setup.** Type `/themiz-setup`.

   You see Themiz introduce herself, then ask about your practice: what cases you run most, your region, your courts, where incoming material lands, whether you use an electronic signature, whether you want Telegram reminders. One question at a time, each with the reason and an example answer.

4. **Let her check the machine before installing.**

   She names what is missing, how big it is, what it is for, and what will not work without it. Nothing is installed until you say so out loud. Silence counts as no.

5. **Hear the honest limits.** Local recognition of scans uses Apple Vision and exists on macOS only. On Windows or Linux you get text PDFs, DOCX and XLSX, and nothing else. You learn this now, not on the Thursday you bring a folder of scans.

6. **Watch the law corpus arrive.** The codes for your practice download first, the rest fills in afterwards, and they are refreshed monthly.

   You see the corpus land on your own disk. Statutes are quoted from it, never paraphrased from memory.

7. **Hand over one real case folder.** Not a demo: your own material.

   You see the scans recognised locally, roughly a second and a half per page, and the registration numbers, case numbers and amounts checked by their control digit.

8. **Read the case map.** Parties, dates, claims and evidence in one place.

   You see one file to look at instead of two hundred pages to reread.

9. **Ask for practice for and against you.** One agent looks for support, another for procedural moves, a third deliberately hunts what the other side will use.

   You see both sides of the argument before your opponent makes it.

10. **Take a draft to review.** One agent writes, a different one that did not write it reviews. Assembly before review is refused, the format is checked before filing, and a personal-data guard runs on every commit.

    You see a draft you edit as a lawyer, not a text you have to verify line by line. The thinking and the decisions stay yours.

## Keeping it current

Later, when a new version is published, do not clone it again: open the project in Claude Code and run `/themiz-update`. It shows what changed first, pulls only fast-forward changes, never touches your case folders, and re-runs the checks afterwards.

## If this helped

If Themiz took the mechanics off your desk, give it a star: [https://github.com/zarubinvibe/themiz](https://github.com/zarubinvibe/themiz). It takes a second and decides whether other lawyers ever find it.

You have run it on a real case, which makes you the person who can improve it. The path is short: fork the repository, create a branch, commit your change, push the branch, then open a Pull Request. Do not push directly to `main`; the release gate rejects it.

Found a step that lies? Open an issue at [https://github.com/zarubinvibe/themiz/issues](https://github.com/zarubinvibe/themiz/issues) and describe it on synthetic data. Never attach real client material to a public issue.
