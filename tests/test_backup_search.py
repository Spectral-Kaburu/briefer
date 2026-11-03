"""Tests for the `/v2/search` backup adapter (T8.2).

The ddgs backup is the last resort in a search that has already failed, so its
own contract is the opposite of every other client in this project: it must
**never raise**. A connect error, a timeout, a non-2xx, malformed JSON and an
unexpected shape are all "no backup results", not a crashed run — and a crashed
run at that point loses a briefing the owner was waiting for.

Every test here is mocked with respx. The one that talks to a real instance lives
in `test_live.py` under `@pytest.mark.live`.

The response shape is verified, not assumed (NOTES-APIS.md, probed 2026-11-03
against the running instance): `data` is a **mapping keyed by source type**, and
`sources: ["news"]` still returns items under `data["web"]` — there is no news
branch and no date field on any item.
"""

from __future__ import annotations

import typing as t

import httpx
import pytest
import respx

from briefing.fetch.local import BASE, FirecrawlSearch

pytestmark = pytest.mark.asyncio

WEB = "https://nation.africa/bank-raises-rate"


def _web_item(url: str = WEB, title: str = "Bank raises rate") -> dict[str, t.Any]:
    return {"url": url, "title": title, "description": "The central bank acted."}


def _payload(items: t.Any) -> dict[str, t.Any]:
    return {"success": True, "data": {"web": items}}


@pytest.fixture
def search() -> FirecrawlSearch:
    return FirecrawlSearch(base_url=BASE, timeout_s=5.0)


# --- the happy paths -------------------------------------------------------


@respx.mock
async def test_a_web_query_returns_normalized_results(search: FirecrawlSearch) -> None:
    """The point of the adapter: web hits become `SearchResult`s."""
    respx.post(f"{BASE}/v2/search").respond(
        200, json=_payload([_web_item(), _web_item(url="https://a.example/x")])
    )

    results = await search.search("kenya news", limit=5, mode="web")

    assert [r.url for r in results] == [WEB, "https://a.example/x"]
    assert results[0].title == "Bank raises rate"
    assert results[0].snippet == "The central bank acted."
    assert results[0].origin == "search"


@respx.mock
async def test_a_news_query_asks_for_the_news_source(search: FirecrawlSearch) -> None:
    """`mode` must reach the request, or a news run silently searches web."""
    route = respx.post(f"{BASE}/v2/search").respond(200, json=_payload([_web_item()]))

    results = await search.search("kenya news", limit=5, mode="news")

    assert route.calls.last.request.content is not None
    assert b"news" in route.calls.last.request.content
    assert len(results) == 1


@respx.mock
async def test_a_news_response_under_the_news_key_is_read(
    search: FirecrawlSearch,
) -> None:
    """Today the instance answers news under `web`; if that ever changes, read it."""
    respx.post(f"{BASE}/v2/search").respond(
        200,
        json={"success": True, "data": {"web": [_web_item()], "news": [_web_item()]}},
    )

    results = await search.search("kenya news", limit=5, mode="news")

    # One item, not two: the same URL must not arrive twice just because the
    # instance answered it under both keys.
    assert [r.url for r in results] == [WEB]


@respx.mock
async def test_age_is_left_empty(search: FirecrawlSearch) -> None:
    """No verified date field exists on this endpoint (NOTES-APIS.md).

    An invented age is worse than a missing one: the UI shows it to the user.
    """
    respx.post(f"{BASE}/v2/search").respond(200, json=_payload([_web_item()]))

    results = await search.search("kenya news", limit=5, mode="web")

    assert results[0].age == ""


@respx.mock
async def test_duplicate_urls_are_dropped(search: FirecrawlSearch) -> None:
    """The same story twice pushes a real one off a 5-card screen."""
    respx.post(f"{BASE}/v2/search").respond(
        200,
        json=_payload([_web_item(), _web_item(), _web_item(url="https://a.example/x")]),
    )

    results = await search.search("kenya news", limit=5, mode="web")

    assert [r.url for r in results] == [WEB, "https://a.example/x"]


@respx.mock
async def test_an_item_without_a_url_is_skipped(search: FirecrawlSearch) -> None:
    """A hit with no URL cannot be fetched, so it cannot be shown."""
    respx.post(f"{BASE}/v2/search").respond(200, json=_payload([{}, _web_item()]))

    results = await search.search("kenya news", limit=5, mode="web")

    assert [r.url for r in results] == [WEB]


# --- never raises: every failure is "no backup results" --------------------


@respx.mock
async def test_a_non_2xx_returns_nothing(search: FirecrawlSearch) -> None:
    respx.post(f"{BASE}/v2/search").respond(500, json={"error": "boom"})

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_a_connect_error_returns_nothing(search: FirecrawlSearch) -> None:
    """The common case: Docker is not running, so there is no instance at all."""
    respx.post(f"{BASE}/v2/search").mock(side_effect=httpx.ConnectError("connection refused"))

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_a_timeout_returns_nothing(search: FirecrawlSearch) -> None:
    respx.post(f"{BASE}/v2/search").mock(side_effect=httpx.ReadTimeout("too slow"))

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_malformed_json_returns_nothing(search: FirecrawlSearch) -> None:
    respx.post(f"{BASE}/v2/search").respond(200, content=b"<html>not json</html>")

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_a_flat_list_is_not_the_web_key(search: FirecrawlSearch) -> None:
    """The documented shape is a mapping; a flat list is a different server."""
    respx.post(f"{BASE}/v2/search").respond(
        200, json=_payload([_web_item()]) | {"data": [_web_item()]}
    )

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_a_missing_web_key_returns_nothing(search: FirecrawlSearch) -> None:
    respx.post(f"{BASE}/v2/search").respond(200, json={"success": True, "data": {}})

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_a_null_body_returns_nothing(search: FirecrawlSearch) -> None:
    respx.post(f"{BASE}/v2/search").respond(200, json={"success": True, "data": None})

    assert await search.search("kenya news", limit=5, mode="web") == []


@respx.mock
async def test_a_non_dict_item_is_skipped(search: FirecrawlSearch) -> None:
    respx.post(f"{BASE}/v2/search").respond(200, json=_payload(["a bare string", _web_item()]))

    results = await search.search("kenya news", limit=5, mode="web")

    assert [r.url for r in results] == [WEB]


@respx.mock
async def test_the_request_carries_the_query_and_limit(search: FirecrawlSearch) -> None:
    """The one place the query and the limit are sent; getting either wrong is
    a silently-useless backup."""
    route = respx.post(f"{BASE}/v2/search").respond(200, json=_payload([_web_item()]))

    await search.search("kenya news", limit=7, mode="web")

    body = route.calls.last.request.content
    assert body is not None
    assert b"kenya news" in body
    assert b"7" in body


@respx.mock
async def test_a_configured_trailing_slash_is_not_doubled() -> None:
    """`base_url` from config may carry a `/`; `//v2/search` is a 404."""
    route = respx.post(f"{BASE}/v2/search").respond(200, json=_payload([_web_item()]))

    results = await FirecrawlSearch(base_url=f"{BASE}/", timeout_s=5.0).search(
        "kenya news", limit=5
    )

    assert route.called, "the trailing slash changed the path the adapter posts to"
    assert results, "the trailing slash made the request fail"
