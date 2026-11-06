"""Real-world regression: the owner's failing queries, run for real.

Marked `live`, so it only runs under `make live`, with the real network, the
real local Firecrawl and the real Gemini key. It stays red until D10 to D14 are
fixed, and then guards them. It uses built-in config defaults (the suite hides
the owner's config); run `scripts/repro_realworld.py` directly to include it.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "repro_realworld.py"
    spec = importlib.util.spec_from_file_location("repro_realworld", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_owners_real_queries_give_relevant_untruncated_answers() -> None:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.skip("GEMINI_API_KEY is not set; this test exists to exercise the real model")
    problems = _load().diagnose(spend_api=False)
    assert not problems, "\n" + "\n".join(f"  - {p}" for p in problems)
