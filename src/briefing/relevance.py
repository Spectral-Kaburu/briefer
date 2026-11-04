"""Passage relevance scoring for query-aware extraction (T9.3).

BM25-style scoring with fuzzy matching for typo-tolerant query matching.
"""

from __future__ import annotations

import re

# Stop words that are removed during scoring
_STOP_WORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "to",
    "and",
    "or",
    "with",
    "by",
    "at",
    "from",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "as",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "but",
    "not",
    "no",
    "just",
    "only",
    "also",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "what",
    "which",
    "who",
    "when",
    "where",
    "why",
    "how",
    "am",
    "being",
}

# Query words that benefit from digit/date matching
_DATE_RELEVANT_WORDS = {
    "version",
    "release",
    "latest",
    "current",
    "price",
    "date",
    "when",
    "how many",
    "how much",
}

# Fuzzy matching threshold for 5+ letter tokens
_FUZZY_THRESHOLD = 0.8  # 80% similarity


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase word set, removing stop words."""
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    return words - _STOP_WORDS


def _plural_fold(word: str) -> str:
    """Simple plural folding: remove trailing 's' or 'es'."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"  # categories -> category
    if word.endswith("es") and len(word) > 3:
        return word[:-2]  # boxes -> box
    if word.endswith("s") and len(word) > 2:
        return word[:-1]  # cats -> cat
    return word


def _fuzzy_match(query_token: str, vocab: set[str]) -> bool:
    """Check if query token fuzzy-matches any word in vocabulary.

    Only applies to tokens with 5+ letters.
    """
    if len(query_token) < 5:
        return query_token in vocab

    # Check for exact match first
    if query_token in vocab:
        return True

    # Simple fuzzy: check if any vocab word is close enough
    query_lower = query_token.lower()
    query_base = _plural_fold(query_lower)

    for word in vocab:
        word_base = _plural_fold(word)
        # Check if one contains the other (handles typos like "verstion" -> "version")
        if query_base in word_base or word_base in query_base:
            return True
        # Check for character-level similarity
        if _sequence_similarity(query_base, word_base) >= _FUZZY_THRESHOLD:
            return True
    return False


def _sequence_similarity(a: str, b: str) -> float:
    """Simple sequence similarity using character overlap ratio."""
    if not a or not b:
        return 0.0
    # Simple approach: ratio of shared characters to longer string
    a_chars = set(a)
    b_chars = set(b)
    intersection = len(a_chars & b_chars)
    return intersection / max(len(a_chars | b_chars), 1)


def score_chunk(chunk: str, query: str, url: str = "") -> float:
    """Score a text chunk against a query.

    Returns a score where higher = more relevant.
    BM25-style with fuzzy matching for typo tolerance.
    """
    if not chunk or not query:
        return 0.0

    # Tokenize both
    query_tokens = _tokenize(query)
    chunk_tokens = _tokenize(chunk)

    if not query_tokens:
        return 0.0

    # Score based on word overlap
    total_score = 0.0
    matched_tokens = 0

    for token in query_tokens:
        # Try exact match first
        if token in chunk_tokens:
            total_score += 1.0
            matched_tokens += 1
            continue

        # Try fuzzy match for 5+ letter tokens
        if len(token) >= 5 and _fuzzy_match(token, chunk_tokens):
            total_score += 0.8  # Slight penalty for fuzzy
            matched_tokens += 1

    # Boost for digits/dates when query contains relevant words AND there are matched tokens
    query_has_date_relevance = any(word in query.lower() for word in _DATE_RELEVANT_WORDS)
    if query_has_date_relevance and matched_tokens > 0:
        # Count digits in chunk as a relevance signal
        digit_count = sum(c.isdigit() for c in chunk)
        total_score += digit_count * 0.1

    # Normalize by chunk length (shorter = more focused on query)
    chunk_len = len(chunk_tokens)
    if chunk_len == 0:
        return 0.0
    return total_score / (chunk_len**0.5)  # BM25-style length normalization


def split_into_chunks(text: str, sentences: int = 3) -> list[str]:
    """Split cleaned text into chunks of 2-3 sentences each.

    Returns list of chunk strings, each containing 2-3 sentences.
    """
    if not text:
        return []

    # Split by sentence boundary (period, exclamation, question mark followed by space/capital)
    sentence_pattern = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
    sentences_list = sentence_pattern.split(text)

    if not sentences_list:
        return [text] if text.strip() else []

    # Clean up sentences
    sentences_list = [s.strip() for s in sentences_list if s.strip()]

    if not sentences_list:
        return []

    # Group into 2-3 sentence chunks
    chunks: list[str] = []
    i = 0
    while i < len(sentences_list):
        # Take 3 sentences for longer chunks, 2 for shorter runs
        end = min(i + 3, len(sentences_list))
        chunk = " ".join(sentences_list[i:end])
        chunks.append(chunk)
        i = end

    return chunks


def rank_chunks(chunks: list[str], query: str, url: str = "") -> list[tuple[str, float]]:
    """Rank chunks by relevance to query.

    Returns list of (chunk, score) tuples sorted by score descending.
    """
    scored = [(chunk, score_chunk(chunk, query, url)) for chunk in chunks]
    scored.sort(key=lambda x: -x[1])
    return scored
