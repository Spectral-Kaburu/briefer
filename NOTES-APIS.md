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

### Transient engine failure (probed 2026-11-03, worth knowing before debugging the adapter)

| Item | Verified fact |
|---|---|
| Search, engines down | `POST /v2/search` still answers **HTTP 200** with `{"success":true,"data":{},"creditsUsed":0}` — an **empty mapping**, not an error. A client that checks the status code sees a valid, empty page and reports "no results" |
| Scrape, engines down | `POST /v2/scrape` answers **HTTP 500** with `code: "SCRAPE_ALL_ENGINES_FAILED"` and an `engines tried: [playwright, fetch]` list, while `GET /` still answers 200 `{"message":"Firecrawl API"}` |
| What it means | the API process is up while its engines are not, so **health/liveness is not evidence that search or scrape will work**. Both states were observed to clear on their own within minutes |

So an empty `data: {}` is a *degradation signal*, not a "the query matched nothing" signal — the
adapter cannot tell the two apart, and `creditsUsed: 0` is the one hint. `FirecrawlSearch` returns
`[]` for both, which is correct (it must never raise), but a live test asserting on non-empty results
can fail against a healthy adapter when the instance is in this state.

So the two suspects are settled, and neither changes shipped behaviour:

1. **`age` stays empty for backup hits** — confirmed, not merely assumed. The
   shipped `SearchResult.from_search` already leaves it empty, so it is *correct*;
   only the comment claiming UNVERIFIED needed retiring.
2. **Any adapter over this endpoint must unwrap `data["web"]`** before it hands
   item dicts to `from_search`. `Searcher._backup_search` already normalizes a
   *list of item dicts*, so the unwrapping belongs in the concrete backup adapter.

**Gap found, worth flagging (T2.2, now closed by T2.2a — 2026-11-02):** T2.2
shipped the fallback *routing* and the *normalization* but no concrete adapter
that actually POSTs to `/v2/search`, so `Searcher(backup=...)` currently has
nothing real to be given in production. This was a T2.2 wiring gap, not a T2.3
blocker — the live test asserts the adapter-side shape so whoever writes it gets
caught if they assume a flat `data` list.

**Closed:** `LocalFirecrawl.search(query, limit)` is that adapter (`fetch/local.py`),
wired at `cli._searcher`. `sources` is always `["web"]`; it unwraps `data["web"]`
and returns raw item dicts, leaving the field mapping to `SearchResult.from_search`.
Unlike `scrape` and `health`, it *raises* on a dead instance — `Searcher._backup_search`
is the single caller that must never fail and already guards it (see D22).
Note that PLAN.md T8.2 revisits this same adapter and asks for more of it (a
`mode` argument, never-raise-with-DEBUG-logging, a CLI-level wiring test, and a
live test); treat T2.2a as the first cut, not the final shape.

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

### Typer + Click (for T3.3)

Verified 2026-11-01 against the **installed sources** (`typer 0.27.3`,
`click 8.5.0`, `.venv/lib/python3.14/site-packages/`) with a throwaway probe app,
because several of these are exactly backwards from what the docs suggest and
each one cost a wrong test in T3.3.

| Item | Verified fact |
|---|---|
| `CliRunner` streams | click 8.5's `Result` **separates `.stdout`, `.stderr` and `.output`**. Click usage errors (`No such option`, `Missing argument`) land on **`.stderr`** with exit code 2. There is no `mix_stderr` parameter any more |
| `typer.Exit(code=n)` | raises `SystemExit(n)`, *not* a message — so it is the clean no-traceback exit. `CliRunner` records it as `.exception` of type `SystemExit`, with `result.exit_code` set |
| `rich_markup_mode=None` | disables Rich's panel help; Click's own help renders instead, at **fixed width**. The default (`"rich"`) wraps against a detected console width, so help snapshots shift between a terminal and a captured stream |
| `invoke_without_command=True` | on the *app* lets the callback run with no subcommand; it must also be set on the callback to combine with `no_args_is_help=True`. This is how a group-level `-r QUERY` option works |
| Group option vs bare positional | with ≥2 commands registered, a bare positional on the group is read as a **subcommand name**, not a query. A one-command Typer app collapses to a bare command, which is why the T0.1 skeleton appeared to accept one |
| Paired flag `--news/--web` | one parameter: passing **both** sets it twice and exits **0** — the last one silently wins, with no error. Mutual exclusion needs two separate booleans plus an explicit check |
| `bool | None` flag | supported as a tri-state option (`typer.Option(None, "--plain/--no-plain")`) |
| `FORCE_COLOR=1` | Rich treats the console as a terminal. The only way a `CliRunner` run can exercise the colored path |
| Styled badge rendering | `Text.assemble(("🟢 ", style), ("LOCAL", style))` renders as **two spans**, so an ANSI code sits between the emoji and the label — `"🟢 LOCAL"` is never a contiguous substring of a colored render. Assert on the stripped export, or on the emoji alone |

**Consequence for this project:** CLI tests assert error text on `result.stderr`
and result text on `result.stdout`. Never write an error-path assertion against
`.stdout` — it will fail against correct code. A colored-path test needs
`monkeypatch.setenv("FORCE_COLOR", "1")` and must not assert the emoji and its
label as one string.

## 6. Gemini via `google-genai` (for T5.3 / T4.1)

Verified against `google-genai` 2.34.0, installed 2026-11-01 (the package, not the
network — the SDK was inspected locally; no key was present, so nothing called the
API). Source: the installed package's `google/genai` module.

| Fact | Verified value |
|---|---|
| Install | `pip install google-genai` (2.34.0) |
| Client | `google.genai.Client(api_key=...)` — the key is the **only** constructor arg this project needs |
| Sync call | `client.models.generate_content(model: str, contents: str, config: GenerateContentConfig \| None)` |
| Async call | `client.aio.models.generate_content(...)` — same signature, returns a coroutine |
| Config | `google.genai.types.GenerateContentConfig(max_output_tokens=..., temperature=...)` — `max_output_tokens` is an int cap, **not** a spoken-seconds cap |
| Response text | `response.text` concatenates the first candidate's parts; `None` when there is no candidate (e.g. `block_reason` set) |
| Blocked | a blocked prompt returns a response with `prompt_feedback.block_reason` set and `.text is None` — it does **not** raise |

**Not verified** (needs a key, i.e. an owner-side live check): model names, whether
`gemini-2.5-flash` still resolves, quota behaviour, actual token-to-seconds ratio.
Keep the model name in config so the owner can change it without a code edit.
