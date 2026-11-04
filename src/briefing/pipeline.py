"""Search -> fetch -> extract orchestration (SPEC sections 2 and 3).

The pipeline owns exactly one thing the router does not: running many URLs at
once inside one semaphore, with a per-URL timeout, without letting a single bad
URL or a single bad extraction end the run. It emits `Event`s as it goes so the
UI can render live progress, and returns a `Report` the footer panel is built
from.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
import typing as t

from briefing.fetch.router import (
    ErrorClass,
    FetchResult,
    Method,
)
from briefing.search import SearchResult, detect_mode
from briefing.whitelist import normalize_host

# Default parallelism. SPEC section 2 says "extract every page concurrently
# (default 5 at a time)"; the config carries `firecrawl.local.concurrency`.
_DEFAULT_CONCURRENCY = 5

# A per-URL timeout is SPEC's requirement, so the default is a real bound rather
# than None: a caller who never sets one must not get a run that can hang forever
# with all semaphore slots held by stalled URLs. `0` opts out, explicitly.
_DEFAULT_TIMEOUT_S = 30.0

# Test-visible, so a suite can assert the bounded path without waiting 30s.
_SHORT_TIMEOUT_S = 0.05

EventKind = t.Literal["started", "finished", "done"]


@dataclasses.dataclass(frozen=True)
class Event:
    """One progress notification, emitted as the run proceeds."""

    kind: EventKind
    query: str = ""
    mode: str = ""
    url: str = ""
    method: Method | None = None
    error_class: ErrorClass = ErrorClass.OK
    done: int = 0
    total: int = 0


@dataclasses.dataclass
class Result:
    """One fetched-and-extracted page, for a numbered result card."""

    rank: int
    title: str
    url: str
    domain: str
    snippet: str
    source: str
    age: str
    heading: str
    summary: str
    method: Method
    ok: bool
    error_class: ErrorClass = ErrorClass.OK
    #: Why this page degraded (SPEC section 4). Empty on a clean local success.
    warning: str = ""


@dataclasses.dataclass
class Report:
    """The run's outcome: ordered results plus the counts the footer shows.

    `whitelisted` carries (domain, reason) pairs so the footer can render
    SPEC section 3's `whitelist +1 (example.co.ke: captcha)` caption without
    reaching into the whitelist store itself.
    """

    results: list[Result]
    events: list[Event] = dataclasses.field(default_factory=list)
    found: int = 0
    whitelisted: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    elapsed_s: float = 0.0
    progress: Progress | None = None

    def by_method(self) -> dict[Method, int]:
        """Counts per method badge, in badge order, for the footer panel."""
        counts: dict[Method, int] = {}
        for result in self.results:
            counts[result.method] = counts.get(result.method, 0) + 1
        return counts

    @property
    def succeeded(self) -> int:
        """Pages the run actually got content for — the footer's "N extracted"."""
        return sum(1 for r in self.results if r.ok)


class Progress:
    """The live tally the UI renders. One instance per run, injected in.

    Held outside the pipeline so a caller which is *not* reading the event
    stream (a `--json` run, a test) can still inspect the final tally without
    the pipeline having been given a callback.
    """

    def __init__(self) -> None:
        self.started = False
        self.query = ""
        self.mode = ""
        self.total = 0
        self.finished = 0
        self.events: list[Event] = []

    def record(self, event: Event) -> None:
        self.events.append(event)
        if event.kind == "started":
            self.started = True
            self.query = event.query
            self.mode = event.mode
            self.total = event.total
        elif event.kind == "finished":
            self.finished += 1


class Pipeline:
    """`search -> fetch -> extract`, concurrent under one semaphore."""

    def __init__(
        self,
        *,
        searcher: t.Any,
        router: t.Any,
        extractor: t.Any,
        whitelist: t.Any | None = None,
        concurrency: int = _DEFAULT_CONCURRENCY,
        timeout_s: float | None = _DEFAULT_TIMEOUT_S,
        progress: Progress | None = None,
        summarizer: t.Any | None = None,
    ) -> None:
        self.searcher = searcher
        self.router = router
        self.extractor = extractor
        self.whitelist = whitelist
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.progress = progress if progress is not None else Progress()
        # T4.1's `--llm`. None is the default and means extractive-only; the
        # summarizer runs inside the same semaphore, so `--llm` costs no extra
        # concurrency over the fetch it follows.
        self.summarizer = summarizer

    def _emit(self, event: Event, on_event: t.Callable[[Event], None] | None) -> None:
        self.progress.record(event)
        if on_event is not None:
            on_event(event)

    async def run(self, query: str, *, on_event: t.Callable[[Event], None] | None = None) -> Report:
        """Search, fetch, and extract. One bad URL never ends the run."""
        started = time.monotonic()
        mode = _mode(query)
        try:
            # `Searcher` already normalizes; its hits are `SearchResult`s, not raw
            # engine dicts. Re-normalizing here raised `AttributeError` on the
            # NamedTuple and the broad `except` hid it as an empty search.
            hits: list[SearchResult] = list(await self.searcher.search(query))
        except Exception:  # noqa: BLE001 - a failed search is a valid, empty outcome
            hits = []

        usable: list[SearchResult] = []
        seen_urls: set[str] = set()
        for hit in hits:
            # Two hits can point at one page; fetching it twice would double-count
            # it in the footer, so the first (best-ranked) hit wins.
            if hit.url and hit.url not in seen_urls:
                seen_urls.add(hit.url)
                usable.append(hit)
        self._emit(Event(kind="started", query=query, mode=mode, total=len(usable)), on_event)

        semaphore = asyncio.Semaphore(self.concurrency)
        before = self._whitelisted()
        tasks = [
            asyncio.create_task(self._one(hit, rank, semaphore, on_event, query=query))
            for rank, hit in enumerate(usable, start=1)
        ]
        # gather with return_exceptions: a per-URL failure is already handled, so
        # this only guards against a bug in the orchestration itself.
        settled = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[Result] = []
        for rank, outcome in enumerate(settled, start=1):
            if isinstance(outcome, Result):
                outcome.rank = rank
                results.append(outcome)
            else:
                # The orchestrator itself failed: keep the run alive and report
                # the page as failed rather than dropping it from the footer.
                hit = usable[rank - 1]
                results.append(_failed(hit, rank, ErrorClass.DEAD, f"could not process {hit.url}"))

        self._emit(
            Event(kind="done", query=query, mode=mode, done=len(results), total=len(usable)),
            on_event,
        )
        return Report(
            results=results,
            events=list(self.progress.events),
            found=len(usable),
            whitelisted=self._added(before),
            elapsed_s=time.monotonic() - started,
            progress=self.progress,
        )

    async def _one(
        self,
        hit: SearchResult,
        rank: int,
        semaphore: asyncio.Semaphore,
        on_event: t.Callable[[Event], None] | None,
        query: str = "",
    ) -> Result:
        """Fetch and extract one URL. Never raises."""
        async with semaphore:
            try:
                fetched = await self._fetch(hit)
            except Exception as exc:  # noqa: BLE001 - one bad URL never kills the run
                result = _failed(hit, rank, ErrorClass.DEAD, str(exc))
            else:
                result = _assembled(
                    hit,
                    rank,
                    fetched,
                    await self._summarize(
                        fetched, url=hit.url, search_title=hit.title, query=query
                    ),
                )
            self._emit(
                Event(
                    kind="finished",
                    url=hit.url,
                    method=result.method,
                    error_class=result.error_class,
                    done=rank,
                    total=self.progress.total,
                ),
                on_event,
            )
            return result

    def _whitelisted(self) -> set[str]:
        """Domains in the whitelist before the run, if a store was injected.

        No store means no footer caption, which is a legitimate configuration
        (a read-only status run); it is not an error.
        """
        if self.whitelist is None:
            return set()
        try:
            return {entry.domain for entry in self.whitelist.list()}
        except Exception:  # noqa: BLE001 - an unreadable store must not stop the run
            return set()

    def _added(self, before: set[str]) -> list[tuple[str, str]]:
        """(domain, reason) for every entry this run added, in addition order."""
        if self.whitelist is None:
            return []
        try:
            entries = self.whitelist.list()
        except Exception:  # noqa: BLE001 - ditto
            return []
        return [(entry.domain, entry.reason) for entry in entries if entry.domain not in before]

    async def _fetch(self, hit: SearchResult) -> FetchResult:
        """`router.fetch` under the per-URL timeout, when one is configured.

        `timeout_s` of 0 (or less) means no bound, which has to be asked for:
        the constructor default is the real bound, not "wait forever".
        """
        if not self.timeout_s:
            return await self.router.fetch(hit.url, hit.snippet)
        try:
            return await asyncio.wait_for(self.router.fetch(hit.url, hit.snippet), self.timeout_s)
        except TimeoutError:
            # Not the router's own TRANSIENT class: this is the pipeline's clock,
            # so it is reported as its own failure and keeps the run going.
            return FetchResult(
                method=Method.SNIPPET,
                ok=False,
                error_class=ErrorClass.TRANSIENT_5XX,
                warning=f"Timed out after {self.timeout_s}s.",
            )

    async def _summarize(
        self,
        fetched: FetchResult,
        url: str = "",
        search_title: str = "",
        query: str = "",
    ) -> tuple[str, str]:
        """Heading and summary from the router's markdown. Never raises.

        With a summarizer injected (`--llm`), its answer replaces the extractive
        one and the extractive one is the fallback it degrades to. A page with no
        markdown is left alone: there is nothing to abstract from, and asking a
        model to summarise a page it never saw is how a briefing invents news.
        """
        if not fetched.markdown:
            return fetched.heading, fetched.summary
        try:
            extracted = self.extractor.extract(
                fetched.markdown,
                title=fetched.heading,
                url=url,
                search_title=search_title,
                query=query if query else None,
            )
        except Exception:  # noqa: BLE001 - a bad extraction degrades to snippet
            return fetched.heading, fetched.summary
        heading, summary = _pair(extracted)
        if self.summarizer is None:
            return heading, summary
        try:
            abstractive = await self.summarizer.summary(
                heading=heading, text=fetched.markdown, fallback=summary
            )
        except Exception:  # noqa: BLE001 - a summarizer that fails leaves the extractive one
            return heading, summary
        return heading, abstractive


def _mode(query: str) -> str:
    """Which searcher the query will use, for the header line."""
    return detect_mode(query)


def _pair(extracted: t.Any) -> tuple[str, str]:
    """`(heading, summary)` from whatever the extractor returned.

    Accepts a two-tuple, an object with `.heading`/`.summary`, or a bare string,
    so a stub extractor does not have to build a real `Extracted`.
    """
    if isinstance(extracted, tuple) and len(extracted) == 2:
        return str(extracted[0]), str(extracted[1])
    heading = getattr(extracted, "heading", "")
    summary = getattr(extracted, "summary", "")
    if heading or summary:
        return str(heading), str(summary)
    return "", str(extracted)


def _assembled(
    hit: SearchResult, rank: int, fetched: FetchResult, summary: tuple[str, str]
) -> Result:
    """One successful (or degraded) page, ready for a result card."""
    return Result(
        rank=rank,
        title=hit.title or fetched.heading,
        url=hit.url,
        domain=normalize_host(hit.url),
        snippet=hit.snippet,
        source=hit.source,
        age=hit.age,
        heading=summary[0],
        summary=summary[1],
        method=fetched.method,
        ok=fetched.ok,
        error_class=fetched.error_class,
        warning=fetched.warning,
    )


def _failed(hit: SearchResult, rank: int, error_class: ErrorClass, warning: str) -> Result:
    """One page the run could not produce, kept so the footer stays honest."""
    return Result(
        rank=rank,
        title=hit.title,
        url=hit.url,
        domain=normalize_host(hit.url),
        snippet=hit.snippet,
        source=hit.source,
        age=hit.age,
        heading="",
        summary="",
        method=Method.SNIPPET,
        ok=False,
        error_class=error_class,
        warning=warning,
    )
