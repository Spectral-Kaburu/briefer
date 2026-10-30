# DENIED

Commands the sandbox denied, in `command — why it was needed` form.

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

## Not a denial, but blocking: local Firecrawl is not running

`curl -s -m 5 http://localhost:3002/` returns exit 7 (`connection refused`),
i.e. nothing is listening on that port. CLAUDE.md assigns Docker to the owner.
The local-first fetch router (SPEC section 4) and the `@pytest.mark.live` smoke
tests (T2.3) both need it. Please run `docker compose up -d` in the Firecrawl
project when you get a chance.


