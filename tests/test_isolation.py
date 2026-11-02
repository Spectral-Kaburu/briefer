"""Tests for the test-isolation guard (T8.1).

The guard's job is that no test can reach a real process, a socket, or a DNS
lookup unless it is explicitly marked `@pytest.mark.live`. That is T8.1's
acceptance, so it is tested here *as a guard*: the throwaway cases PLAN.md names
(i) and (ii), plus the unswallowable layer that makes them hold.

Each test below blocks one on purpose. They are not a licence to spawn
processes; they exist so a weaker guard fails loudly instead of silently.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import typing as t

import pytest

# The guard lives in `tests/conftest.py` and is an autouse fixture, so this file
# is already guarded. These fixtures expose its parts so the guard itself can be
# tested without importing a `tests.` module (there is no `tests/__init__.py`).
from conftest import RealSideEffectBlocked, _reset_blocks, blocked


@pytest.fixture(autouse=True)
def _reset() -> t.Iterator[None]:
    """Empty both signals between tests, so teardown only sees this test.

    The counter matters as much as the list: it is what survives a `blocked.clear()`,
    so a test that blocks on purpose must clear both, or the guard's own teardown
    fails a test that was meant to pass.
    """
    _reset_blocks()
    yield
    _reset_blocks()


# --- (i) a real side effect is blocked with the guard's message --------------


def test_a_real_subprocess_is_blocked() -> None:
    """(i): the plain `subprocess.run` form the guard must catch."""
    with pytest.raises(RealSideEffectBlocked, match="patch the seam"):
        subprocess.run([sys.executable, "-c", "print(1)"], check=False)


def test_a_real_socket_connect_is_blocked() -> None:
    import socket

    with pytest.raises(RealSideEffectBlocked, match="patch the seam"):
        socket.socket().connect(("127.0.0.1", 1))


def test_a_dns_lookup_is_blocked() -> None:
    import socket

    with pytest.raises(RealSideEffectBlocked, match="patch the seam"):
        socket.getaddrinfo("localhost", 80)


@pytest.mark.asyncio
async def test_a_real_subprocess_exec_is_blocked() -> None:
    """The form that actually reaches `voice-tts`, via `deliver`'s runner."""
    with pytest.raises(RealSideEffectBlocked, match="patch the seam"):
        await asyncio.create_subprocess_exec("true")


def test_os_system_is_blocked() -> None:
    import os

    with pytest.raises(RealSideEffectBlocked, match="patch the seam"):
        os.system("true")


# --- (ii) a broad `except` cannot swallow it ----------------------------------


def test_a_broad_except_cannot_swallow_the_block() -> None:
    """(ii): `RealSideEffectBlocked` must derive from `BaseException`.

    T5.3's incident was an `except Exception` hiding a real failure, so a guard
    that `except Exception` can swallow is a guard a production bug could switch
    off. Asserting the MRO is how this test fails if someone re-parents the
    exception to `Exception` for convenience.
    """
    assert issubclass(RealSideEffectBlocked, BaseException)
    assert not issubclass(RealSideEffectBlocked, Exception)


def test_a_swallowed_block_still_fails_the_test() -> None:
    """(ii)'s second layer: the record list survives any `except`.

    Even `except BaseException` cannot stop the teardown, because the guard
    records the attempt before raising.
    """
    import socket

    # The `pass` is the point of the test: even a swallowed block must still fail.
    try:
        socket.socket().connect(("127.0.0.1", 1))
    except BaseException:  # noqa - swallowing on purpose; see the assertion
        pass

    assert blocked, "a swallowed block must still be recorded for teardown"


def test_a_cleared_record_still_fails_the_test() -> None:
    """Layer 2 must survive a `blocked.clear()`, not just a swallowed raise.

    `blocked` is an importable module global, so a test that catches the block can
    empty the list on its way past — which made the record layer defeatable. The
    counter is the answer: it cannot be cleared from outside conftest.
    """
    import socket

    from conftest import _blocked_now

    try:
        socket.socket().connect(("127.0.0.1", 1))
    except BaseException:  # noqa - swallowed, then the record is cleared
        blocked.clear()

    assert _blocked_now(), "a swallowed and cleared block must still be reported at teardown"


# --- the marked-live escape hatch --------------------------------------------


def test_a_live_marked_item_is_not_guarded() -> None:
    """`make live` must still run: the `live` marker is the only way through."""
    from conftest import _is_live

    class _Node:
        def __init__(self, markers: tuple[t.Any, ...]) -> None:
            self._markers = markers

        def get_closest_marker(self, name: str) -> t.Any:
            for m in self._markers:
                if m.name == name:
                    return m
            return None

    class _Request:
        def __init__(self, markers: tuple[t.Any, ...]) -> None:
            self.node = _Node(markers)

    assert _is_live(_Request(())) is False
    assert _is_live(_Request((pytest.mark.live,))) is True


def test_the_guard_really_lets_the_live_marker_through() -> None:
    """The fixture must *consult* `_is_live`, not merely define it.

    The first cut installed the guard unconditionally while its docstring
    promised live-marked tests were exempt, and only `make live` surfaced it —
    as `RealSideEffectBlocked` in `test_live.py`. Asserting on the fixture's own
    source is the only thing that catches a guard that stops consulting it.
    """
    import inspect

    from conftest import _guard

    source = inspect.getsource(_guard.__wrapped__)
    assert "_is_live(request)" in source, "the fixture must skip installs when live"
