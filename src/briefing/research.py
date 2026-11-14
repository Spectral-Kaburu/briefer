"""Research mode: an answer-first explanation, from every page the search found.

The engine is `briefing.inquiry`; this module is the research profile's name for it
and nothing else. Ask (`briefing.ask`) is the same call with the other `Profile`, so
the two modes share one page reader, one injection guard and one place a failure
becomes a `Notice`.

    await research(query=..., search=..., fetch=..., extract=..., model=...)

The only differences from ask live in `RESEARCH`: every distinct page is read
(not one per host) and the model is asked for a report rather than three
sentences. A caller written against the old research pipeline gets `Answer` —
the same type ask returns — which is what makes one renderer serve both.
"""

from __future__ import annotations

import typing as t

from briefing.inquiry import (
    RESEARCH,
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

__all__ = [
    "RESEARCH",
    "Answer",
    "EvidenceSource",
    "FetchOutcome",
    "Fetched",
    "NoSearchResultsError",
    "Notice",
    "Profile",
    "build_prompt",
    "clean_answer",
    "fit_sources",
    "inquire",
    "prepare_body",
    "render_passages",
    "research",
    "select_hits",
]


async def research(
    **kwargs: t.Any,
) -> Answer:
    """Answer `query` in the research profile's shape: a cited explanation.

    Every argument is the engine's (`briefing.inquiry.inquire`); `profile` is fixed
    to `RESEARCH`. Research no longer summarizes pages one by one — the model
    reads every page whole and writes the answer, or there is no model and the
    passages are shown with the reason.
    """
    return await inquire(profile=RESEARCH, **kwargs)
