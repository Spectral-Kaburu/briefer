"""Tests for search.py (T2.1) and the local-Firecrawl backup (T2.2).

Normalized `SearchResult`, news/text auto-detection, timelimit widening,
rate-limit handling with backoff, and the `/search` fallback. Both dependencies
are injected, so nothing here hits the network.
"""

from __future__ import annotations

import asyncio
import typing as t
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from ddgs.exceptions import RatelimitException, TimeoutException

from briefing.fetch.local import LocalFirecrawl
from briefing.search import (
    _NO_DATE,
    Searcher,
    SearchResult,
    detect_mode,
    parse_age,
    widen_timelimit,
)

# A ddgs news() hit: title/url/body/date/source (per NOTES-APIS.md).
NEWS_HIT: dict[str, t.Any] = {
    "title": "Bank raises lending rate",
    "url": "https://example.com/a",
    "body": "The central bank raised rates by fifty basis points.",
    "date": "2026-10-30T06:00:00+00:00",
    "source": "Reuters",
}
# A ddgs text() hit: title/url/body only, no date, no source.
TEXT_HIT: dict[str, t.Any] = {
    "title": "Lending rates explained",
    "url": "https://example.com/b",
    "body": "A primer on what lending rates mean.",
}


# --- SearchResult normalization -------------------------------------------


def test_result_is_a_normalized_record() -> None:
    result = SearchResult.from_ddgs(NEWS_HIT, origin="news")
    assert result.title == "Bank raises lending rate"
    assert result.url == "https://example.com/a"
    assert result.snippet == NEWS_HIT["body"]
    assert result.source == "Reuters"
    assert result.origin == "news"


def test_text_hit_has_no_age_or_source() -> None:
    """Verified: text() emits no date/source fields (NOTES-APIS.md)."""
    result = SearchResult.from_ddgs(TEXT_HIT, origin="text")
    assert result.age == ""
    assert result.source == ""
    assert result.snippet == TEXT_HIT["body"]


def test_missing_fields_do_not_raise() -> None:
    """A hit with only a url must still normalize; be defensive per NOTES-APIS."""
    result = SearchResult.from_ddgs({"url": "https://example.com/c"}, origin="text")
    assert result.url == "https://example.com/c"
    assert result.title == ""
    assert result.snippet == ""


def test_result_drops_whitespace() -> None:
    hit = {"title": "  spaced  ", "url": " https://example.com/d ", "body": " text "}
    result = SearchResult.from_ddgs(hit, origin="text")
    assert result.title == "spaced"
    assert result.snippet == "text"


def test_result_can_be_ranked_by_recency() -> None:
    older = SearchResult.from_ddgs(NEWS_HIT, origin="news")
    newer = SearchResult.from_ddgs({**NEWS_HIT, "date": "2026-10-31T06:00:00+00:00"}, origin="news")
    assert older.published_at < newer.published_at


def test_result_without_a_date_sorts_last() -> None:
    dated = SearchResult.from_ddgs(NEWS_HIT, origin="news")
    undated = SearchResult.from_ddgs(TEXT_HIT, origin="text")
    assert sorted([undated, dated], key=lambda r: r.published_at) == [dated, undated]


# --- age parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-30T06:00:00+00:00", datetime(2026, 10, 30, 6, tzinfo=UTC)),
        ("2026-10-30 06:00:00", datetime(2026, 10, 30, 6, tzinfo=UTC)),
        ("2026-10-30", datetime(2026, 10, 30, tzinfo=UTC)),
    ],
)
def test_parse_age_accepts_common_shapes(raw: str, expected: datetime) -> None:
    assert parse_age(raw) == expected


def test_parse_age_returns_none_for_junk() -> None:
    assert parse_age("not a date") is None
    assert parse_age("") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # No timezone: some engines omit it, and dropping the hit silently loses
        # a real result from the recency ranking.
        ("2026-10-30T06:00:00", datetime(2026, 10, 30, 6, tzinfo=UTC)),
        ("2026-10-30 06:00:00 UTC", datetime(2026, 10, 30, 6, tzinfo=UTC)),
        ("2026-10-30T06:00:00.123456+00:00", datetime(2026, 10, 30, 6, tzinfo=UTC)),
    ],
)
def test_parse_age_accepts_timezone_less_and_fractional_forms(raw: str, expected: datetime) -> None:
    assert parse_age(raw) == expected


def test_parse_age_handles_relative_forms() -> None:
    """ddgs news engines sometimes emit relative strings rather than ISO dates."""
    before = datetime.now(UTC) - timedelta(hours=3)
    parsed = parse_age("3 hours ago")
    assert parsed is not None
    assert before <= parsed <= datetime.now(UTC)


# --- mode detection -------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "latest flooding in Nairobi",
        "breaking news on the rail strike",
        "today's headlines",
        "news about the election",
        "what happened this morning",
    ],
)
def test_news_queries_are_detected(query: str) -> None:
    assert detect_mode(query) == "news"


@pytest.mark.parametrize(
    "query",
    [
        "how does a lending rate work",
        "difference between fiscal and monetary policy",
        "explain the structure of the senate",
    ],
)
def test_plain_queries_are_detected(query: str) -> None:
    assert detect_mode(query) == "text"


def test_detection_is_case_insensitive() -> None:
    assert detect_mode("LATEST flooding") == "news"


def test_detection_needs_a_word_boundary() -> None:
    """'newest' contains 'news' but is not a news query."""
    assert detect_mode("the newest lending rates") == "text"


def test_query_is_trimmed() -> None:
    assert detect_mode("  latest flooding  ") == "news"


# --- timelimit widening ---------------------------------------------------


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (None, None),
        ("d", "w"),
        ("w", "m"),
        ("m", "y"),
        ("y", "y"),
    ],
)
def test_widen_timelimit_widens_by_one_step(limit: str | None, expected: str | None) -> None:
    assert widen_timelimit(limit) == expected


def test_widening_never_wraps_around() -> None:
    assert widen_timelimit("y") == "y"


# --- the searcher ---------------------------------------------------------


class FakeDDGS:
    """Stands in for `ddgs.DDGS`, recording the call it was given."""

    def __init__(
        self,
        *,
        news: list[dict[str, t.Any]] | None = None,
        text: list[dict[str, t.Any]] | None = None,
        news_raises: Exception | None = None,
        news_failures: int | None = None,
        text_raises: Exception | None = None,
    ) -> None:
        # Default: raise on every call, so a "never succeeds" case needs no count.
        self._news_failures = news_failures if news_failures is not None else 1 << 30
        self._news = news if news is not None else []
        self._text = text if text is not None else []
        self._news_raises = news_raises
        self._text_raises = text_raises
        self.news_calls: list[dict[str, t.Any]] = []
        self.text_calls: list[dict[str, t.Any]] = []
        self._news_calls = 0

    def news(self, query: str, **kwargs: t.Any) -> list[dict[str, t.Any]]:
        self.news_calls.append({"query": query, **kwargs})
        self._news_calls += 1
        if self._news_raises is not None and self._news_calls <= self._news_failures:
            raise self._news_raises
        return self._news

    def text(self, query: str, **kwargs: t.Any) -> list[dict[str, t.Any]]:
        self.text_calls.append({"query": query, **kwargs})
        if self._text_raises is not None:
            raise self._text_raises
        return self._text

    def __enter__(self) -> t.Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


@pytest.fixture
def fake() -> FakeDDGS:
    return FakeDDGS(news=[NEWS_HIT, {**NEWS_HIT, "url": "https://example.com/2"}])


def _searcher(**kwargs: t.Any) -> Searcher:
    return Searcher(**kwargs)


def test_news_query_uses_news_endpoint(fake: FakeDDGS) -> None:
    searcher = _searcher(ddgs_factory=lambda: fake)
    results = asyncio.run(searcher.search("latest flooding"))
    assert [r.origin for r in results] == ["news", "news"]
    assert len(fake.news_calls) == 1
    assert fake.text_calls == []


def test_forced_news_mode_beats_a_plain_query() -> None:
    """SPEC section 3's --news: the flag decides, not the query's words."""
    fake = FakeDDGS(news=[NEWS_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, mode="news")
    results = asyncio.run(searcher.search("how does a lending rate work"))
    assert [r.origin for r in results] == ["news"]
    assert len(fake.news_calls) == 1
    assert fake.text_calls == []


def test_forced_web_mode_beats_a_news_query() -> None:
    """SPEC section 3's --web: ditto, in the other direction."""
    fake = FakeDDGS(text=[TEXT_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, mode="text")
    results = asyncio.run(searcher.search("latest breaking flooding"))
    assert [r.origin for r in results] == ["text"]
    assert len(fake.text_calls) == 1
    assert fake.news_calls == []


def test_no_forced_mode_auto_detects_from_the_query(fake: FakeDDGS) -> None:
    """The default stays SPEC section 2's auto-detect."""
    searcher = _searcher(ddgs_factory=lambda: fake)
    assert searcher.mode_for("latest flooding") == "news"
    assert searcher.mode_for("how a lending rate works") == "text"
    assert searcher.mode_for("latest flooding") == "news"


def test_plain_query_uses_text_endpoint() -> None:
    fake = FakeDDGS(text=[TEXT_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake)
    results = asyncio.run(searcher.search("how does a lending rate work"))
    assert [r.origin for r in results] == ["text"]
    assert len(fake.text_calls) == 1
    assert fake.news_calls == []


def test_results_are_normalized_from_news(fake: FakeDDGS) -> None:
    searcher = _searcher(ddgs_factory=lambda: fake)
    results = asyncio.run(searcher.search("latest flooding"))
    assert results[0].title == "Bank raises lending rate"
    assert results[0].source == "Reuters"


def test_timelimit_is_passed_through(fake: FakeDDGS) -> None:
    searcher = _searcher(ddgs_factory=lambda: fake, timelimit="d", region="ke-en")
    asyncio.run(searcher.search("latest flooding"))
    assert fake.news_calls[0]["timelimit"] == "d"
    assert fake.news_calls[0]["region"] == "ke-en"


def test_max_results_is_passed_through(fake: FakeDDGS) -> None:
    searcher = _searcher(ddgs_factory=lambda: fake, max_results=3)
    asyncio.run(searcher.search("latest flooding"))
    assert fake.news_calls[0]["max_results"] == 3


def test_empty_results_do_not_raise(fake: FakeDDGS) -> None:
    empty = FakeDDGS()
    searcher = _searcher(ddgs_factory=lambda: empty)
    assert asyncio.run(searcher.search("latest flooding")) == []


# --- rate limiting --------------------------------------------------------


def test_rate_limit_retries_then_succeeds() -> None:
    fake = FakeDDGS(news=[NEWS_HIT], news_raises=RatelimitException("slow down"), news_failures=1)
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0)
    results = asyncio.run(searcher.search("latest flooding"))
    assert [r.title for r in results] == ["Bank raises lending rate"]
    assert len(fake.news_calls) == 2  # one failure, one success


def test_rate_limit_gives_up_after_the_retry_budget() -> None:
    fake = FakeDDGS(news=[], news_raises=RatelimitException("slow down"))
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0, max_retries=2)
    results = asyncio.run(searcher.search("latest flooding"))
    assert results == []
    assert len(fake.news_calls) == 3  # the try plus two retries


def test_timeout_is_retried_once() -> None:
    fake = FakeDDGS(news=[NEWS_HIT], news_raises=TimeoutException("too slow"), news_failures=1)
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0)
    results = asyncio.run(searcher.search("latest flooding"))
    assert len(results) == 1
    assert len(fake.news_calls) == 2


def test_retry_uses_the_widened_timelimit() -> None:
    """A rate-limited narrow query is retried wider, per SPEC section 5."""
    fake = FakeDDGS(news=[NEWS_HIT], news_raises=RatelimitException("slow down"), news_failures=1)
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0, timelimit="d")
    asyncio.run(searcher.search("latest flooding"))
    assert fake.news_calls[0]["timelimit"] == "d"
    assert fake.news_calls[1]["timelimit"] == "w"


def test_widening_stops_at_the_top_of_the_scale() -> None:
    fake = FakeDDGS(news=[NEWS_HIT], news_raises=RatelimitException("slow down"), news_failures=1)
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0, timelimit="y")
    asyncio.run(searcher.search("latest flooding"))
    assert [c["timelimit"] for c in fake.news_calls] == ["y", "y"]


def test_other_errors_propagate_as_a_search_failure() -> None:
    fake = FakeDDGS(news=[], news_raises=RuntimeError("boom"))
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0)
    results = asyncio.run(searcher.search("latest flooding"))
    assert results == []
    assert len(fake.news_calls) == 1  # not retried, not a rate limit


# --- backup search via local Firecrawl /search (T2.2) ----------------------

# A /v2/search web item, per NOTES-APIS.md: url, title, description, category.
BASE = "http://localhost:3002"
SEARCH_HIT: dict[str, t.Any] = {
    "url": "https://example.com/e",
    "title": "Bank raises lending rate",
    "description": "The central bank raised rates by fifty basis points.",
    "category": "finance",
}


class FakeFirecrawlSearch:
    """Stands in for local Firecrawl `/search`, recording what it was asked."""

    def __init__(
        self,
        *,
        results: list[dict[str, t.Any]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._results = results if results is not None else []
        self._raises = raises
        self.calls: list[dict[str, t.Any]] = []

    async def search(self, query: str, limit: int) -> list[dict[str, t.Any]]:
        self.calls.append({"query": query, "limit": limit})
        if self._raises is not None:
            raise self._raises
        return self._results


def test_ddgs_success_never_touches_the_backup(fake: FakeDDGS) -> None:
    """SPEC section 5: the backup is a fallback, not a second opinion."""
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    results = asyncio.run(searcher.search("latest flooding"))
    assert [r.origin for r in results] == ["news", "news"]
    assert backup.calls == []  # never consulted


def test_ddgs_failure_falls_back_to_local_search() -> None:
    fake = FakeDDGS(news=[], news_raises=RuntimeError("boom"))
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    results = asyncio.run(searcher.search("latest flooding"))
    assert [r.title for r in results] == ["Bank raises lending rate"]
    assert backup.calls == [{"query": "latest flooding", "limit": 10}]


def test_backup_results_are_normalized() -> None:
    """`description` maps to `snippet`; `category` has no SPEC §5 home and drops."""
    fake = FakeDDGS(news=[], news_raises=RuntimeError("boom"))
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    result = asyncio.run(searcher.search("latest flooding"))[0]
    assert result.url == "https://example.com/e"
    assert result.snippet == "The central bank raised rates by fifty basis points."
    assert result.source == ""
    assert result.origin == "search"


def test_backup_carries_no_date_so_it_sorts_last() -> None:
    """A /search web item has no verified date field (NOTES-APIS.md)."""
    fake = FakeDDGS(news=[], news_raises=RuntimeError("boom"))
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    result = asyncio.run(searcher.search("latest flooding"))[0]
    assert result.age == ""
    assert result.published_at == _NO_DATE


def test_rate_limit_also_falls_back() -> None:
    """SPEC section 5: the backup exists for when ddgs is rate-limited."""
    fake = FakeDDGS(news=[], news_raises=RatelimitException("slow down"))
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    results = asyncio.run(searcher.search("latest flooding"))
    assert len(results) == 1
    assert len(backup.calls) == 1


def test_both_fail_returns_empty(fake: FakeDDGS) -> None:
    backup = FakeFirecrawlSearch(raises=httpx.ConnectError("refused"))
    searcher = _searcher(
        ddgs_factory=lambda: FakeDDGS(news=[], news_raises=RuntimeError("boom")),
        backup=backup,
        backoff_s=0,
    )
    assert asyncio.run(searcher.search("latest flooding")) == []


def test_backup_receives_the_original_query_not_a_widened_one() -> None:
    fake = FakeDDGS(news=[], news_raises=RuntimeError("boom"))
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    asyncio.run(searcher.search("latest flooding"))
    assert backup.calls[0]["query"] == "latest flooding"


def test_no_backup_configured_still_returns_empty() -> None:
    fake = FakeDDGS(news=[], news_raises=RuntimeError("boom"))
    searcher = _searcher(ddgs_factory=lambda: fake, backoff_s=0)
    assert asyncio.run(searcher.search("latest flooding")) == []


def test_plain_query_falls_back_too() -> None:
    """The backup covers `text()` queries, not just news ones."""
    fake = FakeDDGS(text=[], text_raises=RuntimeError("boom"))
    backup = FakeFirecrawlSearch(results=[SEARCH_HIT])
    searcher = _searcher(ddgs_factory=lambda: fake, backup=backup, backoff_s=0)
    results = asyncio.run(searcher.search("how does a lending rate work"))
    assert [r.origin for r in results] == ["search"]
    assert backup.calls[0]["query"] == "how does a lending rate work"


@respx.mock
def test_a_rate_limited_ddgs_is_really_rescued_by_local_firecrawl() -> None:
    """The T2.2 gap as a wiring test, not a shape test.

    Every test above injects `FakeFirecrawlSearch`, which is shaped the way
    `cli._searcher` passes its backup. Production passes a real
    `LocalFirecrawl`, which for a while had no `search` method at all — so
    `_backup_search`'s broad `except` ate the `AttributeError` and a
    rate-limited run returned zero results with nothing to say why. This test
    drives the real class through the real `Searcher`, so losing the adapter
    fails here instead of silently in production.
    """
    respx.post(f"{BASE}/v2/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"web": [SEARCH_HIT]}})
    )
    searcher = Searcher(
        ddgs_factory=lambda: FakeDDGS(news_raises=RatelimitException("429 too many")),
        region="ke-en",
        max_results=5,
        backup=LocalFirecrawl(base_url=BASE),
        backoff_s=0,
    )

    results = asyncio.run(searcher.search("latest flooding in kenya"))

    assert [r.title for r in results] == [SEARCH_HIT["title"]]
    assert [r.url for r in results] == [SEARCH_HIT["url"]]
    assert results[0].snippet == SEARCH_HIT["description"]
    assert results[0].origin == "search"
