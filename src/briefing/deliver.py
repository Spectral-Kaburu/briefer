"""Speak the briefing: `voice-tts` through an injectable runner (T5.4).

SPEC section 6's delivery is `IPC audio.speak` → `voice-tts -s aether -p high
"<text>"`. The runner is injected because the command must never be executed
from a test (CLAUDE.md), and because a process spawn behind a seam is the only
way to prove the argv without spawning one. `SubprocessRunner` is the real one.

`--dry-run` exists so the owner can read the exact command and text before a
voice says them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shlex
import typing as t

#: The binary SPEC section 6 names. Not configurable: wrong voice or pitch is a
#: silent failure the owner cannot hear coming.
COMMAND = "voice-tts"


class DeliveryError(Exception):
    """`voice-tts` ran and failed, or could not run at all.

    Carries a fix hint like every other user-facing error (CLAUDE.md), so a
    broken audio path reads as instructions rather than a traceback.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclasses.dataclass(frozen=True)
class Delivery:
    """What was sent to be spoken, and how.

    `command` is the argv, including on a dry run — that is what the owner reads
    back. `status` is meaningful only when `dry_run` is False, because nothing is
    executed otherwise.
    """

    text: str
    command: list[str]
    dry_run: bool
    status: int


async def deliver(
    text: str,
    *,
    runner: t.Any,
    dry_run: bool = False,
    echo: t.Callable[[str], None] | None = None,
) -> Delivery:
    """Speak `text` through `runner`, or print the command when `dry_run`.

    `echo` is the printer (a test passes a recorder, the CLI passes `typer.echo`);
    it defaults to the real one, so a caller that only cares about the return
    value does not have to supply it.

    A nonzero status, a missing binary, and anything else the runner raises all
    become a `DeliveryError`: the briefing was written, so the run must fail
    loudly rather than silently skipping the part it exists for.
    """
    if echo is None:
        import typer

        echo = typer.echo
    argv = [COMMAND, "-s", "aether", "-p", "high", text]
    if dry_run:
        # The whole point of --dry-run: the owner reads the exact argv, so it is
        # printed as a shell would quote it, not as a Python list.
        echo(" ".join(shlex.quote(part) for part in argv))
        return Delivery(text, argv, dry_run=True, status=0)
    try:
        status = await runner.run(argv)
    except FileNotFoundError:
        raise DeliveryError(
            f"{COMMAND} was not found on PATH, so the briefing was not spoken.",
            f"Install {COMMAND} and check `which {COMMAND}`, or use --dry-run to read the "
            "command without running it.",
        ) from None
    except Exception as exc:  # noqa: BLE001 - an unknown failure must not traceback
        raise DeliveryError(
            f"{COMMAND} failed and the briefing was not spoken.",
            f"Try --dry-run to check the command, then re-run. ({exc})",
        ) from None
    if status:
        raise DeliveryError(
            f"{COMMAND} exited with status {status}, so the briefing was not spoken.",
            f"Re-run with --dry-run to check the command, or check the {COMMAND} logs.",
        )
    return Delivery(text, argv, dry_run=False, status=status)


class SubprocessRunner:
    """Runs argv as a child process and reports its exit status.

    The real runner behind the seam: `deliver` never spawns a process itself, so
    a test can use anything with `async run(argv) -> int` and still guarantee the
    binary is untouched.
    """

    async def run(self, argv: list[str]) -> int:
        """Exit status of argv, or `FileNotFoundError` when it is not on PATH.

        That exception is the signal `deliver` turns into a plain-English error,
        so it must escape rather than become a status.
        """
        process = await asyncio.create_subprocess_exec(*argv)
        return await process.wait()
