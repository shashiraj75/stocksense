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
                      for P6, matched on the same run_id/market/horizon.
  P8 BUY_RANK_QUANTILE  P2 partitioned by WITHIN-RUN percentile of
                      ranking_alpha. Percentiles are computed inside a single
                      run because raw scores are not comparable across runs.

--------------------------------------------------------------------------
3. CLAIM LEVELS
--------------------------------------------------------------------------
Every conclusion the audit emits carries exactly one of these labels.

  PROVEN            Pre-registered comparison; effect size with a
                    date-blocked 95% interval excluding zero; survives Holm
                    correction across its family; adequate independent
                    clusters (>= MIN_CLUSTERS_FOR_INFERENCE); naive and
                    dependence-aware methods AGREE; robust to
                    symbol-jackknife sign flips.
  PRELIMINARY       Directionally suggestive but fails at least one PROVEN
                    requirement — typically cluster adequacy or
                    naive/dependence-aware agreement. May be described as a
                    candidate signal. May NOT be described as an edge.
  NOT_PROVEN        Tested; the data does not support the claim. Explicitly
                    NOT the same as "no effect exists" — always accompanied
                    by the minimum detectable effect.
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
  * no generated data file written inside the repository.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bumped whenever a change alters the numbers this audit produces, so a
# stored bundle can always be traced to the logic that generated it.
CALCULATION_VERSION = "conviction-audit-1.0.0"

MIN_CONVICTION_TO_PUBLISH = 85.0

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
NOT_PROVEN = "NOT_PROVEN"
UNSUPPORTED = "UNSUPPORTED"
NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"
FALSE = "FALSE"

CLAIM_LEVELS = (PROVEN, PRELIMINARY, NOT_PROVEN, UNSUPPORTED, NOT_REPRODUCIBLE, FALSE)

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
