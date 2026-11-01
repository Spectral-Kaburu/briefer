# DENIED

Commands the sandbox denied, in `command — why it was needed` form.

## 2026-10-31

- `BRIEFING_LIVE=1 .venv/bin/pytest tests/test_live.py -v` — the T2.3 live smoke run.
  Denied because the leading `VAR=value` assignment stops the command from matching
  the `Bash(.venv/bin/pytest *)` allowlist rule. **Not re-tried as a variant**, so
  what follows is documented, not worked around.

  **The fix, if the owner wants these runnable autonomous:** add
  `Bash(BRIEFING_LIVE=1 .venv/bin/pytest *)` to `permissions.allow` in
  `.claude/settings.json`. CLAUDE.md forbids me from editing that file, so this one
  needs the owner.

  Local Firecrawl **is** up now, so only this permission stands between T2.3 and
  done. Until then it lives in `BLOCKED.md` with the box unchecked. The tests skip
  cleanly without the variable, so `make check` is unaffected.

- `.venv/bin/python - <<'PY' ... PY` (a 6-line script that patched `at=` to `now=`
  in `tests/test_briefing.py`) — I wanted a small in-place text substitution rather
  than four separate `Edit` calls. Denied: CLAUDE.md forbids heredocs and the
  allowlist has no heredoc rule. **Not re-tried as a variant**; re-done with the
  `Edit` tool, which is the dedicated tool for this and the one CLAUDE.md says to
  prefer anyway. The heredoc was the wrong tool regardless of the allowlist —
  `Edit` was available, correct, and cheaper. Nothing needed from the owner.

- `git check-ignore -v .claude/.stop_gate_count` — I wanted to confirm why a
  `.gitignore`d file still shows as modified in `git status` (it is tracked from
  T0.1, so gitignore does not apply to it). Denied. **Not re-tried.** The answer is
  already known from git's own behaviour and does not need a live check: a tracked
  file stays tracked regardless of `.gitignore`, so `gitignore` only stops *new*
  files being added. Nothing needed from the owner.

## 2026-10-27

- `git -C /home/spectre/Documents/morning add -A` — T0.0 wanted to stage the four
  new note files for the first commit. Denied because the `-C <path>` prefix stops
  the command from matching the `Bash(git add *)` allowlist rule. Not re-tried as a
  variant; re-run in the permitted bare form from the repo root instead (the
  allowlist clearly intends `git add` to be allowed). **Fix if you want the `-C`
  form**: add `Bash(git -C *)` to `permissions.allow` in `.claude/settings.json`.

- `.venv/bin/briefing research "kenya news" 2>&1 | tail -5; echo "exit=..."`
  — probe T0.1 to see whether `briefing research "<query>"` dispatches as a
  subcommand. Denied because the allowlist has no pipe rules and no `$(...)`,
  so the compound form never reached the matcher. Re-run as the plain single
  command and got the answer (exit 2: single-command Typer app collapses the
  group). **Nothing needed from the owner.**

- `mkdir -p /tmp/scratch` — wanted a scratch dir outside the repo for a Typer
  experiment. Denied even though `Bash(mkdir *)` is allowlisted: the sandbox
  blocks writes outside the project directory. Re-run as an inline
  `.venv/bin/python -c` inside the repo instead. **Nothing needed from the
  owner**; just expect any write aimed outside `/home/spectre/Documents/morning`
  to be refused.

## 2026-11-01
- `git show HEAD:src/briefing/ui.py > /tmp/ui_orig.py` — I wanted the pre-fix copy beside the
  patched one to prove my glue-detection probe could actually detect the glue. Worked around by
  temporarily reverting the fix with Edit, running the probe, then re-applying it.

- `BRIEFING_STATE_DIR=/tmp/briefing-smoke .venv/bin/briefing whitelist add ...` — I wanted
  one manual smoke run of the new T3.4 subcommands against a throwaway state dir, to read
  the output as a user sees it rather than only through CliRunner assertions. The
  `CliRunner` tests cover the same code path (same argv parsing, same `typer.echo`),
  so nothing is unverified — only the "does the real entry point behave" question is,
  which `.venv/bin/briefing --help` in the allowlist would also answer. Not blocking.

## Not a denial, but blocking: local Firecrawl is not running

`curl -s -m 5 http://localhost:3002/` returns exit 7 (`connection refused`),
i.e. nothing is listening on that port. CLAUDE.md assigns Docker to the owner.
The local-first fetch router (SPEC section 4) and the `@pytest.mark.live` smoke
tests (T2.3) both need it. Please run `docker compose up -d` in the Firecrawl
project when you get a chance.


