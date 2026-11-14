"""Model access: one outcome type, two providers, and the briefing's `Speaker`.

The prompt lives in `prompts/briefing.txt` rather than in this file, so the
owner can reword the briefing without a code change. It is read per call, not at
import: an edit takes effect on the next run, and an edit never breaks a running
process.

Two names, one direction each: `name` is who the briefing is *from* (the
assistant, which the prompt's "You are {name}" needs) and `user_name` is who it
is *to* — the greeting and the prompt's "address them" instruction. They were
one field, whose default named the assistant as both, so a briefing that could
not be synthesised greeted itself (T5.4); that is T8.5's fix.

`google-genai` and `openai` are imported lazily and only by the real clients, so
a `briefing run` with the extras uninstalled still produces its plain greeting
instead of failing at import (SPEC section 6's failure fallback).

Every model call, from every mode, goes through `call_model`, which never raises
for a model reason and always says what happened: a `ModelResult` either carries
usable text or carries *why not* (`problem()`), in plain English with secrets
already removed. Callers decide what to degrade to; this module never decides
that a failure is silent.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import time
import typing as t
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "briefing.txt"

#: A briefing with nobody to address. The whole greeting when `user_name` is
#: unset (T8.5) — "Good morning, ." reads as a comma and a pause, which is worse
#: than not attempting the name at all.
NO_NAME_GREETING = "Good morning."

#: The same greeting with the person named. `{name}` is filled per run.
PLAIN_GREETING = "Good morning, {name}."


@dataclasses.dataclass(frozen=True)
class ModelResult:
    """What one model call produced, or why it produced nothing usable.

    Every field after `usage` has a default, so a provider (or a test double)
    that only knows text and a finish reason still builds one.
    """

    text: str | None
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None
    #: One line saying the call itself failed (network, auth, quota, bad request),
    #: with secrets removed. None when the call completed.
    error: str | None = None
    #: Why the provider declined to answer (safety block, content filter, refusal).
    block_reason: str | None = None
    #: Who answered, or failed to: "gemini/gemini-2.5-flash", "openai/gpt-4o-mini".
    label: str = ""
    elapsed_s: float = 0.0

    @property
    def truncated(self) -> bool:
        """True when the provider stopped at its output limit, not at the end.

        Gemini spells it `MAX_TOKENS` (sometimes `FinishReason.MAX_TOKENS`); the
        OpenAI-compatible endpoints spell it `length`. Checking only one is how a
        cut-off answer from the other went out looking complete.
        """
        reason = str(self.finish_reason or "").upper()
        return "MAX_TOKENS" in reason or reason == "LENGTH"

    @property
    def stage(self) -> str | None:
        """Where it went wrong: request, blocked, truncated or empty; None when fine."""
        if self.error:
            return "request"
        if self.block_reason:
            return "blocked"
        if self.truncated:
            return "truncated"
        if not (self.text and self.text.strip()):
            return "empty"
        return None

    @property
    def usable(self) -> bool:
        return self.stage is None

    def problem(self) -> str | None:
        """Plain English for why there is no usable answer, or None when there is one."""
        who = self.label or "the model"
        stage = self.stage
        if stage == "request":
            return f"{who} failed: {self.error}"
        if stage == "blocked":
            return f"{who} declined to answer ({self.block_reason})"
        if stage == "truncated":
            return f"{who} was cut off at its output limit (finish reason: {self.finish_reason})"
        if stage == "empty":
            return f"{who} returned no text (finish reason: {self.finish_reason or 'none'})"
        return None

    def as_dict(self) -> dict[str, t.Any]:
        """The diagnostics a `--json` consumer or a bug report wants."""
        return {
            "model": self.label,
            "ok": self.usable,
            "stage": self.stage,
            "problem": self.problem(),
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "elapsed_s": round(self.elapsed_s, 2),
        }


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_ERROR_CAP = 300


def redact(text: str, secrets: t.Iterable[str] = ()) -> str:
    """`text` as one short line with every known secret and bearer token masked.

    An SDK's exception can quote the request it made, so anything that reaches
    the screen, the JSON or a bug report goes through here first.
    """
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    text = _BEARER.sub("Bearer ***", text)
    text = " ".join(text.split())
    return text if len(text) <= _ERROR_CAP else text[: _ERROR_CAP - 1].rstrip() + "…"


def clean_model_text(text: str) -> str:
    """Drop a reasoning model's `<think>` block, which is not part of the answer."""
    return _THINK_BLOCK.sub("", text).strip()


async def call_model(
    llm: t.Any,
    prompt: str,
    *,
    max_output_tokens: int | None = None,
) -> ModelResult:
    """Run `llm.generate(prompt)` and return what happened. Never raises.

    The one seam every mode's model call goes through, so a failure is always a
    value that says where it failed instead of an exception swallowed three
    frames up. `llm` is anything with `async generate(prompt)` returning a
    `ModelResult`, a plain string or None (the older contract test doubles still
    use). `max_output_tokens` is passed only when given, so a double that takes
    just a prompt keeps working.
    """
    label = str(getattr(llm, "label", "") or "")
    secrets = [os.environ.get(str(getattr(llm, "api_key_env", "")), "")]
    started = time.monotonic()
    try:
        if max_output_tokens is None:
            raw = await llm.generate(prompt)
        else:
            raw = await llm.generate(prompt, max_output_tokens=max_output_tokens)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reason = redact(f"{type(exc).__name__}: {exc}", secrets)
        logger.debug("model call failed", exc_info=True)
        return ModelResult(
            text=None, error=reason, label=label, elapsed_s=time.monotonic() - started
        )

    elapsed = time.monotonic() - started
    if isinstance(raw, ModelResult):
        result = raw
    else:  # the older contract: a bare string, or None for "nothing"
        result = ModelResult(text=raw if isinstance(raw, str) else None)
    text = clean_model_text(result.text) if result.text else result.text
    return dataclasses.replace(
        result,
        text=text,
        label=result.label or label,
        elapsed_s=result.elapsed_s or elapsed,
    )


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
    """The real Gemini client, behind the one method everything asks for.

    Callers depend on `generate`, not on this class, so a test hands them any
    object with an `async generate(prompt)` and never constructs one of these.
    `model`, the token cap and the key's variable name come from
    `briefing.gemini` in the config (the CLI used to build this with defaults, so
    a configured model was never used).

    The SDK is imported inside `generate`: the extra is optional, and a briefing
    that cannot reach a model must still fall back rather than die at import.

    The key is read from the variable named by `api_key_env` and passed to the SDK
    explicitly. A missing key is an error here rather than `None`, because `None`
    would let the SDK fall back to `GOOGLE_API_KEY` and quietly use a key the
    config never named.
    """

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        max_output_tokens: int = 200,
        api_key_env: str = "GEMINI_API_KEY",
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.api_key_env = api_key_env
        self._client: t.Any = None

    @property
    def label(self) -> str:
        return f"gemini/{self.model}"

    async def generate(self, prompt: str, *, max_output_tokens: int | None = None) -> ModelResult:
        """One generation. Raises on a failed call; `call_model` turns that into a value."""
        from google import genai  # lazy: the extra is optional (pyproject)
        from google.genai import types

        if self._client is None:
            api_key = os.environ.get(self.api_key_env, "")
            if not api_key:
                raise RuntimeError(f"{self.api_key_env} is not set")
            self._client = genai.Client(api_key=api_key)
        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_output_tokens or self.max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        finish_reason: str | None = None
        usage_info: dict[str, int | None] | None = None
        block_reason: str | None = None
        if response.candidates:
            reason = response.candidates[0].finish_reason
            finish_reason = getattr(reason, "name", None) or str(reason)
            usage = response.usage_metadata
            usage_info = {
                "visible_tokens": getattr(usage, "candidates_token_count", None),
                "thinking_tokens": getattr(usage, "thoughts_token_count", None),
                "total_tokens": getattr(usage, "total_token_count", None),
            }
        else:
            # No candidate at all means the *prompt* was refused, not that the
            # model had nothing to say; the feedback names why.
            feedback = getattr(response, "prompt_feedback", None)
            blocked = getattr(feedback, "block_reason", None)
            block_reason = getattr(blocked, "name", None) or (str(blocked) if blocked else None)
            block_reason = block_reason or "no candidates returned"
        logger.debug(
            "Gemini response: finish_reason=%s usage=%s block=%s",
            finish_reason,
            usage_info,
            block_reason,
        )
        return ModelResult(
            text=response.text if response.candidates else None,
            finish_reason=finish_reason,
            usage=usage_info,
            block_reason=block_reason,
            label=self.label,
        )


class OpenAI:
    """OpenAI-compatible client: OpenAI itself, NVIDIA NIM, or any `/v1` endpoint.

    Mirrors `Gemini`: same `generate()` returning `ModelResult`. `base_url` is
    always handed to the SDK. (It used to be dropped whenever a key was set, so a
    key meant for NVIDIA was sent to `api.openai.com` and rejected there.)

    The key comes from the variable named by `api_key_env` only. A missing key is
    an error rather than `None`: `None` lets the SDK read `OPENAI_API_KEY`, which
    could send a key for one service to another service's `base_url`.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        max_tokens: int = 200,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.max_tokens = max_tokens
        self._client: t.Any = None

    @property
    def label(self) -> str:
        host = self.base_url.split("//")[-1].split("/")[0]
        return f"openai/{self.model}" if host == "api.openai.com" else f"{self.model} @ {host}"

    async def _create(self, prompt: str, limit: int) -> t.Any:
        """The chat call, retried once for models that renamed the token limit.

        Newer OpenAI models reject `max_tokens` and want `max_completion_tokens`;
        older and compatible endpoints only know `max_tokens`. The error names the
        parameter, so the retry happens only when it should.
        """
        kwargs: dict[str, t.Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            return await self._client.chat.completions.create(max_tokens=limit, **kwargs)
        except Exception as exc:
            if "max_completion_tokens" not in str(exc):
                raise
            return await self._client.chat.completions.create(max_completion_tokens=limit, **kwargs)

    async def generate(self, prompt: str, *, max_output_tokens: int | None = None) -> ModelResult:
        """One generation. Raises on a failed call; `call_model` turns that into a value."""
        from openai import AsyncOpenAI  # lazy: the extra is optional

        if self._client is None:
            api_key = os.environ.get(self.api_key_env, "")
            if not api_key:
                raise RuntimeError(f"{self.api_key_env} is not set")
            self._client = AsyncOpenAI(api_key=api_key, base_url=self.base_url or None)

        response = await self._create(prompt, max_output_tokens or self.max_tokens)

        choice = response.choices[0]
        finish_reason = choice.finish_reason
        usage = response.usage
        usage_info: dict[str, int | None] = {
            "visible_tokens": usage.completion_tokens if usage else None,
            "total_tokens": usage.total_tokens if usage else None,
        }
        message = choice.message
        refusal = getattr(message, "refusal", None)
        block_reason: str | None = None
        if finish_reason == "content_filter":
            block_reason = "content_filter"
        elif isinstance(refusal, str) and refusal:
            block_reason = f"refusal: {redact(refusal)}"
        logger.debug(
            "OpenAI response: finish_reason=%s total_tokens=%s",
            finish_reason,
            usage_info.get("total_tokens"),
        )
        content = message.content
        return ModelResult(
            text=content if isinstance(content, str) else None,
            finish_reason=finish_reason,
            usage=usage_info,
            block_reason=block_reason,
            label=self.label,
        )


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


async def _generate(llm: t.Any, prompt: t.Callable[[], str]) -> str | None:
    """The model's usable text for the prompt `prompt()` builds, or None.

    The briefing's model call. A model that fails, is blocked, is cut off or says
    nothing degrades to the greeting (SPEC section 6), but it no longer does so
    silently: the reason is logged at WARNING, which `--verbose` and the log file
    both show. Building the prompt is inside the guard for the same reason — a
    lost `prompts/briefing.txt` is a repo that cannot speak a briefing, which is
    the greeting, not a crash.
    """
    try:
        built = prompt()
    except Exception as exc:  # noqa: BLE001 - an unbuildable prompt degrades like a failed call
        logger.warning(
            "briefing prompt could not be built: %s", redact(f"{type(exc).__name__}: {exc}")
        )
        return None
    result = await call_model(llm, built)
    problem = result.problem()
    if problem is not None:
        logger.warning("briefing model: %s", problem)
        return None
    return str(result.text).strip()
