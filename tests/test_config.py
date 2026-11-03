"""Tests for config loading (T0.4).

Table-driven per CLAUDE.md: one row per documented behaviour.
"""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

import pytest

from briefing.config import (
    ConfigError,
    default_config_path,
    discover_config,
    load_config,
    resolve_api_key,
)

# --- defaults ---------------------------------------------------------------


def test_defaults_match_spec_sketch() -> None:
    cfg = load_config()
    assert cfg.news_topics == ["technology", "world news"]
    assert cfg.search.results == 10
    assert cfg.search.auto_news_detect is True
    assert cfg.firecrawl.local.base_url == "http://localhost:3002"
    assert cfg.firecrawl.local.concurrency == 5
    assert cfg.firecrawl.api.daily_limit == 20
    assert cfg.firecrawl.api.key_env == "FIRECRAWL_API_KEY"
    assert cfg.whitelist.auto_add is True
    assert cfg.whitelist.prefer_api_for_whitelisted is False
    assert cfg.summary.mode == "extractive"
    assert cfg.summary.sentences == 2
    assert cfg.blocklist_topics == ["crypto", "bitcoin", "trading", "forex", "stocks"]
    assert cfg.dedup.headline_ttl_days == 7
    assert cfg.dedup.schedule_window_hours == 24
    assert cfg.timeouts.per_url_s == 30.0
    assert cfg.timeouts.transient_backoff_s == 0.5
    assert cfg.firecrawl.api.timeout_s == 30.0


# --- overrides --------------------------------------------------------------


@pytest.mark.parametrize(
    ("yaml_text", "attr", "expected"),
    [
        ("news_topics: [science]", "news_topics", ["science"]),
        ("search: {results: 3}", "search.results", 3),
        ("search: {region: ke-en}", "search.region", "ke-en"),
        ("firecrawl:\n  local:\n    concurrency: 2", "firecrawl.local.concurrency", 2),
        ("summary: {mode: llm}", "summary.mode", "llm"),
        ("dedup: {headline_ttl_days: 1}", "dedup.headline_ttl_days", 1),
        ("whitelist: {auto_add: false}", "whitelist.auto_add", False),
        ("timeouts: {per_url_s: 5}", "timeouts.per_url_s", 5),
        ("firecrawl:\n  api:\n    timeout_s: 12", "firecrawl.api.timeout_s", 12),
        # 0 opts out of the per-URL bound, so it must survive loading: a falsy
        # value read as "unset" would silently re-impose the default.
        ("timeouts: {per_url_s: 0}", "timeouts.per_url_s", 0),
        ("timeouts: {transient_backoff_s: 2}", "timeouts.transient_backoff_s", 2),
    ],
)
def test_yaml_overrides_apply(tmp_path: t.Any, yaml_text: str, attr: str, expected: object) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    cfg = load_config(path)
    obj: object = cfg
    for part in attr.split("."):
        obj = getattr(obj, part)
    assert obj == expected


def test_partial_yaml_keeps_other_defaults(tmp_path: t.Any) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("news_topics: [ai]\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.news_topics == ["ai"]
    assert cfg.search.results == 10  # untouched default


# --- env-var key lookup -----------------------------------------------------


def test_api_key_read_from_named_env_var() -> None:
    cfg = load_config()
    assert resolve_api_key(cfg, {"FIRECRAWL_API_KEY": "fc-123"}) == "fc-123"


def test_missing_api_key_returns_none() -> None:
    """Absent key is not an error at load time; it is raised when a fetch needs it."""
    assert resolve_api_key(load_config(), {}) is None


def test_api_key_respects_custom_key_env(tmp_path: t.Any) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("firecrawl:\n  api:\n    key_env: MY_FC_KEY\n", encoding="utf-8")
    assert resolve_api_key(load_config(path), {"MY_FC_KEY": "zz"}) == "zz"


# --- paths ------------------------------------------------------------------


def test_paths_overridable_for_tests(tmp_path: t.Any) -> None:
    cfg = load_config(state_dir=tmp_path)
    assert cfg.paths.state_dir == tmp_path
    assert cfg.paths.whitelist == tmp_path / "whitelist.yaml"
    assert cfg.paths.seen_headlines == tmp_path / "seen_headlines.json"


def test_the_greeting_name_defaults_to_empty() -> None:
    """T8.5: nobody is addressed until the owner says who.

    The old default was "Aether", which made a degraded briefing greet itself
    (the T5.4 incident): the assistant's name is who speaks, not who listens.
    """
    assert load_config().briefing.user_name == ""


def test_the_greeting_name_is_overridable(tmp_path: t.Any) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("briefing:\n  user_name: Sam\n", encoding="utf-8")
    assert load_config(path).briefing.user_name == "Sam"


def test_the_assistant_name_keeps_its_own_default() -> None:
    """The prompt still names the speaker, so the field keeps its default."""
    assert load_config().briefing.name == "Aether"


# --- bad input --------------------------------------------------------------


def test_unknown_field_raises_config_error(tmp_path: t.Any) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("nonsense_field: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_wrong_type_raises_config_error(tmp_path: t.Any) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("search:\n  results: not-a-number\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_malformed_yaml_raises_config_error(tmp_path: t.Any) -> None:
    path = tmp_path / "briefing.yaml"
    path.write_text("news_topics: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_no_file_loads_pure_defaults() -> None:
    """`load_config(None)` stays free of file reads, so discovery has to live
    somewhere else (T8.3)."""
    assert load_config(None).search.results == 10


def test_an_explicit_missing_path_is_an_error(tmp_path: t.Any) -> None:
    """T8.3: `--config ~/briefing.yaml` with a typo must not silently become
    defaults — the owner would run on the wrong config and never know."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "nope.yaml")
    message = excinfo.value.message
    assert "does not exist" in message
    assert "nope.yaml" in message
    # The fix hint must say what to do, not just what went wrong (SPEC section 3).
    assert excinfo.value.hint


# --- discovery (T8.3) ------------------------------------------------------


def test_the_env_var_names_the_config_file(tmp_path: t.Any) -> None:
    """`$BRIEFING_CONFIG` outranks the platformdirs location."""
    named = tmp_path / "named.yaml"
    named.write_text("search:\n  results: 11\n", encoding="utf-8")
    monkey = {"BRIEFING_CONFIG": str(named)}
    assert discover_config(monkey) == named


def test_the_env_var_outranks_a_default_that_is_also_present(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """Both files on disk: `$BRIEFING_CONFIG` wins and the loser is never read.

    The two tests either side of this one have only a single file present, so both
    pass no matter which order `discover_config` tries the candidates in — proven
    by sabotaging it to try the default first, which they did not catch. This one
    puts both on disk and asserts the *values* differ, so the winner is proven.
    """
    named = tmp_path / "named.yaml"
    named.write_text("search:\n  results: 11\n", encoding="utf-8")
    default = tmp_path / "briefing" / "config.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("search:\n  results: 12\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("BRIEFING_CONFIG", str(named))
    chosen = discover_config(os.environ)
    assert chosen == named
    assert load_config(chosen).search.results == 11


def test_the_user_config_dir_is_the_fallback(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """The default location, with `XDG_CONFIG_HOME` pointing the test at tmp."""
    default = tmp_path / "briefing" / "config.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("search:\n  results: 12\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)
    assert discover_config(os.environ) == default


def test_no_file_found_is_none_not_an_error(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """A fresh install has no config file, and that is not an error."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("BRIEFING_CONFIG", raising=False)
    assert discover_config(os.environ) is None


def test_no_owner_config_is_visible_to_a_test() -> None:
    """Canary for conftest's `_no_owner_config`.

    Asserted on the mechanism rather than on the absence of the owner's file,
    because this machine has no `~/.config/briefing/config.yaml` and so a bare
    "is None" would pass even with the fixture deleted. Between this fixture
    moving `XDG_CONFIG_HOME` under `tmp_path` and
    `test_the_user_config_dir_is_the_fallback` proving discovery follows XDG,
    discovery cannot reach the owner's directory.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    assert xdg, "conftest must redirect XDG_CONFIG_HOME for every test"
    resolved = default_config_path()
    assert str(resolved).startswith(xdg), f"{resolved} is outside the redirected {xdg}"
    assert discover_config(os.environ) is None


def test_a_stale_env_var_falls_through_to_the_default_path(
    monkeypatch: t.Any, tmp_path: t.Any
) -> None:
    """A `$BRIEFING_CONFIG` that no longer exists must not brick every command:
    it falls through to the platformdirs path, exactly like a missing one."""
    default = tmp_path / "briefing" / "config.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("search:\n  results: 13\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("BRIEFING_CONFIG", str(tmp_path / "gone.yaml"))
    assert discover_config(os.environ) == default


def test_an_empty_env_var_is_not_a_path(monkeypatch: t.Any, tmp_path: t.Any) -> None:
    """`BRIEFING_CONFIG=""` is unset, not a reference to ""."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("BRIEFING_CONFIG", "")
    assert discover_config(os.environ) is None


def test_shipped_example_config_loads() -> None:
    """The example file a user is told to copy must validate against the schema."""
    example = Path(__file__).resolve().parent.parent / "config" / "briefing.yaml.example"
    cfg = load_config(example, state_dir=Path("/tmp/briefing-test-state"))
    # The topics are the owner's own sample list, so only assert the shape they
    # arrive in: re-listing them here broke on every edit to the example.
    assert isinstance(cfg.news_topics, list)
    assert cfg.news_topics
    assert all(isinstance(topic, str) for topic in cfg.news_topics)
    assert cfg.firecrawl.local.base_url == "http://localhost:3002"
    assert cfg.firecrawl.api.key_env == "FIRECRAWL_API_KEY"
