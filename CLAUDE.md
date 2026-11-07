# CLAUDE.md: Briefing project (autonomous build)

You are building the `briefing` Python CLI described in `SPEC.md`, following `PLAN.md`.

## Read first, every session start
1. `SPEC.md` (what to build; error-classification table in section 4 is the source of truth)
2. `PLAN.md` (ordered tasks with skills and acceptance checks)
3. `PROGRESS.md` and `DECISIONS.md` (if present): what is done and what was decided

## The loop (repeat per task)
1. Take the first unchecked task in `PLAN.md` whose dependencies are done.
2. Invoke the skills listed for that task (use the Skill tool).
3. Implement test-first. Run `make check` (ruff + pytest). It must pass.
4. Request review via the `reviewer` subagent for any task marked **[review]**; fix what it finds.
5. Before ticking the box, invoke the `verifier` subagent (or run the task's acceptance command yourself) and confirm it passes.
6. Commit with a simple one-line message: `git add -A` then `git commit -m "T1.2: local firecrawl client"`. Do **not** use heredocs or `$(...)` in commit commands.
7. Tick the task in `PLAN.md` (`- [x]`) and append 1-3 lines to `PROGRESS.md`.

## Permissions: do not trip over them
- The session runs with an allowlist. If a command is denied: **do not retry variants or work around it**. Append `command + why you needed it` to `DENIED.md` and continue with another approach or task.
- Prefer dedicated tools (Read, Edit, Write, Glob, Grep) over shell equivalents.
- Avoid compound commands (`a && b`, `cd x && y`, `$(...)`, heredocs). Use one simple command per call, with relative paths from the repo root.
- Use the project venv: `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/pip`. Prefer `make check`, `make test`.
- Never edit `.claude/settings*.json` or `.claude/hooks/`.

## Hard rules
- No `git push`, no remotes, no `sudo`, no docker lifecycle commands (the owner runs Docker).
- **Never call the real Firecrawl API or Gemini in development or tests.** Tests use `respx` mocks and tmp dirs. Do not read real config/secrets. The owner's keys are intentionally not in your environment.
- Live checks against the local Firecrawl (`curl -s http://localhost:3002/...`) and the live DDG test are allowed but must be marked `@pytest.mark.live` and skipped unless `BRIEFING_LIVE=1`. If local Firecrawl isn't up, skip them and note it in `PROGRESS.md`.
- Never run `voice-tts`. Delivery code uses an injectable runner and a dry-run mode; real audio is tested by the owner afterwards.
- Do not hard-code secrets. API keys come from env vars named in config.

## Code standards
- Python 3.11+, `src/` layout, type hints everywhere, `ruff` clean, small modules, no global mutable state.
- Async I/O with `httpx`; all concurrency through one semaphore in `pipeline.py`.
- Every user-facing error has a plain-English message plus a fix hint.
- Tests: table-driven for classification and whitelist matching; Rich output tested with `Console(record=True, width=100)` snapshots.
- When unsure about a Firecrawl or `ddgs` API detail: fetch the docs (allowed domains) and record the verified facts in `NOTES-APIS.md` instead of guessing.

## Keep context lean
- Delegate doc reading and broad exploration to subagents; keep only conclusions.
- After each task, `PROGRESS.md` must be enough for a fresh session to continue without this conversation.
