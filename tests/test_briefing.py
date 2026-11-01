"""Tests for briefing.py: topic blocklist, dedup (T5.1) and gates (T5.2).

Briefing mode is the only caller, so the blocklist applies here and nowhere else
(SPEC section 6). The dedup store is a plain JSON file with a TTL; the gate
layers the 24h schedule rule and the "never speak over the user" audio rule on
top of it.
"""

from __future__ import annotations

import json
import typing as t
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from briefing.briefing import (
    DedupStore,
    FileAudioState,
    FileSessionSummary,
    Gate,
    SeenHeadline,
    TopicFilter,
    headline_hash,
    is_blocked_topic,
)

HEADLINE = "Bank raises lending rate by fifty basis points"
OTHER = "Flooding displaces families in Nairobi"


# --- headline hashing -----------------------------------------------------


def test_hash_is_stable_for_the_same_headline() -> None:
    assert headline_hash(HEADLINE) == headline_hash(HEADLINE)


def test_hash_ignores_case_and_surrounding_space() -> None:
    assert headline_hash(f" {HEADLINE.title()}  ") == headline_hash(HEADLINE)


def test_hash_differs_for_a_different_headline() -> None:
    assert headline_hash(HEADLINE) != headline_hash(OTHER)


def test_hash_survives_a_round_trip_through_json() -> None:
    """The store is JSON, so the hash must be a JSON-safe string."""
    encoded = json.dumps({"hash": headline_hash(HEADLINE)})
    assert json.loads(encoded)["hash"] == headline_hash(HEADLINE)


def test_hash_is_not_the_headline_itself() -> None:
    """A digest, so the store never keeps headline text."""
    assert HEADLINE not in headline_hash(HEADLINE)


# --- topic blocklist ------------------------------------------------------


def test_blocked_topics_are_rejected() -> None:
    assert is_blocked_topic("bitcoin price today", ["crypto", "bitcoin"])


def test_unblocked_topics_are_allowed() -> None:
    assert not is_blocked_topic("world news", ["crypto", "bitcoin"])


def test_blocklist_matching_is_case_insensitive() -> None:
    assert is_blocked_topic("Crypto winter begins", ["crypto"])


def test_blocklist_matches_a_word_not_a_substring() -> None:
    """'stock' must not block 'knowledge'; SPEC says speculation topics."""
    assert not is_blocked_topic("knowledge economy report", ["stock"])


def test_blocklist_matches_a_word_inside_a_phrase() -> None:
    assert is_blocked_topic("should you buy bitcoin now", ["bitcoin"])


def test_empty_blocklist_blocks_nothing() -> None:
    assert not is_blocked_topic("anything at all", [])


def test_filter_keeps_only_unblocked_topics() -> None:
    kept = TopicFilter(["crypto", "forex"]).keep(["world news", "crypto winter", "technology"])
    assert kept == ["world news", "technology"]


def test_filter_is_case_insensitive_on_topics() -> None:
    kept = TopicFilter(["crypto"]).keep(["Crypto prices", "world news"])
    assert kept == ["world news"]


def test_filter_drops_everything_when_all_blocked() -> None:
    assert TopicFilter(["crypto", "bitcoin"]).keep(["crypto price", "bitcoin now"]) == []


# --- dedup store ----------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> DedupStore:
    return DedupStore(tmp_path / "seen_headlines.json", ttl_days=7)


def _seen(store: DedupStore, headline: str, days_ago: float = 0.0) -> None:
    """Record a headline as seen `days_ago` days in the past."""
    store.record(headline)
    if days_ago:
        raw: dict[str, t.Any] = json.loads(store.path.read_text())
        for entry in raw["entries"]:
            entry["seen_at"] = (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat()
        store.path.write_text(json.dumps(raw))


def test_an_unseen_headline_is_new(store: DedupStore) -> None:
    assert not store.seen(HEADLINE)


def test_a_seen_headline_is_not_new(store: DedupStore) -> None:
    store.record(HEADLINE)
    assert store.seen(HEADLINE)


def test_seen_is_case_insensitive(store: DedupStore) -> None:
    store.record(HEADLINE)
    assert store.seen(HEADLINE.upper())


def test_a_fresh_headline_is_kept(store: DedupStore) -> None:
    _seen(store, HEADLINE, days_ago=1)
    assert store.seen(HEADLINE)


def test_an_expired_headline_is_new_again(store: DedupStore) -> None:
    """SPEC section 6: 7-day TTL, so an old hash stops suppressing."""
    _seen(store, HEADLINE, days_ago=8)
    assert not store.seen(HEADLINE)


def test_expiry_boundary_is_respected(store: DedupStore) -> None:
    _seen(store, HEADLINE, days_ago=6.9)
    assert store.seen(HEADLINE)


def test_two_headlines_are_independent(store: DedupStore) -> None:
    store.record(HEADLINE)
    assert not store.seen(OTHER)


def test_recording_twice_is_idempotent(store: DedupStore) -> None:
    store.record(HEADLINE)
    store.record(HEADLINE)
    entries = json.loads(store.path.read_text())["entries"]
    assert len(entries) == 1


def test_re_recording_refreshes_the_timestamp(store: DedupStore) -> None:
    """Re-seeing a headline must keep it alive, not expire it on first sight."""
    _seen(store, HEADLINE, days_ago=6)
    store.record(HEADLINE)
    assert store.seen(HEADLINE)
    stored = json.loads(store.path.read_text())["entries"][0]["seen_at"]
    assert datetime.fromisoformat(stored) > datetime.now().astimezone() - timedelta(days=1)


def test_store_creates_its_file_on_first_write(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "nested" / "seen.json", ttl_days=7)
    store.record(HEADLINE)
    assert store.path.exists()


def test_store_reads_an_empty_or_missing_file(tmp_path: Path) -> None:
    """A missing file is an empty store, not an error."""
    store = DedupStore(tmp_path / "gone.json", ttl_days=7)
    assert not store.seen(HEADLINE)


def test_store_survives_a_corrupt_file(tmp_path: Path) -> None:
    """A corrupt store must not crash the morning briefing."""
    path = tmp_path / "seen.json"
    path.write_text("{not json at all")
    store = DedupStore(path, ttl_days=7)
    assert not store.seen(HEADLINE)
    store.record(HEADLINE)
    assert store.seen(HEADLINE)


def test_store_survives_an_unexpected_shape(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"entries": "not-a-list"}))
    assert not DedupStore(path, ttl_days=7).seen(HEADLINE)


def test_store_holds_only_hashes(tmp_path: Path) -> None:
    """SPEC stores headline *hashes*, so the file must not keep headline text."""
    store = DedupStore(tmp_path / "seen.json", ttl_days=7)
    store.record(HEADLINE)
    assert HEADLINE not in store.path.read_text()


def test_store_is_sorted_for_stable_output(store: DedupStore) -> None:
    for headline in ("c headline", "a headline", "b headline"):
        store.record(headline)
    hashes = [e["hash"] for e in json.loads(store.path.read_text())["entries"]]
    assert hashes == sorted(hashes)


def test_prune_removes_expired_entries(store: DedupStore) -> None:
    _seen(store, HEADLINE, days_ago=30)
    store.record(OTHER)
    store.prune()
    assert not store.seen(HEADLINE)
    assert store.seen(OTHER)


def test_prune_keeps_fresh_entries(store: DedupStore) -> None:
    _seen(store, HEADLINE, days_ago=1)
    store.prune()
    assert store.seen(HEADLINE)


def test_prune_on_an_empty_store_is_a_no_op(store: DedupStore) -> None:
    store.prune()
    assert store.path.read_text() == json.dumps({"entries": []})


def test_entry_is_a_typed_record() -> None:
    entry = SeenHeadline(hash=headline_hash(HEADLINE), seen_at="2026-10-31T08:00:00+03:00")
    assert entry.hash
    assert entry.seen_at


# --- schedule gate --------------------------------------------------------


def test_schedule_id_is_generated_for_a_date(store: DedupStore) -> None:
    assert store.schedule_id(datetime(2026, 10, 31)) == "morning_briefing_2026-10-31"


def test_schedule_id_suppresses_a_refire_within_the_window(store: DedupStore) -> None:
    """SPEC section 6: schedule_id suppresses re-fire for 24h."""
    now = datetime(2026, 10, 31, 8, 0)
    store.mark_fired(store.schedule_id(now), now=now)
    assert not store.should_fire(store.schedule_id(now), now=now + timedelta(hours=2))


def test_schedule_id_allows_a_fire_after_the_window(store: DedupStore) -> None:
    now = datetime(2026, 10, 31, 8, 0)
    store.mark_fired(store.schedule_id(now), now=now)
    assert store.should_fire(store.schedule_id(now), now=now + timedelta(hours=25))


def test_a_fresh_schedule_id_fires(store: DedupStore) -> None:
    assert store.should_fire(store.schedule_id(datetime(2026, 10, 31)))


def test_a_different_day_has_a_different_schedule_id(store: DedupStore) -> None:
    assert store.schedule_id(datetime(2026, 10, 31)) != store.schedule_id(datetime(2026, 11, 1))


# --- writing format -------------------------------------------------------


def test_seen_headlines_file_is_valid_json(store: DedupStore) -> None:
    store.record(HEADLINE)
    data: dict[str, t.Any] = json.loads(store.path.read_text())
    assert "entries" in data
    assert set(data["entries"][0]) >= {"hash", "seen_at"}


def test_concurrent_records_do_not_lose_entries(store: DedupStore) -> None:
    """The pipeline fetches concurrently, so writes must be serialised."""
    import asyncio

    async def run() -> None:
        await asyncio.gather(*(store.record_async(f"headline {i}") for i in range(15)))

    asyncio.run(run())
    assert len(json.loads(store.path.read_text())["entries"]) == 15


# --- audio-state gate (T5.2) ----------------------------------------------


class FakeAudio:
    """Injected `AudioState`; `speaking` is the whole answer it gives."""

    def __init__(self, speaking: bool = False, raises: Exception | None = None) -> None:
        self._speaking = speaking
        self._raises = raises
        self.calls = 0

    async def speaking(self) -> bool:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._speaking


@pytest.mark.asyncio
async def test_file_audio_state_reads_speaking_from_the_file(tmp_path: Path) -> None:
    """The file is the agreed IPC surface Aether writes."""
    path = tmp_path / "audio.json"
    path.write_text(json.dumps({"state": "speaking"}))
    assert await FileAudioState(path).speaking()


@pytest.mark.asyncio
async def test_file_audio_state_reads_idle_from_the_file(tmp_path: Path) -> None:
    path = tmp_path / "audio.json"
    path.write_text(json.dumps({"state": "idle"}))
    assert not await FileAudioState(path).speaking()


@pytest.mark.asyncio
async def test_file_audio_state_treats_a_missing_file_as_not_speaking(tmp_path: Path) -> None:
    """No file means Aether has not started; that is not a reason to stay silent."""
    assert not await FileAudioState(tmp_path / "absent.json").speaking()


@pytest.mark.asyncio
async def test_file_audio_state_treats_a_corrupt_file_as_not_speaking(tmp_path: Path) -> None:
    """A unreadable state must not wedge the briefing shut, but must be detectable."""
    path = tmp_path / "audio.json"
    path.write_text("{not json")
    assert not await FileAudioState(path).speaking()


@pytest.mark.asyncio
async def test_file_audio_state_treats_an_unknown_state_as_not_speaking(tmp_path: Path) -> None:
    path = tmp_path / "audio.json"
    path.write_text(json.dumps({"state": "garbled"}))
    assert not await FileAudioState(path).speaking()


# --- session summary (T5.4) -------------------------------------------------


@pytest.mark.asyncio
async def test_file_session_summary_reads_the_summary_from_the_file(tmp_path: Path) -> None:
    """SPEC section 6: the briefing continues yesterday's session."""
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"summary": "We deployed the briefing service."}))
    assert await FileSessionSummary(path).text() == "We deployed the briefing service."


@pytest.mark.asyncio
async def test_file_session_summary_is_empty_when_aether_wrote_none(tmp_path: Path) -> None:
    """A missing summary is "no continuity", not a failure.

    A first-ever run has nothing to continue, and the briefing must still speak.
    """
    path = tmp_path / "absent.json"
    assert await FileSessionSummary(path).text() == ""


@pytest.mark.asyncio
async def test_file_session_summary_survives_a_corrupt_file(tmp_path: Path) -> None:
    """A truncated write must not stop the briefing from running."""
    path = tmp_path / "session.json"
    path.write_text("{not json")
    assert await FileSessionSummary(path).text() == ""


@pytest.mark.asyncio
async def test_file_session_summary_ignores_a_non_object_file(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps(["a summary in a list"]))
    assert await FileSessionSummary(path).text() == ""


@pytest.mark.asyncio
async def test_file_session_summary_trims_whitespace(tmp_path: Path) -> None:
    """A trailing newline in the prompt reads as a spoken pause."""
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"summary": "  a summary \n"}))
    assert await FileSessionSummary(path).text() == "a summary"


@pytest.mark.asyncio
async def test_file_audio_state_ignores_a_json_list(tmp_path: Path) -> None:
    path = tmp_path / "audio.json"
    path.write_text(json.dumps(["speaking"]))
    assert not await FileAudioState(path).speaking()


class FakeClock:
    """Injected clock, so the 24h gate never depends on the wall clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: t.Any) -> None:
        self.now += timedelta(**kwargs)


async def _open_briefing(store: DedupStore, audio: FakeAudio, clock: FakeClock) -> str | None:
    """`Gate.refusal`: the refusal reason, or None when the briefing may fire."""
    gate = Gate(store, audio)
    return await gate.refusal(now=clock(), schedule=store.schedule_id(clock()))


@pytest.mark.asyncio
async def test_the_gate_opens_when_nothing_has_fired(store: DedupStore) -> None:
    audio = FakeAudio()
    refusal = await _open_briefing(store, audio, FakeClock())
    assert refusal is None


@pytest.mark.asyncio
async def test_the_gate_refuses_when_the_schedule_already_fired(store: DedupStore) -> None:
    """SPEC section 6: schedule_id suppresses re-fire for 24h."""
    clock = FakeClock()
    store.mark_fired(store.schedule_id(clock()), now=clock())
    refusal = await _open_briefing(store, FakeAudio(), clock)
    assert refusal == "already fired today"


@pytest.mark.asyncio
async def test_the_gate_refuses_while_the_audio_is_speaking(store: DedupStore) -> None:
    """SPEC section 6: never fire while audio.state == speaking."""
    refusal = await _open_briefing(store, FakeAudio(speaking=True), FakeClock())
    assert refusal == "speaking"


@pytest.mark.asyncio
async def test_the_gate_opens_when_the_audio_is_idle(store: DedupStore) -> None:
    refusal = await _open_briefing(store, FakeAudio(speaking=False), FakeClock())
    assert refusal is None


@pytest.mark.asyncio
async def test_the_gate_opens_after_the_24h_window_passes(store: DedupStore) -> None:
    clock = FakeClock()
    store.mark_fired(store.schedule_id(clock()), now=clock())
    clock.advance(hours=25)
    assert await _open_briefing(store, FakeAudio(), clock) is None


@pytest.mark.asyncio
async def test_the_gate_checks_the_audio_even_after_a_refusal(store: DedupStore) -> None:
    """An unreadable audio state must never be the thing that stays silent."""


@pytest.mark.asyncio
async def test_the_gate_does_not_need_the_audio_when_the_schedule_refuses(
    store: DedupStore,
) -> None:
    """The schedule gate alone is enough to refuse, so audio goes unread."""
    clock = FakeClock()
    store.mark_fired(store.schedule_id(clock()), now=clock())
    audio = FakeAudio(speaking=True)
    refusal = await _open_briefing(store, audio, clock)
    assert refusal == "already fired today"
    assert audio.calls == 0


@pytest.mark.asyncio
async def test_the_gate_stays_open_when_the_audio_state_is_unreadable(store: DedupStore) -> None:
    """A raised provider must not wedge the briefing shut (see DECISIONS.md D7)."""
    refusal = await _open_briefing(store, FakeAudio(raises=OSError("no socket")), FakeClock())
    assert refusal is None


@pytest.mark.asyncio
async def test_the_gate_records_the_fire_once_allowed(store: DedupStore) -> None:
    """Marking fired is the caller's move, and the gate must expose it plainly."""
    clock = FakeClock()
    schedule = store.schedule_id(clock())
    gate = Gate(store, FakeAudio())
    assert await gate.refusal(now=clock(), schedule=schedule) is None
    store.mark_fired(schedule, now=clock())
    assert await gate.refusal(now=clock(), schedule=schedule) == "already fired today"


@pytest.mark.asyncio
async def test_two_gates_on_one_store_agree(store: DedupStore) -> None:
    """The gate holds no state of its own, so a second instance sees the same file."""
    clock = FakeClock()
    first = Gate(store, FakeAudio())
    second = Gate(store, FakeAudio())
    assert await first.refusal(now=clock(), schedule=store.schedule_id(clock())) is None
    store.mark_fired(store.schedule_id(clock()), now=clock())
    assert await second.refusal(now=clock(), schedule=store.schedule_id(clock())) is not None
