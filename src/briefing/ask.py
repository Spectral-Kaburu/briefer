"""Ask mode: a question, answered in three cited sentences.

The engine is `briefing.inquiry`; this module is the ask profile's name for it and
nothing else. Research (`briefing.research`) is the same call with its own
`Profile`, so a fix to how pages are read, fenced or reported reaches both.

    await ask(query=..., search=..., fetch=..., extract=..., model=...)

Anything the engine understands is accepted unchanged, so a caller written against
the old `ask.ask` keeps working: the names it used (`Answer`, `EvidenceSource`,
`NoSearchResultsError`, `build_prompt`, `render_passages`) are re-exported here.
"""

from __future__ import annotations

import typing as t

from briefing.inquiry import (
    ASK,
    Answer,
    EvidenceSource,
    Fetched,
    FetchOutcome,
    NoSearchResultsError,
    Notice,
    Profile,
    build_prompt,
    clean_answer,
    fit_sources,
    inquire,
    prepare_body,
    render_passages,
    select_hits,
)

# The engine, under ask's own name: one answer per question, one page per host.
__all__ = [
    "ASK",
    "Answer",
    "EvidenceSource",
    "FetchOutcome",
    "Fetched",
    "NoSearchResultsError",
    "Notice",
    "Profile",
    "ask",
    "build_prompt",
    "clean_answer",
    "fit_sources",
    "inquire",
    "prepare_body",
    "render_passages",
    "select_hits",
]


async def ask(
    **kwargs: t.Any,
) -> Answer:
    """Answer `query` in the ask profile's shape: brief, cited, one page per host.

    Every argument is the engine's (`briefing.inquiry.inquire`); `profile` is fixed
    to `ASK`. There is no ask-side logic left to differ from research's.
    """
    return await inquire(profile=ASK, **kwargs)
