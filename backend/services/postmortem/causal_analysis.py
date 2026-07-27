"""
Deterministic causal-analysis layer — Trade Postmortem Engine, Stage 3.

Problem this solves: `services/postmortem/deterministic.py` (Phase 1)
answers only "what happened" (outcome, P&L, duration, exit mechanism) — it
explicitly scopes out "why it happened" in its own module docstring. This
module is that "why" layer: given a trade's already-computed
`DeterministicPostmortem` and its (possibly absent) `EntrySnapshot`, it
produces a `TradePostmortemNarrative` answering entry conditions, which
signals agreed/contradicted the outcome, the likely price-move cause,
market/timing context, thesis correctness, root cause, and a hedged
takeaway.

Governing rule (verbatim from the product owner): never fabricate a reason,
market event, signal, price, indicator, news item, or causal explanation.
Where evidence is unavailable or causality cannot be established, this
module returns `SignalDirectionAgreement.INSUFFICIENT_EVIDENCE` (or the
narrative-field equivalent) rather than guessing. This is deliberate and
expected to fire often — most trades opened manually (not from a live
Daily Pick / recommendation) carry almost no entry-time signal evidence at
all (see entry_snapshot.py's module docstring), and no market/sector/news/
volatility/liquidity data is ever stored anywhere in this codebase, for any
trade, at any time. A common "insufficient evidence" result is this
module working as designed, not a shortcoming.

Pure function, no I/O, no randomness: `build_causal_analysis` never queries
a database, calls an external API, or calls an LLM — every claim it makes
is a deterministic function of the two typed inputs it's given.
"""

import math
from dataclasses import dataclass, field
from enum import Enum

from services.postmortem.deterministic import DeterministicPostmortem, EvidenceCompleteness, ExitMechanism, Outcome
from services.postmortem.entry_snapshot import EntrySnapshot, VerificationLevel

# Bumped whenever the RULES in this module change (the decision tables, the
# signal-agreement thresholds, the root-cause ordering) — independent of
# deterministic.CALCULATION_VERSION (Phase 1's own rules) and of the API
# response schema version (the JSON shape).
CAUSAL_CALCULATION_VERSION = "1.0.0"

_INSUFFICIENT_EVIDENCE_REASON = "Insufficient evidence to determine this factor reliably."


class SignalDirectionAgreement(str, Enum):
    AGREED = "AGREED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ThesisAssessment(str, Enum):
    CORRECT = "CORRECT"
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    UNSUPPORTED = "UNSUPPORTED"
    TOO_EARLY = "TOO_EARLY"
    TOO_LATE = "TOO_LATE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RootCauseCategory(str, Enum):
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    MARKET_CONDITIONS = "MARKET_CONDITIONS"
    SELECTION = "SELECTION"
    TIMING = "TIMING"
    EXIT_LOGIC = "EXIT_LOGIC"
    # Real, assessable evidence existed but no specific rule below matched —
    # distinct from INSUFFICIENT_EVIDENCE (where the evidence itself is
    # missing). Never conflate "we looked and found no clear cause" with
    # "we couldn't look."
    NOISE_UNEXPLAINED = "NOISE_UNEXPLAINED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class SignalFactorAssessment:
    """One named signal's (technical/recommendation/fundamental/sentiment)
    agreement with the trade's realized outcome, plus the hedged, non-
    fabricated reason string shown alongside it."""

    factor: str
    agreement: SignalDirectionAgreement
    reason: str


@dataclass(frozen=True)
class MarketContextAssessment:
    """Question 8's six named sub-factors. `regime` and `timing` are the
    only two ever backed by real stored data (entry-time market regime,
    execution-range position); the other four are ALWAYS
    INSUFFICIENT_EVIDENCE by construction — this codebase has never stored
    sector, news, volatility, or liquidity data for any trade, at any
    time."""

    regime: SignalFactorAssessment
    timing: SignalFactorAssessment
    sector: SignalFactorAssessment
    news: SignalFactorAssessment
    volatility: SignalFactorAssessment
    liquidity: SignalFactorAssessment


@dataclass(frozen=True)
class TradePostmortemNarrative:
    trade_id: int
    entry_conditions: list[str]
    entry_conditions_reason: str | None
    signal_factors: list[SignalFactorAssessment]
    price_move_cause: SignalFactorAssessment
    market_context: MarketContextAssessment
    exit_mechanism_summary: str
    thesis_assessment: ThesisAssessment
    thesis_reason: str
    root_cause: RootCauseCategory
    root_cause_reason: str
    takeaway: str
    calculation_version: str = CAUSAL_CALCULATION_VERSION
    warnings: list[str] = field(default_factory=list)


def _insufficient(factor: str) -> SignalFactorAssessment:
    return SignalFactorAssessment(
        factor=factor, agreement=SignalDirectionAgreement.INSUFFICIENT_EVIDENCE, reason=_INSUFFICIENT_EVIDENCE_REASON
    )


def _is_finite_number(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _outcome_direction(outcome: Outcome) -> int | None:
    """+1 for WIN, -1 for LOSS, `None` for BREAKEVEN/INDETERMINATE — a
    directional signal cannot be judged to have "agreed" or "contradicted"
    an outcome that itself has no clear direction."""
    if outcome == Outcome.WIN:
        return 1
    if outcome == Outcome.LOSS:
        return -1
    return None


def _classify_directional_signal(
    factor: str, signal_direction: int | None, outcome_direction: int | None
) -> SignalFactorAssessment:
    if signal_direction is None or outcome_direction is None:
        return _insufficient(factor)
    if signal_direction == outcome_direction:
        return SignalFactorAssessment(
            factor=factor,
            agreement=SignalDirectionAgreement.AGREED,
            reason=f"{factor} pointed the same direction as the trade's realized outcome.",
        )
    return SignalFactorAssessment(
        factor=factor,
        agreement=SignalDirectionAgreement.CONTRADICTED,
        reason=f"{factor} pointed the opposite direction from the trade's realized outcome.",
    )


def _technical_signal_direction(technical_signal: str | None) -> int | None:
    if not isinstance(technical_signal, str):
        return None
    value = technical_signal.strip().upper()
    if value in ("BUY", "BULLISH", "STRONG_BUY"):
        return 1
    if value in ("SELL", "BEARISH", "STRONG_SELL"):
        return -1
    return None


def _recommendation_signal_direction(recommendation_signal: str | None) -> int | None:
    return _technical_signal_direction(recommendation_signal)


def _score_direction(score: float | None, *, neutral_midpoint: float) -> int | None:
    """Generic direction for a 0-100-ish score field: above the neutral
    midpoint is bullish, below is bearish. `None` when the score itself is
    missing/non-finite, or sits exactly on the midpoint (no lean either
    way — not a fabricated tie-break)."""
    if not _is_finite_number(score):
        return None
    if score > neutral_midpoint:
        return 1
    if score < neutral_midpoint:
        return -1
    return None


def _build_signal_factors(snapshot: EntrySnapshot | None, outcome: Outcome) -> list[SignalFactorAssessment]:
    outcome_direction = _outcome_direction(outcome)
    if snapshot is None:
        return [
            _insufficient("technical_signal"),
            _insufficient("recommendation_signal"),
            _insufficient("fundamental_score"),
            _insufficient("sentiment_score"),
        ]
    return [
        _classify_directional_signal(
            "technical_signal", _technical_signal_direction(snapshot.technical_signal), outcome_direction
        ),
        _classify_directional_signal(
            "recommendation_signal",
            _recommendation_signal_direction(snapshot.recommendation_signal),
            outcome_direction,
        ),
        _classify_directional_signal(
            "fundamental_score", _score_direction(snapshot.fundamental_score, neutral_midpoint=50.0), outcome_direction
        ),
        _classify_directional_signal(
            "sentiment_score", _score_direction(snapshot.sentiment_score, neutral_midpoint=50.0), outcome_direction
        ),
    ]


def _build_entry_conditions(snapshot: EntrySnapshot | None) -> tuple[list[str], str | None]:
    if snapshot is None:
        return [], _INSUFFICIENT_EVIDENCE_REASON

    facts: list[str] = []
    verified_fields = [
        f for f, level in snapshot.verification_levels.items() if level != VerificationLevel.UNAVAILABLE.value
    ]
    if not verified_fields:
        return [], _INSUFFICIENT_EVIDENCE_REASON

    if snapshot.recommendation_signal:
        facts.append(f"Recommendation signal at entry: {snapshot.recommendation_signal}.")
    if snapshot.technical_signal:
        facts.append(f"Technical signal at entry: {snapshot.technical_signal}.")
    if _is_finite_number(snapshot.technical_rsi):
        facts.append(f"RSI at entry: {snapshot.technical_rsi:.1f}.")
    if _is_finite_number(snapshot.fundamental_score):
        facts.append(f"Fundamental score at entry: {snapshot.fundamental_score:.1f}.")
    if _is_finite_number(snapshot.sentiment_score):
        label = f" ({snapshot.sentiment_label})" if snapshot.sentiment_label else ""
        facts.append(f"Sentiment score at entry: {snapshot.sentiment_score:.1f}{label}.")
    if snapshot.market_regime_trend:
        facts.append(f"Market regime at entry: {snapshot.market_regime_trend}.")
    if snapshot.execution_range_position and snapshot.execution_range_position != "UNKNOWN":
        facts.append(f"Execution price was {snapshot.execution_range_position.replace('_', ' ').lower()} the recommended entry range.")
    if _is_finite_number(snapshot.reward_to_risk_ratio):
        facts.append(f"Reward-to-risk ratio at entry: {snapshot.reward_to_risk_ratio:.2f}.")

    if not facts:
        # Fields were reported but none map to a human-readable fact this
        # function knows how to render (e.g. only daily_pick_run_id/rank
        # were present) — still honest to say so rather than an empty list
        # with no explanation.
        return [], _INSUFFICIENT_EVIDENCE_REASON
    return facts, None


def _build_price_move_cause(signal_factors: list[SignalFactorAssessment]) -> SignalFactorAssessment:
    """Reuses the strongest already-computed directional signal — never
    recomputes anything independently. "Strongest" = first AGREED, else
    first CONTRADICTED, else insufficient."""
    for assessment in signal_factors:
        if assessment.agreement == SignalDirectionAgreement.AGREED:
            return SignalFactorAssessment(
                factor="price_move_cause",
                agreement=SignalDirectionAgreement.AGREED,
                reason=f"Most consistent with {assessment.factor}, which agreed with the outcome.",
            )
    for assessment in signal_factors:
        if assessment.agreement == SignalDirectionAgreement.CONTRADICTED:
            return SignalFactorAssessment(
                factor="price_move_cause",
                agreement=SignalDirectionAgreement.CONTRADICTED,
                reason=f"No entry signal explains the move; {assessment.factor} pointed the opposite way.",
            )
    return _insufficient("price_move_cause")


def _build_market_context(snapshot: EntrySnapshot | None) -> MarketContextAssessment:
    regime = _insufficient("regime")
    timing = _insufficient("timing")
    if snapshot is not None:
        if snapshot.market_regime_trend:
            regime = SignalFactorAssessment(
                factor="regime",
                agreement=SignalDirectionAgreement.INSUFFICIENT_EVIDENCE,
                reason=f"Market regime at entry was recorded as {snapshot.market_regime_trend}; no exit-time regime is ever captured to compare against.",
            )
        if snapshot.execution_range_position and snapshot.execution_range_position != "UNKNOWN":
            timing = SignalFactorAssessment(
                factor="timing",
                agreement=SignalDirectionAgreement.INSUFFICIENT_EVIDENCE,
                reason=f"Entry execution was {snapshot.execution_range_position.replace('_', ' ').lower()} the recommended range; no equivalent exit-timing evidence is captured.",
            )
    return MarketContextAssessment(
        regime=regime,
        timing=timing,
        sector=_insufficient("sector"),
        news=_insufficient("news"),
        volatility=_insufficient("volatility"),
        liquidity=_insufficient("liquidity"),
    )


def _build_thesis_assessment(
    signal_factors: list[SignalFactorAssessment], outcome: Outcome
) -> tuple[ThesisAssessment, str]:
    assessable = [s for s in signal_factors if s.agreement != SignalDirectionAgreement.INSUFFICIENT_EVIDENCE]
    if not assessable:
        return ThesisAssessment.INSUFFICIENT_EVIDENCE, _INSUFFICIENT_EVIDENCE_REASON

    agreed_count = sum(1 for s in assessable if s.agreement == SignalDirectionAgreement.AGREED)
    contradicted_count = len(assessable) - agreed_count

    if outcome == Outcome.WIN and contradicted_count == 0:
        return ThesisAssessment.CORRECT, "Every assessable entry signal agreed with the winning outcome."
    if outcome == Outcome.LOSS and agreed_count == 0:
        return ThesisAssessment.UNSUPPORTED, "No assessable entry signal supported the direction that ultimately lost."
    if agreed_count > 0 and contradicted_count > 0:
        return (
            ThesisAssessment.PARTIALLY_CORRECT,
            f"{agreed_count} of {len(assessable)} assessable entry signals agreed with the outcome; the rest contradicted it.",
        )
    return ThesisAssessment.INSUFFICIENT_EVIDENCE, _INSUFFICIENT_EVIDENCE_REASON


def _build_root_cause(
    postmortem: DeterministicPostmortem,
    thesis: ThesisAssessment,
    signal_factors: list[SignalFactorAssessment],
) -> tuple[RootCauseCategory, str]:
    assessable = [s for s in signal_factors if s.agreement != SignalDirectionAgreement.INSUFFICIENT_EVIDENCE]

    # Rule 1: no usable evidence at all.
    if postmortem.evidence_completeness == EvidenceCompleteness.LIMITED and not assessable:
        return RootCauseCategory.INSUFFICIENT_EVIDENCE, _INSUFFICIENT_EVIDENCE_REASON

    # Rule 2: manual exit at a loss, price was still closer to target than
    # stop when closed — the position was managed out before either level
    # was actually hit.
    if (
        postmortem.exit_mechanism == ExitMechanism.MANUAL
        and postmortem.outcome == Outcome.LOSS
        and postmortem.target_distance_at_exit_pct is not None
        and postmortem.stop_distance_at_exit_pct is not None
        and abs(postmortem.target_distance_at_exit_pct) < abs(postmortem.stop_distance_at_exit_pct)
    ):
        return (
            RootCauseCategory.POSITION_MANAGEMENT,
            "Exit was manual and the loss was realized before the price reached the stop-loss level — "
            "consistent with the position being closed out early rather than stopped out.",
        )

    # Rule 3: stopped out, and most assessable signals were CONTRADICTED —
    # since AGREED/CONTRADICTED is measured against the realized outcome
    # (never against "the position's intended direction", which this
    # long-only codebase can't distinguish from the signal itself), a
    # CONTRADICTED majority on a LOSS means the entry signals themselves
    # were bullish (reasonable) even though the trade lost — consistent
    # with an adverse market move rather than a flawed entry thesis.
    contradicted_count = sum(1 for s in assessable if s.agreement == SignalDirectionAgreement.CONTRADICTED)
    if (
        postmortem.exit_mechanism == ExitMechanism.STOP_LOSS
        and assessable
        and contradicted_count >= math.ceil(len(assessable) / 2)
    ):
        return (
            RootCauseCategory.MARKET_CONDITIONS,
            "Trade was stopped out even though most assessable entry signals were bullish — "
            "consistent with an adverse market move rather than a flawed entry thesis.",
        )

    # Rule 4: thesis was unsupported from the start.
    if thesis == ThesisAssessment.UNSUPPORTED:
        return (
            RootCauseCategory.SELECTION,
            "No assessable entry signal supported the direction taken — consistent with a selection issue at entry.",
        )

    # Rule 5: thesis correctness data flags early/late timing — reserved
    # for a future version once a TOO_EARLY/TOO_LATE thesis rule exists;
    # `_build_thesis_assessment` never currently returns these values, so
    # this rule cannot fire today, but is kept as an explicit, documented
    # no-op rather than silently omitted.
    if thesis in (ThesisAssessment.TOO_EARLY, ThesisAssessment.TOO_LATE):
        return (
            RootCauseCategory.TIMING,
            "Entry/exit timing relative to the thesis appears to be the primary factor.",
        )

    # Rule 6: exit mechanism is unknown and the trade didn't win.
    if postmortem.exit_mechanism == ExitMechanism.UNKNOWN and postmortem.outcome != Outcome.WIN:
        return (
            RootCauseCategory.EXIT_LOGIC,
            "The stored exit reason does not identify a recognized exit mechanism, "
            "so how this trade was actually closed cannot be reliably explained.",
        )

    # Rule 7: default — evidence existed, but no specific rule matched.
    if assessable or postmortem.evidence_completeness != EvidenceCompleteness.LIMITED:
        return (
            RootCauseCategory.NOISE_UNEXPLAINED,
            "Available evidence does not point to a single clear cause for this outcome.",
        )
    return RootCauseCategory.INSUFFICIENT_EVIDENCE, _INSUFFICIENT_EVIDENCE_REASON


_TAKEAWAY_TEMPLATES: dict[tuple[Outcome, RootCauseCategory], str] = {
    (Outcome.WIN, RootCauseCategory.MARKET_CONDITIONS): "This win may suggest the entry thesis was sound and favorable market conditions helped the trade play out.",
    (Outcome.LOSS, RootCauseCategory.POSITION_MANAGEMENT): "This loss may suggest reviewing whether manual exits are being taken before a defined stop-loss level, ahead of the plan.",
    (Outcome.LOSS, RootCauseCategory.MARKET_CONDITIONS): "This loss may suggest the entry thesis was reasonable but market conditions moved against the position.",
    (Outcome.LOSS, RootCauseCategory.SELECTION): "This loss may suggest the entry signals available did not support the direction taken — worth reviewing the selection criteria used.",
    (Outcome.LOSS, RootCauseCategory.TIMING): "This loss may suggest entry or exit timing relative to the thesis is worth reviewing.",
    (Outcome.LOSS, RootCauseCategory.EXIT_LOGIC): "This loss may suggest the exit mechanism used for this trade is not being recorded clearly enough to evaluate.",
}


def _build_takeaway(outcome: Outcome, root_cause: RootCauseCategory, thesis: ThesisAssessment) -> str:
    if root_cause == RootCauseCategory.INSUFFICIENT_EVIDENCE:
        return "Not enough stored evidence exists to draw a specific, reliable lesson from this trade."
    templated = _TAKEAWAY_TEMPLATES.get((outcome, root_cause))
    if templated is not None:
        return templated
    if root_cause == RootCauseCategory.NOISE_UNEXPLAINED:
        return "Available evidence does not point to one clear, reliable lesson from this trade."
    return f"Outcome was {outcome.value.lower()}, attributed to {root_cause.value.lower().replace('_', ' ')}; no further specific lesson is supported by the available evidence."


def build_causal_analysis(
    postmortem: DeterministicPostmortem, snapshot: EntrySnapshot | None
) -> TradePostmortemNarrative:
    """Pure function: one already-computed `DeterministicPostmortem` plus
    its (possibly `None`) `EntrySnapshot` in, one `TradePostmortemNarrative`
    out. Never recomputes outcome/P&L/duration/exit-mechanism — those are
    read verbatim from `postmortem`, never re-derived here."""
    warnings: list[str] = list(postmortem.warnings)

    signal_factors = _build_signal_factors(snapshot, postmortem.outcome)
    entry_conditions, entry_conditions_reason = _build_entry_conditions(snapshot)
    price_move_cause = _build_price_move_cause(signal_factors)
    market_context = _build_market_context(snapshot)
    thesis_assessment, thesis_reason = _build_thesis_assessment(signal_factors, postmortem.outcome)
    root_cause, root_cause_reason = _build_root_cause(postmortem, thesis_assessment, signal_factors)
    takeaway = _build_takeaway(postmortem.outcome, root_cause, thesis_assessment)

    exit_mechanism_summary = (
        f"Closed via {postmortem.exit_mechanism.value}"
        if postmortem.exit_mechanism != ExitMechanism.UNKNOWN
        else "Exit mechanism could not be determined from the stored exit reason"
    )

    return TradePostmortemNarrative(
        trade_id=postmortem.trade_id,
        entry_conditions=entry_conditions,
        entry_conditions_reason=entry_conditions_reason,
        signal_factors=signal_factors,
        price_move_cause=price_move_cause,
        market_context=market_context,
        exit_mechanism_summary=exit_mechanism_summary,
        thesis_assessment=thesis_assessment,
        thesis_reason=thesis_reason,
        root_cause=root_cause,
        root_cause_reason=root_cause_reason,
        takeaway=takeaway,
        calculation_version=CAUSAL_CALCULATION_VERSION,
        warnings=warnings,
    )
