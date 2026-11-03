"""Test-isolation guard (T8.1): no test touches a real process, socket or DNS.

The owner's T5.4 incident is why this exists. While sabotage-testing the
briefing gate, a test left `_RUNNER` unpatched, the sabotage removed the gate,
and the real `SubprocessRunner` spoke aloud with a real `voice-tts`. The gate
tests were then hardened to patch every collaborator — which fixes that one
path. This guard fixes the *class*, so no test can do it again, whatever it
forgets to patch.

Two layers, because T5.3 showed a broad `except` hiding a real failure:

1. `RealSideEffectBlocked` derives from **`BaseException`**, so `except
   Exception` cannot swallow it. A guard that a production bug can disable is
   not a guard.
2. Every attempt is recorded in `blocked`, and the fixture's teardown fails the
   test if the list is non-empty — even if something caught the exception.

The `live` marker is the only way through: `tests/test_live.py` needs the real
network, and `make live` runs it.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import pty
import socket
import subprocess
import typing as t
from pathlib import Path

import pytest

#: Blocked attempts from the test currently running. The teardown reads it.
blocked: list[str] = []

#: Running count of blocks for the current test. A test can clear `blocked`
#: (see `_reset` in test_isolation.py), so the guard's teardown compares this
#: count against what it saw at setup — that is what makes layer 2 survive a
#: swallowed block, not just a swallowed exception.
_count: list[int] = [0]


def _reset_blocks() -> None:
    """Clear both signals. For tests that block on purpose (test_isolation)."""
    blocked.clear()
    _count[0] = 0


def _blocked_now() -> bool:
    """True if anything was blocked since the guard installed.

    Both signals are checked: a cleared `blocked` list is what a deliberate
    clear looks like, and a `_count` that moved is what any block looks like.
    """
    return bool(blocked) or _count[0] > 0


#: What each blocked call tried to do, for the message and the record.
_PROCESS = "spawn a child process"
_SOCKET = "open a real socket"
_DNS = "resolve a host name"

_FIX_HINT = (
    "patch the seam (_RUNNER, _SEARCH, _DEDUP, _AUDIO, _SPEAKER, _PIPELINE) "
    "instead; a test that genuinely needs the network is marked "
    "@pytest.mark.live and run by `make live`"
)


class RealSideEffectBlocked(BaseException):
    """A test tried to reach the real world.

    Derives from `BaseException`, not `Exception`: the point is that a
    `try/except Exception` in the code under test cannot swallow this and turn a
    blocked call into a silently-wrong result (T5.3).
    """


def _block(what: str) -> t.NoReturn:
    blocked.append(what)
    _count[0] += 1
    raise RealSideEffectBlocked(f"test tried {what}; {_FIX_HINT}")


def _subprocess_run(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _subprocess_popen(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _subprocess_call(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _subprocess_check_call(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _subprocess_check_output(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _os_system(command: str) -> int:
    _block(_PROCESS)


def _os_exec(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _os_spawn(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _connect(self: socket.socket, *a: t.Any, **k: t.Any) -> t.Any:
    _block(_SOCKET)


def _connect_ex(self: socket.socket, *a: t.Any, **k: t.Any) -> int:
    _block(_SOCKET)


def _create_connection(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_SOCKET)


def _getaddrinfo(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_DNS)


def _gethostbyname(host: str) -> t.Any:
    _block(_DNS)


def _getnameinfo(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_DNS)


async def _open_connection(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_SOCKET)


async def _create_subprocess_exec(prog: str, *a: str, **k: t.Any) -> t.Any:
    _block(_PROCESS)


async def _create_subprocess_shell(cmd: str, *a: str, **k: t.Any) -> t.Any:
    _block(_PROCESS)


def _multiprocess_start(self: t.Any) -> None:
    _block(_PROCESS)


def _pty_spawn(*a: t.Any, **k: t.Any) -> t.Any:
    _block(_PROCESS)


#: (module, attribute, blocker, original) — captured once at import, so the real
#: functions are always the ones restored.
_PATCHES: list[tuple[t.Any, str, t.Any, t.Any]] = [
    (subprocess, "run", _subprocess_run, subprocess.run),
    (subprocess, "Popen", _subprocess_popen, subprocess.Popen),
    (subprocess, "call", _subprocess_call, subprocess.call),
    (subprocess, "check_call", _subprocess_check_call, subprocess.check_call),
    (subprocess, "check_output", _subprocess_check_output, subprocess.check_output),
    (os, "system", _os_system, os.system),
    (os, "execl", _os_exec, os.execl),
    (os, "execle", _os_exec, os.execle),
    (os, "execlp", _os_exec, os.execlp),
    (os, "execv", _os_exec, os.execv),
    (os, "execve", _os_exec, os.execve),
    (os, "execvp", _os_exec, os.execvp),
    (os, "spawnl", _os_spawn, os.spawnl),
    (os, "spawnv", _os_spawn, os.spawnv),
    (os, "posix_spawn", _os_spawn, os.posix_spawn),
    (multiprocessing.Process, "start", _multiprocess_start, multiprocessing.Process.start),
    (pty, "spawn", _pty_spawn, pty.spawn),
    (socket.socket, "connect", _connect, socket.socket.connect),
    (socket.socket, "connect_ex", _connect_ex, socket.socket.connect_ex),
    (socket, "create_connection", _create_connection, socket.create_connection),
    (socket, "getaddrinfo", _getaddrinfo, socket.getaddrinfo),
    (socket, "gethostbyname", _gethostbyname, socket.gethostbyname),
    (socket, "gethostbyname_ex", _gethostbyname, socket.gethostbyname_ex),
    (socket, "getnameinfo", _getnameinfo, socket.getnameinfo),
    # `socketpair` is deliberately absent: the event loop calls it in
    # `_close_self_pipe_socket` on every loop close, and a socketpair is a
    # loop-local pipe that never touches the network.
    (asyncio, "open_connection", _open_connection, asyncio.open_connection),
    (
        asyncio,
        "create_subprocess_exec",
        _create_subprocess_exec,
        asyncio.create_subprocess_exec,
    ),
    (
        asyncio,
        "create_subprocess_shell",
        _create_subprocess_shell,
        asyncio.create_subprocess_shell,
    ),
]


def _install() -> None:
    for module, attribute, blocker, _original in _PATCHES:
        setattr(module, attribute, blocker)


def _restore() -> None:
    for module, attribute, _blocker, original in _PATCHES:
        setattr(module, attribute, original)


def _is_live(request: pytest.FixtureRequest) -> bool:
    """True when the test is marked `live` and may reach the real network."""
    return bool(request.node.get_closest_marker("live"))


@pytest.fixture(autouse=True)
def _no_owner_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """T8.3: point config discovery at a tmp dir so no test reads the owner's.

    `discover_config` reads `$BRIEFING_CONFIG` then `platformdirs`, which resolves
    to `~/.config/briefing` — a real file the owner may have. Without this, a
    `runner.invoke(app, ["config"])` in any test would silently load it and make
    the suite depend on the machine.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def _guard(request: pytest.FixtureRequest) -> t.Iterator[None]:
    """Install the guard for every test that is not marked `live`.

    `tests/test_isolation.py` installs this same guard directly so its tests
    can assert on the block itself; everything else gets it for free.
    """
    blocked.clear()
    _count[0] = 0
    if not _is_live(request):
        _install()
    try:
        yield
    finally:
        _restore()
        reached = _blocked_now()
        attempts = list(blocked)
        blocked.clear()
        _count[0] = 0
        if reached:
            detail = ", ".join(sorted(set(attempts))) or "an attempt was cleared"
            pytest.fail(f"test reached the real world ({detail}) — {_FIX_HINT}")
