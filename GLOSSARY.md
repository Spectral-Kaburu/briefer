# Briefing Project Glossary

This document provides clear definitions for key terms, concepts, and jargon used throughout the briefing codebase.

## Architecture Concepts

### Modes

**Research Mode** (`-r`, `briefing research`): Searches the web, extracts pages, and produces result cards with summaries. Designed for terminal-first investigation.

**Ask Mode** (`-a`, `--ask`, `--instant`, `briefing ask`): Answers a question using fetched web pages with numbered sources and citations. Produces a concise answer rather than showing all results.

**Briefing Mode** (`briefing run`): Scheduled morning briefing that combines yesterday's session summary with fresh headlines, synthesizes into spoken sentences, and delivers via `voice-tts`.

### Host Normalization

`normalize_host()` converts URLs to a canonical form for storage and matching:
- Removes URL scheme (`https://`, `http://`, `//`)
- Removes `www.` prefix
- Strips port numbers
- Removes path, query string, and fragment
- Converts to lowercase

Example: `"https://WWW.Example.com:8080/a?b=1"` → `"example.com"`

This is used for:
- Whitelist matching (whole-domain rule: `example.com` matches `www.example.com` and `sub.example.com`)
- Host deduplication in ask mode (prevents multiple sources from same domain)

### Seam Pattern (`_PIPELINE`, `_RUNNER`, `_SEARCH`, `_ASK`, `_SPEAKER`, `_AUDIO`, `_DEDUP`, `_SUMMARIZER`)

These are module-level variables that hold the real implementations of collaborators. Tests patch these to inject fakes, allowing the entire collaborator graph to be swapped without touching a running event loop.

Example:
```python
# In cli.py
_PIPELINE = Pipeline
_RUNNER = SubprocessRunner
_SEARCH = _search

# In tests
monkeypatch.setattr(cli_module, "_ASK", fake_ask)
```

## Error Classes (SPEC Section 4)

The fetch router classifies failures into these categories, which determine the action:

| Class | Examples | Action |
|-------|----------|--------|
| **BARRIER** | captcha, 403, 429, Cloudflare challenge, empty render | Auto-whitelist domain, retry via API |
| **TRANSIENT 5xx** | 500, 502, 503, 504 (plain from target) | One local retry; if still fails → snippet only, no whitelist, no API |
| **DEAD** | 404, 410, DNS resolution error, redirect loop | Snippet only. No whitelist, no API |
| **NOT_SOLVABLE_BY_API** | 501, 400, 405, 414, 451, binary content | Snippet only. No whitelist, no API |
| **CONFIG** | 401, 407 from local instance | Show config error with fix hint |
| **LOCAL_DOWN** | Connection refused to local Firecrawl | Use API for this run only, don't whitelist, tell user |
| **API_SIDE** | API 402 (credits), 429, 401 | Stop using API, degrade to snippet only |

## Whitelist

**Purpose**: Domain whitelist for the cloud API path. When local scraping fails with a BARRIER error, the domain is auto-whitelisted and retried via the paid API.

**Behavior**:
- Only BARRIER errors trigger auto-whitelist
- DEAD and API-side errors never whitelist
- `verified` flag: true after first API success
- Expired unverified entries can be pruned

**Storage**: `whitelist.yaml` with entries as hosts without `www.`

**Matching**: Whole-domain rule - `example.com` matches `example.com`, `www.example.com`, and `sub.example.com`

## Summaries

**Extractive Summary** (default): Takes the best sentences from the article body (2-3 sentences, ~320 chars). Available offline, instantly.

**Abstractive Summary** (`--llm`): Gemini summarizes the heading + extracted text. Requires `GEMINI_API_KEY`.

**Synth** (`--synth`): One overall answer built from all page summaries.

## Method Badges

| Badge | Meaning | Color/Style |
|-------|---------|-------------|
| 🟢 LOCAL | Content extracted from local Firecrawl | bold green |
| 🟡 API | Content fetched through cloud API | bold yellow |
| ⚪ SNIPPET | Only search snippet available (extraction failed) | dim |
| 🔴 FAILED | Page could not be processed | bold red |

## Key Terms

### Dedup Store

`DedupStore` tracks seen headlines in `seen_headlines.json` with a 7-day TTL. Used by briefing mode to avoid repeating stories.

### Schedule Gate

`Gate` prevents briefing runs from firing:
1. 24-hour cooldown (same `schedule_id` won't fire twice in window)
2. Audio state check (`audio.state == "speaking"`)

### Pipeline

`Pipeline` orchestrates the research flow: `search → fetch (concurrent) → extract → summarize`. Emits `Event`s for live progress rendering.

### Router

`Router` decides where each URL gets fetched: local Firecrawl first, then API (if whitelisted or local down), finally snippet-only.

### Extractor

`extract()` produces heading + summary from markdown. Can accept a query for query-aware extraction (T9.3).

### SearchResult

Normalized search result with fields: `title`, `url`, `snippet`, `source`, `age`. Returned by both ddgs and Firecrawl search.

### EvidenceSource

In ask mode, represents a numbered source with: `number`, `title`, `url`, `domain`, `method`, `text`, `snippet`.

### Result vs Answer

| Aspect | Result (research) | Answer (ask) |
|--------|-------------------|--------------|
| Input | Pipeline output | Ask mode sources |
| Display | Card list | Answer panel + cards |
| Citation | Implicit (rank number) | Explicit `[1]` style |
| Field names | `heading`, `summary`, `url`, `domain`, `method`, `warning` | Tuple: `(num, title, url, domain, method, text)` |

## Timelimit and Mode Detection

**Auto-Detection**: Queries containing "latest", "breaking", "today", "news" switch to news search with timelimit.

**Modes**:
- `text`: DuckDuckGo web search (default)
- `news`: DuckDuckGo news search

**`--time`**: `day`, `week`, `month`, `any` → ddgs `timelimit` parameter `d`, `w`, `m`, or `None`

## Flow Patterns

### Research Flow
```
query → Searcher (ddgs → Firecrawl backup) → 
Pipeline (semaphore-limited fetch → extract) → 
Report (cards + footer) → UI.render()
```

### Ask Flow
```
query → Searcher → 
Host deduplication (one result per normalized host) → 
Router.fetch with deadline cancellation → 
Evidence builder → Prompt → LLM OR passages → 
Answer.render()
```

### Briefing Flow
```
Scheduler → Gate (24h + audio check) → 
TopicFilter (blocklist) → HeadlineSearch → 
DedupStore → Speaker → Delivery → voice-tts
```