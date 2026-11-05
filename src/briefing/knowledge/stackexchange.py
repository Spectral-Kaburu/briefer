"""Stack Exchange adapter for knowledge source lookups.

Provides answers from Stack Overflow (accepted answers) via the Stack Exchange API.
Never raises; returns empty list on any failure.
"""

import html
import logging
import re

import httpx

from briefing.config import KnowledgeConfig
from briefing.knowledge.base import KnowledgeSource, Passage, strip_control_chars

logger = logging.getLogger(__name__)


class StackExchangeAdapter(KnowledgeSource):
    """Stack Exchange knowledge adapter using search + answers APIs.

    Uses the search advanced endpoint to find questions with accepted answers,
    then fetches those answers.
    """

    def __init__(self, config: KnowledgeConfig, key: str | None = None) -> None:
        """Initialize adapter with config.

        Args:
            config: Knowledge configuration.
            key: Optional API key (read from env at call time if not provided).
        """
        self._config = config
        self._key = key
        self._backoff_until: float | None = None

    @property
    def name(self) -> str:
        """Adapter name for badge."""
        return "SO"

    def badge(self, plain: bool = False) -> str:
        """Return badge string for this source."""
        return "so" if plain else "💬"

    async def lookup(self, query: str, *, limit: int = 3) -> list[Passage]:
        """Look up passages from Stack Exchange.

        Uses search advanced to find questions with accepted answers,
        then fetches those answers.

        Args:
            query: The search query.
            limit: Maximum number of passages to return.

        Returns:
            List of Passage objects (may be empty on any failure).
        """
        # Check backoff from previous response
        if self._backoff_until is not None:
            import time

            if time.time() < self._backoff_until:
                return []

        try:
            # Get API key if not provided
            key = self._key or self._resolve_key()

            # Search for questions
            items = await self._search_questions(query, limit, key)
            if not items:
                return []

            # Get accepted answers for the questions
            question_ids = []
            for item in items:
                if item.get("accepted_answer_id"):
                    question_ids.append(item["accepted_answer_id"])

            if not question_ids:
                return []

            # Batch fetch answers
            answers = await self._fetch_answers(question_ids, key)

            # Build passages from accepted answers
            passages = []
            for item, answer in zip(items, answers):
                if answer:
                    passage = self._make_passage(item, answer)
                    if passage:
                        passages.append(passage)

            return passages[:limit]
        except Exception:  # noqa: BLE001 - adapter contract: never raise
            return []

    def _resolve_key(self) -> str | None:
        """Resolve API key from environment if configured."""
        import os

        env_var = self._config.stackexchange_key_env
        key = os.environ.get(env_var, "")
        return key or None

    async def _search_questions(self, query: str, limit: int, key: str | None) -> list[dict]:
        """Search for questions with accepted answers."""
        url = "https://api.stackexchange.com/2.3/search/advanced"
        params = {
            "q": query,
            "site": "stackoverflow",
            "accepted": "True",
            "sort": "relevance",
            "order": "desc",
            "pagesize": limit,
            "filter": "withbody",
        }
        if key:
            params["key"] = key

        headers = {"User-Agent": self._user_agent}
        timeout = httpx.Timeout(self._config.timeout_s)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()

            # Check quota
            quota_remaining = data.get("quota_remaining", 10000)
            if quota_remaining <= self._config.quota_floor:
                logger.debug(
                    "Stack Exchange quota at %d, at or below floor %d",
                    quota_remaining,
                    self._config.quota_floor,
                )
                return []

            # Check backoff
            if "backoff" in data:
                import time

                backoff = data["backoff"]
                if self._config.backoff is not None and backoff < self._config.backoff:
                    backoff = self._config.backoff
                self._backoff_until = time.time() + backoff
                logger.debug("Stack Exchange backoff requested: %ds", backoff)

            items = data.get("items", [])
            if not isinstance(items, list):
                return []

            return items
        except Exception:  # noqa: BLE001 - adapter contract: never raise
            return []

    async def _fetch_answers(self, answer_ids: list[int], key: str | None) -> list[dict | None]:
        """Fetch answers by ID in a single batch request."""
        if not answer_ids:
            return []

        # Stack Exchange API expects IDs in the path: /answers/123;456
        ids_path = ";".join(str(i) for i in answer_ids)
        url = f"https://api.stackexchange.com/2.3/answers/{ids_path}"
        params = {
            "site": "stackoverflow",
            "filter": "withbody",
        }
        if key:
            params["key"] = key

        headers = {"User-Agent": self._user_agent}
        timeout = httpx.Timeout(self._config.timeout_s)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()

            items = data.get("items", [])
            if not isinstance(items, list):
                return [None] * len(answer_ids)

            # Build response mapping by answer id
            answers_by_id = {item["answer_id"]: item for item in items if isinstance(item, dict)}

            return [answers_by_id.get(aid) for aid in answer_ids]
        except Exception:  # noqa: BLE001 - adapter contract: never raise
            return [None] * len(answer_ids)

    def _make_passage(self, question: dict, answer: dict) -> Passage | None:
        """Build a Passage from question and answer."""
        title = question.get("title", "")
        answer_body = answer.get("body", "")

        # Combine question title + accepted answer text
        # Take the answer text as primary content
        answer_text = self._html_to_text(answer_body)

        if not answer_text.strip():
            return None

        author = answer.get("owner", {}).get("display_name", "")

        return Passage(
            text=strip_control_chars(answer_text),
            title=title,
            url=question.get("link", ""),
            domain="stackoverflow.com",
            license="CC BY-SA 4.0",
            author=author,
        )

    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML body to plain text.

        Keeps code blocks, drops images, handles entities.
        """
        if not html_content:
            return ""

        # Parse HTML
        try:
            # Use a simple approach: regex-based extraction
            # Strip script tags entirely
            text = re.sub(
                r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE
            )

            # Convert <pre><code>...</code></pre> to escaped text with capping
            def process_code_block(match):
                code = match.group(1)
                # Cap at 600 characters
                if len(code) > 600:
                    code = code[:600] + "..."
                # Unescape HTML entities
                code = html.unescape(code)
                return f"\n{code}\n"

            text = re.sub(r"<code>(.*?)</code>", process_code_block, text, flags=re.DOTALL)

            # Strip all other tags
            text = re.sub(r"<[^>]+>", "", text)

            # Handle self-closing tags and attributes
            # Handle link syntax: [text](url) -> text
            text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

            # Unescape HTML entities
            text = html.unescape(text)

            # Clean up whitespace
            text = re.sub(r"[ \t]+\n", "\n", text)
            text = re.sub(r"\n[ \t]+", "\n", text)
            text = re.sub(r"\n{3,}", "\n\n", text)

            # Drop images: remove remaining img tags
            text = re.sub(r"<img[^>]+>", "", text, flags=re.IGNORECASE)

            # Remove any remaining src/href content like "(url)"
            text = re.sub(r"\(https?://[^)]+\)", "", text)

            return text.strip()
        except Exception:  # noqa: BLE001 - fallback to original HTML
            return html_content

    @property
    def _user_agent(self) -> str:
        """Get User-Agent header value."""
        custom = self._config.user_agent
        if custom:
            return custom
        try:
            from briefing import __version__

            return f"briefing/{__version__}"
        except Exception:  # noqa: BLE001 - fallback to generic user agent
            return "briefing"
