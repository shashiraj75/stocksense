"""
The conviction-gate audit's ANALYTICAL CONTRACT.

This module is the single normative statement of what the audit is allowed to
compute and what it is allowed to claim. It exists because the superseded
conviction-accuracy analysis produced several conclusions that its own data
could not support, and did so by silently violating rules that had never been
written down. Writing them down here makes each violation a test failure
instead of a narrative choice.

--------------------------------------------------------------------------
1. MARKET AND HORIZON SEPARATION  (mandatory, non-negotiable)
--------------------------------------------------------------------------
India and US are ALWAYS reported separately. A statistic from one market may
never serve as the baseline, control, or comparison for the other. The
superseded report compared US first/second-half win rates against INDIA's
baseline and concluded "both halves clear the baseline" — an invalid
cross-market comparison, and the direct reason this rule is first.

Horizons (short/medium/long) are likewise always reported separately and
never pooled. The superseded "13,988 rows" figure pooled short and medium
horizons AND counted FETCHED rather than RESOLVED rows, then presented the
total as a short-horizon sample size.

--------------------------------------------------------------------------
2. THE EIGHT POPULATIONS
--------------------------------------------------------------------------
Every statistic must name which of these it is computed over. They are
defined once, here, so that "BUY" always means the same thing.

  P1 ALL_ELIGIBLE     every observation for the market x horizon with a
                      finite signal_confidence (the screened candidate set).
  P2 BUY              P1 where signal = 'BUY'.
  P3 NON_BUY          P1 where signal != 'BUY' (HOLD or SELL). This is the
                      correct comparison population for P2 — NOT "chance".
                      Saying an India result was "indistinguishable from
                      chance" misdescribed a test against P3.
  P4 BUY_HIGH_CONV    P2 where signal_confidence >= 85.0.
  P5 BUY_LOW_CONV     P2 where signal_confidence <  85.0. The conviction-gate
                      question is P4 vs P5 — WITHIN BUY. Comparing P4 against
                      all of P3 conflates the gate with the BUY call itself.
  P6 PUBLISHED        P1 where is_daily_pick is true (what users actually saw).
  P7 UNPUBLISHED_BUY  P2 where is_daily_pick is false — the counterfactual
                      for P6. THE MATCH IS MANDATORY AND EXACT: a PUBLISHED
                      row may only ever be compared against UNPUBLISHED_BUY
                      rows carrying the SAME run_id, market and horizon, and
                      only within a SINGLE publication-policy era. See
                      section 6.
  P8 BUY_RANK_QUANTILE  P2 partitioned by WITHIN-RUN percentile of
                      ranking_alpha. Percentiles are computed inside a single
                      run because raw scores are not comparable across runs.

--------------------------------------------------------------------------
3. CLAIM LEVELS
--------------------------------------------------------------------------
Every conclusion the audit emits carries exactly one of these labels.

  PROVEN            Pre-registered comparison meeting EVERY one of these,
                    with no exception and no manual promotion:
                      (a) the comparison is identifiable at all (see
                          NOT_IDENTIFIABLE below),
                      (b) adequate independent clusters on BOTH clustering
                          dimensions (session date AND symbol),
                      (c) a date-blocked 95% interval excluding zero,
                      (d) a two-way (date x symbol) cluster permutation
                          p-value below alpha,
                      (e) survives Holm correction across the COMPLETE
                          pre-registered family of the whole audit run,
                      (f) naive and dependence-aware methods AGREE,
                      (g) SYMBOL-JACKKNIFE SIGN STABILITY — the estimate does
                          not flip sign when any single symbol is deleted.
                    (g) is a HARD precondition evaluated BEFORE classification
                    and passed into the classifier, never checked afterwards
                    as a footnote. A sign-unstable result can never be PROVEN.
  PRELIMINARY       GENUINELY DIRECTIONALLY SUGGESTIVE evidence: the
                    comparison IS identifiable, the effect is estimable, the
                    dependence-aware point estimate is consistent in sign with
                    the naive one and the jackknife is sign-stable — but at
                    least one PROVEN requirement is unmet (typically Holm
                    survival or interval width). May be described as a
                    candidate signal. May NOT be described as an edge.
                    PRELIMINARY IS NOT A DUMPING GROUND. "Inference was not
                    available" is NOT preliminary evidence — that is
                    NOT_IDENTIFIABLE. The superseded report's central error
                    was treating an un-analysable comparison as a suggestive
                    one.
  NOT_IDENTIFIABLE  The comparison cannot be estimated from this data at all,
                    so NOTHING — not even a direction — may be claimed. Use
                    when any of:
                      * too few independent clusters on either dimension,
                      * either comparison group is empty (or empty after
                        run-matching / policy-era restriction),
                      * the policy era is immature: it exists but has too few
                        runs, or too few RESOLVED rows, to estimate anything.
                    A NOT_IDENTIFIABLE cell must NEVER be silently downgraded
                    into PRELIMINARY or reported as NOT_PROVEN — those both
                    imply a test was run. None was.
  NOT_PROVEN        Tested; a real, adequately-powered test was performed and
                    the data does not support the claim. Explicitly NOT the
                    same as "no effect exists" — ALWAYS accompanied by a
                    CLUSTER-ADJUSTED minimum detectable effect (a plain
                    independence MDE overstates this sample's power by the
                    design effect and may never be reported as if it were
                    cluster-aware).
  UNSUPPORTED       Asserted by an earlier report with no computation behind
                    it at all (e.g. "the edge lives entirely in the binary
                    BUY call", "negative momentum rules out trend chasing").
  NOT_REPRODUCIBLE  Cannot be recomputed from data that still exists (e.g.
                    sector breadth — no sector field exists anywhere in the
                    schema; and the two historical row-level denominator
                    gaps whose intermediates were lost).
  FALSE             Contradicted by current evidence (e.g. "alpha_observations
                    is completely empty").

--------------------------------------------------------------------------
4. THE TWO RETURN MEASURES
--------------------------------------------------------------------------
Both are GROSS — before commissions, spread, slippage, taxes, borrow and
market impact. No transaction-cost model is implemented in this audit, and
neither measure may be described as net or as investor P&L.

  A. RESEARCH_PRIOR_CLOSE
     "PRIOR-CLOSE-TO-FUTURE-CLOSE RESEARCH RETURN — NON-EXECUTABLE"
     Entry: first close on/after reference_session_date. Exit: close
     `trading_days` sessions later. This is the ORIGINAL method, retained
     unchanged for historical comparability with the superseded report.
     Daily Picks are generated pre-market (India ~20:37 UTC / ~02:07 IST
     next day; US ~06:00 UTC), so this entry close precedes the pick's own
     existence. It is a research reference price, NOT look-ahead bias and
     NOT an achievable fill. It may never be reported as investor return.

  B. EXECUTABLE_NEXT_OPEN
     "NEXT-TRADABLE-OPEN-TO-HORIZON-CLOSE GROSS BENCHMARK RETURN"
     Entry: open of the first regular session strictly after
     run_generated_at (see audit_calendar.next_tradable_open — real NYSE and
     NSE calendars, weekends/holidays/DST handled). Exit: close of the
     session exactly `trading_days` sessions after entry. A window that has
     not fully elapsed is EXCLUDED, never truncated or extrapolated.

--------------------------------------------------------------------------
5. PROHIBITED OPERATIONS
--------------------------------------------------------------------------
  * no cross-market baselines (rule 1),
  * no pooling of horizons,
  * no sector claims — the schema has no sector column, so sector breadth is
    permanently NOT_REPRODUCIBLE,
  * no promotion of an exploratory (factor/regime/momentum) finding to a
    causal or headline claim,
  * no significance claim below the cluster-adequacy floor,
  * no write of any kind to any production table — the audit is read-only,
  * no generated data file written inside the repository,
  * no pooling of publication-policy eras in ANY published/unpublished
    comparison (section 6),
  * no promotion of a claim level by hand — the classifier's output is the
    only claim level the audit or its documentation may state.

--------------------------------------------------------------------------
6. PUBLICATION-POLICY ERAS
--------------------------------------------------------------------------
The publication policy changed DURING the observation window, and the two
changes did NOT land on the same date. Collapsing them into one boundary —
or pooling rows across them — compares populations produced by different
selection rules and is forbidden.

The boundaries below were VERIFIED against live production data, per market,
by observing the first session date from which every subsequent run complies
(see the audit's data-integrity check `publication_gate_and_cap_by_era`).
They are OBSERVED-COMPLIANCE boundaries derived from the rows themselves,
not deployment timestamps read from a changelog:

  ERA_LEGACY        No effective conviction gate: runs published BUY rows
                    with signal_confidence far below 85, up to 6 per run.
  ERA_GATE_ONLY     Every published row has signal_confidence >= 85, but the
                    publication cap is still 6 per run.
  ERA_GATE_PLUS_CAP Gate >= 85 AND at most 3 published rows per run.

Per-market first session date of each era (verified 2026-08-22 against the
live table; US and INDIA differ and must not be merged):

  US  : legacy through 2026-08-07 | gate_only from 2026-08-10
        | gate_plus_cap from 2026-08-17
  IN  : legacy through 2026-08-12 | gate_only from 2026-08-13
        | gate_plus_cap from 2026-08-17

A comparison restricted to an era with too few runs or too few RESOLVED rows
is NOT_IDENTIFIABLE — never NOT_PROVEN and never PRELIMINARY.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

# Bumped whenever a change alters the numbers this audit produces, so a
# stored bundle can always be traced to the logic that generated it.
CALCULATION_VERSION = "conviction-audit-2.0.0"

MIN_CONVICTION_TO_PUBLISH = 85.0

# Publication cap in force during ERA_GATE_PLUS_CAP.
MAX_PUBLISHED_PER_RUN = 3

# --- Publication-policy eras (section 6) ----------------------------------
ERA_LEGACY = "legacy"
ERA_GATE_ONLY = "gate_only"
ERA_GATE_PLUS_CAP = "gate_plus_cap"

POLICY_ERAS = (ERA_LEGACY, ERA_GATE_ONLY, ERA_GATE_PLUS_CAP)

# First run_session_date of each non-legacy era, PER MARKET. Verified against
# live production rows on 2026-08-22 — the gate and the cap became effective
# on DIFFERENT dates, and the gate date differs between the two markets.
# Anything earlier than the gate_only date is ERA_LEGACY.
POLICY_ERA_BOUNDARIES = {
    "US": {ERA_GATE_ONLY: _dt.date(2026, 8, 10),
           ERA_GATE_PLUS_CAP: _dt.date(2026, 8, 17)},
    "IN": {ERA_GATE_ONLY: _dt.date(2026, 8, 13),
           ERA_GATE_PLUS_CAP: _dt.date(2026, 8, 17)},
}

# An era must contribute at least this many distinct runs AND this many
# resolved rows in BOTH comparison groups before any estimate is attempted.
# Below either floor the comparison is NOT_IDENTIFIABLE.
MIN_RUNS_PER_ERA_FOR_ESTIMATE = 8
MIN_RESOLVED_PER_GROUP_FOR_ESTIMATE = 30


def policy_era(market: str, session_date) -> str:
    """
    The publication-policy era a run belongs to, from its market and
    run_session_date. Pure and total: every date maps to exactly one era.
    """
    bounds = POLICY_ERA_BOUNDARIES.get(market)
    if bounds is None:
        raise ValueError(f"no policy-era boundaries configured for market={market!r}")
    day = session_date
    if isinstance(day, _dt.datetime):
        day = day.date()
    elif not isinstance(day, _dt.date):
        day = _dt.date.fromisoformat(str(day))
    if day >= bounds[ERA_GATE_PLUS_CAP]:
        return ERA_GATE_PLUS_CAP
    if day >= bounds[ERA_GATE_ONLY]:
        return ERA_GATE_ONLY
    return ERA_LEGACY

RESEARCH_PRIOR_CLOSE = "RESEARCH_PRIOR_CLOSE"
EXECUTABLE_NEXT_OPEN = "EXECUTABLE_NEXT_OPEN"

RETURN_MEASURES = {
    RESEARCH_PRIOR_CLOSE: (
        "PRIOR-CLOSE-TO-FUTURE-CLOSE RESEARCH RETURN — NON-EXECUTABLE. "
        "Gross of all costs. Entry is a prior-session close that precedes the "
        "pick's own pre-market generation; retained for historical "
        "comparability only. Never investor P&L."
    ),
    EXECUTABLE_NEXT_OPEN: (
        "NEXT-TRADABLE-OPEN-TO-HORIZON-CLOSE GROSS BENCHMARK RETURN. "
        "Gross of all costs (no commission, spread, slippage, tax or impact "
        "model). Entry is the open of the first regular session strictly "
        "after run_generated_at. A benchmark, not a realised fill."
    ),
}

# Claim levels — see section 3 above.
PROVEN = "PROVEN"
PRELIMINARY = "PRELIMINARY"
NOT_IDENTIFIABLE = "NOT_IDENTIFIABLE"
NOT_PROVEN = "NOT_PROVEN"
UNSUPPORTED = "UNSUPPORTED"
NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"
FALSE = "FALSE"

CLAIM_LEVELS = (PROVEN, PRELIMINARY, NOT_IDENTIFIABLE, NOT_PROVEN,
                UNSUPPORTED, NOT_REPRODUCIBLE, FALSE)

# Levels that may be described to a user as evidence of anything at all.
# NOT_IDENTIFIABLE is deliberately absent: it means no test was run.
EVIDENTIAL_LEVELS = (PROVEN, PRELIMINARY)

# Population identifiers — see section 2 above.
P_ALL_ELIGIBLE = "ALL_ELIGIBLE"
P_BUY = "BUY"
P_NON_BUY = "NON_BUY"
P_BUY_HIGH_CONV = "BUY_HIGH_CONV"
P_BUY_LOW_CONV = "BUY_LOW_CONV"
P_PUBLISHED = "PUBLISHED"
P_UNPUBLISHED_BUY = "UNPUBLISHED_BUY"
P_BUY_RANK_QUANTILE = "BUY_RANK_QUANTILE"

POPULATIONS = (
    P_ALL_ELIGIBLE, P_BUY, P_NON_BUY, P_BUY_HIGH_CONV, P_BUY_LOW_CONV,
    P_PUBLISHED, P_UNPUBLISHED_BUY, P_BUY_RANK_QUANTILE,
)

# Claims the superseded external report made, with this audit's verdict on
# each. Documentation-facing; also asserted by the regression tests so a
# corrected claim cannot silently regress.
SUPERSEDED_CLAIMS = {
    "US edge confirmed": (
        PRELIMINARY,
        "Candidate-level BUY vs eligible-non-BUY difference is directionally "
        "positive but rests on a naive independence assumption violated by "
        "repeated symbols, session-date clustering and uncorrected multiple "
        "comparisons. Correct classification: PRELIMINARY CANDIDATE-LEVEL US "
        "SIGNAL — NOT A PROVEN DAILY PICKS EDGE.",
    ),
    "India indistinguishable from chance": (
        FALSE,
        "Framing error. India BUY was tested against the ELIGIBLE NON-BUY "
        "population, not against chance. The correct statement is that India "
        "BUY did not outperform eligible non-BUY at the available sample size.",
    ),
    "13,988 sample size": (
        FALSE,
        "Mislabelled. 13,988 was a FETCHED row count pooling short and medium "
        "horizons, not a resolved short-horizon sample. It also violates the "
        "horizon-separation rule. Any published sample size must be a "
        "RESOLVED count for a single market x horizon.",
    ),
    "look-ahead bias in the backtest": (
        FALSE,
        "The entry price precedes the pick's own generation, so it cannot be "
        "look-ahead. It is a NON-EXECUTABLE PRIOR-SESSION RESEARCH PRICE — a "
        "distinct and separately-reported defect, addressed by adding the "
        "EXECUTABLE_NEXT_OPEN measure alongside it.",
    ),
    "edge is broad across sectors": (
        NOT_REPRODUCIBLE,
        "No sector column exists anywhere in alpha_observations or any joined "
        "table. Sector breadth cannot be computed and must not be claimed.",
    ),
    "edge lives entirely in the binary BUY call": (
        UNSUPPORTED,
        "No computation supporting this was ever performed. Ranking lift "
        "within BUY had not been measured at all.",
    ),
    "negative momentum rules out trend chasing": (
        UNSUPPORTED,
        "Exploratory observation presented as a causal conclusion.",
    ),
    "alpha_observations is completely empty": (
        FALSE,
        "The canonical table is populated with real prediction-time evidence. "
        "It remains disconnected from any production learning feedback loop, "
        "which is the accurate containment rationale.",
    ),
}

# Historical gaps that cannot be recomputed because the row-level
# intermediates no longer exist. Recorded so they are reported honestly
# rather than re-manufactured.
PERMANENTLY_NOT_REPRODUCIBLE = (
    "The 331-row denominator gap in a prior US-short bucketing pass: the "
    "row-level intermediates were not retained and cannot be reconstructed.",
    "The final 1-row identity inside a 1,358-vs-1,351 reconciliation: 8 of "
    "those rows were positively identified (all PANW, by symbol + "
    "reference_session_date + run_generated_at); the last row's identity is "
    "unrecoverable.",
)


@dataclass(frozen=True)
class Claim:
    """One audit conclusion, always carrying its evidence level."""

    key: str
    level: str
    statement: str

    def __post_init__(self) -> None:
        if self.level not in CLAIM_LEVELS:
            raise ValueError(
                f"claim level {self.level!r} is not one of {CLAIM_LEVELS}"
            )


class ReconciliationError(RuntimeError):
    """
    Raised when a row-count waterfall does not balance exactly.

    The audit fails loudly rather than reporting a denominator it cannot
    account for — the specific failure mode that produced the superseded
    report's unexplained 331-row and 1-row gaps.
    """


def assert_reconciles(stage: str, fetched: int, included: int, excluded: int) -> None:
    """Every stage must satisfy fetched == included + excluded, exactly."""
    if fetched != included + excluded:
        raise ReconciliationError(
            f"{stage}: fetched={fetched} != included={included} + "
            f"excluded={excluded} (difference {fetched - included - excluded}). "
            "The audit refuses to report an unreconciled denominator."
        )
