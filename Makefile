.PHONY: setup test lint smoke demo door1-pilot door1

setup:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

smoke:
	uv run python scripts/smoke_llm.py

demo:
	uv run python scripts/demo_task.py

door1-pilot:
	uv run python scripts/run_door1.py --limit 4 --variants clean,poisoned --concurrency 1

door1:
	uv run python scripts/run_door1.py --concurrency 2
