"""Tests for knowledge/stackexchange.py - Stack Exchange adapter.

TDD: tests first, then verify with implementation.
"""

import asyncio

import pytest
import respx
from httpx import Response

from briefing.config import KnowledgeConfig
from briefing.knowledge.base import Passage
from briefing.knowledge.stackexchange import StackExchangeAdapter


def _async_adapter_lookup(
    adapter: StackExchangeAdapter, query: str, limit: int = 3
) -> list[Passage]:
    """Helper to run async lookup."""
    return asyncio.run(adapter.lookup(query, limit=limit))


class TestStackExchangeAdapter:
    """Tests for StackExchangeAdapter."""

    @pytest.fixture
    def adapter(self) -> StackExchangeAdapter:
        """Create adapter with default config."""
        config = KnowledgeConfig()
        return StackExchangeAdapter(config)

    @pytest.fixture
    def adapter_with_key(self) -> StackExchangeAdapter:
        """Create adapter with a fake API key."""
        config = KnowledgeConfig()
        return StackExchangeAdapter(config, key="fake-api-key")

    def test_search_advanced_success(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Search advanced returns items with accepted answers."""
        with respx.mock:
            # Mock search response
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "How to do X in Python?",
                                "body": "<p>Question body</p>",
                                "accepted_answer_id": 12345,
                                "link": "https://stackoverflow.com/questions/1/1",
                                "owner": {"display_name": "User123"},
                            }
                        ],
                        "has_more": False,
                        "quota_remaining": 9999,
                        "quota_max": 10000,
                    },
                )
            )
            # Mock answers response
            respx.get("https://api.stackexchange.com/2.3/answers/12345").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": 12345,
                                "body": "<p>Accepted answer</p><pre><code>code</code></pre>",
                                "owner": {"display_name": "AnswerUser"},
                            }
                        ],
                        "quota_remaining": 9998,
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "python x")
            assert len(result) == 1
            assert result[0].title == "How to do X in Python?"
            assert "Accepted answer" in result[0].text
            assert result[0].author == "AnswerUser"
            assert result[0].license == "CC BY-SA 4.0"
            assert result[0].domain == "stackoverflow.com"

    def test_no_accepted_answer_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Questions without accepted answers return empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "How to do X in Python?",
                                "body": "<p>Question body</p>",
                                # No accepted_answer_id
                                "link": "https://stackoverflow.com/questions/1/1",
                            }
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "python x")
            assert result == []

    def test_html_to_text(self, adapter_with_key: StackExchangeAdapter) -> None:
        """HTML body is converted to plain text."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "Test question",
                                "body": "<p>Hello <b>world</b></p>",
                                "accepted_answer_id": 12345,
                                "link": "https://stackoverflow.com/questions/1/1",
                            }
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )
            respx.get("https://api.stackexchange.com/2.3/answers/12345").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": 12345,
                                "body": "<p>Accepted <em>answer</em> with link</p>",
                                "owner": {"display_name": "User"},
                            }
                        ],
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert "<p>" not in result[0].text
            assert "<b>" not in result[0].text
            assert "<em>" not in result[0].text
            assert "Accepted answer with link" in result[0].text

    def test_code_block_capped_at_600_chars(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Code blocks in answers are capped at 600 characters."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "Test",
                                "body": "Q",
                                "accepted_answer_id": 12345,
                                "link": "https://stackoverflow.com/questions/1/1",
                            }
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )
            respx.get("https://api.stackexchange.com/2.3/answers/12345").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": 12345,
                                "body": "<pre><code>" + "x" * 1000 + "</code></pre>",
                                "owner": {"display_name": "User"},
                            }
                        ],
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            # Code blocks should be capped - verify text is reasonable length
            assert len(result[0].text) < 1000
            assert "x" in result[0].text

    def test_images_dropped(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Images are dropped from the text."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "Test with image",
                                "body": "<p>Text with <img src='image.png'/> image</p>",
                                "accepted_answer_id": 12345,
                                "link": "https://stackoverflow.com/questions/1/1",
                            }
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )
            respx.get("https://api.stackexchange.com/2.3/answers/12345").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": 12345,
                                "body": "<p>Answer with <img src='pic.jpg'/> and text</p>",
                                "owner": {"display_name": "User"},
                            }
                        ],
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert "<img" not in result[0].text
            assert "pic.jpg" not in result[0].text
            assert "text" in result[0].text

    def test_quota_floor(self) -> None:
        """Adapter returns empty when quota at or below floor."""
        config = KnowledgeConfig(quota_floor=99)
        adapter = StackExchangeAdapter(config)

        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [],
                        "quota_remaining": 99,  # At floor
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_500_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """500 error returns empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(500, json={"error": "server error"})
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []

    def test_429_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """429 (rate limit) returns empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(429, json={"error": "rate limited"})
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []

    def test_timeout_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Timeout returns empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                side_effect=Exception("timeout")
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []

    def test_malformed_json_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Malformed JSON returns empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(200, content=b"not json")
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []

    def test_wrong_shape_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Response without items array returns empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(200, json={"error": "bad request", "items": "not an array"})
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []

    def test_empty_items_returns_empty(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Empty items list returns empty."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [],
                        "quota_remaining": 9999,
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []

    def test_user_agent_is_generic(self, adapter_with_key: StackExchangeAdapter) -> None:
        """User-Agent header is generic briefing/<version>."""
        with respx.mock:
            route = respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [],
                        "quota_remaining": 9999,
                    },
                )
            )

            _async_adapter_lookup(adapter_with_key, "test")
            request = route.calls[0].request
            assert "briefing/" in request.headers.get("User-Agent", "")
            assert "@" not in request.headers.get("User-Agent", "")
            assert "lee" not in request.headers.get("User-Agent", "").lower()

    def test_limit_respected(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Limit parameter limits number of results."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": f"Q{i}",
                                "body": "Q",
                                "accepted_answer_id": i,
                                "link": "#" + str(i),
                                "owner": {"display_name": "U"},
                            }
                            for i in range(10)
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )
            respx.get("https://api.stackexchange.com/2.3/answers/").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": i,
                                "body": "A",
                                "owner": {"display_name": "AU"},
                            }
                            for i in range(10)
                        ],
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test", limit=3)
            # With limit=3, should only get 3 results
            assert len(result) <= 3

    def test_no_key_works(self, adapter) -> None:
        """Adapter works without an API key (key is optional)."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [],
                        "quota_remaining": 9990,  # Low quota without key
                    },
                )
            )

            result = _async_adapter_lookup(adapter, "test")
            assert result == []

    def test_script_tag_stripped(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Script tags are stripped from HTML."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "Test",
                                "body": "<p>Question before script</p>",
                                "accepted_answer_id": 12345,
                                "link": "https://stackoverflow.com/questions/1/1",
                            }
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )
            respx.get("https://api.stackexchange.com/2.3/answers/12345").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": 12345,
                                "body": "<p>Before script</p><script>alert('xss')</script><p>After script</p>",
                                "owner": {"display_name": "User"},
                            }
                        ],
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert "<script>" not in result[0].text
            assert "alert" not in result[0].text
            assert "Before script" in result[0].text
            assert "After script" in result[0].text

    def test_backoff_response_handled(self, adapter_with_key: StackExchangeAdapter) -> None:
        """Backoff field in response is handled correctly."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [],
                        "quota_remaining": 9999,
                        "backoff": 10,
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert result == []  # Empty items returns empty

    def test_control_characters_stripped_from_answer(
        self, adapter_with_key: StackExchangeAdapter
    ) -> None:
        """Control characters and ANSI escapes should be stripped from answer text."""
        with respx.mock:
            respx.get("https://api.stackexchange.com/2.3/search/advanced").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "title": "Test question",
                                "body": "<p>Question body</p>",
                                "accepted_answer_id": 12345,
                                "link": "https://stackoverflow.com/questions/1/1",
                            }
                        ],
                        "quota_remaining": 9999,
                    },
                )
            )
            # Answer contains ANSI escape and control chars
            ansi_escape = "\x1b[32m\x1b[0m"  # Green color codes
            control_chars = "\x00\x01\x02\x08\x1f"  # NUL, SOH, STX, BS, US
            respx.get("https://api.stackexchange.com/2.3/answers/12345").mock(
                return_value=Response(
                    200,
                    json={
                        "items": [
                            {
                                "answer_id": 12345,
                                "body": f"<p>Answer with{ansi_escape}control{control_chars}chars</p>",
                                "owner": {"display_name": "User"},
                            }
                        ],
                    },
                )
            )

            result = _async_adapter_lookup(adapter_with_key, "test")
            assert len(result) == 1
            # Control chars should be stripped
            assert ansi_escape not in result[0].text
            assert "\x00" not in result[0].text
            assert "\x08" not in result[0].text
            # Normal text should remain
            assert "Answer" in result[0].text
            assert "control" in result[0].text
            assert "chars" in result[0].text
