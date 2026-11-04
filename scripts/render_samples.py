"""Regenerate `docs/sample-output.txt` from the real UI renderers (T3.5).

Not a test. This renders the five T3.5 scenarios through the same `briefing.ui`
functions `briefing research` calls, so the saved samples are the exact bytes the
CLI writes — not an approximation of them. Run from the repo root:

    .venv/bin/python scripts/render_samples.py

Each scenario is a hand-built `Report` or `Answer`, so nothing here touches the
network, a search, or a fetch.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from briefing.ask import Answer
from briefing.fetch.router import ErrorClass, Method
from briefing.pipeline import Event, Report, Result
from briefing.ui import UI

OUT = Path(__file__).resolve().parent.parent / "docs" / "sample-output.txt"

HEADER = """\
`briefing` sample output
========================

Five runs rendered through the real `briefing.ui` renderers (T3.5), at the two
widths that matter. No network, no pipeline: each scenario is a `Report` handed
to the same functions `briefing research` calls, so these are the exact bytes the
UI writes. Regenerate with `scripts/render_samples.py` rather than editing by
hand.

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

Ask mode scenarios (T9.7):

  A1. Answered with sources - happy path with LLM
  A2. No model available - passages only with warning
  A3. Sources disagree - answer states disagreement
  A4. Nothing found - clear error message
"""


def _result(rank, title, url, snippet, summary, method, **kw):
    from urllib.parse import urlparse

    return Result(
        rank=rank,
        title=title,
        heading=kw.pop("heading", title),
        url=url,
        domain=urlparse(url).hostname or "",
        snippet=snippet,
        source=kw.pop("source", ""),
        age=kw.pop("age", ""),
        summary=summary,
        method=method,
        ok=kw.pop("ok", method is not Method.SNIPPET),
        error_class=kw.pop("error_class", ErrorClass.OK),
        **kw,
    )


SCENARIOS = {
    "1. All local, all good (the happy path)": Report(
        found=5,
        elapsed_s=6.4,
        whitelisted=[],
        results=[
            _result(
                1,
                "Kenya's economy grew 5.2% last quarter",
                "https://nation.africa/kenya/economy-grew",
                "GDP expanded on strong agriculture and services output.",
                "Kenya's economy grew 5.2 percent last quarter. "
                "Agriculture and services drove the expansion.",
                Method.LOCAL,
                age="2h ago",
            ),
            _result(
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
    "2. A barrier page recovered by the API": Report(
        found=6,
        elapsed_s=9.1,
        whitelisted=[("paywalled.example", "captcha")],
        results=[
            _result(
                1,
                "Paywalled: the full rate decision",
                "https://paywalled.example/rate-decision",
                "The committee voted 6-1 to hold.",
                "The committee voted six to one to hold the rate. "
                "Inflation expectations were the deciding factor.",
                Method.API,
                age="1h ago",
            ),
            _result(
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
    "3. Everything degraded to snippets (local down)": Report(
        found=4,
        elapsed_s=1.2,
        whitelisted=[],
        results=[
            _result(
                1,
                "Inflation eases to 4.1 percent",
                "https://nation.africa/inflation-eases",
                "Food prices fell for a third straight month.",
                "",
                Method.SNIPPET,
            ),
            _result(
                2,
                "Analysts split on the next move",
                "https://businessdaily.africa/analysts-split",
                "Half expect a cut by December.",
                "",
                Method.SNIPPET,
            ),
        ],
    ),
    "4. A hard failure alongside real results": Report(
        found=5,
        elapsed_s=7.8,
        whitelisted=[],
        results=[
            _result(
                1,
                "Central bank statement in full",
                "https://nation.africa/statement-full",
                "The governor's remarks on Tuesday.",
                "The governor defended the hold on Tuesday. He cited exchange-rate volatility.",
                Method.LOCAL,
                age="2h ago",
            ),
            _result(
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
    "5. Long titles, long summaries, narrow and wide terminals": Report(
        found=3,
        elapsed_s=5.0,
        whitelisted=[("very-long-domain-name-here.example.co.ke", "403 forbidden")],
        results=[
            _result(
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
            ),
            (
                2,
                "Python Release Schedule",
                "https://python.org/devcycle/",
                "python.org",
                "LOCAL",
                "Python 3.13 is currently in the development phase with planned October 2025 release.",
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
            ),
            (
                2,
                "Applications",
                "https://example.org/ml-apps",
                "example.org",
                "LOCAL",
                "Applications include image recognition, natural language processing, and predictive analytics.",
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
            ),
            (
                2,
                "Python 3.13 Ships Ahead of Schedule",
                "https://techcrunch.com/2026/10/15/python-3-13",
                "techcrunch.com",
                "LOCAL",
                "Python 3.13.0 was officially released on October 14, 2026, earlier than expected.",
            ),
        ],
        timings={"search": 0.51, "fetch": 1.45, "model": 0.67},
        warnings=[],
    ),
}


def _finished(result: Result, total: int) -> Event:
    """The progress line the pipeline emits for one finished URL.

    Derived from the same `Result` the card renders rather than a second fixture,
    so the live block cannot drift out of step with the cards below it.
    """
    return Event(
        kind="finished",
        query="kenya economy",
        mode="text",
        url=result.url,
        method=result.method,
        error_class=result.error_class,
        done=result.rank,
        total=total,
    )


def _render_answer(answer: Answer, *, plain: bool = False) -> str:
    """Render an Answer to a string (plain text mode, width 100).

    This mirrors `_render_answer` from cli.py but returns a string.
    """
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console(record=True, width=100)
    if plain:
        console.no_color = True

    # 1. Answer panel
    answer_panel = Panel(
        Text(answer.text),
        title="Answer",
        box=box.SIMPLE if plain else box.ROUNDED,
        expand=True,
    )
    console.print(answer_panel)

    # 2. Numbered source list
    console.print()
    for num, title, url, domain, method, text in answer.sources:
        # Build method badge text
        badge_text = f"[{method}]"

        line = Text()
        line.append(f"[{num}] ", "bold")
        line.append(title or "(no title)")
        line.append(f"  {badge_text}")
        console.print(line)

    # 3. Footer with timings and warnings
    footer_parts = []

    # Source counts by method
    local_count = sum(1 for s in answer.sources if s[4] == "LOCAL")
    api_count = sum(1 for s in answer.sources if s[4] == "API")
    snippet_count = sum(1 for s in answer.sources if s[4] == "SNIPPET")

    if local_count or api_count:
        counts_parts = []
        if local_count:
            counts_parts.append(f"{local_count} local")
        if api_count:
            counts_parts.append(f"{api_count} API")
        if snippet_count:
            counts_parts.append(f"{snippet_count} snippet")
        footer_parts.append(" · ".join(counts_parts))

    if answer.used_llm:
        method_desc = "LLM"
    else:
        method_desc = "passages"
    footer_parts.append(f"{len(answer.sources)} sources ({method_desc})")

    if answer.timings:
        timing_parts = []
        if "search" in answer.timings:
            timing_parts.append(f"search: {answer.timings['search']:.2f}s")
        if "fetch" in answer.timings:
            timing_parts.append(f"fetch: {answer.timings['fetch']:.2f}s")
        if "model" in answer.timings:
            timing_parts.append(f"model: {answer.timings['model']:.2f}s")
        if timing_parts:
            footer_parts.append(" · " + " · ".join(timing_parts))

    if answer.warnings:
        footer_parts.append(" · " + "; ".join(answer.warnings))

    if footer_parts:
        footer_panel = Panel(
            Text(" · ".join(footer_parts), "dim"),
            box=box.SIMPLE,
            expand=True,
        )
        console.print(footer_panel)

    return console.export_text(clear=False)


def main() -> str:
    chunks = [HEADER]

    # Render research scenarios
    for label, report in SCENARIOS.items():
        for width in (100, 80):
            console = Console(record=True, width=width)
            ui = UI(console=console, plain=True)
            ui.begin("kenya economy", "text")
            for result in report.results:
                ui.on_event(_finished(result, len(report.results)))
            ui.show(report)
            chunks.append("=" * width)
            chunks.append(f"{label}  [width {width}]")
            chunks.append("=" * width)
            chunks.append(console.export_text(clear=False))

    # Render ask scenarios
    chunks.append("\n" + "=" * 60)
    chunks.append("ASK MODE SCENARIOS (T9.7)")
    chunks.append("=" * 60 + "\n")

    for label, answer in ASK_SCENARIOS.items():
        output = _render_answer(answer, plain=False)
        chunks.append("=" * 100)
        chunks.append(f"{label}  [width 100]")
        chunks.append("=" * 100)
        chunks.append(output)

    text = "\n".join(chunks)
    OUT.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    main()
