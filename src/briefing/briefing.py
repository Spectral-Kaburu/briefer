"""Briefing-mode filters: topic blocklist and headline-hash dedup (SPEC section 6).

The blocklist drops crypto/finance-speculation topics that are noise in a morning
briefing. The dedup store keeps a hash of every headline already delivered for 7
days, so a story that ran yesterday is not repeated today. Schedule ids live in
the same file and stop the briefing firing twice inside its window.

Reads are lock-free (a save always writes a complete document); writes take one
asyncio lock so concurrent records cannot lose each other, and every write is
atomic so a crash cannot leave a half-written file.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import typing as t
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

_SCHEDULE_PREFIX = "morning_briefing"
_DEFAULT_TTL_DAYS = 7
_DEFAULT_WINDOW_HOURS = 24

# A word is a run of letters/digits, so "stock" is a word and "stocks" is another.
_WORD = re.compile(r"[a-z0-9]+")


def headline_hash(headline: str) -> str:
    """Stable digest of a headline.

    Whitespace and case are normalised first, so a re-run that re-cases or
    re-wraps the same headline still looks like the story it is. The full digest
    is kept: a truncated one risks a silent collision, which would suppress a
    headline the owner never saw.
    """
    normalised = " ".join(headline.split()).lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def is_blocked_topic(topic: str, blocklist: t.Iterable[str]) -> bool:
    """True when `topic` names a blocked speculation topic.

    Whole words only: "stock" blocks "stocks rally" but not "knowledge economy".
    """
    words = set(_WORD.findall(topic.lower()))
    return any(blocked.lower() in words for blocked in blocklist)


class TopicFilter:
    """Keeps the topics that are not blocked (SPEC section 6)."""

    def __init__(self, blocklist: t.Sequence[str]) -> None:
        self.blocklist = list(blocklist)

    def keep(self, topics: t.Iterable[str]) -> list[str]:
        return [topic for topic in topics if not is_blocked_topic(topic, self.blocklist)]


class SeenHeadline(BaseModel):
    """One remembered headline: its hash and when it was last seen."""

    hash: str
    seen_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class DedupStore:
    """Headline-hash memory with a TTL, plus the 24h schedule gate."""

    def __init__(
        self,
        path: Path,
        *,
        ttl_days: int = _DEFAULT_TTL_DAYS,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
    ) -> None:
        self.path = path
        self.ttl_days = ttl_days
        self.window_hours = window_hours
        self._lock = asyncio.Lock()

    # --- reading -------------------------------------------------------------

    def _load(self) -> tuple[list[SeenHeadline], dict[str, str]]:
        """Read the store. Missing, corrupt, or malformed reads as empty.

        A broken store must never stop the briefing from running, so every
        failure of this kind yields an empty one rather than an exception.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return [], {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], {}
        if not isinstance(data, dict):
            return [], {}
        raw_entries = data.get("entries")
        items = raw_entries if isinstance(raw_entries, list) else []
        entries: list[SeenHeadline] = []
        for item in items:
            if not isinstance(item, dict) or "hash" not in item:
                continue
            try:
                entries.append(SeenHeadline.model_validate(item))
            except ValidationError:
                continue
        fired = data.get("fired")
        return entries, dict(fired) if isinstance(fired, dict) else {}

    def seen(self, headline: str) -> bool:
        """True when this headline was recorded inside the TTL."""
        digest = headline_hash(headline)
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days)
        return any(e.hash == digest and _after(e.seen_at, cutoff) for e in self._load()[0])

    # --- writing -------------------------------------------------------------

    def _save(self, entries: list[SeenHeadline], fired: dict[str, str]) -> None:
        """Write atomically: a temp file in the same directory, then rename.

        `fired` is omitted when empty, so a store that has never scheduled a run
        stays exactly `{"entries": []}`.
        """
        payload: dict[str, t.Any] = {
            "entries": [
                {"hash": e.hash, "seen_at": e.seen_at}
                for e in sorted(entries, key=lambda e: e.hash)
            ]
        }
        if fired:
            payload["fired"] = dict(sorted(fired.items()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{datetime.now(UTC).timestamp():.6f}.tmp"
        )
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def record(self, headline: str) -> None:
        """Remember a headline now.

        Idempotent, and a re-seen headline is refreshed rather than duplicated,
        so a story that keeps running stays alive instead of ageing out.
        """
        entries, fired = self._load()
        digest = headline_hash(headline)
        now = datetime.now(UTC).isoformat()
        for entry in entries:
            if entry.hash == digest:
                entry.seen_at = now
                break
        else:
            entries.append(SeenHeadline(hash=digest, seen_at=now))
        self._save(entries, fired)

    async def record_async(self, headline: str) -> None:
        """`record` under the lock: concurrent records would lose each other."""
        async with self._lock:
            self.record(headline)

    def prune(self) -> None:
        """Drop entries past the TTL so the file cannot grow without bound."""
        entries, fired = self._load()
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days)
        self._save([e for e in entries if _after(e.seen_at, cutoff)], fired)

    # --- the 24h schedule gate ----------------------------------------------

    def should_fire(
        self, schedule: str, *, window_hours: int | None = None, now: datetime | None = None
    ) -> bool:
        """False when `schedule` already fired inside `window_hours`."""
        fired = self._load()[1].get(schedule)
        if not fired:
            return True
        cutoff = _aware(now) - timedelta(
            hours=self.window_hours if window_hours is None else window_hours
        )
        return not _after(fired, cutoff)

    def schedule_id(self, when: datetime) -> str:
        """One id per briefing run, so a refire inside the window is recognisable."""
        return f"{_SCHEDULE_PREFIX}_{when.date().isoformat()}"

    def mark_fired(self, schedule: str, now: datetime | None = None) -> None:
        """Note that `schedule` fired, so `should_fire` can gate a refire."""
        entries, fired = self._load()
        fired[schedule] = _aware(now).isoformat()
        self._save(entries, fired)


class FileAudioState:
    """Reads Aether's recorded audio state from a small JSON file.

    This is the agreed IPC surface: Aether writes `{"state": "idle"}` when it
    finishes speaking and the next `briefing run` may proceed. See
    `docs/AETHER_INTEGRATION.md` (T6.4) for the full contract.

    A missing or unreadable file means "no state recorded", which is *not*
    evidence that Aether is mid-sentence; see `Gate.refusal` for why that is
    treated as safe rather than blocking.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    async def speaking(self) -> bool:
        """True only when the file records a positive speaking state."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        return str(data.get("state", "")).strip().lower() == "speaking"


class Gate:
    """Decides whether a briefing may fire: 24h schedule + audio state.

    Holds no state of its own beyond the store and the audio provider it was
    built with, so two gates over one store always agree. Both collaborators are
    injected, which is what keeps T5.3/T5.4 tests off the real audio state.
    """

    def __init__(self, store: DedupStore, audio: t.Any) -> None:
        self.store = store
        self.audio = audio

    async def refusal(self, *, now: datetime, schedule: str) -> str | None:
        """Why the briefing may not fire, or None when it may.

        The schedule gate is checked first, then the audio gate. A provider that
        *raises* is treated as not speaking (SPEC section 6 gates on a recorded
        state, and we never recorded ours), so an unreachable Aether cannot wedge
        the briefing silent forever — recorded as D7.
        """
        if not self.store.should_fire(schedule, now=now):
            return "already fired today"
        try:
            if await self.audio.speaking():
                return "speaking"
        except Exception:  # noqa: BLE001 - an unreachable provider must not block
            return None
        return None


def _aware(moment: datetime | None) -> datetime:
    """UTC now when no moment is given, and UTC when a naive one is."""
    if moment is None:
        return datetime.now(UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _after(stamp: str, cutoff: datetime) -> bool:
    """True when `stamp` is at or after `cutoff`.

    An unparseable stamp counts as expired, so a corrupt entry stops suppressing
    headlines rather than silently hiding them.
    """
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed >= cutoff
