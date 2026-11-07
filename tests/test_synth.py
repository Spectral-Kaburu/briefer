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

import typing as t
from unittest.mock import AsyncMock, MagicMock

import pytest

from briefing.synth import NO_NAME_GREETING, ResearchSynth, Speaker

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
    ) -> None:
        self.text = text
        self.raises = raises
        self.prompts: list[str] = []

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
