"""Tests for cli.py (T3.3, T3.4): the Typer CLI surface, run offline.

Every test drives `runner.invoke(app, ...)` with the pipeline substituted through
`monkeypatch`, and the state dir pointed at a tmp dir, so no search, fetch, or
network happens here. What is under test is the CLI's own job: parsing the flags
SPEC section 3 names, wiring them into the pipeline, and rendering the report as
cards or as JSON.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import typing as t
from datetime import datetime
from pathlib import Path

import respx
from ddgs.exceptions import TimeoutException
from typer.testing import CliRunner

import briefing.cli as cli_module
import briefing.search as search_module
from briefing.cli import app
from briefing.fetch.router import Method
from briefing.inquiry import ASK, Answer, EvidenceSource, inquire
from briefing.search import SearchResult
from briefing.whitelist import Whitelist, WhitelistEntry

runner = CliRunner()

_VERBOSE_HELP = ["research", "--help"]


class _DeadDDGS:
    """ddgs that times out: the failure SPEC section 5 sends to the backup.

    A context manager because `Searcher._run` opens one per call.
    """

    def __enter__(self) -> t.Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def text(self, *a: t.Any, **k: t.Any) -> list[dict[str, t.Any]]:
        raise TimeoutException("ddgs timed out")

    def news(self, *a: t.Any, **k: t.Any) -> list[dict[str, t.Any]]:
        raise TimeoutException("ddgs timed out")


class FakeInquiry:
    """Stands in for the engine, recording how it was constructed and called.

    An awaitable callable rather than a plain function, because the seam is
    `await`ed. Class-level state so a test reads the calls back after `invoke`
    returns: this is the seam `_PIPELINE` used to be, and the wiring tests (does
    the timeout reach the engine, does `--results` reach the searcher) read what
    the CLI built rather than running a real search.
    """

    built: t.ClassVar[dict[str, t.Any]] = {}
    answer: t.ClassVar[Answer]
    #: Every `inquire(...)` call, so a test can assert the profile it was given.
    calls: t.ClassVar[list[dict[str, t.Any]]] = []

    @classmethod
    def make(cls) -> t.Callable[..., t.Awaitable[Answer]]:
        """A fresh async callable bound to this class's recording state."""

        async def _inquiry(**kwargs: t.Any) -> Answer:
            cls.calls.append(kwargs)
            return cls.answer

        return _inquiry


def _configure(answer: Answer, monkeypatch: t.Any) -> None:
    """Point the CLI's engine seam at a fake returning `answer`."""
    FakeInquiry.answer = answer
    FakeInquiry.calls = []
    monkeypatch.setattr(cli_module, "_INQUIRE", FakeInquiry.make())


def _inquiry_kwargs(**overrides: t.Any) -> dict[str, t.Any]:
    """The arguments the CLI hands the engine, with the display seams dropped.

    A research/ask run passes its UI callbacks; a test that only cares about
    configuration compares the rest, so the callbacks are compared separately.
    """
    call = FakeInquiry.calls[-1]
    return {
        k: v for k, v in call.items() if k not in ("on_progress", "on_source", "on_partial")
    } | overrides


def _source() -> EvidenceSource:
    return EvidenceSource(
        number=1,
        source="Bank raises rate",
        domain="nation.africa",
        title="Bank raises rate",
        url="https://nation.africa/bank-raises-rate",
        method=Method.LOCAL,
        text="A two sentence summary.",
        snippet="The central bank raised its lending rate.",
        heading="Bank raises rate",
        age="2h ago",
        ok=True,
    )


def _answer(**overrides: t.Any) -> Answer:
    base: dict[str, t.Any] = {
        "question": "bank rate",
        "text": "The bank held its rate steady [1].",
        "used_llm": True,
        "sources": [_source()],
        "timings": {"search": 0.8, "fetch": 3.1, "model": 2.5},
        "warnings": [],
        "notices": [],
        "found": 1,
        "whitelisted": [("example.co.ke", "captcha")],
        "profile": "research",
    }
    base.update(overrides)
    return Answer(**base)


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

#: What the fake model answers, for the tests that assert on rendered output.
ANSWER = "The bank held its rate steady."


def test_research_runs_the_engine_with_the_research_profile(monkeypatch: t.Any) -> None:
    """The two modes are one engine differing by profile (the owner's requirement).

    `--llm` and `--synth` remain accepted for compatibility; the answer always
    comes from the engine's RESEARCH profile now, not a second summarizer pass.
    """
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "bank rate", "--llm"])
    assert result.exit_code == 0, result.stdout
    assert FakeInquiry.calls[0]["profile"].name == "research"


def test_ask_runs_the_engine_with_the_ask_profile(monkeypatch: t.Any) -> None:
    _configure(_answer(profile="ask"), monkeypatch)
    result = runner.invoke(app, ["ask", "bank rate"])
    assert result.exit_code == 0, result.stdout
    assert FakeInquiry.calls[0]["profile"].name == "ask"


def test_a_model_answer_is_printed_after_the_cards(monkeypatch: t.Any) -> None:
    """One answer, over the numbered source cards."""
    _configure(_answer(text=ANSWER), monkeypatch)
    result = runner.invoke(app, ["research", "bank rate"])
    assert ANSWER in result.stdout


def test_a_model_with_no_answer_leaves_the_cards_as_the_answer(
    monkeypatch: t.Any,
) -> None:
    """A model that cannot answer leaves the cards as the answer, unadorned."""
    _configure(_answer(text="", used_llm=False), monkeypatch)
    result = runner.invoke(app, ["research", "bank rate"])
    assert "A two sentence summary." in result.stdout


def test_json_carries_the_answer(monkeypatch: t.Any) -> None:
    """A `--json` consumer gets the answer as a field, not as text to scrape."""
    _configure(_answer(text=ANSWER), monkeypatch)
    result = runner.invoke(app, ["research", "bank rate", "--json"])
    assert json.loads(result.stdout)["answer"] == ANSWER


def test_json_carries_the_answer_even_without_a_model(monkeypatch: t.Any) -> None:
    """The answer field is always present now: the engine's output, not a flag's."""
    _configure(_answer(text="", used_llm=False), monkeypatch)
    result = runner.invoke(app, ["research", "bank rate", "--json"])
    assert json.loads(result.stdout)["answer"] == ""


def test_llm_flags_still_warn_when_no_key_is_missing(monkeypatch: t.Any) -> None:
    """T9.4: a missing key is a warning, not a crash — and the flags still work.

    `--llm` and `--synth` are deprecated no-ops now (the engine answers whenever
    a key exists), but a run that asks for LLM output and has no key must say so
    rather than silently degrading.
    """
    _configure(_answer(), monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(app, ["research", "bank rate", "--llm"])
    assert result.exit_code == 0, result.stderr
    assert "No LLM API key found" in result.stderr
    assert "OPENAI_API_KEY or GEMINI_API_KEY" in result.stderr
    # Still produces output (the cards answer, as they do without a model)
    assert "1 found" in result.stdout


def test_synth_still_warns_when_no_key_is_missing(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(app, ["research", "bank rate", "--synth"])
    assert result.exit_code == 0, result.stderr
    assert "No LLM API key found" in result.stderr
    assert "OPENAI_API_KEY or GEMINI_API_KEY" in result.stderr
    assert "1 found" in result.stdout


# --- LLM provider selection (preferred_provider) ---------------------------


def test_selected_llm_prefers_openai_when_both_keys_present(
    monkeypatch: t.Any,
) -> None:
    """When both keys are present and preferred_provider is empty, OpenAI is selected."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)

    from briefing.cli import _selected_llm
    from briefing.config import load_config

    loaded = load_config()
    provider, name = _selected_llm(loaded)
    assert name == "openai"
    assert provider is not None
    assert name == "openai"


def test_selected_llm_respects_gemini_when_preferred_configured(
    monkeypatch: t.Any,
    tmp_path: t.Any,
) -> None:
    """When preferred_provider is 'gemini', Gemini is selected even with both keys."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")

    path = tmp_path / "briefing.yaml"
    path.write_text("briefing:\n  preferred_provider: gemini\n", encoding="utf-8")

    from briefing.cli import _selected_llm
    from briefing.config import load_config

    loaded = load_config(path)
    provider, name = _selected_llm(loaded)
    assert name == "gemini"
    assert provider is not None


def test_selected_llm_uses_openai_when_only_openai_key_available(
    monkeypatch: t.Any,
) -> None:
    """When only OpenAI key is present, OpenAI is selected."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)

    from briefing.cli import _selected_llm
    from briefing.config import load_config

    loaded = load_config()
    provider, name = _selected_llm(loaded)
    assert name == "openai"
    assert provider is not None


def test_selected_llm_uses_gemini_when_only_gemini_key_available(
    monkeypatch: t.Any,
) -> None:
    """When only Gemini key is present, Gemini is selected."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)

    from briefing.cli import _selected_llm
    from briefing.config import load_config

    loaded = load_config()
    provider, name = _selected_llm(loaded)
    assert name == "gemini"
    assert provider is not None


def test_selected_llm_returns_none_when_no_keys_available(
    monkeypatch: t.Any,
) -> None:
    """When no keys are present, (None, None) is returned."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)

    from briefing.cli import _selected_llm
    from briefing.config import load_config

    loaded = load_config()
    provider, name = _selected_llm(loaded)
    assert provider is None
    assert name is None


# --- the -r shorthand ----------------------------------------------------


def test_a_bare_query_runs_the_research_command(monkeypatch: t.Any) -> None:
    """SPEC section 3: `briefing -r "<query>"` is shorthand for `research`."""
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["-r", "latest news in kenya"])
    assert result.exit_code == 0, result.stdout
    assert cli_module._LAST_BUILT["query"] == "latest news in kenya"


def test_the_shorthand_renders_the_result_cards(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
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
    _configure(_answer(), monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = runner.invoke(app, ["-r", "latest news in kenya"])
    assert "🟢" in result.stdout
    assert "LOCAL" in result.stdout


# --- flags reach the pipeline ---------------------------------------------


def test_the_results_flag_sets_the_search_width(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--results", "25"])
    assert result.exit_code == 0, result.stdout
    searcher = cli_module._LAST_BUILT["searcher"]
    assert searcher.max_results == 25


def test_a_news_query_uses_the_news_searcher(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    runner.invoke(app, ["research", "latest kenya news", "--news"])
    assert cli_module._LAST_BUILT["searcher"].region
    # The query's own news words are not what makes this a news run — the flag is,
    # so the searcher's mode must say so rather than being re-derived per query.
    assert cli_module._LAST_BUILT["searcher"].mode == "news"


def test_news_flag_forces_news_on_a_plain_query(monkeypatch: t.Any) -> None:
    """SPEC section 2: --news forces news search even with no news word in the query.

    `Searcher.search` picks the endpoint from the query, so a flag that only
    changed the header word would leave `ddgs.text` running. Asserting on the
    searcher's own mode is what makes that impossible.
    """
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya economy", "--news"])
    assert result.exit_code == 0, result.stderr
    assert cli_module._LAST_BUILT["searcher"].mode == "news"


def test_web_flag_forces_web_on_a_news_query(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "latest breaking news", "--web"])
    assert result.exit_code == 0, result.stderr
    assert cli_module._LAST_BUILT["searcher"].mode == "text"


def test_no_flag_leaves_the_searcher_to_auto_detect(monkeypatch: t.Any) -> None:
    """The default is SPEC section 2's auto-detect, which is `mode=None`."""
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "latest breaking news"])
    assert result.exit_code == 0, result.stderr
    assert cli_module._LAST_BUILT["searcher"].mode is None


def test_no_api_disables_the_api_for_the_run(monkeypatch: t.Any) -> None:
    """SPEC section 4's `--no-api`: no API calls, and nothing whitelisted."""
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--no-api"])
    assert result.exit_code == 0, result.stdout
    assert cli_module._LAST_BUILT["router"].no_api is True


def test_the_time_flag_is_accepted(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    for value in ("day", "week", "month", "any"):
        result = runner.invoke(app, ["research", "kenya news", "--time", value])
        assert result.exit_code == 0, (value, result.stdout)


def test_news_and_web_are_mutually_exclusive(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--news", "--web"])
    assert result.exit_code != 0
    assert "web" in result.stderr or "news" in result.stderr
    assert "Traceback" not in result.stderr


# --- json ----------------------------------------------------------------


def test_json_output_is_valid_json(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
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
    _configure(_answer(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    config = _timing_config(tmp_path, "timeouts: {per_url_s: 7}")
    result = runner.invoke(app, ["research", "kenya news", "--config", str(config)])
    assert result.exit_code == 0, result.stdout
    assert cli_module._LAST_BUILT["per_url_s"] == 7


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
    _configure(_answer(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    config = _timing_config(tmp_path, "firecrawl:\n  local:\n    concurrency: 2")
    result = runner.invoke(app, ["research", "kenya news", "--config", str(config)])
    assert result.exit_code == 0, result.stdout
    assert cli_module._LAST_BUILT["concurrency"] == 2


def test_json_output_carries_no_rich_decorations(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--json"])
    assert "╭" not in result.stdout
    assert "Query:" not in result.stdout
    assert "🟢" not in result.stdout


def test_json_output_says_how_each_page_was_fetched(monkeypatch: t.Any) -> None:
    """SPEC section 3: a JSON consumer needs the method badge too."""
    second = dataclasses.replace(_source(), number=2, domain="example.co.ke", method=Method.API)
    _configure(_answer(sources=[_source(), second]), monkeypatch)
    payload = json.loads(runner.invoke(app, ["research", "kenya news", "--json"]).stdout)
    assert [r["method"] for r in payload["results"]] == ["local", "api"]


def test_json_output_carries_the_reason_a_page_degraded(monkeypatch: t.Any) -> None:
    """SPEC section 4: an Aether consumer needs the same reason the terminal gets."""
    second = dataclasses.replace(
        _source(),
        number=2,
        domain="example.co.ke",
        method=Method.SNIPPET,
        ok=False,
        warning="page not found (HTTP 404)",
    )
    _configure(_answer(sources=[_source(), second]), monkeypatch)
    payload = json.loads(runner.invoke(app, ["research", "kenya news", "--json"]).stdout)
    assert payload["results"][1]["warning"] == "page not found (HTTP 404)"


# --- plain ---------------------------------------------------------------


def test_plain_output_has_no_emoji_and_no_ansi(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--plain"])
    assert result.exit_code == 0, result.stdout
    assert "LOCAL" in result.stdout
    assert "🟢" not in result.stdout


# --- errors --------------------------------------------------------------


def test_an_unknown_flag_is_a_clean_error(monkeypatch: t.Any) -> None:
    _configure(_answer(), monkeypatch)
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
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--time", "fortnight"])
    assert result.exit_code != 0
    assert "fortnight" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_results_value_below_one_is_a_clean_error(monkeypatch: t.Any) -> None:
    """SPEC section 3's "plain English plus a fix hint" covers bad values too."""
    _configure(_answer(), monkeypatch)
    result = runner.invoke(app, ["research", "kenya news", "--results", "0"])
    assert result.exit_code != 0
    assert "0" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_named_config_file_that_is_absent_fails(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """T8.3: a typo in `--config` must fail, not silently run on defaults.

    This used to assert the opposite (exit 0). T8.3 reversed it on purpose: an
    explicitly named file that does not exist is a `ConfigError`, so a typo
    cannot quietly change which config the owner is running under.
    """
    _configure(_answer(), monkeypatch)
    missing = tmp_path / "no-such.yaml"
    result = runner.invoke(app, ["research", "--config", str(missing), "kenya news"])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert "no-such.yaml" in result.stderr


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


class _UnreachableFirecrawl:
    """`_local_firecrawl`'s stand-in: reports the local instance as down.

    `status` used to reach the real socket here, so these six tests only ever
    passed on a machine with Docker up (T8.1). The URL still comes from config
    either way, so the "names the local URL" assertion stays a real assertion.
    """

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        pass

    async def health(self) -> bool:
        return False


def _status_env(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """An isolated state dir, with the network probes stubbed out."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_ddgs_reachable", lambda: True)
    monkeypatch.setattr(cli_module, "_local_firecrawl", _UnreachableFirecrawl)


def test_status_reports_every_check_spec_section_3_names(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    _status_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    lowered = result.stdout.lower()
    assert "local firecrawl" in lowered
    assert "firecrawl key" in lowered
    assert "gemini key" in lowered
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


def test_status_says_gemini_key_is_missing_and_names_the_env_var(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """T9.4: status reports Gemini key status."""
    _status_env(monkeypatch, tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "GEMINI_API_KEY" in result.stdout
    assert "not set" in result.stdout.lower()


def test_status_says_gemini_key_is_present_without_printing_it(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """T9.4: the Gemini key must never appear in output."""
    _status_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-key-456")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "gemini-secret-key-456" not in result.stdout
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
    _configure(_answer(), monkeypatch)
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


def test_the_gemini_key_never_reaches_the_log_file_or_the_json(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """T9.4: the Gemini key must be masked just like the Firecrawl key."""
    _configure(_answer(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-key-456")
    result = runner.invoke(app, ["research", "kenya news", "--llm", "--verbose", "--json"])
    assert result.exit_code == 0, result.stderr
    log = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "gemini-secret-key-456" not in log
    assert "gemini-secret-key-456" not in result.stdout

    # A leak through this same logger must be masked.
    cli_module.LOG.info("resolved gemini key %s", "gemini-secret-key-456")
    masked = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "gemini-secret-key-456" not in masked
    assert "***" in masked


def test_a_verbose_run_logs_its_decisions(tmp_path: Path, monkeypatch: t.Any) -> None:
    """Without --verbose the log stays quiet about the query; with it, it says.

    Asserted on the query because that is the one thing a debug log exists to
    record: what was asked, and of which searcher.
    """
    _configure(_answer(), monkeypatch)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    runner.invoke(app, ["research", "kenya economy news", "--verbose"])
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "kenya economy news" in text


def test_the_r_shorthand_also_writes_the_log_file(tmp_path: Path, monkeypatch: t.Any) -> None:
    """The headline path must not be the one that skips logging setup.

    The shorthand passes no flags, so this is what proves the install of the
    log handlers does not depend on `--verbose` being given.
    """
    _configure(_answer(), monkeypatch)
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


def test_config_says_which_file_is_in_force(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """T8.3: `briefing config` must name the file, or say there is none."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stderr
    assert "none (built-in defaults)" in result.stdout


def test_config_names_a_discovered_file(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """A discovered file is in force, and `briefing config` must show it."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    source = tmp_path / "briefing" / "config.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("search:\n  results: 42\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stderr
    assert str(source) in result.stdout
    assert "42" in result.stdout


def test_config_says_how_to_create_one(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """Saying "none (built-in defaults)" is only actionable if it says what to do."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)
    result = runner.invoke(app, ["config"])
    assert "briefing.yaml.example" in result.stdout


def test_a_broken_discovered_file_fails_cleanly(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """A file nobody typed on the command line must still fail loudly, and must
    name the file so the owner knows which one to fix."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    source = tmp_path / "briefing" / "config.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("news_topics: 5\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)
    result = runner.invoke(app, ["config"])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    # The path is wrapped mid-string by the error renderer, so join it before
    # asserting: a split path would otherwise not match.
    assert "briefing/config.yaml" in result.stderr.replace("\n", "")
    assert "news_topics" in result.stderr


def test_an_explicit_missing_path_is_not_silently_defaults(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """The other half of T8.3: a typo in `--config` must not run on defaults."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["config", "--config", str(tmp_path / "gone.yaml")])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert "gone.yaml" in result.stderr


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


# --- the backup search wiring (T8.2) ---------------------------------------


@respx.mock
def test_a_timed_out_ddgs_really_reaches_the_backup(monkeypatch: t.Any, tmp_path: Path) -> None:
    """The wiring itself, not the adapter.

    Every search test above injects a fake backup, so nothing proved that
    `_searcher` passes a *real* one into the real `Searcher`: the T2.2 gap was
    exactly that, and a broad `except` hid it as "ddgs found nothing". This one
    fails only ddgs, lets the real factories run, and asserts the real adapter
    was reached over the wire.
    """
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    # Only ddgs is faked. `_searcher`, the real `Searcher` and the real
    # `FirecrawlSearch` all stay — that is the whole point of the test.
    monkeypatch.setattr(search_module, "DDGS", _DeadDDGS)

    backup_route = respx.post("http://localhost:3002/v2/search").respond(
        200,
        json={
            "success": True,
            "data": {
                "web": [
                    {
                        "url": "https://nation.africa/bank-raises-rate",
                        "title": "Bank raises rate",
                        "description": "The central bank acted.",
                    }
                ]
            },
        },
    )
    # The router's fetch of the one hit the backup returns.
    respx.post("http://localhost:3002/v2/scrape").respond(
        200, json={"success": True, "data": {"markdown": "Body.", "metadata": {"title": "T"}}}
    )

    result = runner.invoke(app, ["research", "latest flooding in kenya", "--results", "1"])

    assert result.exit_code == 0, result.stderr
    assert backup_route.called, "the backup was never reached — the wiring is broken"
    assert "Bank raises rate" in result.stdout


# --- ported from tests/test_pipeline.py (Stream C) -------------------------
# The pipeline is gone, but three of its behaviours had no equivalent in the
# engine's suite, so they are re-pinned here against the engine: the whitelist
# is read before the fetches start, progress stages arrive in order, and a
# display that throws costs only the display.


_ASK_HITS = [SearchResult("T", "https://old.example/a", "s", "old.example")]


def _engine_answer(**engine_overrides: t.Any) -> Answer:
    """One ask-profile run of the real engine against fakes, no network."""

    async def fetch(_hit: t.Any) -> tuple[str, str, str]:
        return "T", "sum", "# T\n\nbody"

    async def search(_q: str) -> list[SearchResult]:
        return list(_ASK_HITS)

    def extract(md: str, title: str, _url: str, _st: str, **_kw: t.Any) -> tuple[str, str]:
        return title, "sum"

    kwargs: dict[str, t.Any] = {
        "query": "q",
        "search": search,
        "fetch": fetch,
        "extract": extract,
        "model": None,
        "pages": 1,
        "per_url_s": 2.0,
        "deadline_s": 0.0,
    }
    kwargs.update(engine_overrides)
    return asyncio.run(inquire(profile=ASK, **kwargs))


def test_the_whitelist_is_read_before_the_fetches_start() -> None:
    """A domain whitelisted by an earlier run must not be reported as new.

    The snapshot is taken before any fetch, so a barrier page the router adds
    mid-run still counts as an addition, while one already there does not.
    """
    store = Whitelist(Path("/tmp") / "never-whitelist.yaml")

    async def _no_add(*_a: t.Any, **_k: t.Any) -> None:
        """The store records nothing: this run adds no domain."""

    store.add_async = _no_add  # type: ignore[method-assign]
    store.list = lambda: [WhitelistEntry("old.example", "captcha", "auto")]  # type: ignore[method-assign]
    answer = _engine_answer(whitelist=store)
    assert answer.whitelisted == []


def test_progress_stages_arrive_in_order() -> None:
    """A live display depends on the order, not just the presence, of stages."""
    seen: list[str] = []
    answer = _engine_answer(on_progress=lambda v: seen.append(v[0]))
    assert seen[0] == "search"
    assert "searched" in seen
    assert seen.index("fetch") < seen.index("fetched")
    assert answer.sources


def test_a_raising_display_callback_does_not_stop_the_run() -> None:
    """A display that throws must cost the display, not the answer."""

    def _boom(*_a: t.Any, **_k: t.Any) -> None:
        raise RuntimeError("display exploded")

    answer = _engine_answer(on_progress=_boom, on_source=_boom)
    assert answer.sources
