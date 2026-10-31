# DECISIONS

Judgment calls made during the autonomous build, with the reason. Newest last.

---

## D2 — 2026-10-27 · Firecrawl error codes SPEC names that do not exist in v2 (T0.3/T1.1)

SPEC §4 names `SCRAPE_ALL_ENGINES_FAILED`, `SCRAPE_SITE_ERROR`,
`SCRAPE_DNS_RESOLUTION_ERROR`. None appear in the current v2 error reference
(`api-reference/errors.md`), and a probe of the v1 source path 404'd. They are
v1-era strings. **Decision**: the classifier (T1.1) matches them by
prefix/substring (`SCRAPE_` family) rather than exact equality, so both v1 and v2
spellings classify, and each maps to the class SPEC assigns it
(BARRIER / DEAD / DEAD). Record the real strings verbatim in `NOTES-APIS.md` once
the local instance can emit them.

## D3 — 2026-10-27 · `.claude` excluded from ruff rather than editing the hooks

`.claude/hooks/stop_gate.py` has two blind `except Exception` (BLE001).
`CLAUDE.md` forbids editing `.claude/hooks/`. **Decision**: add `.claude` to
`[tool.ruff] exclude`. **Reason**: it is harness plumbing the owner wrote, not
project source; failing `make check` over it would block every task, and editing
it is forbidden. Cost: those two spots stay unlinted, which is acceptable because
they are out of scope for this project.

## D4 — 2026-10-27 · `pip install -e ".[dev]"` replaced by two installs (T0.1)

The T0.1 acceptance names one command, but pip's editable build requires `src/`
to exist at *build* time, and `src/` only appears once `cli.py` is written.
**Decision**: deps via plain `pip install <pkgs>`, then `pip install -e . --no-deps`.
**Reason**: same resulting environment, and the acceptance's real intent (deps
present + `briefing --help` exits 0) is met. The shipped `.venv` was also stale —
created at `~/Documents/hop` and copied here, so every shebang was broken;
`python3 -m venv --clear .venv` fixed it.

## D1 — 2026-10-27 · Missing skills are skipped, not worked around (T0.0)

`skill-creator` and `mcp-builder` are named by PLAN.md but are not installed.
**Decision**: skip those optional tasks rather than hand-rolling a skill or an MCP
server. **Reason**: both are Phase 7 optional, non-blocking, and writing them by
hand would produce an unmaintained artifact plus scope creep. Logged in
`PROGRESS.md` under T0.0.

## D7 — 2026-10-31 · An unreadable audio state is treated as "not speaking" (T5.2)

SPEC §6 gates the briefing on `audio.state == "speaking"`. Aethr writes that state
to a file (`FileAudioState`), so there are four ways the read can fail: no file
(never written), corrupt JSON, a JSON value that is not a mapping, and a provider
that raises. **Decision**: all four mean "not speaking", and the gate opens.
**Reason**: SPEC §6 turns on a state we do not know, not on our own ability to
read it. The failure mode of the opposite choice is worse in both directions — a
gate that never opens is a briefing that silently never speaks, and an
unreachable Aether socket would wedge it permanently with no log line. An open
gate with an unreadable state merely risks talking over the user once, which is
recoverable and visible. The provider stays injectable so the owner can invert
this without touching `Gate`.

## D8 — 2026-10-31 · The pipeline's own timeout maps to TRANSIENT_5XX (T3.1)

SPEC §4 has no row for "the pipeline gave up on a slow URL". The timeout comes from
`pipeline.py`, not from Firecrawl, so it is not really a target 5xx. **Decision**:
report it as `TRANSIENT_5XX`, which the router treats as snippet-only with no
whitelist write and no API retry. **Reason**: that is the least-privileged outcome
on the table and matches what a 503 would have produced; any other class risks
spending an API call on a URL that never answered locally. `warning` carries the
real text ("Timed out after Ns."), so nothing is lost.

## D9 — 2026-10-31 · Duplicate search hits fetch once (T3.1)

Search can return the same URL twice (two queries, one topic list; or a promotion
that also appears organically). **Decision**: the first, best-ranked hit wins; the
second is dropped. **Reason**: fetching twice wastes an API call at the cap, and
`Report.found` would count one page as two, corrupting the footer's "N found" line.
Rank-shifting was the alternative and is wrong: dropping should renumber the
cards, but rank is the hit's *search* position and the UI cards are numbered from
it — so `rank` is reassigned after `gather` settles, sequentially and in search
order, which is the same numbering with no gap.
