"""Client for the self-hosted Firecrawl scrape and search endpoints.

One `scrape` call returns a `LocalScrapeResult` either way: a success carries the
markdown and heading, a failure carries the SPEC section 4 classification.
`scrape` never raises on a fetch failure, so one bad URL can never kill a run.

`FirecrawlSearch` is the separate `/v2/search` client for the ddgs backup and
carries the same rule for the opposite reason: a failed backup is the *last* event
in a search that has already failed, so there is nothing left to degrade to and
no exception worth raising.
"""

from __future__ import annotations

import logging
import typing as t

import httpx

from ..search import SearchResult
from .errors import ErrorClass, Origin, classify

_SCRAPE_PATH = "/v2/scrape"
_SEARCH_PATH = "/v2/search"
_FIRST_H1_CHARS = 200

#: The instance the SPEC and the shipped config example both name.
BASE = "http://localhost:3002"

logger = logging.getLogger(__name__)


class LocalScrapeResult(t.NamedTuple):
    """Outcome of one local scrape — data on success, classification on failure."""

    ok: bool
    markdown: str = ""
    title: str = ""
    status_code: int | None = None
    error_class: ErrorClass = ErrorClass.OK
    reason: str = ""


class LocalFirecrawl:
    """Async client for a self-hosted Firecrawl instance.

    `timeout_s` bounds each call; `client` is injectable so tests can reuse one
    transport. This instance is shared by the router and must not hold state.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3002",
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.AsyncClient:
        # A per-instance client is created lazily so a client constructed off the
        # event loop (e.g. in a fixture) still works when awaited later.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def scrape(self, url: str) -> LocalScrapeResult:
        """Scrape one URL through the local instance.

        Never raises for a fetch failure: a transport error or a non-2xx response
        becomes a classified `LocalScrapeResult`. Only a programming error in the
        JSON shape can raise, and that is classified too.
        """
        client = self._http()
        try:
            response = await client.post(
                f"{self.base_url}{_SCRAPE_PATH}",
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ConnectTimeout, OSError) as exc:
            classified = classify(exc=exc, origin=Origin.LOCAL)
            return LocalScrapeResult(
                ok=False, error_class=classified.error_class, reason=classified.reason
            )

        return self._from_response(response)

    def _from_response(self, response: httpx.Response) -> LocalScrapeResult:
        body = response.text
        code = _error_code(response)
        # The classifier needs the extracted content only to spot a near-empty
        # render, so parse first when the response looks like a scrape result.
        data = _scrape_data(response)
        markdown = data.get("markdown") or ""
        status = (
            data.get("metadata", {}).get("statusCode")
            if isinstance(data.get("metadata"), dict)
            else None
        )

        classified = classify(
            status=response.status_code,
            code=code,
            body=body,
            content=markdown,
            origin=Origin.LOCAL,
        )
        if classified.error_class is not ErrorClass.OK:
            return LocalScrapeResult(
                ok=False,
                status_code=status if isinstance(status, int) else None,
                error_class=classified.error_class,
                reason=classified.reason,
            )

        return LocalScrapeResult(
            ok=True,
            markdown=markdown,
            title=data.get("metadata", {}).get("title") or _first_h1(markdown),
            status_code=status if isinstance(status, int) else response.status_code,
            error_class=ErrorClass.OK,
            reason=classified.reason,
        )

    async def health(self) -> bool:
        """True when the local instance answers a plain GET on its root."""
        try:
            response = await self._http().get(f"{self.base_url}/")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ConnectTimeout, OSError):
            return False
        return response.status_code < 500


class FirecrawlSearch:
    """The ddgs backup: POST `/v2/search` and normalize the hits (T8.2).

    Separate from `LocalFirecrawl` because the two clients have incompatible
    failure contracts. A scrape failure is a *classify* input (SPEC §4's whole
    table), so `LocalFirecrawl.scrape` must return it; a backup failure is a
    *fallback* outcome with nothing left to fall back to, so this one swallows it
    and reports "no results". Putting both under one `except` would let a bad
    search response become a classified scrape failure, which is not a thing.

    It never raises. A connect error, a timeout, a non-2xx, malformed JSON or a
    shape this code has not seen all read as "the backup had nothing", logged at
    DEBUG so a silent backup is visible to anyone who looks.
    """

    def __init__(
        self,
        base_url: str = BASE,
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # A configured `http://host:3002/` must not become `//v2/search`.
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def aclose(self) -> None:
        """Close the client this instance created; an injected one is not ours."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, limit: int, mode: str = "web") -> list[SearchResult]:
        """Normalized hits for `query`, or `[]` for any failure at all.

        `mode` reaches the request as the `sources` array, because a news run
        asking for `web` is a different search than the one it thinks it made.
        The response is keyed by source type either way (NOTES-APIS.md §3), so
        results are read from the key matching `mode` and fall back to `web`.
        """
        source = "news" if mode == "news" else "web"
        try:
            response = await self._http().post(
                f"{self.base_url}{_SEARCH_PATH}",
                json={"query": query, "limit": limit, "sources": [source]},
            )
        except Exception as exc:  # noqa: BLE001 - a failed backup is not fatal
            logger.debug("backup search %s failed: %r", self.base_url, exc)
            return []
        if response.status_code >= 400:
            logger.debug("backup search %s answered %d", self.base_url, response.status_code)
            return []
        return _normalize(response, source)


def _normalize(response: httpx.Response, source: str) -> list[SearchResult]:
    """The deduplicated `SearchResult`s of one search response.

    Every shape that is not the documented one reads as "no results" rather than
    raising: this is the last call in a failed search, and a shape we have not
    seen is not worth losing the run over (T8.2).
    """
    try:
        payload = response.json()
    except ValueError:
        logger.debug("backup search returned a non-JSON body")
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        logger.debug("backup search data was %s, not a mapping", type(data).__name__)
        return []

    items: list[t.Mapping[str, t.Any]] = []
    items.extend(_dict_items(data.get(source)))
    # The instance answers a news request under `web` (probed, NOTES-APIS.md §3),
    # so both keys are read and the union is deduplicated by URL below.
    if source != "web":
        items.extend(_dict_items(data.get("web")))

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in items:
        result = SearchResult.from_search(item)
        # A non-mapping item would make `from_search` raise, and the backup is
        # already the failure path — drop it instead.
        if not result.url or result.url in seen:
            continue
        seen.add(result.url)
        results.append(result)
    return results


def _dict_items(value: t.Any) -> list[t.Mapping[str, t.Any]]:
    """`value` as a list of mappings, or an empty list if it is anything else."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _error_code(response: httpx.Response) -> str | None:
    """Firecrawl v2 carries a machine-readable code in some error bodies."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code") or payload.get("error")
    return code if isinstance(code, str) else None


def _scrape_data(response: httpx.Response) -> dict[str, t.Any]:
    """The `data` object of a v2 scrape response, or an empty dict."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _first_h1(markdown: str) -> str:
    """Heading from the first markdown h1, when metadata carried no title."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:_FIRST_H1_CHARS]
    return ""
