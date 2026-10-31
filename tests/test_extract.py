"""Tests for heading + extractive summary extraction (T1.5).

Fixtures per the acceptance: a news article, a nav-heavy page, an empty page, and
a page with no h1.
"""

from __future__ import annotations

import pytest

from briefing.extract import Extracted, extract

NEWS_MD = """# Kenya's election body names new boss

The Independent Electoral and Boundaries Commission has appointed a new
chairperson after a six-month search, ending a leadership vacuum that had
delayed preparations for the next general election.

Officials said the appointment takes effect immediately. The new chairperson
pledged to restore public confidence ahead of the polls.

Commissioners held a brief ceremony at the commission's headquarters.
"""

NAV_HEAVY_MD = """Home | World | Business | Sport | Opinion | Lifestyle

Subscribe to our newsletter
Accept all cookies

# Transport fares to rise 15% next month

The Kenya Transport Authority has approved a 15 percent increase in fares across
all major routes, citing rising fuel costs and maintenance expenses. Commuters
will pay more from the first of next month.

Operators had lobbied for a larger increase. The regulator settled on 15 percent
after a series of consultations with both operators and consumer groups.

The decision takes effect nationwide.
"""

EMPTY_MD = ""

NO_H1_MD = """The government has announced a new policy on data protection.

The directive requires all agencies to conduct audits before sharing records.
Officials expect compliance within twelve months.
"""

MULTI_PARA_MD = """Some opening paragraph that is short.

This paragraph is much longer and contains the actual substance of the article,
which is exactly the kind of sentence the extractive summary should keep.

A closing note.
"""

# --- news article ----------------------------------------------------------


def test_news_article_gets_heading_from_title() -> None:
    result = extract(NEWS_MD, title=None)
    assert result.heading == "Kenya's election body names new boss"


def test_news_article_summary_is_extractive_not_first_line_only() -> None:
    """The summary should be the substance, not the headline restated."""
    result = extract(NEWS_MD, title=None)
    assert "appointed a new chairperson" in result.summary


def test_metadata_title_wins_over_h1() -> None:
    result = extract(NEWS_MD, title="Metadata title wins")
    assert result.heading == "Metadata title wins"


def test_summary_trims_to_configured_sentences() -> None:
    result = extract(NEWS_MD, title=None, sentences=1)
    assert ":" not in result.summary  # 1 sentence holds no clause separator
    assert len(result.summary) < 200


def test_summary_of_two_sentences_by_default() -> None:
    result = extract(NEWS_MD, title=None)
    assert result.summary.count(".") >= 2


# --- nav-heavy page --------------------------------------------------------


def test_nav_lines_are_stripped_from_summary() -> None:
    result = extract(NAV_HEAVY_MD, title=None)
    assert "newsletter" not in result.summary.lower()
    assert "Accept all cookies" not in result.summary


def test_nav_heavy_still_finds_heading() -> None:
    result = extract(NAV_HEAVY_MD, title=None)
    assert result.heading == "Transport fares to rise 15% next month"


def test_nav_heavy_summary_keeps_substance() -> None:
    result = extract(NAV_HEAVY_MD, title=None)
    assert "15 percent increase" in result.summary


# --- empty page ------------------------------------------------------------


def test_empty_page_yields_no_heading_and_empty_summary() -> None:
    result = extract(EMPTY_MD, title=None)
    assert result.heading == ""
    assert result.summary == ""


def test_whitespace_only_page_is_empty() -> None:
    result = extract("   \n\n  \t \n", title=None)
    assert result.summary == ""


def test_title_survives_an_empty_body() -> None:
    result = extract(EMPTY_MD, title="Only a title")
    assert result.heading == "Only a title"


# --- no h1 -----------------------------------------------------------------


def test_no_h1_uses_metadata_title() -> None:
    result = extract(NO_H1_MD, title="Government directive")
    assert result.heading == "Government directive"


def test_no_h1_and_no_title_yields_empty_heading() -> None:
    result = extract(NO_H1_MD, title=None)
    assert result.heading == ""


def test_no_h1_summary_still_extracted() -> None:
    result = extract(NO_H1_MD, title=None)
    assert "data protection" in result.summary


# --- sentence selection ----------------------------------------------------


def test_short_opening_paragraph_is_skipped_for_substance() -> None:
    """A one-line opener should not consume the summary budget."""
    result = extract(MULTI_PARA_MD, title=None, sentences=1)
    assert "actual substance of the article" in result.summary
    assert "short" not in result.summary


def test_summary_ends_on_a_sentence_boundary() -> None:
    for text in (NEWS_MD, NAV_HEAVY_MD, MULTI_PARA_MD):
        summary = extract(text, title=None).summary
        assert summary == "" or summary[-1] in ".!?", summary


def test_all_sentences_requested_keeps_everything() -> None:
    result = extract(NEWS_MD, title=None, sentences=99)
    assert "pledged to restore public confidence" in result.summary


# --- boilerplate removal ---------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Home | World | Business",
        "Accept all cookies",
        "Sign in to continue",
        "Subscribe to our newsletter",
        "Advertisement",
        "Share this article",
        "Skip to content",
        "[ ] Toggle navigation",
    ],
)
def test_each_boilerplate_line_is_dropped(line: str) -> None:
    md = line + "\n\nThis is the real sentence of the story, and it must survive."
    summary = extract(md, title=None, sentences=1).summary
    assert line.split("|")[0].strip().lower() not in summary.lower()
    assert "real sentence of the story" in summary


def test_links_and_emphasis_markers_stripped() -> None:
    md = "**Bold** text with [a link](https://example.com) inside the prose."
    summary = extract(md, title=None, sentences=1).summary
    assert "**" not in summary
    assert "](" not in summary
    assert "Bold" in summary


# --- result type -----------------------------------------------------------


def test_extracted_is_a_plain_result() -> None:
    result = extract(NEWS_MD, title=None)
    assert isinstance(result, Extracted)
    assert result.heading
    assert result.summary
