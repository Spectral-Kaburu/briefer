"""Tests for the `briefing run` command (T5.4; SPEC section 6).

`briefing run` is the scheduled briefing: gather headlines for every unblocked
topic, drop the ones already seen, synthesise, and speak. The command is only
wiring, so everything it depends on is substituted — the searcher, the gate, the
speaker and above all the runner. Nothing here spawns `voice-tts` (CLAUDE.md);
the dry-run tests assert the command is *printed* rather than executed.

The behaviours worth pinning are the order and the gates: a briefing that speaks
over Aether, that repeats yesterday, or that says nothing when all fetching
failed, is worse than no briefing at all.
"""

from __future__ import annotations

import types
import typing as t
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

import briefing.cli as cli_module
from briefing.cli import app

runner = CliRunner()


class FakeSearch:
    """Stands in for topic → headlines, so the search never runs."""

    def __init__(self, topics: dict[str, list[str]] | None = None) -> None:
        self.topics = topics or {}
        self.asked: list[str] = []

    async def headlines(self, topic: str) -> list[str]:
        self.asked.append(topic)
        return self.topics.get(topic, [])


class FakeSpeaker:
    """Records the headlines it was given and returns canned text."""

    def __init__(self, text: str = "Good morning. Here are the headlines.") -> None:
        self.text = text
        self.calls: list[list[str]] = []

    async def briefing(self, headlines: t.Sequence[str]) -> t.Any:
        self.calls.append(list(headlines))
        return types.SimpleNamespace(text=self.text, fallback=False)


class FakeRunner:
    """Records argv and answers with the status it was told to."""

    def __init__(self, status: int = 0) -> None:
        self.status = status
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str]) -> int:
        self.calls.append(argv)
        return self.status


class FakeAudio:
    def __init__(self, speaking: bool = False) -> None:
        self.speaking_flag = speaking

    async def speaking(self) -> bool:
        return self.speaking_flag


def _write_config(tmp_path: Path, topics: list[str], **extra: t.Any) -> Path:
    path = tmp_path / "briefing.yaml"
    lines = ["news_topics:"]
    lines += [f"  - {topic}" for topic in topics]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- gathering headlines ---------------------------------------------------


def test_run_gathers_every_configured_topic(monkeypatch: t.Any, tmp_path: Path) -> None:
    search = FakeSearch({"technology": ["A chip story"], "world news": ["A world story"]})
    monkeypatch.setattr(cli_module, "_SEARCH", lambda *a, **k: search)
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(
        app, ["run", "--config", str(_write_config(tmp_path, ["technology", "world news"]))]
    )
    assert result.exit_code == 0, result.stderr
    assert sorted(search.asked) == ["technology", "world news"]


def test_run_does_not_search_a_blocked_topic(monkeypatch: t.Any, tmp_path: Path) -> None:
    """SPEC section 6: no crypto / finance speculation in the morning briefing.

    The blocklist is briefing-mode-only, and this is where it bites: searching a
    blocked topic would put crypto in the spoken briefing.
    """
    search = FakeSearch({"bitcoin": ["A coin story"], "technology": ["A chip story"]})
    monkeypatch.setattr(cli_module, "_SEARCH", lambda *a, **k: search)
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(
        app, ["run", "--config", str(_write_config(tmp_path, ["bitcoin", "technology"]))]
    )
    assert result.exit_code == 0, result.stderr
    assert "bitcoin" not in search.asked
    assert "technology" in search.asked


def test_run_speaks_the_headlines_it_gathered(monkeypatch: t.Any, tmp_path: Path) -> None:
    speaker = FakeSpeaker()
    monkeypatch.setattr(cli_module, "_SPEAKER", lambda *a, **k: speaker)
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story", "Another"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert speaker.calls == [["A chip story", "Another"]]


# --- dedup (T5.1) ----------------------------------------------------------


def test_a_headline_already_seen_is_not_repeated(monkeypatch: t.Any, tmp_path: Path) -> None:
    """SPEC section 6: a story that ran yesterday is not repeated today."""
    from briefing.briefing import DedupStore

    store = DedupStore(tmp_path / "seen_headlines.json")
    store.record("A chip story")

    speaker = FakeSpeaker()
    monkeypatch.setattr(cli_module, "_SPEAKER", lambda *a, **k: speaker)
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story", "A fresh story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert speaker.calls == [["A fresh story"]]


def test_a_synthesised_headline_is_recorded_as_seen(monkeypatch: t.Any, tmp_path: Path) -> None:
    """The briefing must not repeat tomorrow what it said today.

    Recording only after a successful run matters too: a run that failed to
    speak must leave the headline unseen so it is retried.
    """
    from briefing.briefing import DedupStore

    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert DedupStore(tmp_path / "seen_headlines.json").seen("A chip story")


def test_nothing_seen_after_a_failed_run(monkeypatch: t.Any, tmp_path: Path) -> None:
    """A briefing that could not be spoken must be retried, not lost."""
    from briefing.briefing import DedupStore

    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner(status=3))
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code != 0
    assert not DedupStore(tmp_path / "seen_headlines.json").seen("A chip story")


# --- the gates (T5.2) ------------------------------------------------------


def test_a_briefing_that_already_fired_does_not_fire_again(
    monkeypatch: t.Any, tmp_path: Path
) -> None:
    """SPEC section 6's 24h schedule gate.

    The timer may fire twice; the briefing must not be spoken twice.
    """
    from briefing.briefing import DedupStore

    now = datetime.now(UTC)
    store = DedupStore(tmp_path / "seen_headlines.json")
    store.mark_fired(store.schedule_id(now), now=now)

    speaker = FakeSpeaker()
    monkeypatch.setattr(cli_module, "_SPEAKER", lambda *a, **k: speaker)
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert speaker.calls == []


def test_a_briefing_does_not_speak_over_aether(monkeypatch: t.Any, tmp_path: Path) -> None:
    """SPEC section 6: never fire while `audio.state == "speaking"`."""
    monkeypatch.setattr(cli_module, "_AUDIO", lambda *a, **k: FakeAudio(speaking=True))
    speaker = FakeSpeaker()
    monkeypatch.setattr(cli_module, "_SPEAKER", lambda *a, **k: speaker)
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert speaker.calls == []


def test_a_refused_briefing_says_why(monkeypatch: t.Any, tmp_path: Path) -> None:
    """A silent refusal is indistinguishable from a broken briefing.

    Every seam is patched even though the gate is expected to stop the run
    first: a regression in the gate must fail this test, not fall through to a
    real search and a real `voice-tts`.
    """
    monkeypatch.setattr(cli_module, "_AUDIO", lambda *a, **k: FakeAudio(speaking=True))
    speaker = FakeSpeaker()
    monkeypatch.setattr(cli_module, "_SPEAKER", lambda *a, **k: speaker)
    fake_runner = FakeRunner()
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: fake_runner)
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert "speaking" in result.stdout.lower()
    assert speaker.calls == [] and fake_runner.calls == []


# --- the fallback (SPEC section 6) ----------------------------------------


def test_a_run_with_no_headlines_still_speaks(monkeypatch: t.Any, tmp_path: Path) -> None:
    """SPEC section 6: if all fetching fails, a plain session greeting.

    Silence is the one outcome a morning briefing must not produce.
    """
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": []}),
    )
    fake = FakeRunner()
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: fake)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert fake.calls, "a greeting was not spoken"
    assert fake.calls[0][-1].strip(), "nothing to say"


# --- delivery --------------------------------------------------------------


def test_a_real_run_speaks_through_the_runner(monkeypatch: t.Any, tmp_path: Path) -> None:
    """The whole point of `briefing run`."""
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    fake = FakeRunner()
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: fake)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code == 0, result.stderr
    assert len(fake.calls) == 1
    assert fake.calls[0][:5] == ["voice-tts", "-s", "aether", "-p", "high"]


def test_a_dry_run_prints_the_command_and_speaks_nothing(
    monkeypatch: t.Any, tmp_path: Path
) -> None:
    """`--dry-run` is the owner's check before a voice says it."""
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    fake = FakeRunner()
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: fake)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(
        app,
        ["run", "--dry-run", "--config", str(_write_config(tmp_path, ["technology"]))],
    )
    assert result.exit_code == 0, result.stderr
    assert fake.calls == []
    assert "voice-tts" in result.stdout
    assert "A chip story" in result.stdout or "aether" in result.stdout
    assert not (tmp_path / "seen_headlines.json").exists(), "a dry run remembered headlines"


def test_a_dry_run_prints_the_spoken_text(monkeypatch: t.Any, tmp_path: Path) -> None:
    """The text matters more than the command: it is what will be said."""
    monkeypatch.setattr(cli_module, "_SPEAKER", lambda *a, **k: FakeSpeaker("A canned briefing."))
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner())
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(
        app,
        ["run", "--dry-run", "--config", str(_write_config(tmp_path, ["technology"]))],
    )
    assert result.exit_code == 0, result.stderr
    assert "A canned briefing." in result.stdout


def test_a_refused_briefing_does_not_mark_fired(monkeypatch: t.Any, tmp_path: Path) -> None:
    """Marking fired would suppress tomorrow's briefing for today's refusal."""
    from briefing.briefing import DedupStore

    monkeypatch.setattr(cli_module, "_AUDIO", lambda *a, **k: FakeAudio(speaking=True))
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    fake_runner = FakeRunner()
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: fake_runner)
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    config = _write_config(tmp_path, ["technology"])
    file_store = DedupStore(tmp_path / "seen.json")
    monkeypatch.setattr(cli_module, "_DEDUP", lambda *a, **k: file_store)
    result = runner.invoke(app, ["run", "--config", str(config)])
    assert fake_runner.calls == []
    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / "seen.json").exists() or not file_store.should_fire(
        file_store.schedule_id(datetime.now(UTC))
    )


def test_a_failed_delivery_is_reported_with_a_hint(monkeypatch: t.Any, tmp_path: Path) -> None:
    """A broken audio path must read as instructions, not a traceback."""
    monkeypatch.setattr(
        cli_module,
        "_SEARCH",
        lambda *a, **k: FakeSearch({"technology": ["A chip story"]}),
    )
    monkeypatch.setattr(cli_module, "_RUNNER", lambda: FakeRunner(status=3))
    monkeypatch.setattr(cli_module, "_STATE_DIR", lambda: tmp_path)
    result = runner.invoke(app, ["run", "--config", str(_write_config(tmp_path, ["technology"]))])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert result.stderr.strip()
