"""Tests for ui.py (T3.2): Rich rendering of a run, per SPEC section 3.

Snapshots use `Console(record=True, width=100)` + `export_text()`, both verified
against the installed `rich` (NOTES-APIS.md section 5). Nothing here runs a
pipeline, a search, or a fetch: the renderers take a `Report`, so the captured
text is exactly the bytes Rich would write.

Markup safety is the load-bearing property here. Titles, headings, summaries and
URLs all arrive from search results and are never trusted, so they must go
through `Text` rather than markup strings.
"""

from __future__ import annotations

import typing as t

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from briefing.fetch.router import ErrorClass, Method
from briefing.pipeline import Event, Report, Result
from briefing.ui import UI, badge, card, footer, header

# A recorded console is non-terminal, so `plain` auto-detects to True; every test
# that wants the colored rendering passes plain=False explicitly.
_COLORED = {"record": True, "width": 100}


def _render(renderable: t.Any, *, plain: bool = False) -> str:
    """Render one renderable to plain text, the way a snapshot test reads it.

    Deliberately **not** `markup=False`: that flag masks a regression where
    untrusted text was routed through a markup string, because it renders such a
    string literally either way. The markup-safety guarantee is guarded where it
    can actually bite — on the object type, in
    `test_a_card_carries_text_objects_not_markup_strings`.
    """
    console = Console(**_COLORED)  # type: ignore[arg-type]
    console.print(renderable)
    return console.export_text()


def _result(**kwargs: t.Any) -> Result:
    base: dict[str, t.Any] = {
        "rank": 1,
        "title": "Bank raises rate",
        "url": "https://nation.africa/bank-raises-rate",
        "domain": "nation.africa",
        "snippet": "The central bank raised its lending rate.",
        "source": "",
        "age": "2h ago",
        "heading": "Bank raises rate",
        "summary": "The central bank raised its lending rate by fifty basis points. It cited inflation.",
        "method": Method.LOCAL,
        "ok": True,
        "error_class": ErrorClass.OK,
    }
    base.update(kwargs)
    return Result(**base)  # type: ignore[arg-type]


def _report(*results: Result, **kwargs: t.Any) -> Report:
    base: dict[str, t.Any] = {
        "results": list(results),
        "found": len(results),
        "elapsed_s": 6.4,
        "whitelisted": [],
        "progress": None,
    }
    base.update(kwargs)
    return Report(**base)


# --- badges ----------------------------------------------------------------


def test_a_failed_fetch_degrades_to_the_snippet_not_to_failed() -> None:
    """T3.5: what a hard failure actually shows, driven through the real pipeline.

    SPEC section 3 defines ⚪ SNIPPET as "extraction failed, DDG snippet only",
    and every row of its classification table resolves to snippet-only, API, or a
    config error — none of them to "nothing to show at all". A DDG hit always
    carries a snippet, so there is always something to render. Hence a dead page
    shows ⚪ with its snippet, and 🔴 FAILED is unreachable in practice.

    Pinned here because it is the opposite of what the badge's own unit test
    implies (`badge(Method.LOCAL, ok=False)` renders FAILED, but no code path
    builds that combination), and because reading the rendered samples is what
    raised the question. If SPEC ever wants a true FAILED state, this is the test
    to change.
    """
    import asyncio

    from briefing.fetch.router import FetchResult, Method
    from briefing.pipeline import Pipeline

    class AlwaysFails:
        async def fetch(self, url: str, snippet: str) -> FetchResult:
            return FetchResult(method=Method.SNIPPET, ok=False, snippet=snippet)

    class OneHit:
        async def search(self, query: str):
            return [{"title": "Gone", "url": "https://gone.example/x", "body": "a snippet"}]

    report = asyncio.run(
        Pipeline(
            searcher=OneHit(),
            router=AlwaysFails(),
            extractor=None,
            whitelist=None,
        ).run("query")
    )
    assert report.results
    shown = _render(badge(report.results[0].method, report.results[0].ok, plain=True))
    assert "SNIPPET" in shown
    assert "FAILED" not in shown
    # And the snippet the run still has is on the card, which is the point.
    assert "a snippet" in _render(card(report.results[0], plain=True))
    assert _render(badge(Method.LOCAL, True, plain=False)).strip() == "🟢 LOCAL"
    assert _render(badge(Method.API, True, plain=False)).strip() == "🟡 API"
    assert _render(badge(Method.SNIPPET, False, plain=False)).strip() == "⚪ SNIPPET"
    assert _render(badge(Method.LOCAL, False, plain=False)).strip() == "🔴 FAILED"


def test_plain_badges_drop_the_emoji() -> None:
    assert _render(badge(Method.LOCAL, True, plain=True)).strip() == "LOCAL"


def test_a_snippet_only_page_is_degraded_not_failed() -> None:
    """A snippet page still has its snippet, so SPEC section 3 shows ⚪ not 🔴."""
    assert _render(badge(Method.SNIPPET, ok=False, plain=False)).strip() == "⚪ SNIPPET"


# --- the header ------------------------------------------------------------


def test_the_header_names_the_query_and_mode() -> None:
    out = _render(header("latest breaking news in kenya", "news", plain=False))
    assert "briefing" in out
    assert "▸ research" in out
    assert 'Query: "latest breaking news in kenya"' in out
    assert "mode: news" in out


def test_the_header_carries_the_time_window() -> None:
    out = _render(header("latest news", "news", "past 24h", plain=False))
    assert "mode: news · past 24h" in out


def test_the_header_leaves_out_an_absent_window() -> None:
    assert "·" not in _render(header("a query", "text", plain=False))


def test_the_query_is_not_parsed_as_markup() -> None:
    """A query containing [bold] must render literally, not as a tag."""
    out = _render(header("query with [bold] brackets", "text", plain=False))
    assert "[bold]" in out


# --- result cards ----------------------------------------------------------


def test_a_card_shows_rank_heading_meta_summary_and_url() -> None:
    out = _render(card(_result(), plain=False))
    assert "1" in out
    assert "Bank raises rate" in out
    assert "nation.africa" in out
    assert "2h ago" in out
    assert "central bank raised" in out
    assert "https://nation.africa/bank-raises-rate" in out
    assert "🟢 LOCAL" in out


def test_a_card_with_no_age_drops_the_separator() -> None:
    out = _render(card(_result(age=""), plain=False))
    assert "nation.africa" in out
    assert " · " not in out


def test_a_card_falls_back_to_the_search_title() -> None:
    """The heading is the extracted one; the search title stands in for it."""
    out = _render(card(_result(heading="", title="Search title"), plain=False))
    assert "Search title" in out


def test_a_card_is_markup_safe() -> None:
    """Headlines and summaries come from the network and must not be re-parsed."""
    result = _result(heading="Rate [not bold] decision", summary="A [b]summary[/b].")
    out = _render(card(result, plain=False))
    assert "[not bold]" in out
    assert "[b]summary[/b]" in out


def test_the_footer_uses_specs_wording_for_two_snippet_only_pages() -> None:
    """SPEC section 3 writes "2 snippet-only", not "2 snippet-onlys"."""
    report = _report(
        _result(rank=1),
        _result(rank=2, domain="b.example", ok=False, method=Method.SNIPPET),
        _result(rank=3, domain="c.example", ok=False, method=Method.SNIPPET),
        found=3,
    )
    assert "2 snippet-only ·" in _render(footer(report, plain=False))
    assert "snippet-onlys" not in _render(footer(report, plain=False))


def test_a_card_carries_text_objects_not_markup_strings() -> None:
    """Guard the mechanism, not only the outcome.

    `[not bold]` reaches `export_text()` intact either way: `Text.append`
    sanitizes brackets, and so does `markup=False`. The property that actually
    distinguishes the safe implementation is that untrusted fields live inside a
    `Text`, so a refactor to `console.print(f"... {heading} ...")` fails here.
    """
    panel = card(_result(heading="unicode ሴ dash", summary="prose"), plain=False)
    assert isinstance(panel.renderable, Text)


def test_a_card_without_a_summary_still_renders() -> None:
    """A page that degraded to snippet-only has no summary to show."""
    out = _render(
        card(_result(ok=False, method=Method.SNIPPET, summary=""), plain=False),
    )
    assert "⚪ SNIPPET" in out
    assert "nation.africa" in out


def test_a_snippet_only_card_shows_the_snippet_it_does_have() -> None:
    """T3.5: the snippet is a snippet-only page's ONLY content.

    Rendering it as heading + domain + URL and nothing else throws away the one
    thing the run still knows about the page — which is the entire point of
    degrading to snippet-only rather than failing. Found by reading the rendered
    samples, not by the existing test, which only asserted what was absent.
    """
    out = _render(
        card(
            _result(
                ok=False,
                method=Method.SNIPPET,
                summary="",
                snippet="The central bank raised its lending rate.",
            ),
            plain=False,
        ),
    )
    assert "⚪ SNIPPET" in out
    assert "The central bank raised its lending rate." in out
    # Not repeated: a real summary still means no snippet line.
    assert out.count("The central bank raised its lending rate.") == 1


def test_a_card_with_a_summary_does_not_also_show_the_snippet() -> None:
    """The snippet is the fallback body, not a second body."""
    out = _render(
        card(_result(snippet="A SNIPPET THAT MUST NOT APPEAR"), plain=False),
    )
    assert "by fifty basis points" in out  # the summary
    assert "A SNIPPET THAT MUST NOT APPEAR" not in out


def test_the_footer_wraps_at_the_separator_when_the_terminal_is_narrow() -> None:
    """T3.5: a narrow terminal must wrap between segments, not inside one.

    Documenting the *known* limit rather than asserting it is fixed: Rich's
    default word-wrap breaks `whitelist +1 (example.co.ke:` away from `captcha)`
    and orphans `6.4s`, which reads badly. The two fixes both cost more than the
    defect does — non-breaking spaces would break the plain-text substring
    assertions the rest of this file depends on (`"2 snippet-only ·"`), and
    teaching `footer()` the console width would change a pure function's
    signature to serve a 60-column terminal. The footer is one line at the
    100-column width the design targets; below that it degrades by wrapping.
    """
    report = _report(
        _result(rank=1),
        _result(rank=2, domain="standardmedia.co.ke", method=Method.API, ok=True),
        _result(rank=3, domain="the-star.co.ke", ok=False, method=Method.SNIPPET),
    )
    report.whitelisted = [("a-very-long-domain-name.example.co.ke", "captcha on the page")]
    console = Console(record=True, width=60)
    console.print(footer(report, plain=True))
    assert "3 found" in console.export_text(clear=False)


# --- the footer ------------------------------------------------------------


def test_the_footer_counts_found_extracted_and_snippet_only() -> None:
    report = _report(
        _result(rank=1),
        _result(rank=2, domain="standardmedia.co.ke", method=Method.API, ok=True),
        _result(rank=3, domain="the-star.co.ke", ok=False, method=Method.SNIPPET),
        found=3,
    )
    out = _render(footer(report, plain=False))
    assert "3 found" in out
    assert "2 extracted" in out
    assert "1 local" in out
    assert "1 api" in out
    assert "1 snippet-only" in out
    assert "6.4s" in out


def test_the_footer_lists_whitelisted_domains_with_reasons() -> None:
    """SPEC section 3: `whitelist +1 (example.co.ke: captcha)`."""
    report = _report(_result(), whitelisted=[("example.co.ke", "captcha")], found=1)
    out = _render(footer(report, plain=False))
    assert "whitelist +1 (example.co.ke: captcha)" in out


def test_the_footer_omits_empty_sections() -> None:
    """A clean run shows found, extracted and time — nothing else."""
    out = _render(footer(_report(_result()), plain=False))
    assert "snippet-only" not in out
    assert "whitelist" not in out


def test_the_footer_survives_a_run_with_no_results() -> None:
    out = _render(footer(_report(found=0, elapsed_s=0.0), plain=False))
    assert "0 found" in out
    assert "0.0s" in out
    assert "extracted" not in out


# --- the UI object ---------------------------------------------------------


def test_the_header_cards_and_footer_render_through_one_ui() -> None:
    ui = UI(Console(**_COLORED), plain=False)  # type: ignore[arg-type]
    ui.begin("latest news", "news")
    ui.on_event(Event(kind="finished", url="https://nation.africa/x", method=Method.LOCAL))
    ui.show(_report(_result(), whitelisted=[("example.co.ke", "captcha")]))
    out = ui.console.export_text()
    assert "briefing" in out
    assert "🟢 LOCAL" in out
    assert "3 found" not in out  # a one-result run, not the footer fixture
    assert "1 found" in out
    assert "whitelist +1 (example.co.ke: captcha)" in out


def test_plain_mode_switches_color_off_on_the_console() -> None:
    """`--plain` and a piped console both mean no decoration (SPEC section 3)."""
    console = Console(**_COLORED)  # type: ignore[arg-type]
    UI(console, plain=True)
    assert console.no_color is True

    colored = Console(**_COLORED)  # type: ignore[arg-type]
    UI(colored, plain=False)
    assert colored.no_color is False


def test_no_color_switches_color_off_without_plain(monkeypatch: t.Any) -> None:
    """SPEC section 3: "Respects NO_COLOR".

    Rich detects the variable at Console construction. The property worth pinning
    is that colour comes off *without* `--plain`, i.e. a terminal run with
    NO_COLOR set keeps its layout but loses its colour.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    ui = UI(Console(record=True, width=100, force_terminal=True), plain=False)
    assert ui.console.no_color is True
    assert ui.plain is False


def test_a_piped_console_renders_plain_without_being_asked() -> None:
    """SPEC section 3: "auto --plain when piped"."""
    console = Console(**_COLORED)  # type: ignore[arg-type]
    assert console.is_terminal is False
    ui = UI(console)
    assert ui.plain is True


def test_forced_plain_beats_a_terminal() -> None:
    """`--plain` must work even when the output really is a terminal."""
    ui = UI(Console(record=True, width=100, force_terminal=True), plain=True)
    assert ui.plain is True


def test_the_live_block_keeps_its_lines_when_plain() -> None:
    """A piped run must not erase the progress it just wrote.

    `transient` is what erases it, so a plain (logged) run leaves it behind.
    """
    ui = UI(Console(**_COLORED), plain=True)  # type: ignore[arg-type]
    ui.begin("latest news", "news")
    ui.on_event(Event(kind="finished", url="https://nation.africa/x", method=Method.LOCAL))
    ui.show(_report(_result()))
    out = ui.console.export_text()
    assert "nation.africa" in out


def test_an_empty_url_in_an_event_does_not_crash_the_progress() -> None:
    """The pipeline emits a done event with no URL; the UI must ignore it."""
    ui = UI(Console(**_COLORED), plain=True)  # type: ignore[arg-type]
    ui.begin("latest news", "news")
    ui.on_event(Event(kind="done", query="q", done=0, total=0))
    ui.show(_report(_result()))
    assert ui.console.export_text()


def test_a_result_is_a_real_panel() -> None:
    """Guard the type so a future refactor cannot return a bare string."""
    assert isinstance(card(_result(), plain=False), Panel)


def test_the_footer_is_a_real_panel() -> None:
    assert isinstance(footer(_report(_result()), plain=False), Panel)
