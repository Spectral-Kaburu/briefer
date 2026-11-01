"""The `briefing` command-line interface (SPEC sections 3 and 7).

`research`, `whitelist`, `status` and `config` exist; `run` arrives with T5.4.
Everything below the argument parsing is the dependency wiring, which lives in
one place so a test can swap the pipeline (or the collaborators) by name rather
than reaching into a running event loop.

Help text uses plain Click (`rich_markup_mode=None`) rather than Rich's panel
help: Rich wraps against a detected console width, which makes help snapshots
depend on the terminal. Plain Click's help is stable everywhere.
"""

from __future__ import annotations

import asyncio
import json
import os
import typing as t
from pathlib import Path

import typer
from rich.console import Console

from briefing.config import Config, ConfigError, load_config, resolve_api_key
from briefing.extract import Extracted, extract
from briefing.fetch.api import ApiFirecrawl, usage_today
from briefing.fetch.local import LocalFirecrawl
from briefing.fetch.router import Router
from briefing.pipeline import Pipeline, Report
from briefing.search import Searcher
from briefing.ui import UI
from briefing.whitelist import Whitelist, WhitelistEntry, normalize_host

# The seam the CLI tests use: patching this one name swaps the whole run's
# collaborator graph. Real code uses the real `Pipeline`.
_PIPELINE = Pipeline


def _STATE_DIR() -> Path:
    """Where the whitelist, dedup store and API call log live.

    Indirects `config._default_state_dir` so a test can point the whole CLI at a
    tmp dir with one `monkeypatch.setattr`, instead of setting `BRIEFING_STATE_DIR`
    and hoping no other test races it.
    """
    from briefing.config import _default_state_dir

    return _default_state_dir()


app = typer.Typer(
    help="Search the web, extract pages, and summarize the results.",
    no_args_is_help=True,
    add_completion=False,
    # `invoke_without_command` lets the callback run with no subcommand, which is
    # what `briefing -r "<query>"` needs: SPEC section 3 makes the shorthand
    # dispatch straight into research. Only the callback can see it, because with
    # two commands registered a bare positional would be read as a subcommand
    # name (a single-command Typer app collapses its command away, which is how
    # T0.1's skeleton accepted one).
    invoke_without_command=True,
    rich_markup_mode=None,
)

# `-n` is SPEC section 3's short flag for `--results`; `-r` is reserved for the
# `research` shorthand on the group, and Typer rejects a duplicate short option.


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    research: str | None = typer.Option(
        None, "-r", "--research", metavar="QUERY", help="Shorthand for `briefing research`."
    ),
) -> None:
    """Research the web, or manage a briefing run."""
    if research is not None:
        # No options: SPEC section 3's shorthand takes a query and nothing else.
        # Extra flags belong to `briefing research`, which is the same operation.
        _research(research)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        # `no_args_is_help` handles a bare `briefing`; reaching here means the
        # callback ran with neither, which should not normally happen.
        raise typer.Exit(1)


@app.command()
def research(
    query: str = typer.Argument(..., metavar="QUERY", help="Question or topic to research."),
    results: int = typer.Option(None, "-n", "--results", help="How many results to fetch."),
    time: str = typer.Option(None, "--time", help="Recency window: day, week, month, any."),
    news: bool = typer.Option(False, help="Force news search instead of auto-detect."),
    web: bool = typer.Option(False, help="Force web search instead of auto-detect."),
    llm: bool = typer.Option(False, "--llm", help="Abstractive summaries via Gemini (T4.1)."),
    synth: bool = typer.Option(False, "--synth", help="One overall answer from all summaries."),
    no_api: bool = typer.Option(False, "--no-api", help="Never call the Firecrawl API."),
    json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    plain: bool | None = typer.Option(None, "--plain/--no-plain", help="Plain text, no color."),
    config: str = typer.Option(None, "--config", help="Path to a briefing.yaml to load."),
) -> None:
    """Search the web for QUERY, extract each page, and summarize what was found.

    DuckDuckGo finds roughly 10 results; every page is then fetched through local
    Firecrawl, falling back to the API only where SPEC section 4 allows it. News
    words in the query (latest, breaking, today) switch to news search; --news
    and --web force it either way.

    Examples:

        briefing research "latest breaking news in kenya"

        briefing research "kenya economy" --results 20 --time week

        briefing research "climate adaptation" --no-api --json
    """
    _research(
        query,
        results=results,
        time=time,
        news=news,
        web=web,
        no_api=no_api,
        json_out=json_out,
        plain=plain,
        config=config,
    )


# --- whitelist (T3.4) ------------------------------------------------------

whitelist_app = typer.Typer(
    help="Manage the domains the cloud API may fetch.", no_args_is_help=True
)


def _store() -> Whitelist:
    """The whitelist the config names, for the subcommands below."""
    return _whitelist(_load(None))


def _whitelist_line(entry: WhitelistEntry) -> str:
    """One entry as a row of `whitelist list`, with its state spelled out."""
    state = "verified" if entry.verified else "unverified"
    reason = f" · {entry.reason}" if entry.reason else ""
    return f"{entry.domain}  [{state} · {entry.source} · {entry.api_hits} api hits]{reason}"


@whitelist_app.command(name="list")
def whitelist_list() -> None:
    """List whitelisted domains, and why each is there."""
    entries = _store().list()
    if not entries:
        typer.echo("No domains whitelisted.")
        return
    for entry in entries:
        typer.echo(_whitelist_line(entry))


@whitelist_app.command(name="add")
def whitelist_add(
    domain: str = typer.Argument(..., help="Domain to allow via the API."),
    reason: str = typer.Option("", "--reason", "-R", help="Why it is whitelisted."),
) -> None:
    """Whitelist a domain so the cloud API may fetch it."""
    store = _store()
    store.add(domain, reason, source="manual")
    typer.echo(f"Whitelisted {normalize_host(domain)}.")


@whitelist_app.command(name="remove")
def whitelist_remove(
    domain: str = typer.Argument(..., help="Domain to remove."),
) -> None:
    """Remove a domain from the whitelist."""
    store = _store()
    store.remove(domain)
    typer.echo(f"Removed {normalize_host(domain)}.")


@whitelist_app.command(name="prune")
def whitelist_prune() -> None:
    """Drop unverified auto entries — the API could not get past those."""
    before = len(_store().list())
    _store().prune()
    dropped = before - len(_store().list())
    typer.echo(f"Pruned {dropped} entr{'y' if dropped == 1 else 'ies'}.")


app.add_typer(whitelist_app, name="whitelist")


# --- status (T3.4) ---------------------------------------------------------


def _ddgs_reachable() -> bool:
    """One real `text()` query: True when ddgs answers at all.

    Local Firecrawl is the backup search, so it is disabled here — otherwise a
    working local instance would mask a dead ddgs and the row would lie.
    """
    try:
        return bool(asyncio.run(_probe_ddgs()))
    except Exception:  # noqa: BLE001 - any failure is simply "unreachable"
        return False


async def _probe_ddgs() -> bool:
    from ddgs import DDGS

    searcher = Searcher(ddgs_factory=DDGS, max_results=1, backup=None)
    return bool(await searcher.search("briefing"))


@app.command()
def status() -> None:
    """Health check: local Firecrawl, API key, ddgs, and today's API usage."""
    loaded = _load(None)
    key = resolve_api_key(loaded, os.environ)
    used = usage_today(loaded.paths.state_dir / "api-calls.jsonl")
    local_ok = asyncio.run(_local_firecrawl(loaded).health())
    ddgs_ok = _ddgs_reachable()

    def row(label: str, ok: bool, detail: str) -> str:
        mark = "✔" if ok else "✘"
        return f"{mark} {label:<13} {detail}"

    lines = [
        row(
            "local firecrawl",
            local_ok,
            loaded.firecrawl.local.base_url
            if local_ok
            else f"{loaded.firecrawl.local.base_url} is unreachable. Run: docker compose up -d",
        ),
        row(
            "api key",
            key is not None,
            f"{loaded.firecrawl.api.key_env} is {'set' if key else 'not set'}"
            + ("" if key else ". Set it to use the cloud API."),
        ),
        row("ddgs", ddgs_ok, "reachable" if ddgs_ok else "unreachable"),
        row(
            "api usage",
            used < loaded.firecrawl.api.daily_limit,
            f"{used} of {loaded.firecrawl.api.daily_limit} calls today",
        ),
    ]
    for line in lines:
        typer.echo(line)


# --- config (T3.4) ---------------------------------------------------------


@app.command(name="config")
def show_config(
    config_file: str = typer.Option(None, "--config", help="Path to a briefing.yaml to load."),
) -> None:
    """Show the effective config and where it lives."""
    loaded = _load(config_file)
    lines = [
        f"state dir   {loaded.paths.state_dir}",
        f"whitelist   {loaded.paths.whitelist}",
        f"log file    {loaded.paths.log_file}",
        f"api calls   {loaded.paths.state_dir / 'api-calls.jsonl'}",
        "",
        (
            f"search      {loaded.search.results} results · region {loaded.search.region}"
            f" · auto news detect {loaded.search.auto_news_detect}"
        ),
        (
            f"local       {loaded.firecrawl.local.base_url}"
            f" · timeout {loaded.firecrawl.local.timeout_s}s"
            f" · concurrency {loaded.firecrawl.local.concurrency}"
        ),
        (
            f"api         {loaded.firecrawl.api.base_url}"
            f" · key env {loaded.firecrawl.api.key_env}"
            f" · daily limit {loaded.firecrawl.api.daily_limit}"
        ),
        (
            f"whitelist   auto_add {loaded.whitelist.auto_add}"
            f" · prefer api {loaded.whitelist.prefer_api_for_whitelisted}"
        ),
        f"summary     {loaded.summary.mode} · {loaded.summary.sentences} sentences",
        (
            f"dedup       {loaded.dedup.headline_ttl_days} day TTL"
            f" · {loaded.dedup.schedule_window_hours}h schedule window"
        ),
        f"topics      {', '.join(loaded.news_topics)}",
        f"blocklist   {', '.join(loaded.blocklist_topics)}",
    ]
    for line in lines:
        typer.echo(line)


def _research(
    query: str,
    *,
    results: int | None = None,
    time: str | None = None,
    news: bool = False,
    web: bool = False,
    no_api: bool = False,
    json_out: bool = False,
    plain: bool | None = None,
    config: str | None = None,
) -> None:
    """Wire the collaborators from config, run the pipeline, render the report.

    Every failure path exits with a plain-English message and a fix hint and no
    traceback (SPEC section 3); `_load` already turns a `ConfigError` into one.
    """
    if news and web:
        _fail(
            "--news and --web both set the search mode.",
            "Pick one: drop --news, or drop --web.",
        )
    loaded = _load(config)

    if results is not None and results < 1:
        _fail(f"--results must be at least 1, not {results}.", "Try a value like 10.")

    searcher = _searcher(loaded, _mode_for(news, web))
    if results is not None:
        searcher.max_results = results
    if time is not None:
        searcher.timelimit = _timelimit(time)

    whitelist = _whitelist(loaded)
    ui = None if json_out else UI(plain=plain)
    pipeline = _PIPELINE(
        searcher=searcher,
        router=_router(loaded, no_api, whitelist),
        extractor=Extractor(loaded.summary.sentences),
        whitelist=whitelist,
    )
    if ui is not None:
        # The header names the mode the run will actually use, not a second guess.
        ui.begin(query, searcher.mode_for(query))
    try:
        report = asyncio.run(pipeline.run(query, on_event=None if ui is None else ui.on_event))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        _fail("Interrupted.", "Run the same command again; partial results were not saved.")
    if ui is not None:
        ui.show(report)
    else:
        typer.echo(json.dumps(_as_json(report), indent=2))


def _mode_for(news: bool, web: bool) -> str | None:
    """`--news`/`--web` force the mode; neither leaves auto-detect in charge."""
    if news:
        return "news"
    if web:
        return "text"
    return None


def _load(config: str | None) -> Config:
    """Load the config, turning a ConfigError into a clean exit.

    Every command goes through here, so the SPEC §3 promise (plain-English error
    plus fix hint, never a traceback) holds for `config` and the subcommands too
    rather than only for `research`.
    """
    try:
        return load_config(
            Path(config).expanduser() if config is not None else None,
            state_dir=_STATE_DIR(),
        )
    except ConfigError as exc:
        _fail(exc.message, exc.hint)


def _local_firecrawl(loaded: Config) -> LocalFirecrawl:
    return LocalFirecrawl(
        base_url=loaded.firecrawl.local.base_url,
        timeout_s=loaded.firecrawl.local.timeout_s,
    )


def _searcher(loaded: Config, mode: str | None) -> Searcher:
    return Searcher(
        region=loaded.search.region,
        max_results=loaded.search.results,
        # SPEC section 2: a news query starts at the past day and widens on its
        # own; `Searcher`'s own default carries that rule, so nothing is set here.
        backup=_local_firecrawl(loaded),
        mode=mode,
    )


class Extractor:
    """Adapts `extract` to the pipeline's `extractor.extract(markdown, url=...)`.

    The pipeline's contract is an object with that method, and `extract` is a
    plain function taking `sentences` — this binds the configured sentence count.
    """

    def __init__(self, sentences: int = 2) -> None:
        self.sentences = sentences

    def extract(self, markdown: str, url: str = "") -> Extracted:
        return extract(markdown, sentences=self.sentences)


def _whitelist(loaded: Config) -> Whitelist:
    # `config.whitelist.path` is relative to the state dir in the default config;
    # `Paths.whitelist` is that path already resolved (T0.4's `_build_paths`).
    return Whitelist(loaded.paths.whitelist)


def _router(loaded: Config, no_api: bool, whitelist: Whitelist) -> Router:
    """The fetch router: local client, API client, whitelist, per SPEC §4."""
    key = resolve_api_key(loaded, os.environ)
    # A missing key is not fatal here: the router only needs it when a URL is
    # whitelisted or local is down, and both of those degrade to snippet-only on
    # a missing key. Failing early would block an entirely local run.
    return Router(
        local=_local_firecrawl(loaded),
        api=ApiFirecrawl(
            base_url=loaded.firecrawl.api.base_url,
            api_key=key,
            daily_limit=loaded.firecrawl.api.daily_limit,
            log_path=loaded.paths.state_dir / "api-calls.jsonl",
        ),
        whitelist=whitelist,
        no_api=no_api,
        prefer_api_for_whitelisted=loaded.whitelist.prefer_api_for_whitelisted,
    )


def _timelimit(time: str) -> str | None:
    """Map a CLI word to ddgs' `timelimit`, or fail naming the four values."""
    mapping = {"day": "d", "week": "w", "month": "m", "any": None}
    if time not in mapping:
        _fail(f"Unknown --time value {time!r}.", "Use one of: day, week, month, any.")
    return mapping[time]


def _as_json(report: Report) -> dict[str, t.Any]:
    """The report as JSON (SPEC section 3): every field a consumer would want.

    `method` and `error_class` are strings so a JSON reader gets the same badge
    vocabulary the terminal shows, and `ok` lets a consumer filter without
    knowing the enum.
    """
    return {
        "found": report.found,
        "succeeded": report.succeeded,
        "elapsed_s": report.elapsed_s,
        "whitelisted": [[domain, reason] for domain, reason in report.whitelisted],
        "results": [
            {
                "rank": result.rank,
                "title": result.title,
                "heading": result.heading,
                "summary": result.summary,
                "snippet": result.snippet,
                "url": result.url,
                "domain": result.domain,
                "source": result.source,
                "age": result.age,
                "method": result.method.value,
                "ok": result.ok,
                "error_class": result.error_class.name,
            }
            for result in report.results
        ],
    }


def _fail(message: str, hint: str) -> t.NoReturn:
    """A plain-English error with its fix hint, on stderr, and no traceback."""
    console = Console(stderr=True)
    console.print(f"[bold red]Error:[/bold red] {message}")
    if hint:
        console.print(f"[dim]Fix: {hint}[/dim]")
    raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
