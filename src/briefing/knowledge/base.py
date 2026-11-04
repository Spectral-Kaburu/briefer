"""Base types and protocol for knowledge source adapters.

Defines the `Passage` dataclass for knowledge content and the `KnowledgeSource`
protocol that all adapters must implement. This module has no I/O - all network
calls are in the adapter implementations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Pattern to remove ANSI escape sequences (ESC [ ... m)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Pattern to remove C0 controls (0x00-0x1f except \t=0x09, \n=0x0a, \r=0x0d)
# and C1 controls (0x7f-0x9f)
# \x00-\x08: NUL through BS (keep TAB \x09)
# \x0a=LF, \x0d=CR: keep
# \x0b-\x0c: VT, FF (skip)
# \x0e-\x1f: SO through US
# \x7f-\x9f: DEL through CISU
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_control_chars(text: str) -> str:
    """Remove C0 controls, C1 controls, and ANSI escape sequences.

    Args:
        text: The text to clean.

    Returns:
        Text with control characters removed, newlines and tabs preserved.
    """
    # First strip ANSI escape sequences (they contain control chars)
    text = _ANSI_ESCAPE_RE.sub("", text)
    # Then strip remaining control characters
    return _CONTROL_RE.sub("", text)


@dataclass
class Passage:
    """A piece of knowledge from a source adapter.

    Attributes:
        text: The main content of the passage (already stripped of HTML and controls).
        title: A short title or heading for the passage.
        url: The source URL (must be https on an allowed host).
        domain: The source domain for deduplication (e.g., "wikipedia.org").
        license: The license of the source content (e.g., "CC BY-SA 4.0").
        author: The author/attribution for the source (e.g., Stack Overflow user).
    """

    text: str
    title: str = ""
    url: str = ""
    domain: str = ""
    license: str = ""
    author: str = ""


@runtime_checkable
class KnowledgeSource(Protocol):
    """Protocol that all knowledge adapters must implement.

    Adapters are responsible for:
    - Never raising (connect error, timeout, non-2xx, bad JSON, unexpected shape
      should all return `[]`)
    - Logging failures at DEBUG level
    - Honouring their configured timeout
    - Sending a generic User-Agent (`briefing/<version>`)
    - Returning text stripped of HTML and control characters
    """

    @property
    def name(self) -> str:
        """The adapter name for the badge (e.g., "WIKI", "SO", "DDGA")."""
        ...

    def badge(self, plain: bool = False) -> str:
        """Return the badge string for this source.

        Args:
            plain: If True, return a plain-text badge (e.g., "wiki").
                  If False, return an emoji badge (e.g., "📖").

        Returns:
            The badge string.
        """
        ...

    async def lookup(self, query: str, *, limit: int = 3) -> list[Passage]:
        """Look up passages matching the query.

        Args:
            query: The search query.
            limit: Maximum number of passages to return.

        Returns:
            List of Passage objects (may be empty on any failure).
        """
        ...
