"""Turn extracted page content into a heading plus a short summary.

Default summarisation is **extractive and offline**: pick the best sentences from
the article rather than rewriting them (SPEC section 2). `--llm` (T4.1) replaces
this with an abstractive summary, so this stays the always-available fallback.
"""

from __future__ import annotations

import re
import typing as t

# Lines that are pure navigation, cookie or paywall furniture rather than prose.
_BOILERPLATE_LINE_MARKERS = (
    "accept all cookies",
    "accept cookies",
    "cookie settings",
    "cookie policy",
    "we use cookies",
    "sign in",
    "log in",
    "subscribe",
    "newsletter",
    "advertisement",
    "sponsored",
    "share this",
    "skip to content",
    "toggle navigation",
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "follow us",
    "related articles",
    "most read",
    "also read",
)

# A line is furniture if it is one of these, or if it is mostly links/pipe nav.
_NAV_SEPARATOR = re.compile(r"\s*\|\s*")
_LINK_ONLY = re.compile(r"^(\[([^\]]*)\]\([^)]*\)\s*)+$")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"\*\*|__|\*|_")
_TAG = re.compile(r"<[^>]*>")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")

# An opener shorter than this is treated as a kicker, not substance.
_MIN_SUBSTANCE_CHARS = 40
# A pipe-separated segment at or under this length is a nav item, not prose.
_NAV_SEGMENT_CHARS = 12
# A line needs at least this many pipe-separated segments to count as a nav bar.
_NAV_MIN_SEGMENTS = 3
_HEADING_CHARS = 200


class Extracted(t.NamedTuple):
    """Heading and short summary for one page."""

    heading: str
    summary: str


def extract(
    markdown: str,
    title: str | None = None,
    sentences: int = 2,
) -> Extracted:
    """Extract a heading and an extractive summary from page content.

    `title` is the metadata title, which wins over the first markdown h1. Both can
    be absent, in which case the heading is empty and the summary stands alone.
    """
    heading = _clean(title or "") or _first_h1(markdown)
    summary = _summarise(markdown, sentences)
    return Extracted(heading=heading, summary=summary)


def _first_h1(markdown: str) -> str:
    """Heading from the first markdown h1, when metadata carried no title."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return _clean(stripped[2:])[:_HEADING_CHARS]
    return ""


def _summarise(markdown: str, sentences: int) -> str:
    """Pick the best `sentences` sentences from the article body.

    Boilerplate lines are dropped first, then sentences are taken from the start
    of what remains. A page whose first line is a short kicker gets the kicker
    skipped so the summary budget is spent on substance.
    """
    body = _prose_lines(markdown)
    candidates: list[str] = []
    for line in body:
        candidates.extend(_split_sentences(line))

    # Merge fragments left by sentence splitting so we do not quote half a line.
    merged = _merge_fragments(candidates)
    return " ".join(_skip_kickers(merged, sentences))


def _is_nav_bar(line: str) -> bool:
    """A pipe-separated run of several short segments is navigation, not prose.

    "Home | World | Business | Sport | Opinion" carries no links, so a link-based
    test is not enough. The signal is the segment count: prose rarely has five or
    more pipe-separated fragments, and a nav bar almost always does.
    """
    if not _NAV_SEPARATOR.search(line):
        return False
    segments = [s.strip() for s in _NAV_SEPARATOR.split(line) if s.strip()]
    if len(segments) < _NAV_MIN_SEGMENTS:
        return False
    # Fragments longer than a nav item mean this is a real list in prose.
    return all(len(_MARKDOWN_LINK.sub(r"\1", s)) <= _NAV_SEGMENT_CHARS for s in segments)


def _prose_lines(markdown: str) -> list[str]:
    """Content lines with markdown scaffolding and furniture removed.

    Only *furniture* is dropped here. A short line is kept: whether it is a kicker
    or real content is a whole-document question, answered by `_skip_kickers`.
    """
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):  # a heading is not body prose
            continue
        if line.startswith(("```", "~~~", ">", "- ", "* ", "|", "![")):
            continue
        low = line.lower()
        if any(marker in low for marker in _BOILERPLATE_LINE_MARKERS):
            continue
        if _is_nav_bar(line):
            continue
        cleaned = _clean(line)
        if not cleaned:
            continue
        lines.append(cleaned)
    return lines


def _clean(line: str) -> str:
    """Strip markdown link scaffolding and emphasis from a line."""
    line = _TAG.sub("", line)
    line = _MARKDOWN_LINK.sub(r"\1", line)
    line = _EMPHASIS.sub("", line)
    return line.strip()


def _split_sentences(line: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(line) if p.strip()]
    return parts or ([line.strip()] if line.strip() else [])


def _merge_fragments(sentences: list[str]) -> list[str]:
    """Rejoin sentences a split broke, so the summary never quotes half a line."""
    merged: list[str] = []
    for sentence in sentences:
        if merged and not merged[-1].endswith((".", "!", "?", '."', ".'")):
            merged[-1] = f"{merged[-1]} {sentence}"
            continue
        merged.append(sentence)
    return merged


def _pick_sentences(sentences: list[str], count: int) -> list[str]:
    """First `count` sentences, skipping an opener that is only a kicker.

    A one-line teaser ("Some opening paragraph that is short.") carries no
    substance, so when a longer sentence follows we spend the summary budget on
    that instead. If the *only* sentences are short, they are used as-is.
    """
    if count <= 0:
        return []
    picked: list[str] = []
    for sentence in sentences:
        picked.append(sentence)
        if len(picked) >= count:
            break
    return picked


def _skip_kickers(sentences: list[str], count: int) -> list[str]:
    """Reorder so a short opener does not consume the budget when prose exists.

    Only applies when there is something longer to use; an article that is
    genuinely all short lines should not be reduced to nothing.
    """
    substantial = [s for s in sentences if len(s) >= _MIN_SUBSTANCE_CHARS]
    if not substantial:
        return sentences
    ordered = substantial + [s for s in sentences if len(s) < _MIN_SUBSTANCE_CHARS]
    return _pick_sentences(ordered, count)
