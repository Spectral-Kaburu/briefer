"""Tests for the cloud Firecrawl API client (T1.3).

All mocked with respx. The API key is never read from a real environment and
never written to a real log.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from briefing.config import Config as ConfigModel
from briefing.config import load_config
from briefing.fetch.api import ApiCall, ApiFirecrawl
from briefing.fetch.errors import ErrorClass

BASE = "https://api.firecrawl.dev"
KEY = "fc-secret-key-123"


@pytest.fixture
def config(tmp_path: object) -> ConfigModel:
    from pathlib import Path

    # daily_limit is patched by the fixture below: the spec default of 20 would
    # make the cap tests slow and opaque.
    cfg = load_config(state_dir=Path(str(tmp_path)))
    cfg.firecrawl.api.daily_limit = 3
    return cfg


@pytest.fixture
def client(config: ConfigModel, tmp_path: object) -> ApiFirecrawl:
    from pathlib import Path

    return ApiFirecrawl(
        base_url=BASE,
        api_key=KEY,
        daily_limit=config.firecrawl.api.daily_limit,
        log_path=Path(str(tmp_path)) / "api_calls.jsonl",
    )


def _ok(title: str = "Story headline", markdown: str = "Full article body text.") -> dict:
    return {"success": True, "data": {"markdown": markdown, "metadata": {"title": title}}}


# --- success ---------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_scrape_success(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok("Big story")))
    result = await client.scrape("https://example.co.ke/a", reason="captcha on nation.africa")
    assert result.ok
    assert result.error_class is ErrorClass.OK
    assert result.title == "Big story"
    assert result.markdown == "Full article body text."


@pytest.mark.asyncio
@respx.mock
async def test_authorization_header_sent(client: ApiFirecrawl) -> None:
    route = respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok()))
    await client.scrape("https://example.com", reason="barrier")
    assert route.calls.last.request.headers["authorization"] == f"Bearer {KEY}"


@pytest.mark.asyncio
@respx.mock
async def test_every_call_is_logged(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok()))
    await client.scrape("https://example.com/page", reason="captcha")
    entries = [json.loads(line) for line in client.log_path.read_text().splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["url"] == "https://example.com/page"
    assert entry["domain"] == "example.com"
    assert entry["reason"] == "captcha"
    assert entry["outcome"] == "success"
    assert "ts" in entry


# --- failure modes ---------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_402_payment_required_stops_api(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(
        return_value=httpx.Response(402, json={"code": "insufficient_credits"})
    )
    result = await client.scrape("https://example.com", reason="barrier")
    assert result.error_class is ErrorClass.API_SIDE
    assert not client.can_use()


@pytest.mark.asyncio
@respx.mock
async def test_401_stops_api(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(401, text="Unauthorized"))
    result = await client.scrape("https://example.com", reason="barrier")
    assert result.error_class is ErrorClass.API_SIDE
    assert not client.can_use()


@pytest.mark.asyncio
@respx.mock
async def test_429_stops_api(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(429, text="Rate limited"))
    result = await client.scrape("https://example.com", reason="barrier")
    assert result.error_class is ErrorClass.API_SIDE
    assert not client.can_use()


@pytest.mark.asyncio
@respx.mock
async def test_failure_is_logged_too(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(402, text="no credits"))
    await client.scrape("https://example.com/x", reason="barrier")
    entry = json.loads(client.log_path.read_text().splitlines()[0])
    assert entry["outcome"] != "success"


@pytest.mark.asyncio
@respx.mock
async def test_no_key_in_log(client: ApiFirecrawl) -> None:
    """SECURITY: the key must never appear in the call log."""
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok()))
    await client.scrape("https://example.com", reason="barrier")
    assert KEY not in client.log_path.read_text()


# --- daily cap -------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_cap_blocks_after_limit(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok()))
    for i in range(3):
        assert await client.scrape(f"https://example.com/{i}", reason="barrier")
    result = await client.scrape("https://example.com/4", reason="barrier")
    assert not result.ok
    assert result.error_class is ErrorClass.API_SIDE
    assert respx.calls.call_count == 3  # the capped call never reached the network


@pytest.mark.asyncio
@respx.mock
async def test_can_use_false_at_cap(client: ApiFirecrawl) -> None:
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok()))
    assert client.can_use()
    for i in range(3):
        await client.scrape(f"https://example.com/{i}", reason="barrier")
    assert not client.can_use()


@pytest.mark.asyncio
@respx.mock
async def test_capped_call_is_still_logged(client: ApiFirecrawl) -> None:
    """SPEC: at the cap, fetches degrade to snippet-only but entries are recorded."""
    respx.post(f"{BASE}/v2/scrape").mock(return_value=httpx.Response(200, json=_ok()))
    for i in range(3):
        await client.scrape(f"https://example.com/{i}", reason="barrier")
    await client.scrape("https://capped.example", reason="barrier")
    urls = [json.loads(line)["url"] for line in client.log_path.read_text().splitlines()]
    assert "https://capped.example" in urls


# --- concurrent cap enforcement (T1.6 review finding) -----------------------


@pytest.mark.asyncio
@respx.mock
async def test_cap_holds_under_concurrent_fetches(client: ApiFirecrawl) -> None:
    """SPEC section 4: api_daily_limit caps API calls.

    The cap must survive `asyncio.gather`, which is how the pipeline (T3.1) drives
    it. Without an atomic check-and-reserve, every coroutine passed `can_use()`
    before any of them incremented, so the cap was exceeded by design.

    The mock awaits before answering, as a real transport does; without that
    suspension every coroutine would run to completion in turn and the race would
    never reproduce.
    """
    import asyncio

    async def slow_ok(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        return httpx.Response(200, json=_ok())

    respx.post(f"{BASE}/v2/scrape").mock(side_effect=slow_ok)
    results = await asyncio.gather(
        *(client.scrape(f"https://example.com/{i}", reason="barrier") for i in range(10))
    )
    assert client._calls_made == 3
    assert respx.calls.call_count == 3  # the other seven never reached the network
    assert [r.ok for r in results].count(True) == 3


@pytest.mark.asyncio
@respx.mock
async def test_disable_holds_under_concurrent_refusals(client: ApiFirecrawl) -> None:
    """SPEC section 4: an API-side refusal stops the API for the rest of the run."""
    import asyncio

    async def slow_402(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        return httpx.Response(402, text="no credits")

    respx.post(f"{BASE}/v2/scrape").mock(side_effect=slow_402)
    await asyncio.gather(
        *(client.scrape(f"https://example.com/{i}", reason="barrier") for i in range(5))
    )
    assert not client.can_use()


# --- missing key -----------------------------------------------------------


def test_missing_key_raises_clear_error(tmp_path: object) -> None:
    from pathlib import Path

    from briefing.fetch.api import MissingApiKeyError

    client = ApiFirecrawl(
        base_url=BASE,
        api_key=None,
        daily_limit=3,
        log_path=Path(str(tmp_path)) / "log.jsonl",
    )
    assert not client.can_use()
    with pytest.raises(MissingApiKeyError) as exc:
        client._require_key()
    assert "FIRECRAWL_API_KEY" in str(exc.value)


# --- log record shape ------------------------------------------------------


def test_call_record_is_typed() -> None:
    from datetime import datetime

    record = ApiCall(
        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
        url="https://a.example/x",
        domain="a.example",
        reason="captcha",
        outcome="success",
    )
    assert record.domain == "a.example"
    assert record.ts


# --- daily usage (for `briefing status`) ------------------------------------


def test_daily_usage_counts_todays_rows(tmp_path: object) -> None:
    from datetime import datetime, timedelta
    from pathlib import Path

    from briefing.fetch.api import usage_today

    log = Path(str(tmp_path)) / "api_calls.jsonl"
    now = datetime.now().astimezone()
    rows = [
        {
            "ts": now.isoformat(timespec="seconds"),
            "url": "https://a.example/x",
            "domain": "a.example",
            "reason": "captcha",
            "outcome": "success",
        },
        {
            "ts": now.isoformat(timespec="seconds"),
            "url": "https://b.example/y",
            "domain": "b.example",
            "reason": "captcha",
            "outcome": "capped",
        },
        # Yesterday: same log, outside today's window.
        {
            "ts": (now - timedelta(days=1)).isoformat(timespec="seconds"),
            "url": "https://c.example/z",
            "domain": "c.example",
            "reason": "captcha",
            "outcome": "success",
        },
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert usage_today(log, now=now) == 2


def test_daily_usage_of_an_absent_log_is_zero(tmp_path: object) -> None:
    from pathlib import Path

    from briefing.fetch.api import usage_today

    assert usage_today(Path(str(tmp_path)) / "no-such.jsonl") == 0


def test_daily_usage_of_a_malformed_log_is_zero(tmp_path: object) -> None:
    """A corrupt line must not break `status` — it reports what it can read."""
    from pathlib import Path

    from briefing.fetch.api import usage_today

    log = Path(str(tmp_path)) / "api_calls.jsonl"
    log.write_text('{"ts": "not a date"}\nnot json at all\n', encoding="utf-8")
    assert usage_today(log) == 0


# --- configurable timeout (T6.1) --------------------------------------------


def test_the_api_clients_timeout_is_configurable(tmp_path: object) -> None:
    """The local client takes a timeout; the API client had one baked in.

    T6.1 requires timeouts be configurable, so a slow API route must be tunable
    the same way a slow local instance already is.
    """
    from pathlib import Path

    api = ApiFirecrawl(
        base_url=BASE, api_key=KEY, timeout_s=7.0, log_path=Path(str(tmp_path)) / "calls.jsonl"
    )
    assert api.timeout_s == 7.0


def test_the_api_clients_timeout_defaults_to_30s(tmp_path: object) -> None:
    """The documented default must stay the default."""
    from pathlib import Path

    api = ApiFirecrawl(base_url=BASE, api_key=KEY, log_path=Path(str(tmp_path)) / "calls.jsonl")
    assert api.timeout_s == 30.0
