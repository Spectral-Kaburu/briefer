# BLOCKED

Tasks that cannot be completed in this session, with the reason. Boxes stay
unchecked in `PLAN.md`.

## T2.3 — Live smoke tests for ddgs and local Firecrawl

**Blocked by:** local Firecrawl is not running. `curl -s -m 5 http://localhost:3002/`
returns exit 7 (`connection refused`), i.e. nothing is listening on that port.
Checked 2026-10-31, and repeatedly across earlier sessions.

Three honest attempts were made:

1. `curl -s -m 5 http://localhost:3002/` → exit 7, no listener.
2. `curl -s -m 5 -o /dev/null -w "%{http_code}" http://localhost:3002/` → exit 7, `000`.
3. `docker ps` → read-only check allowed by CLAUDE.md.

The tests themselves are written as `@pytest.mark.live` and skip unless
`BRIEFING_LIVE=1`, so they are harmless to leave in the suite; they simply cannot
be *executed* yet. Everything T2.3 needs is otherwise in place.

**What the owner must do:** `docker compose up -d` in the Firecrawl project, then
re-run `BRIEFING_LIVE=1 .venv/bin/pytest -m live`.

Two UNVERIFIED facts in `NOTES-APIS.md` §2 stay unverified until then, and both
only affect T2.2's already-shipped code:

- The `/v2/search` news-item date field name, which is why `age` is left empty for
  backup results and they sort last.
- The SPEC §5.2 claim that self-hosted search switched to DuckDuckGo in v2.8.0.

Neither changes any shipped behaviour: the fallback maps only the four fields the
doc page verifies (`url`, `title`, `description`, `category`) and drops the rest.

**Not blocking:** T2.2 (mocked, done), T3.1–T3.5 (all mocked), T5.2–T5.4 (all
mocked). Only T2.3 and the live half of T6.5 wait on this.
