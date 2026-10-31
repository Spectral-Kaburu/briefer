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
**UNVERIFIED from the docs page**. The live probe (below) confirms the instance
returns DuckDuckGo-shaped web results but does not by itself prove which engine
produced them, so the SPEC claim stays UNVERIFIED as a claim about v2.8.0.

### T2.2 additions — verified against the doc page

| Item | Verified fact |
|---|---|
| Web result fields | `url`, `title`, `description`, `category` |
| Scraped result fields | `title`, `description`, `url`, `markdown`, `links`, `metadata` |
| `sources` accepts | `web`, `news`, `images`, `alexandria` |

So a `sources: ["web"]` backup search maps to `SearchResult` as
`url`→url, `title`→title, `description`→snippet. The `category` field has no home
in SPEC §5's five fields and is dropped.

### T2.3 live probe — 2026-10-31, against the owner's running instance

Probed with `curl -s -X POST http://localhost:3002/v2/search` (T2.3's live run
itself is still blocked by a permissions pattern — see `BLOCKED.md`; the facts
below are from direct curl, which CLAUDE.md permits).

| Item | Verified fact |
|---|---|
| `data` shape | **a mapping keyed by source type**, not a flat list: `{"web": [ ... ]}` |
| Item fields | `url`, `title`, `description` — exactly the three the doc page names |
| Date field | **none on any item**, for `sources:["web"]` *or* `sources:["news"]` |
| `sources: ["news"]` | returns items under `data.web` anyway — no news branch, no date |
| Other response fields | `creditsUsed` (int, 2 seen) and `id` (a request id) |

So the two suspects are settled, and neither changes shipped behaviour:

1. **`age` stays empty for backup hits** — confirmed, not merely assumed. The
   shipped `SearchResult.from_search` already leaves it empty, so it is *correct*;
   only the comment claiming UNVERIFIED needed retiring.
2. **Any adapter over this endpoint must unwrap `data["web"]`** before it hands
   item dicts to `from_search`. `Searcher._backup_search` already normalizes a
   *list of item dicts*, so the unwrapping belongs in the concrete backup adapter.

**Gap found, worth flagging:** T2.2 shipped the fallback *routing* and the
*normalization* but no concrete adapter that actually POSTs to `/v2/search`, so
`Searcher(backup=...)` currently has nothing real to be given in production. This
is a T2.2 wiring gap, not a T2.3 blocker — the live test asserts the adapter-side
shape so whoever writes it gets caught if they assume a flat `data` list.

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

## 5. Rich + Typer (for T3.2/T3.3)

Verified 2026-10-31 against the **installed `rich` source** (in
`.venv/lib/python3.14/site-packages/rich/`), which is authoritative over the docs
for what this project actually runs against. Via a subagent that cited
`file:line`; where docs and source differ, source won.

### Console — the snapshot-test pattern

| Item | Verified fact |
|---|---|
| Snapshot | `Console(record=True, width=100)` + `console.export_text()` |
| `export_text(*, clear=True, styles=False)` | default returns **plain text** (control segments filtered); `styles=True` embeds ANSI. **Asserts `record=True`**, else `AssertionError` (`console.py:2204`) |
| `width=100` | fixed width; the `COLUMNS` env is ignored while set |
| `NO_COLOR` | detected **at construction**: `no_color if no_color is not None else os.environ.get("NO_COLOR","") != ""` (`console.py:725-729`). Any non-empty value wins. Changing `os.environ` later does not affect a built Console |
| `is_terminal` | `force_terminal` if not None, else environ-detected. A `record=True` console is **not** a terminal |
| `no_color` | plain instance attribute, so it is settable after construction and honoured at render time (`console.py:2138`) |

### Live — one block, not N Status objects

| Item | Verified fact |
|---|---|
| `Live(renderable, console=, auto_refresh=, refresh_per_second=4, transient=False, redirect_stdout=True, redirect_stderr=True, vertical_overflow="ellipsis")` | full signature, `live.py:57-70` |
| `Status` | is **just a `Live` around one `Spinner`** (`status.py:37-42`), so it is single-line. N `Status` objects each run their own refresh thread and fight over the screen |
| Correct pattern | one `Live` holding a `Group` of lines, updated via `live.update(Group(...), refresh=True)` |
| `transient=True` | on `stop()` emits carriage-return + erase-in-line `height` times (`live_render.py:72-84`) — the block **vanishes**, cursor returns to where Live began |
| `update(renderable, refresh=False)` | `live.py:230-242` |

**Consequence for this project:** a colored run erases the progress block before
the cards print (SPEC §3's design), while a plain/piped run needs it kept, so
`ui.UI` sets `transient = not plain`. A recorded console is non-terminal, so
`Live` writes nothing on `refresh()` and only renders on `stop()` — which is why
the snapshot tests pass `plain=True` when they assert on progress lines.

### Markup safety (load-bearing for this project)

Titles, headings, summaries and URLs arrive from search results, so they must not
be re-parsed as markup:

| Item | Verified fact |
|---|---|
| `Text.append(str, style)` | sanitises control codes but **does not parse `[tags]`** (`text.py:964-1006`) |
| `Text.append(Text, style=...)` | **raises `ValueError("style must not be set when appending Text instance")`** (`text.py:992`). This bit during T3.2 — `Text.assemble` inherits it |
| `Text.assemble(*parts)` | parts are `str`, `(str, style)`, or `Text`; style forbidden only for `Text` parts (`text.py:357-400`) |
| `console.print(str)` | **unsafe** for untrusted text: `RE_TAGS` matches `[`+letter and a malformed tag raises `MarkupError` (`markup.py:106-132`) |
| `console.print(Text(...))` | safe — `Text` implements `__rich_console__`, rendered directly (`console.py:1318`) |
| `Panel(Text(...))` | safe — the renderable is passed straight through, **not** re-parsed as markup (`panel.py:280`) |
| `rich.markup.escape` | escapes brackets, if markup strings are ever needed |

### Panel / Table params actually used

`Panel(renderable, box=, title=, subtitle=, title_align=, subtitle_align=, expand=True, padding=(0,1), width=)`;
`Table(*headers, box=, show_header=, pad_edge=, expand=False, padding=)`.
`box=None` removes borders. `Panel.expand` defaults True, `Table.expand` False.

### Typer (for T3.3)

Not yet read. To verify against `typer.tiangolo.com` when implementing `cli.py`.

## 6. Gemini via `google-genai` (for T4.1)

Not yet verified.
