"""Classify fetch failures per SPEC.md section 4.

The classification table in SPEC section 4 is the single source of truth; this
module is that table. The rules are ordered, most specific first:

  1. Origin is API and the failure is a recognised API-side error  -> API_SIDE
  2. Code or status maps to DEAD / NOT_SOLVABLE                    -> that class
  3. Local instance unreachable (connection-level)                -> LOCAL_DOWN
  4. Recognised barrier status/code/timeout/challenge/empty        -> BARRIER
  5. LOCAL + auth failure                                          -> CONFIG
  6. LOCAL + plain 5xx                                             -> TRANSIENT_5XX
  7. 2xx with real content                                         -> OK

Anything unrecognised defaults to BARRIER: an unknown failure must never be
silently treated as a clean success.
"""

from __future__ import annotations

import enum
import re
import typing as t

import httpx


class ErrorClass(enum.Enum):
    """What kind of failure this was, and what the router should therefore do."""

    OK = "ok"
    BARRIER = "barrier"
    TRANSIENT_5XX = "transient_5xx"
    DEAD = "dead"
    NOT_SOLVABLE_BY_API = "not_solvable_by_api"
    CONFIG = "config"
    LOCAL_DOWN = "local_down"
    API_SIDE = "api_side"


class Origin(enum.Enum):
    """Where the failure came from, which changes how some statuses read."""

    LOCAL = "local"
    API = "api"


# SPEC section 4, row 4: the API cannot help with these.
_NOT_SOLVABLE_STATUSES = frozenset({501, 400, 405, 414, 451})
# SPEC section 4, row 3: the page is gone or will never render.
_DEAD_STATUSES = frozenset({404, 410})
# SPEC section 4, row 1: barriers.
_BARRIER_STATUSES = frozenset({403, 429, 408})
# SPEC row 2: plain 502/503/504 from the target, without an engines-failed code.
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
# SPEC section 4, row 7.
_API_SIDE_STATUSES = frozenset({402, 429, 401})

# SPEC section 4, row 3, code forms. Both v1 and v2 spellings, matched by prefix
# so a code we have not seen yet still lands in the right row (see NOTES-APIS D2).
_DEAD_CODE_PREFIXES = (
    "SCRAPE_SITE_ERROR",
    "SCRAPE_DNS_RESOLUTION_ERROR",
    "SITE_ERROR",
    "DNS_ERROR",
)
# SPEC section 4, row 1, code forms.
_BARRIER_CODE_PREFIXES = ("SCRAPE_ALL_ENGINES_FAILED", "ALL_ENGINES_FAILED")
_NOT_SOLVABLE_CODE_PREFIXES = ("THIRD_PARTY_DATA_UNSUPPORTED",)

# SPEC section 4, row 3, body forms: Dead links that arrive without a 404.
_DEAD_BODY_MARKERS = (
    "invalid url",
    "redirect loop",
    "too many redirects",
    "dns resolution",
    "name or service not known",
    "no such host",
)

# SPEC section 4, row 4, body forms: content the API will not accept either.
_NOT_SOLVABLE_BODY_MARKERS = (
    "is not supported",
    "unsupported content type",
    "binary content",
    "application/pdf",
    "content-length too long",
)

# Challenge / anti-bot pages: these arrive as HTTP 200 with real-looking bodies.
_CHALLENGE_MARKERS = (
    "just a moment",
    "enable javascript and cookies",
    "cf-browser-verification",
    "robot check",
    "checking your browser",
    "attention required",
    "verify you are a human",
    "captcha",
    "anti-bot",
    "are you a robot",
    "ddos protection by cloudflare",
)

# A page with only navigation furniture was not rendered: treat as a barrier.
_BOILERPLATE_MARKERS = (
    "cookie",
    "sign in",
    "log in",
    "menu",
    "home",
    "about",
    "contact",
    "accept all",
    "privacy",
    "terms",
    "newsletter",
    "subscribe",
    "share this",
    "advertisement",
    "skip to content",
    "toggle",
)

# A line longer than this holds prose, so the page did render.
_MAX_BOILERPLATE_LINE_CHARS = 40

# Stripped before measuring prose: comments, then any other tag.
_TAG_OR_COMMENT = re.compile(r"<!--.*?-->|<[^>]*>", re.DOTALL)


class Classification(t.NamedTuple):
    """Result of a classification: the class plus a human-readable reason."""

    error_class: ErrorClass
    reason: str


def _is_challenge_page(body: str) -> bool:
    low = body.lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _prose_words(line: str) -> list[str]:
    """Words left after stripping HTML tags/comments — i.e. real page text."""
    stripped = _TAG_OR_COMMENT.sub(" ", line)
    return [w for w in stripped.split() if any(ch.isalpha() for ch in w)]


def _is_boilerplate_only(content: str) -> bool:
    """True when content carries no substantive prose, only nav/cookie furniture.

    SPEC row 1 treats an empty or near-empty render (JS failed) as a barrier. A
    real page has at least one line of prose; a page that is *only* nav furniture
    is a page whose body never rendered.
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return True
    for line in lines:
        # A line of pure markup or punctuation holds no prose.
        if not _prose_words(line):
            continue
        # A long line holds prose, so the page did render.
        if len(line) > _MAX_BOILERPLATE_LINE_CHARS:
            return False
        if not any(marker in line.lower() for marker in _BOILERPLATE_MARKERS):
            return False
    return True


def _matches_any(code: str, prefixes: t.Sequence[str]) -> bool:
    return any(code.startswith(prefix) for prefix in prefixes)


def classify(
    *,
    status: int | None = None,
    code: str | None = None,
    body: str | None = None,
    content: str | None = None,
    origin: Origin = Origin.LOCAL,
    exc: type[Exception] | Exception | None = None,
    content_type: str | None = None,
) -> Classification:
    """Classify one failed fetch.

    Callers with no HTTP response at all pass `exc` (either the exception class
    or a caught instance); callers with a response pass `status`/`code`/`body`.
    `content` is the extracted text, when any, and drives the near-empty-render
    check.
    """
    # Rule 1: API-side errors always outrank everything else, because when the
    # API is refusing (credits, rate limit, auth) nothing else can be tried.
    if origin is Origin.API and (status in _API_SIDE_STATUSES or code == "insufficient_credits"):
        return Classification(ErrorClass.API_SIDE, "cloud API refused the request")

    # Rule 2: dead and not-solvable are decided before connection errors, because
    # a 404 or a 451 is a real answer about the page, not a transport failure.
    if code is not None and _matches_any(code, _DEAD_CODE_PREFIXES):
        return Classification(ErrorClass.DEAD, f"page is permanently unavailable ({code})")
    if status in _DEAD_STATUSES:
        return Classification(ErrorClass.DEAD, f"page not found (HTTP {status})")
    if code is not None and _matches_any(code, _NOT_SOLVABLE_CODE_PREFIXES):
        return Classification(
            ErrorClass.NOT_SOLVABLE_BY_API, f"format the API cannot handle ({code})"
        )
    if status in _NOT_SOLVABLE_STATUSES:
        # 'Invalid URL' is a dead link even at 400, so check the body first.
        if body and "invalid url" in body.lower():
            return Classification(ErrorClass.DEAD, "the URL itself is invalid")
        return Classification(
            ErrorClass.NOT_SOLVABLE_BY_API, f"the API cannot help (HTTP {status})"
        )
    low_body = (body or "").lower()
    if _is_challenge_page(body or ""):
        return Classification(
            ErrorClass.BARRIER,
            "the site answered with a challenge page, so a retry may get past it",
        )
    if any(marker in low_body for marker in _DEAD_BODY_MARKERS):
        return Classification(ErrorClass.DEAD, "the page is dead or its URL is invalid")
    if any(marker in low_body for marker in _NOT_SOLVABLE_BODY_MARKERS):
        return Classification(
            ErrorClass.NOT_SOLVABLE_BY_API, "the page is a format the API cannot read"
        )

    # Rule 3: no HTTP response at all, so we only have an exception.
    if exc is not None and status is None:
        exc_type = exc if isinstance(exc, type) else type(exc)
        # A timeout that says *where* it failed decides the class (SPEC row 6 vs
        # row 1): reaching Firecrawl itself means LOCAL_DOWN; the target timing
        # out mid-scrape is a barrier. Both are timeouts, so test the specific
        # classes before the general ones.
        if issubclass(exc_type, httpx.ConnectTimeout):
            return Classification(ErrorClass.LOCAL_DOWN, "could not reach local Firecrawl in time")
        if issubclass(exc_type, (httpx.ConnectError, ConnectionError)):
            return Classification(ErrorClass.LOCAL_DOWN, "local Firecrawl is not reachable")
        if issubclass(exc_type, (httpx.TimeoutException, TimeoutError)):
            # A read/pool timeout with no target-side status: the site blocked us.
            return Classification(ErrorClass.BARRIER, "the scrape timed out, so a retry may help")
        return Classification(ErrorClass.LOCAL_DOWN, f"local Firecrawl raised {exc_type.__name__}")

    # Rule 4: codes outrank statuses — a barrier code on a 503 is still a barrier.
    if code is not None and _matches_any(code, _BARRIER_CODE_PREFIXES):
        return Classification(ErrorClass.BARRIER, f"all local scrape engines failed ({code})")

    # Rule 5: barriers (SPEC row 1) and transient 5xx (SPEC row 2).
    if status in _BARRIER_STATUSES:
        return Classification(
            ErrorClass.BARRIER, f"the site blocked the local scrape (HTTP {status})"
        )
    if status in _TRANSIENT_STATUSES and origin is Origin.LOCAL:
        # A plain 5xx from the target, without an engines-failed code: one local
        # retry, and if it still fails, snippet only. Never whitelisted, never API.
        return Classification(
            ErrorClass.TRANSIENT_5XX, f"the site had a server error (HTTP {status})"
        )

    # Rule 6: a local auth failure is a misconfiguration, not a barrier. This must
    # be decided before the content check: a failed response carries no content,
    # and an empty body must not be read as "the page rendered empty".
    if origin is Origin.LOCAL and status in {401, 407}:
        return Classification(ErrorClass.CONFIG, "local Firecrawl rejected our credentials")

    # Rule 6b: an empty or furniture-only render. Placed after the auth check so
    # only a response that actually reached the page can read as a failed render.
    if content is not None and (not content.strip() or _is_boilerplate_only(content)):
        return Classification(
            ErrorClass.BARRIER, "the page rendered empty, so a retry may get more"
        )

    # Rule 7: content that looks fine.
    if status is not None and 200 <= status < 300:
        return Classification(ErrorClass.OK, "extracted locally")

    return Classification(ErrorClass.BARRIER, "the local scrape failed for an unrecognised reason")
