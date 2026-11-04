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

## D15 — 2026-11-01 · Config errors are caught once, in `_load()` (T3.4)

`_research` caught `ConfigError` and reported it cleanly; `config`, `status` and
the whitelist subcommands did not, so `briefing config --config bad.yaml` exited 1
with an empty stderr. **Decision**: the `try/except ConfigError → _fail()` guard
lives in `_load()`, the one function every command calls to read config.
**Reason**: SPEC §3 promises every user-facing error a plain-English message plus
a fix hint, and four copies of the same guard is four places to forget it. The
alternative — repeating the try/except in each command — is what produced the
gap. `_research`'s local copy was deleted as dead code, and a test asserts
`research` still reports cleanly *through the shared path*, so the guard cannot
quietly fork again. Related: [[no-default-config-file]] — a `briefing.yaml` in the
state dir is inert, because `load_config(None)` reads no file.

## D16 — 2026-11-01 · No auto-discovered config file (observed, not decided; T3.4)

While fixing D15 I wrote a test asserting `status` fails when a malformed
`briefing.yaml` sits in the state dir. It failed for an unexpected reason: nothing
reads that file. `load_config(path)` loads only the path it is given;
`load_config(None)` returns pure defaults. SPEC §3 names no default config
location, and T0.4 built paths (state dir, whitelist, log) but no config path.
**Decision**: leave it explicit-only — this is T0.4's design and SPEC's, and
silently picking up a file the user never named would be a new feature, not a
fix. **Open question for T6.x**, not a defect: should `briefing config` report
"no config file in use" so the owner knows defaults are in play? Logged rather
than built, because nothing in SPEC asks for it.

**CLOSED by T8.3 (2026-11-04)** — the explicit-only decision is reversed. The
README told the owner to copy `config/briefing.yaml.example` into place, and
that file did nothing unless the path was typed on every command: a documented
file that is inert is worse than no documentation. Discovery now runs, in
order: `--config PATH`, then `$BRIEFING_CONFIG`, then
`<platformdirs user_config_dir>/briefing/config.yaml`, then built-in defaults.

The asymmetry with D15's `load_config(path)` guard is deliberate and is the one
thing worth keeping: a path the owner *typed* that does not exist is a
`ConfigError`, because a typo must never silently run on different settings than
intended, while a *discovered* file that is absent is silent, because a fresh
install must not error. `load_config(None)` still reads nothing from disk, so
the observation above stands and the existing tests stay honest — discovery is a
separate pure function, `discover_config(env)`, called by the CLI's `_load()`.
`briefing config` answers the open question directly, naming the file in force or
"none (built-in defaults)" plus how to create one.

## D17 — 2026-11-01 · `Speaker` depends on `generate`, not on the `Gemini` class (T5.3)

The synthesis needs one async method, and `Gemini` (the SDK wrapper) is the only
implementation. **Decision**: `Speaker.__init__(gemini: t.Any)` — duck-typed on
`async generate(prompt) -> str | None`. **Reason**: this is a bug fix. My first
draft wrapped an injected SDK-shaped client, so the tests passed a fake with
`generate` and every call silently fell through to the greeting on
`AttributeError: 'Fake' object has no attribute 'aio'` — caught only because the
`--dry-run`-free debug print surfaced what the broad `except` was swallowing.
Typing the parameter as `Gemini` would have made that a *type-visible* lie, since
tests pass fakes. The dependency direction is now documented on the class.

Two follow-ons, both laziness rather than design: no sentence-count enforcement
in code (SPEC §6's "under ~45s" is a prompt constraint; truncating on `.` would
mangle "Dr." and "3.5" in a live briefing — a cap belongs to `max_output_tokens`
and the model), and `prompts/briefing.txt` is read per call rather than imported.

## D18 — 2026-11-01 · `briefing run`'s collaborators are configured, not constructed (T5.4)

`_briefing` needs a searcher, a store, an audio-state reader and a speaker, each
built from `Config`. **Decision**: each is a small factory over the loaded config
(`_search`, `_dedup`, `_audio`, `_speaker`), published as the seams `_SEARCH`,
`_DEDUP`, `_AUDIO`, `_SPEAKER` beside the existing `_PIPELINE`/`_RUNNER`.
**Reason**: the alternative — constructing `DedupStore`, `Gemini`, `Searcher` and
`FileAudioState` inline — leaves a test able to swap only the runner, so the
refusal tests depend on the gate to keep the *other* real collaborators
unreachable. That is exactly the trap that produced the incident in PROGRESS.md:
a gate regression fell through to a real `voice-tts` and a real search. A factory
per collaborator means every test names the whole graph, and a regression fails a
test named for it. `Speaker` is still built from `Gemini()` inside the factory,
so tests that want the real thing can ask for it and tests that do not, cannot.

One consequence worth knowing: `if not dry_run:` in `_briefing` guards only the
dry-run case. A *failed* delivery never reaches that line — `deliver` raises and
`_fail` exits first. The two behaviours (dry run records nothing, failed delivery
records nothing) are correct and tested, but they are enforced by different code,
which is why the dry-run test asserts on the store file directly.

## D19 — 2026-11-02 · redaction is one filter on every sink, not a promise (T6.2)

T6.2 needed `--verbose`, a log path in `status`, and "no secrets in logs". **Decision**:
`src/briefing/log.py` owns one `configure()` and one `SecretFilter`, and the CLI
resolves the API key at call time and hands it to the filter rather than the filter
reading the environment.

**Reason**: the alternative — "we never log the key" — is a promise the next log
line breaks silently. The filter is blind (it masks the value it was given, nothing
else), so it costs nothing until something does leak, and then it fails safe. Two
details are load-bearing and easy to lose: the filter is attached to *handlers*, not
the logger, so records from child module loggers are masked too; and `exc_info` is
rendered into a masked `exc_text` and cleared, because a traceback renders separately
from `msg`/`args` and would otherwise print the key in full.

`--verbose` lowers **both** sinks to DEBUG rather than only adding stderr: an
unattended `briefing run --verbose` has no stderr left to read afterwards, so a
debug record that only reaches the terminal is useless to the systemd case T6.4
builds. To make the flag worth its name, `Router.fetch` is now a thin wrapper that
logs one decision line per URL over `_route()` — one seam covers every branch of
the SPEC §4 table, where a line in each of the three result constructors would not.

A review raised two further defects here, one real and one not. The real one: closing
every replaced handler closed a `FileHandler`'s descriptor *and* would take stderr
with it, so `configure` now closes only what it owns (`FileHandler`) and detaches the
rest — `StreamHandler` was given `sys.stderr`, not sold it. The false positive was
the same concern argued from `StreamHandler.close()` closing its stream; a test that
reconfigures verbose→quiet and then writes to stderr passes either way, which is how
it was settled rather than by reading CPython. Kept anyway, because it is the one
assertion that would catch someone "fixing" the handler loop to close everything.
The log is unrotated: `shortcut:` comment in `log.py` names the volume and the
upgrade path.

## D20 — 2026-11-02 · the README does not point at files that do not exist yet (T6.3)

A first cut of the README referenced `docs/AETHER_INTEGRATION.md` and `systemd/`
as though they were there. Both are **T6.4**, unchecked at the time of writing, so
the README shipped two dead references on its most important surfaces. **Decision**:
those two references are stated as pending, with the T6.4 task named, and a note
that nothing in briefing mode depends on them — run `briefing run` from cron today
and the Aether contract only matters when Aether shows up.

**Reason**: a README that promises documents it does not contain teaches the
reader not to trust the rest of it, and the fix is free — say "not yet" instead of
linking nothing. The alternative was to write T6.4 first, but T6.3's content was
already accurate and reordering the plan to satisfy a doc's link is the tail
wagging the dog.

Also recorded here: PLAN.md T6.3 names `pipx install -e .` verbatim, so the README
keeps it, but `pipx --help` and the pipx docs were both denied in this sandbox,
which means that flag is asserted rather than verified (see DENIED.md). The venv
`pip install -e .` line sits beside it as the fallback that needs no faith, and the
README says so.

## D21 — 2026-11-02 · `--llm` summarises where the markdown is, not in the CLI (T4.1)

**Decision**: the abstractive summary is produced inside `Pipeline._summarize`,
which gained an optional `summarizer` and became `async`, rather than as a
post-pass in the CLI over the finished `Report`.

**Reason**: SPEC section 2 says the abstractive summary is written from "heading +
extracted text", but `Result` carries only the finished two-sentence summary. A
CLI post-pass would therefore be re-summarising the summary — an "abstractive"
feature that reads a two-sentence extract and rewrites it, which is nearly a
no-op that still costs a model call per page. The pipeline is the one place that
still holds `fetched.markdown`, so the seam goes there and the model sees the
page. The cost is one more optional constructor argument and an `await` in `_one`;
the benefit is a feature that does what the flag says.

The same applies to `--synth`: it must run *after* the run, because it answers
from every page, so it is a second `asyncio.run` in `_research` rather than a
nested loop. Its sources are `(title, summary)` per page the run got content for
— a failed fetch has no summary to cite, and a source the answer invents from a
snippet is worse than no source.

Also decided: the two research prompts are inline constants in `synth.py`, not
files. `prompts/briefing.txt` is a file because the owner is expected to reword a
spoken greeting; nothing in SPEC section 2 asks for the research prompts to be
reworded, and two more writable files in the repo is state nobody asked for. If
that changes, move both to `prompts/` and read them per call the way `load_prompt`
does.

Finally, `_generate` builds the prompt *inside* its `try`. Refactoring `Speaker`
to share it initially moved `self.prompt(headlines)` outside the guard, and
`tests/test_synth.py::test_a_missing_prompt_file_falls_back_without_calling_the_client`
caught that the greeting then became a `FileNotFoundError`. Building it inside is
what keeps a lost prompt file degrading to the greeting, as SPEC section 6
requires.

## D22 — 2026-11-02 · the backup search adapter raises; its caller guards (T2.2a)

`LocalFirecrawl`'s existing contract is "no method raises on a fetch failure,
so one bad URL can never kill a run" — and `search` breaks it.

**Decision**: `search()` propagates a transport error; it does not return an
empty list, a sentinel, or a classified result object.

**Reason**: the broad `try/except` already exists in `Searcher._backup_search`
(search.py), is documented as "a failed backup is still not fatal", and is
covered by a test that injects a raising backup. Swallowing the error inside
`search()` would make it a *second*, independent copy of that policy — the exact
duplication D15 removed from the config-error path, where four copies of a guard
produced a gap. One guard, in the caller that owns the failure mode.

The consequence is a real asymmetry worth knowing: a scrape failure never
reaches the router as an exception, because a scrape failure is a *classify*
input (SPEC §4's whole table), while a search failure is a *fallback* outcome
(SPEC §5) with nothing to classify it as. Different callers want different
things from the same client, and the client cannot serve both from one shape.

Cost: `_backup_search`'s broad `except` is load-bearing rather than lazy, so it
must not be tightened without giving `search` a real failure channel. Recorded
there and in `search.py`.

Related: the adapter returns *raw item dicts* rather than `SearchResult`s, so
`from_search` stays the single place the `/v2/search` field mapping lives —
otherwise the mapping would exist twice and drift, which is the failure mode D21
moved the summarizer to avoid.

## D23 — 2026-11-03 · the guard installs unless `_is_live` says otherwise, and its own escape hatch is tested (T8.1)

The first cut of `tests/conftest.py` installed the guard for **every** test while
its docstring promised live-marked tests were exempt. The 4 `test_live.py` tests
then failed under `make live` with `RealSideEffectBlocked`.

**Decision**: the fixture consults `_is_live(request)` before installing, and
`test_the_guard_really_lets_the_live_marker_through` asserts the fixture's own
source contains that call.

**Reason**: an escape hatch nobody exercises is an escape hatch nobody tests. The
unit test for `_is_live` alone passes against a fixture that never calls it — so
that test was exactly the "fake that returns the shape the consumer wants" trap
from T2.2, one layer up. The source assertion is unusual, and it is warranted
here: the bug class is a guard drifting away from its own documentation, and no
behavioural test can catch it because the failure only appears in the one command
(`make live`) CI never runs.

**Cost / why not something else**: a behavioural test would need a real live test
to assert against, which means a real network call in the ordinary suite — what
the guard exists to prevent. An alternative was to invert the fixture so it
installs only for guarded tests and mark `test_live.py` explicitly; that keeps
the escape hatch implicit and loses the assertion above.

**Amended after review (2026-11-03): layer 2 was defeatable and is now not.**
`blocked` is an importable module global, so a test that caught the block could
`blocked.clear()` on its way past and teardown saw an empty list — proven with a
throwaway test that connected a real socket and passed. The record is now backed
by `_count` (`conftest.py`), which only `_block` increments and no test can reset,
so `_blocked_now()` is true whether or not the list survives. A test that blocks
on purpose (`test_isolation.py`) clears both through `_reset_blocks()`.

Related and worth recording: **`socket.socketpair` is deliberately not blocked.**
Blocking it broke every async test, because the Unix event loop calls it in
`_close_self_pipe_socket` on close and a socketpair is a loop-local pipe that
never reaches the network. The lesson is that "reach the network" and "use a
socket" are not the same predicate, and a guard that patches the broader one
fails the loop's own bookkeeping.

## D24 — 2026-11-03 · `test_run.py` patches `_SPEAKER` in one autouse fixture, not at each `_STATE_DIR` site (T8.1)

`_speaker` builds a real `Gemini()`, and the google-genai SDK resolves a host when
it constructs its client, so an unpatched seam reached the network from 7 tests in
that file.

**Decision**: one autouse `_fake_speaker` fixture patches `_SPEAKER` for the whole
file, instead of adding a line beside each of the 15 `_STATE_DIR` patches.

**Reason**: the per-site version was tried first and it broke 5 tests that build
their own `FakeSpeaker` and assert on `speaker.calls` — the added line came *after*
theirs and silently overrode it. Two of those five assert `speaker.calls == []`,
which made the clobbered tests pass while asserting nothing at all. An autouse
fixture runs before the test body, so a test's own `monkeypatch.setattr` still
wins and both kinds of test keep their meaning; and the ordering guarantee is
visible rather than incidental.

**Cost**: every test in the file now gets a fake speaker whether it wanted one or
not, so a future test that *should* assert the speaker was never built has to
overwrite the fixture to restore the real thing — and there is currently no test
that does, which is worth knowing before relying on it.

## D25 — 2026-11-03 · the backup adapter never raises; D22's asymmetry is resolved (T8.2)

T8.0(a) flagged the conflict: D22 made `LocalFirecrawl.search()` **raise** and put
the guard in `Searcher._backup_search`, while PLAN.md T8.2 asks for a
`FirecrawlSearch` that **never raises**. The owner's task text is the later
instruction, and it is the one written knowing D22 existed.

**Decision**: `FirecrawlSearch.search` swallows every failure — connect error,
timeout, non-2xx, malformed JSON and any shape this code has not seen — and
returns `[]`, logging at DEBUG. `_backup_search`'s broad `except` stays, but it
is no longer load-bearing: it now only catches a mis-wired seam, not a dead
instance.

**Reason**: D22's worry was *duplication* — two copies of "a failed backup is
not fatal". It is resolved, not ignored, because the two clients have different
callers and T8.2 gives them different homes: `LocalFirecrawl.scrape` feeds
SPEC §4's classify table, so its failures must survive as classifications;
`FirecrawlSearch` is the last event of a search that already failed, so its
failures have nothing left to degrade to and nothing to classify. Putting both
under one `except` is what D22 was arguing against; splitting the *classes*
rather than the *policy* keeps one policy per failure mode.

**Sub-decision (the one T8.2 names explicitly):** an *empty* ddgs answer — not a
failed one — also triggers the backup, logged at INFO ("ddgs returned nothing,
trying backup"). A rate-limited or timed-out search and a search that genuinely
found nothing are indistinguishable to a user waiting for a briefing, and the
backup costs one local HTTP call.

**Correction, 2026-11-04 (found by T8.8):** the decision above was recorded when
T8.2's box was ticked, but the INFO line was never written — `search.py` had only
the DEBUG failure log, so an empty ddgs answer fell through silently and this
decision described behaviour that did not exist. The log now exists
(`search.py`, guarded by `self.backup is not None` so it never claims an attempt
that is not in play), and two tests in `tests/test_search.py` pin it: one that
the empty answer really reaches the backup and logs the trigger at INFO, one that
`backup=None` logs nothing about trying. The level, not just the wording, is
pinned — downgrading the line to DEBUG fails the first test.

Cost, so it is not rediscovered: `FirecrawlSearch` returning `[]` means a
*degraded instance* and a *broken adapter* look identical to any caller. The live
test in `test_live.py` therefore probes the raw endpoint before failing, and
skips when the service is degraded. `NOTES-APIS.md` §2 records the observed
symptom: the API answers 200 with an empty `data` while its engines are down.

Related: D22, D21, and the T8.0(a) note that T2.2a's shape was a first cut.

## D26 — 2026-11-04 · `user_name` and `name` are the two directions, and only one has a default (T8.5)

The owner's first real run printed "Good morning, Aether." Aether is the
assistant. `briefing.name` filled two different slots — said the assistant's name
into the greeting *and* into the prompt's "You are {name}" — and neither SPEC §6
nor the code distinguished them, so the default greeted the speaker.

**Decision**: `user_name` (who is *addressed*, default `""`) and `name` (who
*speaks*, still "Aether") are separate config fields.

**Reason**: SPEC §6 only ever describes the greeting as "plain session greeting".
It says nothing about a name, which means a name must be opt-in: the failure
fallback has to be safe with no config at all, and an empty `user_name` gives
"Good morning." — not "Good morning, .", which is what the old `.format(name="")`
produced and what a test actively pinned. Keeping `name` preserves SPEC §6's "You
are {name}" prompt line without inventing a second persona.

**Cost**: two fields where there was one, and the template carries one more slot
({address}) whose non-empty form says "The person you are speaking to is called
X — refer to them by name." It is empty unless a name is set, because a model told
to address somebody *by name* and given none invents one — that is why the
instruction is conditional and not just the value. Wiring is not covered by
`synth` alone: `_speaker` must pass both, and that test patches `Speaker` rather
than `_SPEAKER`, because patching the factory replaces the code under test — the
first version of the wiring test did exactly that and passed under a sabotage that
swapped the two names back. A factory needs its *product* faked.

## D27 — 2026-11-04 · `search.region` stays `us-en`, because the probe found no region better (T8.4)

SPEC §6 ships `region: ke-en` with its own "verify region code accepted by ddgs"
comment, and §12 asks it as an open question. T8.4 answers it with a probe
(`scripts/probe_region.py`, `make probe-region`) rather than a guess: news mode,
`"latest breaking news in kenya"`, `max_results=10`, one run per region.

**Decision**: the default stays **`us-en`**, the value already shipped at
`config.py:38` and the one ddgs itself defaults to (`ddgs.py:141`).

**Reason**: three candidates, zero measured difference. `ke-en`, `wt-wt` and
`us-en` each returned the *same five US hosts* — `21alivenews.com`,
`broadwayworld.com`, `missoulian.com`, `sports.yahoo.com`, `yahoo.com` — and
**not one `.ke` host, in any region, in any round**. The `DDGSException("No
results found.")` failures were random per request, not per region (once
`ke-en`+`wt-wt` failed while `us-en` returned 10; once the reverse). So there is
no evidence that would justify changing a value to another value. Changing it to
`ke-en` because the briefing is Kenyan would be a change with no measured effect
— and it would name a code that the probe just showed does not deliver Kenyan
results. `us-en` has one further tie-breaker: it is the library's own default, so
the config file is not silently overriding ddgs with a value nothing backs up.

**What this does NOT claim**: `us-en` is not *verified good*, it is *no worse
than the alternatives* on this query. The ddgs `region` reaches DDG as the `l`
market hint verbatim (`engines/duckduckgo_news.py:48`) and is not validated
(`NOTES-APIS` §4), so no region code can be proven correct from ddgs's source
alone — the only evidence is what comes back.

**Cost, recorded rather than hidden**: a Kenya query still returns US
syndication of Kenyan stories. That is a real weakness of the default, and the
honest fix is not `region` (which the probe shows is inert for this query) but
the query wording or a host-side filter — a change of behaviour, and out of
T8.4's scope. Plan it as its own task rather than folding it into a config
default, and until then `ke-en` etc. remain one-line owner-editable changes
because the field is a passthrough.

**Owner step (SPEC §12's open question, answered):** none required. The default
is unchanged from what already shipped, and the example config's comment now
says why changing `region` may not help.

## D7 — 2026-11-04 · Heading from first h1 can be unrelated to page content (T9.0 diagnosis)

Diagnostic probe: `curl -s -X POST http://localhost:3002/v2/scrape` for
`https://versionlog.com/python/3.11/` was blocked by sandbox permissions.
Recorded in `BLOCKED.md`. Reasoned from code instead.

**Observation:** `extract.py:112` selects heading as: `title or _first_h1(markdown)`.
When no title is provided, it takes the *first* `h1` in the markdown. This can be
anything the page puts there — navigation titles, changelogs, sidebar headings,
or actual page titles.

**Root cause:** The pipeline (search result title as `title`) should provide a
relevant heading, but when `title` is `None` or empty, the fallback to first h1
is a blunt instrument that makes no quality assessment.

**Diagnosed pattern from T8.7:** pages where `data.metadata.title` is unrelated to
the user's query (e.g., a changelog line like "Fixed: type checker now handles
recursive generics" shown as the title of a "Python 3.11 release announcement" page).

**Decision:** Implemented option 1 with a tie-breaker priority. Added `_select_heading()` function that:
1. Scores each heading candidate by word overlap with URL path and search title
2. Minimum overlap threshold (score >= 1) for a candidate to be considered "relevant"
3. Priority tie-breaker: metadata_title > first_h1 > search_title
4. Debug logging via logger.debug() shows selection details

**Implementation:** Modified `extract()` to accept `url` and `search_title` parameters, updated `pipeline._summarize()` and `cli.Extractor` to pass them through, added `_extract_url_path()` and `_heading_overlap_score()` helper functions.