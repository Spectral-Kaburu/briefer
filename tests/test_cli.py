"""Smoke tests for the CLI skeleton (T0.1)."""

import sys

from typer.testing import CliRunner

from briefing.cli import app

runner = CliRunner()


def test_help_exits_zero_and_lists_research() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "research" in result.stdout


def test_console_script_help_exits_zero() -> None:
    """The T0.1 acceptance check: the installed `briefing` entry point runs."""
    import subprocess
    from pathlib import Path

    exe = Path(sys.executable).parent / "briefing"
    proc = subprocess.run(
        [str(exe), "--help"], check=False, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert "research" in proc.stdout


def test_research_takes_a_query() -> None:
    result = runner.invoke(app, ["latest news in kenya"])
    assert result.exit_code == 0
    assert "latest news in kenya" in result.stdout
