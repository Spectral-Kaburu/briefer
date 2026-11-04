---
name: ruthless-audit
description: Whole-repo adversarial audit run in rounds until a round finds nothing new. Use when asked to audit, harden, bug-hunt or find everything wrong across the briefing repo, and for PLAN.md Phase 11. Maps the repo, fans out read-only slice-auditor agents, runs mechanical sweeps, verifies every finding against its cited line, keeps the AUDIT.md ledger, hands fixes to fix-loop, and recurses on what changed. Never edits code itself.
---

# ruthless-audit

You are the orchestrator. You find defects; you do not fix them (that is `fix-loop`) and you do not grade your own fixes (that is `ruthless-review`). The lenses are in `lenses.md` next to this file: read it before round 1.

## Ground rules

- A finding needs `file:line` and a **quoted line**. You re-open every P0 and P1 yourself and compare the quote; a mismatch discards the finding and counts as a hallucination in the round's stats.
- Never run `briefing run` through the real entry point, never run `voice-tts`, no network outside `@pytest.mark.live` and the project's allowed smoke forms, no sandbox escalation, no new dependency.
- Items the project marks owner's-call or out of scope are not findings.
- "Clean" is never a verdict. The verdict is "no findings in round N under these lenses, scope covered: ...".

## The ledger: `AUDIT.md` (repo root)

```
# AUDIT ledger
## Baseline      commit, make check counts, make live result, date, assumed load
## Inventory     file | lines | test file
## Findings
| ID | Sev | Conf | Lens | Where | Problem | Status | Fix |
## Checked fine  lens | what | round        (so a later round does not redo it)
## Rounds
| Round | Scope | Slices | New P0 | P1 | P2 | P3 | Discarded | Fixed | Stop? |
```

IDs are `A-001`, `A-002`, and so on. Status is `open`, `fixed`, `blocked` (with the `BLOCKED.md` entry) or `wontfix` (with the reason). Update it after every fix, not at the end.

## One round

0. **Assume a load.** In round 1, write the load the code is judged against in the baseline (default: one person running a CLI, plus one scheduled run at 08:00). Scale findings are judged against it.
1. **Scope.** Round 1 is the whole repo. Round N is every file changed since round N-1 began (`git diff --name-only <start-sha>`), plus the files that import them (one level: `rg -l "from briefing\.<mod> import|import briefing\.<mod>" src tests`), plus their tests. Record the start sha in the ledger.
2. **Mechanical sweeps.** Run the sweeps in `lenses.md` and record each result, hit or empty. Judge each hit by reading the code around it.
3. **Slices.** Partition the scope into slices of at most about 600 source lines, always a module with its test file. Launch one `slice-auditor` per slice in **a single message** so they run in parallel, giving each its file list, the round number and the instruction to read `lenses.md` first. Cap at eight per message.
4. **Verify.** For every returned finding, open the cited lines and compare the quote. Discard mismatches. De-duplicate across slices and sweeps. A `possible` finding gets one honest attempt at a repro; if it does not reproduce it goes to `Checked fine` as "could not confirm", not to the queue.
5. **Triage.** Order by severity, then confidence. `certain` and `likely` P0 to P2 findings enter the fix queue. Sort by blast radius within a severity: shared code (router, config, extract, ask) before leaves.
6. **Fix.** Hand each queued finding to `fix-loop`, one at a time. If a fix touches shared code, rerun the affected slice's auditor afterward instead of trusting the fix.
7. **Close the round.** `make check` must be green. Write the round's row. Decide whether to go on.

## Recursion and stopping

The loop is: audit, fix, then audit what the fixes touched, because a fix is new code and new code has new bugs. Stop when **a round adds no new P0 to P2 finding after its own fixes landed and `make check` is green**, or after **round 5**, or when only `blocked` items remain. State which one. At the stop, list what was never in scope (files not audited, lenses not applied) so the owner knows where the edge of the claim is.

If a round is finding the same kind of defect repeatedly, stop and write a lens-level finding ("this class keeps recurring: ...") with the root cause and a sweep that would catch it, add the sweep to `lenses.md`, and rerun that sweep repo-wide. That is the point of recursion: the audit improves itself.

## Owner report: `AUDIT-REPORT.md`

When the audit stops, write a plain-English report for the owner, who may not have seen this code. Short sentences, everyday words, a technical term explained the first time. Structure:

- `What this repo does:` two or three sentences, then `Load assumed:`.
- Findings in three groups, most important first, skipping empty ones: **Must fix** (bug, security, data loss, breaks at the assumed load), **Should fix** (risky code without a test, real slowness, duplication, code that should not exist), **Nice to have**. Number them across groups so the owner can say "fix 2 and 5". Show at most 20 and say how many smaller ones were left out.
- Each finding has four parts, one or two short sentences each: **What this is**, **Problem** (the concrete case), **Fix** (the smallest one that works, preferring deletion), **If we skip it**. Heading line: `file:line`.
- End with `Verdict:` (healthy, or what to fix first), `Lean: -<N> lines, -<M> dependencies possible.` when lean findings exist, and `Not checked:` (files, lenses and anything not run).

Findings already `fixed` appear in a short "Fixed during the audit" list with their sabotage result. Lean findings the audit did not fix are the owner's pick: `fix-loop` deletes only code proven dead by a whole-tree grep, and leaves every other lean finding in the report, because merging, splitting and replacing are decisions, not bugs.

## Output to the user

The ledger holds the evidence and `AUDIT-REPORT.md` is the report. In the reply give: the stop condition, the rounds table, counts by severity (found, fixed, blocked), the three worst findings in one line each, and the `BLOCKED.md` questions. Do not paste the ledger.
