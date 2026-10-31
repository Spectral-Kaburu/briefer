"""Tests for whitelist management (T1.4).

Covers host normalization, subdomain suffix matching, add/remove/list/prune, the
verified flag, atomic writes, and concurrent adds.
"""

from __future__ import annotations

import asyncio
import typing as t
from pathlib import Path

import pytest
import yaml

from briefing.whitelist import (
    Whitelist,
    WhitelistEntry,
    host_matches,
    normalize_host,
)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "whitelist.yaml"


@pytest.fixture
def wl(path: Path) -> Whitelist:
    return Whitelist(path)


# --- normalization ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("www.example.com", "example.com"),
        ("EXAMPLE.com", "example.com"),
        ("example.com:8080", "example.com"),
        ("https://example.com/path?q=1", "example.com"),
        ("blog.example.co.ke", "blog.example.co.ke"),
        ("  www.Example.COM  ", "example.com"),
        ("https://www.example.co.ke/story", "example.co.ke"),
    ],
)
def test_normalize_strips_www_case_and_scheme(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


# --- matching --------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "entry", "matches"),
    [
        ("example.com", "example.com", True),
        ("www.example.com", "example.com", True),
        ("blog.example.com", "example.com", True),
        ("deep.sub.example.com", "example.com", True),
        ("example.co.ke", "example.com", False),
        ("notexample.com", "example.com", False),
        ("example.com.evil.net", "example.com", False),
        ("sub.example.com", "com", True),  # a bare TLD entry intentionally matches subdomains
    ],
)
def test_host_matches_whole_domain_including_subdomains(
    host: str, entry: str, matches: bool
) -> None:
    assert host_matches(host, entry) is matches


# --- add / list / remove ---------------------------------------------------


def test_add_creates_entry_with_auto_source(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    entries = wl.list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.domain == "example.com"
    assert entry.reason == "captcha"
    assert entry.source == "auto"
    assert entry.verified is False
    assert entry.api_hits == 0
    assert entry.added_at


def test_add_is_idempotent_and_keeps_reason(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    wl.add("example.com", reason="captcha again")
    assert len(wl.list()) == 1
    assert wl.list()[0].reason == "captcha"


def test_add_manual_source(wl: Whitelist) -> None:
    wl.add("example.com", reason="owner asked", source="manual")
    assert wl.list()[0].source == "manual"


def test_remove_drops_entry(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    wl.remove("example.com")
    assert wl.list() == []


def test_remove_unknown_is_not_an_error(wl: Whitelist) -> None:
    wl.remove("nowhere.example")
    assert wl.list() == []


def test_list_sorted_by_domain(wl: Whitelist) -> None:
    wl.add("zzz.example", reason="r")
    wl.add("aaa.example", reason="r")
    assert [e.domain for e in wl.list()] == ["aaa.example", "zzz.example"]


def test_is_whitelisted_uses_suffix_matching(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    assert wl.is_whitelisted("blog.example.com")
    assert not wl.is_whitelisted("example.co.ke")


def test_is_whitelisted_normalizes_the_query(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    assert wl.is_whitelisted("https://www.example.com/x")


# --- verified flag ---------------------------------------------------------


def test_record_api_success_marks_verified(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    wl.record_api_call("example.com", success=True)
    entry = wl.list()[0]
    assert entry.verified is True
    assert entry.api_hits == 1


def test_record_api_failure_keeps_unverified(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    wl.record_api_call("example.com", success=False)
    entry = wl.list()[0]
    assert entry.verified is False
    assert entry.api_hits == 1


def test_record_api_call_on_unknown_host_is_a_noop(wl: Whitelist) -> None:
    wl.record_api_call("unknown.example", success=True)
    assert wl.list() == []


def test_record_api_call_matches_subdomain(wl: Whitelist) -> None:
    wl.add("example.com", reason="captcha")
    wl.record_api_call("blog.example.com", success=True)
    assert wl.list()[0].verified is True


# --- prune -----------------------------------------------------------------


def test_prune_removes_unverified(wl: Whitelist) -> None:
    wl.add("unverified.example", reason="captcha")
    wl.prune()
    assert wl.list() == []


def test_prune_keeps_verified(wl: Whitelist) -> None:
    wl.add("verified.example", reason="captcha")
    wl.record_api_call("verified.example", success=True)
    wl.prune()
    assert len(wl.list()) == 1


def test_prune_keeps_manual_entries_even_unverified(wl: Whitelist) -> None:
    """Manual entries are the owner's and must survive an automatic prune."""
    wl.add("manual.example", reason="owner asked", source="manual")
    wl.prune()
    assert len(wl.list()) == 1


# --- persistence / atomicity ----------------------------------------------


def test_entry_survives_reload(path: Path) -> None:
    wl = Whitelist(path)
    wl.add("example.com", reason="captcha")
    wl.record_api_call("example.com", success=True)
    reloaded = Whitelist(path).list()
    assert len(reloaded) == 1
    assert reloaded[0].domain == "example.com"
    assert reloaded[0].verified is True


def test_missing_file_yields_empty_list(wl: Whitelist) -> None:
    assert wl.list() == []


def test_no_temp_files_left_behind(wl: Whitelist, path: Path) -> None:
    """Writes must be atomic: temp file + rename, never a partial file."""
    wl.add("example.com", reason="captcha")
    leftovers = [p.name for p in path.parent.iterdir() if p.name != "whitelist.yaml"]
    assert leftovers == []


def test_corrupt_file_falls_back_to_empty(path: Path) -> None:
    path.write_text("not: [valid: yaml", encoding="utf-8")
    assert Whitelist(path).list() == []


def test_entry_model_round_trips() -> None:
    entry = WhitelistEntry(domain="example.com", added_at="2026-10-31T00:00:00", reason="captcha")
    reloaded = WhitelistEntry.model_validate(entry.model_dump())
    assert reloaded.domain == entry.domain
    assert reloaded.verified is False


# --- concurrency -----------------------------------------------------------


def test_concurrent_adds_do_not_lose_entries(path: Path) -> None:
    """SPEC requires whitelist writes to be safe under concurrent fetches."""

    async def run() -> None:
        wl = Whitelist(path)
        await asyncio.gather(
            *(wl.add_async(f"site{i}.example", reason="captcha") for i in range(25))
        )

    asyncio.run(run())
    domains = {e.domain for e in Whitelist(path).list()}
    assert len(domains) == 25


def test_concurrent_adds_all_have_valid_reasons(path: Path) -> None:
    async def run() -> None:
        wl = Whitelist(path)
        await asyncio.gather(
            *(wl.add_async(f"host{i}.example", reason=f"barrier {i}") for i in range(10))
        )

    asyncio.run(run())
    reasons = {e.reason for e in Whitelist(path).list()}
    assert all(r.startswith("barrier ") for r in reasons)


def test_add_async_is_the_same_operation_as_add(wl: Whitelist) -> None:
    async def run() -> None:
        await wl.add_async("example.com", reason="captcha")

    asyncio.run(run())
    assert wl.list()[0].domain == "example.com"


# --- YAML shape ------------------------------------------------------------


def test_written_file_is_valid_yaml_with_expected_shape(path: Path) -> None:
    wl = Whitelist(path)
    wl.add("example.com", reason="captcha")
    data: dict[str, t.Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "entries" in data
    entry = data["entries"][0]
    assert set(entry) >= {"domain", "added_at", "reason", "source", "api_hits", "verified"}
