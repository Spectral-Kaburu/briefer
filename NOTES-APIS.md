# NOTES-APIS

Verified facts about the third-party APIs this project calls, each with the source
URL it was verified against. No guessing: anything unverified is marked UNVERIFIED.

Status: stub. Populated by **T0.3**.

Sections planned:
- Firecrawl local/cloud: base paths, scrape request/response fields, `/search`
  request/response shape, health check, error codes (from
  `https://docs.firecrawl.dev/api-reference/errors.md`).
- `ddgs`: `text()` / `news()` signatures, `timelimit` accepted values, `region`
  codes, rate-limit behaviour.
- Typer / Rich: the specific APIs used by `cli.py` and `ui.py`.
- Gemini via `google-genai`: only what `synth.py` needs.
