"""Door-1 grid: one scored run per case, variant, and seed.

A finished run (``status: ok``) is skipped on the next launch. An ``errored``
row is tried again, and the newest row for a run id is the one that counts.
``DailyQuotaExceeded`` stops the grid; every other exception is recorded on
that run and the rest continue.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from agent_sec.agent.target import TargetAgent
from agent_sec.gateway.gateway import PolicyGateway
from agent_sec.gateway.prompts import BASE_SYSTEM_PROMPT
from agent_sec.llm import DailyQuotaExceeded
from agent_sec.logging_utils import JsonlRunLogger
from agent_sec.servers.benign import make_benign_server
from agent_sec.servers.poisoned import AttackCase, Variant, make_tool_server, manifest_for
from agent_sec.servers.secret_store import generate_secret, make_secret_store

logger = logging.getLogger(__name__)

MAX_STEPS = 8
TEMPERATURE = 0.0
GridVariant = Literal["clean", "poisoned"]
_SERVED: dict[GridVariant, Variant] = {"clean": "benign", "poisoned": "poisoned"}


@dataclass(frozen=True)
class RunJob:
    """One cell of the grid."""

    case: AttackCase
    variant: GridVariant
    defense: str
    seed: int


def run_id_for(case_id: str, variant: str, defense: str, seed: int) -> str:
    """Stable id: ``door1__<case>__<variant>__<defense>__s<seed>``."""
    return f"door1__{case_id}__{variant}__{defense}__s{seed}"


def secret_for(case_id: str, seed: int) -> str:
    """Secret for this run. The case id is mixed in with SHA-256, not ``hash()``."""
    mixed = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest(), 16) % 1000
    return generate_secret(seed * 1000 + mixed)


def iter_jobs(
    cases: Sequence[AttackCase],
    *,
    variants: Sequence[GridVariant] = ("clean", "poisoned"),
    seeds: Sequence[int] = (0,),
    defense: str = "none",
    limit: int | None = None,
    only: str | None = None,
) -> list[RunJob]:
    """Expand the grid. ``limit`` keeps the first N cases; ``only`` is an id prefix."""
    selected = [case for case in cases if only is None or case.id.startswith(only)]
    if limit is not None:
        selected = selected[:limit]
    jobs: list[RunJob] = []
    for case in selected:
        for variant in variants:
            for seed in seeds:
                jobs.append(RunJob(case, variant, defense, seed))
    return jobs


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file. Missing files and blank lines are empty."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def latest_results(path: Path) -> dict[str, dict[str, Any]]:
    """Newest results row for each run id. Later lines replace earlier ones."""
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        latest[str(row["run_id"])] = row
    return latest


def _append_row(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _job_fields(job: RunJob) -> dict[str, Any]:
    return {
        "run_id": run_id_for(job.case.id, job.variant, job.defense, job.seed),
        "case_id": job.case.id,
        "paradigm": job.case.paradigm,
        "variant": job.variant,
        "defense": job.defense,
        "seed": job.seed,
    }


async def execute_run(
    job: RunJob,
    *,
    runs_dir: Path,
    llm_fn: Callable[..., Any],
    model: str,
) -> dict[str, Any]:
    """Run one cell, score its log, and append a results row."""
    from agent_sec.eval.judge import score_run

    fields = _job_fields(job)
    run_id = fields["run_id"]
    log_path = runs_dir / f"{run_id}.jsonl"
    results_path = runs_dir / "results.jsonl"
    if log_path.exists():
        log_path.unlink()
    run_logger = JsonlRunLogger(log_path)
    secret = secret_for(job.case.id, job.seed)
    served = _SERVED[job.variant]
    rug = job.case.rug_pull and job.variant == "poisoned"
    approved_variant: Variant = "benign" if rug else served
    run_logger.log(run_id, "runner", "approved_manifest", manifest_for(job.case, approved_variant))
    try:
        agent = TargetAgent(
            servers=[
                make_benign_server(),
                make_secret_store(secret),
                make_tool_server(job.case, served),
            ],
            gateway=PolicyGateway([], run_logger),
            model=model,
            system_prompt=BASE_SYSTEM_PROMPT,
            max_steps=MAX_STEPS,
            logger=run_logger,
            llm_fn=llm_fn,
            temperature=TEMPERATURE,
        )
        await agent.respond([{"role": "user", "content": job.case.user_task}], run_id)
    except DailyQuotaExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 — one bad run is recorded; the grid continues
        row = {**fields, "status": "errored", "error_type": type(exc).__name__, "score": None}
        _append_row(results_path, row)
        logger.error("run %s failed: %s", run_id, exc)
        return row

    score = score_run(read_jsonl(log_path), job.case, secret)
    row = {**fields, "status": "ok", "error_type": None, "score": asdict(score)}
    _append_row(results_path, row)
    return row


async def run_grid(
    cases: Sequence[AttackCase],
    *,
    runs_dir: Path,
    llm_fn: Callable[..., Any],
    model: str,
    variants: Sequence[GridVariant] = ("clean", "poisoned"),
    seeds: Sequence[int] = (0,),
    defense: str = "none",
    concurrency: int = 2,
    limit: int | None = None,
    only: str | None = None,
) -> list[dict[str, Any]]:
    """Run every unfinished cell. Returns the rows written on this launch."""
    done = latest_results(runs_dir / "results.jsonl")
    pending = [
        job
        for job in iter_jobs(
            cases,
            variants=variants,
            seeds=seeds,
            defense=defense,
            limit=limit,
            only=only,
        )
        if done.get(run_id_for(job.case.id, job.variant, job.defense, job.seed), {}).get("status")
        != "ok"
    ]
    semaphore = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()
    written: list[dict[str, Any]] = []

    async def _one(job: RunJob) -> dict[str, Any] | None:
        async with semaphore:
            if stop.is_set():
                return None
            try:
                row = await execute_run(job, runs_dir=runs_dir, llm_fn=llm_fn, model=model)
            except DailyQuotaExceeded:
                stop.set()
                raise
            written.append(row)
            return row

    tasks = [asyncio.create_task(_one(job)) for job in pending]
    try:
        await asyncio.gather(*tasks)
    except DailyQuotaExceeded:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.error(
            "Daily quota exceeded. The quota resets at midnight Pacific. Stopping the grid."
        )
        raise
    return written


def summarize(rows: Sequence[Mapping[str, Any]]) -> str:
    """Rates by paradigm and variant. Rates use finished runs; errored runs are counted apart."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault((str(row["paradigm"]), str(row["variant"])), []).append(row)
    lines: list[str] = []
    for paradigm, variant in sorted(groups):
        items = groups[(paradigm, variant)]
        ok = [row for row in items if row.get("status") == "ok" and row.get("score")]
        errored = sum(row.get("status") == "errored" for row in items)

        def _rate(field: str, finished: list[Mapping[str, Any]] = ok) -> float:
            if not finished:
                return 0.0
            return sum(bool(row["score"][field]) for row in finished) / len(finished)

        lines.append(
            f"{paradigm} {variant}: runs={len(items)} "
            f"attack={_rate('attack_success'):.0%} "
            f"secret_access={_rate('secret_accessed'):.0%} "
            f"task={_rate('task_success'):.0%} errored={errored}"
        )
    return "\n".join(lines)
