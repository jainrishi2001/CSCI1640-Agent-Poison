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

from tqdm import tqdm

from agent_sec.config import get_settings
from agent_sec.eval.runner import RunJob, latest_results, run_grid, summarize
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


def _progress_line(
    event: str,
    job: RunJob | None,
    count: int,
    total: int,
    row: dict[str, Any] | None,
    bar: tqdm | None,
) -> None:
    """One flushed line per run so a captured terminal shows the live count."""
    if event == "plan":
        print(f"grid: {total} runs to do", flush=True)
        return
    assert job is not None
    label = f"{job.case.id} {job.variant}"
    if event == "start":
        print(f"[{count}/{total}] running {label}", flush=True)
        if bar is not None:
            bar.set_postfix_str(f"running {label}", refresh=False)
        return
    status = "skipped" if row is None else str(row["status"])
    detail = ""
    score = None if row is None else row.get("score")
    if score:
        detail = f" attack={score['attack_success']} task={score['task_success']}"
    elif row is not None and row.get("error_type"):
        detail = f" {row['error_type']}"
    print(f"[{count}/{total}] {status} {label}{detail}", flush=True)
    if bar is not None:
        bar.update(1)
        bar.set_postfix_str(f"{status} {label}", refresh=False)


async def launch(
    args: argparse.Namespace,
    *,
    llm_fn: Any,
    model: str,
    runs_dir: Path,
) -> int:
    """Run the selected cells and print rates from the latest results row of each run."""
    cases = load_cases(args.cases)
    bar = tqdm(total=0, file=sys.stderr, mininterval=1, dynamic_ncols=True, unit="run")

    def progress(
        event: str,
        job: RunJob | None,
        count: int,
        total: int,
        row: dict[str, Any] | None,
    ) -> None:
        if event == "plan":
            bar.reset(total=total)
        _progress_line(event, job, count, total, row, bar)

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
            progress=progress,
        )
    except DailyQuotaExceeded:
        code = 1
    else:
        code = 0
    finally:
        bar.close()
    text = summarize(list(latest_results(runs_dir / "results.jsonl").values()))
    print(text or "no runs recorded", flush=True)
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
