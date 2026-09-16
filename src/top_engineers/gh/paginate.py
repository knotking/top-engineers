"""Date-slice recursion around the silent search cap, plus overflow drain.

GitHub search caps at 1,000 results SILENTLY -- no error, no warning, just a short result
set. So read ``issueCount`` first and split the date range recursively above the cap. This is
self-correcting, which means there is no need to guess a slice width up front.

A single day still over the cap is REAL DATA LOSS and must be logged loudly -- it cannot be
split any further.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, AsyncIterator

from ..config import SEARCH_RESULT_CAP, SKIM_PAGE
from .client import GraphQLClient
from .queries import COUNT_QUERY, OVERFLOW_QUERIES, SKIM_QUERY, search_query

log = logging.getLogger(__name__)


async def count_for(client: GraphQLClient, query_string: str) -> int:
    data = await client.execute(COUNT_QUERY, {"q": query_string})
    return data["search"]["issueCount"]


async def iter_search(
    client: GraphQLClient,
    variant: str,
    since: date,
    until: date,
    stats: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield every PR for one title variant, splitting date ranges as needed."""
    async for node in _walk(client, variant, since, until, stats):
        yield node


async def _walk(
    client: GraphQLClient,
    variant: str,
    since: date,
    until: date,
    stats: dict[str, Any] | None,
) -> AsyncIterator[dict[str, Any]]:
    q = search_query(variant, since.isoformat(), until.isoformat())
    total = await count_for(client, q)

    if total == 0:
        return

    if total > SEARCH_RESULT_CAP:
        if since >= until:
            # Cannot split a single day any further: this is unrecoverable loss, not a warning.
            log.error(
                "DATA LOSS: %s on %s has %d results, above the %d cap and unsplittable; "
                "%d PRs will be missing",
                variant, since.isoformat(), total, SEARCH_RESULT_CAP, total - SEARCH_RESULT_CAP,
            )
            if stats is not None:
                stats.setdefault("uncapped_days", []).append(
                    {"variant": variant, "day": since.isoformat(), "issue_count": total}
                )
        else:
            mid = since + (until - since) // 2
            async for node in _walk(client, variant, since, mid, stats):
                yield node
            async for node in _walk(client, variant, mid + timedelta(days=1), until, stats):
                yield node
            return

    cursor: str | None = None
    seen = 0
    while True:
        data = await client.execute(
            SKIM_QUERY, {"q": q, "first": SKIM_PAGE, "after": cursor}
        )
        search = data["search"]
        for node in search["nodes"]:
            if node:
                yield node
                seen += 1
        page = search["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    if stats is not None:
        slices = stats.setdefault("slices", [])
        slices.append(
            {
                "variant": variant,
                "since": since.isoformat(),
                "until": until.isoformat(),
                "issue_count": total,
                "fetched": seen,
            }
        )


_CONNECTION_PATH = {
    "files": ("files",),
    "commits": ("commits",),
    "reviews": ("reviews",),
    "reviewThreads": ("reviewThreads",),
    "comments": ("comments",),
}


async def drain_overflow(client: GraphQLClient, pr: dict[str, Any]) -> dict[str, int]:
    """Follow hasNextPage on every nested connection.

    Measured ~4% of PRs overflow (reviews only), so this is cheap -- but skipping it biases
    the dataset against exactly the busiest PRs.
    """
    drained: dict[str, int] = {}
    number = pr["number"]
    for field in _CONNECTION_PATH:
        conn = pr.get(field)
        if not conn:
            continue
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            continue
        cursor = page.get("endCursor")
        added = 0
        while cursor:
            data = await client.execute(
                OVERFLOW_QUERIES[field], {"number": number, "after": cursor}
            )
            sub = data["repository"]["pullRequest"][field]
            conn["nodes"].extend(n for n in sub["nodes"] if n)
            added += len(sub["nodes"])
            sub_page = sub["pageInfo"]
            cursor = sub_page["endCursor"] if sub_page["hasNextPage"] else None
        conn["pageInfo"] = {"hasNextPage": False, "endCursor": None}
        drained[field] = added
    return drained
