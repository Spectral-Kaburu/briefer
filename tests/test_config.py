"""Tests for config loading (T0.4).

Table-driven per CLAUDE.md: one row per documented behaviour.
"""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

from briefing.config import ConfigError, load_config, resolve_api_key

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


def test_missing_file_falls_back_to_defaults() -> None:
    """A named-but-absent config file is not fatal; defaults still load."""
    cfg = load_config(None)
    assert cfg.search.results == 10


def test_shipped_example_config_loads() -> None:
    """The example file a user is told to copy must validate against the schema."""
    example = Path(__file__).resolve().parent.parent / "config" / "briefing.yaml.example"
    cfg = load_config(example, state_dir=Path("/tmp/briefing-test-state"))
    assert cfg.news_topics == ["technology", "world news"]
    assert cfg.firecrawl.local.base_url == "http://localhost:3002"
    assert cfg.firecrawl.api.key_env == "FIRECRAWL_API_KEY"
