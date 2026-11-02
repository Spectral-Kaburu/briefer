"""Tests for cli.py (T3.3, T3.4): the Typer CLI surface, run offline.

Every test drives `runner.invoke(app, ...)` with the pipeline substituted through
`monkeypatch`, and the state dir pointed at a tmp dir, so no search, fetch, or
network happens here. What is under test is the CLI's own job: parsing the flags
SPEC section 3 names, wiring them into the pipeline, and rendering the report as
cards or as JSON.
"""

from __future__ import annotations

import dataclasses
import json
import typing as t
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

import briefing.cli as cli_module
from briefing.cli import app
from briefing.fetch.router import ErrorClass, Method
from briefing.pipeline import Report, Result
from briefing.whitelist import Whitelist

runner = CliRunner()

_VERBOSE_HELP = ["research", "--help"]


class FakePipeline:
    """Stands in for `Pipeline`, recording how it was constructed.

    Class-level on purpose: the CLI constructs it internally, so a test reads the
    kwargs back off the class after `invoke` returns.
    """

    built: t.ClassVar[dict[str, t.Any]] = {}
    report: t.ClassVar[Report]

    def __init__(self, **kwargs: t.Any) -> None:
        FakePipeline.built = kwargs

    async def run(self, query: str, *, on_event: t.Any = None) -> Report:
        FakePipeline.built["query"] = query
        return FakePipeline.report  # type: ignore[attr-defined]


def _configure(report: Report, monkeypatch: t.Any) -> None:
    """Point the CLI's factory at a fake pipeline returning `report`."""
    FakePipeline.report = report
    monkeypatch.setattr(cli_module, "_PIPELINE", FakePipeline)


def _result() -> Result:
    return Result(
        rank=1,
        title="Bank raises rate",
        url="https://nation.africa/bank-raises-rate",
        domain="nation.africa",
        snippet="The central bank raised its lending rate.",
        source="",
        age="2h ago",
        heading="Bank raises rate",
        summary="A two sentence summary.",
        method=Method.LOCAL,
        ok=True,
        error_class=ErrorClass.OK,
    )


def _report() -> Report:
    return Report(
        results=[_result()],
        found=1,
        elapsed_s=6.4,
        whitelisted=[("example.co.ke", "captcha")],
    )


# --- help -----------------------------------------------------------------


def test_help_exits_zero_and_lists_the_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "research" in result.stdout


def test_research_help_reads_clearly_with_examples() -> None:
    """T3.3's acceptance: the help must read clearly and include examples."""
    result = runner.invoke(app, _VERBOSE_HELP)
    assert result.exit_code == 0
    assert "QUERY" in result.stdout
    assert "latest" in result.stdout.lower()


def test_research_help_lists_every_flag_from_spec_section_3() -> None:
    result = runner.invoke(app, _VERBOSE_HELP)
    for flag in (
        "--results",
        "--time",
        "--news",
        "--web",
        "--llm",
        "--synth",
        "--no-api",
        "--json",
        "--plain",
    ):
        assert flag in result.stdout, flag


def test_the_r_shorthand_is_documented() -> None:
    result = runner.invoke(app, ["--help"])
    assert "-r" in result.stdout


# --- --llm and --synth (T4.1; SPEC section 2) --------------------------------

#: What the fake model answers. Set per test rather than once, because the
#: "said nothing" case leaves the class attribute None and would otherwise leak
#: into the next test.
ANSWER = "The bank held its rate steady."


class FakeSummarizer:
    """Stands in for `ResearchSynth`: no Gemini client is ever constructed.

    Class-level for the same reason `FakePipeline` is — the CLI builds the real
    factory internally, so the test reads back what it built and what it asked.
    """

    built: t.ClassVar[dict[str, t.Any]] = {}
    #: What the fake answers; None is the "model said nothing" case.
    text: t.ClassVar[str | None] = "The bank held its rate steady."

    def __init__(self, loaded: t.Any = None) -> None:
        # The seam is called with the config positionally, the way the real
        # `_summarizer(loaded)` is, even though this fake ignores it.
        FakeSummarizer.built = {"loaded": loaded}

    async def summary(self, *, heading: str, text: str, fallback: str) -> str:
        FakeSummarizer.built["summary_call"] = (heading, fallback)
        return "An abstractive summary."

    async def answer(self, *, query: str, sources: t.Sequence[tuple[str, str]]) -> str | None:
        FakeSummarizer.built["answer_query"] = query
        FakeSummarizer.built["answer_sources"] = list(sources)
        return FakeSummarizer.text


def test_llm_hands_the_pipeline_a_summarizer(monkeypatch: t.Any) -> None:
    """SPEC section 2's `--llm`: the pipeline gets something to summarise with."""
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_SUMMARIZER", FakeSummarizer)
    runner.invoke(app, ["research", "bank rate", "--llm"])
    assert isinstance(FakePipeline.built["summarizer"], FakeSummarizer)


def test_without_llm_the_summarizer_is_never_built(monkeypatch: t.Any) -> None:
    """The extractive default must not construct a Gemini client it will not use."""
    _configure(_report(), monkeypatch)
    builds: list[int] = []
    monkeypatch.setattr(cli_module, "_SUMMARIZER", lambda _loaded: builds.append(1))
    runner.invoke(app, ["research", "bank rate"])
    assert builds == []
    assert FakePipeline.built["summarizer"] is None


def test_synth_prints_the_answer_after_the_cards(monkeypatch: t.Any) -> None:
    """SPEC section 2's `--synth`: one answer, over the numbered results."""
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_SUMMARIZER", FakeSummarizer)
    FakeSummarizer.text = ANSWER
    result = runner.invoke(app, ["research", "bank rate", "--synth"])
    assert ANSWER in result.stdout


def test_synth_asks_about_the_query_with_a_source_per_result(monkeypatch: t.Any) -> None:
    """The answer cites sources, so the prompt is numbered per fetched page."""
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_SUMMARIZER", FakeSummarizer)
    runner.invoke(app, ["research", "bank rate", "--synth"])
    assert FakeSummarizer.built["answer_query"] == "bank rate"
    assert FakeSummarizer.built["answer_sources"] == [
        ("Bank raises rate", "A two sentence summary.")
    ]


def test_synth_prints_no_answer_block_when_the_model_has_none(monkeypatch: t.Any) -> None:
    """A model that cannot answer leaves the cards as the answer, unadorned."""
    FakeSummarizer.text = None
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_SUMMARIZER", FakeSummarizer)
    result = runner.invoke(app, ["research", "bank rate", "--synth"])
    assert "Answer" not in result.stdout
    assert "A two sentence summary." in result.stdout


def test_json_carries_the_synth_answer(monkeypatch: t.Any) -> None:
    """A `--json` consumer gets the answer as a field, not as text to scrape."""
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_SUMMARIZER", FakeSummarizer)
    FakeSummarizer.text = ANSWER
    result = runner.invoke(app, ["research", "bank rate", "--synth", "--json"])
    assert json.loads(result.stdout)["answer"] == ANSWER


def test_json_has_no_answer_field_without_synth(monkeypatch: t.Any) -> None:
    """`--synth` is opt-in, so its field is absent unless it was asked for."""
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "bank rate", "--json"])
    assert "answer" not in json.loads(result.stdout)


# --- the -r shorthand ----------------------------------------------------


def test_a_bare_query_runs_the_research_command(monkeypatch: t.Any) -> None:
    """SPEC section 3: `briefing -r "<query>"` is shorthand for `research`."""
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["-r", "latest news in kenya"])
    assert result.exit_code == 0, result.stdout
    assert FakePipeline.built["query"] == "latest news in kenya"


def test_the_shorthand_renders_the_result_cards(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["-r", "latest news in kenya"])
    # A Rich console in a captured stream is not a terminal, so UI auto-plains
    # (SPEC section 3, "auto --plain when piped"); the badge text survives.
    assert "LOCAL" in result.stdout
    assert "1 found" in result.stdout


def test_a_colored_run_keeps_the_emoji_badge(monkeypatch: t.Any) -> None:
    """The piped case drops emoji; a terminal run keeps them (SPEC section 3).

    Asserted on the emoji alone: Rich writes the styled badge as separate spans,
    so an ANSI code sits between the emoji and its label in a colored render.
    """
    _configure(_report(), monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = runner.invoke(app, ["-r", "latest news in kenya"])
    assert "🟢" in result.stdout
    assert "LOCAL" in result.stdout


# --- flags reach the pipeline ---------------------------------------------


def test_the_results_flag_sets_the_search_width(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--results", "25"])
    assert result.exit_code == 0, result.stdout
    searcher = FakePipeline.built["searcher"]
    assert searcher.max_results == 25


def test_a_news_query_uses_the_news_searcher(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    runner.invoke(app, ["research", "latest kenya news", "--news"])
    assert FakePipeline.built["searcher"].region
    # The query's own news words are not what makes this a news run — the flag is,
    # so the searcher's mode must say so rather than being re-derived per query.
    assert FakePipeline.built["searcher"].mode == "news"


def test_news_flag_forces_news_on_a_plain_query(monkeypatch: t.Any) -> None:
    """SPEC section 2: --news forces news search even with no news word in the query.

    `Searcher.search` picks the endpoint from the query, so a flag that only
    changed the header word would leave `ddgs.text` running. Asserting on the
    searcher's own mode is what makes that impossible.
    """
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya economy", "--news"])
    assert result.exit_code == 0, result.stderr
    assert FakePipeline.built["searcher"].mode == "news"


def test_web_flag_forces_web_on_a_news_query(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "latest breaking news", "--web"])
    assert result.exit_code == 0, result.stderr
    assert FakePipeline.built["searcher"].mode == "text"


def test_no_flag_leaves_the_searcher_to_auto_detect(monkeypatch: t.Any) -> None:
    """The default is SPEC section 2's auto-detect, which is `mode=None`."""
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "latest breaking news"])
    assert result.exit_code == 0, result.stderr
    assert FakePipeline.built["searcher"].mode is None


def test_no_api_disables_the_api_for_the_run(monkeypatch: t.Any) -> None:
    """SPEC section 4's `--no-api`: no API calls, and nothing whitelisted."""
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--no-api"])
    assert result.exit_code == 0, result.stdout
    assert FakePipeline.built["router"].no_api is True


def test_the_time_flag_is_accepted(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    for value in ("day", "week", "month", "any"):
        result = runner.invoke(app, ["research", "kenya news", "--time", value])
        assert result.exit_code == 0, (value, result.stdout)


def test_news_and_web_are_mutually_exclusive(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--news", "--web"])
    assert result.exit_code != 0
    assert "web" in result.stderr or "news" in result.stderr
    assert "Traceback" not in result.stderr


# --- json ----------------------------------------------------------------


def test_json_output_is_valid_json(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["found"] == 1
    assert payload["results"][0]["domain"] == "nation.africa"
    assert payload["results"][0]["method"] == "local"
    assert payload["whitelisted"] == [["example.co.ke", "captcha"]]


# --- configurable timing (T6.1) --------------------------------------------


def _timing_config(tmp_path: t.Any, text: str) -> Path:
    path = tmp_path / "briefing.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_pipeline_gets_its_configured_timeout(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """SPEC section 4's per-URL timeout must be tunable without editing source."""
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    config = _timing_config(tmp_path, "timeouts: {per_url_s: 7}")
    result = runner.invoke(app, ["research", "kenya news", "--config", str(config)])
    assert result.exit_code == 0, result.stdout
    assert FakePipeline.built["timeout_s"] == 7


def test_the_router_gets_its_configured_backoff(tmp_path: t.Any) -> None:
    """The one retry SPEC allows for a transient 5xx sleeps this long."""
    from briefing.config import load_config
    from briefing.whitelist import Whitelist

    config = _timing_config(tmp_path, "timeouts: {transient_backoff_s: 2}")
    loaded = load_config(config, state_dir=tmp_path)
    router = cli_module._router(loaded, no_api=False, whitelist=Whitelist(tmp_path / "w.yaml"))
    assert router.backoff_s == 2


def test_the_api_client_gets_its_configured_timeout(tmp_path: t.Any) -> None:
    """A slow API route must be tunable without editing source (T6.1)."""
    from briefing.config import load_config
    from briefing.whitelist import Whitelist

    config = _timing_config(tmp_path, "firecrawl:\n  api:\n    timeout_s: 12")
    loaded = load_config(config, state_dir=tmp_path)
    router = cli_module._router(loaded, no_api=False, whitelist=Whitelist(tmp_path / "w.yaml"))
    assert router.api.timeout_s == 12


def test_the_pipeline_gets_its_configured_concurrency(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """SPEC section 2: "default 5 at a time", carried by config.

    The key was displayed by `briefing config` but never reached the pipeline,
    so setting it changed nothing — a worse outcome than not offering it.
    """
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    config = _timing_config(tmp_path, "firecrawl:\n  local:\n    concurrency: 2")
    result = runner.invoke(app, ["research", "kenya news", "--config", str(config)])
    assert result.exit_code == 0, result.stdout
    assert FakePipeline.built["concurrency"] == 2


def test_json_output_carries_no_rich_decorations(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--json"])
    assert "╭" not in result.stdout
    assert "Query:" not in result.stdout
    assert "🟢" not in result.stdout


def test_json_output_says_how_each_page_was_fetched(monkeypatch: t.Any) -> None:
    """SPEC section 3: a JSON consumer needs the method badge too."""
    report = _report()
    report.results.append(
        dataclasses.replace(_result(), rank=2, domain="example.co.ke", method=Method.API, ok=True)
    )
    _configure(report, monkeypatch)
    payload = json.loads(runner.invoke(app, ["research", "kenya news", "--json"]).stdout)
    assert [r["method"] for r in payload["results"]] == ["local", "api"]


def test_json_output_carries_the_reason_a_page_degraded(monkeypatch: t.Any) -> None:
    """SPEC section 4: an Aether consumer needs the same reason the terminal gets."""
    report = _report()
    report.results.append(
        dataclasses.replace(
            _result(),
            rank=2,
            domain="example.co.ke",
            method=Method.SNIPPET,
            ok=False,
            warning="page not found (HTTP 404)",
        )
    )
    _configure(report, monkeypatch)
    payload = json.loads(runner.invoke(app, ["research", "kenya news", "--json"]).stdout)
    assert payload["results"][1]["warning"] == "page not found (HTTP 404)"


# --- plain ---------------------------------------------------------------


def test_plain_output_has_no_emoji_and_no_ansi(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--plain"])
    assert result.exit_code == 0, result.stdout
    assert "LOCAL" in result.stdout
    assert "🟢" not in result.stdout


# --- errors --------------------------------------------------------------


def test_an_unknown_flag_is_a_clean_error(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--nonsuch"])
    assert result.exit_code != 0
    # Click reports usage errors on stderr; nothing may reach the result stream.
    assert "nonsuch" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_missing_query_is_a_clean_error() -> None:
    result = runner.invoke(app, ["research"])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert "QUERY" in result.stderr


def test_the_config_error_is_shown_with_a_fix_hint(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """SPEC section 3: plain-English message plus a fix hint, never a traceback."""
    bad = tmp_path / "briefing.yaml"
    bad.write_text("news_topics: 5\n", encoding="utf-8")
    result = runner.invoke(app, ["research", "kenya news", "--config", str(bad)])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert "news_topics" in result.stderr
    assert "Fix" in result.stderr


# --- the config wiring -----------------------------------------------------


def test_an_unknown_time_value_is_a_clean_error(monkeypatch: t.Any) -> None:
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--time", "fortnight"])
    assert result.exit_code != 0
    assert "fortnight" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_results_value_below_one_is_a_clean_error(monkeypatch: t.Any) -> None:
    """SPEC section 3's "plain English plus a fix hint" covers bad values too."""
    _configure(_report(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--results", "0"])
    assert result.exit_code != 0
    assert "0" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_named_config_file_that_is_absent_still_runs(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """An explicitly named file that does not exist is not fatal (config.py)."""
    _configure(_report(), monkeypatch)
    missing = tmp_path / "no-such.yaml"
    result = runner.invoke(app, ["research", "kenya news", "--config", str(missing)])
    assert result.exit_code == 0, result.stderr


# --- the extractor adapter -------------------------------------------------


def test_the_extractor_honors_the_configured_sentence_count() -> None:
    """`summary.sentences` is the only config the summary reads (SPEC section 7).

    The sentences are long enough to be "substantial", because extract.py
    deliberately returns an all-short page untrimmed rather than emitting nothing.
    """
    from briefing.cli import Extractor

    markdown = (
        "# Heading\n\n"
        "The central bank raised its lending rate by fifty basis points on Thursday. "
        "It cited persistent inflation pressure that has now lasted several quarters. "
        "Analysts expect a further tightening before the end of the financial year."
    )
    assert Extractor(1).extract(markdown).summary.count(".") == 1
    assert Extractor(2).extract(markdown).summary.count(".") == 2


def test_the_extractor_exposes_the_method_the_pipeline_calls() -> None:
    """`Pipeline` calls `extractor.extract(markdown, url=...)`, not `extract(...)`."""
    from briefing.cli import Extractor

    extracted = Extractor().extract("# A heading\n\nSome prose.", url="https://e.co.ke/x")
    assert extracted.heading
    assert extracted.summary


# --- whitelist subcommands (T3.4) ------------------------------------------


def test_whitelist_list_shows_stored_entries(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    store = Whitelist(tmp_path / "whitelist.yaml")
    store.add("example.co.ke", "captcha", source="auto")
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "list"])
    assert result.exit_code == 0, result.stderr
    assert "example.co.ke" in result.stdout
    assert "captcha" in result.stdout


def test_whitelist_list_of_an_empty_store_says_so(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    """An empty whitelist is a normal state, not an error."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "list"])
    assert result.exit_code == 0, result.stderr
    assert "no domains" in result.stdout.lower()


def test_whitelist_add_records_the_domain(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "add", "paywalled.example", "--reason", "paywall"])
    assert result.exit_code == 0, result.stderr
    entries = {e.domain: e for e in Whitelist(tmp_path / "whitelist.yaml").list()}
    assert "paywalled.example" in entries
    assert entries["paywalled.example"].source == "manual"
    assert entries["paywalled.example"].reason == "paywall"


def test_whitelist_add_normalizes_the_host(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    """SPEC section 4: entries are hosts without scheme, www, or port."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "add", "https://WWW.Paywalled.example:8080/a?b=1"])
    assert result.exit_code == 0, result.stderr
    domains = [e.domain for e in Whitelist(tmp_path / "whitelist.yaml").list()]
    assert domains == ["paywalled.example"]
    # The echo repeats the normalized host back, not the raw argument: a user who
    # pasted a URL needs to see what was actually stored.
    assert "paywalled.example" in result.stdout.lower()
    assert "8080" not in result.stdout


def test_whitelist_add_is_idempotent(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    runner.invoke(app, ["whitelist", "add", "a.example", "--reason", "first"])
    runner.invoke(app, ["whitelist", "add", "a.example", "--reason", "second"])
    entries = Whitelist(tmp_path / "whitelist.yaml").list()
    assert [e.domain for e in entries] == ["a.example"]
    assert entries[0].reason == "first"


def test_whitelist_remove_drops_the_domain(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    store = Whitelist(tmp_path / "whitelist.yaml")
    store.add("gone.example", "captcha")
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "remove", "gone.example"])
    assert result.exit_code == 0, result.stderr
    assert Whitelist(tmp_path / "whitelist.yaml").is_whitelisted("gone.example") is False


def test_whitelist_remove_of_an_absent_domain_is_not_an_error(
    tmp_path: t.Any, monkeypatch: t.Any
) -> None:
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "remove", "never-there.example"])
    assert result.exit_code == 0, result.stderr


def test_whitelist_prune_drops_unverified_auto_entries(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    """SPEC section 4: the API could not get past these, so they are noise."""
    store = Whitelist(tmp_path / "whitelist.yaml")
    store.add("unverified.example", "captcha", source="auto")
    store.add("manual.example", "owner asked", source="manual")
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "prune"])
    assert result.exit_code == 0, result.stderr
    remaining = [e.domain for e in Whitelist(tmp_path / "whitelist.yaml").list()]
    assert remaining == ["manual.example"]


def test_whitelist_prune_keeps_verified_entries(tmp_path: t.Any, monkeypatch: t.Any) -> None:
    """A verified entry earned its place: the API succeeded on it once."""
    store = Whitelist(tmp_path / "whitelist.yaml")
    store.add("verified.example", "captcha", source="auto")
    store.record_api_call("verified.example", success=True)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["whitelist", "prune"])
    assert result.exit_code == 0, result.stderr
    assert Whitelist(tmp_path / "whitelist.yaml").is_whitelisted("verified.example")


# --- status (T3.4) ---------------------------------------------------------


def _status_env(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """An isolated state dir, with the network probes stubbed out."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_ddgs_reachable", lambda: True)


def test_status_reports_every_check_spec_section_3_names(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    _status_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    lowered = result.stdout.lower()
    assert "local firecrawl" in lowered
    assert "api key" in lowered
    assert "ddgs" in lowered
    assert "api usage" in lowered


def test_status_names_the_local_url_when_it_is_unreachable(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """SPEC section 3: errors carry a fix hint, and this one names the port."""
    _status_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "localhost:3002" in result.stdout


def test_status_says_the_key_is_missing_and_names_the_env_var(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    _status_env(monkeypatch, tmp_path)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "FIRECRAWL_API_KEY" in result.stdout
    assert "not set" in result.stdout.lower()


def test_status_says_the_key_is_present_without_printing_it(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """The key must never appear in output (CLAUDE.md: no secrets in output)."""
    _status_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-key-123")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "fc-secret-key-123" not in result.stdout
    assert "set" in result.stdout.lower()


def test_status_counts_todays_api_calls(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    _status_env(monkeypatch, tmp_path)
    log = tmp_path / "api-calls.jsonl"
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    log.write_text(
        f'{{"ts": "{stamp}", "url": "https://a.example/x", "domain": "a.example",'
        f' "reason": "captcha", "outcome": "success"}}\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "1" in result.stdout


# --- logging (T6.2) -------------------------------------------------------


def test_status_names_the_log_file(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """T6.2: "log file path in status", so a support question can point at it."""
    _status_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert str(tmp_path / "briefing.log") in result.stdout


def test_research_help_lists_verbose() -> None:
    result = runner.invoke(app, _VERBOSE_HELP)
    assert "--verbose" in result.stdout


def test_run_help_lists_verbose() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert "--verbose" in result.stdout


def test_the_key_never_reaches_the_log_file_or_the_json(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """T6.2's acceptance: the key appears in neither sink, at any verbosity.

    The pipeline is faked but the config and the logger are the real ones, and
    `cli_module.LOG` is driven with the key to prove the filter *fires* rather
    than the key happening to be absent: an assertion of absence alone would
    also pass against a logger that redacts nothing.
    """
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-key-123")
    result = runner.invoke(app, ["research", "kenya news", "--verbose", "--json"])
    assert result.exit_code == 0, result.stderr
    log = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "fc-secret-key-123" not in log
    assert "fc-secret-key-123" not in result.stdout

    # A leak through this same logger must be masked, not merely avoided.
    cli_module.LOG.info("resolved key %s", "fc-secret-key-123")
    masked = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "fc-secret-key-123" not in masked
    assert "***" in masked


def test_a_verbose_run_logs_its_decisions(tmp_path: Path, monkeypatch: t.Any) -> None:
    """Without --verbose the log stays quiet about the query; with it, it says.

    Asserted on the query because that is the one thing a debug log exists to
    record: what was asked, and of which searcher.
    """
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    runner.invoke(app, ["research", "kenya economy news", "--verbose"])
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "kenya economy news" in text


def test_the_r_shorthand_also_writes_the_log_file(tmp_path: Path, monkeypatch: t.Any) -> None:
    """The headline path must not be the one that skips logging setup.

    The shorthand passes no flags, so this is what proves the install of the
    log handlers does not depend on `--verbose` being given.
    """
    _configure(_report(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["-r", "kenya economy news"])
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "briefing.log").exists()


# --- config (T3.4) ---------------------------------------------------------


def test_config_shows_effective_values_and_where_they_live(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """SPEC section 3: "Show effective config and where it lives"."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stderr
    assert str(tmp_path) in result.stdout
    lowered = result.stdout.lower()
    assert "results" in lowered
    assert "region" in lowered


def test_config_names_the_api_key_env_var_without_its_value(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret-key-123")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stderr
    assert "FIRECRAWL_API_KEY" in result.stdout
    assert "fc-secret-key-123" not in result.stdout


def test_config_honours_an_explicit_file(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """`--config` also applies to `briefing config`, not just `research`."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    source = tmp_path / "briefing.yaml"
    source.write_text("search:\n  results: 42\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "--config", str(source)])
    assert result.exit_code == 0, result.stderr
    assert "42" in result.stdout


# --- a broken config file is every command's problem (spec §3) --------------


def _broken_config(tmp_path: t.Any) -> t.Any:
    bad = tmp_path / "briefing.yaml"
    bad.write_text("news_topics: 5\n", encoding="utf-8")
    return bad


def test_a_broken_config_file_fails_config_with_a_message(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """`research` already reports this cleanly (SPEC §3); no other command may
    exit with a bare code and nothing said."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["config", "--config", str(_broken_config(tmp_path))])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert result.stderr.strip()  # not a bare exit code with nothing said
    assert "news_topics" in result.stderr
    assert "Fix" in result.stderr


def test_a_broken_config_file_fails_research_with_a_message(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """The guard has to live in one place, so this stays green through it."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(
        app, ["research", "kenya news", "--config", str(_broken_config(tmp_path))]
    )
    assert result.exit_code != 0
    assert "news_topics" in result.stderr
