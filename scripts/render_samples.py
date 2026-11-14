"""Regenerate `docs/sample-output.txt` from the real UI renderers (T3.5).

Not a test. This renders the five T3.5 scenarios through the same `briefing.ui`
functions `briefing research` calls, so the saved samples are the exact bytes the
CLI writes — not an approximation of them. Run from the repo root:

    .venv/bin/python scripts/render_samples.py

Each scenario is a hand-built `Report` or `Answer`, so nothing here touches the
network, a search, or a fetch.
"""

from __future__ import annotations

import typing as t
from pathlib import Path

from rich.console import Console

from briefing.fetch.router import ErrorClass, Method
from briefing.inquiry import Answer, EvidenceSource, Notice
from briefing.ui import UI

OUT = Path(__file__).resolve().parent.parent / "docs" / "sample-output.txt"

HEADER = """\
`briefing` sample output
========================

Five runs rendered through the real `briefing.ui` renderers (T3.5), at the two
widths that matter. No network, no engine: each scenario is a hand-built `Answer`
handed to the same functions `briefing research` calls, so these are the exact
bytes the UI writes. Regenerate with `scripts/render_samples.py` rather than
editing by hand.

Each scenario shows its live progress block (SPEC section 3's one spinner line
per URL) and then a representative couple of cards; `found` in the footer counts
the whole run, so the cards below it are a subset, not the complete set.

What each scenario is for:

  1. All local, all good - the happy path, so the cards' normal state is on
     record.
  2. A barrier page recovered by the cloud API - the API badge and the whitelist
     caption in the footer.
  3. Everything degraded to snippets - local Firecrawl down, so every card is a
     snippet and the snippet is the whole result.
  4. A hard failure beside real results - a failed page that must not stop the
     other cards rendering.
  5. Long titles, long summaries, a long domain - the wrapping case.

Ask mode scenarios (T9.7, T10.7):

  A1. Answered with sources - happy path with LLM
  A2. No model available - passages only with warning
  A3. Sources disagree - answer states disagreement
  A4. Knowledge sources (Wiki + SO) - answer with Wikipedia and Stack Exchange
  A5. Nothing found - clear error message
"""


def _source(number, title, url, snippet, summary, method, **kw):
    """One numbered source card: the same fields the engine hands the UI."""
    from urllib.parse import urlparse

    return EvidenceSource(
        number=number,
        source=title,
        domain=urlparse(url).hostname or "",
        title=title,
        heading=kw.pop("heading", title),
        url=url,
        snippet=snippet,
        text=summary,
        method=method,
        ok=kw.pop("ok", method is not Method.SNIPPET),
        error_class=kw.pop("error_class", ErrorClass.OK),
        warning=kw.pop("warning", ""),
        age=kw.pop("age", ""),
    )


def _answer(*, found, elapsed_s, sources, **kw):
    """A research answer for the given cards, so the footer has its counts."""
    warnings = [s.warning for s in sources if s.warning]
    notices = [Notice("fetch", w) for w in warnings]
    return Answer(
        question="kenya economy",
        text=kw.pop("text", ""),
        used_llm=kw.pop("used_llm", False),
        sources=list(sources),
        timings={"search": 0.8, "fetch": elapsed_s},
        warnings=warnings,
        notices=notices,
        found=found,
        whitelisted=kw.pop("whitelisted", []),
        profile="research",
        **kw,
    )


SCENARIOS = {
    "1. All local, all good (the happy path)": _answer(
        found=5,
        elapsed_s=6.4,
        whitelisted=[],
        sources=[
            _source(
                1,
                "Kenya's economy grew 5.2% last quarter",
                "https://nation.africa/kenya/economy-grew",
                "GDP expanded on strong agriculture and services output.",
                "Kenya's economy grew 5.2 percent last quarter. "
                "Agriculture and services drove the expansion.",
                Method.LOCAL,
                age="2h ago",
            ),
            _source(
                2,
                "What the shilling's stability means for borrowers",
                "https://businessdaily.africa/shilling-stability",
                "The currency held steady through the rate decision.",
                "The shilling held steady against the dollar. "
                "Borrowers may see relief on dollar-denominated loans.",
                Method.LOCAL,
                age="5h ago",
            ),
        ],
    ),
    "2. A barrier page recovered by the API": _answer(
        found=6,
        elapsed_s=9.1,
        whitelisted=[("paywalled.example", "captcha")],
        sources=[
            _source(
                1,
                "Paywalled: the full rate decision",
                "https://paywalled.example/rate-decision",
                "The committee voted 6-1 to hold.",
                "The committee voted six to one to hold the rate. "
                "Inflation expectations were the deciding factor.",
                Method.API,
                age="1h ago",
            ),
            _source(
                2,
                "Regional comparison: East African central banks",
                "https://nation.africa/regional-banks",
                "Neighbours took different paths this quarter.",
                "Neighbouring central banks took different paths. "
                "Kenya held while others tightened.",
                Method.LOCAL,
                age="3h ago",
            ),
        ],
    ),
    "3. Everything degraded to snippets (local down)": _answer(
        found=4,
        elapsed_s=1.2,
        whitelisted=[],
        sources=[
            _source(
                1,
                "Inflation eases to 4.1 percent",
                "https://nation.africa/inflation-eases",
                "Food prices fell for a third straight month.",
                "",
                Method.SNIPPET,
            ),
            _source(
                2,
                "Analysts split on the next move",
                "https://businessdaily.africa/analysts-split",
                "Half expect a cut by December.",
                "",
                Method.SNIPPET,
            ),
        ],
    ),
    "4. A hard failure alongside real results": _answer(
        found=5,
        elapsed_s=7.8,
        whitelisted=[],
        sources=[
            _source(
                1,
                "Central bank statement in full",
                "https://nation.africa/statement-full",
                "The governor's remarks on Tuesday.",
                "The governor defended the hold on Tuesday. He cited exchange-rate volatility.",
                Method.LOCAL,
                age="2h ago",
            ),
            _source(
                2,
                "Dead link: page returns 404",
                "https://gone.example/old-story",
                "A story that has since been removed.",
                "",
                Method.SNIPPET,
                ok=False,
                error_class=ErrorClass.DEAD,
                # The reason `classify` gives a 404, now that T6.1 carries it
                # from the router to the card.
                warning="page not found (HTTP 404)",
            ),
        ],
    ),
    "5. Long titles, long summaries, narrow and wide terminals": _answer(
        found=3,
        elapsed_s=5.0,
        whitelisted=[("very-long-domain-name-here.example.co.ke", "403 forbidden")],
        sources=[
            _source(
                1,
                "A long headline that wraps once at eighty columns and shows the continuation line",
                "https://very-long-domain-name-here.example.co.ke/some/deep/path/story",
                "A snippet long enough to wrap at eighty columns too, so the dim "
                "meta line and the title line exercise the same rules.",
                "The first sentence of this summary is long enough to wrap on its own "
                "at a narrow terminal width. The second sentence follows it and should "
                "hang-indent to match, or the whole card loses its left edge.",
                Method.API,
                age="4h ago",
            ),
        ],
    ),
}


# --- Ask mode scenarios (T9.7) ---

ASK_SCENARIOS = {
    "A1. Answered with sources (LLM available)": Answer(
        question="What is the current Python version?",
        text="Python 3.12 is the latest stable release, with Python 3.13 in development.",
        used_llm=True,
        sources=[
            (
                1,
                "Python 3.12 Released",
                "https://python.org/downloads/",
                "python.org",
                "LOCAL",
                "Python 3.12 brought many improvements including the new type parameter syntax.",
                "",
                "",
                "",
            ),
            (
                2,
                "Python Release Schedule",
                "https://python.org/devcycle/",
                "python.org",
                "LOCAL",
                "Python 3.13 is currently in the development phase with planned October 2025 release.",
                "",
                "",
                "",
            ),
        ],
        timings={"search": 0.45, "fetch": 1.23, "model": 0.89},
        warnings=[],
    ),
    "A2. No model available (passages only)": Answer(
        question="What is machine learning?",
        text="""[1] Definition of ML
Machine learning is a subset of artificial intelligence that enables computers to learn from data without being explicitly programmed.

[2] Applications
Applications include image recognition, natural language processing, and predictive analytics.""",
        used_llm=False,
        sources=[
            (
                1,
                "Definition of ML",
                "https://example.org/ml-basics",
                "example.org",
                "LOCAL",
                "Machine learning is a subset of artificial intelligence that enables computers to learn from data without being explicitly programmed.",
                "",
                "",
                "",
            ),
            (
                2,
                "Applications",
                "https://example.org/ml-apps",
                "example.org",
                "LOCAL",
                "Applications include image recognition, natural language processing, and predictive analytics.",
                "",
                "",
                "",
            ),
        ],
        timings={"search": 0.32, "fetch": 0.91},
        warnings=["No Gemini API key found; returning passages only."],
    ),
    "A3. Sources disagree (clearly stated)": Answer(
        question="What version of Python is stable?",
        text="Sources disagree: python.org says 3.12.3 is the latest stable, while techcrunch.com says 3.13.0 was released.",
        used_llm=True,
        sources=[
            (
                1,
                "Python 3.12.3 Released",
                "https://python.org/downloads/",
                "python.org",
                "LOCAL",
                "Python 3.12.3 is the latest stable release as of today.",
                "",
                "",
                "",
            ),
            (
                2,
                "Python 3.13 Ships Ahead of Schedule",
                "https://techcrunch.com/2026/10/15/python-3-13",
                "techcrunch.com",
                "LOCAL",
                "Python 3.13.0 was officially released on October 14, 2026, earlier than expected.",
                "",
                "",
                "",
            ),
        ],
        timings={"search": 0.51, "fetch": 1.45, "model": 0.67},
        warnings=[],
    ),
    "A4. Knowledge sources (Wiki + SO)": Answer(
        question="What is the Python programming language?",
        text="Python is a high-level, interpreted programming language known for its clear syntax and readability. It was created by Guido van Rossum in 1991 and first released in 1994.",
        used_llm=True,
        sources=[
            (
                1,
                "Python (programming language)",
                "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "wikipedia.org",
                "WIKI",
                "Python is a high-level, interpreted programming language known for its clear syntax and readability. It was created by Guido van Rossum and first released in 1991. Python emphasizes code readability with its notable use of whitespace.",
                "CC BY-SA 4.0",
                "",
                "",
            ),
            (
                2,
                "How to install Python on Windows",
                "https://stackoverflow.com/questions/12345/how-to-install-python",
                "stackoverflow.com",
                "SO",
                "Download the installer from python.org and run it. Make sure to check 'Add Python to PATH' during installation options. Python 3.12 is the latest stable version as of 2024.",
                "CC BY-SA 4.0",
                "Jane Developer",
                "",
            ),
        ],
        timings={"search": 0.35, "fetch": 0.82},
        warnings=[],
    ),
    "A5. Nothing found (clear error)": Answer(
        question="xyznonexistentquery12345",
        text="No sources available to answer the query.",
        used_llm=False,
        sources=[],
        timings={"search": 0.01, "fetch": 0.0},
        warnings=["No search results found for query."],
    ),
}


def _live_lines(answer: Answer) -> t.Iterator[str]:
    """The (stage, detail) pairs the engine emits, one per page as it lands.

    Derived from the same sources the cards render rather than a second fixture,
    so the live block cannot drift out of step with the cards below it.
    """
    yield "search", ""
    yield "searched", str(answer.found)
    yield "fetch", str(len(answer.sources))
    for source in answer.sources:
        yield "fetched", source.domain
    if answer.timings.get("model"):
        yield "model", ""


def _render(answer: Answer, *, width: int, plain: bool) -> str:
    """Render one answer through the real UI, capturing the bytes it writes.

    Same objects `briefing research` and `briefing ask` build, same UI call
    order the CLI uses, so the saved samples are the exact bytes a run writes
    rather than an approximation of them.
    """
    console = Console(record=True, width=width)
    with UI(console=console, plain=plain) as ui:
        ui.begin(answer.question, "research")
        for stage, detail in _live_lines(answer):
            ui.on_progress(stage, detail)
        for source in answer.sources:
            ui.on_source(source)
        ui.show(answer)
    return console.export_text(clear=False)


def main() -> str:
    chunks = [HEADER]

    for label, answer in SCENARIOS.items():
        for width in (100, 80):
            chunks.append("=" * width)
            chunks.append(f"{label}  [width {width}]")
            chunks.append("=" * width)
            chunks.append(_render(answer, width=width, plain=True))

    chunks.append("\n" + "=" * 60)
    chunks.append("ASK MODE SCENARIOS")
    chunks.append("=" * 60 + "\n")

    for label, answer in ASK_SCENARIOS.items():
        chunks.append("=" * 100)
        chunks.append(f"{label}  [width 100]")
        chunks.append("=" * 100)
        chunks.append(_render(answer, width=100, plain=True))

    text = "\n".join(chunks)
    OUT.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    main()
