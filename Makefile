.PHONY: check test fmt live

check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/pytest

test:
	.venv/bin/pytest

fmt:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .

# The real ddgs and the real local Firecrawl: the four `@pytest.mark.live` tests
# in tests/test_live.py. These hit the network, so `check` and `test` never run
# them — this target is the only way to, and it is deliberately not a
# dependency of either. CLAUDE.md permits live checks against the local Firecrawl
# and live ddgs when they are marked and gated, which these are.
live:
	BRIEFING_LIVE=1 .venv/bin/pytest -m live
