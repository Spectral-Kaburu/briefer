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

Next: **Phase 1** — T1.1 `fetch/errors.py` (classifier for SPEC §4).


