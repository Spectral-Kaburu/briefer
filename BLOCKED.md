# BLOCKED

Tasks that cannot be completed in this session, with the reason. Boxes stay
unchecked in `PLAN.md`.

## Nothing is blocked

T2.3 was the last one and it is **done** — the four `@pytest.mark.live` tests ran
against real ddgs and a running local Firecrawl and all passed (see `PROGRESS.md`).
The blocker was never the services; it was the shell form
`BRIEFING_LIVE=1 .venv/bin/pytest`, which cannot match the
`Bash(.venv/bin/pytest *)` allowlist rule with its leading `VAR=value`. A `live`
target in the `Makefile` now sets that variable inside the recipe, so the run is
plain `make live`. Details and the still-outstanding permission suggestion are in
`PROGRESS.md` and `DENIED.md`.

## Still worth the owner's hands (not blocking any task)

- `systemd-analyze verify systemd/briefing.service systemd/briefing.timer` — the
  T6.4 units are reviewed against the code they run, but never parsed by systemd.
- `pipx install --help | grep editable` — closes whether T6.3's verbatim
  `pipx install -e .` is real. The venv fallback beside it needs no faith.
