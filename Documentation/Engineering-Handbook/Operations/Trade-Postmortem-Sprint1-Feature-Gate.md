# Trade Postmortem Sprint 1 — Feature Gate

**Status as of PR #32: DORMANT — NOT ENABLED FOR USERS.**

## What is gated

Sprint 1's daily Trade Postmortem surface, and only that surface:

- Backend: `GET /api/paper-trading/postmortem/daily` (gated by
  `TRADE_POSTMORTEM_DAILY_ENABLED`, defaults to disabled — returns
  `404 {"error_code": "FEATURE_NOT_ENABLED"}` with no date parsing,
  database query, or attribution computation when disabled).
- Frontend: the "Postmortem" top-nav link and the `/postmortem` page
  (gated by `NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED`, a build-time
  flag defaulting to disabled — hides the nav item, never calls the
  daily API, and shows a plain "not available yet" state on direct
  navigation).

**Not gated, and unaffected by either flag:** the pre-existing Phase 1
single-trade endpoint (`GET /api/paper-trading/postmortem/{trade_id}`),
and the entire existing Paper Trading page/flow.

## Why it's dormant

Sprint 1 (see `backend/services/postmortem/evidence_attribution.py`'s
module docstring) is an evidence-governance *foundation* — claim-level
provenance, a non-circular signal scorecard, and a multi-contributor
model — not the complete investor-facing product. It intentionally does
not yet include:

- automatic close-to-report orchestration (a central close service with
  a crash-safe outbox);
- immutable exit snapshots;
- persisted/versioned reports;
- point-in-time price-path evidence (MFE/MAE);
- market, sector, volatility, liquidity, or news evidence acquisition;
- PDF/CSV export.

Because of this, most trades today honestly resolve to `NOT_TESTABLE` or
`INSUFFICIENT_EVIDENCE` for most questions a postmortem report should
answer, and `primary_contributor` is normally `null`. That is correct,
evidence-governed behavior — never fabricated — but it is not yet what a
public-facing surface should default to showing every user.

## How to enable locally / in a preview environment

Set both, then rebuild the frontend (Next.js inlines `NEXT_PUBLIC_*` at
build time — a runtime-only env change on an already-built frontend has
no effect):

```
TRADE_POSTMORTEM_DAILY_ENABLED=true
NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED=true
```

Accepted true values for both (case-insensitive, trimmed): `1`, `true`,
`yes`, `on`. Anything else — including unset, empty, or any other
string — resolves to disabled. Neither flag has been set in Railway or
Vercel production configuration by this PR.

## Required gate before enabling in production

At minimum, all of the following must be true before
`TRADE_POSTMORTEM_DAILY_ENABLED` / `NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED`
are set to `true` in Railway/Vercel production:

1. **Durable automatic report creation** — every closed trade (manual,
   stop-loss, target, and any future exit mechanism) reliably produces a
   report without requiring a user to load a page at the right time.
2. **Report persistence** — a versioned, persisted report store (not
   computed fresh on every `GET`), so a report survives process restarts
   and can be regenerated/diffed deterministically.
3. **Explicit production authorization** — a separate, later decision
   from a human owner, following this repo's standing "never
   self-authorize a merge/deploy" convention.
4. **Frontend production verification** — a real, observed pass through
   the enabled UI in a production-equivalent environment (not just local
   `next build`), including mobile and accessibility checks.
5. **Evidence limitations clearly visible** — the still-narrow evidence
   base (most signals `NOT_TESTABLE`, `primary_contributor` usually
   `null`) must remain honestly and visibly communicated in the UI, not
   smoothed over as coverage improves incrementally.

Until all five are satisfied, this surface should remain disabled by
default in every environment that real users can reach.
