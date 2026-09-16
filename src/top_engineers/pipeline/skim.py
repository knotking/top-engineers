"""Phase 1 -- cheap sweep, rank by participation, take the cohort.

Instrument marginal yield per variant and delete what earns nothing: `bugfix` and `hotfix`
both measured 0 and are already gone from config.TITLE_VARIANTS. This records yield per
variant on every run so the next dead variant is visible rather than inferred.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from ..config import Config, DISPLAY_SIZE, NORMALISE_POOL_SIZE, TITLE_VARIANTS
from ..gh.checkpoint import Checkpoint
from ..gh.client import GraphQLClient
from ..gh.paginate import count_for, iter_search
from ..gh.queries import search_query
from ..identity import Actors

log = logging.getLogger(__name__)


async def bot_share(client: GraphQLClient, cfg: Config) -> dict[str, int]:
    """One count query per side. Never download rows you intend to discard."""
    from ..config import BOT_AUTHORS, REPO_NAME, REPO_OWNER

    window = f"merged:{cfg.since.isoformat()}..{cfg.until.isoformat()}"
    base = f"repo:{REPO_OWNER}/{REPO_NAME} is:pr is:merged {window}"
    bots = " ".join(f"-author:{a}" for a in BOT_AUTHORS)
    total = await count_for(client, base)
    human = await count_for(client, f"{base} {bots}")
    return {"total": total, "excluding_bots": human, "bot_authored": total - human}


async def run_skim(client: GraphQLClient, cfg: Config, actors: Actors) -> dict[str, Any]:
    cfg.ensure_dirs()
    scratch = cfg.checkpoint_dir / "skim.jsonl"
    stats: dict[str, Any] = {"variant_yield": {}, "slices": [], "uncapped_days": []}

    seen_numbers: set[int] = set()
    with Checkpoint(scratch, key="number") as ckpt:
        if ckpt.seen:
            log.info("resuming skim: %d PRs already checkpointed", len(ckpt.seen))
            seen_numbers |= {int(n) for n in ckpt.seen}

        for variant in TITLE_VARIANTS:
            before = len(seen_numbers)
            q = search_query(variant, cfg.since.isoformat(), cfg.until.isoformat())
            declared = await count_for(client, q)
            fetched = 0
            async for node in iter_search(client, variant, cfg.since, cfg.until, stats):
                fetched += 1
                number = node["number"]
                if number in seen_numbers:
                    continue
                seen_numbers.add(number)
                node["_variant"] = variant
                ckpt.add(_skim_record(node, variant))

            marginal = len(seen_numbers) - before
            stats["variant_yield"][variant] = {
                "issue_count": declared,
                "fetched": fetched,
                "marginal_new_prs": marginal,
            }
            # A variant that adds nothing has earned nothing: say so loudly enough to delete it.
            if marginal == 0:
                log.warning("variant %r contributed 0 new PRs -- candidate for deletion", variant)
            log.info("variant %r: %d declared, %d new", variant, declared, marginal)

    records = list(_load_skim(scratch))
    ranking = rank_participation(records, actors)
    stats["skimmed_prs"] = len(records)
    stats["ranked_people"] = len(ranking)
    return {"records": records, "ranking": ranking, "stats": stats, "scratch": scratch}


def _skim_record(node: dict[str, Any], variant: str) -> dict[str, Any]:
    """Only what is needed to decide WHO to hydrate, plus updatedAt for incremental runs."""
    author = node.get("author") or {}
    reviews = node.get("reviews") or {}
    return {
        "number": node["number"],
        "title": node.get("title"),
        "createdAt": node.get("createdAt"),
        "mergedAt": node.get("mergedAt"),
        "updatedAt": node.get("updatedAt"),
        "author_login": author.get("login"),
        "author_type": author.get("__typename"),
        "variant": variant,
        "review_total": reviews.get("totalCount", 0),
        "reviewers": [
            {"login": (r.get("author") or {}).get("login"),
             "type": (r.get("author") or {}).get("__typename"),
             "state": r.get("state")}
            for r in (reviews.get("nodes") or [])
            if r and (r.get("author") or {}).get("login")
        ],
    }


def _load_skim(scratch):
    from ..rawio import iter_jsonl

    by_number: dict[int, dict[str, Any]] = {}
    for rec in iter_jsonl(scratch):
        by_number[rec["number"]] = rec
    return by_number.values()


def rank_participation(records, actors: Actors) -> list[dict[str, Any]]:
    """Participation = authored + reviewed. Humans only; bots never enter the ranking."""
    authored: dict[str, int] = defaultdict(int)
    reviewed: dict[str, int] = defaultdict(int)
    touched: dict[str, set[int]] = defaultdict(set)

    for rec in records:
        author = rec.get("author_login")
        if author and actors.is_human(author, rec.get("author_type")):
            authored[author] += 1
            touched[author].add(rec["number"])
        for reviewer in rec.get("reviewers") or []:
            login = reviewer.get("login")
            if login and actors.is_human(login, reviewer.get("type")) and login != author:
                reviewed[login] += 1
                touched[login].add(rec["number"])

    people = set(authored) | set(reviewed)
    ranking = [
        {
            "login": login,
            "authored_n": authored.get(login, 0),
            "reviewed_n": reviewed.get(login, 0),
            "participation": authored.get(login, 0) + reviewed.get(login, 0),
            "pr_numbers": sorted(touched[login]),
        }
        for login in people
    ]
    ranking.sort(key=lambda r: (-r["participation"], r["login"]))
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
        # Normalise against a wider pool than we display: percentiles over 10 people are
        # coarse buckets and the shrinkage prior gets noisy.
        row["in_normalise"] = i <= NORMALISE_POOL_SIZE
        row["in_display"] = i <= DISPLAY_SIZE
    return ranking


def hydration_targets(ranking: list[dict[str, Any]]) -> list[int]:
    """Every PR the normalise pool authored or reviewed -- the Phase 2 work list."""
    targets: set[int] = set()
    for row in ranking:
        if row["in_normalise"]:
            targets.update(row["pr_numbers"])
    return sorted(targets)
