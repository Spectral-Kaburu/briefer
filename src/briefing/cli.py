"""briefing command-line interface."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Search the web, extract pages, and summarize results.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def research(query: str = typer.Argument(..., help="Question or topic to research.")) -> None:
    """Search the web for QUERY and summarize the results."""
    typer.echo(f"researching: {query}")
