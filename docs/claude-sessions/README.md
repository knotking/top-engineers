# Claude Code sessions

This project was built in a single Claude Code session on **2026-09-16, 11:11 → 12:16 UTC-ish
(~65 minutes)**, following a plan → implement SDLC flow.

| | |
|---|---|
| [`2026-09-16-build.md`](2026-09-16-build.md) | Full session log: what was asked, what was built, what broke |
| [`../time.md`](../time.md) | Time distribution and detailed timeline |
| [`../plan/readme.md`](../plan/readme.md) | The plan, with progress marks and deviations |

## Method

1. **`/plan`** — restate the requirement, verify assumptions against the live API, write the
   plan to `docs/plan/readme.md`, stop for confirmation.
2. **`/implement`** — work the plan in order, marking progress and recording deviations.

The one rule that shaped everything: **measure before building on an assumption.** Ten minutes
of API queries during planning overturned four claims in the brief, one of which
(`checkSuites` returning null conclusions) would have silently invalidated a scored metric.
