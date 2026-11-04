# Audit lenses and mechanical sweeps

Each lens is a set of questions to ask of **every function**, not a topic to skim. Auditors list what they checked per lens.

## Rules for every finding

- **A concrete case or no finding.** State the input or situation and the wrong result it leads to ("an order placed at 00:30 is billed on the previous day"). "Could be a problem" and "consider" are not findings.
- **A `shortcut: <limit>, <when to upgrade>` comment is a decision, not a finding**, unless the stated load or a hostile input (lens L2) already crosses the limit it names. Say which.
- **Before calling code unused or duplicated, grep the whole tree**: source, tests, fixtures, config, docs, and string or dynamic references (`getattr`, entry points, YAML keys, CLI names). Put the grep in the finding.
- **State the assumed load** in the ledger baseline: this is a single-user CLI plus a scheduled run at 08:00, unless the owner says otherwise. Judge scale against that, not against a service with many users.

## Lenses

**L1 Errors and cancellation.** Does anything catch `BaseException`, a bare `except:`, or `except Exception: pass` and hide a real failure? Is `asyncio.CancelledError` swallowed (it must propagate)? Does every contract that says "never raises" really never raise, including on bad JSON, a non-dict item, a missing key, a huge body, a wrong type? Does every network call have a timeout that actually reaches the client? Does `asyncio.gather` use `return_exceptions` where one failure must not kill the rest? Are tasks cancelled **and awaited** at a deadline? Is cleanup in `finally`?

**L2 Secrets and injection.** Can a key, a real name or an email reach a log line, an exception message, a `repr`, argv, a URL, a header, a fixture, `--json`, or a file? Are keys read at call time only? Is `yaml.safe_load` the only YAML loader? Is any command built from a string, or run with `shell=True`, `os.system` or a shell-interpolated `voice-tts` text (argv list only)? Can untrusted text (a page, a snippet, an API response) escape its prompt delimiter (case, whitespace, unicode look-alikes, the closing delimiter split across chunks), inject Rich markup (must be `Text`, never markup), or put control characters and ANSI escapes on the terminal or into a log (log injection through newlines)? Can a search-result URL point at `localhost`, a private address, `file:` or a userinfo `@host` trick, and does fetching it go through a scheme and host check? Can a path from config or a URL escape the state directory?

**L3 Concurrency and async.** Blocking calls inside `async` code (`time.sleep`, sync `httpx`, sync `ddgs` without `to_thread`)? Shared mutable state across tasks? Semaphores or locks leaked on an error path? Races on the whitelist, dedup store, API call log or schedule state (atomic write, lock, read-modify-write window)? Does a deadline really cancel in-flight work?

**L4 Data, time, shapes.** Naive datetimes; the 24-hour gate and TTL boundaries (off by one, equal, DST-free Africa/Nairobi but UTC state); the clock injected everywhere it is read; response shapes trusted without validation; `None` and empty everywhere; unicode and encoding; unbounded body size; integer and float units (seconds vs milliseconds).

**L5 Resources.** Unclosed `httpx` clients, file handles and temp files; unbounded memory (page size, passages), unbounded log or JSONL growth; the state directory growing forever.

**L6 Logic and dead code.** Off-by-one, wrong default, inverted condition, unreachable branch, a flag parsed and never used, a config key defined and never read (or read and never defined), a constant duplicated in two places that can drift, `normalize_host` edge cases (port, trailing dot, uppercase, IDN, userinfo, IPv6 literal), a regex that backtracks catastrophically on long input, sort stability and tie-breaking.

**L7 CLI and UX.** Exit codes; error messages with a fix hint; stdout holds only the result and stderr holds the noise (`--json` must be parseable); `--plain`, `NO_COLOR` and piping; `--help` text and examples true to behaviour; the `-r` and `-a` group-shorthand boundary; unreadable config fails cleanly and names the path.

**L8 Tests that cannot fail.** For each test, name the one change to the code that would turn it red. No such change means a finding. Look for: assertions only on a mock the test itself configured; the unit under test patched away; no assertion at all; `assert x is not None` where the value matters; a test that passes with candidate order reversed (the project has been burned by this); a sabotage-resistant docstring ("verified that...") with no assertion behind it; order- or time-dependent tests; leftover `skip` or `xfail`; fixtures that contain third-party text; any exemption in `tests/conftest.py`; missing negative cases for every guard (gate, cap, floor, deadline, dedup, delimiter).

**L9 Claims against code.** A ticked task whose feature is absent or only partly there (the project once ticked a log line that was never written); a `DECISIONS.md` entry the code contradicts; README or sample output out of date against `--help` and `briefing config`; dead links; counts that no longer match.

**L10 Packaging and ops.** Imports not declared in `pyproject.toml` and declared deps never imported; Python-version syntax; the entry point; Makefile targets that do not run; `.gitignore` covering `.smoke-state`, the state dir and anything that could hold a key; systemd unit directives and the 08:00 Africa/Nairobi timer.

**L12 Lean.** Code that should not exist or should be smaller. *Delete*: dead code, unused options, flags and config keys, speculative features. *Reuse*: two helpers doing one job (keep one, name the path). *Stdlib or native*: the standard library or an installed dependency already does it, or a dependency does what a few lines would. *YAGNI*: an interface with one implementation, a factory with one product, a wrapper that only passes calls through. *Merge*: near-copies that must change together. *Split*: one function doing several unrelated jobs, by job and never by line count. Lean findings are **P3** by default; proven-dead code (the whole-tree grep is in the finding) may be P2. Count the lines and dependencies a finding would remove for the owner report.

**L11 Knowledge adapters and ask mode.** Every adapter honours the shared contract (never raises, timeout, generic User-Agent, HTML and control characters stripped, license attached); the quota floor and `backoff` really stop calls; host de-duplication; knowledge passages numbered first; delimiters and prompt text unchanged by the adapters; `-r` and briefing output byte-identical to before.

## Mechanical sweeps

Run each, read each hit in context, record hit or empty. Adjust paths to the repo layout. When a hit is fine, write it under "Checked fine" with the reason.

```
rg -n "except BaseException|^\s*except:\s*$|except Exception:\s*(pass|\.\.\.)?\s*$" src
rg -n "CancelledError" src
rg -n "shell=True|os\.system|subprocess\.|create_subprocess" src
rg -n "yaml\.load\(" src
rg -n "(api[_-]?key|token|secret|password)\s*[:=]\s*[\"'][^\"']{8,}" src tests config docs
rg -n "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}" src tests config docs scripts systemd
rg -n "time\.sleep|requests\.|urllib\.request|\.get\(.*\)\.json\(\)" src
rg -n "datetime\.(now|utcnow)\(" src
rg -n "asyncio\.gather\(" src
rg -n "create_task\(|ensure_future\(" src
rg -n "AsyncClient\(|httpx\.(get|post|request)\(" src
rg -n "timeout" src
rg -n "TODO|FIXME|XXX|HACK|NotImplemented" src
rg -n "pytest\.mark\.(skip|xfail)|pytest\.skip\(" tests
rg -n "noqa|type: ignore|pragma: no cover" src tests
rg -n "assert True|assert .* is not None\s*$|assert .*\bmock\b.*called" tests
rg -n "\bprint\(" src
rg -n "\.format\(|f\"[^\"]*\{[^}]*(page|text|content|snippet|passage)" src
rg -n "console\.print\(|Panel\(|Text\.from_markup|markup=True" src
rg -n "rstrip\(|lstrip\(|strip\(\"/" src
```

Config drift (do it by hand, field by field): for every field in `config.py`, `rg -n "<field>" src` outside `config.py` must show a reader; every key in `config/briefing.yaml.example` must be a field; every default in the README block must equal the code default.

Import drift: compare `[project] dependencies` in `pyproject.toml` with `rg -n "^(from|import) " src` third-party modules.

Docs drift: run `briefing --help`, `briefing research --help`, `briefing ask --help`, `briefing config` (state dir pointed at `.smoke-state`) and compare with the README tables.
