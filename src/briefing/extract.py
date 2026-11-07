"""Turn extracted page content into a heading plus a short summary.

Default summarisation is **extractive and offline**: pick the best sentences from
the article rather than rewriting them (SPEC section 2). `--llm` (T4.1) replaces
this with an abstractive summary, so this stays the always-available fallback.
"""

from __future__ import annotations

import logging
import re
import typing as t

logger = logging.getLogger(__name__)

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
    # Consent and ad-blocker notices that were being shown as the page's snippet
    # (planetf1's "Using Brave, Firefox strict mode, VPN or privacy extensions?
    # These may block ads automatically." and f1dailybrief's consent banner).
    # Long and specific enough that dropping the whole line costs no prose.
    "block ads",
    "ad blocker",
    "adblock",
    "affiliate disclosure",
    "optional measurement",
    "accept optional",
    "decline optional",
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
# Summary cap as defence in depth (T9.1)
_SUMMARY_CAP_CHARS = 320

# A markdown horizontal rule, whether its own line or a separator mid-line. `=`
# is included because a setext heading underline (`Title` / `====`) is the same
# furniture with a different glyph; `*` and `_` are not, since three of them can
# still be emphasis inside prose.
_RULE = re.compile(r"[-=]{3,}")
# A leading notification/count badge ("0 Search option...", "3 comments").
_BADGE = re.compile(r"^\d+\s+")
# A space wedged before punctuation, which dropping a link element leaves behind.
_PUNCT_SPACE = re.compile(r"\s+([,.;:!?])")

# Bare URLs to drop from text (T9.1 D2)
_BARE_URL = re.compile(r"https?://[^\s]+")

# Link-wrapped images: [![alt](url)](url) - need special handling (T9.1 D2.1)
_LINK_WRAPPED_IMAGE = re.compile(r"\[!\[[^\]]*\]\([^\)]+\)\]\([^\)]+\)")

# D13 cleanup: Bibliography lines and DOI/PMID/ISSN markers from card text
_BIBLIOGRAPHY_LINE = re.compile(r"(?:^|\s)\d{1,2}\.\s+[A-Z][\w'’-]+ [A-Z]{1,3}[,.]")
_DOIPMIDISSN_MARKER = re.compile(
    r"^(?:DOI|PMID|ISSN)\s*[:：]"
)  # Only match lines primarily being a marker
_ESCAPED_BRACKET = re.compile(r"\[\\\[\d+\\\]\]\(")


class Extracted(t.NamedTuple):
    """Heading and short summary for one page."""

    heading: str
    summary: str


def clean_markdown(text: str) -> str:
    """Strip furniture and noise from markdown before extraction (T9.1).

    Removes:
    - Horizontal rules and setext underlines (lines of >=3 dashes/equals)
    - Image markdown (![alt](url)) and link-wrapped images ([![...]...)](...))
    - Bare URLs
    - Navigation bars, cookie banners, and other furniture lines
    - Lines that are mostly non-letters or long runs without spaces
    - Collapses repeated segments (D5): lines appearing 3+ times are furniture

    Leaves fenced code blocks intact (just does not use them to lead a summary).
    Applied before any extraction.
    """
    # First pass: identify and mark repeated lines (D5 - flattened tables)
    # A line appearing 3+ times is considered a table/repeated content, not prose
    line_counts: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            line_counts[stripped] = line_counts.get(stripped, 0) + 1

    # Lines that are repeated 3+ times are furniture
    repeated_lines = {line for line, count in line_counts.items() if count >= 3}

    lines: list[str] = []
    raw_lines = text.splitlines()
    for idx, line in enumerate(raw_lines):
        # Skip setext underlines / horizontal rules themselves
        if _RULE.fullmatch(line):
            continue
        # Check if this line is ABOVE a rule (setext heading) - skip it
        if idx + 1 < len(raw_lines) and _RULE.fullmatch(raw_lines[idx + 1]):
            continue
        # Skip fenced code blocks
        if line.strip().startswith(("```", "~~~")):
            continue
        # Check for link-only lines BEFORE any processing (D3, T8.7)
        # Lines that are just links like "[Skip to content](url) [Home](url)" are nav
        if _LINK_ONLY.match(line):
            continue
        # Strip link-wrapped images (T9.1 D2.1) first
        if _LINK_WRAPPED_IMAGE.search(line):
            line = _LINK_WRAPPED_IMAGE.sub("", line)
        # Strip image markdown lines entirely (lines starting with ![])
        if line.strip().startswith("!["):
            continue
        # Unwrap remaining markdown links FIRST, then strip URLs
        # Order matters: unwrap [text](url) -> text, then strip any remaining bare URLs
        line = _MARKDOWN_LINK.sub(r"\1", line)
        # Strip bare URLs (after link unwrapping)
        line = _BARE_URL.sub("", line)
        # Normalize whitespace FIRST (so furniture prefix matching works with any spacing)
        line = " ".join(line.split())
        # Strip leading furniture prefixes
        line = _strip_leading_furniture(line)
        # Skip if empty
        if not line.strip():
            continue
        # Skip lines that are repeated (flattened table pattern - D5)
        if line in repeated_lines:
            continue
        # Skip lines that are mostly non-letters (< 50% letters/spaces)
        letter_ratio = sum(c.isalpha() or c.isspace() for c in line) / len(line)
        if letter_ratio < 0.5:
            continue
        # D13: Strip bibliography lines and DOI/PMID/ISSN markers from cards
        if _BIBLIOGRAPHY_LINE.search(line):
            continue
        if _DOIPMIDISSN_MARKER.search(line):
            continue
        if _ESCAPED_BRACKET.search(line):
            continue
        # Skip lines that contain only furniture markers
        low = line.lower()
        if any(marker in low for marker in _BOILERPLATE_LINE_MARKERS):
            continue
        # Skip lines that start with furniture prefixes
        if any(low.startswith(prefix) for prefix in _BOILERPLATE_PREFIXES):
            continue
        lines.append(line)

    return "\n\n".join(lines)


def extract(
    markdown: str,
    title: str | None = None,
    sentences: int = 2,
    url: str = "",
    search_title: str = "",
    query: str | None = None,
) -> Extracted:
    """Extract a heading and an extractive summary from page content.

    `title` is the metadata title from the page, which wins over first h1 when
    relevant. `url` and `search_title` are used for heading sanity rules (T9.2).

    `query` enables query-aware extraction (T9.3): with a query, returns the
    best 1-2 chunks that answer the question, capped at 320 characters.

    A 320-character cap is applied as defense in depth (T9.1).
    """
    # Apply clean_markdown before extraction (T9.1)
    cleaned = clean_markdown(markdown)

    heading, _ = _select_heading(cleaned, title or "", url, search_title)

    if query:
        # Query-aware extraction: rank chunks by relevance
        from briefing.relevance import rank_chunks, split_into_chunks

        chunks = split_into_chunks(cleaned)
        if chunks:
            scored = rank_chunks(chunks, query, url)
            # Take top 1-2 chunks within the cap
            selected_texts = []
            for chunk, score in scored:
                if score > 0:  # Only consider chunks with some relevance
                    selected_texts.append(chunk)
                    if len(selected_texts) >= 2:
                        break

            if selected_texts:
                summary = " ".join(selected_texts)
                # Apply cap as defense in depth (same 320 char cap from T9.1)
                if len(summary) > _SUMMARY_CAP_CHARS:
                    cut = summary[:_SUMMARY_CAP_CHARS].rsplit(None, 1)[0]
                    summary = cut + "..."
                return Extracted(heading=heading, summary=summary)

    # Default extractive summarisation (without query)
    summary = _summarise(cleaned, sentences)
    # Apply cap as defense in depth
    if len(summary) > _SUMMARY_CAP_CHARS:
        # Cut at word boundary with ellipsis
        cut = summary[:_SUMMARY_CAP_CHARS].rsplit(None, 1)[0]
        summary = cut + "..."
    return Extracted(heading=heading, summary=summary)


def _first_h1(markdown: str) -> str:
    """Heading from the first markdown h1, when metadata carried no title."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return _clean(stripped[2:])[:_HEADING_CHARS]
    return ""


def _select_heading(
    markdown: str,
    metadata_title: str,
    url: str,
    search_title: str,
) -> tuple[str, str]:
    """Select the best heading using a sanity rule based on word overlap.

    Candidates are evaluated for word overlap with URL path and search title:
    1. Candidates WITH overlap are considered relevant
    2. Among relevant candidates, pick the best-scored one
    3. Priority order: metadata_title > first_h1 > search_title
    4. If no candidate has overlap, fall back to empty string

    Returns (heading, source_name) tuple for debug logging.
    """
    # Extract URL path for comparison (remove domain, keep words)
    url_path = _extract_url_path(url)

    # Build list of (heading, source) tuples with their overlap scores
    candidates: list[tuple[str, str, float]] = []

    if metadata_title:
        score = _heading_overlap_score(metadata_title, url_path, search_title)
        candidates.append((metadata_title, "metadata_title", score))

    h1 = _first_h1(markdown)
    if h1:
        score = _heading_overlap_score(h1, url_path, search_title)
        candidates.append((h1, "first_h1", score))

    if search_title:
        score = _heading_overlap_score(search_title, url_path, search_title)
        candidates.append((search_title, "search_title", score))

    if not candidates:
        return "", "none"

    # Determine minimum score for "having overlap" - a threshold to distinguish
    # "some overlap" from "no meaningful overlap"
    # A score > 0 means there's at least one word in common
    MIN_OVERLAP_SCORE = 1.0  # At least one overlapping word

    # First, try to find candidates with meaningful overlap
    relevant = [(h, s, sc) for h, s, sc in candidates if sc >= MIN_OVERLAP_SCORE]

    if relevant:
        # Pick the best-relevant candidate, with priority tie-breaker
        # Priority boost: ensures metadata_title > first_h1 > search_title
        # Even if search_title has more word overlap, metadata_title is preferred
        priority_boost = {"metadata_title": 1000.0, "first_h1": 100.0, "search_title": 0.0}
        scored = [(h, s, sc + priority_boost.get(s, 0)) for h, s, sc in relevant]
        scored.sort(key=lambda x: -x[2])
        best_heading, best_source = scored[0][0], scored[0][1]
        best_score = scored[0][2] - priority_boost.get(scored[0][1], 0)
    else:
        # No candidate has overlap - pick the one with highest raw score
        # (could be search_title as last resort)
        candidates.sort(key=lambda x: -x[2])
        best_heading, best_source, best_score = candidates[0]

    # Log the heading selection at DEBUG level
    logger.debug(
        "Heading selected: %r from %s (score=%d, candidates=%s)",
        best_heading,
        best_source,
        best_score,
        [(h, s, sc) for h, s, sc in candidates],
    )

    return best_heading, best_source


def _extract_url_path(url: str) -> str:
    """Extract the meaningful words from a URL path for comparison."""
    if not url:
        return ""
    # Remove scheme and domain, get path
    # e.g., https://python.org/downloads/3.11/ -> "downloads 3 11"
    import re

    # Strip protocol and domain
    path = re.sub(r"^https?://[^/]+/", "", url)
    # Remove trailing slash and file extensions
    path = re.sub(r"\.(html?|php|asp|aspx|json)$", "", path)
    # Replace separators with spaces
    path = re.sub(r"[/_\-.]", " ", path)
    # Remove numbers from version strings but keep them as words for comparison
    path = re.sub(r"\d+", " ", path)
    # Return lowercase words
    return " ".join(path.lower().split())


def _heading_overlap_score(heading: str, url_path: str, search_title: str) -> int:
    """Score a heading by word overlap with URL path and search title.

    Higher score = more overlap = more relevant.
    Uses word matching with stop word filtering.
    """
    if not heading:
        return 0

    # Stop words to ignore
    stop_words = {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "to",
        "and",
        "or",
        "with",
        "by",
        "at",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "as",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "but",
        "not",
        "no",
        "just",
        "only",
        "also",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
    }

    # Extract words from heading (lowercase, no punctuation)
    heading_words = set(re.findall(r"[a-zA-Z]+", heading.lower()))
    heading_words -= stop_words

    # Score from URL path overlap
    url_words = set(url_path.split()) if url_path else set()
    path_score = len(heading_words & url_words) * 10

    # Score from search title overlap
    search_words = set(re.findall(r"[a-zA-Z]+", search_title.lower())) if search_title else set()
    search_words -= stop_words
    search_score = len(heading_words & search_words) * 10

    # Bonus for having any overlap at all
    total_overlap = len(heading_words & (url_words | search_words))
    if total_overlap > 0:
        total_overlap += 5  # Bonus for having any overlap

    return path_score + search_score + total_overlap


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
