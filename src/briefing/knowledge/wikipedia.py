"""Wikipedia adapter for knowledge source lookups.

Provides encyclopedic answers via Wikipedia's OpenSearch and REST Summary APIs.
Never raises; returns empty list on any failure.
"""

import httpx

from briefing.config import KnowledgeConfig
from briefing.knowledge.base import KnowledgeSource, Passage


class WikipediaAdapter(KnowledgeSource):
    """Wikipedia knowledge adapter using OpenSearch + REST Summary APIs.

    OpenSearch is used for fuzzy matching of queries against Wikipedia titles.
    REST Summary provides the actual content for the best match.
    """

    def __init__(self, config: KnowledgeConfig) -> None:
        """Initialize adapter with config."""
        self._config = config

    @property
    def name(self) -> str:
        """Adapter name for badge."""
        return "WIKI"

    def badge(self, plain: bool = False) -> str:
        """Return badge string for this source."""
        return "wiki" if plain else "📖"

    async def lookup(self, query: str, *, limit: int = 3) -> list[Passage]:
        """Look up passages from Wikipedia.

        Uses OpenSearch to find matching titles, then fetches the summary
        for each non-disambiguation page.

        Args:
            query: The search query.
            limit: Maximum number of passages to return.

        Returns:
            List of Passage objects (may be empty on any failure).
        """
        try:
            # OpenSearch for fuzzy title matching
            search_results = await self._search_titles(query, limit)
            if not search_results:
                return []

            # Get summary for first non-disambiguation result
            for title, url in search_results:
                summary = await self._get_summary(title, url)
                if summary:
                    return [summary]

            return []
        except Exception:  # noqa: BLE001 - adapter contract: never raise, return []
            return []

    async def _search_titles(self, query: str, limit: int) -> list[tuple[str, str]]:
        """Search Wikipedia for matching titles via OpenSearch.

        Returns list of (title, url) tuples, excluding disambiguation pages.
        """
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "profile": "fuzzy",
            "format": "json",
        }

        headers = {"User-Agent": self._user_agent}
        timeout = httpx.Timeout(self._config.timeout_s)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()

            # OpenSearch returns [searchterm, titles, descriptions, urls]
            if not isinstance(data, list) or len(data) != 4:
                return []

            titles = data[1]
            urls = data[3]

            if not isinstance(titles, list):
                return []

            results = []
            for i, title in enumerate(titles):
                url_for_title = urls[i] if i < len(urls) else ""
                # Skip disambiguation pages and non-Wikipedia URLs
                if title and (
                    "disambiguation" not in title.lower()
                    and url_for_title
                    and url_for_title.startswith("https://en.wikipedia.org/")
                ):
                    results.append((title, url_for_title))

            return results
        except Exception:  # noqa: BLE001 - adapter contract: never raise, return []
            return []

    async def _get_summary(self, title: str, url: str) -> Passage | None:
        """Get summary for a Wikipedia page.

        Returns None if the page is disambiguation or fetch fails.
        """
        # URL-encode the title for the REST endpoint
        encoded_title = title.replace(" ", "_").replace("(", "%28").replace(")", "%29")
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"

        headers = {"User-Agent": self._user_agent}
        timeout = httpx.Timeout(self._config.timeout_s)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(summary_url, headers=headers)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = response.json()

            # Check if it's a disambiguation page
            if data.get("type") == "disambiguation":
                return None

            extract = data.get("extract", "")
            page_title = data.get("title", title)
            canonical_url = data.get("content_urls", {}).get("desktop", {}).get("page", url)

            # Verify URL is https and from Wikipedia
            if not canonical_url.startswith("https://en.wikipedia.org/"):
                canonical_url = url

            return Passage(
                text=extract,
                title=page_title,
                url=canonical_url,
                domain="wikipedia.org",
                license="CC BY-SA 4.0",
                author="Wikipedia contributors",
            )
        except Exception:  # noqa: BLE001 - adapter contract: never raise, return []
            return None

    @property
    def _user_agent(self) -> str:
        """Get User-Agent header value."""
        custom = self._config.user_agent
        if custom:
            return custom
        # Import version here to avoid circular imports at module load
        try:
            from briefing import __version__

            return f"briefing/{__version__}"
        except Exception:  # noqa: BLE001 - fallback to generic user agent
            return "briefing"