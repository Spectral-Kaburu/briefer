"""Live smoke tests (T2.3): reach `ddgs` and the local Firecrawl for real.

Everything here is marked `live` and skipped unless `BRIEFING_LIVE` is set, so
`make check` never touches the network. Run once against the owner's services:

    BRIEFING_LIVE=1 .venv/bin/pytest tests/test_live.py

These exist to verify the two facts NOTES-APIS.md marks UNVERIFIED: that the
installed `ddgs` returns the shapes `Searcher` normalizes, and that the
self-hosted Firecrawl's `/v2/search` emits the fields the backup path reads.
"""

from __future__ import annotations

import os

import httpx
import pytest

from briefing.fetch.local import LocalFirecrawl
from briefing.search import Searcher, SearchResult

# The self-hosted instance (SPEC section 7). Not configurable on purpose: a live
# smoke test that silently pointed somewhere else would prove nothing.
_BASE_URL = "http://localhost:3002"

requires_live = pytest.mark.skipif(
    not os.environ.get("BRIEFING_LIVE"),
    reason="live smoke test: set BRIEFING_LIVE=1 to run it against the real services",
)


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_ddgs_text_search_returns_normalized_results() -> None:
    """A plain query must come back as `SearchResult`s with real URLs."""
    searcher = Searcher(region="us-en", max_results=10, timelimit="w")
    results = await searcher.search("python asyncio semaphore tutorial")

    assert results, "ddgs text() returned no hits for a common query"
    assert all(item.url.startswith("http") for item in results), results
    assert any(item.title for item in results), results


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_ddgs_news_search_returns_dated_results() -> None:
    """A news query routes to `news()`, which NOTES-APIS says emits a date."""
    searcher = Searcher(region="us-en", max_results=10, timelimit="d")
    results = await searcher.search("latest technology news")

    assert results, "ddgs news() returned no hits for a common query"
    assert any(item.age for item in results), "no hit carried a date field"


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_local_firecrawl_is_reachable_and_scrapes() -> None:
    """The local instance must answer health and produce a real scrape."""
    client = LocalFirecrawl(base_url=_BASE_URL, timeout_s=60.0)
    try:
        assert await client.health(), f"no Firecrawl at {_BASE_URL}"
        result = await client.scrape("https://example.com")
    finally:
        await client.aclose()

    assert result.ok, result.reason
    assert result.markdown.strip(), "scrape returned no markdown"
    assert result.status_code == 200, result.status_code


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_local_firecrawl_search_matches_the_documented_shape() -> None:
    """Probe `/v2/search`: the shape the backup path reads.

    Verified live 2026-10-31 against the running instance: `data` is a **mapping**
    keyed by source type (`data.web`), not a flat list — so whatever adapts this
    endpoint must unwrap `data["web"]` before normalizing. Items carry
    `url`/`title`/`description` and **no date field**, which is why backup results
    have no `age`. Both recorded in NOTES-APIS.md.
    """
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60.0) as client:
        response = await client.post(
            "/v2/search",
            json={"query": "kenya technology news", "limit": 5, "sources": ["web"]},
        )

    assert response.status_code == 200, response.text[:200]
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    assert isinstance(data, dict), f"data is a mapping, not a list: {type(data).__name__}"

    web = data.get("web")
    assert isinstance(web, list) and web, f"no data.web list: {list(data)}"

    items = [SearchResult.from_search(hit) for hit in web if isinstance(hit, dict)]
    assert items, "/v2/search returned no results for a common query"
    assert all(item.url.startswith("http") for item in items), items
    assert all(item.title for item in items), "an item carried no title"
    assert all(item.snippet for item in items), "an item carried no description"
    # Verified: no date field on any item, so `age` stays empty for backup hits.
    assert not any("date" in hit for hit in web), "a date field appeared; NOTES-APIS is stale"
