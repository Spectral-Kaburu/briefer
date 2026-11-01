"""The `briefing` command-line interface (SPEC sections 3 and 7).

`research` is the only command implemented so far; `run`, `whitelist`, `status`
and `config` arrive with T3.4. Everything below the argument parsing is the
dependency wiring, which lives in one place so a test can swap the pipeline (or
the collaborators) by name rather than reaching into a running event loop.

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
from briefing.fetch.api import ApiFirecrawl
from briefing.fetch.local import LocalFirecrawl
from briefing.fetch.router import Router
from briefing.pipeline import Pipeline, Report
from briefing.search import Searcher
from briefing.ui import UI
from briefing.whitelist import Whitelist

# The seam the CLI tests use: patching this one name swaps the whole run's
# collaborator graph. Real code uses the real `Pipeline`.
_PIPELINE = Pipeline

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
    traceback (SPEC section 3). `ConfigError` carries its own hint; the rest
    state the command that would fix the problem.
    """
    if news and web:
        _fail(
            "--news and --web both set the search mode.",
            "Pick one: drop --news, or drop --web.",
        )
    try:
        loaded = _load(config)
    except ConfigError as exc:
        _fail(exc.message, exc.hint)

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
    return load_config(Path(config).expanduser() if config is not None else None)


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
