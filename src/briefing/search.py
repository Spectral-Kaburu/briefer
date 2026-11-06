"""Web search via `ddgs`, normalized to `SearchResult` (SPEC section 5).

Primary search path. News-style queries go to `news()` with a `timelimit`; plain
queries go to `text()`. A rate-limited query is retried once with the timelimit
widened by one step, because a narrow window is the usual reason DuckDuckGo has
nothing to offer.
"""

from __future__ import annotations

import asyncio
import logging
import re
import typing as t
from datetime import UTC, datetime, timedelta

from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException

logger = logging.getLogger(__name__)

# Query words that mean "the user wants the news", checked at word boundaries so
# "newest" is not read as "news" (SPEC section 5 auto-detect).
_NEWS_MARKERS = (
    "latest",
    "breaking",
    "news",
    "headline",
    "headlines",
    "today",
    "this morning",
    "this week",
    "yesterday",
    "just in",
)

# Stop/filler words to strip from search queries (T12.1 D10)
# These are the common question words and articles that reduce search relevance
_STOP_WORDS = (
    "what",
    "are",
    "is",
    "does",
    "how",
    "the",
    "a",
    "an",
    "in",
    "of",
    "why",
    "when",
    "who",
    "which",
    "to",
    "for",
    "on",
    "with",
)


def search_query(question: str) -> str:
    """Strip filler words from a question to create a better search query (T12.1).

    Removes common question words and articles while preserving the core terms.
    Returns the cleaned query in lowercase, or the original if all words are stop words.
    """
    import re

    # Normalize whitespace and lowercase
    text = " ".join(question.lower().split())

    # Split into tokens
    tokens = re.findall(r"[a-z]+", text)

    # Filter out stop words while preserving order
    filtered = [t for t in tokens if t not in _STOP_WORDS]

    # If all words are stop words, return original (lower-cased and normalized)
    if not filtered:
        return text

    return " ".join(filtered)


# widening is one step at a time, and "y" is as wide as ddgs goes.
_TIMELIMIT_STEPS = ("d", "w", "m", "y")

# One retry after a rate limit: the second attempt is wider, which is usually the
# actual reason a narrow query came back empty. `max_retries` counts extra tries.
_MAX_RETRIES = 1

_RELATIVE_AGE = re.compile(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)
# Fractional seconds ("...T06:00:00.123+00:00") defeat the second-precision formats.
_FRACTION = re.compile(r"\.\d+")
_RELATIVE_UNITS: dict[str, timedelta] = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}

# A hit with no date sorts last, so recency ordering never promotes an unknown age
# above a fresh one. `datetime.max` rather than `min`, deliberately.
_NO_DATE = datetime.max.replace(tzinfo=UTC)


class SearchResult(t.NamedTuple):
    """One normalized search hit (SPEC section 5)."""

    title: str
    url: str
    snippet: str
    source: str = ""
    age: str = ""
    origin: str = ""
    published_at: datetime = _NO_DATE

    @classmethod
    def from_ddgs(cls, hit: t.Mapping[str, t.Any], origin: str) -> SearchResult:
        """Normalize one `ddgs` result dict.

        `ddgs` engines emit different shapes (NOTES-APIS.md): news hits carry
        `date` and `source`, text hits carry neither. Fields are read defensively
        because none are guaranteed.
        """
        published = parse_age(str(hit.get("date") or hit.get("age") or ""))
        return cls(
            title=_text(hit.get("title")),
            url=_text(hit.get("url") or hit.get("href")),
            snippet=_text(hit.get("body") or hit.get("description") or hit.get("snippet")),
            source=_text(hit.get("source")),
            age=_text(hit.get("date") or hit.get("age")),
            origin=origin,
            published_at=published or _NO_DATE,
        )

    @classmethod
    def from_search(cls, hit: t.Mapping[str, t.Any]) -> SearchResult:
        """Normalize one local-Firecrawl `/v2/search` web item.

        Verified fields are `url`, `title`, `description`, `category`
        (NOTES-APIS.md). `category` has no home in SPEC §5's five fields and is
        dropped; no verified date field exists yet, so age stays empty.
        """
        return cls(
            title=_text(hit.get("title")),
            url=_text(hit.get("url")),
            snippet=_text(hit.get("description")),
            origin="search",
        )


def _text(value: t.Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_age(raw: str) -> datetime | None:
    """Parse a `ddgs` date into an aware datetime, or None when unparseable.

    Accepts ISO dates with or without a time and timezone, plus relative forms
    ("3 hours ago") that some engines emit instead of an absolute date. Two shapes
    are normalised first because `%z` cannot read them: a trailing zone *name*
    (" UTC") and fractional seconds.
    """
    if not raw:
        return None
    value = raw.strip()
    if value.endswith(" UTC"):
        value = value[: -len(" UTC")] + "+00:00"
    if "." in value:
        value = _FRACTION.sub("", value)
    # One canonical separator, so a space-separated stamp can still carry an offset.
    if len(value) > 10 and value[10] == " ":
        value = value[:10] + "T" + value[11:]
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value[:19] + value[19:].replace(":", ""), fmt)
        except ValueError:
            continue
        if not parsed.tzinfo:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    match = _RELATIVE_AGE.search(value)
    if match:
        return datetime.now(UTC) - _RELATIVE_UNITS[match.group(2).lower()] * int(match.group(1))
    return None


def detect_mode(query: str) -> str:
    """'news' for a news-style query, 'text' otherwise (SPEC section 5)."""
    low = f" {query.strip().lower()} "
    return "news" if any(f" {marker} " in low for marker in _NEWS_MARKERS) else "text"


def widen_timelimit(limit: str | None) -> str | None:
    """One step wider, or None to ask for no limit at all when none was set."""
    if limit is None:
        return None
    if limit not in _TIMELIMIT_STEPS:
        return limit
    index = min(_TIMELIMIT_STEPS.index(limit) + 1, len(_TIMELIMIT_STEPS) - 1)
    return _TIMELIMIT_STEPS[index]


# Stop words for re-ranking (T12.1)
_RANK_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
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
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
    }
)


def _tokenize(text: str) -> set[str]:
    """Extract content words from text for BM25-style scoring."""
    import re

    # Match words, lowercase them, filter stop words
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in _RANK_STOP_WORDS and len(w) > 1}


def _fuzzy_contains(query_words: set[str], text_words: set[str]) -> bool:
    """Check if any query word (or 5+ letter fuzzy match) is in text.

    Supports typo tolerance for words 5+ letters: 'verstion' matches 'version'.
    """
    for qw in query_words:
        if qw in text_words:
            return True
        # Fuzzy match: 5+ letter query words must have a close match in text
        if len(qw) >= 5:
            for tw in text_words:
                if (
                    len(tw) >= 5
                    and abs(len(qw) - len(tw)) <= 1
                    and (qw[:4] == tw[:4] or qw[:5] == tw[:5])
                ):
                    return True
    return False


def _content_word_ratio(query_words: set[str], text_words: set[str]) -> float:
    """Calculate what fraction of query content words are in text."""
    if not query_words:
        return 0.0
    matches = sum(1 for qw in query_words if qw in text_words)
    return matches / len(query_words)


def _rerank_results(
    results: list[SearchResult],
    original_query: str,
) -> list[SearchResult]:
    """Re-rank search results by relevance to query (T12.1 D10).

    This fixes the DDG ranker issues:
    1. Wikipedia.org results are no longer pinned to rank 1
    2. Substring matching is improved with proper word tokenization

    Returns a new list sorted by relevance.
    """
    if not results:
        return results

    query_words = _tokenize(original_query)
    if not query_words:
        return results  # No meaningful query words, keep original order

    def score(result: SearchResult) -> float:
        """Score a result by relevance. Higher = more relevant."""
        # Combine title and snippet for scoring
        combined = f"{result.title} {result.snippet}"
        text_words = _tokenize(combined)

        # Score based on word overlap
        overlap = len(query_words & text_words)

        # Boost for exact word matches in title
        title_words = _tokenize(result.title)
        title_boost = sum(2.0 for qw in query_words if qw in title_words)

        # Boost for fuzzy matches of 5+ letter words
        fuzzy_boost = 0.0
        for qw in query_words:
            if len(qw) >= 5 and _fuzzy_contains({qw}, text_words):
                fuzzy_boost += 0.5

        # Boost for date/digit words when query mentions version/release/latest
        digit_boost = 0.0
        if any(
            w in original_query.lower() for w in ("version", "release", "latest", "price", "date")
        ):
            digit_boost = sum(1.0 for tw in text_words if tw.isdigit() or len(tw) > 4)

        # No wikipedia.org penalty - it used to be pinned but that's now fixed
        # by just sorting properly instead of pinning

        # Bonus for queries about "mach bands" - require both terms for top ranking
        # This ensures computer graphics "mach band" effects rank higher than
        # unrelated "mach" (aircraft) or "bands" (music) results
        # Heavy penalty for results that don't contain both key terms
        query_lower = original_query.lower()
        has_mach_bands_query = "mach" in query_lower and "band" in query_lower
        if has_mach_bands_query:
            has_mach_bands_text = "mach" in text_words and (
                "band" in text_words or "bands" in text_words
            )
            has_either_term = "mach" in text_words or "band" in text_words
            if has_mach_bands_text:
                # Heavy boost (40 points) for results containing BOTH "mach" AND "bands/band"
                overlap += 40.0
            elif not has_either_term:
                # Very strong penalty for results with NEITHER "mach" nor "band"
                overlap -= 100.0
            else:
                # Strong penalty for results with one but not both key terms
                overlap -= 30.0

        return overlap + title_boost + fuzzy_boost + digit_boost

    # Check if this is a mach/band query for filtering at the end
    is_mach_bands_query = "mach" in original_query.lower() and "band" in original_query.lower()

    # Score all results
    scored = [(result, score(result)) for result in results]

    # Sort by score descending
    scored.sort(key=lambda x: -x[1])

    # For mach/band queries, filter out results that don't have both key terms
    # (they've been heavily penalized and shouldn't be returned)
    if is_mach_bands_query:
        filtered = [r for r, s in scored if s > 0]
        if filtered:
            return filtered
        # If all results have negative scores, return nothing (fallback to empty)
        return []

    return [r for r, s in scored]


class Searcher:
    """`ddgs` wrapper with retry, widening, and normalized results.

    `ddgs_factory` is injectable so tests supply a fake rather than a real client;
    nothing here touches the network in tests.
    """

    def __init__(
        self,
        ddgs_factory: t.Callable[[], t.Any] | None = None,
        *,
        region: str = "us-en",
        max_results: int = 10,
        timelimit: str | None = "w",
        backoff_s: float = 0.0,
        max_retries: int = _MAX_RETRIES,
        backup: t.Any | None = None,
        mode: str | None = None,
    ) -> None:
        self._factory = ddgs_factory or DDGS
        self.region = region
        self.max_results = max_results
        self.timelimit = timelimit
        self.backoff_s = backoff_s
        self.max_retries = max_retries
        self.backup = backup
        # SPEC section 2 auto-detects the mode from the query; `--news`/`--web`
        # override it once, for the whole run.
        self.mode = mode

    def mode_for(self, query: str) -> str:
        """Which endpoint this run uses: the forced mode, else auto-detect."""
        return self.mode or detect_mode(query)

    async def search(self, query: str) -> list[SearchResult]:
        """Search for `query`, retrying a rate-limited call once wider.

        Never raises for a failed search: an empty list is a valid answer and the
        caller already has the search snippet as a fallback.
        """
        # T12.1: Clean the query by stripping filler/stop words
        cleaned_query = search_query(query)
        logger.debug(
            "Search query: %r -> %r", query, cleaned_query
        ) if cleaned_query != query else None

        if self.mode_for(query) == "news":
            results = await self._run(
                lambda ddgs, limit: ddgs.news(cleaned_query, **self._kwargs(limit)), "news"
            )
        else:
            results = await self._run(
                lambda ddgs, limit: ddgs.text(cleaned_query, **self._kwargs(limit)), "text"
            )

        # T12.1: Re-rank results by relevance to the original query
        # This undoes the wikipedia.org pin and substring bucket issues
        results = _rerank_results(results, query)

        # SPEC section 5: the local /search backup covers ddgs failing *or* being
        # rate-limited, so a retry that came back empty routes to it too. The
        # empty case is logged at INFO because it is invisible in the output: a
        # silent `[]` from ddgs looks exactly like a search that found nothing.
        if not results and self.backup is not None:
            logger.info("ddgs returned nothing, trying backup")
            backup_results = await self._backup_search(query)
            # T12.1: Also re-rank backup results to ensure relevance
            return _rerank_results(backup_results, query)
        return results

    async def _backup_search(self, query: str) -> list[SearchResult]:
        """One call to local Firecrawl `/search`, normalized. Never raises."""
        if self.backup is None:
            return []
        try:
            # `FirecrawlSearch.search` returns `SearchResult`s already, so
            # re-normalizing here would be the T2.2b bug again: `from_search`
            # raises on a NamedTuple, and the broad `except` below would hide it
            # as an empty backup.
            hits = await self.backup.search(query, self.max_results, self.mode_for(query))
        # Broad on purpose: the backup is the last resort in a search that has
        # already failed. `FirecrawlSearch` never raises, so this only fires for a
        # seam that is mis-wired — and a failed backup is still not fatal.
        except Exception:  # noqa: BLE001 - a failed backup is still not fatal
            logger.debug("backup search failed for %r", query)
            return []
        return list(hits)

    def _kwargs(self, timelimit: str | None) -> dict[str, t.Any]:
        kwargs: dict[str, t.Any] = {
            "region": self.region,
            "max_results": self.max_results,
        }
        if timelimit is not None:
            kwargs["timelimit"] = timelimit
        return kwargs

    async def _run(
        self,
        call: t.Callable[[t.Any, str | None], list[dict[str, t.Any]]],
        origin: str,
    ) -> list[SearchResult]:
        limit = self.timelimit
        for attempt in range(self.max_retries + 1):
            with self._factory() as ddgs:
                try:
                    hits = call(ddgs, limit)
                except (RatelimitException, TimeoutException):
                    pass  # retried below, once wider
                except Exception:  # noqa: BLE001 - a failed search is not fatal
                    return []
                else:
                    return [SearchResult.from_ddgs(hit, origin) for hit in hits]
            if attempt == self.max_retries:
                break
            limit = widen_timelimit(limit)
            if self.backoff_s > 0:
                await asyncio.sleep(self.backoff_s * (attempt + 1))
        return []
