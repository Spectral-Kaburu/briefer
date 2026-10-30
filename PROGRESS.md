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

