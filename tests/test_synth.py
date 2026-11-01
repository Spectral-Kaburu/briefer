"""Tests for synth.py: the briefing synthesis and its fallback (T5.3).

SPEC section 6: a Gemini prompt turns the name, yesterday's session summary and
today's headlines into 2-3 spoken sentences. The network is never touched here:
`Speaker` depends on the `generate` method, not on the `Gemini` class, so the
tests hand it any object with `async generate(prompt)` and never construct a key
(CLAUDE.md: never call the real Gemini in tests).

The fallback matters as much as the synthesis: SPEC section 6 says a briefing
whose fetching failed entirely gets a plain greeting rather than silence, and a
model that raises, returns nothing, or returns only whitespace must all degrade
the same way.
"""

from __future__ import annotations

import typing as t

import pytest

from briefing.synth import PLAIN_GREETING, Speaker

# Every test here awaits, so the marker is module-wide rather than 13 copies.
pytestmark = pytest.mark.asyncio

HEADLINES = ["Bank raises the lending rate", "Flooding displaces families in Nairobi"]
SESSION = "Yesterday we deployed the briefing service."
GREETING = "Good morning, Aether."


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

    The fake is passed straight to `Speaker`: it depends on `generate`, not on
    the `Gemini` class, so no wrapper stands between the test and the behaviour.
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


# --- the fallback (SPEC section 6) ----------------------------------------


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
    """An unconfigured name is not a reason to say nothing at all."""
    client = FakeClient()
    briefing = await speaker(client, name="").briefing(HEADLINES)
    assert briefing.text == "Good morning. Here is your briefing."

    empty = await speaker(client, name="").briefing([])
    assert empty.text == PLAIN_GREETING.format(name="")


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
