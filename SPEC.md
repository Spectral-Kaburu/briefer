# Briefing: Project Spec (v3)

> **Status**: Planned / Not implemented
> **Language**: Python
> **Origin**: Extracted from `todo-morning-news-briefing.md` (Aether docs `06-tools`, `08-implementation-plan`, `10-nudge-microservice`)

## Decisions log

| Decision | Choice |
|---|---|
| Language | Python |
| Whitelist granularity | **Whole domain** (subdomains included) |
| Auto-whitelist trigger | Barrier errors only (section 4). Dead-link and "API wouldn't fix it" errors never whitelist and never use the API |
| Summaries | Extractive by default; `--llm` opt-in (assumed, easy to change) |
| CLI | Typer + Rich, colored, self-explanatory |
| Search | DuckDuckGo via `ddgs`; local Firecrawl `/search` as backup |

---

## 1. Goal

A Python CLI + service that searches the web, extracts pages, and returns clean, summarized results. Two modes:

1. **Research mode** (terminal-first): ask a question, get the heading and a short summary of each relevant page.
2. **Briefing mode** (scheduled): topic-driven morning news, fed to an LLM to produce a spoken briefing for Aether via `voice-tts`.

Aether becomes a *client* of this project instead of owning the fetching logic.

---

## 2. The headline use case

```bash
briefing -r "What is the latest breaking news in kenya"
```

1. **Interpret the query**: words like *latest / breaking / today / news* switch DDG to **news search** with a recency filter (past day, widening to week if too few results); otherwise normal web search.
2. **Search DuckDuckGo** for ~10 results.
3. **Extract every page concurrently** (default 5 at a time) through the fetch router (section 4).
4. **Read each page**: heading (Firecrawl metadata title / first `h1`) + short summary (2-3 sentences).
5. **Print colored results** with source, method badge and URL, then a stats footer.

### Summaries
- **Default (offline, instant)**: extractive: first substantive paragraph(s), trimmed to ~2 sentences, nav/cookie/boilerplate stripped.
- **`--llm`**: Gemini abstractive summary of heading + extracted text.
- **`--synth`**: one overall answer built from all page summaries, with source numbers.

---

## 3. CLI design (Typer + Rich)

| Command | What it does |
|---|---|
| `briefing -r "<query>"` | Shorthand for `briefing research "<query>"` |
| `briefing research "<query>"` | Search + extract + summarize |
| `briefing run` | Run the morning briefing now (scheduler entry point) |
| `briefing whitelist list / add / remove / prune` | Manage API-allowed domains |
| `briefing status` | Health check: local Firecrawl, API key/credits, DDG, daily API usage |
| `briefing config` | Show effective config and where it lives |

Research flags: `-n/--results 10` · `--time day|week|month|any` · `--news/--web` · `--llm` · `--synth` · `--no-api` · `--json` · `--plain`

### Look and feel
- Live progress: one spinner line per URL, updating in place, ending in a status badge.
- Method badges: 🟢 `LOCAL` · 🟡 `API` · ⚪ `SNIPPET` (extraction failed, DDG snippet only) · 🔴 `FAILED`.
- Result card: rank, **bold heading**, dim domain + age, short summary, URL.
- Footer panel: `10 found · 8 extracted (7 local, 1 API) · 2 snippet-only · whitelist +1 (example.co.ke: captcha) · 6.4s`
- Errors in plain English with a fix hint (e.g. `Local Firecrawl not reachable at localhost:3002. Run: docker compose up -d`).
- Respects `NO_COLOR`; auto `--plain` when piped.

```
 briefing ▸ research
 Query: "What is the latest breaking news in kenya"   mode: news · past 24h

 ✔ LOCAL  nation.africa      ✔ LOCAL  standardmedia.co.ke
 ✔ LOCAL  the-star.co.ke     ⚠ API    example.co.ke  (captcha → whitelisted)

 1  Heading of the first story                           LOCAL
    nation.africa · 2h ago
    Two-sentence summary, cleaned of navigation and cookie text.
    https://nation.africa/...
```

---

## 4. Fetch router and auto-whitelist

### Flow

```
URL ──► Local Firecrawl (Docker)
          │
      success ──► return
          │
        error ──► classify (table below)
                     │
        ┌────────────┼──────────────────────┐
     BARRIER      DEAD / NOT SOLVABLE     LOCAL DOWN
        │             │                      │
   whitelist domain   snippet only,      API for this run,
   + retry via API    nothing whitelisted  nothing whitelisted
```

### Error classification (`fetch/errors.py`, pure function, table-driven tests)

| Class | Examples | Action |
|---|---|---|
| **BARRIER** | captcha / anti-bot / Cloudflare challenge page; HTTP 403; 429; 408 or timeout; `SCRAPE_ALL_ENGINES_FAILED`; empty or near-empty content (JS render failed) | Auto-whitelist the **domain**, retry via API |
| **TRANSIENT 5xx** | plain 500 / 502 / 503 / 504 from the target without an engines-failed code | One local retry with backoff; if it still fails → snippet only, **no** whitelist, **no** API |
| **DEAD** | 404, 410, `SCRAPE_SITE_ERROR` (unrecoverable site error), `SCRAPE_DNS_RESOLUTION_ERROR`, invalid URL, redirect loop | Snippet only. No whitelist, no API |
| **NOT SOLVABLE BY API** | **501**, 400, 405, 414, 451, unsupported / binary content type | Snippet only. No whitelist, no API |
| **CONFIG** | 401 / 407 from the *local* instance (auth or proxy misconfig) | Show config error with fix hint. No whitelist, no API |
| **LOCAL DOWN** | connection refused / local timeout reaching Firecrawl itself | Use API for this run only, **do not** write whitelist entries, tell the user |
| **API-SIDE** | API 402 (credits), API 429, API 401 | Stop using API for the run, show a clear message, degrade to snippet only |

> The table is the single source of truth: extend it rather than adding ad-hoc `if` branches.
> Firecrawl error codes and statuses come from `https://docs.firecrawl.dev/api-reference/errors.md`; confirm against it when implementing.

### Whitelist rules
- Stored in `whitelist.yaml`; entries are **hosts without `www.`**. A host matches if it equals the entry or ends with `.` + entry.
- Entry fields: `domain`, `added_at`, `reason`, `source` (`auto`/`manual`), `api_hits`, `verified`.
- `verified` becomes true after the first API success. If the API also fails, the entry stays unverified and `whitelist prune` removes it.
- `prefer_api_for_whitelisted` (default false): when true, whitelisted domains skip local and go straight to the API.
- `api_daily_limit` caps API calls; at the cap, fetches degrade to snippet-only but entries are still recorded.
- Whitelist writes are atomic (temp file + rename) and safe under concurrent fetches.
- Every API call is logged: URL, domain, reason, time, outcome.

---

## 5. Search

- **Primary**: `ddgs` (`news()` for news-style queries with `timelimit`, `text()` otherwise).
- **Backup**: local Firecrawl `/search`. Firecrawl release notes say self-hosted search was switched to DuckDuckGo (v2.8.0), so this gives a second path when `ddgs` is rate-limited. Verify it against your running instance.
- Both return a normalized `SearchResult(title, url, snippet, source, age)`.

---

## 6. Briefing mode (carried over)

- **Trigger**: scheduled time (e.g. `08:00` via Nudge / systemd timer) or first startup of the day (`system.hello`).
- **Context**: yesterday's session summary (`PopLastSessionSummary()`, destructive) + fresh headlines per configured topic.
- **Blocklist**: no crypto / finance-speculation topics (briefing mode only).
- **Dedup**: headline hashes in `seen_headlines.json` (7-day TTL); `schedule_id` `morning_briefing_YYYY-MM-DD` suppresses re-fire for 24h.
- **Gate**: never fire while `audio.state == "speaking"`.
- **Delivery**: IPC `audio.speak` → `voice-tts -s aether -p high "<text>"`.
- **Synthesis**: Gemini prompt (`prompts/briefing.txt`): name + session summary + headlines → 2-3 spoken sentences, under ~45s.
- **Failure fallback**: if all fetching fails, plain session greeting.

---

## 7. Stack

| Concern | Choice |
|---|---|
| CLI | `typer` + `rich` |
| HTTP | `httpx` (async) |
| Search | `ddgs` |
| Models/config | `pydantic` + YAML, `platformdirs` for paths |
| Concurrency | `asyncio` + semaphore |
| LLM (optional) | `google-genai` |
| Packaging | `pyproject.toml`, `src/` layout, `briefing` entry point |
| Tests | `pytest`, `pytest-asyncio`, `respx`; UI snapshot tests with `Console(record=True)` |
| Lint/format | `ruff` |

```
briefing/
├── pyproject.toml  Makefile  README.md
├── src/briefing/
│   ├── cli.py  ui.py  search.py  extract.py  synth.py
│   ├── pipeline.py        # search → fetch → extract orchestration
│   ├── fetch/ router.py local.py api.py errors.py
│   ├── whitelist.py  briefing.py  deliver.py  config.py
├── prompts/briefing.txt
├── config/briefing.yaml.example  whitelist.yaml
└── tests/
```

### Config sketch
```yaml
news_topics: ["technology", "world news"]
search:    {results: 10, region: ke-en, auto_news_detect: true}   # verify region code accepted by ddgs
firecrawl:
  local: {base_url: http://localhost:3002, timeout_s: 30, concurrency: 5}
  api:   {base_url: https://api.firecrawl.dev, key_env: FIRECRAWL_API_KEY, daily_limit: 20}
whitelist: {auto_add: true, path: whitelist.yaml, prefer_api_for_whitelisted: false}
summary:   {mode: extractive, sentences: 2}
blocklist_topics: [crypto, bitcoin, trading, forex, stocks]
dedup:     {headline_ttl_days: 7, schedule_window_hours: 24}
```

---

## 8. Prior art (researched 2026-10-26)

Nothing found that combines **self-hosted-first routing + auto-whitelisted API fallback + colored research CLI**. Closest projects, worth reading before building:

| Project | What it does | What to look at |
|---|---|---|
| `andyliszewski/webcrawl-mcp` | MCP server: DuckDuckGo search + free-library scraping, Firecrawl only as a last-resort fallback for low-quality extractions | How it judges "low-quality extraction" (feeds our empty-content heuristic) |
| `pi-web-toolkit` (ADR 0001) | Local-first search/scrape; Firecrawl keyless cloud fallback only when a local backend errors | Its fallback-only design and "never leave the user worse off than local" rule |
| `KB01111/research-mcp` | Searches 9 sources, scrapes (Firecrawl → Tavily → Jina), reranks, summarizes with a free-LLM chain ending in extractive | Summarizer fallback chain and scraper chain |
| GPT Researcher | Full research-report agent; supports `SCRAPER=firecrawl` incl. self-hosted | Heavier than needed, but shows self-hosted Firecrawl wiring |
| `firecrawl/cli` (official) | `firecrawl search "..." --scrape` does search + scrape in one command; cloud-oriented | Output shape and flags; no fallback/whitelist logic |

Caveat: judged from READMEs/ADRs, not full source reads.

---

## 9. Remaining open questions
1. Is extractive-by-default fine, with `--llm` opt-in?
2. What daily API cap (default 20)?
3. Which ddgs region code should be the default? (`ke-en` is unverified.)
