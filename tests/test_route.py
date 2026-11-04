"""Tests for knowledge/route.py - source selection for ask mode."""

from briefing.knowledge.route import choose_sources


class TestChooseSources:
    """Tests for choose_sources() with at least 25 test cases."""

    # Wikipedia-selected tests (encyclopedic cues)
    def test_what_is_python_returns_wiki(self) -> None:
        assert choose_sources("What is Python?") == ["WIKI"]

    def test_who_is_albert_einstein_returns_wiki(self) -> None:
        assert choose_sources("Who is Albert Einstein?") == ["WIKI"]

    def test_history_of_python_returns_wiki(self) -> None:
        assert choose_sources("History of Python programming language") == ["WIKI"]

    def test_define_python_returns_wiki(self) -> None:
        assert choose_sources("Define Python programming language") == ["WIKI"]

    def test_when_was_python_created_returns_wiki(self) -> None:
        assert choose_sources("When was Python created?") == ["WIKI"]

    def test_meaning_of_love_returns_wiki(self) -> None:
        assert choose_sources("What is the meaning of love?") == ["WIKI"]

    def test_capital_of_france_returns_wiki(self) -> None:
        assert choose_sources("Capital of France?") == ["WIKI"]

    def test_what_are_the_capitals_returns_wiki(self) -> None:
        assert choose_sources("What are the capitals of European countries?") == ["WIKI"]

    # Stack Overflow-selected tests (developer cues)
    def test_python_error_returns_so(self) -> None:
        assert choose_sources("I'm getting a Python error") == ["SO"]

    def test_traceback_returns_so(self) -> None:
        assert choose_sources("Here's my Python Traceback") == ["SO"]

    def test_exception_returns_so(self) -> None:
        assert choose_sources("Why does this exception occur?") == ["SO"]

    def test_how_do_i_in_python_returns_so(self) -> None:
        assert choose_sources("How do I sort a list in Python?") == ["SO"]

    def test_how_to_parse_json_returns_so(self) -> None:
        assert choose_sources("How to parse JSON in JavaScript?") == ["SO"]

    def test_why_does_it_fail_returns_so(self) -> None:
        assert choose_sources("Why does my code fail with IndexError?") == ["SO"]

    def test_java_exception_returns_so(self) -> None:
        assert choose_sources("Java exception how to fix") == ["SO"]

    def test_go_language_returns_so(self) -> None:
        assert choose_sources("How to use Go channels?") == ["SO"]

    def test_javascript_how_to_returns_so(self) -> None:
        assert choose_sources("How to write async JavaScript?") == ["SO"]

    def test_cxx_namespace_returns_so(self) -> None:
        assert choose_sources("What does ::mean in C++?") == ["SO"]

    # Tests for multiple matches or edge cases
    def test_both_encyclopedic_and_developer_returns_both(self) -> None:
        """Question matching both patterns selects both (WIKI first, then SO)."""
        # "what is" is encyclopedic, "in Python" is developer
        result = choose_sources("What is Python and how to use it in Python?")
        assert result == ["WIKI", "SO"]

    def test_typo_query_with_what_is_still_wiki(self) -> None:
        """Typo like 'verstion' with 'what is' still matches encyclopedic."""
        assert choose_sources("What is the current python verstion?") == ["WIKI"]

    # News-mode filtering tests
    def test_latest_python_version_no_match(self) -> None:
        """'latest' triggers news mode, should select nothing."""
        assert choose_sources("What is the latest Python version?") == []

    def test_breaking_news_no_match(self) -> None:
        """'breaking' triggers news mode."""
        assert choose_sources("Breaking: latest news in technology") == []

    def test_today_news_no_match(self) -> None:
        """'today' triggers news mode."""
        assert choose_sources("What happened today in tech?") == []

    # Tests that should return nothing
    def test_simple_factual_no_match(self) -> None:
        """Simple factual without cues selects nothing."""
        assert choose_sources("Python") == []

    def test_random_words_no_match(self) -> None:
        """Random words without patterns selects nothing."""
        assert choose_sources("foo bar baz qux") == []

    def test_empty_string_returns_empty(self) -> None:
        """Empty question returns empty."""
        assert choose_sources("") == []

    def test_whitespace_only_returns_empty(self) -> None:
        """Whitespace-only question returns empty."""
        assert choose_sources("   ") == []

    # Case insensitivity tests
    def test_case_insensitive_encyclopedic(self) -> None:
        assert choose_sources("WHAT IS PYTHON?") == ["WIKI"]
        assert choose_sources("HOW DO I PYTHON?") == ["SO"]

    def test_history_of_java_and_python_returns_wiki(self) -> None:
        """History questions with multiple entities still select Wikipedia."""
        assert choose_sources("History of Java and Python") == ["WIKI"]
