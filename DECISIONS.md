# DECISIONS

Judgment calls made during the autonomous build, with the reason. Newest last.

---

## D2 — 2026-10-27 · Firecrawl error codes SPEC names that do not exist in v2 (T0.3/T1.1)

SPEC §4 names `SCRAPE_ALL_ENGINES_FAILED`, `SCRAPE_SITE_ERROR`,
`SCRAPE_DNS_RESOLUTION_ERROR`. None appear in the current v2 error reference
(`api-reference/errors.md`), and a probe of the v1 source path 404'd. They are
v1-era strings. **Decision**: the classifier (T1.1) matches them by
prefix/substring (`SCRAPE_` family) rather than exact equality, so both v1 and v2
spellings classify, and each maps to the class SPEC assigns it
(BARRIER / DEAD / DEAD). Record the real strings verbatim in `NOTES-APIS.md` once
the local instance can emit them.

## D3 — 2026-10-27 · `.claude` excluded from ruff rather than editing the hooks

`.claude/hooks/stop_gate.py` has two blind `except Exception` (BLE001).
`CLAUDE.md` forbids editing `.claude/hooks/`. **Decision**: add `.claude` to
`[tool.ruff] exclude`. **Reason**: it is harness plumbing the owner wrote, not
project source; failing `make check` over it would block every task, and editing
it is forbidden. Cost: those two spots stay unlinted, which is acceptable because
they are out of scope for this project.

## D4 — 2026-10-27 · `pip install -e ".[dev]"` replaced by two installs (T0.1)

The T0.1 acceptance names one command, but pip's editable build requires `src/`
to exist at *build* time, and `src/` only appears once `cli.py` is written.
**Decision**: deps via plain `pip install <pkgs>`, then `pip install -e . --no-deps`.
**Reason**: same resulting environment, and the acceptance's real intent (deps
present + `briefing --help` exits 0) is met. The shipped `.venv` was also stale —
created at `~/Documents/hop` and copied here, so every shebang was broken;
`python3 -m venv --clear .venv` fixed it.

## D1 — 2026-10-27 · Missing skills are skipped, not worked around (T0.0)

`skill-creator` and `mcp-builder` are named by PLAN.md but are not installed.
**Decision**: skip those optional tasks rather than hand-rolling a skill or an MCP
server. **Reason**: both are Phase 7 optional, non-blocking, and writing them by
hand would produce an unmaintained artifact plus scope creep. Logged in
`PROGRESS.md` under T0.0.

## D7 — 2026-10-31 · An unreadable audio state is treated as "not speaking" (T5.2)

SPEC §6 gates the briefing on `audio.state == "speaking"`. Aethr writes that state
to a file (`FileAudioState`), so there are four ways the read can fail: no file
(never written), corrupt JSON, a JSON value that is not a mapping, and a provider
that raises. **Decision**: all four mean "not speaking", and the gate opens.
**Reason**: SPEC §6 turns on a state we do not know, not on our own ability to
read it. The failure mode of the opposite choice is worse in both directions — a
gate that never opens is a briefing that silently never speaks, and an
unreachable Aether socket would wedge it permanently with no log line. An open
gate with an unreadable state merely risks talking over the user once, which is
recoverable and visible. The provider stays injectable so the owner can invert
this without touching `Gate`.

## D8 — 2026-10-31 · The pipeline's own timeout maps to TRANSIENT_5XX (T3.1)

SPEC §4 has no row for "the pipeline gave up on a slow URL". The timeout comes from
`pipeline.py`, not from Firecrawl, so it is not really a target 5xx. **Decision**:
report it as `TRANSIENT_5XX`, which the router treats as snippet-only with no
whitelist write and no API retry. **Reason**: that is the least-privileged outcome
on the table and matches what a 503 would have produced; any other class risks
spending an API call on a URL that never answered locally. `warning` carries the
real text ("Timed out after Ns."), so nothing is lost.

## D10 — 2026-11-01 · `-r` lives on the group callback via `invoke_without_command` (T3.3)

SPEC §3's shorthand is `briefing -r "<query>"`. The obvious implementation — a
bare positional argument on a single-command app — stops working the moment a
second command (`run`, `whitelist`, `status`, `config`, all T3.4) is registered,
because Click then reads a bare positional as a *subcommand name*. Typer's
own collapse of a one-command app into a bare command had already hidden this
in T0.1's skeleton. **Decision**: put `-r/--research` on the group callback and
set `invoke_without_command=True` on both the app and the callback.
**Reason**: it keeps working at any command count and costs one flag. Cost: help
shows `-r` as an option on `briefing` rather than as an argument, which is how
Click spells group options and matches SPEC's own usage line.

## D11 — 2026-11-01 · `rich_markup_mode=None`, so help is plain Click (T3.3)

Typer defaults to rich markup help panels. **Decision**: disable them.
**Reason**: Rich wraps help text against a *detected console width*, so the
help output changes between a terminal and a captured stream — which makes the
help-based tests assert on a moving target. Click's own help is fixed-width
stable, and SPEC §3 asks only that help "read clearly" and include examples.
Worth revisiting only if the owner wants panel help.

## D12 — 2026-11-01 · `--news`/`--web` fail together instead of one silently winning (T3.3)

Click treats a paired `--news/--web` flag as one parameter, so passing both
just sets the value twice and exits 0 — the user gets whichever came last with
no warning. **Decision**: two separate booleans plus an explicit check that
fails with a message when both are set. **Reason**: SPEC §3 spells them as
alternatives, and a silently-ignored flag is a bug the user cannot see. The
message names both flags and which to drop.

## D13 — 2026-11-01 · The `--time` window sets `Searcher.timelimit` directly (T3.3)

`SearchConfig` (pydantic, `extra="forbid"`) has no `timelimit` field — SPEC §7's
config sketch omits it, and `Searcher` already carries the "past day, widening
to week" rule as its own default. **Decision**: mutate the constructed
`Searcher.timelimit` rather than adding a config key. **Reason**: the shortest
diff that works with the schema as specified, and it keeps one source of truth
for the default. Cost: config-driven timelimit would need a schema change first.

## D14 — 2026-11-01 · `--llm`/`--synth` parse now, act at T4.1 (T3.3)

SPEC §2 makes both opt-in and T4.1 (optional) implements them. **Decision**:
declare the flags in T3.3 so the surface is complete and `--llm` no longer
errors as unknown, but leave them inert until T4.1 lands. **Reason**: an unknown
flag is a hard error, so declaring them early is strictly better than omitting
them; wiring half a Gemini client would be worse than none. The help text says
"(T4.1)" so the inertness is visible rather than a surprise.

## D9 — 2026-10-31 · Duplicate search hits fetch once (T3.1)

Search can return the same URL twice (two queries, one topic list; or a promotion
that also appears organically). **Decision**: the first, best-ranked hit wins; the
second is dropped. **Reason**: fetching twice wastes an API call at the cap, and
`Report.found` would count one page as two, corrupting the footer's "N found" line.
Rank-shifting was the alternative and is wrong: dropping should renumber the
cards, but rank is the hit's *search* position and the UI cards are numbered from
it — so `rank` is reassigned after `gather` settles, sequentially and in search
order, which is the same numbering with no gap.
