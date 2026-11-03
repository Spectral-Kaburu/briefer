"""Gemini synthesis: briefing mode (T5.3) and research mode (T4.1).

The prompt lives in `prompts/briefing.txt` rather than in this file, so the
owner can reword the briefing without a code change. It is read per call, not at
import: an edit takes effect on the next run, and an edit never breaks a running
process.

Two names, one direction each: `name` is who the briefing is *from* (the
assistant, which the prompt's "You are {name}" needs) and `user_name` is who it
is *to* — the greeting and the prompt's "address them" instruction. They were
one field, whose default named the assistant as both, so a briefing that could
not be synthesised greeted itself (T5.4); that is T8.5's fix.

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

#: A briefing with nobody to address. The whole greeting when `user_name` is
#: unset (T8.5) — "Good morning, ." reads as a comma and a pause, which is worse
#: than not attempting the name at all.
NO_NAME_GREETING = "Good morning."

#: The same greeting with the person named. `{name}` is filled per run.
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


def _greeting(user_name: str) -> str:
    """The plain greeting, naming the person only when there is one to name."""
    stripped = user_name.strip()
    if not stripped:
        return NO_NAME_GREETING
    return PLAIN_GREETING.format(name=stripped)


def _address_line(user_name: str) -> str:
    """The prompt's name instruction, or nothing when nobody is named.

    An empty line rather than an empty value: the template asks the model to
    address the person by name, and a model told to do that with no name
    invents one (T8.5).
    """
    stripped = user_name.strip()
    if not stripped:
        return ""
    return f"\nThe person you are speaking to is called {stripped} — refer to them by name."


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

    def __init__(
        self,
        gemini: t.Any,
        *,
        name: str = "",
        user_name: str = "",
        session_summary: str = "",
    ) -> None:
        self.gemini = gemini
        #: Who the briefing is from (the assistant), used by the prompt only.
        self.name = name
        #: Who it is to. The greeting and the prompt's address instruction.
        self.user_name = user_name
        self.session_summary = session_summary

    def prompt(self, headlines: t.Sequence[str]) -> str:
        """The filled prompt. Kept public because it is what a test inspects."""
        template = load_prompt()
        joined = "\n".join(f"- {headline}" for headline in headlines)
        return template.format(
            name=self.name,
            session_summary=self.session_summary,
            headlines=joined,
            address=_address_line(self.user_name),
        )

    async def briefing(self, headlines: t.Sequence[str]) -> SpokenBriefing:
        """Synthesise, or fall back to the greeting.

        An empty headline list never reaches the model: with nothing fetched
        there is nothing to summarise, and a model given an empty list invents
        news (SPEC section 6's "if all fetching fails, plain session greeting").
        """
        if not headlines:
            return SpokenBriefing(_greeting(self.user_name), fallback=True)
        text = await _generate(self.gemini, lambda: self.prompt(headlines))
        if text is None:
            return SpokenBriefing(_greeting(self.user_name), fallback=True)
        return SpokenBriefing(text, fallback=False)


async def _generate(gemini: t.Any, prompt: t.Callable[[], str]) -> str | None:
    """The model's usable text for the prompt `prompt()` builds, or None.

    The one place a model failure becomes a value rather than an exception. Both
    `Speaker` and `ResearchSynth` go through it, because SPEC section 6's fallback
    and section 2's `--llm` degrade the same way: a model that raises, returns
    nothing, or returns only whitespace is a model that said nothing.

    Building the prompt is inside the try for the same reason — a lost
    `prompts/briefing.txt` is a repo that cannot speak a briefing, which is the
    greeting, not a crash.
    """
    try:
        text = await gemini.generate(prompt())
    except Exception:  # noqa: BLE001 - any failure degrades, never raises
        return None
    if text is None or not text.strip():
        return None
    return str(text).strip()


# Research mode's prompts. They are not files like `prompts/briefing.txt` because
# nothing in SPEC section 2 asks for the owner to reword them; if that changes,
# move both to `prompts/` and read them per call the way `load_prompt` does.
_ABSTRACTIVE_PROMPT = (
    "Summarise the page below in two sentences, using only what it says. If it says\n"
    "nothing of substance, say so rather than inventing anything.\n\n"
    "Heading: {heading}\n\n{text}"
)

_ANSWER_PROMPT = (
    "Answer the question in three sentences or fewer, using only the sources below.\n"
    "Cite each claim's source as its number in brackets, e.g. [1]. If the sources do\n"
    "not answer it, say that instead of guessing.\n\n"
    "Question: {query}\n\n{sources}"
)


class ResearchSynth:
    """Research mode's two Gemini features: abstractive summaries, one answer.

    `--llm` rewrites each page's extractive summary; `--synth` builds one answer
    from all of them, numbered so the answer can cite its sources (SPEC section 2).
    Both are opt-in, so both must be able to say "nothing": `summary` takes the
    fallback it degrades to, and `answer` returns None.
    """

    def __init__(self, gemini: t.Any) -> None:
        self.gemini = gemini

    async def summary(self, *, heading: str, text: str, fallback: str) -> str:
        """The model's summary of the page, or `fallback` when there isn't one.

        `fallback` is the extractive summary the page already had, which is the
        whole of SPEC section 2's graceful degradation: a model that cannot
        summarise leaves the result as it was, never emptier.
        """
        prompt = _ABSTRACTIVE_PROMPT.format(heading=heading, text=text)
        return await _generate(self.gemini, lambda: prompt) or fallback

    async def answer(self, *, query: str, sources: t.Sequence[tuple[str, str]]) -> str | None:
        """One answer across every source, or None when there isn't one.

        Empty `sources` never reaches the model: with nothing fetched there is
        nothing to answer from, and a model given no sources invents news.
        """
        if not sources:
            return None
        numbered = "\n".join(
            f"{position}. {heading} — {summary}"
            for position, (heading, summary) in enumerate(sources, start=1)
        )
        prompt = _ANSWER_PROMPT.format(query=query, sources=numbered)
        return await _generate(self.gemini, lambda: prompt)
