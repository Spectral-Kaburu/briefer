"""Configuration loading for briefing.

Defaults follow the sketch in SPEC.md section 7. Every value is overridable from
a YAML file, and the state directory is injectable so tests never touch the real
user directory.
"""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


class ConfigError(Exception):
    """Raised when a config file cannot be read or fails validation.

    Carries a plain-English message plus a fix hint so the CLI can show both.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: int = 10
    region: str = "us-en"
    auto_news_detect: bool = True


class LocalFirecrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://localhost:3002"
    timeout_s: float = 30.0
    concurrency: int = 5


class ApiFirecrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://api.firecrawl.dev"
    key_env: str = "FIRECRAWL_API_KEY"
    daily_limit: int = 20


class FirecrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local: LocalFirecrawlConfig = LocalFirecrawlConfig()
    api: ApiFirecrawlConfig = ApiFirecrawlConfig()


class WhitelistConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_add: bool = True
    path: str = "whitelist.yaml"
    prefer_api_for_whitelisted: bool = False


class SummaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "extractive"
    sentences: int = 2


class DedupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline_ttl_days: int = 7
    schedule_window_hours: int = 24


class Paths(BaseModel):
    state_dir: Path
    whitelist: Path
    seen_headlines: Path
    log_file: Path


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_topics: list[str] = ["technology", "world news"]
    search: SearchConfig = SearchConfig()
    firecrawl: FirecrawlConfig = FirecrawlConfig()
    whitelist: WhitelistConfig = WhitelistConfig()
    summary: SummaryConfig = SummaryConfig()
    blocklist_topics: list[str] = ["crypto", "bitcoin", "trading", "forex", "stocks"]
    dedup: DedupConfig = DedupConfig()
    paths: Paths


def _default_state_dir() -> Path:
    """User-level state dir; overridable for tests via load_config(state_dir=)."""
    override = os.environ.get("BRIEFING_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "briefing"


def _build_paths(state_dir: Path) -> Paths:
    return Paths(
        state_dir=state_dir,
        whitelist=state_dir / "whitelist.yaml",
        seen_headlines=state_dir / "seen_headlines.json",
        log_file=state_dir / "briefing.log",
    )


def load_config(path: Path | None = None, state_dir: Path | None = None) -> Config:
    """Load config from YAML at `path`, layered over the defaults.

    Raises ConfigError with a fix hint when the file is unreadable, malformed, or
    contains a field that is not part of the schema (which usually means a typo).
    """
    data: dict[str, t.Any] = {}
    if path is not None:
        if not path.exists():
            # A named-but-absent file is not fatal: defaults still load.
            data = {}
        else:
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ConfigError(
                    f"Config file {path} is not valid YAML.",
                    "Fix the syntax error reported by the parser, or delete the file to use defaults.",
                ) from exc
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ConfigError(
                    f"Config file {path} must contain a YAML mapping, got {type(loaded).__name__}.",
                    "Top-level entries should look like `news_topics: [...]`, not a bare list.",
                )
            data = loaded

    if state_dir is None:
        state_dir = _default_state_dir()
    data = {**data, "paths": _build_paths(state_dir)}

    try:
        return Config(**data)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"
            for e in exc.errors()
        )
        raise ConfigError(
            f"Config file {path} has invalid values ({detail}).",
            "Check the field names and types against SPEC.md section 7.",
        ) from exc


def resolve_api_key(config: Config, env: t.Mapping[str, str]) -> str | None:
    """Read the Firecrawl API key from the environment at call time.

    Returns None when the variable is absent or empty — callers that actually
    need the key must raise their own error with a fix hint.
    """
    value = env.get(config.firecrawl.api.key_env, "")
    return value or None
