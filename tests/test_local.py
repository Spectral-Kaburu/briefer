"""Tests for the local Firecrawl client (T1.2).

All mocked with respx; no real network, no real Firecrawl.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from briefing.fetch.errors import ErrorClass
from briefing.fetch.local import LocalFirecrawl

BASE = "http://localhost:3002"


@pytest.fixture
def client() -> LocalFirecrawl:
    return LocalFirecrawl(base_url=BASE, timeout_s=5.0)


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
