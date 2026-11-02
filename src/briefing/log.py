"""Diagnostic logging for `briefing` (T6.2).

One module so the redaction rule lives in exactly one place. The run's decisions
are written to `paths.log_file` at INFO, so the file exists after any run and
`briefing status` can name it; `--verbose` lowers both sinks to DEBUG, and adds
stderr, so the detail survives the process for an unattended run.

CLAUDE.md forbids secrets in logs, and the key is the one value that could get
there, so every sink carries a `SecretFilter` holding that key's value. The
filter is deliberately blind: it masks what it was given and nothing else, which
is why the caller — which is the only place the key is resolved — passes it in.
"""

from __future__ import annotations

import logging
import sys
import traceback
import typing as t
from pathlib import Path

#: The root logger name; child modules get `briefing.<module>` under it.
ROOT = "briefing"

#: What a masked secret looks like in the log.
MASK = "***"

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_DATEFMT = "%H:%M:%S"

#: Below this a "secret" is more likely an ordinary word than a key, and masking
#: it would corrupt every line of the log to protect nothing.
_MIN_SECRET_LEN = 4


def _mask(text: str, secrets: t.Collection[str]) -> str:
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, MASK)
    return text


class SecretFilter(logging.Filter):
    """Masks each secret out of a record before any handler writes it.

    Attached to handlers rather than to a logger so it covers records from the
    child module loggers too, which propagate up to these handlers.
    """

    def __init__(self, secrets: t.Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secrets if len(s) >= _MIN_SECRET_LEN)

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record in place; always let it through."""
        if isinstance(record.msg, str):
            record.msg = _mask(record.msg, self._secrets)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: _mask(value, self._secrets) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    _mask(value, self._secrets) if isinstance(value, str) else value
                    for value in record.args
                )
        if record.exc_info:
            # A traceback renders separately from the message, so masking the
            # message alone leaves the secret printed in full. Render it here,
            # masked, and hand the handler the text instead of the exception:
            # `Formatter` prefers a pre-set `exc_text` over re-formatting.
            record.exc_text = _mask(
                "".join(traceback.format_exception(*record.exc_info)), self._secrets
            )
            record.exc_info = None
        return True


def configure(
    *,
    log_file: Path | None = None,
    verbose: bool = False,
    secrets: t.Iterable[str] = (),
    logger_name: str = ROOT,
) -> logging.Logger:
    """Point the `briefing` logger at `log_file`, and at stderr when `verbose`.

    Clears the previous configuration first, so a second call (a test's, or a
    second command in one process) does not append a second set of handlers to
    the same logger and double every line.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        # Detached always, closed only when the handler owns the file it holds:
        # a live FileHandler keeps the old file open until it is collected, and
        # a process that reconfigures (or a test suite calling this hundreds of
        # times) would otherwise run out of descriptors. Closing a StreamHandler
        # would close `sys.stderr` itself, which the handler was only given.
        logger.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()

    redactor = SecretFilter(secrets)
    handlers: list[logging.Handler] = []
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        # The file's level follows the flag: a debug record needs to land
        # somewhere it survives the process, because an unattended `briefing
        # run --verbose` has no stderr left to read afterwards.
        file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        # shortcut: no rotation, so `briefing run --verbose` under a scheduler
        # grows without bound. Only ~a line per URL at DEBUG and verbose is
        # opt-in, so this is ~300KB/year; upgrade to RotatingFileHandler when
        # the owner actually wants a scheduled verbose run.
        handlers.append(file_handler)
    if verbose:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.DEBUG)
        handlers.append(stream)

    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        handler.addFilter(redactor)
        logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """The logger for a module, under the configured root."""
    return logging.getLogger(name)
