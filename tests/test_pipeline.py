"""Tests for pipeline.py (T3.1): search -> fetch -> extract orchestration.

Everything the pipeline touches is injected: the searcher, the router, and the
extractor are all fakes, so a mixed-outcome run is reproducible without a network
and without a local Firecrawl.
"""

from __future__ import annotations

import asyncio
import json
import typing as t
from pathlib import Path

import httpx
import respx

from briefing.fetch.api import ApiFirecrawl
from briefing.fetch.local import LocalFirecrawl
from briefing.fetch.router import ErrorClass, FetchResult, Method, Router
from briefing.pipeline import (
    _DEFAULT_TIMEOUT_S,
    _SHORT_TIMEOUT_S,
    Event,
    Pipeline,
    Progress,
    Report,
)
from briefing.whitelist import Whitelist

# One fake search hit: the fields `Searcher` normalizes.
HIT: dict[str, t.Any] = {"title": "Bank raises rate", "url": "https://a.example/x", "body": "s"}

# The real router's BARRIER test: a page that renders but carries no substance.
_BARRIER_MARKDOWN = "   \n\n  \n"
# A page with a real h1 and prose, so the extractor has something to summarize.
_API_MARKDOWN = (
    "# Bank raises rate\n\n"
    "The central bank raised its lending rate by fifty basis points.  \n"
    "It cited persistent inflation pressure across the economy."
)


class FakeSearcher:
    """Stands in for `Searcher`, recording the query it was given."""

    def __init__(
        self,
        *,
        hits: list[dict[str, t.Any]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._hits = hits if hits is not None else []
        self._raises = raises
        self.calls: list[str] = []

    async def search(self, query: str) -> list[dict[str, t.Any]]:
        self.calls.append(query)
        if self._raises is not None:
            raise self._raises
        return list(self._hits)


class FakeRouter:
    """Stands in for `Router`. `answers` maps URL -> FetchResult."""

    def __init__(self, answers: dict[str, FetchResult] | None = None) -> None:
        self._answers = answers or {}
        self.fetched: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def fetch(self, url: str, snippet: str = "") -> FetchResult:
        self.fetched.append(url)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        # A real await point, so the semaphore actually has something to arbitrate.
        await asyncio.sleep(0)
        self.in_flight -= 1
        return self._answers.get(url, FetchResult(method=Method.SNIPPET, ok=True))


class FakeExtractor:
    """Stands in for the extraction step."""

    def __init__(self, raises: Exception | None = None) -> None:
        self._raises = raises
        self.seen: list[str] = []

    def extract(self, markdown: str, url: str = "") -> object:
        self.seen.append(markdown)
        if self._raises is not None:
            raise self._raises
        return "SUMMARY"


def _result(
    url: str,
    *,
    ok: bool = True,
    method: Method = Method.LOCAL,
    markdown: str = "body",
) -> FetchResult:
    # The router's own convention: anything that degraded to the DDG snippet is
    # not ok, even though the run continues.
    return FetchResult(method=method, ok=ok, markdown=markdown)


# --- the happy path -------------------------------------------------------


def test_the_pipeline_runs_search_fetch_and_extract() -> None:
    router = FakeRouter({"https://a.example/x": _result("https://a.example/x")})
    pipeline = Pipeline(searcher=FakeSearcher(hits=[HIT]), router=router, extractor=FakeExtractor())
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.succeeded == 1
    assert router.fetched == ["https://a.example/x"]


def test_the_searcher_receives_the_query() -> None:
    searcher = FakeSearcher(hits=[HIT])
    pipeline = Pipeline(
        searcher=searcher,
        router=FakeRouter({"https://a.example/x": _result("https://a.example/x")}),
        extractor=FakeExtractor(),
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert searcher.calls == ["latest flooding"]


def test_a_successful_result_carries_heading_and_summary() -> None:
    router = FakeRouter({"https://a.example/x": _result("https://a.example/x")})
    pipeline = Pipeline(searcher=FakeSearcher(hits=[HIT]), router=router, extractor=FakeExtractor())
    [result] = asyncio.run(pipeline.run("latest flooding")).results
    assert result.summary == "SUMMARY"
    assert result.method == Method.LOCAL


def test_snippets_are_carried_through_to_the_router() -> None:
    """SPEC section 3: a failed fetch degrades to the DDG snippet."""
    router = FakeRouter()  # no answer for the URL: a plain SNIPPET result
    pipeline = Pipeline(searcher=FakeSearcher(hits=[HIT]), router=router, extractor=FakeExtractor())
    [result] = asyncio.run(pipeline.run("latest flooding")).results
    assert result.method == Method.SNIPPET
    assert result.ok


def test_every_fetched_url_is_tried() -> None:
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(4)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(), extractor=FakeExtractor()
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert len(report.results) == 4


# --- concurrency ----------------------------------------------------------


def test_concurrency_is_capped_by_the_semaphore() -> None:
    """SPEC section 2: ~5 at a time."""
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(12)]
    router = FakeRouter()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=router,
        extractor=FakeExtractor(),
        concurrency=3,
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert router.peak_in_flight <= 3


def test_concurrency_actually_overlaps() -> None:
    """A cap of 1 would also satisfy the bound above; the point is parallelism."""
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(6)]
    router = FakeRouter()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=router,
        extractor=FakeExtractor(),
        concurrency=3,
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert router.peak_in_flight > 1


def test_all_urls_are_fetched_even_when_the_cap_is_small() -> None:
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(10)]
    router = FakeRouter()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=router,
        extractor=FakeExtractor(),
        concurrency=2,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert len(router.fetched) == 10
    assert len(report.results) == 10


# --- one bad URL never kills the run -------------------------------------


def test_a_bad_router_call_does_not_stop_the_run() -> None:
    """SPEC: one bad URL never kills the run."""

    class Boom(FakeRouter):
        async def fetch(self, url: str, snippet: str = "") -> FetchResult:
            if url == "https://a.example/1":
                raise RuntimeError("boom")
            return await super().fetch(url, snippet)

    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(3)]
    pipeline = Pipeline(searcher=FakeSearcher(hits=hits), router=Boom(), extractor=FakeExtractor())
    report = asyncio.run(pipeline.run("latest flooding"))
    assert len(report.results) == 3
    assert [r.error_class for r in report.results if r.url == "https://a.example/1"] == [
        ErrorClass.DEAD
    ]


def test_a_bad_extract_does_not_stop_the_run() -> None:
    hits = [{**HIT, "url": "https://a.example/0"}, {**HIT, "url": "https://a.example/1"}]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=FakeRouter(
            {
                "https://a.example/0": _result("https://a.example/0"),
                "https://a.example/1": _result("https://a.example/1"),
            }
        ),
        extractor=FakeExtractor(raises=ValueError("bad markdown")),
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert len(report.results) == 2
    assert all(r.summary == "" for r in report.results)


def test_a_failing_search_yields_no_results_and_no_crash() -> None:
    pipeline = Pipeline(
        searcher=FakeSearcher(raises=RuntimeError("ddgs down")),
        router=FakeRouter(),
        extractor=FakeExtractor(),
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.results == []
    assert report.found == 0


def test_a_hit_with_no_url_is_dropped() -> None:
    """A hit with no URL cannot be fetched, and must not crash the run."""
    hits = [{"title": "no url here"}]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(), extractor=FakeExtractor()
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.results == []


def test_duplicate_urls_are_fetched_once() -> None:
    """Two hits pointing at one page should not double-count in the footer."""
    hits = [HIT, HIT]
    router = FakeRouter()
    pipeline = Pipeline(searcher=FakeSearcher(hits=hits), router=router, extractor=FakeExtractor())
    asyncio.run(pipeline.run("latest flooding"))
    assert router.fetched == ["https://a.example/x"]


# --- progress events ------------------------------------------------------


def test_a_started_event_is_emitted_first() -> None:
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    events = list(asyncio.run(pipeline.run("latest flooding")).events)
    assert events[0].kind == "started"


def test_every_url_emits_a_finished_event() -> None:
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(3)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(), extractor=FakeExtractor()
    )
    events = list(asyncio.run(pipeline.run("latest flooding")).events)
    done = [e for e in events if e.kind == "finished"]
    assert len(done) == 3


def test_a_done_event_is_emitted_last() -> None:
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    events = list(asyncio.run(pipeline.run("latest flooding")).events)
    assert events[-1].kind == "done"


def test_progress_is_reportable_to_a_callback() -> None:
    """SPEC section 3: the pipeline emits events the UI consumes."""
    seen: list[str] = []

    def on_event(event: Event) -> None:
        seen.append(event.kind)

    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    asyncio.run(pipeline.run("latest flooding", on_event=on_event))
    assert seen[0] == "started"
    assert "finished" in seen
    assert seen[-1] == "done"


def test_events_are_replayable_after_the_run() -> None:
    """The report carries its events, so a non-live caller can render them."""
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert list(report.events)
    assert all(isinstance(e, Event) for e in report.events)


def test_a_started_event_carries_the_query_and_mode() -> None:
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    events = asyncio.run(pipeline.run("latest flooding")).events
    assert events[0].query == "latest flooding"
    assert events[0].mode == "news"


def test_a_finished_event_names_the_url_and_the_method() -> None:
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    done = [e for e in asyncio.run(pipeline.run("latest flooding")).events if e.kind == "finished"]
    assert done[0].url == "https://a.example/x"
    assert done[0].method == Method.SNIPPET


def test_a_failed_url_still_reports_a_finished_event() -> None:
    """A per-URL failure must reach the UI, not vanish into the event stream."""

    class Boom(FakeRouter):
        async def fetch(self, url: str, snippet: str = "") -> FetchResult:
            raise RuntimeError("boom")

    pipeline = Pipeline(searcher=FakeSearcher(hits=[HIT]), router=Boom(), extractor=FakeExtractor())
    done = [e for e in asyncio.run(pipeline.run("latest flooding")).events if e.kind == "finished"]
    assert len(done) == 1
    assert done[0].error_class == ErrorClass.DEAD


# --- report shape (the footer panel, SPEC section 3) ---------------------


def test_the_report_counts_what_it_found() -> None:
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(5)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(), extractor=FakeExtractor()
    )
    assert asyncio.run(pipeline.run("latest flooding")).found == 5


def test_the_report_counts_by_method() -> None:
    answers = {
        "https://a.example/0": _result("https://a.example/0", method=Method.LOCAL),
        "https://a.example/1": _result("https://a.example/1", method=Method.API),
        "https://a.example/2": _result("https://a.example/2", method=Method.SNIPPET),
    }
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(3)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(answers), extractor=FakeExtractor()
    )
    counts = asyncio.run(pipeline.run("latest flooding")).by_method()
    assert counts == {Method.LOCAL: 1, Method.API: 1, Method.SNIPPET: 1}


def test_the_report_counts_snippet_only_results() -> None:
    answers = {
        "https://a.example/0": _result("https://a.example/0", method=Method.SNIPPET, ok=False),
        "https://a.example/1": _result("https://a.example/1", method=Method.SNIPPET, ok=False),
        "https://a.example/2": _result("https://a.example/2", method=Method.LOCAL),
    }
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(3)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(answers), extractor=FakeExtractor()
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.by_method()[Method.SNIPPET] == 2
    assert report.succeeded == 1


def test_the_report_elapsed_seconds_is_non_negative() -> None:
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    assert asyncio.run(pipeline.run("latest flooding")).elapsed_s >= 0


def test_results_keep_search_order() -> None:
    """The result list mirrors the search ranking, for the numbered cards."""
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(3)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits), router=FakeRouter(), extractor=FakeExtractor()
    )
    assert [r.url for r in asyncio.run(pipeline.run("latest flooding")).results] == [
        "https://a.example/0",
        "https://a.example/1",
        "https://a.example/2",
    ]


def test_results_carry_the_search_snippet_and_metadata() -> None:
    """The DDG snippet is the only content when extraction fails."""
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    [result] = asyncio.run(pipeline.run("latest flooding")).results
    assert result.title == "Bank raises rate"
    assert result.snippet == "s"
    assert result.url == "https://a.example/x"


# --- the per-URL timeout --------------------------------------------------


def test_the_timeout_is_applied_per_url() -> None:
    """SPEC: per-URL timeouts, so one slow page cannot eat the whole run."""
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(2)]

    class Slow(FakeRouter):
        async def fetch(self, url: str, snippet: str = "") -> FetchResult:
            if url == "https://a.example/0":
                await asyncio.sleep(10)
            return await super().fetch(url, snippet)

    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=Slow(),
        extractor=FakeExtractor(),
        timeout_s=0.05,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert [r.error_class for r in report.results] == [ErrorClass.TRANSIENT_5XX, ErrorClass.OK]


def test_a_slow_url_does_not_stop_the_others() -> None:
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(3)]

    class Slow(FakeRouter):
        async def fetch(self, url: str, snippet: str = "") -> FetchResult:
            if url == "https://a.example/1":
                await asyncio.sleep(10)
            return await super().fetch(url, snippet)

    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=Slow(),
        extractor=FakeExtractor(),
        timeout_s=0.05,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert len(report.results) == 3


def test_the_search_is_not_subject_to_the_per_url_timeout() -> None:
    """The timeout is per URL, not around the whole run."""

    class Slowish(FakeSearcher):
        async def search(self, query: str) -> list[dict[str, t.Any]]:
            await asyncio.sleep(0.05)
            return list(self._hits)

    pipeline = Pipeline(
        searcher=Slowish(hits=[HIT]),
        router=FakeRouter({"https://a.example/x": _result("https://a.example/x")}),
        extractor=FakeExtractor(),
        timeout_s=0.01,
    )
    assert asyncio.run(pipeline.run("latest flooding")).succeeded == 1


def test_no_timeout_means_unbounded_when_opted_out() -> None:
    """`timeout_s=0` means "no bound", explicitly, for a caller that wants it."""
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=FakeRouter(),
        extractor=FakeExtractor(),
        timeout_s=0,
    )
    assert pipeline.timeout_s == 0


# --- review findings 3 and 4 ---------------------------------------------


def test_a_finished_event_reports_the_hit_rank_not_a_tally() -> None:
    """`Event.done` is the hit's rank, not a running count: use `Progress`."""
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(5)]
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=FakeRouter(),
        extractor=FakeExtractor(),
        concurrency=5,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    done = [e for e in report.events if e.kind == "finished"]
    assert sorted(e.done for e in done) == [1, 2, 3, 4, 5]
    assert report.progress.finished == 5


def test_progress_is_the_running_tally() -> None:
    """SPEC section 3's live line is "N of M done", which `Progress` carries."""
    hits = [{**HIT, "url": f"https://a.example/{i}"} for i in range(6)]
    progress = Progress()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=hits),
        router=FakeRouter(),
        extractor=FakeExtractor(),
        concurrency=2,
        progress=progress,
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert progress.total == 6
    assert progress.finished == 6


def test_the_timeout_has_a_safe_default() -> None:
    """Review finding 4: "no timeout" must not mean "wait forever" by absence.

    A caller that never sets `timeout_s` still gets a bound, so a stalled URL
    cannot occupy its semaphore slot indefinitely.
    """
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    assert pipeline.timeout_s == _DEFAULT_TIMEOUT_S


def test_a_stalled_url_is_bounded_even_without_an_explicit_timeout() -> None:
    """The default timeout alone must rescue an unbounded run."""

    class Hangs(FakeRouter):
        async def fetch(self, url: str, snippet: str = "") -> FetchResult:
            await asyncio.sleep(3600)
            return await super().fetch(url, snippet)

    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=Hangs(), extractor=FakeExtractor()
    )
    # Patching the default to something small proves the bound without waiting
    # the real default; the default's value is asserted in its own test.
    pipeline.timeout_s = _SHORT_TIMEOUT_S
    report = asyncio.run(asyncio.wait_for(pipeline.run("latest flooding"), 5))
    assert report.results[0].error_class == ErrorClass.TRANSIENT_5XX
    # T6.1: the reason must reach the user, not only the enum.
    assert "Timed out" in report.results[0].warning


def test_progress_reports_are_shared_not_rebuilt() -> None:
    """`Progress` is the value the UI renders; it must not be a per-run local."""
    progress = Progress()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=FakeRouter(),
        extractor=FakeExtractor(),
        progress=progress,
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert progress.finished == 1


# --- whitelist additions in the footer (review finding 2) -----------------


def test_the_report_carries_whitelist_additions(tmp_path: Path) -> None:
    """SPEC section 3's footer shows `whitelist +1 (example.co.ke: captcha)`."""
    whitelist = Whitelist(tmp_path / "whitelist.yaml")
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=FakeRouter(),
        extractor=FakeExtractor(),
        whitelist=whitelist,
    )
    asyncio.run(pipeline.run("latest flooding"))

    class Adding(FakeRouter):
        async def fetch(self, url: str, snippet: str = "") -> FetchResult:
            await whitelist.add_async("barrier.example", reason="captcha")
            return await super().fetch(url, snippet)

    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=Adding(),
        extractor=FakeExtractor(),
        whitelist=whitelist,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.whitelisted == [("barrier.example", "captcha")]


def test_a_preexisting_entry_is_not_reported_as_added(tmp_path: Path) -> None:
    whitelist = Whitelist(tmp_path / "whitelist.yaml")
    whitelist.add("old.example", reason="captcha")
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=FakeRouter(),
        extractor=FakeExtractor(),
        whitelist=whitelist,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.whitelisted == []


def test_no_whitelist_means_an_empty_caption() -> None:
    """The store is optional: a read-only run simply reports no additions."""
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]), router=FakeRouter(), extractor=FakeExtractor()
    )
    assert asyncio.run(pipeline.run("latest flooding")).whitelisted == []


def test_the_whitelist_is_read_before_the_fetches_start(tmp_path: Path) -> None:
    """The snapshot is taken before, or an entry added during the run is missed."""
    whitelist = Whitelist(tmp_path / "whitelist.yaml")

    async def add_during_fetch(url: str, snippet: str = "") -> FetchResult:
        await whitelist.add_async("mid.example", reason="captcha")
        return FetchResult(method=Method.LOCAL, ok=True, markdown="body")

    class Adding(FakeRouter):
        fetch = staticmethod(add_during_fetch)  # type: ignore[assignment]

    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=Adding(),
        extractor=FakeExtractor(),
        whitelist=whitelist,
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    assert report.whitelisted == [("mid.example", "captcha")]


# --- the acceptance test: mixed outcomes through the real router -----------
#
# T3.1's acceptance is "mocked end-to-end test with mixed outcomes (local ok,
# barrier->API, dead, local down)". Everything above uses a fake router, so it
# proves the pipeline's own bookkeeping but not that the SPEC section 4 table
# actually applies. This section drives the real `Router` with a mocked
# `LocalFirecrawl` and `ApiFirecrawl`, so the classification -> routing decisions
# are exercised, not modelled.


def _mixed_router(tmp_path: Path, *, no_key: bool = False, with_key: bool = False) -> Router:
    """A real router over a temp whitelist, with nothing set up yet.

    A test fixture key, never a real one — CLAUDE.md forbids real API keys, and
    the API is mocked at the transport level anyway.
    """
    router = Router(
        local=LocalFirecrawl(),
        api=ApiFirecrawl(daily_limit=50, api_key="test-key-fixture" if with_key else None),
        whitelist=Whitelist(tmp_path / "whitelist.yaml"),
        no_api=no_key,
    )
    return router


def _run_mixed(router: Router, tmp_path: Path) -> Report:
    """Drive a real router through all four SPEC section 4 outcomes."""
    searcher = FakeSearcher(
        hits=[
            {**HIT, "url": "https://ok.example/a"},
            {**HIT, "url": "https://barrier.example/b"},
            {**HIT, "url": "https://dead.example/c"},
            {**HIT, "url": "https://down.example/d"},
        ]
    )
    pipeline = Pipeline(
        searcher=searcher,
        router=router,
        extractor=FakeExtractor(),
        whitelist=router.whitelist,
    )
    return asyncio.run(pipeline.run("latest flooding"))


@respx.mock
def test_local_ok_extracts_normally(tmp_path: Path) -> None:
    respx.post("http://localhost:3002/v2/scrape").mock(
        return_value=httpx.Response(200, json=_local_ok("https://ok.example/a"))
    )
    report = _run_mixed(_mixed_router(tmp_path), tmp_path)
    by_url = {r.url: r for r in report.results}
    assert by_url["https://ok.example/a"].method == Method.LOCAL
    assert by_url["https://ok.example/a"].ok


@respx.mock
def test_a_barrier_goes_to_the_api_and_whitelists_the_domain(tmp_path: Path) -> None:
    """SPEC section 4 row 1: whitelist + retry via API."""
    router = _mixed_router(tmp_path, with_key=True)
    respx.post("http://localhost:3002/v2/scrape").mock(
        return_value=httpx.Response(200, json=_local_barrier("https://barrier.example/b"))
    )
    api_route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(200, json=_api_ok("https://barrier.example/b"))
    )
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[{**HIT, "url": "https://barrier.example/b"}]),
        router=router,
        extractor=FakeExtractor(),
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    [result] = report.results
    assert result.method == Method.API
    assert result.ok
    assert api_route.call_count == 1
    assert Whitelist(tmp_path / "whitelist.yaml").is_whitelisted("barrier.example")
    assert report.by_method() == {Method.API: 1}
    assert report.found == 1
    assert report.succeeded == 1


@respx.mock
def test_a_dead_url_degrades_to_snippet_with_no_api_call(tmp_path: Path) -> None:
    """SPEC section 4 row 3: snippet only, no whitelist, no API."""
    api_route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(200, json=_api_ok("https://dead.example/c"))
    )
    respx.post("http://localhost:3002/v2/scrape").mock(
        return_value=httpx.Response(404, json={"success": False})
    )
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[{**HIT, "url": "https://dead.example/c"}]),
        router=_mixed_router(tmp_path),
        extractor=FakeExtractor(),
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    [result] = report.results
    assert result.method == Method.SNIPPET
    assert not result.ok
    assert api_route.call_count == 0
    assert not (tmp_path / "whitelist.yaml").exists()


@respx.mock
def test_local_down_uses_the_api_without_whitelisting(tmp_path: Path) -> None:
    """SPEC section 4 row 6: API for this run only, no whitelist entry."""
    respx.post("http://localhost:3002/v2/scrape").mock(side_effect=httpx.ConnectError("refused"))
    api_route = respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        return_value=httpx.Response(200, json=_api_ok("https://down.example/d"))
    )
    router = _mixed_router(tmp_path, with_key=True)
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[{**HIT, "url": "https://down.example/d"}]),
        router=router,
        extractor=FakeExtractor(),
    )
    report = asyncio.run(pipeline.run("latest flooding"))
    [result] = report.results
    assert result.method == Method.API
    assert api_route.call_count == 1
    assert not (tmp_path / "whitelist.yaml").exists()
    assert report.by_method() == {Method.API: 1}


@respx.mock
def test_all_four_outcomes_in_one_run(tmp_path: Path) -> None:
    """The acceptance, in one run: local ok, barrier->API, dead, local down."""

    def scrape(request: httpx.Request) -> httpx.Response:
        # The target lives in the POST body, not in the request URL.
        url = _target_url(request)
        if "down.example" in url:
            raise httpx.ConnectError("refused")
        if "dead.example" in url:
            return httpx.Response(404, json={"success": False})
        if "barrier.example" in url:
            return httpx.Response(200, json=_local_barrier(url))
        return httpx.Response(200, json=_local_ok(url))

    local_route = respx.post("http://localhost:3002/v2/scrape").mock(side_effect=scrape)
    respx.post("https://api.firecrawl.dev/v2/scrape").mock(
        side_effect=lambda request: httpx.Response(200, json=_api_ok(_target_url(request)))
    )

    router = _mixed_router(tmp_path, with_key=True)
    report = _run_mixed(router, tmp_path)
    by_url = {r.url: r for r in report.results}

    assert by_url["https://ok.example/a"].method == Method.LOCAL
    assert by_url["https://barrier.example/b"].method == Method.API
    assert by_url["https://dead.example/c"].method == Method.SNIPPET
    assert by_url["https://down.example/d"].method == Method.API

    # The footer's "N found · N extracted (N local, N API) · N snippet-only" line.
    assert report.found == 4
    assert report.succeeded == 3
    assert report.by_method() == {Method.LOCAL: 1, Method.API: 2, Method.SNIPPET: 1}
    # All four attempted local first; "down" is the one whose attempt failed.
    assert local_route.call_count == 4

    # The whitelist side effect, per SPEC section 4.
    whitelist = Whitelist(tmp_path / "whitelist.yaml")
    assert whitelist.is_whitelisted("barrier.example")
    assert not whitelist.is_whitelisted("dead.example")
    assert not whitelist.is_whitelisted("down.example")

    # The footer's `whitelist +1 (barrier.example: ...)` caption.
    assert [domain for domain, _ in report.whitelisted] == ["barrier.example"]


def _local_ok(url: str) -> dict[str, t.Any]:
    return {"success": True, "data": _page(url)}


def _local_barrier(url: str) -> dict[str, t.Any]:
    """A render with enough furniture to look alive but no real content."""
    return {"success": True, "data": _page(url, markdown=_BARRIER_MARKDOWN)}


def _api_ok(url: str) -> dict[str, t.Any]:
    return {"success": True, "data": _page(url, markdown=_API_MARKDOWN)}


def _target_url(request: httpx.Request) -> str:
    """The URL a Firecrawl scrape call was asked to fetch, read from its body."""
    body = json.loads(request.content.decode())
    return str(body.get("url", ""))


def _page(url: str, markdown: str = _API_MARKDOWN) -> dict[str, t.Any]:
    return {
        "markdown": markdown,
        "metadata": {"title": "Bank raises rate", "statusCode": 200},
        "html": "",
        "links": [],
    }


# --- the reason reaches the user (T6.1) --------------------------------------


def test_a_degraded_result_carries_the_routers_warning() -> None:
    """SPEC section 4: the reason a page degraded must reach the user.

    The router composes this per failure class; `_assembled` used to drop it, so
    a run of snippet-only results showed a badge and no reason at all.
    """
    router = FakeRouter(
        {
            "https://a.example/x": FetchResult(
                method=Method.SNIPPET,
                ok=False,
                error_class=ErrorClass.DEAD,
                warning="page not found (HTTP 404)",
            )
        }
    )
    pipeline = Pipeline(searcher=FakeSearcher(hits=[HIT]), router=router, extractor=FakeExtractor())
    [result] = asyncio.run(pipeline.run("latest flooding")).results
    assert result.warning == "page not found (HTTP 404)"


# --- the optional abstractive summarizer (T4.1; SPEC section 2) --------------


class FakeSummarizer:
    """Stands in for `ResearchSynth`: the one signature the pipeline calls."""

    def __init__(self, text: str = "Abstractive summary.", raises: Exception | None = None) -> None:
        self._text = text
        self._raises = raises
        self.seen: list[dict[str, str]] = []

    async def summary(self, *, heading: str, text: str, fallback: str) -> str:
        self.seen.append({"heading": heading, "text": text, "fallback": fallback})
        if self._raises is not None:
            raise self._raises
        return self._text


def test_llm_replaces_the_extractive_summary_with_the_models_one() -> None:
    """SPEC section 2's `--llm`: the page's summary becomes the model's, in place."""
    router = FakeRouter({"https://a.example/x": _result("https://a.example/x")})
    summarizer = FakeSummarizer("The bank raised the rate by fifty basis points.")
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=router,
        extractor=FakeExtractor(),
        summarizer=summarizer,
    )
    [result] = asyncio.run(pipeline.run("latest flooding")).results
    assert result.summary == "The bank raised the rate by fifty basis points."


def test_the_summarizer_gets_the_page_it_summarised() -> None:
    """An abstractive summary is written from the page, not from its old summary."""
    router = FakeRouter({"https://a.example/x": _result("https://a.example/x", markdown="PAGE")})
    summarizer = FakeSummarizer()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=router,
        extractor=FakeExtractor(),
        summarizer=summarizer,
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert summarizer.seen[0]["text"] == "PAGE"


def test_a_page_with_no_content_is_never_summarised() -> None:
    """A snippet-only page has no body: `--llm` has nothing to rewrite it from."""
    router = FakeRouter()  # no answer: SNIPPET, so no markdown to summarise
    summarizer = FakeSummarizer()
    pipeline = Pipeline(
        searcher=FakeSearcher(hits=[HIT]),
        router=router,
        extractor=FakeExtractor(),
        summarizer=summarizer,
    )
    asyncio.run(pipeline.run("latest flooding"))
    assert summarizer.seen == []
