---
name: fix-loop
description: Fix one audit or review finding the strict way: reproduce red, smallest fix, green, then revert the fix to watch the test fail again (sabotage), full make check, and hunt the same bug's siblings. Use for every finding from ruthless-audit, ruthless-review or mutation-sweep, and whenever a bug is reported in the briefing repo. Refuses to weaken tests or add exemptions.
---

# fix-loop

One finding at a time, smallest change that fixes it, proof that the proof works. The finding comes with an ID, `file:line`, a quote and a repro idea (from `AUDIT.md` or a review).

## Steps

1. **Re-read the finding's lines.** Confirm the quote still matches the file. If the code moved or the finding is wrong, mark it `wontfix` with the reason in `AUDIT.md` and stop. A fix for a bug that is not there is a regression.
2. **Red first.** Write the test from the repro idea using a **synthetic** fixture (invented text; never paste third-party content). Put it in the existing test file for that module. Run only that test and read the failure: it must fail for **the reason in the finding**, not for a typo or an import error. Paste the failure line into your notes.
3. **Smallest fix.** Change the least code that makes the test pass. No refactor, no rename, no drive-by cleanup, no new dependency, no public flag change. If the right fix needs a design decision or changes a public behaviour, stop: write the exact question to `BLOCKED.md`, mark the finding `blocked`, and move on.
4. **Green.** Run the new test, then the module's tests, then `make check`. All green, with the same skip count as before.
5. **Sabotage.** Revert **only the fix** (keep the test) with `git stash push <file>` or by hand, run the new test, and confirm it fails again with the same reason; then restore the fix and confirm green. A test that does not go red under sabotage proves nothing: strengthen it and repeat. Do this every time, including for one-line fixes.
6. **Siblings.** Ask what else has the same shape and grep for it (the same call, pattern, constant or contract in other modules; the same missing check in the other two adapters; the same assertion gap in the neighbouring tests). Each sibling that is really broken becomes its own ledger finding and goes through this loop. Siblings that are fine are recorded under "Checked fine".
7. **Record.** Update `AUDIT.md` (status `fixed`, the test name, the sabotage result), and append one line to `PROGRESS.md` for P0 and P1 fixes. Keep commits to one finding each if the project commits.

## Rules that are never bent

- Never make a test pass by weakening it: no deleted or loosened assertion, no new `skip`, `xfail`, `noqa`, `type: ignore`, no change to what a fixture returns to hide the failure, **no exemption in `tests/conftest.py`**. If a test is wrong, make it assert more, and say so in the ledger.
- Never fix by catching: wrapping the failing call in `try/except` to make a crash go away is a new bug unless the contract says that call must not raise.
- Never touch an owner's-call item from `PLAN.md` or `CLAUDE.md`.
- Two failed attempts: stop guessing, switch to `superpowers:systematic-debugging` (root cause first), and if it is still unclear after that, mark the finding `blocked` with what you learned.
- A fix that makes any other test fail is not done. Find out why before touching that test; the other test may be the one telling the truth.
- No real subprocess, socket, `voice-tts` or `briefing run`. Patch the seam.

## Finish

Report in two lines: the finding ID and what changed (file and function), and the evidence (test name, red reason, green, sabotage red). Do not narrate the steps.
