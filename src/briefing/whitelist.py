"""Domain whitelist for the cloud API path (SPEC section 4).

A domain is whitelisted when the *local* scrape hit a barrier (captcha, 403, 429,
empty render) and the API could get past it. Dead links and errors the API cannot
fix are never whitelisted. Entries are stored as hosts without `www.`, and a host
matches an entry when it equals it or ends with `.` + it.

Writes are atomic (temp file + rename) and serialised through one asyncio lock, so
concurrent fetches cannot lose each other's entries.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_TOP_LEVEL_KEY = "entries"


class WhitelistEntry(BaseModel):
    """One whitelisted host. `verified` means the API succeeded on it once."""

    domain: str
    added_at: str = Field(default_factory=lambda: datetime.now().astimezone().isoformat())
    reason: str = ""
    source: str = "auto"
    api_hits: int = 0
    verified: bool = False


def normalize_host(raw: str) -> str:
    """Host form used for storage and matching: no scheme, `www.`, port, or case.

    "https://WWW.Example.com:8080/a?b=1" -> "example.com"
    """
    value = raw.strip().lower()
    for prefix in ("https://", "http://", "//"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # Strip userinfo ("user@host") and a trailing port, but keep IPv6 literals.
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    if value.startswith("["):  # IPv6 literal
        return value.split("]", 1)[0] + "]"
    if ":" in value:
        value = value.split(":", 1)[0]
    value = value.removeprefix("www.")
    return value


def host_matches(host: str, entry: str) -> bool:
    """True when `host` is `entry` or a subdomain of it (whole-domain rule)."""
    candidate = normalize_host(host)
    target = normalize_host(entry)
    return candidate == target or candidate.endswith("." + target)


class Whitelist:
    """Async whitelist store. One instance per process, one lock, atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    # --- reading (lock-free reads: a save always writes a complete document) ---

    def _load(self) -> list[WhitelistEntry]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            return []
        if not isinstance(data, dict):
            return []
        entries = data.get(_TOP_LEVEL_KEY) or []
        if not isinstance(entries, list):
            return []
        parsed: list[WhitelistEntry] = []
        for item in entries:
            if isinstance(item, dict):
                try:
                    parsed.append(WhitelistEntry.model_validate(item))
                except ValueError:
                    continue
        return parsed

    def list(self) -> list[WhitelistEntry]:
        """Entries sorted by domain, for stable output and tests."""
        return sorted(self._load(), key=lambda e: e.domain)

    def _find(self, entries: list[WhitelistEntry], domain: str) -> WhitelistEntry | None:
        for entry in entries:
            if host_matches(domain, entry.domain):
                return entry
        return None

    def is_whitelisted(self, host: str) -> bool:
        return self._find(self._load(), host) is not None

    # --- writing -------------------------------------------------------------

    def _save(self, entries: list[WhitelistEntry]) -> None:
        """Write atomically: a temp file in the same directory, then rename.

        Rename within one filesystem is atomic, so a reader never sees a partial
        document, and a crash mid-write cannot corrupt the whitelist.
        """
        payload = {
            _TOP_LEVEL_KEY: [
                entry.model_dump() for entry in sorted(entries, key=lambda e: e.domain)
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{datetime.now().astimezone().timestamp():.6f}.tmp"
        )
        try:
            tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _apply_add(
        self, entries: list[WhitelistEntry], domain: str, reason: str, source: str
    ) -> None:
        """Add or refresh one entry in a loaded list. Idempotent."""
        target = normalize_host(domain)
        entry = self._find(entries, target)
        if entry is None:
            entries.append(WhitelistEntry(domain=target, reason=reason, source=source))
            return
        if not entry.reason:
            entry.reason = reason
        # A manual entry stays manual even if the router later touches it.
        if entry.source != "manual":
            entry.source = source

    def add(self, domain: str, reason: str = "", source: str = "auto") -> None:
        """Whitelist a domain. Idempotent: an existing entry keeps its reason."""
        entries = self._load()
        self._apply_add(entries, domain, reason, source)
        self._save(entries)

    async def add_async(self, domain: str, reason: str = "", source: str = "auto") -> None:
        """Whitelist a domain under the lock, so concurrent fetches do not lose it."""
        async with self._lock:
            self.add(domain, reason, source)

    def remove(self, domain: str) -> None:
        entries = [e for e in self._load() if not host_matches(domain, e.domain)]
        self._save(entries)

    def prune(self) -> None:
        """Drop unverified auto entries.

        Manual entries are the owner's and survive. Unverified auto entries are
        exactly the ones the API also could not get past.
        """
        entries = [e for e in self._load() if e.verified or e.source == "manual"]
        self._save(entries)

    def record_api_call(self, host: str, success: bool) -> None:
        """Note an API attempt against this host.

        `success` after the first success is what flips `verified` to True; a
        failure leaves the entry unverified so `prune` will clean it up.
        """
        entries = self._load()
        entry = self._find(entries, host)
        if entry is None:
            return
        entry.api_hits += 1
        if success:
            entry.verified = True
        self._save(entries)
