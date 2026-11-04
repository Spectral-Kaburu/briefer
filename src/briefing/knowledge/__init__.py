"""Knowledge source adapters for ask mode.

Provides the base `Passage` type and `KnowledgeSource` protocol that all
adapters must implement. Adapters: Wikipedia, Stack Exchange, DuckDuckGo
Instant Answer.
"""

from briefing.knowledge.base import KnowledgeSource, Passage

__all__ = ["KnowledgeSource", "Passage"]
