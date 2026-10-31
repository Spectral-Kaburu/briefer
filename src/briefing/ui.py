"""Rich rendering of a run: header, live progress, result cards, footer.

Every renderer here is a pure function of a `Result`/`Report`/`Event`, so the
snapshots in `tests/test_ui.py` (`Console(record=True, width=100)`) capture the
exact bytes Rich writes, with no pipeline, search or fetch involved.

Markup safety is load-bearing: headings, summaries and URLs all arrive from
search results and are never trusted, so they are rendered through `Text` and
never through markup strings. `Text` does not re-parse `[tags]` (verified in
NOTES-APIS.md section 5), whereas `console.print(str)` with markup on would.
"""

from __future__ import annotations

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from briefing.fetch.router import ErrorClass, Method
from briefing.pipeline import Event, Report, Result
from briefing.whitelist import normalize_host

# SPEC section 3's badges: emoji, label, style.
_BADGES: dict[Method, tuple[str, str, str]] = {
    Method.LOCAL: ("🟢", "LOCAL", "bold green"),
    Method.API: ("🟡", "API", "bold yellow"),
    Method.SNIPPET: ("⚪", "SNIPPET", "dim"),
}
# A page that is not ok and not snippet-only: extraction genuinely failed.
_FAILED = ("🔴", "FAILED", "bold red")


def badge(method: Method, ok: bool = True, *, plain: bool = False) -> Text:
    """One method badge, per SPEC section 3."""
    emoji, label, style = _BADGES[method]
    # A snippet-only page still has its search snippet, so SPEC section 3 shows
    # it as degraded (⚪) rather than failed (🔴).
    if not ok and method is not Method.SNIPPET:
        emoji, label, style = _FAILED
    if plain:
        return Text(label)
    return Text.assemble((f"{emoji} ", style), (label, style))


def header(query: str, mode: str, window: str | None = None, *, plain: bool = False) -> Group:
    """The two-line title SPEC section 3 shows above the results."""
    title = Text.assemble(
        (" briefing ", "bold"),
        ("▸ ", "bold cyan"),
        ("research", "bold cyan"),
    )
    where = f"mode: {mode}" if window is None else f"mode: {mode} · {window}"
    detail = Text.assemble(
        (' Query: "', "dim"),
        (query, "italic"),
        ('"', "dim"),
        (f"   {where}", "dim"),
    )
    return Group(title, detail)


def card(result: Result, *, plain: bool = False) -> Panel:
    """One result card: rank, heading, domain + age, summary, URL."""
    heading = result.heading or result.title or result.url
    top = Text()
    top.append(f"{result.rank}  ", "bold")
    top.append(heading, "bold")
    top.append("  ")
    top.append(badge(result.method, result.ok, plain=plain))
    meta = Text(result.domain, "dim")
    if result.age:
        meta.append(f" · {result.age}", "dim")

    body = Text()
    body.append(top)
    body.append("\n")
    body.append(meta)
    if result.summary:
        body.append(f"\n{result.summary}")
    body.append(f"\n{result.url}", "blue")
    return Panel(body, box=box.ROUNDED, expand=True, padding=(0, 1))


def footer(report: Report, *, plain: bool = False) -> Panel:
    """The stats footer SPEC section 3 specifies, with its caption."""
    extracted: dict[Method, int] = {}
    for result in report.results:
        if result.ok:
            extracted[result.method] = extracted.get(result.method, 0) + 1
    snippet_only = sum(1 for r in report.results if r.method is Method.SNIPPET and not r.ok)

    # SPEC section 3's format, verbatim: "10 found · 8 extracted (7 local, 1 API)
    # · 2 snippet-only · whitelist +1 (example.co.ke: captcha) · 6.4s". It is one
    # line on purpose; how it behaves at narrow widths is T3.5's question, not
    # this function's.
    parts = [f"{report.found} found"]
    if report.succeeded:
        breakdown = ", ".join(f"{count} {method.value}" for method, count in extracted.items())
        parts.append(f"{report.succeeded} extracted ({breakdown})")
    # SPEC section 3's wording is "2 snippet-only" — an adjective, not a plural
    # noun, so no "s" however many there were.
    if snippet_only:
        parts.append(f"{snippet_only} snippet-only")
    if report.whitelisted:
        listed = ", ".join(f"{domain}: {reason}" for domain, reason in report.whitelisted)
        parts.append(f"whitelist +{len(report.whitelisted)} ({listed})")
    parts.append(f"{report.elapsed_s:.1f}s")
    return Panel(Text(" · ".join(parts), "dim"), box=box.SIMPLE, expand=True)


class UI:
    """Renders one run: header, live progress, cards, footer.

    `plain=None` means "decide from the console": SPEC section 3 requires auto
    `--plain` when piped, and a recorded or redirected console is not a terminal.
    `--plain` on the command line forces it on either way.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        plain: bool | None = None,
        window: str | None = None,
    ) -> None:
        self.console = console if console is not None else Console()
        self.plain = (not self.console.is_terminal) if plain is None else plain
        self.window = window
        # `--plain` and a piped run both mean "this is not decoration", so colour
        # is dropped. Only ever on -> off, so a caller who already forced no_color
        # is unaffected.
        if self.plain:
            self.console.no_color = True
        self._live: Live | None = None
        self._lines: list[Text] = []

    def begin(self, query: str, mode: str) -> None:
        """Print the header and open the live progress block."""
        self.console.print(header(query, mode, self.window, plain=self.plain))
        # A plain run is a log: the progress lines are the only record of what
        # was fetched, so they must survive instead of being erased on stop.
        self._live = Live(console=self.console, auto_refresh=False, transient=not self.plain)
        self._live.start(refresh=True)

    def on_event(self, event: Event) -> None:
        """Pipeline callback: turn one finished URL into a progress line."""
        if event.kind != "finished" or not event.url:
            return
        line = Text()
        line.append("✔ " if event.error_class is ErrorClass.OK else "⚠ ", "bold")
        line.append(badge(event.method, event.error_class is ErrorClass.OK, plain=self.plain))
        line.append(f"  {normalize_host(event.url)}")
        self._lines.append(line)
        self._refresh()

    def show(self, report: Report) -> None:
        """Close the live progress block, then print the cards and footer."""
        if self._live is not None:
            self._live.stop()
            self._live = None
        for result in report.results:
            self.console.print(card(result, plain=self.plain))
        self.console.print(footer(report, plain=self.plain))

    def _refresh(self) -> None:
        if self._live is None:
            return
        self._live.update(Group(*self._lines), refresh=True)
