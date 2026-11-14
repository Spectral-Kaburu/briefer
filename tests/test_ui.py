"""Tests for ui.py: Rich rendering of one inquiry (`ask` and `research` alike).

Snapshots use `Console(record=True, width=100)` + `export_text()`. Nothing here
runs a search, a fetch or a model: the renderers take an `Answer`, so the captured
text is exactly the bytes Rich would write.

Markup safety is the load-bearing property. Titles, summaries, URLs, warnings and
the model's own Markdown all arrive from the network and must never be parsed as
Rich markup, nor make a clickable link or a terminal escape.
"""

from __future__ import annotations

import typing as t

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from briefing.fetch.router import Method
from briefing.inquiry import ASK, RESEARCH, Answer, EvidenceSource, Notice
from briefing.synth import ModelResult
from briefing.ui import UI, answer_card, badge, card, footer, header

# A recorded console is non-terminal, so `plain` auto-detects to True; every test
# that wants the coloured rendering passes plain=False explicitly.
_COLORED = {"record": True, "width": 100}


def _console(**kwargs: t.Any) -> Console:
    return Console(**{**_COLORED, **kwargs})


def _render(renderable: t.Any, *, width: int = 100) -> str:
    """One renderable as the plain text a snapshot test reads.

    Deliberately **not** `markup=False`: that flag would mask a regression where
    untrusted text went through a markup string.
    """
    console = Console(record=True, width=width)
    console.print(renderable)
    return console.export_text()


def _src(number: int = 1, **kwargs: t.Any) -> EvidenceSource:
    base: dict[str, t.Any] = {
        "number": number,
        "source": "Bank raises rate",
        "domain": "nation.africa",
        "title": "Bank raises rate",
        "url": "https://nation.africa/bank-raises-rate",
        "method": Method.LOCAL,
        "text": "The central bank raised its lending rate by fifty basis points.",
        "snippet": "The central bank raised its lending rate.",
        "heading": "Bank raises rate",
        "age": "2h ago",
    }
    base.update(kwargs)
    return EvidenceSource(**base)


def _model(**kwargs: t.Any) -> ModelResult:
    base: dict[str, t.Any] = {
        "text": "ok",
        "finish_reason": "stop",
        "label": "openai/gpt-4o-mini",
        "elapsed_s": 2.1,
    }
    base.update(kwargs)
    return ModelResult(**base)


def _answer(*sources: EvidenceSource, **kwargs: t.Any) -> Answer:
    base: dict[str, t.Any] = {
        "question": "what did the bank do",
        "text": "The bank raised its rate [1].",
        "used_llm": True,
        "sources": list(sources) or [_src()],
        "timings": {"search": 0.8, "fetch": 3.1, "model": 2.1},
        "warnings": [],
        "model": _model(),
        "profile": "research",
        "found": 3,
    }
    base.update(kwargs)
    return Answer(**base)


# --- badges ----------------------------------------------------------------


def test_badges() -> None:
    assert _render(badge(Method.LOCAL, True)).strip() == "🟢 LOCAL"
    assert _render(badge(Method.API, True)).strip() == "🟡 API"
    assert _render(badge(Method.SNIPPET, False)).strip() == "⚪ SNIPPET"
    assert _render(badge(Method.LOCAL, False)).strip() == "🔴 FAILED"
    assert _render(badge(Method.LOCAL, True, plain=True)).strip() == "LOCAL"


# --- the header ------------------------------------------------------------


def test_the_header_names_the_profile_for_both_modes() -> None:
    assert "▸ ask" in _render(header("q", ASK))
    assert "▸ research" in _render(header("q", RESEARCH))
    assert "▸ research" in _render(header("q", "research"))  # a bare name works too


def test_the_header_shows_query_mode_and_window() -> None:
    out = _render(header("latest news", RESEARCH, "news", "past 24h"))
    assert 'Query: "latest news"' in out
    assert "mode: news · past 24h" in out


def test_the_header_leaves_out_absent_mode_and_window() -> None:
    assert "·" not in _render(header("a query", ASK))
    assert "mode" not in _render(header("a query", ASK))


def test_the_header_is_markup_safe_and_one_line() -> None:
    out = _render(header("query with [bold] brackets\nand a newline", ASK))
    assert "[bold]" in out
    assert 'Query: "query with [bold] brackets and a newline"' in out


# --- source cards ----------------------------------------------------------


def test_a_card_shows_number_heading_meta_text_and_url() -> None:
    out = _render(card(_src(), plain=False))
    assert "[1]" in out
    assert "Bank raises rate" in out
    assert "nation.africa · 2h ago" in out
    assert "central bank raised" in out
    assert "https://nation.africa/bank-raises-rate" in out
    assert "🟢 LOCAL" in out


def test_a_card_shows_the_citation_number_not_its_position() -> None:
    """Cards in a list are numbered by `source.number`: `[3]` in the answer is card `[3]`."""
    answer = _answer(_src(3, domain="c.example"), _src(7, domain="g.example"), text="x [3] [7]")
    console = _console()
    UI(console, plain=False).show(answer)
    out = console.export_text()
    assert "[3]" in out
    assert "[7]" in out
    assert "[1]  " not in out
    assert "[2]  " not in out


def test_cards_print_in_citation_order_even_if_given_out_of_order() -> None:
    answer = _answer(_src(5, domain="e.example"), _src(2, domain="b.example"))
    console = _console()
    UI(console, plain=False).show(answer)
    out = console.export_text()
    assert out.index("b.example") < out.index("e.example")


def test_a_card_with_no_age_drops_the_separator() -> None:
    assert " · " not in _render(card(_src(age=""), plain=False))


def test_a_card_falls_back_to_the_title_then_the_url() -> None:
    assert "Search title" in _render(card(_src(heading="", title="Search title")))
    assert "only-url.example" in _render(
        card(_src(heading="", title="", url="https://only-url.example/"))
    )


def test_a_card_is_markup_safe() -> None:
    out = _render(card(_src(heading="Rate [not bold] decision", text="A [b]summary[/b].")))
    assert "[not bold]" in out
    assert "[b]summary[/b]" in out


def test_a_card_carries_text_objects_not_markup_strings() -> None:
    """Guard the mechanism: untrusted fields live inside a `Text`."""
    assert isinstance(card(_src()).renderable, Text)


def test_a_card_says_why_the_page_degraded_and_keeps_it_inert() -> None:
    out = _render(
        card(
            _src(
                ok=False,
                method=Method.SNIPPET,
                text="",
                warning="the site says [bold]blocked[/bold] (HTTP 404)",
            )
        )
    )
    assert "the site says [bold]blocked[/bold] (HTTP 404)" in out
    assert "⚪ SNIPPET" in out


def test_a_snippet_only_card_shows_the_snippet_once() -> None:
    out = _render(card(_src(ok=False, method=Method.SNIPPET, text="")))
    assert out.count("The central bank raised its lending rate.") == 1


def test_a_card_with_text_does_not_also_show_the_snippet() -> None:
    assert "MUST NOT APPEAR" not in _render(card(_src(snippet="MUST NOT APPEAR")))


def test_a_clean_card_carries_no_warning_and_a_bare_link_still_renders() -> None:
    out = _render(card(_src(text="", snippet="", heading="", title="")))
    assert "[1]" in out
    assert "https://nation.africa/bank-raises-rate" in out


def test_a_card_shows_license_and_author_for_knowledge_sources() -> None:
    out = _render(card(_src(method=Method.WIKI, license="CC BY-SA 4.0", author="someone")))
    assert "CC BY-SA 4.0" in out
    assert "by someone" in out
    assert "📖 WIKI" in out


def test_a_card_accepts_a_method_given_as_a_name() -> None:
    """Legacy tuples carry the method as its name, e.g. "API"."""
    assert "🟡 API" in _render(card(_src(method="API")))


def test_escape_sequences_in_card_text_never_reach_the_output() -> None:
    out = _render(card(_src(text="red \x1b[31mALERT\x1b[0m text", heading="bell\x07 title")))
    assert "\x1b" not in out
    assert "\x07" not in out
    assert "ALERT" in out


def test_a_newline_in_a_title_does_not_break_the_card() -> None:
    out = _render(card(_src(heading="Line one\n╭──fake border")))
    first = next(line for line in out.splitlines() if "Line one" in line)
    assert "fake border" in first


# --- the answer card -------------------------------------------------------


def test_the_answer_card_renders_markdown_on_a_terminal() -> None:
    panel = answer_card(
        _answer(text="The bank **raised** its rate [1].\n\n- one\n- two"), plain=False
    )
    assert isinstance(panel, Panel)
    out = _render(panel)
    assert "✨ ANSWER" in out
    assert "synthesised by openai/gpt-4o-mini · 1 source" in out
    assert "**" not in out  # rendered, not shown raw
    assert "The bank raised its rate [1]." in out
    assert "• one" in out


def test_rich_markup_in_model_text_is_inert() -> None:
    """`[bold red]x[/]` in the model's Markdown is literal text, not a Rich style tag."""
    text = "Danger [bold red]x[/] and [link=https://evil.example]click[/link] and [/] done"
    panel = answer_card(_answer(text=text), plain=False)
    out = _render(panel)
    assert "[bold red]x[/]" in out
    assert "[link=https://evil.example]click[/link]" in out
    assert "and [/] done" in out
    # And at the style level: nothing in the rendered segments is red or a link.
    console = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    console.print(panel)
    ansi = console.export_text(styles=True)
    assert "\x1b[1;31m" not in ansi
    assert "\x1b]8;" not in ansi  # no OSC 8 hyperlink


def test_a_markdown_link_shows_its_url_and_is_not_clickable() -> None:
    text = "See [the docs](https://example.com/docs) for more."
    panel = answer_card(_answer(text=text), plain=False)
    assert "https://example.com/docs" in _render(panel)
    console = Console(record=True, width=100, force_terminal=True, color_system="truecolor")
    console.print(panel)
    assert "\x1b]8;" not in console.export_text(styles=True)


def test_a_link_cannot_hide_a_different_target_behind_its_label() -> None:
    text = "[https://bank.example](https://evil.example/phish)"
    out = _render(answer_card(_answer(text=text), plain=False))
    assert "https://evil.example/phish" in out


def test_ansi_escapes_in_model_text_are_stripped() -> None:
    text = "Rate rose \x1b[2J\x1b]0;pwned\x07 by half a point [1]."
    for plain in (False, True):
        out = _render(answer_card(_answer(text=text), plain=plain))
        assert "\x1b" not in out
        assert "\x07" not in out
        assert "Rate rose" in out


def test_plain_prints_the_raw_markdown_as_text() -> None:
    text = "The bank **raised** its rate [1].\n\n- one\n- [docs](https://example.com)"
    out = _render(answer_card(_answer(text=text), plain=True))
    assert "**raised**" in out
    assert "[docs](https://example.com)" in out
    assert "- one" in out
    assert "ANSWER" in out
    assert "✨" not in out


def test_plain_model_text_is_not_parsed_as_markup() -> None:
    out = _render(answer_card(_answer(text="x [bold red]y[/] z"), plain=True))
    assert "[bold red]y[/]" in out


def test_ask_without_a_model_shows_the_passages_card() -> None:
    answer = _answer(
        text="[1] Bank raises rate\nThe central bank raised its rate.",
        used_llm=False,
        model=None,
        profile="ask",
        show_passages_when_no_model=True,
        notices=[Notice("model", "No LLM API key found")],
    )
    out = _render(answer_card(answer, plain=False))
    assert "📄 PASSAGES" in out
    assert "no model answer" in out
    assert "The central bank raised its rate." in out


def test_research_without_a_model_shows_no_answer_card_only_cards_and_the_notice() -> None:
    answer = _answer(
        text="[1] Bank raises rate\nThe central bank raised its rate.",
        used_llm=False,
        model=None,
        profile="research",
        notices=[Notice("model", "No LLM API key found; showing the extracted passages only.")],
    )
    assert answer_card(answer, plain=False) is None
    console = _console()
    UI(console, plain=False).show(answer)
    out = console.export_text()
    assert "Answer" not in out
    assert "PASSAGES" not in out
    assert "[1]  Bank raises rate" in out
    assert "model: No LLM API key found" in out


def test_a_failed_model_is_visible_for_research_too() -> None:
    answer = _answer(
        text="[1] x",
        used_llm=False,
        model=_model(text=None, error="HTTP 429 quota exceeded"),
        notices=[
            Notice(
                "model",
                "openai/gpt-4o-mini failed: HTTP 429 quota exceeded; "
                "showing the extracted passages instead",
            )
        ],
    )
    assert answer_card(answer) is None
    out = _render(footer(answer))
    assert "model: openai/gpt-4o-mini failed: HTTP 429 quota exceeded" in out


def test_an_empty_answer_text_gives_no_card() -> None:
    assert answer_card(_answer(text="  \n")) is None


def test_the_provisional_card_is_badged_and_lists_what_is_still_loading() -> None:
    answer = _answer(provisional=True, pending=["slow.example", "slower.example"])
    out = _render(answer_card(answer, plain=False))
    assert "⏳ PROVISIONAL" in out
    assert "still reading: slow.example, slower.example" in out
    assert "ANSWER" not in out


def test_a_provisional_card_without_pending_hosts_has_no_still_reading_line() -> None:
    assert "still reading" not in _render(answer_card(_answer(provisional=True), plain=False))


def test_no_provisional_card_when_the_model_failed() -> None:
    answer = _answer(provisional=True, used_llm=False, text="[1] x", pending=["a.example"])
    assert answer_card(answer) is None


# --- the footer ------------------------------------------------------------


def test_the_footer_counts_found_extracted_and_snippet_only() -> None:
    answer = _answer(
        _src(1),
        _src(2, domain="standardmedia.co.ke", method=Method.API),
        _src(3, domain="the-star.co.ke", ok=False, method=Method.SNIPPET),
        found=3,
    )
    out = _render(footer(answer))
    assert "3 found" in out
    assert "2 extracted (1 local, 1 api)" in out
    assert "1 snippet-only" in out


def test_the_footer_uses_specs_wording_for_two_snippet_only_pages() -> None:
    answer = _answer(
        _src(1),
        _src(2, domain="b.example", ok=False, method=Method.SNIPPET),
        _src(3, domain="c.example", ok=False, method=Method.SNIPPET),
        found=3,
    )
    out = _render(footer(answer))
    assert "2 snippet-only" in out
    assert "snippet-onlys" not in out


def test_the_footer_lists_whitelist_additions_with_reasons() -> None:
    answer = _answer(whitelisted=[("example.co.ke", "captcha")])
    assert "whitelist +1 (example.co.ke: captcha)" in _render(footer(answer))


def test_the_footer_shows_timings_and_the_model_label() -> None:
    out = _render(footer(_answer()))
    assert "search 0.8s" in out
    assert "fetch 3.1s" in out
    assert "model 2.1s" in out
    assert "6.0s total" in out
    assert "model: openai/gpt-4o-mini (2.1s)" in out


def test_a_clean_run_has_no_notice_whitelist_or_snippet_lines() -> None:
    out = _render(footer(_answer()))
    assert "snippet-only" not in out
    assert "whitelist" not in out
    assert "search:" not in out


def test_every_notice_is_on_its_own_line_prefixed_by_its_stage() -> None:
    answer = _answer(
        notices=[
            Notice("search", "search did not finish within 20s"),
            Notice("fetch", "slow.example timed out; used its snippet"),
            Notice("model", "the answer cites [9], which is not one of the sources"),
        ]
    )
    lines = _render(footer(answer)).splitlines()
    assert any(line.strip() == "search: search did not finish within 20s" for line in lines)
    assert any(line.strip().startswith("fetch: slow.example timed out") for line in lines)
    assert any(line.strip().startswith("model: the answer cites [9]") for line in lines)


def test_a_failed_model_is_named_once_not_twice() -> None:
    problem = "openai/gpt-4o-mini failed: HTTP 500"
    answer = _answer(
        used_llm=False,
        model=_model(text=None, error="HTTP 500"),
        notices=[Notice("model", f"{problem}; showing the extracted passages instead")],
    )
    assert _render(footer(answer)).count(problem) == 1


def test_a_failed_model_with_no_notice_still_shows_in_the_footer() -> None:
    answer = _answer(used_llm=False, model=_model(text=None, block_reason="SAFETY"))
    assert "model: openai/gpt-4o-mini declined to answer (SAFETY)" in _render(footer(answer))


def test_a_truncated_model_is_visible_in_the_footer() -> None:
    answer = _answer(model=_model(text="cut", finish_reason="length"), used_llm=False)
    assert "cut off at its output limit" in _render(footer(answer))


def test_notices_are_markup_safe_and_one_line() -> None:
    answer = _answer(notices=[Notice("fetch", "site said [bold]no[/bold]\nsecond line")])
    out = _render(footer(answer))
    assert "[bold]no[/bold] second line" in out


def test_warnings_without_notices_still_reach_the_footer() -> None:
    answer = _answer(warnings=["a legacy warning"], notices=[])
    assert "a legacy warning" in _render(footer(answer))


def test_the_footer_survives_a_run_with_nothing() -> None:
    answer = _answer(sources=[], found=0, timings={}, model=None, used_llm=False, text="")
    answer = Answer(**{**answer.__dict__, "sources": []})
    out = _render(footer(answer))
    assert "0 found" in out
    assert "extracted" not in out
    assert "total" not in out


def test_the_footer_is_a_real_panel() -> None:
    assert isinstance(footer(_answer()), Panel)


# --- the UI object ---------------------------------------------------------


def test_header_cards_and_footer_render_through_one_ui_for_both_profiles() -> None:
    outputs = {}
    for profile in (ASK, RESEARCH):
        console = _console()
        ui = UI(console, plain=False)
        ui.begin("latest news", profile, "news")
        ui.on_source(_src())
        ui.show(_answer(profile=profile.name, whitelisted=[("example.co.ke", "captcha")]))
        outputs[profile.name] = console.export_text()
    for name, out in outputs.items():
        assert f"▸ {name}" in out
        assert "✨ ANSWER" in out
        assert "🟢 LOCAL" in out
        assert "whitelist +1 (example.co.ke: captcha)" in out
    # Same layout: only the profile name in the header differs.
    assert outputs["ask"].replace("ask", "X") == outputs["research"].replace("research", "X")


def test_progress_status_lines_and_sources_are_shown_live_and_kept_when_plain() -> None:
    console = _console()
    ui = UI(console, plain=True)
    ui.begin("q", RESEARCH)
    ui.on_progress("search")
    ui.on_progress("searched", "10")
    ui.on_progress("fetch", "2")
    ui.on_source(_src(1))
    ui.on_source(_src(2, domain="b.example", ok=False, method=Method.SNIPPET))
    ui.on_progress("model")
    ui.show(_answer(_src(1), _src(2, domain="b.example")))
    out = console.export_text()
    assert "✔ LOCAL  [1] nation.africa" in out
    assert "⚠ SNIPPET  [2] b.example" in out


def test_a_progress_line_ends_before_the_first_card_starts() -> None:
    """A kept progress line must be *terminated*: `Live` leaves the cursor mid-line."""
    console = _console()
    ui = UI(console, plain=True)
    ui.begin("q", ASK)
    ui.on_source(_src(1))
    ui.show(_answer(_src(1)))
    out = console.export_text()
    assert "nation.africa\n╭" in out, "the progress line ran into the card border"


def test_the_status_line_does_not_linger_in_a_plain_log() -> None:
    console = _console()
    ui = UI(console, plain=True)
    ui.begin("q", ASK)
    ui.on_progress("fetch", "1")
    ui.on_source(_src(1))
    ui.on_progress("model")
    ui.show(_answer(_src(1)))
    out = console.export_text()
    assert "Writing the answer" not in out
    assert "Reading 1 pages" not in out


def test_the_status_line_tracks_pages_read() -> None:
    ui = UI(_console(), plain=True)
    ui.begin("q", ASK)
    ui.on_progress("fetch", "3")
    ui.on_source(_src(1))
    assert ui._status == "Read 1/3 pages"
    ui.on_progress("fetched", "ignored")  # counted from `on_source`, not here
    assert ui._status == "Read 1/3 pages"
    ui.close()


def test_progress_callbacks_before_begin_are_harmless() -> None:
    ui = UI(_console(), plain=True)
    ui.on_progress("search")
    ui.on_source(_src())
    ui.show(_answer())


def test_a_provisional_card_prints_before_the_final_answer_and_the_slow_page_is_in_the_final() -> (
    None
):
    console = _console()
    ui = UI(console, plain=False)
    ui.begin("q", RESEARCH)
    ui.on_source(_src(1))
    ui.on_partial(
        _answer(
            _src(1),
            text="Provisional text [1].",
            provisional=True,
            pending=["slow.example"],
        )
    )
    ui.on_source(_src(2, domain="slow.example"))
    ui.show(_answer(_src(1), _src(2, domain="slow.example"), text="Final text [1][2].", found=2))
    out = console.export_text()
    assert out.index("PROVISIONAL") < out.index("Final text")
    assert "still reading: slow.example" in out
    assert out.index("Final text") < out.index("[2]  Bank raises rate")
    assert "✨ ANSWER" in out


def test_a_failed_provisional_model_prints_its_notice_not_a_card() -> None:
    console = _console()
    ui = UI(console, plain=False)
    ui.begin("q", RESEARCH)
    ui.on_partial(
        _answer(
            provisional=True,
            used_llm=False,
            text="[1] x",
            notices=[Notice("model", "openai/gpt-4o-mini failed: HTTP 500; showing passages")],
        )
    )
    ui.close()
    out = console.export_text()
    assert "PROVISIONAL" not in out
    assert "model: openai/gpt-4o-mini failed: HTTP 500" in out


def test_close_is_idempotent_and_the_context_manager_closes_the_live_block() -> None:
    console = _console()
    with UI(console, plain=True) as ui:
        ui.begin("q", ASK)
        ui.on_source(_src())
    assert ui._live is None
    ui.close()


def test_plain_mode_switches_colour_off_on_the_console() -> None:
    console = _console()
    UI(console, plain=True)
    assert console.no_color is True
    coloured = _console()
    UI(coloured, plain=False)
    assert coloured.no_color is False


def test_no_color_switches_colour_off_without_plain(monkeypatch: t.Any) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    ui = UI(Console(record=True, width=100, force_terminal=True), plain=False)
    assert ui.console.no_color is True
    assert ui.plain is False


def test_a_piped_console_renders_plain_without_being_asked() -> None:
    console = _console()
    assert console.is_terminal is False
    assert UI(console).plain is True


def test_forced_plain_beats_a_terminal() -> None:
    assert UI(Console(record=True, width=100, force_terminal=True), plain=True).plain is True


def test_plain_output_goes_to_stdout_by_default(capsys: t.Any) -> None:
    """Both modes print plain output on stdout (the old `ask` used stderr)."""
    ui = UI(plain=True)
    ui.show(_answer(profile="ask"))
    captured = capsys.readouterr()
    assert "The bank raised its rate [1]." in captured.out
    assert captured.err == ""


def test_the_window_appears_in_the_header() -> None:
    console = _console()
    ui = UI(console, plain=False, window="past 24h")
    ui.begin("q", RESEARCH, "news")
    ui.close()
    assert "mode: news · past 24h" in console.export_text()


def test_the_footer_wraps_when_the_terminal_is_narrow() -> None:
    answer = _answer(whitelisted=[("a-very-long-domain-name.example.co.ke", "captcha on the page")])
    console = Console(record=True, width=60)
    console.print(footer(answer, plain=True))
    assert "found" in console.export_text()
