"""Phase 2 -- deep-fetch only the PRs the cohort authored or reviewed.

MEASURE FIRST: this workload is LATENCY-bound, not budget-bound. Median GraphQL RTT measured
4.4s against 0.8s pacing, so latency is ~85% of wall-clock and concurrency is the lever.

Budget is nonetheless watched, because concurrency converts a latency problem into a budget
problem: at the tuned page sizes the whole job costs ~0.56 points/PR and fits inside a single
5,000-point window. Untuned it is ~1.56 and spans three, with two 45-minute refill waits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..config import BATCH_REQUEUE_LIMIT, Config, HYDRATE_BATCH
from ..gh.checkpoint import Checkpoint
from ..gh.client import GraphQLClient
from ..gh.paginate import drain_overflow
from ..gh.queries import hydrate_query
from ..rawio import iter_jsonl

log = logging.getLogger(__name__)


def batched(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def fetch_batch(client: GraphQLClient, numbers: list[int]) -> list[dict[str, Any]]:
    data = await client.execute(hydrate_query(numbers))
    repo = data.get("repository") or {}
    prs = []
    for i in range(len(numbers)):
        pr = repo.get(f"p{i}")
        if pr:
            prs.append(pr)
    # ALWAYS drain overflow. Silent truncation is biased: it deflates exactly the busiest,
    # most-reviewed PRs, which are the ones the reviewer metrics depend on.
    for pr in prs:
        drained = await drain_overflow(client, pr)
        if drained:
            log.debug("pr %s drained overflow: %s", pr["number"], drained)
    return prs


async def run_hydrate(
    client: GraphQLClient,
    cfg: Config,
    targets: list[int],
    on_batch: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    """Hydrate `targets`, resuming from the checkpoint and loading per batch."""
    cfg.ensure_dirs()
    scratch = cfg.checkpoint_dir / "hydrate.jsonl"

    already = {rec["number"] for rec in iter_jsonl(scratch) if "number" in rec}
    todo = [n for n in targets if n not in already]
    if already:
        log.info("resuming hydrate: %d done, %d remaining", len(already), len(todo))

    stats: dict[str, Any] = {
        "targets": len(targets),
        "resumed": len(already),
        "fetched": 0,
        "batches": 0,
        "child_counts": {},
        "failed_prs": [],
    }

    with Checkpoint(scratch, key="number", on_flush=_flush_adapter(on_batch)) as ckpt:
        batches = batched(todo, HYDRATE_BATCH)
        # A bounded worker pool, not one task per batch: the limiter paces, but spawning
        # thousands of coroutines up front just queues memory.
        queue: asyncio.Queue[list[int]] = asyncio.Queue()
        for b in batches:
            queue.put_nowait(b)

        lock = asyncio.Lock()

        attempts: dict[tuple[int, ...], int] = {}

        async def worker() -> None:
            while True:
                try:
                    batch = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                key = tuple(batch)
                try:
                    prs = await fetch_batch(client, batch)
                except Exception as exc:
                    # Dropping a batch here would be SILENT data loss within the run. Re-queue
                    # it, and if it still will not land, record the PR numbers so the operator
                    # knows exactly what is missing rather than inferring it from a short count.
                    attempts[key] = attempts.get(key, 0) + 1
                    if attempts[key] <= BATCH_REQUEUE_LIMIT:
                        log.warning(
                            "batch of %d failed (%s); re-queueing (attempt %d/%d)",
                            len(batch), exc, attempts[key], BATCH_REQUEUE_LIMIT,
                        )
                        queue.put_nowait(batch)
                    else:
                        log.error(
                            "DATA LOSS: batch of %d PRs failed %d times, giving up: %s",
                            len(batch), attempts[key], batch,
                        )
                        async with lock:
                            stats["failed_prs"].extend(batch)
                    continue
                async with lock:
                    for pr in prs:
                        ckpt.add(pr)
                    stats["fetched"] += len(prs)
                    stats["batches"] += 1
                    _accumulate_counts(stats["child_counts"], prs)
                    if stats["batches"] % 10 == 0:
                        log.info(
                            "hydrated %d/%d PRs | %d pts | conc=%d | backoffs=%d",
                            stats["fetched"], len(todo),
                            client.limiter.points_spent,
                            client.concurrency.limit,
                            client.limiter.backoffs,
                        )

        workers = [asyncio.create_task(worker()) for _ in range(client.concurrency.cap)]
        await asyncio.gather(*workers)

    stats["points_spent"] = client.limiter.points_spent
    stats["requests"] = client.limiter.requests
    stats["backoffs"] = client.limiter.backoffs
    stats["scratch"] = str(scratch)
    if stats["failed_prs"]:
        log.error(
            "%d PRs could not be hydrated; re-run to pick them up from the checkpoint",
            len(stats["failed_prs"]),
        )
    if stats["fetched"]:
        stats["points_per_pr"] = round(client.limiter.points_spent / stats["fetched"], 3)
    return stats


def _flush_adapter(on_batch):
    if on_batch is None:
        return None

    def _inner(batch: list[dict[str, Any]]) -> None:
        # Raw is already on disk by the time this runs -- that ordering is the whole point.
        on_batch(batch)

    return _inner


def _accumulate_counts(acc: dict[str, Any], prs: list[dict[str, Any]]) -> None:
    """Track mean children per PR so page-size tuning can be regression-checked."""
    from .records import child_counts

    for pr in prs:
        for field, n in child_counts(pr).items():
            slot = acc.setdefault(field, {"total": 0, "prs": 0})
            slot["total"] += n
            slot["prs"] += 1


def mean_child_counts(acc: dict[str, Any]) -> dict[str, float]:
    return {
        field: round(slot["total"] / slot["prs"], 2)
        for field, slot in acc.items()
        if slot["prs"]
    }
