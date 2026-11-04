"""Tests for knowledge/wikipedia.py - Wikipedia adapter.

TDD: tests first, then verify with implementation.
"""

import asyncio

import pytest
import respx
from httpx import Response

from briefing.config import KnowledgeConfig
from briefing.knowledge.base import Passage
from briefing.knowledge.wikipedia import WikipediaAdapter


def _async_adapter_lookup(adapter: WikipediaAdapter, query: str, limit: int = 3) -> list[Passage]:
    """Helper to run async lookup."""
    return asyncio.run(adapter.lookup(query, limit=limit))


class TestWikipediaAdapter:
    """Tests for WikipediaAdapter."""

    @pytest.fixture
    def adapter(self) -> WikipediaAdapter:
        """Create adapter with default config."""
        config = KnowledgeConfig()
        return WikipediaAdapter(config)

    def test_opensearch_search_returns_titles(self, adapter: WikipediaAdapter) -> None:
        """OpenSearch returns 4-element arrays with titles at index 1."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "python programming language",
                        ["Python (programming language)"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Python_(programming_language)"],
                    ],
                )
            )
            respx.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/Python_(programming_language)"
            ).mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Python (programming language)",
                        "extract": "A programming language.",
                        "content_urls": {
                            "desktop": {
                                "page": "https://en.wikipedia.org/wiki/Python_(programming_language)"
                            }
                        },
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "python programming language")
            assert len(result) == 1
            assert result[0].title == "Python (programming language)"

    def test_fuzzy_parameter_present(self, adapter: WikipediaAdapter) -> None:
        """OpenSearch should be called with profile=fuzzy for typo tolerance."""
        with respx.mock:
            route = respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Test"],
                    ],
                )
            )
            # Set up summary response
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test").mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Test",
                        "extract": "Test content",
                        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Test"}},
                    },
                )
            )

            _async_adapter_lookup(adapter, "test")
            # Verify fuzzy was in request
            request_url = str(route.calls[0].request.url)
            assert "profile=fuzzy" in request_url

    def test_disambiguation_page_skipped(self, adapter: WikipediaAdapter) -> None:
        """First hit is disambiguation page, should try next."""
        with respx.mock:
            # First hit is disambiguation
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "python",
                        ["Python (disambiguation)"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Python"],
                    ],
                )
            )
            # Second hit (after skipping disambig) - need to mock again since OpenSearch is called again
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "python",
                        ["Python (programming language)"],
                        ["A programming language"],
                        ["https://en.wikipedia.org/wiki/Python"],
                    ],
                )
            )
            # Summary for second hit
            respx.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/Python_(programming_language)"
            ).mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Python (programming language)",
                        "extract": "A programming language",
                        "content_urls": {
                            "desktop": {
                                "page": "https://en.wikipedia.org/wiki/Python_(programming_language)"
                            }
                        },
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "python")
            # Should have used second hit, not disambig
            assert len(result) == 1
            assert result[0].title == "Python (programming language)"

    def test_all_hits_disambiguation_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """If all hits are disambiguation pages, return empty."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "python",
                        ["Python (disambiguation)"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Python"],
                    ],
                )
            )

            result = _async_adapter_lookup(adapter, "python")
            assert result == []

    def test_empty_opensearch_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """Empty OpenSearch result returns empty."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "nonexistentthing12345",
                        [],
                        [],
                        [],
                    ],
                )
            )

            result = _async_adapter_lookup(adapter, "nonexistentthing12345")
            assert result == []

    def test_summary_404_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """404 on summary returns empty list."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Test"],
                    ],
                )
            )
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test").mock(
                return_value=Response(404, json={"error": "not found"})
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_500_on_search_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """500 error on search returns empty."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(500, json={"error": "server error"})
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_timeout_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """Timeout returns empty."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(side_effect=Exception("timeout"))

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_malformed_json_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """Malformed JSON returns empty."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(200, content=b"not json")
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_wrong_shape_returns_empty(self, adapter: WikipediaAdapter) -> None:
        """Response that isn't 4-element array returns empty."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(200, json={"not": "an array"})
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_url_not_https_dropped(self, adapter: WikipediaAdapter) -> None:
        """URL that's not https Wikipedia is dropped."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["http://en.wikipedia.org/wiki/Test"],  # http, not https
                    ],
                )
            )
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test").mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Test",
                        "extract": "Test content",
                        "content_urls": {
                            "desktop": {"page": "http://en.wikipedia.org/wiki/Test"}  # http URL
                        },
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "test")
            # Should drop the https check and return empty
            assert result == []

    def test_limit_respected(self, adapter: WikipediaAdapter) -> None:
        """Limit parameter limits number of results."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Result1", "Result2", "Result3"],
                        ["desc1", "desc2", "desc3"],
                        [
                            "https://en.wikipedia.org/wiki/Result1",
                            "https://en.wikipedia.org/wiki/Result2",
                            "https://en.wikipedia.org/wiki/Result3",
                        ],
                    ],
                )
            )
            # Only first result gets summary
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Result1").mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Result1",
                        "extract": "Content 1",
                        "content_urls": {
                            "desktop": {"page": "https://en.wikipedia.org/wiki/Result1"}
                        },
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "test", limit=1)
            assert len(result) == 1

    def test_license_is_cc_by_sa(self, adapter: WikipediaAdapter) -> None:
        """Passage license should be CC BY-SA 4.0."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Test"],
                    ],
                )
            )
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test").mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Test",
                        "extract": "Test content",
                        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Test"}},
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result[0].license == "CC BY-SA 4.0"
            assert result[0].domain == "wikipedia.org"

    def test_non_wikipedia_url_dropped(self, adapter: WikipediaAdapter) -> None:
        """URL from non-Wikipedia host is dropped."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://evil.com/wiki/Test"],  # Not wikipedia.org
                    ],
                )
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_timeout_config_honored(self) -> None:
        """Adapter uses configured timeout."""
        config = KnowledgeConfig(timeout_s=1.0)
        adapter = WikipediaAdapter(config)

        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(side_effect=Exception("timeout"))

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_user_agent_is_generic(self, adapter: WikipediaAdapter) -> None:
        """User-Agent header is generic briefing/<version>."""
        with respx.mock:
            route = respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Test"],
                    ],
                )
            )

            # Need to also mock the summary endpoint
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test").mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Test",
                        "extract": "Test content",
                        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Test"}},
                    },
                )
            )

            _async_adapter_lookup(adapter, "test")
            request = route.calls[0].request
            assert "briefing/" in request.headers.get("User-Agent", "")
            # Should not contain personal info
            assert "@" not in request.headers.get("User-Agent", "")
            assert "lee" not in request.headers.get("User-Agent", "").lower()

    def test_custom_user_agent(self) -> None:
        """Custom user agent is used when configured."""
        config = KnowledgeConfig(user_agent="my-custom-agent/1.0")
        adapter = WikipediaAdapter(config)

        with respx.mock:
            route = respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://en.wikipedia.org/wiki/Test"],
                    ],
                )
            )
            respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/Test").mock(
                return_value=Response(
                    200,
                    json={
                        "type": "standard",
                        "title": "Test",
                        "extract": "Test content",
                        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Test"}},
                    },
                )
            )

            _async_adapter_lookup(adapter, "test")
            request = route.calls[0].request
            assert request.headers.get("User-Agent") == "my-custom-agent/1.0"

    def test_non_standard_url_dropped(self, adapter: WikipediaAdapter) -> None:
        """Non-https or non-Wikipedia URL is dropped."""
        with respx.mock:
            respx.get("https://en.wikipedia.org/w/api.php").mock(
                return_value=Response(
                    200,
                    json=[
                        "test",
                        ["Test"],
                        [""],
                        ["https://example.com/page"],  # Not wikipedia.org
                    ],
                )
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []
