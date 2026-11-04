"""The `briefing` command-line interface (SPEC sections 3 and 7).

`research`, `run`, `whitelist`, `status` and `config` exist; `run` is the
scheduled morning briefing (T5.4). Everything below the argument parsing is
the dependency wiring, which lives in one place so a test can swap the pipeline
(or any collaborator) by name rather than reaching into a running event loop.

Help text uses plain Click (`rich_markup_mode=None`) rather than Rich's panel
help: Rich wraps against a detected console width, which makes help snapshots
depend on the terminal. Plain Click's help is stable everywhere.
"""

from __future__ import annotations

import asyncio
import json
import os
import typing as t
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from briefing.ask import Answer, NoSearchResultsError
from briefing.briefing import DedupStore, FileAudioState, FileSessionSummary, Gate, TopicFilter
from briefing.config import (
    Config,
    ConfigError,
    default_config_path,
    discover_config,
    load_config,
    resolve_api_key,
    resolve_gemini_key,
)
from briefing.deliver import DeliveryError, SubprocessRunner, deliver
from briefing.extract import Extracted, extract
from briefing.fetch.api import ApiFirecrawl, usage_today
from briefing.fetch.local import FirecrawlSearch, LocalFirecrawl
from briefing.fetch.router import Method, Router
from briefing.log import configure as configure_logging
from briefing.log import get_logger
from briefing.pipeline import Pipeline, Report
from briefing.search import Searcher, SearchResult
from briefing.synth import Gemini, ResearchSynth, Speaker
from briefing.ui import UI, answer, card
from briefing.whitelist import Whitelist, WhitelistEntry, normalize_host


class HeadlineSearch:
    """One news topic in, headlines out (SPEC section 6).

    The seam between `briefing run` and the search stack: brief mode never runs
    the research pipeline, it asks per topic and keeps only the titles.
    `Searcher.search` never raises — it answers [] on failure — so neither does
    this, and a topic that fails simply contributes nothing to the briefing.
    """

    def __init__(self, searcher: Searcher) -> None:
        self.searcher = searcher

    async def headlines(self, topic: str) -> list[str]:
        return [hit.title for hit in await self.searcher.search(topic)]


def _search(loaded: Config) -> HeadlineSearch:
    """Brief mode is a news briefing, so its searcher is forced to news."""
    return HeadlineSearch(_searcher(loaded, "news"))


def _dedup(loaded: Config) -> DedupStore:
    """Headline hashes and the 24h schedule gate, both driven by config."""
    return DedupStore(
        loaded.paths.seen_headlines,
        ttl_days=loaded.dedup.headline_ttl_days,
        window_hours=loaded.dedup.schedule_window_hours,
    )


def _audio(loaded: Config) -> FileAudioState:
    """Aether's recorded audio state, the gate's second condition."""
    return FileAudioState(loaded.paths.audio_state)


def _speaker(loaded: Config, session_summary: str = "") -> Speaker:
    """The real speaker: Gemini for the text, both names and the session."""
    return Speaker(
        Gemini(),
        name=loaded.briefing.name,
        user_name=loaded.briefing.user_name,
        session_summary=session_summary,
    )


def _summarizer(loaded: Config) -> ResearchSynth:
    """`--llm` and `--synth`: research mode's two Gemini calls, one client.

    Brief mode's `Speaker` and this both wrap a `Gemini`, so a run makes at most
    one client and neither mode pays for the other's.
    """
    return ResearchSynth(Gemini())


# The seams the tests patch by name, so a whole run's collaborator graph swaps
# without touching a running event loop. Real code uses the real things.
_PIPELINE = Pipeline
_RUNNER = SubprocessRunner
_SEARCH = _search
_SPEAKER = _speaker
_AUDIO = _audio
_DEDUP = _dedup
_SUMMARIZER = _summarizer

# _ASK is the ask implementation that can be swapped by tests.
# It accepts CLI parameters and returns an Answer.


def _searcher_for_ask(loaded: Config, mode: str | None, no_api: bool) -> Searcher:
    """Create a Searcher for ask mode sharing the same configuration as research."""
    return Searcher(
        region=loaded.search.region,
        max_results=loaded.search.results,
        backup=FirecrawlSearch(
            base_url=loaded.firecrawl.local.base_url,
            timeout_s=loaded.firecrawl.local.timeout_s,
        ),
        mode=mode,
    )


async def _ask_implementation(
    query: str,
    *,
    news: bool = False,
    web: bool = False,
    time: str | None = None,
    pages: int = 3,
    no_api: bool = False,
    config: str | None = None,
) -> Answer:
    """Build collaborators and call ask.ask (the real implementation).

    This is the actual ask implementation that tests don't patch - they patch
    _ASK instead.
    """
    from briefing.ask import ask

    loaded = _load(config)

    mode = _mode_for(news, web)

    # Create searcher
    searcher = _searcher_for_ask(loaded, mode, no_api)
    if time is not None:
        searcher.timelimit = _timelimit(time)

    # Build components
    whitelist = _whitelist(loaded)
    router = _router(loaded, no_api, whitelist)

    async def _fetch_hit(hit: SearchResult) -> tuple[str, str, str]:
        """Fetch a single hit through the router."""
        result = await router.fetch(hit.url, hit.snippet or "", hit.title or "", hit.source)
        return result.heading, result.summary, hit.snippet or ""

    # Create a simple async wrapper for search
    async def _search_hits(q: str) -> list[SearchResult]:
        return await searcher.search(q)

    # Build extractor
    extractor = Extractor(loaded.summary.sentences)

    # Resolve Gemini key for the model
    gemini_key = resolve_gemini_key(loaded, os.environ)
    model: t.Callable[[str, str], t.Awaitable[str | None]] | None = None
    if gemini_key:
        from briefing.synth import Gemini

        llm = Gemini()

        async def _model(prompt: str, query: str) -> str | None:
            """Call the Gemini model."""
            try:
                return await llm.generate(prompt)
            except Exception:  # noqa: BLE001
                return None

        model = _model

    # Create knowledge adapters if enabled
    knowledge_adapters: list = []
    if loaded.knowledge.enabled:
        from briefing.knowledge.stackexchange import StackExchangeAdapter
        from briefing.knowledge.wikipedia import WikipediaAdapter

        if loaded.knowledge.wikipedia:
            wiki_adapter = WikipediaAdapter(loaded.knowledge)
            knowledge_adapters.append(wiki_adapter)

        if loaded.knowledge.stackexchange:
            se_adapter = StackExchangeAdapter(
                loaded.knowledge,
                key=loaded.knowledge.stackexchange_key_env
                and os.environ.get(loaded.knowledge.stackexchange_key_env),
            )
            knowledge_adapters.append(se_adapter)

    # Call the ask function from ask.py
    return await ask(
        query=query,
        search=_search_hits,
        fetch=_fetch_hit,
        extract=extractor.extract,
        model=model,
        results=loaded.ask.results,
        pages=pages,
        per_url_s=loaded.timeouts.per_url_s,
        deadline_s=loaded.ask.deadline_s,
        knowledge_enabled=loaded.knowledge.enabled,
        knowledge_adapters=knowledge_adapters if knowledge_adapters else None,
        knowledge_config=loaded.knowledge,
    )


# The seam that tests patch - starts as the real implementation
_ASK = _ask_implementation


def _ask(
    query: str,
    *,
    pages: int = 3,
    time: str | None = None,
    news: bool = False,
    web: bool = False,
    no_api: bool = False,
    json_out: bool = False,
    plain: bool | None = None,
    config: str | None = None,
    verbose: bool = False,
) -> None:
    """Ask a question and get an answer with numbered sources."""
    if news and web:
        _fail(
            "--news and --web both set the search mode.",
            "Pick one: drop --news, or drop --web.",
        )

    loaded = _load(config)
    _configure_logging(loaded, verbose)

    try:
        result: Answer = asyncio.run(
            _ASK(
                query=query,
                pages=pages,
                time=time,
                news=news,
                web=web,
                no_api=no_api,
                config=config,
            )
        )
    except NoSearchResultsError as exc:
        _fail(exc.message, exc.hint)

    # Render the output
    if json_out:
        typer.echo(json.dumps(_answer_as_json(result), indent=2))
    else:
        _render_answer(result, plain=plain)


def _render_answer(answer: Answer, *, plain: bool | None = None) -> None:
    """Render an Answer as an answer panel followed by source cards (like research).

    The output is:
    1. Answer panel (the text field, possibly from model or passages)
    2. Source cards (domain, title, URL, method badge) - only sources with text
    3. Footer with timings and warnings
    """
    from rich import box
    from rich.panel import Panel
    from rich.text import Text

    console = Console(stderr=(plain is True), width=80) if plain else Console(width=80)
    if plain:
        console.no_color = True

    # 1. Answer panel
    answer_panel = Panel(
        Text(answer.text),
        title="Answer",
        box=box.SIMPLE if plain else box.ROUNDED,
        expand=False,
    )
    console.print(answer_panel)

    # 2. Source cards - using the same UI as research mode
    # Filter out sources with no preview text (links with no previews are irrelevant)
    sources_with_text = [
        (num, title, url, domain, method, text)
        for num, title, url, domain, method, text in answer.sources
        if text  # Exclude sources with empty text
    ]

    if sources_with_text:
        console.print()
        for rank, (num, title, url, domain, method, text) in enumerate(sources_with_text, start=1):
            # Build a Result-like object for the card renderer
            result = _AnswerResult(
                rank=rank,
                title=title,
                url=url or "",
                domain=domain,
                method=Method[method],
                heading=title,
                summary=text,
                ok=True,
                age="",
                snippet=text,
                source="",
                warning=None,
            )
            console.print(card(result, plain=plain))

    # 3. Footer with timings and warnings
    footer_parts = []

    # Source counts by method (count all sources, including those without text)
    local_count = sum(1 for s in answer.sources if s[4] == "LOCAL")
    api_count = sum(1 for s in answer.sources if s[4] == "API")
    snippet_count = sum(1 for s in answer.sources if s[4] == "SNIPPET")

    if local_count or api_count or snippet_count:
        counts_parts = []
        if local_count:
            counts_parts.append(f"{local_count} local")
        if api_count:
            counts_parts.append(f"{api_count} API")
        if snippet_count:
            counts_parts.append(f"{snippet_count} snippet")
        footer_parts.append(" · ".join(counts_parts))

    if answer.used_llm:
        method_desc = "LLM"
    else:
        method_desc = "passages"
    footer_parts.append(f"{len(answer.sources)} sources ({method_desc})")

    if answer.timings:
        timing_parts = []
        if "search" in answer.timings:
            timing_parts.append(f"search: {answer.timings['search']:.1f}s")
        if "fetch" in answer.timings:
            timing_parts.append(f"fetch: {answer.timings['fetch']:.1f}s")
        if "model" in answer.timings:
            timing_parts.append(f"model: {answer.timings['model']:.1f}s")
        if timing_parts:
            footer_parts.append(" · " + " · ".join(timing_parts))

    if answer.warnings:
        footer_parts.append(" · " + "; ".join(answer.warnings))

    if footer_parts:
        footer_panel = Panel(
            Text(" · ".join(footer_parts), "dim"),
            box=box.SIMPLE,
            expand=False,
        )
        console.print(footer_panel)


class _AnswerResult:
    """Adapter that makes Answer sources compatible with the card() UI.

    This is a simple dataclass that mimics the Result structure expected by ui.card(),
    allowing us to reuse the research card rendering code for ask mode.
    """

    def __init__(
        self,
        rank: int,
        title: str,
        url: str,
        domain: str,
        method: Method,
        heading: str,
        summary: str,
        ok: bool,
        age: str,
        snippet: str,
        source: str,
        warning: str | None,
    ) -> None:
        self.rank = rank
        self.title = title
        self.url = url
        self.domain = domain
        self.method = method
        self.heading = heading
        self.summary = summary
        self.ok = ok
        self.age = age
        self.snippet = snippet
        self.source = source
        self.warning = warning or ""


def _answer_as_json(answer: Answer) -> dict[str, t.Any]:
    """Convert an Answer to a JSON-serializable dict."""
    return {
        "question": answer.question,
        "answer": answer.text,
        "used_llm": answer.used_llm,
        "sources": [
            {
                "number": s[0],
                "title": s[1],
                "url": s[2],
                "domain": s[3],
                "method": s[4],
                "passages": s[5],
            }
            for s in answer.sources
        ],
        "timings": answer.timings,
        "warnings": answer.warnings,
    }


def _STATE_DIR() -> Path:
    """Where the whitelist, dedup store and API call log live.

    Indirects `config._default_state_dir` so a test can point the whole CLI at a
    tmp dir with one `monkeypatch.setattr`, instead of setting `BRIEFING_STATE_DIR`
    and hoping no other test races it.
    """
    from briefing.config import _default_state_dir

    return _default_state_dir()


LOG = get_logger("briefing.cli")


def _configure_logging(loaded: Config, verbose: bool) -> None:
    """Turn the configured log file on, and stderr too when `--verbose`.

    The keys are resolved here because this is the one place that already has them:
    `log.configure` filters them out of every record, so a future log line holding
    the whole config (or an exception quoting a request) cannot leak them.
    """
    firecrawl_key = resolve_api_key(loaded, os.environ)
    gemini_key = resolve_gemini_key(loaded, os.environ)
    # Stack Exchange key: read from env at call time (knowledge adapter pattern)
    stackexchange_key = os.environ.get(loaded.knowledge.stackexchange_key_env, "") or None
    configure_logging(
        log_file=loaded.paths.log_file,
        verbose=verbose,
        secrets=[k for k in (firecrawl_key, gemini_key, stackexchange_key) if k],
    )


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
# `research` shorthand on the group, and `-a` is reserved for the `ask` shorthand.
# Typer rejects a duplicate short option.
# -a/--ask/--instant are the ask mode shorthands per D10.


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    research: str | None = typer.Option(
        None, "-r", "--research", metavar="QUERY", help="Shorthand for `briefing research`."
    ),
    ask_query: str | None = typer.Option(
        None, "-a", "--ask", "--instant", metavar="QUERY", help="Shorthand for `briefing ask`."
    ),
) -> None:
    """Research the web, or manage a briefing run."""
    # Check for mutually exclusive shorthand and subcommand (decision D12)
    # When invoked_subcommand is set, we're being called as part of a subcommand
    if ctx.invoked_subcommand is not None:
        if ask_query is not None:
            _fail(
                "Cannot use -a/--ask shorthand with 'briefing ask' subcommand.",
                "Use either -a 'question' or 'briefing ask question', not both.",
            )
        if research is not None:
            _fail(
                "Cannot use -r/--research shorthand with 'briefing research' subcommand.",
                "Use either -r 'question' or 'briefing research question', not both.",
            )

    if research is not None:
        # No options: SPEC section 3's shorthand takes a query and nothing else.
        # Extra flags belong to `briefing research`, which is the same operation.
        _research(research)
        raise typer.Exit()
    if ask_query is not None:
        # Ask mode shorthand: -a, --ask, or --instant
        _ask(ask_query)
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
    llm: bool = typer.Option(
        False, "--llm", help="Abstractive summaries via Gemini, instead of extractive."
    ),
    synth: bool = typer.Option(False, "--synth", help="One overall answer from all summaries."),
    no_api: bool = typer.Option(False, "--no-api", help="Never call the Firecrawl API."),
    json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    plain: bool | None = typer.Option(None, "--plain/--no-plain", help="Plain text, no color."),
    verbose: bool = typer.Option(False, "--verbose", help="Log each decision to stderr."),
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
        verbose=verbose,
        llm=llm,
        synth=synth,
    )


@app.command()
def ask(
    query: str = typer.Argument(..., metavar="QUERY", help="Question to answer."),
    pages: int = typer.Option(3, "--pages", help="Maximum number of pages to fetch."),
    time: str | None = typer.Option(None, "--time", help="Recency window: day, week, month, any."),
    news: bool = typer.Option(False, "--news", help="Force news search mode."),
    web: bool = typer.Option(False, "--web", help="Force web search mode."),
    no_api: bool = typer.Option(False, "--no-api", help="Never call the Firecrawl API."),
    json_out: bool = typer.Option(False, "--json", help="Emit the answer as JSON."),
    plain: bool | None = typer.Option(None, "--plain/--no-plain", help="Plain text, no color."),
    verbose: bool = typer.Option(False, "--verbose", help="Log each decision to stderr."),
    config: str | None = typer.Option(None, "--config", help="Path to a briefing.yaml to load."),
) -> None:
    """Answer a question using web search results with numbered sources.

    Ask searches the web, extracts pages, and generates an answer based on the
    fetched content. Unlike research mode which shows all results, ask mode
    produces a concise answer with citations.

    Examples:

        briefing ask "what is the current python version?"

        briefing ask "latest news in kenya" --pages 5 --time day

        briefing ask "who won the election?" --news --json
    """
    _ask(
        query,
        pages=pages,
        time=time,
        news=news,
        web=web,
        no_api=no_api,
        json_out=json_out,
        plain=plain,
        config=config,
        verbose=verbose,
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
    firecrawl_key = resolve_api_key(loaded, os.environ)
    gemini_key = resolve_gemini_key(loaded, os.environ)
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
            "firecrawl key",
            firecrawl_key is not None,
            f"{loaded.firecrawl.api.key_env} is {'set' if firecrawl_key else 'not set'}"
            + ("" if firecrawl_key else ". Set it to use the cloud API."),
        ),
        row(
            "gemini key",
            gemini_key is not None,
            f"{loaded.briefing.gemini.api_key_env} is {'set' if gemini_key else 'not set'}"
            + ("" if gemini_key else ". Set it to use --llm/--synth."),
        ),
        row("ddgs", ddgs_ok, "reachable" if ddgs_ok else "unreachable"),
        row(
            "api usage",
            used < loaded.firecrawl.api.daily_limit,
            f"{used} of {loaded.firecrawl.api.daily_limit} calls today",
        ),
        # T6.2: naming the log file is what makes a bug report answerable.
        row("log file", True, str(loaded.paths.log_file)),
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
    in_force = _config_file(config_file)
    lines = [
        "config file " + (str(in_force) if in_force is not None else "none (built-in defaults)"),
        (f"           copy config/briefing.yaml.example to {default_config_path()} to set one"),
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
        f"gemini      {loaded.briefing.gemini.model} · key env {loaded.briefing.gemini.api_key_env}",
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
    verbose: bool = False,
    llm: bool = False,
    synth: bool = False,
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
    _configure_logging(loaded, verbose)

    if results is not None and results < 1:
        _fail(f"--results must be at least 1, not {results}.", "Try a value like 10.")

    searcher = _searcher(loaded, _mode_for(news, web))
    if results is not None:
        searcher.max_results = results
    if time is not None:
        searcher.timelimit = _timelimit(time)

    whitelist = _whitelist(loaded)
    ui = None if json_out else UI(plain=plain)
    # Built only when a flag asks for it. Neither Gemini feature is on by default
    # (SPEC section 2), and a client that is never called is never built.
    gemini_key = resolve_gemini_key(loaded, os.environ)
    if (llm or synth) and not gemini_key:
        typer.echo(
            "No Gemini API key found (set GEMINI_API_KEY); using extractive summary.",
            err=True,
        )
    summarizer = _SUMMARIZER(loaded) if (llm or synth) and gemini_key else None
    pipeline = _PIPELINE(
        searcher=searcher,
        router=_router(loaded, no_api, whitelist),
        extractor=Extractor(loaded.summary.sentences),
        whitelist=whitelist,
        concurrency=loaded.firecrawl.local.concurrency,
        timeout_s=loaded.timeouts.per_url_s,
        summarizer=summarizer,
    )
    if ui is not None:
        # The header names the mode the run will actually use, not a second guess.
        ui.begin(query, searcher.mode_for(query))
    try:
        report = asyncio.run(pipeline.run(query, on_event=None if ui is None else ui.on_event))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        _fail("Interrupted.", "Run the same command again; partial results were not saved.")
    # `--synth` is its own call, after the run: it answers from every page, so it
    # has nothing to say until the run is over. Second `asyncio.run` rather than a
    # nested one, because the pipeline's loop is already closed by then.
    answer_text = (
        asyncio.run(summarizer.answer(query=query, sources=_answer_sources(report)))
        if synth and summarizer is not None
        else None
    )
    if ui is not None:
        ui.show(report)
        if answer_text:
            ui.console.print(answer(answer_text))
    else:
        typer.echo(json.dumps(_as_json(report, answer_text), indent=2))
    LOG.info(
        "research %r via %s search: %d results, %d fetched ok, %.1fs",
        query,
        searcher.mode_for(query) or "auto",
        report.found,
        report.succeeded,
        report.elapsed_s,
    )


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the command, speak nothing."),
    verbose: bool = typer.Option(False, "--verbose", help="Log each decision to stderr."),
    config: str = typer.Option(None, "--config", help="Path to a YAML config file."),
) -> None:
    """Run the morning briefing now: the scheduler's entry point (SPEC section 6).

    In order: the 24h gate, then the audio gate, then headlines per unblocked
    topic minus the ones already seen, then synthesis, then delivery. A refused
    run says why; a run with no headlines still speaks the greeting, because
    silence is the one outcome a morning briefing must not produce.
    """
    loaded = _load(config)
    _configure_logging(loaded, verbose)
    asyncio.run(_briefing(loaded, dry_run=dry_run))


async def _briefing(loaded: Config, *, dry_run: bool) -> None:
    """The briefing itself, with every collaborator taken from its seam.

    Async because search, the gates, synthesis and delivery all are. A test
    patches the seams instead of calling this directly, which is what keeps the
    whole graph — including `voice-tts` — off a real run.
    """
    gemini_key = resolve_gemini_key(loaded, os.environ)
    if not gemini_key:
        typer.echo(
            "No Gemini API key found (set GEMINI_API_KEY); using plain greeting.",
            err=True,
        )
    store = _DEDUP(loaded)
    gate = Gate(store, _AUDIO(loaded))

    now = datetime.now(UTC)
    schedule = store.schedule_id(now)
    refusal = await gate.refusal(now=now, schedule=schedule)
    if refusal is not None:
        typer.echo(f"Briefing not spoken: {refusal}.")
        return

    keep = TopicFilter(loaded.blocklist_topics).keep(loaded.news_topics)
    search = _SEARCH(loaded)
    fresh: list[str] = []
    for topic in keep:
        for headline in await search.headlines(topic):
            if headline.strip() and not store.seen(headline):
                fresh.append(headline)

    summary = await FileSessionSummary(loaded.paths.session_summary).text()
    speaker = _SPEAKER(loaded, summary)
    try:
        delivery = await deliver(
            (await speaker.briefing(fresh)).text, runner=_RUNNER(), dry_run=dry_run
        )
    except DeliveryError as exc:
        _fail(exc.message, exc.hint)

    if not dry_run:
        # Nothing is remembered for a dry run, and a failed delivery has
        # already exited above, so what gets recorded here was really spoken.
        for headline in fresh:
            store.record(headline)
        store.mark_fired(schedule, now=now)
    typer.echo(f"{'Would speak (dry run)' if dry_run else 'Spoke'}: {delivery.text}")


def _mode_for(news: bool, web: bool) -> str | None:
    """`--news`/`--web` force the mode; neither leaves auto-detect in charge."""
    if news:
        return "news"
    if web:
        return "text"
    return None


def _config_file(config: str | None, env: t.Mapping[str, str] = os.environ) -> Path | None:
    """Which config file is in force, or `None` for built-in defaults (T8.3).

    `--config` wins and must exist; without it, `discover_config` looks at
    `$BRIEFING_CONFIG` and then the platformdirs default, and only ever returns a
    file that exists.
    """
    if config is not None:
        return Path(config).expanduser()
    return discover_config(env)


def _load(config: str | None, env: t.Mapping[str, str] = os.environ) -> Config:
    """Load the config, turning a ConfigError into a clean exit.

    Every command goes through here, so the SPEC §3 promise (plain-English error
    plus fix hint, never a traceback) holds for `config` and the subcommands too
    rather than only for `research`.
    """
    try:
        return load_config(_config_file(config, env), state_dir=_STATE_DIR())
    except ConfigError as exc:
        _fail(exc.message, exc.hint)


def _local_firecrawl(loaded: Config) -> LocalFirecrawl:
    return LocalFirecrawl(
        base_url=loaded.firecrawl.local.base_url,
        timeout_s=loaded.firecrawl.local.timeout_s,
    )


def _searcher(loaded: Config, mode: str | None) -> Searcher:
    # T8.2: the backup is `FirecrawlSearch`, not the scrape client — it POSTs to
    # `/v2/search`, honors `mode`, and never raises (a failed backup is the last
    # event in a search that already failed).
    return Searcher(
        region=loaded.search.region,
        max_results=loaded.search.results,
        # SPEC section 2: a news query starts at the past day and widens on its
        # own; `Searcher`'s own default carries that rule, so nothing is set here.
        backup=FirecrawlSearch(
            base_url=loaded.firecrawl.local.base_url,
            timeout_s=loaded.firecrawl.local.timeout_s,
        ),
        mode=mode,
    )


class Extractor:
    """Adapts `extract` to the pipeline's `extractor.extract(markdown, url=...)`.

    The pipeline's contract is an object with that method, and `extract` is a
    plain function taking `sentences` — this binds the configured sentence count.
    """

    def __init__(self, sentences: int = 2) -> None:
        self.sentences = sentences

    def extract(
        self,
        markdown: str,
        title: str | None = None,
        url: str = "",
        search_title: str = "",
        query: str | None = None,
    ) -> Extracted:
        return extract(
            markdown,
            title=title,
            sentences=self.sentences,
            url=url,
            search_title=search_title,
            query=query,
        )


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
            timeout_s=loaded.firecrawl.api.timeout_s,
            log_path=loaded.paths.state_dir / "api-calls.jsonl",
        ),
        whitelist=whitelist,
        no_api=no_api,
        prefer_api_for_whitelisted=loaded.whitelist.prefer_api_for_whitelisted,
        backoff_s=loaded.timeouts.transient_backoff_s,
    )


def _timelimit(time: str) -> str | None:
    """Map a CLI word to ddgs' `timelimit`, or fail naming the four values."""
    mapping = {"day": "d", "week": "w", "month": "m", "any": None}
    if time not in mapping:
        _fail(f"Unknown --time value {time!r}.", "Use one of: day, week, month, any.")
    return mapping[time]


def _answer_sources(report: Report) -> list[tuple[str, str]]:
    """(title, summary) per page worth citing, numbered later by `ResearchSynth`.

    Only pages the run actually got content for: a failed fetch has no summary to
    cite, and a source the answer invents from a snippet is worse than no source.
    """
    return [
        (result.title or result.heading, result.summary)
        for result in report.results
        if result.ok and result.summary
    ]


def _as_json(report: Report, answer: str | None = None) -> dict[str, t.Any]:
    """The report as JSON (SPEC section 3): every field a consumer would want.

    `method` and `error_class` are strings so a JSON reader gets the same badge
    vocabulary the terminal shows, and `ok` lets a consumer filter without
    knowing the enum. `answer` is the `--synth` answer, absent without the flag so
    a consumer never filters a field it did not ask for.
    """
    payload: dict[str, t.Any] = {
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
                # SPEC section 4's reason, so a consumer sees what the terminal
                # sees rather than inferring it from `method` alone.
                "warning": result.warning,
            }
            for result in report.results
        ],
    }
    if answer is not None:
        payload["answer"] = answer
    return payload


def _fail(message: str, hint: str) -> t.NoReturn:
    """A plain-English error with its fix hint, on stderr, and no traceback."""
    console = Console(stderr=True)
    console.print(f"[bold red]Error:[/bold red] {message}")
    if hint:
        console.print(f"[dim]Fix: {hint}[/dim]")
    raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
