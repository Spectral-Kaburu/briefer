"""Client for the self-hosted Firecrawl scrape and search endpoints.

One `scrape` call returns a `LocalScrapeResult` either way: a success carries the
markdown and heading, a failure carries the SPEC section 4 classification.
`scrape` never raises on a fetch failure, so one bad URL can never kill a run.
`search` does raise, and its one caller guards it — see its docstring.
"""

from __future__ import annotations

import typing as t

import httpx

from .errors import ErrorClass, Origin, classify

_SCRAPE_PATH = "/v2/scrape"
_SEARCH_PATH = "/v2/search"
_FIRST_H1_CHARS = 200


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

    async def search(self, query: str, limit: int) -> list[dict[str, t.Any]]:
        """Search the local instance; one raw web item dict per hit.

        The ddgs backup path (SPEC section 5), used only when ddgs has already
        failed or come back empty. Items are returned unnormalized because
        `SearchResult.from_search` owns the field mapping; this method owns only
        the endpoint and the unwrapping.

        Unlike `scrape` and `health`, a unreachable instance raises: the caller
        (`Searcher._backup_search`) already catches everything, and the backup
        is the last resort in a search that has already failed, so there is no
        worse state left to degrade to.
        """
        response = await self._http().post(
            f"{self.base_url}{_SEARCH_PATH}",
            json={"query": query, "limit": limit, "sources": ["web"]},
        )
        return _search_items(response)


def _search_items(response: httpx.Response) -> list[dict[str, t.Any]]:
    """The `data["web"]` items of a v2 search response, or an empty list.

    A search response keys its hits by source type (`{"web": [...]}`) rather
    than returning a flat list — verified by live probe in NOTES-APIS.md §3.
    `sources` is always `["web"]` here, so no other key is expected, but a
    missing or wrongly-typed `web` reads as "no results" rather than raising:
    a shape we have not seen yet is not worth killing the run over.
    """
    try:
        payload = response.json()
    except ValueError:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("web") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    # A non-mapping item would make `from_search` raise, and the backup is
    # already the failure path — drop it instead.
    return [item for item in items if isinstance(item, dict)]


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
