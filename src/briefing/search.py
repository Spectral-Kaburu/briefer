"""Web search via `ddgs`, normalized to `SearchResult` (SPEC section 5).

Primary search path. News-style queries go to `news()` with a `timelimit`; plain
queries go to `text()`. A rate-limited query is retried once with the timelimit
widened by one step, because a narrow window is the usual reason DuckDuckGo has
nothing to offer.
"""

from __future__ import annotations

import asyncio
import re
import typing as t
from datetime import UTC, datetime, timedelta

from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException

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
    ) -> None:
        self._factory = ddgs_factory or DDGS
        self.region = region
        self.max_results = max_results
        self.timelimit = timelimit
        self.backoff_s = backoff_s
        self.max_retries = max_retries
        self.backup = backup

    async def search(self, query: str) -> list[SearchResult]:
        """Search for `query`, retrying a rate-limited call once wider.

        Never raises for a failed search: an empty list is a valid answer and the
        caller already has the search snippet as a fallback.
        """
        if detect_mode(query) == "news":
            results = await self._run(
                lambda ddgs, limit: ddgs.news(query, **self._kwargs(limit)), "news"
            )
        else:
            results = await self._run(
                lambda ddgs, limit: ddgs.text(query, **self._kwargs(limit)), "text"
            )
        # SPEC section 5: the local /search backup covers ddgs failing *or* being
        # rate-limited, so a retry that came back empty routes to it too.
        return results or await self._backup_search(query)

    async def _backup_search(self, query: str) -> list[SearchResult]:
        """One call to local Firecrawl `/search`, normalized. Never raises."""
        if self.backup is None:
            return []
        try:
            hits = await self.backup.search(query, self.max_results)
        except Exception:  # noqa: BLE001 - a failed backup is still not fatal
            return []
        return [SearchResult.from_search(hit) for hit in hits]

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
