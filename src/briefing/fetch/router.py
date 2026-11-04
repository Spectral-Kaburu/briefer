"""Decide where a URL gets fetched from, per SPEC section 4.

This module holds the policy the classifier only implies: local first, the cloud
API only for barriers (paid for once, then remembered), snippet-only for
everything the API cannot fix. Every branch maps to one row of the SPEC §4 table
and invents no row of its own.
"""

from __future__ import annotations

import asyncio
import enum
import typing as t

from .. import extract as _extract
from ..log import get_logger
from ..whitelist import Whitelist, normalize_host
from .api import ApiFirecrawl, ApiScrapeResult
from .errors import ErrorClass
from .local import LocalFirecrawl, LocalScrapeResult

LOG = get_logger("briefing.fetch.router")

# The one backoff SPEC allows between a transient attempt and its retry. SPEC
# names no number, so it is a constructor argument; T6.1 makes it configurable.
_TRANSIENT_BACKOFF_S = 0.5


class Method(enum.Enum):
    """How a page was obtained, which drives the UI badge."""

    LOCAL = "local"
    API = "api"
    SNIPPET = "snippet"
    WIKI = "wiki"
    SO = "so"
    DDGA = "ddga"


class FetchResult(t.NamedTuple):
    """Outcome of routing one URL.

    `warning` is a plain-English note for the user (API spent, local down, snippet
    only); it is empty on a clean local success. `error_class` is the
    classification of the failure that was survived, and stays set on a result
    that later succeeded through the API.
    """

    method: Method
    ok: bool
    markdown: str = ""
    heading: str = ""
    summary: str = ""
    status_code: int | None = None
    error_class: ErrorClass = ErrorClass.OK
    warning: str = ""


class Router:
    """Route one fetch through local Firecrawl, the cloud API, or the snippet.

    The clients and the whitelist are injected, so the router holds no I/O state
    of its own and is driven by respx-mocked transports in tests.
    """

    def __init__(
        self,
        local: LocalFirecrawl,
        api: ApiFirecrawl,
        whitelist: Whitelist,
        no_api: bool = False,
        prefer_api_for_whitelisted: bool = False,
        backoff_s: float = _TRANSIENT_BACKOFF_S,
    ) -> None:
        self.local = local
        self.api = api
        self.whitelist = whitelist
        self.no_api = no_api
        self.prefer_api_for_whitelisted = prefer_api_for_whitelisted
        self.backoff_s = backoff_s

    async def fetch(self, url: str, snippet: str = "") -> FetchResult:
        """Fetch one URL, degrading by the SPEC §4 rules. Never raises.

        `snippet` is the search result's own text, which is all that survives
        when every fetch path gives up.
        """
        result = await self._route(url, snippet)
        # One seam for every branch of the SPEC §4 table, so `--verbose` reports
        # the decision rather than only the summary of the run that made it.
        LOG.debug(
            "routed %s -> %s%s",
            url,
            result.method.value,
            f" ({result.warning})" if result.warning else "",
        )
        return result

    async def _route(self, url: str, snippet: str) -> FetchResult:
        host = normalize_host(url)

        if self.prefer_api_for_whitelisted and self.whitelist.is_whitelisted(host):
            return await self._via_api(url, host, "domain is whitelisted", snippet)

        local = await self.local.scrape(url)
        if local.ok:
            return self._local_result(local)

        return await self._failed(url, host, local, snippet)

    async def _failed(
        self,
        url: str,
        host: str,
        result: LocalScrapeResult,
        snippet: str,
    ) -> FetchResult:
        """Route a failed local scrape by the class `local.py` already assigned."""
        error_class = result.error_class
        reason = result.reason

        if error_class is ErrorClass.BARRIER:
            if self.no_api:
                # Without the API path there is no retry and no verified entry, so
                # recording the domain would only litter the whitelist.
                return self._snippet(snippet, reason, error_class)
            # SPEC: whitelist the domain first, then retry it through the API.
            await self.whitelist.add_async(host, reason=reason, source="auto")
            return await self._via_api(url, host, reason, snippet, error_class=error_class)

        if error_class is ErrorClass.TRANSIENT_5XX:
            return await self._retry_once(url, result, snippet)

        if error_class is ErrorClass.LOCAL_DOWN:
            if self.no_api:
                return self._snippet(snippet, reason, error_class)
            if not self.api.can_use():
                return self._snippet(
                    snippet,
                    f"{reason}, and the cloud API is not available either",
                    error_class,
                )
            # SPEC: use the API for this run only, and write no whitelist entry.
            return await self._via_api(
                url, host, reason, snippet, error_class=error_class, local_down=True
            )

        # DEAD, NOT_SOLVABLE_BY_API and CONFIG end here: snippet only, no
        # whitelist, no API, but still a clear message (SPEC §4 rows 3-5).
        return self._snippet(snippet, reason, error_class)

    async def _retry_once(self, url: str, result: LocalScrapeResult, snippet: str) -> FetchResult:
        """The single local retry SPEC allows for a transient 5xx."""
        if self.backoff_s > 0:
            await asyncio.sleep(self.backoff_s)
        retry = await self.local.scrape(url)
        if retry.ok:
            return self._local_result(retry)
        return self._snippet(snippet, retry.reason or result.reason, retry.error_class)

    async def _via_api(
        self,
        url: str,
        host: str,
        reason: str,
        snippet: str,
        error_class: ErrorClass = ErrorClass.OK,
        local_down: bool = False,
    ) -> FetchResult:
        """The paid path. Callers decide whether it is reachable at all."""
        result = await self.api.scrape(url, reason)
        if result.ok:
            # The first success is what makes the entry verified (SPEC §4).
            await self.whitelist.record_api_call_async(host, success=True)
            warning = f"{reason}; fetched through the cloud API" if local_down else result.reason
            return self._api_result(result, warning=warning, error_class=error_class)

        # A failed API call leaves the entry unverified so `prune` can clear it.
        await self.whitelist.record_api_call_async(host, success=False)
        if result.error_class is ErrorClass.API_SIDE:
            warning = (
                "the cloud API refused the request and is disabled for this run, "
                "so only the search result is available"
            )
        elif local_down:
            warning = f"{reason}, and the cloud API fallback failed too"
        else:
            warning = reason
        return self._snippet(snippet, warning, result.error_class)

    def _local_result(self, result: LocalScrapeResult) -> FetchResult:
        extracted = _extract.extract(result.markdown, title=result.title or None)
        return FetchResult(
            method=Method.LOCAL,
            ok=True,
            markdown=result.markdown,
            heading=extracted.heading,
            summary=extracted.summary,
            status_code=result.status_code,
        )

    def _api_result(
        self,
        result: ApiScrapeResult,
        warning: str,
        error_class: ErrorClass,
    ) -> FetchResult:
        extracted = _extract.extract(result.markdown, title=result.title or None)
        return FetchResult(
            method=Method.API,
            ok=True,
            markdown=result.markdown,
            heading=extracted.heading,
            summary=extracted.summary,
            error_class=error_class,
            warning=warning,
        )

    def _snippet(
        self,
        snippet: str,
        warning: str,
        error_class: ErrorClass = ErrorClass.BARRIER,
    ) -> FetchResult:
        return FetchResult(
            method=Method.SNIPPET,
            ok=False,
            markdown=snippet,
            warning=warning,
            error_class=error_class,
        )

    async def local_health(self) -> bool:
        """True when the local instance answers its health endpoint."""
        return await self.local.health()
