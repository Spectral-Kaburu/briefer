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

import dataclasses
import typing as t
from collections import Counter

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from briefing.fetch.router import ErrorClass, Method
from briefing.pipeline import Event, Report, Result
from briefing.whitelist import normalize_host

if t.TYPE_CHECKING:
    from briefing.ask import Answer


@dataclasses.dataclass(frozen=True)
class CardView:
    """The fields `card` reads, for callers that have no pipeline `Result`.

    `ask` mode builds its cards from `Answer.sources`, not from a pipeline run, so
    it fills this instead of faking a `Result`. Anything `card` reads must be here.
    """

    rank: int
    title: str
    url: str
    domain: str
    method: Method
    heading: str = ""
    summary: str = ""
    snippet: str = ""
    ok: bool = True
    age: str = ""
    warning: str = ""
    license: str = ""
    author: str = ""


# SPEC section 3's badges: emoji, label, style.
_BADGES: dict[Method, tuple[str, str, str]] = {
    Method.LOCAL: ("🟢", "LOCAL", "bold green"),
    Method.API: ("🟡", "API", "bold yellow"),
    Method.SNIPPET: ("⚪", "SNIPPET", "dim"),
    Method.WIKI: ("📖", "WIKI", "cyan"),
    Method.SO: ("💡", "SO", "blue"),
    Method.DDGA: ("🤔", "DDGA", "magenta"),
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


def _panel(top: Text, meta: Text, details: list[Text]) -> Panel:
    """The one frame every card is drawn in: source cards and the answer card.

    A card is a title line (with its badge), a dim meta line, then any number of
    detail lines, one per row. Box, width and padding are set only here, so a
    change to this function changes every card at once. Callers pass `Text`, never
    markup strings, so untrusted content cannot be re-parsed as `[tags]`.
    """
    body = Text()
    body.append(top)
    body.append("\n")
    body.append(meta)
    for line in details:
        body.append("\n")
        body.append(line)
    return Panel(body, box=box.ROUNDED, expand=True, padding=(0, 1))


def card(result: Result | CardView, *, plain: bool = False) -> Panel:
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

    details: list[Text] = []
    # SPEC section 4: why this page degraded. `Text` holds it verbatim, so a
    # reason quoted from the target's own response body stays inert text.
    if result.warning:
        details.append(Text(result.warning, "yellow"))
    # Show license for CC BY-SA content (Wikipedia, Stack Exchange)
    if result.license:
        details.append(Text(result.license, "cyan"))
    # Show author for Stack Exchange answers
    if result.author:
        details.append(Text(f"by {result.author}", "bright_black"))
    # The summary is the body when there is one. A snippet-only page has no
    # summary, and its snippet is the whole result — dropping it would leave a
    # card that is a heading and a link with nothing read in between.
    prose = result.summary or result.snippet
    if prose:
        details.append(Text(prose))
    details.append(Text(result.url, "blue"))
    return _panel(top, meta, details)


# The answer card's badge. It is not a fetch method, so it lives beside `_BADGES`
# rather than in it: the same emoji + label + style shape, used by both modes.
_ANSWER_BADGE = ("✨", "ANSWER", "bold magenta")
_PASSAGES_BADGE = ("📄", "PASSAGES", "bold cyan")


def answer(
    text: str,
    *,
    plain: bool = False,
    used_llm: bool = True,
    sources: int | None = None,
) -> Panel:
    """The answer card, shared by `research --synth` and `ask`.

    Built exactly like `card`: same rounded box, full width, padding, and a top
    line of bold title + badge, so the answer reads as the first card in the list
    rather than a differently-styled panel. `used_llm=False` marks an ask run that
    fell back to raw passages, so the badge never claims a model wrote them.

    Through `Text` like every other renderer here, because a model's output is no
    more trusted than a search result's — `[tags]` in it must not become markup.
    """
    emoji, label, style = _ANSWER_BADGE if used_llm else _PASSAGES_BADGE
    top = Text()
    top.append("Answer", "bold")
    top.append("  ")
    top.append(Text(label) if plain else Text.assemble((f"{emoji} ", style), (label, style)))

    meta = Text("synthesised answer" if used_llm else "no model answer", "dim")
    if sources:
        meta.append(f" · {_plural(sources, 'source')}", "dim")

    prose = Text(text)
    if not used_llm:
        # The passages fallback is "[n] Title" lines followed by their text, so a
        # line opening with a source number is a heading. Only done here: in a
        # model's answer "[1]" is a citation inside a sentence, never a heading.
        prose.highlight_regex(r"(?m)^\[\d+\] .*$", "bold")
    return _panel(top, meta, [prose])


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
    return _footer_panel(parts)


def _plural(n: int, noun: str) -> str:
    """ "1 source", "2 sources"."""
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _footer_panel(parts: list[str]) -> Panel:
    """The one footer frame: dim parts joined by " · ", used by both modes."""
    return Panel(Text(" · ".join(parts), "dim"), box=box.SIMPLE, expand=True)


def ask_footer(answer: Answer) -> Panel | None:
    """The ask-mode footer: source methods, answer kind, timings, warnings."""
    parts: list[str] = []
    # Counted over every source, including ones with no card, so the numbers match
    # "N sources". Labels come from `Method.value`, as in the research footer.
    counts = Counter(s[4] for s in answer.sources)
    if counts:
        parts.append(", ".join(f"{n} {Method[name].value}" for name, n in counts.items()))
    kind = "LLM" if answer.used_llm else "passages"
    parts.append(f"{_plural(len(answer.sources), 'source')} ({kind})")
    parts.extend(
        f"{stage}: {answer.timings[stage]:.1f}s"
        for stage in ("search", "fetch", "model")
        if stage in answer.timings
    )
    if answer.warnings:
        parts.append("; ".join(answer.warnings))
    return _footer_panel(parts)


def render_answer(
    reply: Answer, *, plain: bool | None = None, console: Console | None = None
) -> None:
    """Print an ask `Answer`: the answer card, a card per source, then the footer.

    Sources with no text are skipped, since a card of just a link tells the reader
    nothing. `--plain` goes to stderr uncoloured, as it always has.
    """
    if console is None:
        console = Console(stderr=True) if plain else Console()
    if plain:
        console.no_color = True
    console.print(
        answer(reply.text, plain=bool(plain), used_llm=reply.used_llm, sources=len(reply.sources))
    )
    # Source tuple: (n, title, url, domain, method, text, license, author, snippet)
    shown = [s for s in reply.sources if s[5]]
    if shown:
        console.print()
    for rank, (_n, title, url, domain, method, text, lic, author, _snip) in enumerate(shown, 1):
        view = CardView(
            rank=rank,
            title=title,
            url=url or "",
            domain=domain,
            method=Method[method],
            heading=title,
            summary=text,
            snippet=text,
            license=lic,
            author=author,
        )
        console.print(card(view, plain=bool(plain)))
    console.print(ask_footer(reply))


class AskProgress:
    """A live status line for `briefing ask`, on stderr so `--json` stays clean.

    Only built on a real terminal and only when output is not `--plain`/`--json`:
    a piped run gets no spinner characters in its logs, and the caller then does not
    pass `on_progress` at all, so a test double never sees the argument.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._status: t.Any = None
        self._total = 0
        self._done = 0

    @classmethod
    def maybe(cls, *, json_out: bool, plain: bool | None) -> AskProgress | None:
        if json_out or plain:
            return None
        console = Console(stderr=True)
        return cls(console) if console.is_terminal else None

    def __enter__(self) -> t.Self:
        self._status = self._console.status("Searching the web…")
        self._status.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._status is not None:
            self._status.stop()

    def update(self, stage: str, detail: str = "") -> None:
        """The `on_progress` callback: turn a stage into one line of status."""
        if self._status is None:
            return
        if stage == "searched":
            text = f"Found {detail} results"
        elif stage == "fetch":
            self._total, self._done = int(detail or 0), 0
            text = f"Reading {detail} pages…"
        elif stage == "fetched":
            self._done += 1
            text = f"Read {self._done}/{self._total} pages · {detail}"
        elif stage == "model":
            text = "Writing the answer…"
        else:
            text = "Searching the web…"
        self._status.update(text)


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
            # A non-terminal Live leaves the cursor where its last update ended,
            # without the newline a normal print emits, so the first card's border
            # welds onto the last progress line. A terminal clears the live area on
            # stop and takes the cursor with it, so it needs no extra line.
            if not self.console.is_terminal:
                self.console.line()
        for result in report.results:
            self.console.print(card(result, plain=self.plain))
        self.console.print(footer(report, plain=self.plain))

    def _refresh(self) -> None:
        if self._live is None:
            return
        self._live.update(Group(*self._lines), refresh=True)
