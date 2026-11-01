"""Tests for cli.py (T3.3): the Typer CLI surface, run offline.

Every test drives `runner.invoke(app, ...)` with the pipeline substituted through
`monkeypatch`, so no search, fetch, or network happens here. What is under test is
the CLI's own job: parsing the flags SPEC section 3 names, wiring them into the
pipeline, and rendering the report as cards or as JSON.
"""

from __future__ import annotations

import dataclasses
import json
import typing as t

from typer.testing import CliRunner

import briefing.cli as cli_module
from briefing.cli import app
from briefing.fetch.router import ErrorClass, Method
from briefing.pipeline import Report, Result

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
