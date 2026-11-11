"""Tests for synth.py: the briefing synthesis and its fallback (T5.3), and the
research summaries and answer (T4.1).

SPEC section 6: a Gemini prompt turns the name, yesterday's session summary and
today's headlines into 2-3 spoken sentences. The network is never touched here:
`Speaker` depends on the `generate` method, not on the `Gemini` class, so the
tests hand it any object with `async generate(prompt)` and never construct a key
(CLAUDE.md: never call the real Gemini in tests). `ResearchSynth` is handed the
same way.

The fallback matters as much as the synthesis: SPEC section 6 says a briefing
whose fetching failed entirely gets a plain greeting rather than silence, and a
model that raises, returns nothing, or returns only whitespace must all degrade
the same way. SPEC section 2's `--llm` degrades to the extractive summary that is
already on the result, for the same reason.
"""

from __future__ import annotations

import logging
import typing as t
from unittest.mock import AsyncMock, MagicMock

import pytest

from briefing.synth import NO_NAME_GREETING, ModelResult, ResearchSynth, Speaker

# Every test here awaits, so the marker is module-wide rather than 13 copies.
pytestmark = pytest.mark.asyncio

HEADLINES = ["Bank raises the lending rate", "Flooding displaces families in Nairobi"]
SESSION = "Yesterday we deployed the briefing service."
#: The fallback greeting with nobody configured (T8.5). It used to be "Good
#: morning, Aether." — the assistant greeting itself, which is the T5.4 incident.
GREETING = "Good morning."


class FakeClient:
    """The one method `Gemini` calls, canned either way."""

    def __init__(
        self,
        text: str | None = "Good morning. Here is your briefing.",
        raises: Exception | None = None,
        model: str = "gemini-2.5-flash",
    ) -> None:
        self.text = text
        self.raises = raises
        self.prompts: list[str] = []
        # What the real client carries, so a failure's log line can name the
        # model that produced (or failed to produce) the answer.
        self.model = model

    async def generate(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        if self.raises is not None:
            raise self.raises
        return self.text


def speaker(client: t.Any, **kwargs: t.Any) -> Speaker:
    """A Speaker named Aether, over the given fake Gemini.

    `user_name` is deliberately left out of the defaults: T8.5 made it the only
    thing that puts a name in the greeting, so a test that wants a named greeting
    must ask for one. The fake is passed straight to `Speaker`: it depends on
    `generate`, not on the `Gemini` class, so no wrapper stands between the test
    and the behaviour.
    """
    base = {"name": "Aether", "session_summary": SESSION}
    base.update(kwargs)
    return Speaker(client, **base)


# --- the prompt (SPEC section 6) ------------------------------------------


async def test_the_prompt_carries_the_name_the_session_summary_and_the_headlines() -> None:
    """SPEC section 6's three inputs. Miss one and the briefing loses its thread."""
    client = FakeClient()
    await speaker(client).briefing(HEADLINES)
    prompt = client.prompts[0]
    assert "Aether" in prompt
    assert SESSION in prompt
    for headline in HEADLINES:
        assert headline in prompt


async def test_the_prompt_asks_for_spoken_plain_text_not_prose() -> None:
    """A briefing is read aloud: no markdown, no bullets, no lists.

    Without the constraint a model happily returns formatted text, which
    `voice-tts` then spells out.
    """
    client = FakeClient()
    await speaker(client).briefing(HEADLINES)
    prompt = client.prompts[0].lower()
    assert "spoken" in prompt
    assert "plain text" in prompt
    assert "no markdown" in prompt
    assert "bullet" in prompt


async def test_the_prompt_asks_for_2_to_3_sentences() -> None:
    """SPEC section 6: "2-3 spoken sentences, under ~45s"."""
    client = FakeClient()
    await speaker(client).briefing(HEADLINES)
    assert "2-3" in client.prompts[0]


async def test_headlines_reach_the_prompt_in_order() -> None:
    client = FakeClient()
    await speaker(client).briefing(HEADLINES)
    prompt = client.prompts[0]
    assert prompt.index(HEADLINES[0]) < prompt.index(HEADLINES[1])


async def test_the_prompt_is_loaded_from_the_file_not_inlined() -> None:
    """`prompts/briefing.txt` is SPEC section 6's location: the owner can reword
    the briefing without a code change, and it is read per call so an edit takes
    effect on the next run rather than needing a reinstall.
    """
    from briefing import synth

    original = synth.PROMPT_PATH.read_text(encoding="utf-8")
    synth.PROMPT_PATH.write_text("Say hello to {name}.", encoding="utf-8")
    try:
        client = FakeClient()
        await speaker(client).briefing(HEADLINES)
        assert client.prompts[0] == "Say hello to Aether."
    finally:
        synth.PROMPT_PATH.write_text(original, encoding="utf-8")


# --- synthesis -------------------------------------------------------------


async def test_the_models_text_is_the_briefing() -> None:
    client = FakeClient("Good morning. Here are the headlines.")
    briefing = await speaker(client).briefing(HEADLINES)
    assert briefing.text == "Good morning. Here are the headlines."
    assert briefing.fallback is False


async def test_the_models_text_is_trimmed() -> None:
    client = FakeClient("  a briefing  \n")
    briefing = await speaker(client).briefing(HEADLINES)
    assert briefing.text == "a briefing"


# --- the greeting's name (T8.5) -------------------------------------------


async def test_the_greeting_names_nobody_by_default() -> None:
    """The T5.4 regression test, and the whole point of T8.5.

    `briefing.name` defaults to "Aether", and it used to fill the greeting too,
    so a briefing that could not be synthesised greeted itself: "Good morning,
    Aether." Aether is the assistant; the greeting must name nobody unless the
    owner says who it addresses.
    """
    client = FakeClient(raises=RuntimeError("down"))
    briefing = await speaker(client).briefing(HEADLINES)
    assert briefing.text == GREETING
    assert "Aether" not in briefing.text


async def test_the_greeting_names_the_person_when_configured() -> None:
    """`user_name` is the one thing that puts a name in the greeting."""
    client = FakeClient(raises=RuntimeError("down"))
    briefing = await speaker(client, user_name="Sam").briefing(HEADLINES)
    assert briefing.text == "Good morning, Sam."


async def test_a_whitespace_user_name_is_no_name() -> None:
    """`user_name: "  "` is unset, not a name that is two spaces long.

    Saying "Good morning,  ." reads as a comma and a pause, which is worse than
    not attempting the name at all.
    """
    client = FakeClient(raises=RuntimeError("down"))
    briefing = await speaker(client, user_name="   ").briefing(HEADLINES)
    assert briefing.text == GREETING


async def test_the_prompt_names_the_person_only_when_configured() -> None:
    """The prompt's name slot is the person's, and it is empty otherwise.

    The template asks the model to address the person by name; given no name it
    would invent one, so the instruction must go too, not just the value.
    """
    client = FakeClient()
    await speaker(client, user_name="Sam").briefing(HEADLINES)
    named = client.prompts[0]
    assert "Sam" in named
    # On its own line, not appended to the last headline: the template joins the
    # two, and a leading space read "...families in Nairobi The person you are
    # speaking to is called Sam" — the headline had swallowed the instruction.
    assert "\nThe person you are speaking to is called Sam" in named

    client = FakeClient()
    await speaker(client).briefing(HEADLINES)
    unnamed = client.prompts[0]
    assert "Sam" not in unnamed
    assert "by name" not in unnamed


async def test_the_assistant_name_is_still_the_prompts_own_name() -> None:
    """Separating the two must not cost the prompt its speaker.

    `briefing.name` is who the briefing is *from*; `user_name` is who it is
    *to*. Only the latter changed here.
    """
    client = FakeClient()
    await speaker(client, user_name="Sam").briefing(HEADLINES)
    prompt = client.prompts[0]
    assert "Aether" in prompt
    assert "Sam" in prompt
    assert prompt.index("Aether") < prompt.index("Sam")


async def test_a_client_that_raises_falls_back_to_the_greeting() -> None:
    client = FakeClient(raises=RuntimeError("quota exhausted"))
    briefing = await speaker(client).briefing(HEADLINES)
    assert briefing.text == GREETING
    assert briefing.fallback is True


async def test_empty_headlines_fall_back_without_calling_the_client() -> None:
    """Nothing was fetched, so there is nothing to summarise.

    SPEC section 6: "if all fetching fails, plain session greeting". Calling the
    model with an empty list would invite it to invent news.
    """
    client = FakeClient()
    briefing = await speaker(client).briefing([])
    assert briefing.text == GREETING
    assert briefing.fallback is True
    assert client.prompts == []


async def test_a_client_that_returns_nothing_falls_back() -> None:
    """A blocked candidate must not produce an empty briefing.

    The SDK returns `response.text is None` for a blocked prompt rather than
    raising (NOTES-APIS.md section 6), so this is the ordinary blocked path.
    """
    client = FakeClient(text=None)
    briefing = await speaker(client).briefing(HEADLINES)
    assert briefing.text == GREETING
    assert briefing.fallback is True


async def test_a_client_that_returns_only_whitespace_falls_back() -> None:
    client = FakeClient(text="   \n ")
    briefing = await speaker(client).briefing(HEADLINES)
    assert briefing.text == GREETING
    assert briefing.fallback is True


async def test_a_briefing_with_no_name_still_speaks() -> None:
    """An unconfigured name is not a reason to say nothing at all.

    This used to assert `PLAIN_GREETING.format(name="")`, i.e. the degenerate
    "Good morning, ." — a spoken comma and a pause. T8.5 removed that case
    outright: no name means no name clause.
    """
    client = FakeClient()
    briefing = await speaker(client, name="").briefing(HEADLINES)
    assert briefing.text == "Good morning. Here is your briefing."

    empty = await speaker(client, name="").briefing([])
    assert empty.text == NO_NAME_GREETING


async def test_a_fallback_is_still_a_speakable_briefing() -> None:
    client = FakeClient(raises=RuntimeError("down"))
    briefing = await speaker(client).briefing(HEADLINES)
    assert isinstance(briefing.text, str)
    assert briefing.text.strip()


async def test_a_missing_prompt_file_falls_back_without_calling_the_client() -> None:
    """No prompt file means no synthesis, and never a call with an empty prompt.

    A repo that lost `prompts/briefing.txt` says the greeting rather than asking
    a model to improvise a briefing from nothing.
    """
    from briefing import synth

    original = synth.PROMPT_PATH.read_text(encoding="utf-8")
    synth.PROMPT_PATH.unlink()
    try:
        client = FakeClient()
        briefing = await speaker(client).briefing(HEADLINES)
        assert briefing.text == GREETING
        assert briefing.fallback is True
        assert client.prompts == []
    finally:
        synth.PROMPT_PATH.write_text(original, encoding="utf-8")


# --- research mode (T4.1; SPEC section 2) ------------------------------------

SOURCES = [
    ("Bank holds the rate", "The central bank left its benchmark rate at 12 percent."),
    ("Flooding displaces families", "Families in Nairobi moved to higher ground."),
]
EXTRACTIVE = "The bank left its rate at 12 percent."


async def test_llm_replaces_the_picked_sentences_with_the_models_summary() -> None:
    """SPEC section 2's `--llm`: an abstractive summary, not a re-picked extractive one."""
    client = FakeClient("The central bank held its benchmark rate steady at 12 percent.")
    summary = await ResearchSynth(client).summary(
        heading="Bank holds the rate", text="The bank left its rate.", fallback=EXTRACTIVE
    )
    assert summary == "The central bank held its benchmark rate steady at 12 percent."


@pytest.mark.parametrize(
    ("text", "raises"),
    [
        (None, None),
        ("", None),
        ("   \n\t ", None),
        ("never reached", RuntimeError("quota")),
    ],
)
async def test_any_model_failure_keeps_the_extractive_summary(
    text: str | None, raises: Exception | None
) -> None:
    """SPEC section 2: `--llm` is an opt-in, so a model that cannot answer must leave
    the summary the page already had rather than ending the run."""
    client = FakeClient(text, raises=raises)
    summary = await ResearchSynth(client).summary(heading="H", text="Body.", fallback=EXTRACTIVE)
    assert summary == EXTRACTIVE


async def test_the_summary_prompt_carries_the_heading_and_the_page_text() -> None:
    """What an abstractive summary is written from: the heading, and the page text."""
    client = FakeClient()
    await ResearchSynth(client).summary(
        heading="Bank holds the rate", text="Body sentences here.", fallback=EXTRACTIVE
    )
    assert "Bank holds the rate" in client.prompts[0]
    assert "Body sentences here." in client.prompts[0]


async def test_the_answer_prompt_numbers_every_source() -> None:
    """SPEC section 2: the answer cites source numbers, so the prompt has to number them."""
    client = FakeClient()
    await ResearchSynth(client).answer(query="What did the bank do?", sources=SOURCES)
    prompt = client.prompts[0]
    assert "What did the bank do?" in prompt
    for position, (heading, _) in enumerate(SOURCES, start=1):
        assert f"{position}. {heading}" in prompt


async def test_no_answer_is_returned_when_the_model_fails() -> None:
    """`--synth` is additive: nothing to show means the numbered cards are the answer."""
    client = FakeClient(raises=RuntimeError("down"))
    assert await ResearchSynth(client).answer(query="q", sources=SOURCES) is None


async def test_nothing_is_summarised_when_there_are_no_sources() -> None:
    """Nothing fetched means nothing to synthesise, and never a call with nothing."""
    client = FakeClient()
    assert await ResearchSynth(client).answer(query="q", sources=[]) is None
    assert client.prompts == []


# --- OpenAI provider (T13) -----------------------------------------------------


async def test_openai_generate_returns_model_result() -> None:
    """OpenAI client should return ModelResult with text and metadata."""
    from briefing.synth import OpenAI

    openai = OpenAI(model="gpt-4o-mini", api_key_env="OPENAI_API_KEY")

    # Mock the AsyncOpenAI client and its methods
    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].message.content = "Test response"
    mock_completion.choices[0].finish_reason = "stop"
    mock_completion.usage = MagicMock()
    mock_completion.usage.completion_tokens = 50
    mock_completion.usage.total_tokens = 100

    mock_chat = MagicMock()
    mock_chat.completions = MagicMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)

    mock_client = MagicMock()
    mock_client.chat = mock_chat

    openai._client = mock_client

    result = await openai.generate("Hello?")
    assert result.text == "Test response"
    assert result.finish_reason == "stop"
    assert result.usage is not None


async def test_openai_generate_handles_exceptions() -> None:
    """OpenAI client should handle API exceptions gracefully."""
    from briefing.synth import OpenAI

    openai = OpenAI()

    # Mock the AsyncOpenAI client to raise an exception
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = MagicMock(side_effect=RuntimeError("API error"))

    openai._client = mock_client

    # The generate method should propagate exceptions (they're caught by the caller)
    with pytest.raises(RuntimeError):
        await openai.generate("Hello?")


async def test_openai_accepts_custom_base_url() -> None:
    """OpenAI client should accept a custom base_url for NVIDIA or other endpoints."""
    from briefing.synth import OpenAI

    openai = OpenAI(
        model="nvidia/llama-3.1-nemotron-70b-instruct",
        api_key_env="NVIDIA_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
    )
    assert openai.base_url == "https://integrate.api.nvidia.com/v1"
    assert openai.model == "nvidia/llama-3.1-nemotron-70b-instruct"


async def test_openai_base_url_is_used_when_a_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real key must not silently route the call to api.openai.com.

    Regression: `if not api_key and self.base_url` meant a configured NVIDIA
    `base_url` was dropped the moment `NVIDIA_API_KEY` was set, so the key went
    to OpenAI, was answered 401, and `_generate`'s swallow-all turned that into
    "Model returned no answer; returning passages instead."
    """
    from briefing.synth import OpenAI

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-not-a-real-key")
    seen: dict[str, t.Any] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: t.Any) -> None:
            seen.update(kwargs)

    # Patched at the import site: generate() does `from openai import AsyncOpenAI`.
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)

    openai_client = OpenAI(
        model="nvidia/llama-3.1-nemotron-70b-instruct",
        api_key_env="NVIDIA_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
    )

    with pytest.raises(AttributeError):
        # The fake has no `.chat`, which stops generate() before any network
        # call; by then the constructor kwargs are recorded.
        await openai_client.generate("Hello?")

    assert seen.get("base_url") == "https://integrate.api.nvidia.com/v1"
    assert seen.get("api_key") == "nvapi-not-a-real-key"


async def test_openai_with_speaker_produces_briefing() -> None:
    """Speaker should work with OpenAI as the LLM provider."""
    from briefing.synth import OpenAI, Speaker

    # Create a mock OpenAI client
    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].message.content = "Good morning. Here's your briefing."
    mock_completion.choices[0].finish_reason = "stop"
    mock_completion.usage = MagicMock()
    mock_completion.usage.completion_tokens = 50
    mock_completion.usage.total_tokens = 100

    # Use the actual OpenAI class with mocked client
    openai = OpenAI(model="gpt-4o-mini", api_key_env="OPENAI_API_KEY")
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    openai._client = mock_client

    speaker = Speaker(openai, name="Aether", user_name="Sam", session_summary="Yesterday")

    result = await speaker.briefing(["News item 1", "News item 2"])
    assert result.text == "Good morning. Here's your briefing."
    assert result.fallback is False


async def test_openai_client_falls_back_on_exception() -> None:
    """Speaker falls back when OpenAI client raises an exception."""
    from briefing.synth import OpenAI, Speaker

    # Use the actual OpenAI class with mocked client that raises
    openai = OpenAI()
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = MagicMock(side_effect=RuntimeError("API down"))
    openai._client = mock_client

    speaker = Speaker(openai, name="Aether", user_name="Sam", session_summary="Yesterday")

    result = await speaker.briefing(["News item 1", "News item 2"])
    assert result.text == "Good morning, Sam."
    assert result.fallback is True


# --- a model failure says what and why (visibility) -------------------------


class RaisingClient:
    """Same duck-type as `Gemini`, but raises. Names the model so the log line
    can be checked for it, and no test ever constructs a real client or a key."""

    def __init__(self, error: Exception, model: str = "gemini-2.5-flash") -> None:
        self.error = error
        self.model = model

    async def generate(self, prompt: str) -> t.Any:
        raise self.error


class ModelResultClient:
    """Returns a canned `ModelResult`, the way the real clients do."""

    def __init__(self, result: t.Any, model: str = "gemini-2.5-flash") -> None:
        self.result = result
        self.model = model

    async def generate(self, prompt: str) -> t.Any:
        return self.result


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The rendered messages of the WARNING records at the synth logger."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and record.name == "briefing.synth"
    ]


async def test_a_model_that_raises_says_the_exception_and_the_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The swallow-all caller used to return None silently, so the log (and the
    owner reading it) could not tell a 401 from a quota cap from a dead network.
    Now it names the exception class, its message and the model that raised."""
    with caplog.at_level(logging.WARNING, logger="briefing.synth"):
        briefing = await speaker(RaisingClient(RuntimeError("boom"))).briefing(HEADLINES)

    assert briefing.fallback is True  # behaviour is unchanged
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "RuntimeError" in warnings[0]
    assert "boom" in warnings[0]
    assert "gemini-2.5-flash" in warnings[0]


async def test_the_exception_path_passes_the_traceback_to_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`exc_info=True` is what puts the stack in the log file; without it the
    class and message are all there is, which is not enough to diagnose a call."""
    with caplog.at_level(logging.WARNING, logger="briefing.synth"):
        await speaker(RaisingClient(RuntimeError("boom"))).briefing(HEADLINES)

    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError


async def test_an_empty_answer_says_it_came_back_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A model that returns nothing used to be indistinguishable from one that
    raised: both just produced the greeting. Now the empty case names itself."""
    with caplog.at_level(logging.WARNING, logger="briefing.synth"):
        briefing = await speaker(FakeClient(text="")).briefing(HEADLINES)

    assert briefing.fallback is True
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "no text" in warnings[0]
    assert "gemini-2.5-flash" in warnings[0]


async def test_a_whitespace_only_answer_says_it_came_back_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Whitespace-only is the same silent failure as empty, and says so too."""
    with caplog.at_level(logging.WARNING, logger="briefing.synth"):
        briefing = await speaker(FakeClient(text="  \n\t ")).briefing(HEADLINES)

    assert briefing.fallback is True
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "whitespace" in warnings[0]


async def test_a_max_tokens_result_names_the_provider_and_the_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T12.2's truncation warning survives, and now says which model it was, so
    the token cap can be the thing that gets raised."""
    with caplog.at_level(logging.WARNING, logger="briefing.synth"):
        result = await speaker(
            ModelResultClient(
                ModelResult(text=None, finish_reason="MAX_TOKENS"),
            )
        ).briefing(HEADLINES)

    assert result.fallback is True
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "MAX_TOKENS" in warnings[0]
    assert "gemini-2.5-flash" in warnings[0]


async def test_a_successful_model_call_logs_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The visibility is for failures only: a briefing that worked must not
    produce a warning, or the log stops meaning anything."""
    with caplog.at_level(logging.WARNING, logger="briefing.synth"):
        briefing = await speaker(FakeClient("A briefing worth saying.")).briefing(HEADLINES)

    assert briefing.fallback is False
    assert _warnings(caplog) == []


async def test_a_missing_openai_package_is_not_invisible(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The `openai` extra is optional, so the import is lazy — and a lazy import
    that failed said nothing at all. Now the missing package is named, with the
    endpoint it was needed for and the fix hint."""
    import sys
    import types

    stub = types.ModuleType("openai")

    def _no_async_openai(name: str) -> t.NoReturn:
        if name == "AsyncOpenAI":
            raise ImportError("No module named 'openai'")
        raise AttributeError(name)

    # A stub rather than sys.modules['openai'] = None: the latter raises a bare
    # ModuleNotFoundError that does not say which import failed.
    stub.__getattr__ = _no_async_openai
    monkeypatch.setitem(sys.modules, "openai", stub)

    from briefing.synth import OpenAI

    client = OpenAI(
        model="nvidia/llama-3.1-nemotron-70b-instruct",
        api_key_env="NVIDIA_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
    )

    with caplog.at_level(logging.WARNING, logger="briefing.synth"), pytest.raises(ImportError):
        await client.generate("Hello?")

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "openai" in warnings[0]
    assert "not installed" in warnings[0]
    assert "https://integrate.api.nvidia.com/v1" in warnings[0]
    assert "nvidia/llama-3.1-nemotron-70b-instruct" in warnings[0]


async def test_a_client_that_cannot_be_constructed_names_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SDK constructor reads the environment; a misconfigured endpoint or a
    bad key raised out of it, and the swallow-all caller could only say "no
    answer". Now the construction failure says which endpoint and why."""
    import openai

    class BrokenAsyncOpenAI:
        def __init__(self, **kwargs: t.Any) -> None:
            raise ValueError("api_key must be a string, not None")

    monkeypatch.setattr(openai, "AsyncOpenAI", BrokenAsyncOpenAI)

    from briefing.synth import OpenAI

    client = OpenAI(
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
    )

    with caplog.at_level(logging.WARNING, logger="briefing.synth"), pytest.raises(ValueError):
        await client.generate("Hello?")

    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "api_key must be a string" in warnings[0]
    assert "https://api.openai.com/v1" in warnings[0]
    assert "gpt-4o-mini" in warnings[0]


async def test_no_openai_message_ever_carries_a_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CLAUDE.md's one log rule: the key must not survive. This module never
    interpolates the key itself — the message carries the model name and the
    endpoint, and the key itself is masked on its way to the sink by
    `log.SecretFilter` (test_log.py covers that). Assert both halves: what this
    module composes, and that the record is safe there."""
    import openai

    class KeyCheckingAsyncOpenAI:
        def __init__(self, **kwargs: t.Any) -> None:
            # A message that mentions neither the key nor a slug of one: the
            # realistic non-echoing case, so the assertion is about what this
            # module composes. `SecretFilter` covers the echoing case, and
            # test_log.py asserts that.
            raise ValueError("invalid api key given; check that the endpoint is reachable")

    monkeypatch.setattr(openai, "AsyncOpenAI", KeyCheckingAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-from-a-test")

    from briefing.synth import OpenAI

    client = OpenAI(model="gpt-4o-mini", api_key_env="OPENAI_API_KEY")

    with (
        caplog.at_level(logging.WARNING, logger="briefing.synth"),
        pytest.raises(ValueError, match="invalid api key given"),
    ):
        await client.generate("Hello?")

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    # What this module composes: the model, the endpoint and the reason — never
    # the key itself, nor any part of it.
    assert "gpt-4o-mini" in warnings[0]
    assert "https://api.openai.com/v1" in warnings[0]
    assert "ValueError" in warnings[0]
    assert "sk-fake-key-from-a-test" not in warnings[0]
    # No key slug either: nothing of the key's value is logged in any form.
    for part in ("sk-fake-key", "fake-key-from-a-test", "sk-fake"):
        assert part not in warnings[0]
