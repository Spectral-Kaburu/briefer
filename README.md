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
`pipx`:

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
docker compose up --build -d      # any recent v2 image
```

What `briefing` actually depends on is the **`/v2/` API surface** — `POST /v2/scrape`
and `POST /v2/search`, each returning a `data` envelope — and those are verified
against the docs and the local instance in `NOTES-APIS.md` sections 1 and 2. No
specific image tag is required: pick whichever v2 release you prefer, and if a
future v3 renames the paths, those two sections are where the fact changes.

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

**Use `--dry-run` for your first few, and note what the real one costs.** A dry
run prints the delivery command and speaks nothing, and it records **no**
state — the day stays unfired, so you can run it as often as you like. A real
run does the opposite: it marks that day as fired in the dedup store, so the
08:00 `briefing.timer` (or your own cron) finds the gate shut and skips. Run the
real thing at 07:00 to test it and there will be no 08:00 briefing behind it.

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
`~/.config/briefing/config.yaml`. The name is fixed — only the *content* comes
from [`config/briefing.yaml.example`](config/briefing.yaml.example).

A config file is discovered in this order, and the first one that **exists**
wins. A missing default is silent (a fresh install must not error) and a stale
`$BRIEFING_CONFIG` falls through to the next candidate rather than bricking
every command:

1. `--config PATH` — on `research`, `run` and `config`
2. `$BRIEFING_CONFIG`
3. `<user config dir>/briefing/config.yaml` — `~/.config` on Linux and the
   platform equivalent elsewhere, so `XDG_CONFIG_HOME` moves it
4. Built-in defaults

`status` and the `whitelist` commands have no `--config` flag, so they always
follow 2–4 above.

`briefing config` prints which file is in force and the effective search,
Firecrawl, whitelist, summary, dedup and topic values, so it is the fastest way
to confirm a change took. It does not print `timeouts` or the two names below.

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
briefing:  {user_name: "", name: Aether}
```

**The API key is never in a file.** `firecrawl.api.key_env` names the environment
variable that holds it (`FIRECRAWL_API_KEY` by default), and it is read at call
time. It is also filtered out of every log line, so a key can reach neither the
log nor `--json` output — `tests/test_log.py` pins that.

**Two names, one direction each.** `briefing.name` is who is *speaking* (default
`Aether`); `briefing.user_name` is who is *addressed*, and it is the greeting
that uses it. `user_name` is empty by default — nobody is named until you say
who, and the assistant's own name never stands in for you. They were a single
`name` field until the briefing greeted the assistant.

**`search.region`** goes straight through to DuckDuckGo's `l` parameter, and
`us-en` is both ddgs's default and ours. On 2026-11-04 the candidate regions
(`ke-en`, `wt-wt`, `us-en`) were compared against the same Kenya query: all
three returned the same five hosts, none of them `.ke`. The query decides the
country, not the region, so the default stayed. `make probe-region` re-runs that
comparison — see `NOTES-APIS.md` §4 for the measured result.

**Search has a backup.** When `ddgs` times out or returns nothing, the same
local Firecrawl is asked directly (`POST /v2/search`), and the empty answer is
logged at INFO so a quiet search has a stated reason. The backup never raises:
a connection error, a timeout, a non-2xx response or an unexpected shape
returns no results and logs at DEBUG, so a degraded Firecrawl costs you results
rather than the run.

State lives under `~/.local/state/briefing/` (override with `BRIEFING_STATE_DIR`):
the whitelist, dedup store, API call log, `briefing.log`, and Aether's audio state.

## Development

```bash
make check     # ruff + pytest, the gate for every commit
make test      # pytest alone
make live      # the six @pytest.mark.live tests: real ddgs and local Firecrawl
make probe-region   # re-run the search.region comparison
.venv/bin/briefing status
```

Tests are offline by default: `respx` mocks the transports and the state dir is
a tmp dir. Spawning a process or opening a socket from a test is **blocked**,
not merely discouraged: `tests/conftest.py` installs an autouse fixture that
replaces `subprocess`, `os.system`, `asyncio.create_subprocess_*` and the
`socket` connect and DNS calls with a `RealSideEffectBlocked` raiser, for every
test not marked `live`. It derives from `BaseException`, so a broad
`except Exception` cannot swallow it, and every attempt is recorded in a list
the teardown checks — so even a test that catches the block still fails. Fix a
blocked test by patching the seam (`_RUNNER`, `_SEARCH`, `_AUDIO`, `_SPEAKER`,
`_DEDUP`, `_PIPELINE`), never by adding an exemption.

The `live` tests are the one exception, and `make live` is the only way to run
them: it sets `BRIEFING_LIVE=1` in the recipe, so the inline env form is not
needed and is refused by the project's rules. When the local Firecrawl's engines
are degraded — its API answers 200 while search returns an empty `data` and
scrape answers 500 — the live tests skip rather than fail, because a degraded
instance and a broken adapter are different findings; see `NOTES-APIS.md` §2.

## Documentation

| File | What it holds |
|---|---|
| `SPEC.md` | The specification; section 4 is the error-classification source of truth |
| `PLAN.md` | Ordered tasks, each with its acceptance check |
| `PROGRESS.md` | What landed and how it was verified |
| `SETUP.md` | How the agent build was started (owner/operator facing) |
| `DECISIONS.md` | Every decision worth arguing about, with its reason |
| `NOTES-APIS.md` | Verified facts about Firecrawl and ddgs, with the source |
| `BLOCKED.md` | Anything that could not be finished, and the exact command for you |
| `DENIED.md` | Commands the build was refused, so you can grant them if you want |
| `docs/AETHER_INTEGRATION.md` | The IPC contract Aether must provide |
| `docs/sample-output.txt` | Real UI renderings, at 100 and 80 columns |

## Status

Phases 0–7 are complete and Phase 8 (the second pass, from the owner's review of
the first build) is finishing: the real end-to-end run has happened and the
defects it found are recorded. See `PLAN.md` for what remains and `PROGRESS.md`
for what has landed and how each piece was verified — including what was left
unfixed on purpose rather than patched with a guess.
