"""Client for the cloud Firecrawl API.

This client is the expensive path: it is only reached when the local scrape hit a
barrier (SPEC section 4). It therefore guards its own cost with a daily cap,
records every call to a JSONL log, and refuses to touch the network without a key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import typing as t
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from .errors import Classification, ErrorClass, Origin, classify

logger = logging.getLogger(__name__)

_SCRAPE_PATH = "/v2/scrape"

_CONFIG_HINT = "Set the key named by firecrawl.api.key_env (default FIRECRAWL_API_KEY) and retry."


class MissingApiKeyError(RuntimeError):
    """Raised when a fetch needs the API key and the env var is not set.

    Carries a plain-English message plus a fix hint, per CLAUDE.md.
    """


class ApiCall(BaseModel):
    """One row of the JSONL call log. Deliberately keyless."""

    ts: str
    url: str
    domain: str
    reason: str
    outcome: str


class ApiScrapeResult(t.NamedTuple):
    ok: bool
    markdown: str = ""
    title: str = ""
    error_class: ErrorClass = ErrorClass.OK
    reason: str = ""


class ApiFirecrawl:
    """Async client for api.firecrawl.dev, capped and logged.

    `api_key` is passed in by the caller rather than read from the environment
    here, so the caller decides *when* the key is read (call time only, per
    SPEC). `daily_limit` counts calls this client instance has made, which is
    correct for one run of one process.
    """

    def __init__(
        self,
        base_url: str = "https://api.firecrawl.dev",
        api_key: str | None = None,
        daily_limit: int = 20,
        timeout_s: float = 30.0,
        log_path: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.daily_limit = daily_limit
        self.timeout_s = timeout_s
        self.log_path = log_path
        self._client = client
        self._owns_client = client is None
        self._calls_made = 0
        self._disabled = False
        # The cap and the refusal flag are read and written from concurrent
        # fetches, so both need a lock — same asymmetry fix as whitelist.py.
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def can_use(self) -> bool:
        """False once a key is missing, the cap is hit, or the API has refused."""
        return bool(self.api_key) and not self._disabled and self._calls_made < self.daily_limit

    async def _reserve(self) -> str | None:
        """Atomically take one call from the daily budget.

        Returns the blocking outcome when the call cannot be made, or None when a
        slot was reserved. Without this the cap is advisory: concurrent fetches
        each read `_calls_made`, all see room, and all call.
        """
        async with self._lock:
            if self._disabled:
                return "disabled"
            if self._calls_made >= self.daily_limit:
                return "capped"
            self._calls_made += 1
            return None

    def _refund(self) -> None:
        """Give a reserved slot back when the call never actually billed.

        A transport error and a barrier mean nothing was spent. An API_SIDE
        refusal is not refunded: it disables the client for the run anyway, and
        keeping the count truthful keeps `can_use()` consistent.
        """
        if self._calls_made > 0:
            self._calls_made -= 1

    def _require_key(self) -> str:
        if not self.api_key:
            raise MissingApiKeyError(
                "The Firecrawl API key is not set. " + _CONFIG_HINT,
            )
        return self.api_key

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    def http_headers(self) -> dict[str, str]:
        """Auth headers, built at call time so a rotated key is picked up."""
        return {"Authorization": f"Bearer {self._require_key()}"}

    async def scrape(self, url: str, reason: str) -> ApiScrapeResult:
        """Scrape one URL through the cloud API.

        `reason` is why the local scrape could not do it (the barrier that was
        hit); it is recorded in the log.
        """
        domain = urlparse(url).hostname or "unknown"

        # The missing key needs no budget check; the cap and a refusal still log.
        outcome = "no-key" if not self.api_key else await self._reserve()
        if outcome is not None:
            self._log(url, domain, reason, outcome)
            if outcome == "no-key":
                self._warn_no_key(url, reason)
            elif outcome == "disabled":
                self._warn_disabled(url, reason)
            else:
                self._warn_capped(url, reason)
            return ApiScrapeResult(
                ok=False, error_class=ErrorClass.API_SIDE, reason=self._degraded_reason(outcome)
            )
        client = self._http()
        try:
            response = await client.post(
                f"{self.base_url}{_SCRAPE_PATH}",
                headers=self.http_headers(),
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            )
        except (httpx.TimeoutException, httpx.ConnectError, OSError) as exc:
            classified = classify(exc=exc, origin=Origin.API)
            self._log(url, domain, reason, "transport-error")
            # The traceback must reach the log file, so it is attached here in the
            # handler that caught it (LOG014 forbids `exc_info=True` in a helper).
            logger.warning(
                "Cloud API: %s (reason: %s) failed in transit: %s: %s",
                url,
                reason,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            self._refund()  # nothing was billed
            return ApiScrapeResult(
                ok=False, error_class=classified.error_class, reason=classified.reason
            )

        status = response.status_code
        code = _error_code(response)
        classified = classify(
            status=status,
            code=code,
            body=response.text,
            content=_markdown(response),
            origin=Origin.API,
        )
        # The classifier sees origin=API, so it returns API_SIDE for 402/429/401.
        if classified.error_class not in _OK_CLASSES:
            self._log(url, domain, reason, classified.error_class.value)
            self._warn_classified(url, reason, classified, status, code)
            if classified.error_class is ErrorClass.API_SIDE:
                # Stop spending money: a refused API is refused for the whole run.
                self._disabled = True
            elif classified.error_class is ErrorClass.BARRIER:
                self._refund()  # the API reached the page but got nowhere
            return ApiScrapeResult(
                ok=False, error_class=classified.error_class, reason=classified.reason
            )

        self._log(url, domain, reason, "success")
        return ApiScrapeResult(
            ok=True,
            markdown=_markdown(response),
            title=_title(response),
            error_class=ErrorClass.OK,
            reason="extracted via cloud API",
        )

    def _degraded_reason(self, outcome: str) -> str:
        if outcome == "no-key":
            return "the API key is not set, so only the search snippet is available"
        if outcome == "disabled":
            return "the cloud API refused an earlier request, so only the snippet is available"
        return f"the daily API limit of {self.daily_limit} is reached, so only the snippet is available"

    def _warn_no_key(self, url: str, reason: str) -> None:
        """The keyless degradation: name the env var the caller should set."""
        logger.warning(
            "Cloud API: no key configured, so %s (reason: %s) falls back to the search "
            "snippet only. %s",
            url,
            reason,
            _CONFIG_HINT,
        )

    def _warn_capped(self, url: str, reason: str) -> None:
        """The cap was hit; say how many calls the run is allowed in total."""
        logger.warning(
            "Cloud API: the daily API limit of %d is reached, so %s (reason: %s) falls "
            "back to the search snippet only. Raise firecrawl.api.daily_limit to allow more.",
            self.daily_limit,
            url,
            reason,
        )

    def _warn_disabled(self, url: str, reason: str) -> None:
        """An earlier refusal stopped the API for the rest of the run."""
        logger.warning(
            "Cloud API: the API refused an earlier request and is not called again this "
            "run, so %s (reason: %s) falls back to the search snippet only.",
            url,
            reason,
        )

    def _warn_classified(
        self,
        url: str,
        reason: str,
        classified: Classification,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        """A failure with a status/answer: name the HTTP status and the Firecrawl code."""
        where = f"HTTP {status}" if status is not None else "the API"
        detail = f"{where}{f' ({code})' if code else ''}"
        logger.warning(
            "Cloud API: %s (reason: %s) failed with %s: %s",
            url,
            reason,
            detail,
            classified.reason,
        )

    def _log(self, url: str, domain: str, reason: str, outcome: str) -> None:
        """Append one JSON line. Never contains the key; never contains body text."""
        if self.log_path is None:
            return
        record = ApiCall(
            ts=datetime.now().astimezone().isoformat(timespec="seconds"),
            url=url,
            domain=domain,
            reason=reason,
            outcome=outcome,
        )
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")
        except OSError:
            # A failed log write must never fail the fetch that triggered it.
            return


_OK_CLASSES = frozenset({ErrorClass.OK})


def usage_today(log_path: Path | None, now: datetime | None = None) -> int:
    """Calls logged since local midnight, for `briefing status`.

    Reads the JSONL this client writes, so the count survives a process restart
    while `ApiFirecrawl._calls_made` (the in-run cap counter) does not. A line
    that cannot be parsed is skipped rather than fatal: `status` is a diagnostic
    and must still print its other rows.
    """
    if log_path is None:
        return 0
    try:
        raw = log_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    moment = (now or datetime.now()).astimezone()
    today = moment.date()
    count = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        stamp = record.get("ts") if isinstance(record, dict) else None
        if not isinstance(stamp, str):
            continue
        try:
            if datetime.fromisoformat(stamp).astimezone().date() == today:
                count += 1
        except ValueError:
            continue
    return count


def _markdown(response: httpx.Response) -> str:
    data = _data(response)
    value = data.get("markdown")
    return value if isinstance(value, str) else ""


def _title(response: httpx.Response) -> str:
    meta = _data(response).get("metadata")
    if isinstance(meta, dict):
        value = meta.get("title")
        if isinstance(value, str):
            return value
    return ""


def _data(response: httpx.Response) -> dict[str, t.Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code") or payload.get("error")
    return code if isinstance(code, str) else None
