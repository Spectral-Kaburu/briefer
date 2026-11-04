"""Ask mode CLI tests (T9.6).

Tests for `briefing -a/--ask/--instant` and the `ask` subcommand.
All tests use fake-driven approaches: no real search, fetch, or model.
"""

from __future__ import annotations

import typing as t

from typer.testing import CliRunner

import briefing.cli as cli_module
from briefing.ask import Answer

runner = CliRunner()

# --- The -a/--ask/--instant shorthand on the group callback -----------------


def test_the_a_shorthand_is_documented() -> None:
    """Spec: -a should be in help text."""
    result = runner.invoke(cli_module.app, ["--help"])
    assert "-a" in result.stdout


def test_ask_shorthand_works(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The -a shorthand should invoke ask mode."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    # Create a fake ask function that returns an Answer
    from briefing import ask as ask_module

    fake_answer = Answer(
        question="What is Python?",
        text="Python is a programming language.",
        used_llm=True,
        sources=[
            (1, "Python 3.12", "https://python.org", "python.org", "LOCAL", "Content about Python")
        ],
        timings={"search": 0.5, "fetch": 0.3, "model": 0.2},
        warnings=[],
    )

    calls: list[str] = []

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        calls.append("asked")
        return fake_answer

    monkeypatch.setattr(ask_module, "ask", fake_ask)
    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["-a", "What is Python?"])
    assert result.exit_code == 0, result.stderr
    assert "Python is a programming language" in result.stdout
    assert "1" in result.stdout  # source number


def test_ask_subcommand_works(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The `briefing ask` subcommand should work."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="What is Python?",
        text="Python is a programming language.",
        used_llm=True,
        sources=[(1, "Python Guide", "https://python.org", "python.org", "LOCAL", "Content")],
        timings={"search": 0.5},
        warnings=[],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "What is Python?"])
    assert result.exit_code == 0, result.stderr
    assert "Python is a programming language" in result.stdout


def test_ask_shorthand_and_subcommand_are_mutually_exclusive(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """Passing both -a and ask subcommand should be an error."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    result = runner.invoke(cli_module.app, ["-a", "question", "ask"])
    # Typer may handle this as an error or ignore the extra arg
    assert (
        result.exit_code != 0
        or "ask" in result.stderr.lower()
        or "extra argument" in result.stderr.lower()
    )


# --- Ask subcommand flags ---------------------------------------------------


def test_ask_help_lists_all_flags() -> None:
    """The ask subcommand help should list all required flags."""
    result = runner.invoke(cli_module.app, ["ask", "--help"])
    assert result.exit_code == 0, result.stderr
    # Required flags from T9.6 spec
    assert "--pages" in result.stdout
    assert "--time" in result.stdout
    assert "--news" in result.stdout
    assert "--web" in result.stdout
    assert "--no-api" in result.stdout
    assert "--json" in result.stdout
    assert "--plain" in result.stdout


def test_ask_pages_flag_sets_max_pages(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --pages flag should set the max_pages parameter."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    received_params: dict[str, t.Any] = {}

    async def fake_ask(*args: t.Any, pages: int = 3, **kwargs: t.Any) -> Answer:
        received_params["pages"] = pages
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    runner.invoke(cli_module.app, ["ask", "test", "--pages", "5"])
    assert received_params.get("pages") == 5


def test_ask_time_flag_is_accepted(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --time flag should be accepted."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "test", "--time", "week"])
    assert result.exit_code == 0, result.stderr


def test_ask_news_flag_forces_news_mode(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --news flag should force news search mode."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    received_params: dict[str, t.Any] = {}

    async def fake_ask(*args: t.Any, news: bool = False, **kwargs: t.Any) -> Answer:
        received_params["news"] = news
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    runner.invoke(cli_module.app, ["ask", "test", "--news"])
    assert received_params.get("news") is True


def test_ask_web_flag_forces_web_mode(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --web flag should force web search mode."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    received_params: dict[str, t.Any] = {}

    async def fake_ask(*args: t.Any, web: bool = False, **kwargs: t.Any) -> Answer:
        received_params["web"] = web
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    runner.invoke(cli_module.app, ["ask", "test", "--web"])
    assert received_params.get("web") is True


def test_ask_no_api_flag_sets_no_api(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --no-api flag should be passed through."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    received_params: dict[str, t.Any] = {}

    async def fake_ask(*args: t.Any, no_api: bool = False, **kwargs: t.Any) -> Answer:
        received_params["no_api"] = no_api
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    runner.invoke(cli_module.app, ["ask", "test", "--no-api"])
    assert received_params.get("no_api") is True


def test_ask_json_flag_sets_json_output(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --json flag should be passed through."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "test", "--json"])
    assert result.exit_code == 0, result.stderr


def test_ask_plain_flag_sets_plain_output(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The --plain flag should be passed through."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    async def fake_ask(*args: t.Any, plain: bool = False, **kwargs: t.Any) -> Answer:
        return Answer(
            question="test",
            text="test",
            used_llm=False,
            sources=[],
            timings={},
            warnings=[],
        )

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "test", "--plain"])
    assert result.exit_code == 0, result.stderr
    # Plain mode avoids ANSI escape codes
    assert "\x1b" not in result.stdout


# --- JSON output format -----------------------------------------------------


def test_ask_json_output_format(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """JSON output should contain all required fields."""
    import json

    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="What is the current Python version?",
        text="Python 3.12 is the latest release.",
        used_llm=True,
        sources=[
            (
                1,
                "Python 3.12 Released",
                "https://python.org/3.12",
                "python.org",
                "LOCAL",
                "Python 3.12 brought many improvements.",
            )
        ],
        timings={"search": 0.3, "fetch": 0.5, "model": 0.8},
        warnings=[],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "What is the current Python version?", "--json"])
    assert result.exit_code == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["question"] == "What is the current Python version?"
    assert payload["answer"] == "Python 3.12 is the latest release."
    assert payload["used_llm"] is True
    assert len(payload["sources"]) == 1
    assert payload["sources"][0]["number"] == 1
    assert payload["sources"][0]["title"] == "Python 3.12 Released"
    assert payload["sources"][0]["url"] == "https://python.org/3.12"
    assert payload["sources"][0]["domain"] == "python.org"
    assert payload["sources"][0]["method"] == "LOCAL"
    assert "timings" in payload
    assert "warnings" in payload


# --- Answer rendering -------------------------------------------------------


def test_ask_rendered_output_shows_answer_and_sources(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """Rendered output should show the answer text and numbered sources."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="What is Python?",
        text="Python is a programming language.\n\n[1] Python Lang\npython.org",
        used_llm=True,
        sources=[(1, "Python Lang", "https://python.org", "python.org", "LOCAL", "Content here")],
        timings={"search": 0.3, "fetch": 0.2, "model": 0.1},
        warnings=[],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "What is Python?"])
    assert result.exit_code == 0, result.stderr
    assert "Python is a programming language" in result.stdout
    assert "[1]" in result.stdout
    assert "python.org" in result.stdout


def test_ask_no_model_warning(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """When no model is used, output should show the passages with a warning."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="What is Python?",
        text="Python is a programming language.\n\n[1] Python Lang\npython.org",
        used_llm=False,
        sources=[(1, "Python Lang", "https://python.org", "python.org", "LOCAL", "Content")],
        timings={"search": 0.3, "fetch": 0.2},
        warnings=["No Gemini API key found; returning passages only."],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "What is Python?"])
    assert result.exit_code == 0, result.stderr
    assert "warning" in result.stdout.lower() or "Gemini" in result.stdout


def test_ask_error_moving_and_a_question_is_clean(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """A missing query should produce a clean error, not a traceback."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    result = runner.invoke(cli_module.app, ["ask"])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert "QUERY" in result.stderr or "query" in result.stderr.lower()


# --- Footer output -----------------------------------------------------------


def test_ask_footer_shows_timings(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The footer should show search, fetch, and model timings."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="test",
        text="Answer text.",
        used_llm=True,
        sources=[],
        timings={"search": 0.1, "fetch": 0.3, "model": 0.2},
        warnings=[],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "test"])
    assert result.exit_code == 0, result.stderr
    # Should show timings breakdown
    assert "search" in result.stdout.lower() or "0.1s" in result.stdout


# --- Error handling -----------------------------------------------------------


def test_ask_with_unreadable_config(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """An unreadable config should produce a clean error."""
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("news_topics: 5\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    result = runner.invoke(cli_module.app, ["ask", "test", "--config", str(bad_config)])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert "news_topics" in result.stderr


def test_ask_help_includes_examples() -> None:
    """The ask subcommand help should include usage examples."""
    result = runner.invoke(cli_module.app, ["ask", "--help"])
    assert result.exit_code == 0, result.stderr
    # Should have examples
    assert "Examples:" in result.stdout or "example" in result.stdout.lower()


# --- Integration with existing flags -----------------------------------------


def test_ask_json_has_no_rich_decorations(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """JSON output should have no Rich decorations."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="test",
        text="Answer.",
        used_llm=True,
        sources=[],
        timings={"search": 0.1},
        warnings=[],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "test", "--json"])
    assert result.exit_code == 0, result.stderr
    assert "╭" not in result.stdout
    assert "│" not in result.stdout


def test_ask_plain_has_no_ansi(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """Plain mode should have no ANSI codes."""
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)

    fake_answer = Answer(
        question="test",
        text="Answer.",
        used_llm=True,
        sources=[],
        timings={"search": 0.1},
        warnings=[],
    )

    async def fake_ask(*args: t.Any, **kwargs: t.Any) -> Answer:
        return fake_answer

    monkeypatch.setattr(cli_module, "_ASK", fake_ask)

    result = runner.invoke(cli_module.app, ["ask", "test", "--plain"])
    assert result.exit_code == 0, result.stderr
    assert "\x1b" not in result.stdout
