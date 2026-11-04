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
    "skip to article",
    # Consent banners and collection widgets arrive as one unbroken line of UI
    # fragments, which interleave too freely for prefix-peeling to finish. These
    # are long enough that they are near-impossible in prose, so the line is
    # dropped whole: the summary empties and the UI falls back to the search
    # snippet, which is the honest result for such a page.
    "opens in a new window",
    "opens an external website",
    "close this consent banner",
    "manage preferences",
    "storage preferences",
    "you are the owner of this collection",
    "you have permission to edit this collection",
    "edit collection",
    "toggle navigation",
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "follow us",
    "related articles",
    "most read",
    "also read",
)

# Chrome that shows up as a *prefix* on a line whose tail is the article, and is
# therefore cut rather than dropped with the line. These are kept apart from the
# markers above because several are ordinary English: "close by" as a whole-line
# match would take "the hospital is close by the river" with it, while cutting a
# leading "Close By" costs at most two words of a sentence that was already UI
# furniture. Ordered longest-first so "skip to article" wins over "skip to".
_BOILERPLATE_PREFIXES = (
    "return to homepage",
    "skip to main content",
    "skip to article",
    "back to top",
    "top stories",
    "skip to content",
    "close by",
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

# A markdown horizontal rule, whether its own line or a separator mid-line. `=`
# is included because a setext heading underline (`Title` / `====`) is the same
# furniture with a different glyph; `*` and `_` are not, since three of them can
# still be emphasis inside prose.
_RULE = re.compile(r"[-=]{3,}")
# A leading notification/count badge ("0 Search option...", "3 comments").
_BADGE = re.compile(r"^\d+\s+")
# A space wedged before punctuation, which dropping a link element leaves behind.
_PUNCT_SPACE = re.compile(r"\s+([,.;:!?])")


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


def _split_at_rule(line: str) -> str:
    """Keep the part of a line that survives a horizontal rule.

    A rule mid-line means the left side was a header block and the right side is
    the body: "Return to Homepage Top Stories: ------------ The clinic returned".
    When the rule is the whole line nothing is left, so `""` says "furniture".
    """
    parts = [p.strip() for p in _RULE.split(line) if p.strip()]
    return max(parts, key=len) if parts else ""


def _strip_leading_furniture(line: str) -> str:
    """Peel chrome prefixes off the front of a line, leaving the prose.

    "Skip to Article Close By Millions of..." is two UI phrases with the article
    behind them, so this repeats while the line still opens with a known prefix
    rather than testing the line as a whole — which would drop the sentence too.
    """
    low = line.lower()
    for prefix in _BOILERPLATE_PREFIXES:  # already longest-first
        if low.startswith(prefix):
            rest = line[len(prefix) :].lstrip(" \t:;,.-")
            # The lstrip can empty a line that was nothing but chrome; returning
            # the original then lets the caller keep deciding what it is.
            return _strip_leading_furniture(rest) if rest else ""
    return line


def _prose_lines(markdown: str) -> list[str]:
    """Content lines with markdown scaffolding and furniture removed.

    Only *furniture* is dropped here. A short line is kept: whether it is a kicker
    or real content is a whole-document question, answered by `_skip_kickers`.
    """
    lines: list[str] = []
    raw_lines = [" ".join(raw.split()) for raw in markdown.splitlines()]
    for index, line in enumerate(raw_lines):
        if not line:
            continue
        if line.startswith("#"):  # a heading is not body prose
            continue
        if line.startswith(("```", "~~~", ">", "- ", "* ", "|", "![")):
            continue
        # A line sitting directly above a rule is that rule's setext heading, so
        # the text above is a title and not body prose.
        following = raw_lines[index + 1] if index + 1 < len(raw_lines) else ""
        if _RULE.fullmatch(following):
            continue
        # Cleaned before the furniture checks, not after: a11y elements arrive as
        # links ("[Skip to main content](url)"), and a prefix test against the
        # raw markdown sees a "[" and matches nothing.
        line = _clean(line)
        line = _BADGE.sub("", line)
        line = _split_at_rule(line)
        if not line:
            continue
        line = _strip_leading_furniture(line)
        if not line:
            continue
        low = line.lower()
        if any(marker in low for marker in _BOILERPLATE_LINE_MARKERS):
            continue
        if _is_nav_bar(line):
            continue
        cleaned = line
        if not cleaned:
            continue
        lines.append(cleaned)
    return lines


def _clean(line: str) -> str:
    """Strip markdown link scaffolding and emphasis from a line.

    Two spacing normalisations follow, because a markdown page carries UI
    spacing into its own text: a space before punctuation ("commission , Bodum",
    what removing a link leaves behind) and doubled spaces where an element was.
    """
    line = _TAG.sub("", line)
    line = _MARKDOWN_LINK.sub(r"\1", line)
    line = _EMPHASIS.sub("", line)
    line = _PUNCT_SPACE.sub(r"\1", line)
    return " ".join(line.split())


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
