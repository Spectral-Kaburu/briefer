"""Tests for deliver.py: running `voice-tts` through an injectable runner (T5.4).

CLAUDE.md: never run `voice-tts`. Every test here uses a fake runner that records
its argv and returns a canned exit status, so the real command is never spawned,
not even guarded by an env var. The owner tests the audio afterwards.

The command itself is the spec: `voice-tts -s aether -p high "<text>"` (SPEC
section 6, "Delivery"). Getting the flags wrong is a silent failure — the text
would come out in the wrong voice or be truncated — so the argv is asserted
exactly rather than by substring.
"""

from __future__ import annotations

import asyncio
import typing as t

import pytest

from briefing.deliver import COMMAND, DeliveryError, SubprocessRunner, deliver

pytestmark = pytest.mark.asyncio


class FakeRunner:
    """Records argv, and answers with the status it was told to."""

    def __init__(self, status: int = 0, raises: Exception | None = None) -> None:
        self.status = status
        self.raises = raises
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str]) -> int:
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        return self.status


# --- the command (SPEC section 6) -----------------------------------------


async def test_the_command_is_voice_tts_with_the_aether_voice_at_high_pitch() -> None:
    runner = FakeRunner()
    await deliver("Good morning.", runner=runner)
    assert runner.calls == [[COMMAND, "-s", "aether", "-p", "high", "Good morning."]]


async def test_the_text_is_the_last_argument() -> None:
    """`voice-tts` reads the text as its final argument.

    Reordered or quoted away, a long briefing would be clipped at the first space.
    """
    runner = FakeRunner()
    await deliver("A two sentence briefing. Second sentence.", runner=runner)
    argv = runner.calls[0]
    assert argv[-1] == "A two sentence briefing. Second sentence."


async def test_a_text_of_spaces_is_still_sent() -> None:
    """An odd text is voice-tts's problem, not ours: we send what we were given."""
    runner = FakeRunner()
    await deliver("   ", runner=runner)
    assert runner.calls[0][-1] == "   "


# --- dry run ----------------------------------------------------------------


async def test_a_dry_run_prints_the_command_and_runs_nothing(capsys: t.Any) -> None:
    runner = FakeRunner()
    result = await deliver("Good morning.", runner=runner, dry_run=True)
    assert runner.calls == []
    assert COMMAND in capsys.readouterr().out
    assert result.dry_run is True


async def test_a_dry_run_still_reports_what_it_would_have_said() -> None:
    """`--dry-run` is how the owner checks the text before it is spoken."""
    runner = FakeRunner()
    result = await deliver("Good morning.", runner=runner, dry_run=True)
    assert result.text == "Good morning."


async def test_the_printed_dry_run_line_carries_the_text(capsys: t.Any) -> None:
    """Without it the printed line is not reproducible by hand."""
    runner = FakeRunner()
    await deliver("Good morning.", runner=runner, dry_run=True)
    out = capsys.readouterr().out
    assert "Good morning." in out


# --- failure ----------------------------------------------------------------


async def test_a_nonzero_exit_is_a_delivery_error_with_the_status() -> None:
    runner = FakeRunner(status=3)
    with pytest.raises(DeliveryError) as excinfo:
        await deliver("Good morning.", runner=runner)
    assert "3" in str(excinfo.value)


async def test_a_nonzero_exit_names_the_command_that_failed() -> None:
    runner = FakeRunner(status=1)
    with pytest.raises(DeliveryError) as excinfo:
        await deliver("Good morning.", runner=runner)
    assert COMMAND in str(excinfo.value)


async def test_a_missing_binary_is_not_a_traceback() -> None:
    """`voice-tts` not on PATH is a plain-English error with a fix hint."""
    runner = FakeRunner(raises=FileNotFoundError(COMMAND))
    with pytest.raises(DeliveryError) as excinfo:
        await deliver("Good morning.", runner=runner)
    error = excinfo.value
    assert COMMAND in str(error)
    assert error.hint


async def test_anything_else_the_runner_raises_is_reported_not_swallowed() -> None:
    """An unknown failure still must not escape as a traceback."""
    runner = FakeRunner(raises=OSError("audio device busy"))
    with pytest.raises(DeliveryError):
        await deliver("Good morning.", runner=runner)


async def test_a_dry_run_cannot_fail_the_run() -> None:
    """Nothing is executed, so nothing can fail."""
    runner = FakeRunner(raises=FileNotFoundError(COMMAND))
    await deliver("Good morning.", runner=runner, dry_run=True)


# --- the real runner ------------------------------------------------------
#
# These two are the seam-contract tests, and they do not spawn anything
# (T8.1's guard). `SubprocessRunner.run` is two lines: spread argv into
# `create_subprocess_exec`, then report the child's exit status. The seam *is*
# that spawn call, so patching it is not a weaker test — it is the only way to
# test the runner without a real process, and it pins the two things a fake
# runner cannot prove: that the argv is spread rather than passed as a list,
# and that a missing binary escapes instead of becoming a status.


class FakeProcess:
    """A child process that never ran.

    `create_subprocess_exec` either returns a process or raises — a binary
    missing from PATH raises there, not from `.wait()` — so `raises` is spent at
    spawn time to match reality.
    """

    def __init__(self, status: int = 0, raises: Exception | None = None) -> None:
        self.status = status
        self.raises = raises
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        return self.status


def _patch_spawn(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeProcess,
    seen: list[list[str]],
) -> None:
    """Stand in for `asyncio.create_subprocess_exec`, recording the argv."""

    async def spawn(*argv: str) -> FakeProcess:
        seen.append(list(argv))
        if process.raises is not None:
            raise process.raises
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


async def test_the_real_runner_reports_a_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`deliver` needs a status to decide success, and it must be the child's."""
    seen: list[list[str]] = []
    process = FakeProcess(status=7)
    _patch_spawn(monkeypatch, process, seen)

    argv = [COMMAND, "-s", "aether", "-p", "high", "Good morning."]
    status = await SubprocessRunner().run(argv)

    assert status == 7
    assert process.waited, "the status must come from waiting on the child"
    # Spread, not a list: `create_subprocess_exec([...])` would run a program
    # literally named the whole argv, and no audio would ever be produced.
    assert seen == [argv]


async def test_the_real_runner_lets_a_missing_binary_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FileNotFoundError` must escape, or the missing-binary error cannot happen."""
    seen: list[list[str]] = []
    _patch_spawn(monkeypatch, FakeProcess(raises=FileNotFoundError(COMMAND)), seen)

    with pytest.raises(FileNotFoundError):
        await SubprocessRunner().run([f"{COMMAND}-definitely-not-installed"])
