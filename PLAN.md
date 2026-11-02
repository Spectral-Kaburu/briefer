# PLAN.md: Briefing build plan

Conventions: `- [ ]` = open, `- [x]` = done, tasks tagged `(optional)` don't block finishing. `[review]` = run the `reviewer` subagent. Skill names come from the **superpowers** plugin unless prefixed otherwise. If a named skill isn't installed, skip it and note that in `PROGRESS.md`.

**Skills used throughout**: `superpowers:test-driven-development` for every implementation task, `superpowers:systematic-debugging` when anything fails twice, `superpowers:verification-before-completion` before ticking any box, `superpowers:subagent-driven-development` (or `executing-plans`) as the overall execution method, `superpowers:dispatching-parallel-agents` where marked **[parallel]**.

---

## Phase 0: Bootstrap
Skills: `superpowers:using-superpowers`, `superpowers:test-driven-development`

- [x] **T0.0** List the skills actually available this session; write them to `PROGRESS.md`. Create `DECISIONS.md`, `DENIED.md`, `NOTES-APIS.md` stubs.
- [x] **T0.1** Scaffold: `pyproject.toml` (src layout, `briefing` entry point; deps typer, rich, httpx, ddgs, pydantic, pyyaml, platformdirs; dev deps pytest, pytest-asyncio, respx, ruff), `.gitignore`, `README.md` stub, `.venv`. *Accept*: `.venv/bin/pip install -e ".[dev]"` ok and `.venv/bin/briefing --help` exits 0.
- [x] **T0.2** `Makefile` with `check` (ruff check + ruff format --check + pytest), `test`, `fmt`. *Accept*: `make check` passes on the skeleton.
- [x] **T0.3** API facts: use a subagent to read `https://docs.firecrawl.dev/llms.txt`, the v2 scrape/search reference and `api-reference/errors.md`, plus the `ddgs` README. Record verified endpoint paths, request/response fields, error codes, `ddgs` news/text signatures and region codes in `NOTES-APIS.md` with source URLs. *Accept*: file exists and lists every endpoint/field later tasks use.
- [x] **T0.4** `config.py` (pydantic models, YAML load, defaults, env-var key lookup, platformdirs paths overridable for tests) + `config/briefing.yaml.example`. *Accept*: tests for defaults, overrides, missing key.

## Phase 1: Fetch core (T1.1 to T1.4 are independent) **[parallel]**
Skills: `superpowers:dispatching-parallel-agents`, `superpowers:test-driven-development`

- [x] **T1.1** `fetch/errors.py`: classifier implementing the SPEC section 4 table. Input: HTTP status, Firecrawl error code, response body/markdown, exception type, and whether it came from local or API. Output: enum class + reason string. *Accept*: parametrized test with one case per table row, including captcha-page and empty-content detection fixtures. **[review]**
- [x] **T1.2** `fetch/local.py`: async client for local Firecrawl scrape + health check (per `NOTES-APIS.md`). *Accept*: respx tests for success, 403, 429, timeout, connection refused, 501.
- [x] **T1.3** `fetch/api.py`: cloud API client, daily cap, JSONL call log (URL, domain, reason, outcome). Key read from env var at call time only. *Accept*: tests for success, 402, 429, cap reached, missing key (clear error).
- [x] **T1.4** `whitelist.py`: host normalization, suffix matching (`www.` stripped, subdomains match), add/remove/list/prune, verified flag, atomic writes, concurrent-safe. *Accept*: tests incl. concurrent adds and prune of unverified entries. **[review]**
- [x] **T1.5** `extract.py`: heading + extractive summary from markdown/metadata, boilerplate stripping. *Accept*: fixtures (news article, nav-heavy page, empty page, page with no h1) produce sensible 2-sentence summaries.
- [x] **T1.6** `fetch/router.py`: ties T1.1 to T1.4 together exactly per SPEC section 4 (barrier → whitelist + API; dead/not-solvable → snippet only; local-down → API without whitelisting; API-side errors → degrade; `prefer_api_for_whitelisted`; `--no-api`). *Accept*: scenario tests for every row of the classification table, asserting whitelist side effects and API call counts. **[review]**

## Phase 2: Search
Skills: `superpowers:test-driven-development`

- [x] **T2.1** `search.py`: normalized `SearchResult`; `ddgs` news/text with auto-detect (latest/breaking/today/news), timelimit with day→week widening, rate-limit handling with backoff. *Accept*: mocked tests for detection, widening, rate limit.
- [x] **T2.2** Backup search via local Firecrawl `/search` when `ddgs` fails or rate-limits (verify behavior in `NOTES-APIS.md`). *Accept*: mocked fallback test.
- [ ] **T2.3** Live smoke tests `@pytest.mark.live` for ddgs and local Firecrawl (skipped unless `BRIEFING_LIVE=1`). If local Firecrawl is up, run them once and record results in `PROGRESS.md`.

## Phase 3: Research pipeline and CLI
Skills: `superpowers:test-driven-development`; for `ui.py` also fetch Rich/Typer docs (allowed domains) via a subagent and note the relevant API in `NOTES-APIS.md`.

- [x] **T3.1** `pipeline.py`: search → fetch (semaphore-limited concurrency) → extract; emits progress events consumed by the UI; per-URL timeouts; one bad URL never kills the run. *Accept*: mocked end-to-end test with mixed outcomes (local ok, barrier→API, dead, local down). **[review]**
- [x] **T3.2** `ui.py`: Rich live progress (one spinner line per URL), method badges, result cards, footer panel, `--plain` and `NO_COLOR` handling. *Accept*: snapshot tests via `Console(record=True, width=100)` for each badge state and the footer.
- [x] **T3.3** `cli.py`: `briefing -r "<query>"` shorthand, `research` command and all flags from SPEC section 3, `--json`, friendly errors with fix hints. *Accept*: Typer `CliRunner` tests; `briefing --help` and `briefing research --help` read clearly (include examples in help text).
- [x] **T3.4** `whitelist` subcommands, `status` (local health, API key present?, daily usage, ddgs reachable), `config`. *Accept*: CliRunner tests.
- [x] **T3.5** Output polish pass: render sample runs to text (`Console(record=True).export_text()`) for 5 scenarios; read them critically for clarity and alignment, fix issues. Save samples to `docs/sample-output.txt`. **[review]**

## Phase 4: LLM summaries (optional features, default off)
Skills: `superpowers:test-driven-development`

- [x] **T4.1** `synth.py`: Gemini client via `google-genai`, `--llm` per-page summaries and `--synth` overall answer; graceful fallback to extractive on any failure. *Accept*: mocked tests; never touches the network in tests.

## Phase 5: Briefing mode
Skills: `superpowers:test-driven-development`

- [x] **T5.1** `briefing.py` filters: topic blocklist (crypto/finance speculation) and headline-hash dedup store with 7-day TTL (`seen_headlines.json`, atomic writes). *Accept*: unit tests incl. TTL expiry.
- [x] **T5.2** `schedule_id` 24h gate + audio-state gate behind an injectable `AudioState` provider (interface plus a file/stub implementation; document what Aether must supply). *Accept*: tests for fired-today, speaking, open gate.
- [x] **T5.3** `prompts/briefing.txt` + synthesis call (name, session summary, headlines → 2-3 spoken sentences, under ~45s), with plain-greeting fallback if all fetching fails. *Accept*: mocked tests incl. fallback.
- [x] **T5.4** `deliver.py`: `voice-tts -s aether -p high "<text>"` through an injectable runner, `--dry-run` prints the command. `briefing run` command wires everything. *Accept*: tests with a fake runner; never executes `voice-tts`.

## Phase 6: Hardening and docs
Skills: `superpowers:verification-before-completion`, `superpowers:requesting-code-review`

- [x] **T6.1** Error-path sweep: every failure mode from SPEC section 4 produces a clear message; add missing tests. Timeouts and retries are configurable.
- [x] **T6.2** Logging (`--verbose`, log file path in `status`), no secrets in logs. *Accept*: test that keys never appear in logs or `--json` output.
- [x] **T6.3** `README.md`: install (`pipx install -e .`), config, Docker Firecrawl prerequisites, usage examples with sample output, whitelist behavior.
- [x] **T6.4** `docs/AETHER_INTEGRATION.md` plus `systemd/briefing.service` and `briefing.timer` examples (08:00 Africa/Nairobi), describing the IPC contract Aether must provide.
- [ ] **T6.5** Full `make check`; run live smoke if `BRIEFING_LIVE=1` and local Firecrawl is up; final **[review]** of the whole repo against SPEC; fix findings.

## Phase 7: Extras (optional)
- [ ] **T7.1** `(optional)` Project skill `briefing` (via the `skill-creator` skill, if installed) so future Claude sessions know how to use `briefing -r` and read its JSON output.
- [ ] **T7.2** `(optional)` Expose `research` as an MCP server (via `mcp-builder` skill, if installed).
