"""Source routing for knowledge adapters in ask mode.

A pure function that chooses which knowledge adapters to use based on
the question text. Returns a list of adapter names (source identifiers).
"""

import re

# Encyclopedic cues: questions about definitions, history, facts
ENCYCLOPEDIC_PATTERNS = (
    r"\bwhat is",
    r"\bwhat are",
    r"\bwho is",
    r"\bwho was",
    r"\bdefine\b",
    r"\bhistory of\b",
    r"\bwhen was\b",
    r"\bmeaning of\b",
    r"\bcapital of\b",
)

# Developer cues: error traces, how-to questions, code constructs
DEVELOPER_PATTERNS = (
    r"\berror\b",
    r"\bexception\b",
    r"\btraceback\b",  # lowercase since input is lowercased
    r"\bhow do i\b",
    r"\bhow to\b",  # "how to" signals programming help request
    r"::\w",  # C++ / Rust namespace separator: Class::method
    r"\bcode\b",
    r"\bfunction\b",
    r"\bmethod\b",
    r"\blibrary\b",
    r"\bmodule\b",
    r"\bpackage\b",
    r"\bimport\b",
    r"\binstall\b",
    r"\bsyntax\b",
    r"\bdebug\b",
    r"\bin python\b",  # "in Python" - context for programming language
    r"\bin javascript\b",
    r"\bin java\b",
    r"\bin c\+\+\b",
    r"\bin go\b",
    r"\bin rust\b",
)

# News markers (from search.py) - news mode questions should not use knowledge sources
NEWS_MARKERS = ("latest", "breaking", "today", "news")


def choose_sources(question: str) -> list[str]:
    """Choose which knowledge sources to consult for a question.

    Args:
        question: The user's question string.

    Returns:
        List of source names (e.g., ["WIKI"], ["SO"], ["WIKI", "SO"], or []).
        Knowledge passages are numbered before web sources in ask mode.

    Rules:
        - News-mode questions select neither (knowledge sources lag the news)
        - Encyclopedic cues select Wikipedia
        - Developer cues select Stack Overflow
        - Questions matching both select both (order: WIKI, SO)
        - No match selects nothing
    """
    low = question.lower().strip()

    # News-mode questions: knowledge sources lag the news
    if any(f" {marker} " in f" {low} " for marker in NEWS_MARKERS):
        return []

    sources: list[str] = []

    # Check for encyclopedic patterns
    if any(re.search(pat, low) for pat in ENCYCLOPEDIC_PATTERNS):
        sources.append("WIKI")

    # Check for developer patterns
    if any(re.search(pat, low) for pat in DEVELOPER_PATTERNS):
        sources.append("SO")

    return sources
