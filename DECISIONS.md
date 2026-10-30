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
