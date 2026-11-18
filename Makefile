.PHONY: setup test lint smoke

setup:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

smoke:
	uv run python scripts/smoke_llm.py
