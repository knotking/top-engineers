"""Command line entry points, in the plan's order of work."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import DISPLAY_SIZE, load_config
from .gh.checkpoint import finalize
from .gh.client import GraphQLClient
from .identity import Actors, build_candidates, write_candidates
from .metrics.compute import compute_all
from .pipeline.hydrate import mean_child_counts, run_hydrate
from .pipeline.skim import bot_share, hydration_targets, run_skim
from .rawio import read_jsonl_gz, verify_byte_stability, write_jsonl_gz
from .scoring.score import score_cohort
from .store.ingest import load_cohort, load_pr_batch, load_skim_batch
from .store.load import connect, get_meta, init_schema, set_meta, table_count
from .store.load_from_raw import COHORT_FILE, PRS_FILE, SKIM_FILE, restore
from .store.serving import build_serving, persist_scores

log = logging.getLogger("top_engineers")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def cmd_skim(args) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    actors = Actors.load(cfg.actors_path)
    con = connect(cfg.db_path)
    init_schema(con)

    async with GraphQLClient() as client:
        share = await bot_share(client, cfg)
        log.info(
            "bot share: %d of %d PRs bot-authored (%.1f%%)",
            share["bot_authored"], share["total"],
            100 * share["bot_authored"] / max(share["total"], 1),
        )
        result = await run_skim(client, cfg, actors)

    records = list(result["records"])
    load_skim_batch(con, records)
    load_cohort(con, result["ranking"])

    write_jsonl_gz(cfg.raw_dir / SKIM_FILE, records)
    write_jsonl_gz(cfg.raw_dir / COHORT_FILE, result["ranking"], sort_key="rank")
    finalize(result["scratch"])  # only after canonical files exist

    targets = hydration_targets(result["ranking"])
    stats = result["stats"]
    stats["bot_share"] = share
    stats["hydration_targets"] = len(targets)
    set_meta(con, "skim_stats", json.dumps(stats))
    set_meta(con, "window", f"{cfg.since.isoformat()}..{cfg.until.isoformat()}")

    print(f"\nskimmed {len(records)} PRs; {len(result['ranking'])} people ranked")
    print(f"variant yield: {json.dumps(stats['variant_yield'], indent=2)}")
    if stats["uncapped_days"]:
        print(f"!! DATA LOSS on {len(stats['uncapped_days'])} day(s): {stats['uncapped_days']}")
    print(f"\ntop {DISPLAY_SIZE} by participation:")
    for row in result["ranking"][:DISPLAY_SIZE]:
        print(f"  {row['rank']:3d}. {row['login']:24s} authored={row['authored_n']:4d} reviewed={row['reviewed_n']:4d}")
    print(f"\nhydration targets: {len(targets)} PRs")
    return 0


async def cmd_hydrate(args) -> int:
    cfg = load_config()
    con = connect(cfg.db_path)
    init_schema(con)

    ranking = read_jsonl_gz(cfg.raw_dir / COHORT_FILE)
    if not ranking:
        print("no cohort found -- run `skim` first", file=sys.stderr)
        return 1
    targets = hydration_targets(ranking)
    if args.limit:
        targets = targets[: args.limit]

    async with GraphQLClient() as client:
        stats = await run_hydrate(client, cfg, targets, on_batch=lambda b: load_pr_batch(con, b))

    from .rawio import iter_jsonl

    prs = list(iter_jsonl(cfg.checkpoint_dir / "hydrate.jsonl"))
    digest = write_jsonl_gz(cfg.raw_dir / PRS_FILE, prs)
    if not verify_byte_stability(cfg.raw_dir / PRS_FILE):
        print("!! byte-stability assertion FAILED", file=sys.stderr)
        return 1
    finalize(cfg.checkpoint_dir / "hydrate.jsonl")

    stats["mean_children_per_pr"] = mean_child_counts(stats["child_counts"])
    stats.pop("child_counts", None)
    stats["raw_sha256"] = digest
    set_meta(con, "hydrate_stats", json.dumps(stats))
    print(json.dumps(stats, indent=2))
    return 0


def cmd_score(args) -> int:
    cfg = load_config()
    con = connect(cfg.db_path)
    init_schema(con)
    actors = Actors.load(cfg.actors_path)

    _check_autonomy_coverage(con)

    cohort_rows = con.execute(
        "SELECT login, in_normalise, in_display FROM cohort"
    ).fetchall()
    pool = {r[0] for r in cohort_rows if r[1]}
    people = compute_all(con, actors, cfg.until, cohort=pool or None)
    if not people:
        print("no people computed -- run `skim` and `hydrate` first", file=sys.stderr)
        return 1

    results = score_cohort(people, pool=pool or set(people))
    persist_scores(con, results, people)
    build_serving(con, results, people, meta={
        "window": f"{cfg.since.isoformat()}..{cfg.until.isoformat()}",
        "display_size": str(DISPLAY_SIZE),
        "normalise_pool": str(len(pool)),
    })

    ranked = sorted([r for r in results.values() if r["rank"]], key=lambda r: r["rank"])
    print(f"\nscored {len(results)} people; {len(ranked)} ranked\n")
    print(f"{'#':>3} {'band':>4}  {'login':24s} {'score':>6}  {'interval':>15}  n")
    for r in ranked[:DISPLAY_SIZE]:
        print(f"{r['rank']:3d} {r['tie_band']:4d}  {r['login']:24s} {r['score']:6.3f}  "
              f"[{r['ci_low']:.3f},{r['ci_high']:.3f}]  a={r['authored_n']} r={r['reviewed_n']} d={r['dispatched_n']}")
    return 0


def _check_autonomy_coverage(con, floor: float = 0.80) -> None:
    """Alert if the upstream PR template changed.

    Autonomy is the strongest identity signal available and no heuristic came close to it,
    so a silent drop in coverage would quietly degrade every builder metric.
    """
    row = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE autonomy IS NOT NULL AND autonomy <> 'unknown')
        FROM raw_pr WHERE hydrated
    """).fetchone()
    total, declared = row[0] or 0, row[1] or 0
    if not total:
        return
    coverage = declared / total
    if coverage < floor:
        log.warning(
            "autonomy coverage %.0f%% is below the %.0f%% floor (%d/%d) -- has the PR "
            "template changed upstream? Builder metrics depend on this signal.",
            coverage * 100, floor * 100, declared, total,
        )
    else:
        log.info("autonomy coverage %.0f%% (%d/%d)", coverage * 100, declared, total)


def cmd_restore(args) -> int:
    cfg = load_config()
    con = connect(cfg.db_path)
    stats = restore(con, cfg.raw_dir)
    print(json.dumps(stats, indent=2))
    for t in ("raw_pr", "raw_review", "raw_commit", "raw_file", "cohort"):
        print(f"  {t:16s} {table_count(con, t)}")
    return 0


def cmd_candidates(args) -> int:
    cfg = load_config()
    con = connect(cfg.db_path)
    actors = Actors.load(cfg.actors_path)
    rows = con.execute("""
        SELECT author_login, any_value(author_type), count(*) FROM raw_pr
        WHERE author_login IS NOT NULL GROUP BY 1
    """).fetchall()
    cands = build_candidates([(r[0], r[1], r[2]) for r in rows], actors)
    out = Path("actors.candidates.yaml")
    write_candidates(out, cands)
    print(f"wrote {len(cands)} candidates to {out} -- REVIEW BY HAND before promoting")
    return 0


def cmd_verify(args) -> int:
    cfg = load_config()
    ok = True
    for name in (PRS_FILE, SKIM_FILE, COHORT_FILE):
        path = cfg.raw_dir / name
        if not path.exists():
            print(f"  {name:20s} MISSING")
            continue
        sort_key = "rank" if name == COHORT_FILE else "number"
        stable = verify_byte_stability(path, sort_key=sort_key)
        ok &= stable
        print(f"  {name:20s} {'byte-stable' if stable else 'NOT STABLE'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="top-engineers")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("skim", help="Phase 1: sweep and rank participation")
    h = sub.add_parser("hydrate", help="Phase 2: deep-fetch the cohort's PRs")
    h.add_argument("--limit", type=int, default=None)
    sub.add_parser("score", help="Compute metrics, score, build serving layer")
    sub.add_parser("restore", help="Rebuild the database from committed raw")
    sub.add_parser("candidates", help="Regenerate the actors review queue")
    sub.add_parser("verify", help="Assert raw files are byte-stable")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.cmd == "skim":
        return asyncio.run(cmd_skim(args))
    if args.cmd == "hydrate":
        return asyncio.run(cmd_hydrate(args))
    return {
        "score": cmd_score,
        "restore": cmd_restore,
        "candidates": cmd_candidates,
        "verify": cmd_verify,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
