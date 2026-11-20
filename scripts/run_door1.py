"""Run the door-1 grid against the configured target model.

Reads ``TARGET_MODEL`` from the environment. Logs land in ``runs/door1/``.
The test suite calls ``launch`` with a scripted model and does not run this
script, so ``make test`` never spends quota.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from agent_sec.config import get_settings
from agent_sec.eval.runner import latest_results, run_grid, summarize
from agent_sec.llm import DailyQuotaExceeded, chat
from agent_sec.servers.poisoned import load_cases

_VARIANTS = ("clean", "poisoned")


def _variants(value: str) -> tuple[str, ...]:
    parts = tuple(item.strip() for item in value.split(",") if item.strip())
    unknown = [item for item in parts if item not in _VARIANTS]
    if not parts or unknown:
        raise argparse.ArgumentTypeError(
            "variants must be a comma-separated list of clean,poisoned"
        )
    return parts


def _seeds(value: str) -> tuple[int, ...]:
    raw = [item.strip() for item in value.split(",") if item.strip()]
    try:
        seeds = tuple(int(item) for item in raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if not seeds:
        raise argparse.ArgumentTypeError("provide at least one seed")
    return seeds


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the door-1 CLI. Defaults match the full no-defense grid."""
    parser = argparse.ArgumentParser(description="Run the door-1 no-defense grid.")
    parser.add_argument("--cases", default="attacks/door1_cases.yaml")
    parser.add_argument("--variants", type=_variants, default=_VARIANTS)
    parser.add_argument("--seeds", type=_seeds, default=(0,))
    parser.add_argument("--limit", type=_positive, default=None)
    parser.add_argument("--only", default=None, help="Run case ids with this prefix.")
    parser.add_argument("--concurrency", type=_positive, default=2)
    return parser.parse_args(argv)


async def launch(
    args: argparse.Namespace,
    *,
    llm_fn: Any,
    model: str,
    runs_dir: Path,
) -> int:
    """Run the selected cells and print rates from the latest results row of each run."""
    cases = load_cases(args.cases)
    try:
        await run_grid(
            cases,
            runs_dir=runs_dir,
            llm_fn=llm_fn,
            model=model,
            variants=args.variants,
            seeds=args.seeds,
            concurrency=args.concurrency,
            limit=args.limit,
            only=args.only,
        )
    except DailyQuotaExceeded:
        code = 1
    else:
        code = 0
    text = summarize(list(latest_results(runs_dir / "results.jsonl").values()))
    print(text or "no runs recorded")
    return code


def main(argv: list[str] | None = None) -> int:
    """Load settings and call the real model. Exits 1 when the daily quota stops the grid."""
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    settings = get_settings()
    return asyncio.run(
        launch(
            args,
            llm_fn=chat,
            model=settings.target_model,
            runs_dir=Path(settings.runs_dir) / "door1",
        )
    )


if __name__ == "__main__":
    sys.exit(main())
