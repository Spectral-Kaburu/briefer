---
name: mutation-sweep
description: Audit the tests themselves by hand-mutating the code under them and demanding red. Use for PLAN.md T11.3, after a fix-loop, or whenever tests are suspected of being weak. For each risky unit it applies mutants (flip a comparison, drop a guard, reverse an order, remove an await) and records survivors, each of which is a test that cannot fail. Always restores the code exactly.
---

# mutation-sweep

Green tests prove nothing until you have seen them go red for the right reason. This skill makes them go red, one deliberate bug at a time, and reports the bugs they miss.

## Safety

- Work on a clean tree (`git status` empty) and **restore after every mutant**: `git checkout -- <file>` (or `git stash`) and confirm `git diff --stat` is empty before the next one. Never leave a mutant behind; the last step of the sweep is `git diff --stat` showing nothing.
- Run only the tests that exercise the unit (`pytest <test_file> -x -q`), not the whole suite, until the end.
- Never mutate `tests/conftest.py`'s guard to "see what happens" with network or process access; the guard is audited by reading, and by the project's own sabotage tests.
- No real network, subprocess, `voice-tts` or `briefing run`. Patch seams only.

## Per unit

1. List the unit's **behaviours** from its code and docstrings: every guard, cap, floor, deadline, default, ordering, normalisation, error path and "never raises" contract.
2. For each behaviour, make the smallest mutant that breaks it. Operators to use:
   - comparison: `<` to `<=`, `>` to `>=`, `==` to `!=`, off-by-one on a bound;
   - boolean: drop a `not`, swap `and` and `or`, force a condition `True` or `False`;
   - guard: delete the `if` that rejects, return early, skip the cap or the floor;
   - order: reverse a candidate list, a precedence chain or a sort key;
   - default: change a config default, a timeout, a limit, a TTL;
   - async: remove an `await`, drop the `cancel()` at a deadline, remove `return_exceptions=True`, drop a `finally`;
   - exception: widen or narrow an `except`, re-raise instead of returning `[]`, swallow instead of raising;
   - text: skip the delimiter strip, skip the HTML or control-character strip, use markup instead of `Text`;
   - state: skip the atomic write, skip the lock, keep an expired entry;
   - constant: change a badge, a URL host, a header value.
3. Apply the mutant, run the unit's tests, record the result, restore.
4. **Survivor** = tests stayed green. Record it: unit, mutant (one line), which behaviour it breaks, which test should have caught it.

## Output

For each unit a table in `AUDIT.md` (or the report): `mutant | killed by | survivor?`, and a kill rate. Every survivor becomes a finding with lens L8, severity P1 when the behaviour is a safety property (a secret, a gate, a cap, a deadline, the never-raise contract, the delimiter), P2 otherwise, and goes to `fix-loop` to be fixed by **adding or strengthening a test**, not by changing the code. After the fix, rerun that mutant and see it killed.

Aim for at least eight mutants per unit, covering every behaviour listed in step 1; if a unit has fewer behaviours, say so. Equivalent mutants (ones that cannot change behaviour) are noted as such and not counted as survivors. Finish with `git diff --stat` empty and `make check` green.
