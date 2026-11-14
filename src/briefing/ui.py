"""Rich rendering of one inquiry: header, live progress, answer card, source cards, footer.

`ask` and `research` look and feel the same, so there is one renderer. It takes the
types of `briefing.inquiry` (`Answer`, `EvidenceSource`, `Notice`) and nothing from
the engine's internals, so the snapshots in `tests/test_ui.py`
(`Console(record=True, width=100)`) capture the exact bytes Rich writes with no
search, fetch or model involved.

Markup safety is load-bearing. Headings, summaries, URLs, warnings and the model's
own answer all arrive from the network and are never trusted:

* source-card text goes through `Text`, which does not re-parse `[tags]`;
* the model's Markdown goes through `rich.markdown.Markdown(..., hyperlinks=False)`,
  which builds `Text` from Markdown tokens (so `[bold red]x[/]` stays literal) and
  prints a link's target next to its label instead of making it clickable, so a
  model cannot hide where a link goes;
* every untrusted string is first stripped of control characters and ANSI escapes
  (`strip_control_chars`), because Rich passes ESC through to the terminal.

`--plain` (or a piped console) prints the raw Markdown as `Text`, uncoloured.
"""

from __future__ import annotations

import typing as t
from collections import Counter

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from briefing.fetch.router import Method
from briefing.inquiry import Answer, EvidenceSource, Notice
from briefing.knowledge.base import strip_control_chars

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

# The answer card's badges. Not fetch methods, so they live beside `_BADGES`.
_ANSWER_BADGE = ("✨", "ANSWER", "bold magenta")
_PASSAGES_BADGE = ("📄", "PASSAGES", "bold cyan")
_PROVISIONAL_BADGE = ("⏳", "PROVISIONAL", "bold yellow")


def _clean(text: str | None) -> str:
    """Untrusted text with control characters and ANSI escapes removed."""
    return strip_control_chars(text or "")


def _one_line(text: str | None) -> str:
    """Untrusted text collapsed onto one line, for titles and headers."""
    return " ".join(_clean(text).split())


def _plural(n: int, noun: str) -> str:
    """ "1 source", "2 sources"."""
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _method(source: EvidenceSource) -> Method:
    """The fetch method of a source, which a test double may spell as a name or a value."""
    raw = source.method
    if isinstance(raw, Method):
        return raw
    try:
        return Method[str(raw)]
    except KeyError:
        pass
    try:
        return Method(str(raw))
    except ValueError:
        return Method.SNIPPET


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


def _profile_name(profile: object) -> str:
    """A `Profile` or its bare name."""
    return str(getattr(profile, "name", profile))


def header(
    query: str,
    profile: object,
    mode: str | None = None,
    window: str | None = None,
    *,
    plain: bool = False,
) -> Group:
    """The two-line title above the results; the first line names the profile."""
    title = Text.assemble(
        (" briefing ", "bold"),
        ("▸ ", "bold cyan"),
        (_one_line(_profile_name(profile)), "bold cyan"),
    )
    where = [f"mode: {_one_line(mode)}"] if mode else []
    if window:
        where.append(_one_line(window))
    detail = Text.assemble(
        (' Query: "', "dim"),
        (_one_line(query), "italic"),
        ('"', "dim"),
    )
    if where:
        detail.append("   " + " · ".join(where), "dim")
    return Group(title, detail)


def _frame(body: RenderableType) -> Panel:
    """The one frame every card is drawn in: source cards and the answer card.

    Box, width and padding are set only here, so a change here changes every card.
    """
    return Panel(body, box=box.ROUNDED, expand=True, padding=(0, 1))


def _panel(top: Text, meta: Text, details: list[Text]) -> Panel:
    """A card of `Text` lines: a title line, a dim meta line, then one row per detail."""
    body = Text()
    body.append(top)
    body.append("\n")
    body.append(meta)
    for line in details:
        body.append("\n")
        body.append(line)
    return _frame(body)


def card(source: EvidenceSource, *, plain: bool = False) -> Panel:
    """One source card: its citation number, heading, domain + age, text, URL.

    The number is `source.number`, the one the answer cites as `[n]`, never the
    card's position in the list.
    """
    method = _method(source)
    heading = _one_line(source.heading or source.title or source.url)
    top = Text()
    top.append(f"[{source.number}]  ", "bold")
    top.append(heading, "bold")
    top.append("  ")
    top.append(badge(method, source.ok, plain=plain))
    meta = Text(_one_line(source.domain), "dim")
    if source.age:
        meta.append(f" · {_one_line(source.age)}", "dim")

    details: list[Text] = []
    # Why this page degraded. `Text` holds it verbatim, so a reason quoted from the
    # target's own response body stays inert.
    if source.warning:
        details.append(Text(_clean(source.warning), "yellow"))
    if source.license:
        details.append(Text(_one_line(source.license), "cyan"))
    if source.author:
        details.append(Text(f"by {_one_line(source.author)}", "bright_black"))
    # A snippet-only page has no extract and its snippet is the whole result.
    prose = source.text or source.snippet
    if prose:
        details.append(Text(_clean(prose)))
    details.append(Text(_one_line(source.url), "blue"))
    return _panel(top, meta, details)


def _answer_title(label: tuple[str, str, str], *, plain: bool) -> Text:
    emoji, name, style = label
    top = Text()
    top.append("Answer", "bold")
    top.append("  ")
    top.append(Text(name) if plain else Text.assemble((f"{emoji} ", style), (name, style)))
    return top


def answer_card(answer: Answer, *, plain: bool = False) -> Panel | None:
    """The answer card, or None when there is nothing to show as an answer.

    * A model answer renders its Markdown (`Markdown(hyperlinks=False)`), or the
      raw Markdown as `Text` when `plain`.
    * No usable model answer: `ask` shows the extracted passages; any other profile
      shows no answer card (the cards and the footer's notice say why).
    * A provisional answer carries a PROVISIONAL badge and which hosts are pending.
    """
    if answer.provisional and not answer.used_llm:
        return None
    if not answer.used_llm and not answer.show_passages_when_no_model:
        return None
    if not answer.text.strip():
        return None

    text = _clean(answer.text)
    if answer.provisional:
        label = _PROVISIONAL_BADGE
    elif answer.used_llm:
        label = _ANSWER_BADGE
    else:
        label = _PASSAGES_BADGE
    top = _answer_title(label, plain=plain)

    if answer.used_llm:
        who = _one_line(answer.model.label) if answer.model is not None else ""
        meta = Text(f"synthesised by {who}" if who else "synthesised answer", "dim")
    else:
        meta = Text("no model answer", "dim")
    if answer.sources:
        meta.append(f" · {_plural(len(answer.sources), 'source')}", "dim")
    lines: list[RenderableType] = [top, meta]
    if answer.provisional and answer.pending:
        hosts = ", ".join(_one_line(h) for h in answer.pending)
        lines.append(Text(f"still reading: {hosts}", "yellow"))

    body: RenderableType
    if plain:
        body = Text(text)
    elif answer.used_llm:
        body = Markdown(text, hyperlinks=False)
    else:
        # The passages fallback is "[n] Title" lines followed by their text, so a
        # line opening with a source number is a heading.
        passages = Text(text)
        passages.highlight_regex(r"(?m)^\[\d+\] .*$", "bold")
        body = passages
    lines.append(body)
    return _frame(Group(*lines))


def _stats_line(answer: Answer) -> str:
    """The first footer line: found, extracted (by method), snippet-only, whitelist, timings."""
    sources = answer.sources
    extracted = Counter(_method(s) for s in sources if s.ok)
    snippet_only = sum(1 for s in sources if _method(s) is Method.SNIPPET and not s.ok)
    parts = [f"{answer.found or len(sources)} found"]
    if extracted:
        breakdown = ", ".join(f"{count} {method.value}" for method, count in extracted.items())
        parts.append(f"{sum(extracted.values())} extracted ({breakdown})")
    # "2 snippet-only": an adjective, not a plural noun.
    if snippet_only:
        parts.append(f"{snippet_only} snippet-only")
    if answer.whitelisted:
        listed = ", ".join(
            f"{_one_line(domain)}: {_one_line(reason)}" for domain, reason in answer.whitelisted
        )
        parts.append(f"whitelist +{len(answer.whitelisted)} ({listed})")
    parts.extend(
        f"{stage} {answer.timings[stage]:.1f}s"
        for stage in ("search", "fetch", "model")
        if stage in answer.timings
    )
    if answer.timings:
        parts.append(f"{sum(answer.timings.values()):.1f}s total")
    return " · ".join(parts)


def _model_line(answer: Answer) -> str | None:
    """Which model answered, or why it did not. None when no model was involved.

    A failed model's reason is normally also a `Notice`; it is not printed twice.
    """
    model = answer.model
    if model is None:
        return None
    problem = model.problem()
    if problem is None:
        elapsed = f" ({model.elapsed_s:.1f}s)" if model.elapsed_s else ""
        return f"model: {_one_line(model.label) or 'unknown'}{elapsed}"
    if any(n.stage == "model" and problem in n.message for n in answer.notices):
        return None
    return f"model: {_one_line(problem)}"


def footer(answer: Answer, *, plain: bool = False) -> Panel:
    """The footer: one stats line, the model line, then every notice on its own line."""
    lines = [Text(_stats_line(answer), "dim")]
    model_line = _model_line(answer)
    if model_line:
        lines.append(Text(model_line, "dim"))
    notices: list[Notice] = list(answer.notices)
    if not notices:
        # Warnings built without notices (older callers) still must not vanish.
        notices = [Notice("note", w) for w in answer.warnings]
    for notice in notices:
        lines.append(Text(f"{_one_line(notice.stage)}: {_one_line(notice.message)}", "yellow"))
    return Panel(Text("\n").join(lines), box=box.SIMPLE, expand=True)


class UI:
    """Renders one run, for either profile: header, live progress, cards, footer.

    The CLI wires `begin` / `on_progress` / `on_source` / `on_partial` / `show`
    (see UI_API.md). `plain=None` means "decide from the console": SPEC section 3
    requires auto `--plain` when piped, and a recorded or redirected console is not
    a terminal. `--plain` forces it on either way.
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
        # `--plain` and a piped run both mean "not decoration", so colour is dropped.
        # Only ever on -> off, so a caller who already forced no_color is unaffected.
        if self.plain:
            self.console.no_color = True
        self._live: Live | None = None
        self._lines: list[Text] = []
        self._status = ""
        self._total = 0
        self._done = 0

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> t.Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def begin(self, query: str, profile: object, mode: str | None = None) -> None:
        """Print the header and open the live progress block."""
        self.console.print(header(query, profile, mode, self.window, plain=self.plain))
        self._lines, self._status, self._total, self._done = [], "", 0, 0
        # A plain run is a log: the progress lines are the only record of what was
        # fetched, so they must survive instead of being erased on stop.
        self._live = Live(console=self.console, auto_refresh=False, transient=not self.plain)
        self._live.start(refresh=True)

    def close(self) -> None:
        """Stop the live block. Safe to call twice, and on an error path."""
        if self._live is None:
            return
        self._status = ""
        self._live.update(Group(*self._lines), refresh=True)
        self._live.stop()
        self._live = None
        # A non-terminal Live leaves the cursor where its last update ended, without
        # the newline a normal print emits, so the next card's border would weld onto
        # the last progress line. A terminal clears the live area and needs none.
        if not self.console.is_terminal and self._lines:
            self.console.line()

    # -- callbacks -----------------------------------------------------------

    def on_progress(self, stage: str, detail: str = "") -> None:
        """`inquire(on_progress=...)`: the status line under the per-page lines."""
        if stage == "search":
            self._status = "Searching the web…"
        elif stage == "searched":
            self._status = f"Found {_one_line(detail)} results"
        elif stage == "fetch":
            self._total, self._done = _as_int(detail), 0
            self._status = f"Reading {_one_line(detail)} pages…"
        elif stage == "model":
            self._status = "Writing the answer…"
        else:  # "fetched" is counted from `on_source`, which carries the page itself
            return
        self._refresh()

    def on_source(self, source: EvidenceSource) -> None:
        """`inquire(on_source=...)`: one finished page becomes one progress line."""
        line = Text()
        line.append("✔ " if source.ok else "⚠ ", "bold")
        line.append(badge(_method(source), source.ok, plain=self.plain))
        line.append(f"  [{source.number}] {_one_line(source.domain)}")
        self._lines.append(line)
        self._done += 1
        if self._total:
            self._status = f"Read {self._done}/{self._total} pages"
        self._refresh()

    def on_partial(self, answer: Answer) -> None:
        """`inquire(on_partial=...)`: the provisional card, printed above the live block.

        Printed for good rather than shown inside the live block: a long answer does
        not fit a transient block, and a log wants it. The final answer follows it.
        """
        card_ = answer_card(answer, plain=self.plain)
        if card_ is not None:
            self.console.print(card_)
            return
        # No usable provisional text: say why now, the final answer will say it again.
        for notice in answer.notices:
            if notice.stage == "model":
                self.console.print(Text(f"{notice}", "yellow"))

    def show(self, answer: Answer) -> None:
        """Close the live block, then print the answer card, a card per source, the footer."""
        self.close()
        shown = answer_card(answer, plain=self.plain)
        if shown is not None:
            self.console.print(shown)
        # Every source gets a card, even a bare link: it is what `[n]` points at.
        for source in sorted(answer.sources, key=lambda s: s.number):
            self.console.print(card(source, plain=self.plain))
        self.console.print(footer(answer, plain=self.plain))

    def _refresh(self) -> None:
        if self._live is None:
            return
        rows: list[RenderableType] = list(self._lines)
        if self._status:
            rows.append(Text(self._status, "dim"))
        self._live.update(Group(*rows), refresh=True)


def _as_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
