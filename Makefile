# Convenience targets — `uv run ...` commands are the source of truth.
.PHONY: setup test lint typecheck demo-up demo-down doctor

setup:
	uv sync --all-extras

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src

demo-up:
	uv run sentinelsre demo up

demo-down:
	uv run sentinelsre demo down

doctor:
	uv run sentinelsre doctor
