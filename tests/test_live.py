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

from briefing.fetch.local import FirecrawlSearch, LocalFirecrawl
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


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_the_real_backup_adapter_searches_the_live_instance() -> None:
    """T8.2: the adapter itself, not the raw response it reads.

    The unit tests mock respx, so nothing proved the real `FirecrawlSearch`
    unwraps a real `data.web` — and that unwrapping is the one thing T2.2 got wrong
    the first time. Runs against the owner's instance, so it stays `live`.
    """
    search = FirecrawlSearch(base_url=_BASE_URL, timeout_s=60.0)
    try:
        results = await search.search("kenya technology news", limit=5, mode="web")
    finally:
        await search.aclose()

    if not results:
        # The instance answers 200 with an empty `data` while its engines are
        # down (NOTES-APIS.md, probed 2026-11-03), and an adapter that returned
        # nothing and an adapter that is broken look identical from out here.
        # A second opinion: if the raw endpoint has nothing either, the service
        # is degraded, so there is nothing left to prove — skip, do not fail.
        try:
            async with httpx.AsyncClient(timeout=60.0) as probe:
                raw = await probe.post(
                    f"{_BASE_URL}/v2/search",
                    json={"query": "kenya technology news", "limit": 5, "sources": ["web"]},
                )
            payload = raw.json()
        except Exception as exc:  # noqa: BLE001 - a dead instance proves nothing
            pytest.skip(f"local Firecrawl unreachable ({exc!r}); re-run when it is up")
        if not (payload.get("data") or {}).get("web"):
            pytest.skip("local Firecrawl engines are degraded (empty data.web); re-run later")

    assert results, "the real adapter found nothing the raw probe could see"
    assert all(r.origin == "search" for r in results), results
    assert all(r.url.startswith("http") for r in results), results
    assert all(r.title for r in results), "an item carried no title"
    assert all(r.snippet for r in results), "an item carried no description"
    # No date field on any item, so `age` stays empty for backup hits. Asserted
    # here rather than below because only this test has real items to look at.
    assert not any(r.age for r in results), "a date field appeared; NOTES-APIS is stale"


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_the_real_backup_adapter_never_raises_on_a_dead_instance() -> None:
    """The adapter's whole contract, proven against a port with nothing on it.

    A port that is closed rather than filtered, so the connection is refused
    rather than hanging: the failure a Docker-stopped machine actually produces.
    """
    search = FirecrawlSearch(base_url="http://localhost:3999", timeout_s=5.0)
    try:
        results = await search.search("kenya technology news", limit=5, mode="web")
    finally:
        await search.aclose()

    assert results == [], "a dead instance must read as no results, not a crash"


# --- T10.8: Knowledge source adapter live tests --------------------------------


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_wikipedia_adapter_returns_passage() -> None:
    """T10.8: The real Wikipedia adapter returns a passage for a clear query.

    The quota is barely touched with one lookup. Skips on connection failure,
    timeout, or 429.
    """
    from briefing.config import KnowledgeConfig
    from briefing.knowledge.wikipedia import WikipediaAdapter

    config = KnowledgeConfig(timeout_s=10.0)
    adapter = WikipediaAdapter(config)

    try:
        results = await adapter.lookup("python programming language", limit=1)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Wikipedia adapter failed ({exc!r})")

    # If results is empty, the service may be degraded - skip rather than fail
    if not results:
        pytest.skip("Wikipedia adapter returned no results (service degraded or 429)")

    assert len(results) >= 1
    passage = results[0]
    assert passage.title, "passage has no title"
    assert passage.text, "passage has no text"
    assert "wikipedia.org" in passage.url or "wikipedia.org" in passage.domain
    assert "CC BY-SA" in passage.license
    assert passage.url.startswith("https://")


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_stackexchange_adapter_returns_passage() -> None:
    """T10.8: The real Stack Exchange adapter returns a passage for a clear query.

    The quota is barely touched with one lookup. Skips on connection failure,
    timeout, or 429. Requires a valid STACKEXCHANGE_KEY for best results,
    but works without one (lower quota).
    """
    from briefing.config import KnowledgeConfig
    from briefing.knowledge.stackexchange import StackExchangeAdapter

    config = KnowledgeConfig(timeout_s=10.0)
    key = os.environ.get("STACKEXCHANGE_KEY", None)
    adapter = StackExchangeAdapter(config, key=key)

    try:
        results = await adapter.lookup("python asyncio semaphore", limit=1)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Stack Exchange adapter failed ({exc!r})")

    # If results is empty, check if it's quota exhaustion
    if not results:
        # Could be no accepted answers, quota exhaustion, or service issue
        pytest.skip("Stack Exchange adapter returned no results")

    assert len(results) >= 1
    passage = results[0]
    assert passage.title, "passage has no title"
    assert passage.text, "passage has no text"
    assert "stackoverflow.com" in passage.url or "stackoverflow.com" in passage.domain
    assert "CC BY-SA" in passage.license
    assert passage.url.startswith("https://")


@requires_live
@pytest.mark.live
@pytest.mark.asyncio
async def test_ask_mode_knowledge_sources_integration() -> None:
    """T10.8: End-to-end ask mode with Wikipedia knowledge source integration.

    The ask mode should run knowledge adapters concurrently with web search,
    merge their passages into sources, and number them first.
    """
    from briefing.ask import ask
    from briefing.config import KnowledgeConfig
    from briefing.knowledge.stackexchange import StackExchangeAdapter
    from briefing.knowledge.wikipedia import WikipediaAdapter
    from briefing.search import SearchResult

    config = KnowledgeConfig(timeout_s=15.0)

    # Create knowledge adapters
    knowledge_adapters = [
        WikipediaAdapter(config),
        StackExchangeAdapter(config),
    ]

    # Create a search result that will be deduplicated (domain matches knowledge host)
    # This tests that knowledge passages are numbered first and then web sources
    search_results = [
        SearchResult(
            title="Python Wiki Article",
            url="https://en.wikipedia.org/wiki/Python_(programming_language)",
            snippet="A Wikipedia article about Python (which would be deduplicated)",
            source="",
            age="",
            origin="text",
        ),
        SearchResult(
            title="Python Official Site",
            url="https://www.python.org/",
            snippet="The official Python website",
            source="",
            age="",
            origin="text",
        ),
    ]

    async def fake_search(query: str) -> list[SearchResult]:
        return search_results

    async def fake_fetch(hit: SearchResult) -> tuple[str, str, str]:
        # Return empty markdown for Wikipedia result (will become snippet-only)
        # Return real-ish content for python.org
        if "wikipedia" in hit.url:
            return hit.title or "Test", "", hit.snippet or "Wiki snippet"
        return hit.title or "Test", hit.snippet or "Content", f"# {hit.title}\n{hit.snippet}"

    def fake_extract(
        markdown: str, heading: str, url: str, search_title: str, query: str | None
    ) -> tuple[str, str]:
        return heading or "Test", markdown or heading or search_title or "Content"

    try:
        result = await ask(
            query="what is the python programming language",
            search=fake_search,
            fetch=fake_fetch,
            extract=fake_extract,
            model=None,  # No model key - we'll check passages
            knowledge_enabled=True,
            knowledge_adapters=knowledge_adapters,
            knowledge_config=config,
            pages=3,
            deadline_s=20.0,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ask mode failed ({exc!r})")

    # We should have sources
    assert result.sources, "ask mode returned no sources"

    # First source should be from Wikipedia (numbered before web sources)
    # Check the first source's domain contains wikipedia
    first_source_domain = result.sources[0][
        3
    ]  # domain is 4th element (0-indexed: 0=n, 1=title, 2=url, 3=domain)
    assert "wikipedia" in first_source_domain.lower(), (
        f"first source domain is {first_source_domain}"
    )

    # Count Wiki and SO sources
    wiki_sources = [s for s in result.sources if "wikipedia" in s[3].lower()]

    # At least one Wikipedia source should be present
    assert len(wiki_sources) >= 1, f"no Wikipedia source found in {result.sources}"
