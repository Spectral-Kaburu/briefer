.PHONY: check test fmt

check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/pytest

test:
	.venv/bin/pytest

fmt:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .
