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
from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, ValidationError

#: The environment variable that names a config file (T8.3).
CONFIG_PATH_ENV = "BRIEFING_CONFIG"


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
    #: Seconds before an API call is abandoned (T6.1). Same meaning as
    #: `firecrawl.local.timeout_s`, which was already configurable.
    timeout_s: float = 30.0


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


class GeminiConfig(BaseModel):
    """Gemini LLM configuration (T9.4).

    Extends the LLM config with API key environment variable and model selection.
    Mirrors `firecrawl.api.key_env` pattern.
    """

    model_config = ConfigDict(extra="forbid")

    #: Environment variable holding the Gemini API key (default: GEMINI_API_KEY).
    api_key_env: str = "GEMINI_API_KEY"
    #: The model to use for generation (default: gemini-2.5-flash).
    model: str = "gemini-2.5-flash"


class OpenAIConfig(BaseModel):
    """OpenAI-like LLM configuration (supports OpenAI and NVIDIA).

    Mirrors the GeminiConfig pattern with configurable base_url for OpenAI-compatible
    endpoints like OpenAI's API or NVIDIA's NIM endpoints.
    """

    model_config = ConfigDict(extra="forbid")

    #: Base URL for the API endpoint (default: OpenAI).
    #: Set to NVIDIA endpoint (https://integrate.api.nvidia.com/v1) for NVIDIA models.
    base_url: str = "https://api.openai.com/v1"
    #: Environment variable holding the API key (default: OPENAI_API_KEY).
    api_key_env: str = "OPENAI_API_KEY"
    #: The model to use for generation (default: gpt-4o-mini).
    model: str = "gpt-4o-mini"


class BriefingConfig(BaseModel):
    """Briefing mode's own settings (SPEC section 6).

    Two names, one direction each: `name` is the assistant the briefing is
    *from* (the prompt's "You are {name}"), and `user_name` is the person it is
    *to* — the greeting, and the prompt's instruction to address them. It used
    to be a single field defaulting to "Aether", so the greeting greeted the
    assistant (the T5.4 incident, fixed in T8.5).
    """

    model_config = ConfigDict(extra="forbid")

    #: Who the briefing addresses. Empty by default: nobody is named until the
    #: owner says who, and the assistant's own name never stands in for them.
    user_name: str = ""
    #: Who is speaking. Never used in the greeting.
    name: str = "Aether"
    #: Gemini LLM configuration for --llm and --synth modes (T9.4).
    gemini: GeminiConfig = GeminiConfig()
    #: OpenAI-compatible LLM configuration (OpenAI, NVIDIA NIM, etc.).
    openai: OpenAIConfig = OpenAIConfig()


class TimeoutsConfig(BaseModel):
    """Timing bounds SPEC section 4 implies but names no numbers for (T6.1).

    Both were constructor-only defaults, so a slow Firecrawl or a rate-limited
    host could not be tuned without editing the source.
    """

    model_config = ConfigDict(extra="forbid")

    #: Bound on one URL's fetch, local plus any API retry. `0` opts out, which
    #: is why this is not `float | None`: an absent bound must be asked for.
    per_url_s: float = 30.0
    #: Pause before the one local retry SPEC allows for a transient 5xx.
    transient_backoff_s: float = 0.5


class AskConfig(BaseModel):
    """Ask mode configuration (T9.6).

    Controls the ask mode behavior similar to how the pipeline works for research.
    """

    model_config = ConfigDict(extra="forbid")

    #: Maximum number of search results to consider for fetching (T9.5).
    results: int = 6
    #: Maximum number of pages to fetch and include in the answer.
    pages: int = 3
    #: Overall deadline in seconds for all fetches combined (T9.5).
    deadline_s: float = 10.0


class KnowledgeConfig(BaseModel):
    """Knowledge source adapter configuration (Phase 10).

    Controls which knowledge adapters are enabled and their settings.
    """

    model_config = ConfigDict(extra="forbid")

    #: Whether knowledge adapters are enabled (default: True).
    enabled: bool = True
    #: Enable Wikipedia adapter (default: True).
    wikipedia: bool = True
    #: Enable Stack Exchange adapter (default: True).
    stackexchange: bool = True
    #: Enable DuckDuckGo Instant Answer adapter (default: False).
    ddg_instant: bool = False
    #: Timeout in seconds for each adapter call (default: 5).
    timeout_s: float = 5.0
    #: Custom User-Agent (default: none, uses generic `briefing/<version>`).
    user_agent: str = ""
    #: Environment variable for Stack Exchange API key (default: STACKEXCHANGE_KEY).
    stackexchange_key_env: str = "STACKEXCHANGE_KEY"
    #: Quota floor below which Stack Exchange calls are skipped (default: 20).
    quota_floor: int = 20
    #: Backoff in seconds to wait when API suggests it (default: 60, None = use API value).
    backoff: float | None = 60.0


class PreferredSource(BaseModel):
    """One site whose answers are favoured when a question fits it.

    `topics` decide *when* the site applies: a query that mentions any of them
    (whole words or phrases, case-insensitive) gets an extra `site:` search of
    this domain and its results are promoted. With no topics the site applies to
    every query, but only as a ranking boost (no extra searches), so a long
    topic-less list cannot slow every run down.
    """

    model_config = ConfigDict(extra="forbid")

    domain: str
    topics: list[str] = []
    #: A page that answers matching questions directly. It is added to the
    #: results (and so fetched like any other) whenever a topic matches.
    pinned_url: str = ""


class PreferredSourcesConfig(BaseModel):
    """Sites to prefer when they can answer the question.

    This is separate from `whitelist`, which is the list of domains the cloud
    API may fetch. Preferring a site never lets the API fetch it.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    #: Results per `site:` search.
    extra_results: int = 3
    #: Cap on `site:` searches per query, so many matching sources stay quick.
    max_extra_searches: int = 2
    sources: list[PreferredSource] = []


class Paths(BaseModel):
    """Every file the CLI owns, all under the state dir so a test moves them all
    at once by overriding it."""

    state_dir: Path
    whitelist: Path
    seen_headlines: Path
    log_file: Path
    #: Aether drops yesterday's summary here (T5.4). Missing means "no
    #: continuity", not an error.
    session_summary: Path
    #: Aether records its audio state here (T5.2's gate).
    audio_state: Path


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_topics: list[str] = ["technology", "world news"]
    search: SearchConfig = SearchConfig()
    firecrawl: FirecrawlConfig = FirecrawlConfig()
    whitelist: WhitelistConfig = WhitelistConfig()
    preferred_sources: PreferredSourcesConfig = PreferredSourcesConfig()
    summary: SummaryConfig = SummaryConfig()
    blocklist_topics: list[str] = ["crypto", "bitcoin", "trading", "forex", "stocks"]
    dedup: DedupConfig = DedupConfig()
    briefing: BriefingConfig = BriefingConfig()
    timeouts: TimeoutsConfig = TimeoutsConfig()
    ask: AskConfig = AskConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
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
        session_summary=state_dir / "session-summary.json",
        audio_state=state_dir / "audio-state.json",
    )


def default_config_path() -> Path:
    """Where a discovered config file lives: `<user config dir>/briefing/config.yaml`.

    `platformdirs` follows XDG on Linux and the platform equivalent elsewhere, so
    `XDG_CONFIG_HOME` moves it — which is how the tests point it at a tmp dir
    rather than at the owner's real directory.
    """
    return Path(user_config_dir("briefing")) / "config.yaml"


def discover_config(env: t.Mapping[str, str]) -> Path | None:
    """The config file in force when `--config` was not given, or `None`.

    Precedence (T8.3): `$BRIEFING_CONFIG`, then the platformdirs default. Only a
    file that *exists* is returned, so a missing default is silent (a fresh
    install must not error) and a stale `$BRIEFING_CONFIG` falls through instead
    of bricking every command.

    Reading the env mapping in keeps this a pure function of its argument, so the
    CLI passes `os.environ` and a test passes a dict.
    """
    named = env.get(CONFIG_PATH_ENV, "").strip()
    candidates = [Path(named).expanduser()] if named else []
    candidates.append(default_config_path())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None, state_dir: Path | None = None) -> Config:
    """Load config from YAML at `path`, layered over the defaults.

    Raises ConfigError with a fix hint when the file is unreadable, malformed, or
    contains a field that is not part of the schema (which usually means a typo).

    `path is None` means "no file, defaults only" and reads nothing from disk —
    discovery is `discover_config`'s job, so this stays cheap to call.
    """
    data: dict[str, t.Any] = {}
    if path is not None:
        if not path.exists():
            raise ConfigError(
                f"Config file {path} does not exist.",
                "Check the path, or drop --config to use the built-in defaults.",
            )
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


def resolve_gemini_key(config: Config, env: t.Mapping[str, str]) -> str | None:
    """Read the Gemini API key from the environment at call time.

    Returns None when the variable is absent or empty — callers that actually
    need the key must raise their own error with a fix hint.
    """
    value = env.get(config.briefing.gemini.api_key_env, "")
    return value or None


def resolve_openai_key(config: Config, env: t.Mapping[str, str]) -> str | None:
    """Read the OpenAI API key from the environment at call time.

    Returns None when the variable is absent or empty — callers that actually
    need the key must raise their own error with a fix hint.
    """
    value = env.get(config.briefing.openai.api_key_env, "")
    return value or None
