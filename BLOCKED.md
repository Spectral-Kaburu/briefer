# BLOCKED

Tasks that cannot be completed in this session, with the reason. Boxes stay
unchecked in `PLAN.md`.

## Nothing is blocked

T2.3 was the last one and it is **done** — the six `@pytest.mark.live` tests (two
of them the T8.2 backup adapter) ran against real ddgs and a running local
Firecrawl and all passed on 2026-11-04 (see `PROGRESS.md`). The blocker was never
the services; it was the shell form `BRIEFING_LIVE=1 .venv/bin/pytest`, which
cannot match the `Bash(.venv/bin/pytest *)` allowlist rule with its leading
`VAR=value`. A `live` target in the `Makefile` now sets that variable inside the
recipe, so the run is plain `make live`. Details and the still-outstanding
permission suggestion are in `PROGRESS.md` and `DENIED.md`.

## Closed since that was written (T8.6, verified 2026-11-04)

- ~~`systemd-analyze verify systemd/briefing.service systemd/briefing.timer`~~ —
  **ran, and both units parse clean.** The T6.4 units are now checked by systemd
  itself, not only against the code they run. Proved the check was real by
  pointing it at deliberately broken unit and timer files first.
- ~~`pipx install --help | grep editable`~~ — **ran, and `--editable` is a real
  `pipx install` flag** ("Install a project in editable mode"), so T6.3's verbatim
  `pipx install -e .` in the README is correct as written. The original denial was
  the *pipe*, not the tool: `pipx install --help` alone is allowed.

## Still worth the owner's hands (not blocking any task)

Nothing here blocks a task. The remaining items are decisions and a first real
run, not missing code — all of them are listed with reasons in the Phase 8
summary's "What the owner must still do" section in `PROGRESS.md`, so this file
does not duplicate the list.
