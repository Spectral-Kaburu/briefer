"""Regenerate `docs/sample-output.txt` from the real UI renderers (T3.5).

Not a test. This renders the five T3.5 scenarios through the same `briefing.ui`
functions `briefing research` calls, so the saved samples are the exact bytes the
CLI writes — not an approximation of them. Run from the repo root:

    .venv/bin/python scripts/render_samples.py

Each scenario is a hand-built `Report`, so nothing here touches the network, a
search, or a fetch.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

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


def main() -> str:
    chunks = [HEADER]
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
    text = "\n".join(chunks)
    OUT.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    main()
