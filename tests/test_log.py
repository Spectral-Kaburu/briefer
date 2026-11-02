"""Tests for log.py (T6.2): the log file, `--verbose`, and the redaction rule.

The redaction rule is the point of the module, so it is tested directly first —
a filter that never sees a secret cannot be proven by asserting its absence — and
then the CLI is held to the same promise at its boundary.
"""

from __future__ import annotations

import logging
import sys
import typing as t
from pathlib import Path

from briefing.log import SecretFilter, configure


def test_a_secret_is_masked_out_of_the_message() -> None:
    """The one rule CLAUDE.md states for logs: the key must not survive."""
    record = logging.LogRecord(
        name="briefing",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="using key %s for the api",
        args=("fc-secret-key-123",),
        exc_info=None,
    )
    SecretFilter(["fc-secret-key-123"]).filter(record)
    assert record.getMessage() == "using key *** for the api"


def test_a_secret_is_masked_out_of_the_format_string_too() -> None:
    """A key pasted into the format itself is the same leak as an argument."""
    record = logging.LogRecord(
        name="briefing",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="the key is fc-secret-key-123, keep it quiet",
        args=None,
        exc_info=None,
    )
    SecretFilter(["fc-secret-key-123"]).filter(record)
    assert "fc-secret-key-123" not in record.getMessage()
    assert "***" in record.getMessage()


def test_a_message_with_no_secret_is_left_alone() -> None:
    """Masking is not munging: ordinary lines must come through untouched."""
    record = logging.LogRecord(
        name="briefing",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="routed https://nation.africa/x -> local",
        args=None,
        exc_info=None,
    )
    SecretFilter(["fc-secret-key-123"]).filter(record)
    assert record.getMessage() == "routed https://nation.africa/x -> local"


def test_a_short_secret_is_not_used_as_a_mask() -> None:
    """A two-character "secret" would eat every other word in the log.

    `shortcut: ignores secrets under 4 characters, because a real key is longer;
    upgrade to hashing the env-var name instead of the value if that stops
    holding.`
    """
    record = logging.LogRecord(
        name="briefing",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="a ok b",
        args=None,
        exc_info=None,
    )
    SecretFilter(["ok", ""]).filter(record)
    assert record.getMessage() == "a ok b"


def test_configure_writes_the_log_file_and_returns_one_logger(tmp_path: Path) -> None:
    logger = configure(log_file=tmp_path / "briefing.log", verbose=False)
    assert logger is configure(log_file=tmp_path / "briefing.log", verbose=False)
    logger.info("hello %s", "world")
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "hello world" in text
    assert "briefing" in text


def test_the_log_file_gets_info_but_debug_stays_out(tmp_path: Path) -> None:
    """The file is for a normal run's decisions; debug noise needs --verbose."""
    logger = configure(log_file=tmp_path / "briefing.log", verbose=False)
    logger.info("an info line")
    logger.debug("a debug line")
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "an info line" in text
    assert "a debug line" not in text


def test_verbose_lowers_the_file_too(tmp_path: Path) -> None:
    """An unattended run has no stderr afterwards, so the file must carry it."""
    logger = configure(log_file=tmp_path / "briefing.log", verbose=True)
    logger.debug("a debug line worth keeping")
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "a debug line worth keeping" in text


def test_the_log_file_never_receives_the_secret(tmp_path: Path) -> None:
    logger = configure(
        log_file=tmp_path / "briefing.log", verbose=False, secrets=["fc-secret-key-123"]
    )
    logger.info("api key configured: %s", "fc-secret-key-123")
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "fc-secret-key-123" not in text
    assert "***" in text


def test_verbose_adds_a_stderr_handler_at_debug_level(tmp_path: Path, capsys: t.Any) -> None:
    configure(
        log_file=tmp_path / "briefing.log", verbose=True, logger_name="briefing.verbose-probe"
    )
    logger = logging.getLogger("briefing.verbose-probe")
    logger.debug("a debug line on stderr")
    captured = capsys.readouterr()
    assert "a debug line on stderr" in captured.err
    assert "a debug line on stderr" not in captured.out


def test_without_verbose_debug_reaches_the_terminal_not_at_all(
    tmp_path: Path, capsys: t.Any
) -> None:
    configure(log_file=tmp_path / "briefing.log", verbose=False, logger_name="briefing.quiet-probe")
    logging.getLogger("briefing.quiet-probe").debug("a debug line that stays hidden")
    captured = capsys.readouterr()
    assert "a debug line that stays hidden" not in captured.err
    assert "a debug line that stays hidden" not in captured.out


def test_configure_creates_the_missing_state_dir(tmp_path: Path) -> None:
    """Same convention as the whitelist and the api-call log: mkdir at write."""
    log_file = tmp_path / "nested" / "deeper" / "briefing.log"
    logger = configure(log_file=log_file, verbose=False)
    logger.info("written somewhere new")
    assert log_file.exists()


def test_a_traceback_carrying_the_secret_is_masked(tmp_path: Path) -> None:
    """A key inside an exception is the same leak as one inside a message.

    `exc_info` renders separately from `msg`/`args`, so masking those two is not
    enough — the filter has to fold the masked traceback in itself.
    """
    logger = configure(
        log_file=tmp_path / "briefing.log", verbose=False, secrets=["fc-secret-key-123"]
    )
    try:
        raise RuntimeError("request failed with key fc-secret-key-123")
    except RuntimeError:
        logger.exception("scrape failed")
    text = (tmp_path / "briefing.log").read_text(encoding="utf-8")
    assert "fc-secret-key-123" not in text
    assert "***" in text
    # The reason it was worth logging survives, which is the point of a log.
    assert "scrape failed" in text
    assert "RuntimeError" in text


def test_reconfiguring_closes_the_file_handler_it_replaced(tmp_path: Path) -> None:
    """Two configure calls must not hold the first file open forever.

    Observable as a closed stream, which is the only way a test can see the
    descriptor leak an unclosed handler leaves behind. Only `FileHandler` is
    expected to close: closing `sys.stderr` because a previous test ran
    `--verbose` would break every later test in the process.
    """
    logger = configure(log_file=tmp_path / "one.log", verbose=False)
    replaced = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    configure(log_file=tmp_path / "two.log", verbose=False)
    assert replaced
    # A closed FileHandler drops its stream reference entirely.
    assert all(handler.stream is None or handler.stream.closed for handler in replaced)


def test_reconfiguring_leaves_the_real_stderr_writable(tmp_path: Path, capsys: t.Any) -> None:
    """A replaced StreamHandler must not take the process's stderr with it.

    The handler was *given* stderr rather than owning it, so detaching it must
    leave the shared stream open — otherwise every `_fail` hint the CLI prints
    after a reconfigure raises instead of reaching the user.
    """
    configure(log_file=tmp_path / "briefing.log", verbose=True)
    logger = logging.getLogger("briefing")
    stream_handlers = [
        handler
        for handler in logger.handlers
        # A FileHandler *is* a StreamHandler, so exclude it or the assertion
        # reads the file handler's (correctly) closed stream.
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    configure(log_file=tmp_path / "briefing.log", verbose=False)
    assert stream_handlers
    assert not stream_handlers[0].stream.closed

    sys.stderr.write("after a verbose-then-quiet reconfigure\n")
    sys.stderr.flush()
    assert "after a verbose-then-quiet reconfigure" in capsys.readouterr().err
