"""Tests for the fetch router (T1.6).

Scenario tests for every row of the SPEC section 4 classification table,
asserting whitelist side effects and API call counts.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from briefing.fetch.api import ApiFirecrawl
from briefing.fetch.errors import ErrorClass
from briefing.fetch.local import LocalFirecrawl
from briefing.fetch.router import FetchResult, Method, Router
from briefing.whitelist import Whitelist

LOCAL = "http://localhost:3002"
API = "https://api.firecrawl.dev"
KEY = "fc-key"

ARTICLE = (
    "# Story headline\n\nThe commission appointed a new chairperson today after a long search."
)


def _local_ok(markdown: str = ARTICLE, title: str = "Story headline") -> httpx.Response:
    return httpx.Response(
        200,
        json={"success": True, "data": {"markdown": markdown, "metadata": {"title": title}}},
    )


@pytest.fixture
def wl(tmp_path: Path) -> Whitelist:
    return Whitelist(tmp_path / "whitelist.yaml")


@pytest.fixture
def router(tmp_path: Path, wl: Whitelist) -> Router:
    local = LocalFirecrawl(base_url=LOCAL)
    api = ApiFirecrawl(base_url=API, api_key=KEY, daily_limit=5, log_path=tmp_path / "calls.jsonl")
    return Router(local=local, api=api, whitelist=wl, no_api=False)


def _api_call_count(api: ApiFirecrawl) -> int:
    return api._calls_made


# --- happy path ------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_local_success_uses_local(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=_local_ok())
    result = await router.fetch("https://nation.africa/story")
    assert result.method is Method.LOCAL
    assert result.ok
    assert result.markdown == ARTICLE
    assert result.heading == "Story headline"
    assert _api_call_count(router.api) == 0


@pytest.mark.asyncio
@respx.mock
async def test_local_success_does_not_whitelist(router: Router) -> None:
    """A clean local scrape must never touch the whitelist."""
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=_local_ok())
    await router.fetch("https://nation.africa/story")
    assert router.whitelist.list() == []


# --- BARRIER: whitelist + retry via API ------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_barrier_whitelists_and_retries_via_api(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="Forbidden"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok("API text", "API heading"))
    result = await router.fetch("https://example.co.ke/story")
    assert result.method is Method.API
    assert result.ok
    assert router.whitelist.is_whitelisted("example.co.ke")
    assert _api_call_count(router.api) == 1


@pytest.mark.asyncio
@respx.mock
async def test_barrier_whitelist_reason_records_the_status(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="Forbidden"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())
    await router.fetch("https://example.co.ke/story")
    entry = router.whitelist.list()[0]
    assert entry.domain == "example.co.ke"
    assert entry.reason
    assert entry.source == "auto"


@pytest.mark.asyncio
@respx.mock
async def test_captcha_barrier_whitelists(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(
        return_value=httpx.Response(200, text="Just a moment... enable javascript and cookies")
    )
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())
    result = await router.fetch("https://example.co.ke/x")
    assert result.method is Method.API
    assert router.whitelist.is_whitelisted("example.co.ke")


@pytest.mark.asyncio
@respx.mock
async def test_empty_content_barrier_whitelists(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"markdown": ""}})
    )
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())
    await router.fetch("https://empty.example/x")
    assert router.whitelist.is_whitelisted("empty.example")


@pytest.mark.asyncio
@respx.mock
async def test_api_failure_after_barrier_leaves_entry_unverified(router: Router) -> None:
    """SPEC: if the API also fails, the entry stays unverified and prune removes it."""
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    respx.post(f"{API}/v2/scrape").mock(return_value=httpx.Response(500, text="api error"))
    result = await router.fetch("https://bothfail.example/x")
    assert not result.ok
    assert router.whitelist.list()[0].verified is False
    router.whitelist.prune()
    assert router.whitelist.list() == []


@pytest.mark.asyncio
@respx.mock
async def test_api_success_marks_entry_verified(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())
    await router.fetch("https://verify.example/x")
    entry = router.whitelist.list()[0]
    assert entry.verified is True


# --- DEAD: snippet only, no whitelist, no API ------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_404_yields_snippet_only_and_no_whitelist(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(404, text="Not Found"))
    result = await router.fetch("https://dead.example/x")
    assert result.method is Method.SNIPPET
    assert not result.ok
    assert router.whitelist.list() == []
    assert _api_call_count(router.api) == 0


@pytest.mark.asyncio
@respx.mock
async def test_410_yields_snippet_only(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(410, text="Gone"))
    result = await router.fetch("https://dead.example/x")
    assert result.method is Method.SNIPPET
    assert router.whitelist.list() == []


@pytest.mark.asyncio
@respx.mock
async def test_dns_error_yields_snippet_only(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(
        return_value=httpx.Response(500, json={"code": "SCRAPE_DNS_RESOLUTION_ERROR"})
    )
    result = await router.fetch("https://nodns.example/x")
    assert result.method is Method.SNIPPET
    assert router.whitelist.list() == []
    assert _api_call_count(router.api) == 0


# --- NOT SOLVABLE: snippet only -------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_501_yields_snippet_only(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(501, text="nope"))
    result = await router.fetch("https://binary.example/x")
    assert result.method is Method.SNIPPET
    assert router.whitelist.list() == []
    assert _api_call_count(router.api) == 0


@pytest.mark.asyncio
@respx.mock
async def test_451_yields_snippet_only(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(451, text="legal"))
    result = await router.fetch("https://blocked.example/x")
    assert result.method is Method.SNIPPET
    assert _api_call_count(router.api) == 0


# --- TRANSIENT: one local retry, then snippet -----------------------------


@pytest.mark.asyncio
@respx.mock
async def test_500_retries_once_then_snippet(router: Router) -> None:
    route = respx.post(f"{LOCAL}/v2/scrape").mock(
        return_value=httpx.Response(500, text="Server Error")
    )
    result = await router.fetch("https://flaky.example/x")
    assert result.method is Method.SNIPPET
    assert route.call_count == 2  # the original plus one backoff retry
    assert router.whitelist.list() == []  # never whitelisted
    assert _api_call_count(router.api) == 0  # never the API


@pytest.mark.asyncio
@respx.mock
async def test_transient_recovers_on_retry(router: Router) -> None:
    route = respx.post(f"{LOCAL}/v2/scrape")
    route.side_effect = [
        httpx.Response(503, text="Service Unavailable"),
        _local_ok(),
    ]
    result = await router.fetch("https://recovers.example/x")
    assert result.ok
    assert result.method is Method.LOCAL
    assert router.whitelist.list() == []


# --- LOCAL DOWN: API for this run, no whitelist ---------------------------


@pytest.mark.asyncio
@respx.mock
async def test_connection_refused_uses_api_without_whitelisting(router: Router) -> None:
    """SPEC row 6: API for this run only, do not write whitelist entries."""
    respx.post(f"{LOCAL}/v2/scrape").mock(side_effect=httpx.ConnectError("refused"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok("API text", "API heading"))
    result = await router.fetch("https://down.example/x")
    assert result.method is Method.API
    assert result.ok
    assert router.whitelist.list() == []  # nothing written
    assert "not reachable" in result.warning


@pytest.mark.asyncio
@respx.mock
async def test_connect_timeout_is_local_down(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(side_effect=httpx.ConnectTimeout("timed out"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())
    result = await router.fetch("https://down.example/x")
    assert result.method is Method.API
    assert router.whitelist.list() == []


@pytest.mark.asyncio
@respx.mock
async def test_local_down_degraded_to_snippet_when_no_api(tmp_path: Path, wl: Whitelist) -> None:
    local = LocalFirecrawl(base_url=LOCAL)
    api = ApiFirecrawl(base_url=API, api_key=None, daily_limit=5, log_path=tmp_path / "c.jsonl")
    router = Router(local=local, api=api, whitelist=wl)
    respx.post(f"{LOCAL}/v2/scrape").mock(side_effect=httpx.ConnectError("refused"))
    result = await router.fetch("https://down.example/x")
    assert result.method is Method.SNIPPET
    assert result.warning


# --- API-SIDE: stop using API, degrade to snippet -------------------------


@pytest.mark.asyncio
@respx.mock
async def test_api_402_stops_api_for_the_rest_of_the_run(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    respx.post(f"{API}/v2/scrape").mock(return_value=httpx.Response(402, text="no credits"))
    result = await router.fetch("https://nocredits.example/x")
    assert not result.ok
    assert result.warning
    assert not router.api.can_use()


@pytest.mark.asyncio
@respx.mock
async def test_after_api_refusal_later_barrier_degrades_to_snippet(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    respx.post(f"{API}/v2/scrape").mock(return_value=httpx.Response(429, text="rate limited"))
    await router.fetch("https://first.example/x")
    result = await router.fetch("https://second.example/x")
    assert result.method is Method.SNIPPET
    assert result.warning


# --- CONFIG ----------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_local_401_is_a_config_error_with_a_fix_hint(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(401, text="Unauthorized"))
    result = await router.fetch("https://x.example/a")
    assert not result.ok
    assert result.warning
    assert router.whitelist.list() == []
    assert _api_call_count(router.api) == 0


# --- prefer_api_for_whitelisted -------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_whitelisted_domain_skips_local_when_configured(
    tmp_path: Path, wl: Whitelist
) -> None:
    local = LocalFirecrawl(base_url=LOCAL)
    api = ApiFirecrawl(base_url=API, api_key=KEY, daily_limit=5, log_path=tmp_path / "c.jsonl")
    router = Router(local=local, api=api, whitelist=wl, prefer_api_for_whitelisted=True)
    wl.add("example.co.ke", reason="captcha")
    wl.record_api_call("example.co.ke", success=True)

    route = respx.post(f"{LOCAL}/v2/scrape").mock(return_value=_local_ok())
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok("API text", "API heading"))
    result = await router.fetch("https://example.co.ke/story")
    assert result.method is Method.API
    assert route.call_count == 0  # local was never contacted


@pytest.mark.asyncio
@respx.mock
async def test_whitelisted_domain_still_uses_local_by_default(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=_local_ok())
    router.whitelist.add("example.co.ke", reason="captcha")
    result = await router.fetch("https://example.co.ke/story")
    assert result.method is Method.LOCAL


# --- --no-api --------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_no_api_never_calls_the_api(tmp_path: Path, wl: Whitelist) -> None:
    local = LocalFirecrawl(base_url=LOCAL)
    api = ApiFirecrawl(base_url=API, api_key=KEY, daily_limit=5, log_path=tmp_path / "c.jsonl")
    router = Router(local=local, api=api, whitelist=wl, no_api=True)
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    api_route = respx.post(f"{API}/v2/scrape")
    result = await router.fetch("https://example.co.ke/x")
    assert result.method is Method.SNIPPET
    assert api_route.call_count == 0
    assert _api_call_count(router.api) == 0


@pytest.mark.asyncio
@respx.mock
async def test_no_api_still_does_not_whitelist(tmp_path: Path, wl: Whitelist) -> None:
    local = LocalFirecrawl(base_url=LOCAL)
    api = ApiFirecrawl(base_url=API, api_key=None, daily_limit=5, log_path=tmp_path / "c.jsonl")
    router = Router(local=local, api=api, whitelist=wl, no_api=True)
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    await router.fetch("https://example.co.ke/x")
    assert router.whitelist.list() == []


# --- API cap degrades but still records -----------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_at_api_cap_entries_are_still_recorded(tmp_path: Path, wl: Whitelist) -> None:
    local = LocalFirecrawl(base_url=LOCAL)
    api = ApiFirecrawl(base_url=API, api_key=KEY, daily_limit=1, log_path=tmp_path / "c.jsonl")
    router = Router(local=local, api=api, whitelist=wl)
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())

    await router.fetch("https://one.example/a")
    result = await router.fetch("https://two.example/b")
    assert result.method is Method.SNIPPET
    # The entry that exhausted nothing was recorded; the capped one is recorded.
    assert {e.domain for e in router.whitelist.list()} == {"one.example", "two.example"}


# --- result payload -------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_snippet_method_carries_the_search_snippet(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(404, text="nope"))
    result = await router.fetch("https://dead.example/x", snippet="A DDG snippet.")
    assert result.method is Method.SNIPPET
    assert result.markdown == "A DDG snippet."


@pytest.mark.asyncio
@respx.mock
async def test_result_is_a_typed_record(router: Router) -> None:
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=_local_ok())
    result = await router.fetch("https://x.example/a")
    assert isinstance(result, FetchResult)
    assert result.method in set(Method)


@pytest.mark.asyncio
@respx.mock
async def test_origin_of_the_failing_call_is_local_for_barriers(router: Router) -> None:
    """The router must tell the classifier the failure came from local."""
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(403, text="nope"))
    respx.post(f"{API}/v2/scrape").mock(return_value=_local_ok())
    result = await router.fetch("https://x.example/a")
    assert result.error_class is ErrorClass.BARRIER


@pytest.mark.asyncio
@respx.mock
async def test_health_up(router: Router) -> None:
    respx.get(f"{LOCAL}/").mock(return_value=httpx.Response(200, text="ok"))
    assert await router.local_health() is True


@pytest.mark.asyncio
@respx.mock
async def test_one_bad_url_never_kills_fetch(router: Router) -> None:
    """A 404 still returns a result object rather than raising."""
    respx.post(f"{LOCAL}/v2/scrape").mock(return_value=httpx.Response(404, text="nope"))
    result = await router.fetch("https://dead.example/x")
    assert result.method is Method.SNIPPET
