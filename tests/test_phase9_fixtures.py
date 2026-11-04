"""Synthetic fixture tests for Phase 9 defects (D1-D6, D8, D9).

Each test is marked with @pytest.mark.xfail(strict=True, reason="T9.x")
to ensure make check stays green until the fix lands.

These tests use synthetic page text (never third-party content) to reproduce
the patterns observed during the first real run.

Note: D1-D3 were already fixed in T8.7, so their tests are NOT marked xfail.
They serve to verify the existing fixes work correctly.

D4 was also fixed by T8.7's `_strip_leading_furniture` which peels prefixes
repeatedly until the line starts with prose. The test below uses the original
defect pattern from the owner's report.
"""

from __future__ import annotations

import pytest

from briefing.extract import extract

# =============================================================================
# D1: Setext underlines and rules leak into summaries (FIXED in T8.7)
# =============================================================================


def test_d1_setext_underline_not_in_summary() -> None:
    """Setext heading underlines (=====) should not appear in the summary."""
    md = """Istanbul Marathon Record
====

The runner crossed the line first in a record time.
The event attracted thousands of spectators along the route.
"""
    result = extract(md, title=None, sentences=2)
    assert "====" not in result.summary
    assert "Istanbul Marathon" not in result.summary  # heading not body
    assert "crossed the line" in result.summary


def test_d1_horizontal_rule_not_in_summary() -> None:
    """Markdown horizontal rules should not appear in the summary."""
    md = """# Article Title

Introduction sentence about the topic.

---

The main content of the article starts here. It contains the key information."""
    result = extract(md, title=None, sentences=2)
    assert "---" not in result.summary
    assert "Introduction sentence" in result.summary


# =============================================================================
# D2: Markdown images and link wrappers leak (FIXED in T8.7, except D2.1)
# =============================================================================


def test_d2_plain_link_in_summary() -> None:
    """Plain markdown link text (not the URL) is acceptable, but URL should not."""
    md = """# Article

The project [Python](https://python.org) announced new features.
The changes will be released in Python 3.12 later this year."""
    result = extract(md, title=None, sentences=2)
    # Link text is fine, but URL should be stripped
    assert "https://python.org" not in result.summary
    assert "Python" in result.summary  # text is OK


def test_d2_image_markdown_not_in_summary() -> None:
    """Image markdown (![alt](url)) should not appear in summary."""
    md = """# Project News

![Python logo](https://example.com/python-logo.png "Python Logo")

The Python 3.12 release is now available with improved performance.
Download it from the official website today."""
    result = extract(md, title=None, sentences=2)
    assert "!" not in result.summary
    assert "[" not in result.summary
    assert "logo" not in result.summary
    assert "Python 3.12 release" in result.summary


def test_d2_wrapped_image_not_in_summary() -> None:
    """Link-wrapped images ([![alt](url)](url)) should not appear.

    This is D2.1 - fixed in T9.1.
    """
    md = """# Article

[![Screenshot](https://example.com/screenshot.png)](https://example.com/article)

The release notes detail all the improvements made this cycle.
Developers are excited about the new features."""
    result = extract(md, title=None, sentences=2)
    assert "[" not in result.summary
    assert "!" not in result.summary
    assert "Screenshot" not in result.summary


# =============================================================================
# D3: Page chrome leaks (navigation, cookie banners, etc.) (FIXED in T8.7)
# =============================================================================


def test_d3_skip_to_content_not_in_summary() -> None:
    """A11y 'skip to content' links should not leak."""
    md = """[Skip to main content](https://example.com)

The main article content starts here with important information.
This is the second sentence of the article."""
    result = extract(md, title=None, sentences=2)
    assert "skip to" not in result.summary.lower()


def test_d3_cookie_banner_not_in_summary() -> None:
    """Cookie consent banners should not leak into summary."""
    md = """Accept all cookies
Accept cookies
Cookie settings
We use cookies to improve your experience

The actual article content begins here with important news.
The story continues with details about the announcement."""
    result = extract(md, title=None, sentences=2)
    assert "cookie" not in result.summary.lower()
    assert "actual article content" in result.summary


def test_d3_navigation_bar_not_in_summary() -> None:
    """Navigation bars should not leak into summary."""
    md = """Home | World | Business | Sport | Opinion

The government announced new policies today affecting the tech sector.
These changes will have wide-reaching implications for developers."""
    result = extract(md, title=None, sentences=2)
    assert "|" not in result.summary


# =============================================================================
# D4: Concatenated navigation tokens with no spaces (FIXED in T9.1)
# =============================================================================

# The original defect from T8.7 was about "Return to Homepage Top Stories:
# ------------" - a prefix that ends with ";)" or runs like "» dev (3.16)3.15.23.14…"
# T8.7's _strip_leading_furniture now peels prefixes repeatedly.
# T9.1's clean_markdown drops lines that are mostly non-letters (D4 fixed).


def test_d4_concatenated_navigation_from_T87() -> None:
    """D4 pattern from T8.7: chrome prefix ending in ')' or special chars.

    The pattern "» dev (3.16)3.15.23.14.13.12.11.10" should be stripped entirely
    as navigation furniture, not leak into the summary.
    """
    md = """» dev (3.16)3.15.23.14.13.12.11.10

The new version brings significant performance improvements.
Users can upgrade when they're ready for the changes."""
    result = extract(md, title=None, sentences=2)
    # The entire navigation run should be dropped as furniture
    assert "»" not in result.summary
    assert "dev (3.16)" not in result.summary
    assert "3.15" not in result.summary
    assert "performance improvements" in result.summary


# =============================================================================
# D5: Flattened tables repeat near-identical segments (FIXED in T9.1)
# =============================================================================


def test_d5_flattened_table_not_in_summary() -> None:
    """Repeated table rows should not leak into summary."""
    md = """# Release Notes

Version: 3.11 Status: Stable Life: 5 years
Version: 3.11 Status: Stable Life: 5 years
Version: 3.11 Status: Stable Life: 5 years

The latest release includes many improvements.
Users can download it from the official site."""
    result = extract(md, title=None, sentences=2)
    # Should not have "Version: 3.11 Status: Stable" repeated
    assert "Version: 3.11 Status: Stable Life: 5" not in result.summary
    assert "improvements" in result.summary.lower() or "latest release" in result.summary.lower()


# =============================================================================
# D6: Summary with no sentence terminators is not length-capped (FIXED in T9.1)
# =============================================================================


def test_d6_long_run_no_terminators_is_capped() -> None:
    """A long line without sentence terminators must be capped (320 chars default)."""
    md = """# Article

This is a very long paragraph that contains no sentence terminators at all and just
goes on and on without stopping and keeps going and going and going and going and
going and going and going and going and going and going and going and going and going
and going and going and going and going and going and going.

The actual content has proper sentences with periods."""
    result = extract(md, title=None, sentences=2)
    # Summary should be capped (by default 320 chars)
    assert len(result.summary) <= 320, f"Summary too long: {len(result.summary)} chars"


# =============================================================================
# D7: Heading unrelated to page (to be fixed in T9.2)
# =============================================================================


@pytest.mark.xfail(strict=True, reason="T9.2: heading may be unrelated to page")
def test_d7_heading_unrelated_sanity_rule() -> None:
    """Heading should be relevant to the page content, not just any h1.

    This test documents the defect where a changelog line like
    'Fixed: type checker now handles recursive generics' appears as the
    heading for a 'Python 3.11 release announcement' page.

    The fix (T9.2) will implement a sanity rule: a heading must have
    word overlap with the URL, meta description, or search title.
    """
    # Page about Python 3.11 release, but first h1 is changelog entry
    md = """# Fixed: type checker now handles recursive generics

Python 3.11 has been released with significant performance improvements.
The most notable new feature is the 10-15% speed boost across all workloads."""

    result = extract(md, title=None, sentences=2)
    # The heading should NOT be "Fixed: type checker..." because it's unrelated
    # After T9.2, either heading is empty or mentions Python 3.11/release
    assert result.heading != "Fixed: type checker now handles recursive generics"
    # The answer about Python 3.11 should be in the summary
    assert "Python 3.11" in result.summary or "release" in result.summary.lower()


# =============================================================================
# D8: Cards do not answer the question (buried answer) - T9.3 will fix with query-aware selection
# =============================================================================

# Note: The defect "answer is buried" is when the ANSWER is in a later paragraph,
# not in the first one. The current extractive approach takes the first substantive
# paragraph, which may not contain the answer.


@pytest.mark.xfail(strict=True, reason="T9.3: answer is buried in first paragraph, not selected")
def test_d8_answer_in_later_paragraph() -> None:
    """Key answer sentence should appear in summary, not be buried in later paragraph.

    The current extractive approach takes the first substantive paragraph,
    but the actual answer may be later in the article.
    T9.3 will add query-aware passage scoring with BM25-style ranking.
    """
    md = """# Python Language History

Python was created by Guido van Rossum and first released in 1991.
It is one of the world's most popular programming languages used worldwide.

However, the most important question is not who created Python but what
version is currently stable and recommended for new projects.

Python 3.12, released in October 2023, brings significant performance
improvements with speedups of up to 25% compared to previous versions.
This is the current stable release that most developers now use.

The latest features include better error messages and faster startup times.
Check python.org for download links and the full release notes."""

    result = extract(md, title="Python 3.12 Release", sentences=2)
    # The answer is in paragraph 3-4: "Python 3.12... current stable release"
    # But extract() has no query awareness - it takes the first paragraph
    # T9.3 will pass the query and select the paragraph with the version answer
    # For now, the summary is about creation, NOT about the current version
    # This test expects "3.12" to be in the summary for a version query
    assert "3.12" in result.summary


# =============================================================================
# D9: Same host fills multiple slots, query typo defeats term matching
# =============================================================================

# D9 defect: Query "verstion" (typo) should still match "version" in content
# This requires T9.3's fuzzy matching for 5+ letter tokens


@pytest.mark.xfail(strict=True, reason="T9.3: query matching needs fuzzy logic")
def test_d9_typo_query_finds_relevant_content() -> None:
    """Fuzzy matching should work for typo-ridden queries like 'verstion' -> 'version'.

    T9.3 will add fuzzy matching for query tokens with 5+ letters.
    This test will use the relevance module's passage scoring.
    """
    # Placeholder for when T9.3 implements the relevance module
    # The test will verify that:
    # 1. Query token "verstion" finds content with "version"
    # 2. BM25-style scoring ranks relevant passages higher
    # 3. Fuzzy matching handles common typos
    #
    # Currently: this test needs to be written once T9.3's relevance module exists
    # For now, just document that it's a todo
    assert False, "T9.3 not implemented: need relevance module with fuzzy matching"


# =============================================================================
# Additional tests from T8.7 fixes documentation
# =============================================================================


def test_d3_consent_banner_peeled_to_nothing() -> None:
    """A page whose body is only a consent banner must yield no summary."""
    md = (
        "Opens in a new window Opens an external website Opens an external website "
        "in a new window Close this consent banner Manage Preferences Storage "
        "Preferences Skip to main content Skip to main content You are the owner "
        "of this collection. Edit Collection Close You have permission to edit "
        "this collection."
    )
    assert extract(md).summary == ""


def test_link_scaffolding_leaves_no_double_space() -> None:
    """Removing a link or emphasis can leave " , " where the comma was."""
    md = "The [safety commission](https://example.com) , the agency said."
    assert " ," not in extract(md).summary
    assert "commission, the agency said." in extract(md).summary


def test_a_bare_rule_line_is_dropped() -> None:
    """A markdown horizontal rule is furniture, not an empty sentence."""
    md = "------------\n\nThe commission appointed a new chairperson today."
    assert extract(md).summary == "The commission appointed a new chairperson today."


def test_a_linked_a11y_element_is_still_stripped() -> None:
    """A11y 'skip to' elements arrive as links, so clean before peeling."""
    md = "[Skip to main content](https://example.com) The commission met on Tuesday."
    assert extract(md).summary == "The commission met on Tuesday."
