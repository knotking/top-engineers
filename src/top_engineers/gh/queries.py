"""GraphQL documents.

The skim is deliberately thin. Ask of every discovery field: does this change WHO I fetch
next? If not, it belongs in hydration. ``reviews(first:10){totalCount}`` is enough to rank
participation; ``first:50`` is not needed and costs page size.

Hydration pages are sized for the MEDIAN, not the max (measured 0.560 points/PR vs 1.56
untuned) and batch one alias per PR -- there is no bulk ``pullRequest(numbers: [...])``.
"""

from __future__ import annotations

from ..config import (
    BOT_AUTHORS,
    PAGE_COMMENTS,
    PAGE_COMMITS,
    PAGE_FILES,
    PAGE_REVIEWS,
    PAGE_REVIEW_THREADS,
    REPO_NAME,
    REPO_OWNER,
)

RATE_LIMIT = "rateLimit { cost remaining resetAt nodeCount }"


def search_query(variant: str, since: str, until: str, extra: str = "") -> str:
    """One search string. Bot exclusion is server-side: never download rows you will discard."""
    bots = " ".join(f"-author:{a}" for a in BOT_AUTHORS)
    base = (
        f"repo:{REPO_OWNER}/{REPO_NAME} is:pr is:merged "
        f"merged:{since}..{until} {bots} in:title {variant}"
    )
    return f"{base} {extra}".strip()


COUNT_QUERY = f"""
query($q: String!) {{
  search(query: $q, type: ISSUE, first: 1) {{ issueCount }}
  {RATE_LIMIT}
}}
"""

# Skim: participation only. Everything here must influence WHO we hydrate.
SKIM_QUERY = f"""
query($q: String!, $first: Int!, $after: String) {{
  search(query: $q, type: ISSUE, first: $first, after: $after) {{
    issueCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      ... on PullRequest {{
        number
        title
        createdAt
        mergedAt
        updatedAt
        author {{ login __typename }}
        reviews(first: 10) {{
          totalCount
          nodes {{ author {{ login __typename }} state }}
        }}
      }}
    }}
  }}
  {RATE_LIMIT}
}}
"""

PR_FRAGMENT = f"""
fragment PRCore on PullRequest {{
  number
  title
  url
  state
  createdAt
  mergedAt
  updatedAt
  additions
  deletions
  changedFiles
  bodyText
  author {{ login __typename }}
  mergeCommit {{ oid }}
  baseRefName
  files(first: {PAGE_FILES}) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ path additions deletions }}
  }}
  commits(first: {PAGE_COMMITS}) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ commit {{ oid committedDate }} }}
  }}
  # statusCheckRollup is requested on exactly TWO commits, never across the whole
  # connection. Measured on a 25-PR batch: rollup on all 30 commits 3.22s, on the first
  # commit alone 0.88s -- same point cost, 3.7x the latency. And ci_first_pass_rate is
  # defined on the FIRST pushed commit anyway, so the extra rollups bought nothing.
  # (checkSuites is not used at all: it returns only stale QUEUED suites with null
  # conclusions on this repo, which silently nulled out the metric.)
  firstCommit: commits(first: 1) {{
    nodes {{ commit {{ oid committedDate statusCheckRollup {{ state }} }} }}
  }}
  headCommit: commits(last: 1) {{
    nodes {{ commit {{ oid committedDate statusCheckRollup {{ state }} }} }}
  }}
  reviews(first: {PAGE_REVIEWS}) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ author {{ login __typename }} state submittedAt bodyText }}
  }}
  reviewThreads(first: {PAGE_REVIEW_THREADS}) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      isResolved
      isOutdated
      comments(first: 5) {{ totalCount nodes {{ author {{ login __typename }} bodyText createdAt }} }}
    }}
  }}
  comments(first: {PAGE_COMMENTS}) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ author {{ login __typename }} createdAt bodyText }}
  }}
}}
"""


def hydrate_query(numbers: list[int]) -> str:
    """One alias per PR (``p0:``, ``p1:`` ...) against the shared fragment."""
    aliases = "\n".join(
        f"    p{i}: pullRequest(number: {n}) {{ ...PRCore }}" for i, n in enumerate(numbers)
    )
    return f"""
query {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
{aliases}
  }}
  {RATE_LIMIT}
}}
{PR_FRAGMENT}
"""


# Overflow drain. Silent truncation is BIASED -- it deflates exactly the busiest, most-
# reviewed PRs, which are the ones the reviewer metrics depend on.
OVERFLOW_QUERIES = {
    "files": f"""
query($number: Int!, $after: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    pullRequest(number: $number) {{
      files(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ path additions deletions }}
      }}
    }}
  }}
  {RATE_LIMIT}
}}
""",
    "commits": f"""
query($number: Int!, $after: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    pullRequest(number: $number) {{
      commits(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ commit {{ oid committedDate }} }}
      }}
    }}
  }}
  {RATE_LIMIT}
}}
""",
    "reviews": f"""
query($number: Int!, $after: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    pullRequest(number: $number) {{
      reviews(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ author {{ login __typename }} state submittedAt bodyText }}
      }}
    }}
  }}
  {RATE_LIMIT}
}}
""",
    "reviewThreads": f"""
query($number: Int!, $after: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    pullRequest(number: $number) {{
      reviewThreads(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ isResolved isOutdated comments(first: 5) {{ totalCount nodes {{ author {{ login __typename }} bodyText createdAt }} }} }}
      }}
    }}
  }}
  {RATE_LIMIT}
}}
""",
    "comments": f"""
query($number: Int!, $after: String) {{
  repository(owner: "{REPO_OWNER}", name: "{REPO_NAME}") {{
    pullRequest(number: $number) {{
      comments(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ author {{ login __typename }} createdAt bodyText }}
      }}
    }}
  }}
  {RATE_LIMIT}
}}
""",
}
