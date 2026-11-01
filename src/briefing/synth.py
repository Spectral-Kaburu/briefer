"""Gemini synthesis for briefing mode (T5.3; SPEC section 6).

The prompt lives in `prompts/briefing.txt` rather than in this file, so the
owner can reword the briefing without a code change. It is read per call, not at
import: an edit takes effect on the next run, and an edit never breaks a running
process.

`google-genai` is imported lazily and only by the real client, so a `briefing
run` with the extra uninstalled still produces its plain greeting instead of
failing at import (SPEC section 6's failure fallback).

Every model failure degrades the same way, to one greeting. Nothing here raises
for a model reason: an unspeakable briefing is a broken briefing, and the
fallback is the spec'd behaviour rather than an error.
"""

from __future__ import annotations

import dataclasses
import typing as t
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "briefing.txt"

#: What a model that cannot answer is replaced with. `{name}` is filled per run,
#: so an unconfigured name degrades to "Good morning, ." — see `_greeting`.
PLAIN_GREETING = "Good morning, {name}."


@dataclasses.dataclass(frozen=True)
class SpokenBriefing:
    """What `voice-tts` will say, and whether it is the model's or the fallback's.

    `fallback` is carried so a caller can report *why* the briefing is generic
    (T5.4's `--dry-run` prints it); delivery itself says the same sentence either
    way, because SPEC section 6 treats a greeting as a complete briefing.
    """

    text: str
    fallback: bool


def _greeting(name: str) -> str:
    """The plain greeting, with the name when there is one to use."""
    return PLAIN_GREETING.format(name=name.strip())


def load_prompt() -> str:
    """`prompts/briefing.txt`.

    A missing file raises, and `briefing`'s failure handler turns that into the
    greeting: no prompt means no synthesis, and a silent model call with an empty
    prompt is how a briefing ends up inventing news.
    """
    return PROMPT_PATH.read_text(encoding="utf-8")


class Gemini:
    """The real Gemini client, behind the one method `Speaker` asks for.

    `Speaker` depends on the method, not on this class, so a test hands it any
    object with an `async generate(prompt)` and never constructs one of these.
    `model` and the token cap describe the model call rather than the briefing,
    so they live here.

    The SDK is imported inside `generate`: the extra is optional, and a briefing
    that cannot reach a model must still fall back rather than die at import.
    """

    def __init__(self, *, model: str = "gemini-2.5-flash", max_output_tokens: int = 200) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._client: t.Any = None

    async def generate(self, prompt: str) -> str | None:
        """The model's text for `prompt`, or None when it produced none.

        The key comes from the environment via the SDK's own client, which is
        what keeps a secret out of this process's arguments and config file.
        """
        from google import genai  # lazy: the extra is optional (pyproject)

        if self._client is None:
            self._client = genai.Client()
        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(max_output_tokens=self.max_output_tokens),
        )
        return response.text


class Speaker:
    """Turns a topic's headlines into what Aether says out loud.

    `gemini` is the duck-typed dependency: anything with
    `async generate(prompt) -> str | None`, which is what `Gemini` provides.
    Holds no state beyond what it was built with, so two Speakers built the same
    way produce the same briefing.
    """

    def __init__(self, gemini: t.Any, *, name: str = "", session_summary: str = "") -> None:
        self.gemini = gemini
        self.name = name
        self.session_summary = session_summary

    def prompt(self, headlines: t.Sequence[str]) -> str:
        """The filled prompt. Kept public because it is what a test inspects."""
        template = load_prompt()
        joined = "\n".join(f"- {headline}" for headline in headlines)
        return template.format(
            name=self.name, session_summary=self.session_summary, headlines=joined
        )

    async def briefing(self, headlines: t.Sequence[str]) -> SpokenBriefing:
        """Synthesise, or fall back to the greeting.

        An empty headline list never reaches the model: with nothing fetched
        there is nothing to summarise, and a model given an empty list invents
        news (SPEC section 6's "if all fetching fails, plain session greeting").
        """
        if not headlines:
            return SpokenBriefing(_greeting(self.name), fallback=True)
        try:
            text = await self.gemini.generate(self.prompt(headlines))
        except Exception:  # noqa: BLE001 - any model failure degrades to the greeting
            return SpokenBriefing(_greeting(self.name), fallback=True)
        if text is None or not text.strip():
            return SpokenBriefing(_greeting(self.name), fallback=True)
        return SpokenBriefing(text.strip(), fallback=False)
