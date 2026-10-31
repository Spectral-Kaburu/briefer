# NOTES-APIS

Verified facts about the third-party APIs this project calls, each with the source
URL it was verified against. No guessing: anything unverified is marked UNVERIFIED.

Anything below marked UNVERIFIED must be checked before the code that depends on it
is written.

---

## 1. Firecrawl — scrape

Verified from `https://docs.firecrawl.dev/features/scrape.md` and
`https://docs.firecrawl.dev/features/search.md`.

| Item | Verified fact |
|---|---|
| Endpoint | `POST {base_url}/v2/scrape` |
| Cloud base URL | `https://api.firecrawl.dev` |
| Self-hosted | same `/v2/scrape` path on the local base URL (SPEC uses `http://localhost:3002`) |
| Request fields | `url` (string), `formats` (array), `maxAge` (number), `zeroDataRetention` (boolean), `onlyMainContent` (bool, default `False` per docs) |
| Response shape | `success` (bool), `data`, `data.markdown` (string), `data.html` (string), `data.metadata.statusCode` (int) |
| Status code meaning | `data.metadata.statusCode` 2xx or `304` = clean page load |
| Auth | cloud requires `Authorization: Bearer <key>` (not demonstrated in the scrape doc line quoted; **treat as required for cloud**) |

Notes captured verbatim from the doc page: *"No API key needed to get started."*
This refers to the **cloud** free tier starter flow and must not be read as
"self-hosted needs no key". Self-hosted instances are normally keyless but the
cloud API needs a key. `SPEC.md` section 7 stores the key in
`FIRECRAWL_API_KEY` and `config.py` reads it at call time.

## 2. Firecrawl — search

Verified from `https://docs.firecrawl.dev/features/search.md`.

| Item | Verified fact |
|---|---|
| Endpoint | `POST {base_url}/v2/search` |
| Request fields | `query` (string), `limit` (integer), `sources` (array) |
| Response | `success`, `data` |

**Not verified**: the doc page for search does not mention DuckDuckGo and the
SPEC claim that "self-hosted search was switched to DuckDuckGo in v2.8.0" is
**UNVERIFIED from the docs page**. Treat the backup-search task (T2.2) as needing
a live probe against the running local instance before relying on the shape.

### T2.2 additions — verified against the doc page

| Item | Verified fact |
|---|---|
| Web result fields | `url`, `title`, `description`, `category` |
| Scraped result fields | `title`, `description`, `url`, `markdown`, `links`, `metadata` |
| `sources` accepts | `web`, `news`, `images`, `alexandria` |

So a `sources: ["web"]` backup search maps to `SearchResult` as
`url`→url, `title`→title, `description`→snippet. The `category` field has no home
in SPEC §5's five fields and is dropped; news items also carry a date we could map
to `age`, but which field name the self-hosted instance actually emits is
UNVERIFIED until a live probe — `T2.3` is the task that runs one.

## 3. Firecrawl — error codes

Verified from `https://docs.firecrawl.dev/api-reference/errors.md`.

Documented HTTP statuses: **400, 401, 402, 403, 404, 408, 409, 413, 422, 429,
500, 502, 503, 504**.

Codes present on that page:

| Status | Code / meaning |
|---|---|
| 400 | `Bad Request`, `Invalid URL`, `THIRD_PARTY_DATA_UNSUPPORTED_URL`, `THIRD_PARTY_DATA_UNSUPPORTED_OPTION` |
| 401 | `Unauthorized: Invalid token` |
| 402 | `Payment Required: Insufficient credits` |
| 403 | `Forbidden`, `SCRAPE_PROMPT_INJECTION_DETECTED`, `THIRD_PARTY_DATA_NOT_ENABLED`, `THIRD_PARTY_DATA_ENRICHMENT_NOT_ENABLED`, `THIRD_PARTY_DATA_TERMS_REQUIRED`, "we do not support this site..." |
| 404 | `Not Found`, `THIRD_PARTY_DATA_NOT_FOUND` |
| 408 | `Request Timeout` |
| 409 | `Conflict` |
| 413 | `Payload Too Large` |
| 422 | `Unprocessable Entity`, extraction schema error |
| 429 | `Rate limit exceeded`, `Concurrency limit reached` |
| 500 | `Internal Server Error` |
| 502 | `Bad Gateway` |
| 503 | `Service Unavailable` |
| 504 | `Gateway Timeout` |

Agent codes on the same page: `zdr_not_enabled`, `zdr_threads_unsupported`.
Trace codes: `cancelled`, `credit_limit_reached`, `parent_finished`, `refused`,
`internal`.

### IMPORTANT — codes SPEC names that are NOT on that page

SPEC section 4 names these strings as Firecrawl error codes:
**`SCRAPE_ALL_ENGINES_FAILED`, `SCRAPE_SITE_ERROR`, `SCRAPE_DNS_RESOLUTION_ERROR`**.
None of the three appear on `api-reference/errors.md` (the v2 error reference), and
a source-level probe (`apps/api/src/lib/errors.ts` on `firecrawl/firecrawl`) 404'd.
They exist in older Firecrawl (v1-era) error strings.

**Decision (D2)**: implement the classifier so those three codes are matched as
documented SPEC classes (BARRIER, DEAD, DEAD respectively) but recognise them by
**substring match** (`SCRAPE_` prefix group) rather than exact equality, so both
v1 and v2 code spellings classify correctly. Any real string observed from the
running instance will be added here verbatim.

## 4. ddgs `9.16.1` (installed, verified in `.venv`)

Verified by inspecting the installed package source:
`.venv/lib/python3.14/site-packages/ddgs/`.

| Item | Verified fact |
|---|---|
| `DDGS().text(query, **kwargs)` | returns `list[dict[str, Any]]` |
| `DDGS().news(query, **kwargs)` | returns `list[dict[str, Any]]` |
| kwargs forwarded | `region: str = "us-en"`, `safesearch`, `timelimit: str \| None = None`, `max_results: int \| None = 10`, `page: int = 1`, `backend: str = "auto"` |
| `timelimit` values | `"d"`, `"w"`, `"m"`, `"y"` (or a custom date range) |
| `region` | passthrough to the engine; **not validated by ddgs** (examples given: `us-en`, `uk-en`, `ru-ru`). `ke-en` is therefore *plausible* but unverified |
| Exceptions | `ddgs.exceptions.DDGSException` (base), `RatelimitException` (rate limit exceeded), `TimeoutException` |
| Result dict keys | text results carry `title`, `url`, `body`/`description`, `date`; **normalize defensively, do not assume all keys are present** |

Source files: `ddgs/ddgs.py`, `ddgs/exceptions.py`.

### Result dict keys, verified per engine (T2.1, from `ddgs/engines/*.py`)

| Engine | Fields it actually emits |
|---|---|
| `duckduckgo.py` (text) | `title`, `href`, `body` — **no `date`, no `source`** |
| `duckduckgo_news.py` (news) | `title`, `url`, `body`, `date`, `source`, `rel` |

So: `text()` results have **no age**, and `news()` results have one. The normalized
`SearchResult.age` is therefore empty for `text()` hits unless the caller supplies
it. Confirmed by reading `engines/duckduckgo.py:21` and
`engines/duckduckgo_news.py:23-24` in the installed package.

## 5. Typer + Rich (for T3.2/T3.3)

Not yet fetched. To verify against `typer.tiangolo.com` and
`rich.readthedocs.io` when implementing the UI layer.

## 6. Gemini via `google-genai` (for T4.1)

Not yet verified.
