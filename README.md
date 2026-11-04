# briefing

Search the web, extract pages, and return clean, summarized results — from the
terminal or on a schedule.

```bash
briefing -r "What is the latest breaking news in kenya"
```

Extraction is **local-first**: a self-hosted Firecrawl does the work, and the cloud
API is only spent when a page genuinely blocks the local scrape (see `SPEC.md`
section 4). Everything works with no API key at all — pages that would need the
key fall back to the search snippet rather than failing.

---

## Requirements

- Python 3.11+
- A running **local Firecrawl** at `http://localhost:3002` (see below). Without it
  every result degrades to a snippet, so it is worth starting.
- Optional: a `FIRECRAWL_API_KEY` for the cloud fallback, and `google-genai`
  (`pipx install 'briefing[llm]'`) for `--llm` summaries.

## Install

```bash
pipx install -e .
```

Editable, so `src/` edits take effect without reinstalling. To install without
`pipx` (or if your `pipx` predates `--editable`, which is what `-e` relies on):

```bash
python -m venv .venv && .venv/bin/pip install -e .
```

`--editable` (`-e`) is a real `pipx install` flag — verified on 2026-11-04 from
`pipx install --help` ("Install a project in editable mode") — so either line
above gives an editable install and edits to `src/briefing/` take effect with no
reinstall.

## Start the local Firecrawl

Docker lifecycle is yours to run; this is what the project expects.

```bash
git clone https://github.com/firecrawl/firecrawl
cd firecrawl
git checkout v2.11.162      # the version briefing's paths were written against
docker compose up --build -d
```

It listens on `http://localhost:3002` and needs no API key. Confirm it before
anything else:

```bash
briefing status
```

## Usage

### Research mode

```bash
briefing -r "query"                       # shorthand
briefing research "query"                 # the same thing
```

DuckDuckGo finds about 10 results; each page is then extracted through local
Firecrawl, falling back to the cloud API only where SPEC section 4 allows it.
Words like *latest*, *breaking* and *today* switch the search to news mode;
`--news` and `--web` force it either way.

| Flag | Effect |
|---|---|
| `-n`, `--results N` | How many results to fetch |
| `--time day\|week\|month\|any` | Recency window |
| `--news` / `--web` | Force the search mode |
| `--llm` | Gemini summaries instead of extractive |
| `--synth` | One overall answer built from all summaries |
| `--no-api` | Never spend a cloud API call |
| `--json` | Emit the report as JSON (for scripts and Aether) |
| `--plain` / `--no-plain` | Colour off/on (auto when piped) |
| `--verbose` | Log each routing decision to stderr and the log file |

Sample output lives in [`docs/sample-output.txt`](docs/sample-output.txt),
rendered by the real UI at 100 and 80 columns. Regenerate it with
`scripts/render_samples.py` — do not edit it by hand. This is the happy path —
everything local, nothing spent:

```
 briefing ▸ research
 Query: "kenya economy"   mode: text
✔ LOCAL  nation.africa
✔ LOCAL  businessdaily.africa
╭──────────────────────────────────────────────────────────────────────────────────────────────────╮
│ 1  Kenya's economy grew 5.2% last quarter  LOCAL                                                 │
│ nation.africa · 2h ago                                                                           │
│ Kenya's economy grew 5.2 percent last quarter. Agriculture and services drove the expansion.     │
│ https://nation.africa/kenya/economy-grew                                                         │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
  5 found · 2 extracted (2 local) · 6.4s
```

### Briefing mode

```bash
briefing run              # the morning briefing now
briefing run --dry-run    # print what would be spoken, speak nothing
```

Runs the 24-hour gate, then the audio gate, then headlines per unblocked topic
minus the ones already seen, then synthesis, then delivery via `voice-tts`. A
refused run says why.

The IPC contract with Aether (`docs/AETHER_INTEGRATION.md`) and the systemd units
in `systemd/` (08:00 Africa/Nairobi) are ready if you want Aether to schedule the
briefing. Until then, run `briefing run` yourself or from your own cron — there is
no Aether dependency for that.

### Whitelist

A domain is added automatically when local extraction hits a **barrier** (captcha,
403, 429, empty content) — that is the only trigger, because a barrier is the one
failure the cloud API can fix. Dead links and pages the API cannot help never get
whitelisted and never spend a call.

```bash
briefing whitelist list              # every domain and why it is there
briefing whitelist add example.co.ke # force one
briefing whitelist remove example.co.ke
briefing whitelist prune             # drop auto entries the API could not get past
```

### Health check and config

```bash
briefing status    # local Firecrawl, API key, ddgs, daily API usage, log file
briefing config    # effective values and where they live
```

`status` prints the log file's path so a bug report can point at it, and both
`status` and `config` name the API key's env var without ever printing its value.

## Configuration

Every value below is a default, so copy what you want to change to
`~/.config/briefing/briefing.yaml`. The full commented file is
[`config/briefing.yaml.example`](config/briefing.yaml.example).

```yaml
news_topics: ["technology", "world news"]
search:    {results: 10, region: us-en, auto_news_detect: true}
firecrawl:
  local: {base_url: http://localhost:3002, timeout_s: 30, concurrency: 5}
  api:   {base_url: https://api.firecrawl.dev, key_env: FIRECRAWL_API_KEY,
          daily_limit: 20, timeout_s: 30}
whitelist: {auto_add: true, path: whitelist.yaml, prefer_api_for_whitelisted: false}
summary:   {mode: extractive, sentences: 2}
blocklist_topics: [crypto, bitcoin, trading, forex, stocks]
dedup:     {headline_ttl_days: 7, schedule_window_hours: 24}
timeouts:  {per_url_s: 30, transient_backoff_s: 0.5}
briefing:  {name: Aether}
```

**The API key is never in a file.** `firecrawl.api.key_env` names the environment
variable that holds it (`FIRECRAWL_API_KEY` by default), and it is read at call
time. It is also filtered out of every log line, so a key can reach neither the
log nor `--json` output — `tests/test_log.py` pins that.

State lives under `~/.local/state/briefing/` (override with `BRIEFING_STATE_DIR`):
the whitelist, dedup store, API call log, `briefing.log`, and Aether's audio state.

## Development

```bash
make check     # ruff + pytest, the gate for every commit
make test      # pytest alone
.venv/bin/briefing status
```

Tests are offline by default: `respx` mocks the transports, and the state dir is a
tmp dir. Tests marked `@pytest.mark.live` hit the real ddgs and local Firecrawl
and only run with `BRIEFING_LIVE=1`:

```bash
BRIEFING_LIVE=1 .venv/bin/pytest -m live
```

That prefix form needs `Bash(BRIEFING_LIVE=1 .venv/bin/pytest *)` in the
allowlist — see `DENIED.md` if it is refused.

## Documentation

| File | What it holds |
|---|---|
| `SPEC.md` | The specification; section 4 is the error-classification source of truth |
| `PLAN.md` | Ordered tasks, each with its acceptance check |
| `PROGRESS.md` | What landed and how it was verified |
| `SETUP.md` | How the agent build was started (owner/operator facing) |
| `DECISIONS.md` | Every decision worth arguing about, with its reason |
| `NOTES-APIS.md` | Verified facts about Firecrawl and ddgs, with the source |
| `docs/AETHER_INTEGRATION.md` | The IPC contract Aether must provide |
| `docs/sample-output.txt` | Real UI renderings, at 100 and 80 columns |

## Status

Under construction — see `PLAN.md` for what remains and `PROGRESS.md` for what has
landed and how each piece was verified. Phases 0–5 are complete; Phase 6 (this
README, hardening, docs) is in progress.
