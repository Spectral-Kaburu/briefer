"""Tests for the local Firecrawl client (T1.2).

All mocked with respx; no real network, no real Firecrawl.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from briefing.fetch.errors import ErrorClass
from briefing.fetch.local import FirecrawlSearch, LocalFirecrawl

BASE = "http://localhost:3002"


@pytest.fixture
def client() -> LocalFirecrawl:
    return LocalFirecrawl(base_url=BASE, timeout_s=5.0)


@pytest.fixture
def backup() -> FirecrawlSearch:
    return FirecrawlSearch(base_url=BASE, timeout_s=5.0)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno == logging.WARNING]


def _assert_warns_for(caplog: pytest.LogCaptureFixture, name: str, *needles: str) -> None:
    """Exactly one WARNING matching `name`, and it mentions every needle."""
    records = [
        record
        for record in _warnings(caplog)
        if record.name == name and all(needle in record.getMessage() for needle in needles)
    ]
    assert len(records) == 1, [(r.name, r.getMessage(), r.exc_info) for r in _warnings(caplog)]
    return records[0]


def _ok_payload(title: str = "Test heading", markdown: str = "Article body text.") -> dict:
    return {
        "success": True,
        "data": {
            "markdown": markdown,
            "metadata": {"title": title, "statusCode": 200},
        },
    }


# --- success ---------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_scrape_success(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(
        return_value=httpx.Response(200, json=_ok_payload("Kenya election"))
    )
    result = await client.scrape("https://nation.africa/story")
    assert result.ok
    assert result.error_class is ErrorClass.OK
    assert result.markdown == "Article body text."
    assert result.title == "Kenya election"
    assert result.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_scrape_sends_url_and_formats(client: LocalFirecrawl) -> None:
    route = respx.post(f"{BASE}/v2/scrape").mock(
        return_value=httpx.Response(200, json=_ok_payload())
    )
    await client.scrape("https://example.com/page")
    body = route.calls.last.request.content
    assert b"https://example.com/page" in body
    assert b"markdown" in body


@pytest.mark.asyncio
@respx.mock
async def test_scrape_falls_back_to_first_h1(client: LocalFirecrawl) -> None:
    """No metadata title: heading comes from the first h1 in the markdown."""
    md = "# Heading from h1\n\nBody text of the article follows here."
    respx.post(f"{BASE}/v2/scrape").mock(
        return_value=httpx.Response(200, json=_ok_payload(title="", markdown=md))
    )
    result = await client.scrape("https://example.com")
    assert result.title == "Heading from h1"


@pytest.mark.asyncio
@respx.mock
async def test_scrape_no_title_anywhere_yields_empty(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(
        return_value=httpx.Response(200, json=_ok_payload(title="", markdown="Just body."))
    )
    result = await client.scrape("https://example.com")
    assert result.ok
    assert result.title == ""


# --- error responses -------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_403_classified_as_barrier(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(403, text="Forbidden"))
    result = await client.scrape("https://example.com")
    assert not result.ok
    assert result.error_class is ErrorClass.BARRIER
    assert result.reason


@pytest.mark.asyncio
@respx.mock
async def test_429_classified_as_barrier(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(429, text="Rate limited"))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.BARRIER


@pytest.mark.asyncio
@respx.mock
async def test_501_classified_as_not_solvable(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(501, text="Not Implemented"))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.NOT_SOLVABLE_BY_API


@pytest.mark.asyncio
@respx.mock
async def test_404_classified_as_dead(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(404, text="Not Found"))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.DEAD


@pytest.mark.asyncio
@respx.mock
async def test_500_classified_as_transient(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(500, text="Server Error"))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.TRANSIENT_5XX


@pytest.mark.asyncio
@respx.mock
async def test_challenge_body_classified_as_barrier(client: LocalFirecrawl) -> None:
    body = "Just a moment... verifying your browser"
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, text=body))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.BARRIER


# --- transport failures ----------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_timeout_classified_as_barrier(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(side_effect=httpx.ReadTimeout("too slow"))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.BARRIER


@pytest.mark.asyncio
@respx.mock
async def test_connection_refused_classified_as_local_down(client: LocalFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(side_effect=httpx.ConnectError("refused"))
    result = await client.scrape("https://example.com")
    assert result.error_class is ErrorClass.LOCAL_DOWN
    assert "Firecrawl" in result.reason


@pytest.mark.asyncio
@respx.mock
async def test_one_bad_url_does_not_raise(client: LocalFirecrawl) -> None:
    """A failing scrape returns a result object, never an exception."""
    respx.post(f"{BASE}/v2/scrape").mock(side_effect=httpx.ConnectError("refused"))
    result = await client.scrape("https://example.com")
    assert result.ok is False


# --- health ----------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_health_ok(client: LocalFirecrawl) -> None:
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, text="ok"))
    assert await client.health() is True


@pytest.mark.asyncio
@respx.mock
async def test_health_down_on_connection_refused(client: LocalFirecrawl) -> None:
    respx.get(f"{BASE}/").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.health() is False


@pytest.mark.asyncio
@respx.mock
async def test_health_down_on_server_error(client: LocalFirecrawl) -> None:
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(503, text="down"))
    assert await client.health() is False


# --- visible failures (T12) -------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_scrape_success_logs_no_warning(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok_payload()))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        result = await client.scrape("https://example.com")
    assert result.ok
    assert not _warnings(caplog)


@pytest.mark.asyncio
@respx.mock
async def test_403_scrape_warns_with_url_and_status(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(403, text="Forbidden"))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        result = await client.scrape("https://example.com/blocked")
    assert result.error_class is ErrorClass.BARRIER
    _assert_warns_for(caplog, "briefing.fetch.local", BASE, "https://example.com/blocked", "403")


@pytest.mark.asyncio
@respx.mock
async def test_500_scrape_warns_with_status(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(500, text="Server Error"))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        await client.scrape("https://example.com/boom")
    _assert_warns_for(caplog, "briefing.fetch.local", BASE, "https://example.com/boom", "500")


@pytest.mark.asyncio
@respx.mock
async def test_timeout_scrape_warns_with_exception_class(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(side_effect=httpx.ReadTimeout("too slow"))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        await client.scrape("https://example.com/slow")
    record = _assert_warns_for(
        caplog,
        "briefing.fetch.local",
        BASE,
        "https://example.com/slow",
        "ReadTimeout",
        "too slow",
    )
    assert record.exc_info is not None


@pytest.mark.asyncio
@respx.mock
async def test_connection_refused_scrape_warns_with_exception_class(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(side_effect=httpx.ConnectError("refused"))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        await client.scrape("https://example.com/down")
    record = _assert_warns_for(
        caplog,
        "briefing.fetch.local",
        BASE,
        "https://example.com/down",
        "ConnectError",
        "refused",
    )
    assert record.exc_info is not None


@pytest.mark.asyncio
@respx.mock
async def test_health_connect_error_warns(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(f"{BASE}/").mock(side_effect=httpx.ConnectError("refused"))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        assert await client.health() is False
    _assert_warns_for(
        caplog, "briefing.fetch.local", BASE, "ConnectError", "start it with docker compose up"
    )


@pytest.mark.asyncio
@respx.mock
async def test_health_server_error_warns(
    client: LocalFirecrawl, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(503, text="down"))
    with caplog.at_level(logging.WARNING, logger="briefing.fetch.local"):
        assert await client.health() is False
    _assert_warns_for(caplog, "briefing.fetch.local", BASE, "503")


# --- backup search visibility (T12) ----------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_backup_search_non_2xx_warns_with_status(
    backup: FirecrawlSearch, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/search").mock(return_value=httpx.Response(500, text="Server Error"))
    with caplog.at_level(logging.DEBUG, logger="briefing.fetch.local"):
        results = await backup.search("kenya flooding", limit=5)
    assert results == []
    _assert_warns_for(caplog, "briefing.fetch.local", BASE, "kenya flooding", "500")


@pytest.mark.asyncio
@respx.mock
async def test_backup_search_success_logs_no_warning(
    backup: FirecrawlSearch, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/search").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "data": {"web": [{"url": "https://example.com/a"}]}},
        )
    )
    with caplog.at_level(logging.DEBUG, logger="briefing.fetch.local"):
        results = await backup.search("kenya flooding", limit=5)
    assert [r.url for r in results] == ["https://example.com/a"]
    assert not _warnings(caplog)


@pytest.mark.asyncio
@respx.mock
async def test_backup_search_bad_json_stays_debug_only(
    backup: FirecrawlSearch, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/search").mock(return_value=httpx.Response(200, text="<html>"))
    with caplog.at_level(logging.DEBUG, logger="briefing.fetch.local"):
        assert await backup.search("kenya flooding", limit=5) == []
    assert not _warnings(caplog)
    assert any(record.levelno == logging.DEBUG for record in caplog.records)


@pytest.mark.asyncio
@respx.mock
async def test_backup_search_connect_error_stays_debug_only(
    backup: FirecrawlSearch, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{BASE}/v2/search").mock(side_effect=httpx.ConnectError("refused"))
    with caplog.at_level(logging.DEBUG, logger="briefing.fetch.local"):
        assert await backup.search("kenya flooding", limit=5) == []
    assert not _warnings(caplog)
    assert any(record.levelno == logging.DEBUG for record in caplog.records)
