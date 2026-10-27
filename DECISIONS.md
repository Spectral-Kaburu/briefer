# DECISIONS

Judgment calls made during the autonomous build, with the reason. Newest last.

---

## D1 — 2026-10-27 · Missing skills are skipped, not worked around (T0.0)

`skill-creator` and `mcp-builder` are named by PLAN.md but are not installed.
**Decision**: skip those optional tasks rather than hand-rolling a skill or an MCP
server. **Reason**: both are Phase 7 optional, non-blocking, and writing them by
hand would produce an unmaintained artifact plus scope creep. Logged in
`PROGRESS.md` under T0.0.
