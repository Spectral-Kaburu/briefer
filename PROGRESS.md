# PROGRESS

Build log for the `briefing` CLI. Each entry: task, what landed, how it was verified.

## Session 1 — 2026-10-27 (autonomous run start)

### T0.0 — Session context ✔

Skills actually available this session (from the harness listing), grouped:

**superpowers (the plugin PLAN.md names)** — all present:
`using-superpowers`, `brainstorming`, `writing-plans`, `writing-skills`,
`executing-plans`, `subagent-driven-development`, `dispatching-parallel-agents`,
`test-driven-development`, `systematic-debugging`, `verification-before-completion`,
`requesting-code-review`, `receiving-code-review`, `finishing-a-development-branch`,
`using-git-worktrees`, `diagnosing-superpowers`.

**agent-skills** — present: `using-agent-skills`, `build`, `plan`, `spec`, `review`,
`ship`, `test`, `constraints`, `webperf`.

**Other installed skills**: `graphify`, `idea-to-spec`, `simplicity-first`,
`task-decomposition`, `think-before-coding`, `voice-tts`, `hookify:*`,
`firecrawl:*` (firecrawl, scrape, search, crawl, parse, map, interact, download,
agent, monitor, skill-gen, developer-index, research-index), `commit-commands:*`,
`ponytail:*`, `claude-api`, `code-review`, `simplify`, `fewer-permission-prompts`,
`loop`, `workflow-authoring`, `run`, `dataviz`, `update-config`, `keybindings-help`,
`security-review`, `init`.

**Named by PLAN.md but NOT installed** (will be skipped and logged):
- `skill-creator` → blocks **T7.1** (project skill). Both T7.1 and the
  project-skill part are Phase 7 optional tasks; skip, note, move on.
- `mcp-builder` → blocks **T7.2** (MCP server). Optional. Skip, note, move on.

Note: `using-superpowers` content was already injected at session start (the
skill listing it references is the introduction; the rest are invoked by name via
the Skill tool as tasks need them).

Stubs created this task: `DECISIONS.md`, `DENIED.md`, `NOTES-APIS.md`.

### T0.3 ✔ — API facts (done early, during T0.1)

`NOTES-APIS.md` is populated with verified facts, source URLs included:

- **Firecrawl scrape**: `POST {base}/v2/scrape`; request `url`, `formats`,
  `maxAge`, `zeroDataRetention`, `onlyMainContent`; response
  `success`, `data.markdown`, `data.html`, `data.metadata.statusCode`
  (2xx or `304` = clean load).
- **Firecrawl search**: `POST {base}/v2/search`; request `query`, `limit`,
  `sources`. **Self-hosted / DuckDuckGo-v2.8.0 claim is UNVERIFIED** and is
  flagged as needing a live probe (T2.2).
- **Error codes**: full v2 status list captured (400/401/402/403/404/408/409/
  413/422/429/500/502/503/504) plus all code strings on that page.
  **SPEC's three codes (`SCRAPE_ALL_ENGINES_FAILED`, `SCRAPE_SITE_ERROR`,
  `SCRAPE_DNS_RESOLUTION_ERROR`) are NOT in the current v2 reference** and a
  source probe 404'd — see decision **D2** in `DECISIONS.md`.
- **ddgs** (verified against the installed `ddgs 9.16.1` source, not just the
  README): `text`/`news` take `(query, **kwargs)` returning
  `list[dict[str, Any]]`; forwarded kwargs `region`, `safesearch`, `timelimit`,
  `max_results`, `page`, `backend`. `timelimit` ∈ {`d`,`w`,`m`,`y`}. Exceptions:
  `ddgs.exceptions.DDGSException` / `RatelimitException` / `TimeoutException`.
  `region` is passthrough-unvalidated, so `ke-en` is plausible but unverified.
- Typer + Rich docs and Gemini sections are deliberately still UNVERIFIED; they
  are fetched in T3.2/T3.3 and T4.1 as PLAN.md specifies.

Note: the API-research subagent **failed with a routing error** (not a content
problem), so these facts were gathered by direct WebFetch + source inspection
instead. That is why `NOTES-APIS.md` cites the installed package path.

### T0.1 + T0.2 ✔ — scaffold and Makefile

TDD: wrote `tests/test_cli.py` first, watched it fail with
`ModuleNotFoundError: No module named 'briefing'` (correct RED), then wrote the
minimal `briefing.cli:app`.

Verified, not assumed:
- `.venv/bin/briefing --help` exits **0** and prints the research command.
- `make check` **passes**: `ruff check` clean, `ruff format --check` clean
  (12 files), `pytest` 3 passed.
- Full `install -e ".[dev]"` could not be used as written — see below.

Two problems solved on the way:

1. **The shipped `.venv` was broken.** It was created at `/home/spectre/Documents/hop/`
   then copied here, so every shebang pointed at the old path and
   `.venv/bin/pip` failed with `bad interpreter`. Fixed with
   `python3 -m venv --clear .venv`. *(Note for the owner: `hop` was the original
   project folder name; the venv in the kit is stale.)*
2. **`pip install -e ".[dev]"` fails** because pip's editable build needs `src/`
   to exist at *build* time, and T0.1 creates `src/` only by writing `cli.py`.
   Installed the deps plainly (`pip install pytest ... ruff`) and the package with
   `pip install -e . --no-deps`, which tree-shakes the same result. The T0.1
   acceptance is met either way: deps present, `briefing --help` exits 0.

Ruff flagged two `BLE001` blind-except findings in `.claude/hooks/stop_gate.py`.
Those hooks are the owner's and `CLAUDE.md` forbids editing anything under
`.claude/hooks/`, so the hook was NOT touched — instead `.claude` was added to
`[tool.ruff] exclude`. This is the correct fix: it's harness plumbing, not project
source, and it should not gate `make check`.

Typer finding recorded for T3.3: **Typer 0.27.3 collapses a Typer app that has
only one command.** With just `research` registered, `briefing research "query"`
fails with "Got unexpected extra argument(s)" and the app behaves like a bare
command. Verified that an app with ≥2 commands renders a proper
`COMMAND [ARGS]...` group. So the real CLI (research/run/whitelist/status/config)
will be a correct group — this was purely a skeleton artifact. Do not "fix" it by
adding a dummy command.

Next: **T0.4** `config.py` (pydantic models, YAML load, defaults, env key lookup,
platformdirs paths overridable for tests) + `config/briefing.yaml.example`.
**T0.3** API facts were finished early — see `NOTES-APIS.md`, which is populated,
not a stub.

### T0.4 ✔ — config.py + example config

TDD: `tests/test_config.py` written first, watched it fail with
`ModuleNotFoundError: No module named 'briefing.config'`, then implemented.
21 tests pass, `make check` green.

- Every model sets `extra="forbid"`, so an unknown key is a `ConfigError` naming
  the field — the fix hint points at SPEC §7. A run of tests confirmed
  `news_topics: 5` and `search: {results: not-a-number}` both raise, so the error
  path is proven, not assumed.
- Defaults mirror SPEC §7 exactly, including `ke-en` → left at `us-en` and
  UNVERIFIED in NOTES-APIS.md.
- `resolve_api_key(config, env)` takes the env mapping as an argument and reads
  the key at call time; it returns `None` when absent so load-time never needs a
  key (tests pass with no key in the environment).
- `load_config(state_dir=...)` and `BRIEFING_STATE_DIR` make paths injectable;
  `test_shipped_example_config_loads` guards the shipped example against drift.
- `config/briefing.yaml.example` documents every field inline, verified loadable.

## Phase 1: Fetch core

### T1.1 ✔ — `fetch/errors.py` classifier (SPEC §4 table) · **[review] PASS**

TDD: test first, RED (`No module named 'briefing.fetch.errors'`), then implement.
56 tests; `make check` green. **Reviewer returned PASS** with one suggestion
(plain 503 not covered in the transient row) — fixed by adding 503 to the
parametrised case; 4 transient cases now pass.

Bugs found and fixed during the RED→GREEN loop (worth knowing if you touch
`errors.py` again):

1. **Challenge pages returned DEAD, must be BARRIER** (SPEC row 1). My own comment
   said row 1 while the code said DEAD.
2. **Ordering was wrong in three places.** Rules are now ordered so codes outrank
   statuses (barrier code on a 503 → BARRIER), exceptions outrank statuses, and
   401 from LOCAL is CONFIG while 401 from API is API_SIDE.
3. **Timeouts were split by the wrong axis.** SPEC §4: "LOCAL DOWN | connection
   refused / **local timeout reaching Firecrawl itself**" vs row 1 "408 or
   timeout". A *connect* timeout is LOCAL_DOWN; a *read/pool* timeout is BARRIER.
   The test originally asserted the opposite for `ConnectTimeout`; the SPEC
   decided it, so the test was corrected, not the code.
4. **Body-text rows were entirely missing.** I had encoded only statuses and
   codes, so redirect loops, invalid URLs, and binary content types all fell
   through to the catch-all BARRIER. Added `_DEAD_BODY_MARKERS` /
   `_NOT_SOLVABLE_BODY_MARKERS`, matching SPEC's "invalid URL, redirect loop"
   (row 3) and "unsupported / binary content type" (row 4).
5. **Boilerplate-only detection never fired.** The old word-count heuristic
   treated exactly-8-word nav lists as prose. Replaced with: every line must be a
   boilerplate marker and under 40 chars, with HTML comments/tags stripped first.
   The 40-char bound is what keeps genuinely short articles out of BARRIER.
6. **`exc` must accept instances, not just classes.** A caught exception is an
   instance; normalised with `exc if isinstance(exc, type) else type(exc)`.

Two deliberate leniency decisions, both recorded in `DECISIONS.md`:
SPEC's `SCRAPE_*` codes are matched **by prefix** (see **D2**) so v1 and v2
spellings both classify.

### T1.2 ✔ — `fetch/local.py` local Firecrawl client

TDD, 16 tests, respx-mocked, no network. `scrape()` never raises on a fetch
failure — a transport error or non-2xx becomes a classified `LocalScrapeResult`,
so one bad URL cannot kill a run. Heading falls back to the first markdown `h1`
when metadata carries no title. `health()` is a plain GET on the root.

### T1.3 ✔ — `fetch/api.py` cloud API client

TDD, 13 tests, respx-mocked. `daily_limit` caps calls; a capped call and a
missing-key call are **still logged** (SPEC: "at the cap, fetches degrade to
snippet-only but entries are still recorded") but never reach the network — the
cap test asserts `respx.calls.call_count == 3`. Any API-side refusal (402/429/401)
sets `_disabled`, stopping spend for the rest of the run. `test_no_key_in_log`
asserts the key string is absent from the JSONL log.

### T1.4 ✔ — `whitelist.py` store · **[review] dispatched**

TDD, 39 tests. `normalize_host` strips scheme/`www.`/port/userinfo/case;
`host_matches` implements the whole-domain rule (subdomains match; the
`example.com.evil.net` false positive is covered). Writes are atomic: a temp file
in the same directory + `os.replace` (atomic on one filesystem), so a reader
never sees a partial document. `add_async` serialises through one asyncio lock.
`prune` drops unverified **auto** entries but keeps `manual` ones, since those are
the owner's. Knows nothing about the API key.

**Housekeeping note**: T1.3's files were swept into the T1.4 commit by a
`git add -A` before I had committed T1.3 separately. Both are green and tested;
the history is just mislabelled. If a later task needs "what changed in T1.4",
read `sha::src/briefing/whitelist.py` and ignore `src/briefing/fetch/api.py`.

### T1.5 ✔ — `extract.py` heading + extractive summary

TDD, 27 tests. The summary is **extractive and offline** by design (SPEC §2):
pick the best sentences, never rewrite them, so `--llm` (T4.1) is an upgrade
rather than a requirement. `title` from metadata wins over the first markdown
h1; both can be absent and the summary stands alone.

Three things the tests forced out of the implementation:

1. **A `_MIN_SUBSTANCE_CHARS` filter in `_prose_lines` deleted real sentences.**
   A genuine short line ("Commissioners held a brief ceremony.") was dropped as a
   kicker. Removed the filter from line selection; shortness is now a
   whole-document question answered by `_skip_kickers`, which only reorders when
   there is something longer to spend the budget on.
2. **`Home | World | Business` was not detected as furniture.** The link-based
   test missed it because a nav bar often carries no links at all. Added
   `_is_nav_bar`: ≥3 pipe-separated segments, each ≤12 chars after link
   stripping. The segment count is the signal — prose rarely has five or more.
3. **Sentence splitting left fragments** that could make the summary quote half a
   line, so `_merge_fragments` rejoins a fragment whose predecessor does not end
   on a sentence boundary.

### T1.6 ✔ — `fetch/router.py` the fetch router · **[review] PASS** · **verifier PASS**

TDD, 32 tests, one scenario per row of the SPEC §4 table, each asserting the
whitelist side effect *and* the API call count. `make check` green (209 passed).

The router owns the policy the classifier only implies; every branch is one SPEC
§4 row and invents no row of its own:

| Class | What the router does |
|---|---|
| BARRIER | `whitelist.add_async(host, reason)` **then** API retry |
| TRANSIENT_5XX | one local retry after `backoff_s`; never whitelisted, never API |
| DEAD / NOT_SOLVABLE | snippet only, nothing written |
| CONFIG | snippet only with the classifier's fix hint, no API |
| LOCAL_DOWN | API for this run only — **no** whitelist write, user told |
| API_SIDE | API disabled for the run, snippet only |

Deliberate decisions, all forced by a failing test rather than by taste:

1. **`record_api_call` needed a lock.** The router records API calls from
   concurrent fetches, but only `add_async` was lock-guarded — two concurrent
   `record_api_call`s would each load, increment and save, losing a hit. Added
   `record_api_call_async`, with a test that gathers 12 calls and asserts all 12
   survive. RED first (`AttributeError`), then the three-line method.
2. **At the cap, entries are still recorded.** `test_at_api_cap` caught an
   ordering error in my first draft: I checked `can_use()` *before* whitelisting,
   so a capped fetch dropped the domain entirely. SPEC says at the cap fetches
   "degrade to snippet-only but entries are still recorded", so the entry is
   written and the cap is left for `api.scrape` to report.
3. **A 401 with no content was classified BARRIER, not CONFIG.** The router test
   for SPEC row 5 made an unmocked API call, which is how this surfaced. In
   `errors.py` the empty-content rule ran *before* the local-auth rule, and
   `local.py` passes empty markdown on every failure — so every 401/407 read as
   "the page rendered empty". Fixed by ordering CONFIG ahead of the content
   check, with three regression tests (empty string, no argument, and the two
   statuses). This is exactly the row-ordering trap T1.1 warned about, in a new
   place.
4. **`--no-api` does not whitelist.** No API path means no possible verification,
   so recording the domain would only litter the whitelist with entries `prune`
   would later delete.
5. **`backoff_s` is a constructor argument** because SPEC names no number for the
   transient retry. Tests pass 0 so the suite stays fast; T6.1 makes timeouts and
   retries configurable as the plan requires.

Two implementation notes: `classify` is called once, in `local.py` — the router
reads `result.error_class` rather than re-classifying from a reason string, so
there is exactly one place the table is applied. `error_class` survives a
successful API retry, so the UI can still show "barrier → API".

**The review found a real bug, and it is the same one I had just fixed elsewhere.**
The reviewer FAILed T1.6 because `ApiFirecrawl` mutated `_calls_made` and
`_disabled` with no lock while the router is built to be driven concurrently — so
the daily cap could be exceeded and an API-side refusal could be raced. I had
added `record_api_call_async` under a lock in `whitelist.py` two days earlier and
never asked whether `api.py` had the same exposure. **Lesson worth carrying: when
a concurrency bug is found in one class, grep every other class the same
concurrent caller touches before declaring the family fixed.**

The fix, TDD'd: a RED test that reproduces the race needed a mock which actually
suspends — a plain respx `return_value` resolves without an `await` point, so all
ten coroutines ran to completion in turn and the test passed against the broken
code. With `side_effect` awaiting first, 10 concurrent fetches against
`daily_limit=3` made 10 calls (RED, caught it), now 3. `_reserve` takes a slot
under the lock before the network call and `_refund` returns it when nothing was
billed (transport error, or the API reached the page and got nowhere). An
API_SIDE refusal is deliberately *not* refunded: it disables the client for the
run, so the count no longer gates anything.

Next: **T2.1** `search.py` (normalized `SearchResult`, `ddgs` news/text with
auto-detect, timelimit widening, rate-limit backoff).

### Housekeeping — PLAN.md ticks for T1.1–T1.4 were lost, now restored

The stop gate reported 23 tasks remaining and named **T1.1** as next, but commits
`849dc71` (T1.1) and `1ba91cf` (T1.4) plus two PROGRESS.md entries prove T1.1–T1.4
are finished. The `- [x]` edits for those four were lost somewhere between the
T1.4 review and now — most likely the `git reset` / selective `git add` I used to
split the T1.5 commit from T1.6, which discarded the unstaged PLAN.md edits. The
four boxes are re-ticked. Nothing was re-implemented and no commit content changed;
only the bookkeeping was wrong. Recording it because a future session reading only
PLAN.md would otherwise redo finished work.

### T2.1 ✔ — `search.py` normalized search · **[review] PASS**

TDD, 40 tests, `make check` green (251 passed). `ddgs` is injected as a factory, so
nothing here touches the network.

- `SearchResult(title, url, snippet, source, age)` per SPEC §5. Verified against the
  installed `ddgs` engines rather than assumed: `text()` emits `title`/`url`/`body`
  and **no** date or source, `news()` emits `title`/`url`/`body`/`date`/`source`. So
  `age` is genuinely empty for web hits — recorded in NOTES-APIS.md with file:line.
- `detect_mode` matches news markers at word boundaries, so "newest" is not "news".
- `widen_timelimit` steps one at a time (d→w→m→y) and stops at `y`; `None` stays
  `None` because there is no limit to widen.
- A rate-limited or timed-out search is retried once, wider, with backoff.

Four things the tests caught, all worth knowing before touching this file:

1. **`backoff_s=0` disabled retrying entirely.** My first `_run` broke out of the
   loop when `backoff_s <= 0`, which was how I kept tests fast — so the tests that
   set zero backoff proved nothing about retrying. The sleep is now skipped rather
   than treated as "do not retry"; zero backoff is a legitimate configuration.
2. **`datetime.min` made undated hits sort first.** For a recency-ordered briefing
   that promotes an unknown age above a fresh one, so `_NO_DATE` is `datetime.max`
   and undated hits sort last.
3. **`max_retries` was 2 but meant "1 retry" in the tests.** The tests' own comment
   ("the try plus two retries") was the correct reading, so the default became 1
   and the parameter counts *extra* attempts.
4. **The fake stopped raising after its first failure** because `news_failures`
   defaulted to 1, which silently turned "never succeeds" cases into "fails once
   then returns". Now `news_raises` raises on every call unless a count is given.

One deliberate shortcut: `_run` widens the timelimit by mutating a local `limit`
and passing it into the kwargs, rather than mutating `self.timelimit` — so a failed
wide search does not make the *next* search wide. State is left unmutated
(verified directly, not just asserted).

**Review outcome: PASS**, with two optional findings. I took one and declined the
other:

1. **`parse_age` silently dropped three date shapes** — an ISO stamp with no
   timezone (`2026-10-30T06:00:00`), a trailing zone name
   (`2026-10-30 06:00:00 UTC`), and fractional seconds. Each returned `None`, so a
   real hit vanished from the recency ranking without a trace. Fixed test-first
   (three RED cases, then the fix): normalise a trailing ` UTC` to `+00:00`, strip
   fractional seconds, and canonicalise a space separator to `T` so a space-form
   stamp can still carry an offset. All nine date shapes now verified by direct
   inspection, including the four that already worked.
2. **`detect_mode` false positives on keyword phrases** ("the latest version of
   python" → news). The reviewer noted it but did not call it a bug: SPEC §5 lists
   `latest/breaking/today/news` as the triggers, so matching them is what was
   asked for, and removing the false positives needs NLP rather than keywords.
   **Left as-is deliberately.** Worth recording because the owner may want it
   tightened later — that would be a real change to SPEC §5, not a bug fix.

Next: **T2.2** backup search via local Firecrawl `/search` when `ddgs` fails or
rate-limits (needs a live probe of the local instance to confirm the shape, which
is still down).


