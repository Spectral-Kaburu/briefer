"""Tests for the fetch error classifier (T1.1).

One parametrized case per row of the SPEC.md section 4 classification table,
plus the captcha and empty-content detection fixtures the acceptance names.
"""

from __future__ import annotations

import httpx
import pytest

from briefing.fetch.errors import ErrorClass, Origin
from briefing.fetch.errors import classify as _classify


def classify(**kwargs: object) -> ErrorClass:
    """Convenience: the classification's class, for single-line assertions."""
    return _classify(**kwargs).error_class  # type: ignore[arg-type]


GOOD = "A substantial body of article text that clearly is not a challenge page."

# --- BARRIER (SPEC row 1) ---------------------------------------------------
# captcha / anti-bot / challenge page; 403; 429; 408 or timeout;
# SCRAPE_ALL_ENGINES_FAILED; empty or near-empty content


@pytest.mark.parametrize("status", [403, 429, 408])
def test_barrier_statuses(status: int) -> None:
    assert classify(status=status, content=GOOD, origin=Origin.LOCAL) is ErrorClass.BARRIER


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout, httpx.PoolTimeout, TimeoutError],
)
def test_timeouts_are_barrier(exc: type[Exception]) -> None:
    """Read/pool timeouts mean the target site stalled us: SPEC row 1."""
    assert classify(exc=exc, content=GOOD, origin=Origin.LOCAL) is ErrorClass.BARRIER


def test_engines_failed_code_is_barrier() -> None:
    assert (
        classify(code="SCRAPE_ALL_ENGINES_FAILED", content=GOOD, origin=Origin.LOCAL)
        is ErrorClass.BARRIER
    )


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n\t  \n ",
        "Menu\nHome\nAbout\nContact\nSign in\nCookie settings\nAccept all cookies\n",
        "<!-- comment only, no words at all -->",
    ],
)
def test_empty_or_near_empty_content_is_barrier(content: str) -> None:
    assert classify(status=200, content=content, origin=Origin.LOCAL) is ErrorClass.BARRIER


@pytest.mark.parametrize(
    "body",
    [
        "Just a moment... Enable JavaScript and cookies to continue",
        "<title>Attention Required! | Cloudflare</title>",
        "Checking your browser before accessing",
        "captcha required to continue",
        "Please verify you are a human",
        "cf-browser-verification robot check",
        "Access denied by the site owner (anti-bot protection)",
    ],
)
def test_challenge_page_bodies_are_barrier(body: str) -> None:
    assert classify(status=200, body=body, content=GOOD, origin=Origin.LOCAL) is ErrorClass.BARRIER


def test_genuine_short_article_is_not_barrier() -> None:
    """A legitimately short page must not be mistaken for a failed render."""
    text = "The mayor resigned this morning after a council vote."
    assert classify(status=200, content=text, origin=Origin.LOCAL) is ErrorClass.OK


# --- TRANSIENT 5xx (SPEC row 2) ---------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_plain_server_error_is_transient(status: int) -> None:
    assert classify(status=status, content=GOOD, origin=Origin.LOCAL) is ErrorClass.TRANSIENT_5XX


def test_5xx_with_engines_failed_code_stays_barrier() -> None:
    """The code wins over the status: an engines-failed 503 is still a barrier."""
    assert (
        classify(status=503, code="SCRAPE_ALL_ENGINES_FAILED", content=GOOD, origin=Origin.LOCAL)
        is ErrorClass.BARRIER
    )


# --- DEAD (SPEC row 3) -----------------------------------------------------


@pytest.mark.parametrize("status", [404, 410])
def test_not_found_is_dead(status: int) -> None:
    assert classify(status=status, content=GOOD, origin=Origin.LOCAL) is ErrorClass.DEAD


@pytest.mark.parametrize(
    "code", ["SCRAPE_SITE_ERROR", "SCRAPE_DNS_RESOLUTION_ERROR", "SITE_ERROR", "DNS_ERROR"]
)
def test_v1_and_v2_dead_codes(code: str) -> None:
    assert classify(code=code, content=GOOD, origin=Origin.LOCAL) is ErrorClass.DEAD


def test_redirect_loop_body_is_dead() -> None:
    assert classify(
        body="Too many redirects: redirect loop", content=GOOD, origin=Origin.LOCAL
    ) is (ErrorClass.DEAD)


def test_invalid_url_is_dead() -> None:
    assert (
        classify(body="Invalid URL supplied", content=GOOD, origin=Origin.LOCAL) is ErrorClass.DEAD
    )


# --- NOT SOLVABLE BY API (SPEC row 4) --------------------------------------


@pytest.mark.parametrize("status", [501, 405, 414, 451])
def test_not_solvable_by_api_statuses(status: int) -> None:
    assert (
        classify(status=status, content=GOOD, origin=Origin.LOCAL) is ErrorClass.NOT_SOLVABLE_BY_API
    )


def test_400_is_not_solvable_by_api() -> None:
    assert classify(status=400, content=GOOD, origin=Origin.LOCAL) is ErrorClass.NOT_SOLVABLE_BY_API


def test_400_with_invalid_url_is_dead_not_unsolvable() -> None:
    """'Invalid URL' is a dead link, so DEAD must outrank the generic 400."""
    assert classify(status=400, body="Invalid URL", content=GOOD, origin=Origin.LOCAL) is (
        ErrorClass.DEAD
    )


def test_binary_content_type_is_not_solvable() -> None:
    body = "content-type: application/pdf is not supported"
    assert classify(body=body, content=GOOD, origin=Origin.LOCAL) is ErrorClass.NOT_SOLVABLE_BY_API


# --- CONFIG (SPEC row 5) ---------------------------------------------------


@pytest.mark.parametrize("status", [401, 407])
def test_local_auth_failure_is_config_error(status: int) -> None:
    assert classify(status=status, content=GOOD, origin=Origin.LOCAL) is ErrorClass.CONFIG


def test_api_401_is_api_side_not_config() -> None:
    """401 from the API is an auth problem on the API, not a local misconfig."""
    assert classify(status=401, content=GOOD, origin=Origin.API) is ErrorClass.API_SIDE


# --- LOCAL DOWN (SPEC row 6) ------------------------------------------------


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError, httpx.ConnectTimeout, ConnectionRefusedError, OSError]
)
def test_local_unreachable_is_local_down(exc: type[Exception]) -> None:
    assert classify(exc=exc, content=None, origin=Origin.LOCAL) is ErrorClass.LOCAL_DOWN


def test_local_connection_refused_via_exc_is_local_down() -> None:
    assert (
        classify(exc=ConnectionRefusedError("refused"), content=None, origin=Origin.LOCAL)
        is ErrorClass.LOCAL_DOWN
    )


# --- API-SIDE (SPEC row 7) -------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"), [(402, None), (429, None), (401, None), (402, "insufficient_credits")]
)
def test_api_side_errors_when_origin_is_api(status: int, code: str | None) -> None:
    assert (
        classify(status=status, code=code, content=GOOD, origin=Origin.API) is ErrorClass.API_SIDE
    )


# --- origin overlap: the same status, two classes -------------------------


def test_403_is_barrier_locally_but_api_429_is_api_side() -> None:
    assert classify(status=403, content=GOOD, origin=Origin.LOCAL) is ErrorClass.BARRIER
    assert classify(status=429, content=GOOD, origin=Origin.API) is ErrorClass.API_SIDE


# --- unmapped / defensive --------------------------------------------------


def test_unmapped_status_defaults_to_barrier() -> None:
    """Never silently treat an unknown failure as a clean success."""
    assert classify(status=418, content=GOOD, origin=Origin.LOCAL) is ErrorClass.BARRIER


def test_success_returns_ok() -> None:
    assert classify(status=200, content=GOOD, origin=Origin.LOCAL) is ErrorClass.OK


def test_reason_is_human_readable() -> None:
    """Every failure class must yield a plain-English reason, for the UI badge."""
    result = _classify(status=403, content=GOOD, origin=Origin.LOCAL)
    assert isinstance(result.reason, str) and result.reason
    assert len(result.reason) < 200


def test_reason_names_the_blocking_status() -> None:
    """The reason is shown to users, so it must include the actionable number."""
    result = _classify(status=403, content=GOOD, origin=Origin.LOCAL)
    assert "403" in result.reason


def test_local_down_reason_names_the_service() -> None:
    """SPEC requires the fix hint to say Firecrawl is the thing that is down."""
    result = _classify(exc=ConnectionRefusedError(), content=None, origin=Origin.LOCAL)
    assert "Firecrawl" in result.reason
