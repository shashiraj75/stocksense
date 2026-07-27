"""
Immutable entry-evidence snapshot — Trade Postmortem Engine, Stage 2.

Problem this solves: Phase 1 shipped provenance columns on `paper_trades`
(recommendation_source, daily_pick_run_id, recommendation_reference_price,
etc.) that no live caller has ever populated — every real trade opened
through the product stores NULL for all of them. This module captures what
StockSense360 *actually* knew about a recommendation at the moment a trade
was opened, so a later forensic report can honestly reconstruct the
original thesis instead of comparing against today's (possibly different)
recommendation.

Field selection discipline: every field below either (a) maps to a value
that genuinely flows through the current frontend Buy path today (verified
directly — see PaperTradeModal.tsx, picks/page.tsx, stock/[symbol]/page.tsx,
and the `Prediction` TypeScript interface in api.ts), (b) is deterministically
computable server-side from those real values, or (c) is a Phase-1
provenance concept (daily_pick_run_id, daily_pick_rank, model_version)
carried forward for schema continuity even though nothing populates it yet
— documented per-field below, never a newly-invented placeholder.

Explicitly NOT captured, because it does not exist anywhere in the current
data flow (verified by reading frontend/src/utils/api.ts's `Prediction`
interface and every PaperTradeModal call site): quality_score, growth_score,
valuation_score, risk_penalty, analyst_consensus_score, macro_score,
quality_gate_result, liquidity_result, volatility_result, week52_position,
recommendation_id (no stable per-recommendation ID is ever generated —
`/api/predictions` computes live and caches in-memory, it never logs to the
`predictions` DB table or returns that table's id), quote_timestamp/
market_data_provider (no per-quote timestamp or provider identity is ever
sent by the frontend to POST /buy). Adding columns for these would be
indistinguishable from fabricating evidence that was never actually
available — exactly what this program forbids.

Trust boundary (Part 7): every recommendation-evidence field here is
labeled CLIENT_REPORTED (the frontend computed/fetched it and asserts it to
the backend) or UNAVAILABLE (the value is missing) — never SERVER_VERIFIED.
No server-side lookup path exists today to independently corroborate any of
these values (no recommendation ID reaches the backend to look up against
`predictions`/`daily_picks_cache`) — see `VerificationLevel` below.
`simulated_execution_price`/`user_selected_stop_loss`/
`user_selected_target_price` are the trade's OWN parameters (server range-
validated, not third-party "evidence"), so they are excluded from the
verification-level map entirely, not silently marked verified.

No short-selling: every trade is long-only (see paper_trade_math.py and
deterministic.py's identical note) — `execution_range_position` and
`reward_to_risk_ratio` below assume a long position's sign convention.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

SNAPSHOT_SCHEMA_VERSION = "1.0.0"


class EvidenceSource(str, Enum):
    MANUAL = "MANUAL"
    SCREENER = "SCREENER"
    DAILY_PICK = "DAILY_PICK"
    RESEARCH = "RESEARCH"
    # Reserved for a future historical-backfill phase (explicitly out of
    # scope here) — never produced by new-trade code in this stage.
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


class VerificationLevel(str, Enum):
    SERVER_VERIFIED = "SERVER_VERIFIED"
    CLIENT_REPORTED = "CLIENT_REPORTED"
    UNAVAILABLE = "UNAVAILABLE"


class RangePosition(str, Enum):
    BELOW_RANGE = "BELOW_RANGE"
    WITHIN_RANGE = "WITHIN_RANGE"
    ABOVE_RANGE = "ABOVE_RANGE"
    UNKNOWN = "UNKNOWN"


# The recommendation-evidence fields this module labels in `verification_levels`.
# Deliberately excludes the trade's own order parameters (simulated_execution_price,
# user_selected_stop_loss, user_selected_target_price) — those aren't
# third-party evidence subject to a trust question, they're the order itself.
VERIFIABLE_EVIDENCE_FIELDS: tuple[str, ...] = (
    "recommendation_signal",
    "recommendation_generated_at",
    "recommendation_reference_price",
    "recommendation_entry_low",
    "recommendation_entry_high",
    "recommended_stop_loss",
    "recommended_target_price",
    "confidence_score",
    "technical_signal",
    "technical_rsi",
    "technical_macd_diff",
    "fundamental_score",
    "sentiment_score",
    "sentiment_label",
    "market_regime_trend",
    "market_regime_score_adj",
    "market_regime_reason",
    "recommendation_reasoning",
    "daily_pick_run_id",
    "daily_pick_rank",
    "model_version",
)


def _is_finite_number(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_finite_positive(value) -> bool:
    return _is_finite_number(value) and value > 0


@dataclass(frozen=True)
class RecommendationContext:
    """Typed input boundary: everything the Buy request is allowed to report
    about the recommendation that was visible when the user clicked Buy.
    Purely a data carrier — no validation happens here, `build_entry_snapshot`
    does all of it, so construction can never partially validate.
    """

    evidence_source: str
    simulated_execution_price: float
    user_selected_stop_loss: float | None = None
    user_selected_target_price: float | None = None
    recommendation_signal: str | None = None
    recommendation_generated_at: datetime | None = None
    recommendation_reference_price: float | None = None
    recommendation_entry_low: float | None = None
    recommendation_entry_high: float | None = None
    recommended_stop_loss: float | None = None
    recommended_target_price: float | None = None
    confidence_score: float | None = None
    technical_signal: str | None = None
    technical_rsi: float | None = None
    technical_macd_diff: float | None = None
    fundamental_score: float | None = None
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    market_regime_trend: str | None = None
    market_regime_score_adj: float | None = None
    market_regime_reason: str | None = None
    recommendation_reasoning: list | None = None
    daily_pick_run_id: str | None = None
    daily_pick_rank: int | None = None
    model_version: str | None = None


@dataclass(frozen=True)
class EntrySnapshot:
    """The immutable, persisted shape of one entry-evidence snapshot row.
    Field order/names mirror `paper_trade_entry_snapshot`'s columns exactly."""

    paper_trade_id: int
    user_id: str
    symbol: str
    market: str
    snapshot_schema_version: str
    evidence_source: str
    daily_pick_run_id: str | None
    daily_pick_rank: int | None
    recommendation_signal: str | None
    recommendation_generated_at: datetime | None
    recommendation_reference_price: float | None
    recommendation_entry_low: float | None
    recommendation_entry_high: float | None
    simulated_execution_price: float
    execution_slippage_pct: float | None
    execution_range_position: str
    recommended_stop_loss: float | None
    recommended_target_price: float | None
    user_selected_stop_loss: float | None
    user_selected_target_price: float | None
    user_overrode_recommendation: bool | None
    reward_to_risk_ratio: float | None
    confidence_score: float | None
    technical_signal: str | None
    technical_rsi: float | None
    technical_macd_diff: float | None
    fundamental_score: float | None
    sentiment_score: float | None
    sentiment_label: str | None
    market_regime_trend: str | None
    market_regime_score_adj: float | None
    market_regime_reason: str | None
    recommendation_reasoning: list | None
    model_version: str | None
    verification_levels: dict[str, str]


def _compute_execution_slippage_pct(reference_price: float | None, execution_price: float) -> float | None:
    if not _is_finite_positive(reference_price):
        return None
    return round((execution_price - reference_price) / reference_price * 100, 4)


def _classify_execution_range_position(
    execution_price: float, entry_low: float | None, entry_high: float | None
) -> RangePosition:
    if not _is_finite_positive(entry_low) or not _is_finite_positive(entry_high) or entry_low > entry_high:
        return RangePosition.UNKNOWN
    if execution_price < entry_low:
        return RangePosition.BELOW_RANGE
    if execution_price > entry_high:
        return RangePosition.ABOVE_RANGE
    return RangePosition.WITHIN_RANGE


def _compute_reward_to_risk_ratio(
    reference_price: float | None, stop_loss: float | None, target_price: float | None
) -> float | None:
    """(target - reference) / (reference - stop), long-only. `None` unless
    all three inputs are real, finite numbers and the implied risk
    (reference - stop) is strictly positive — a non-positive risk denominator
    would either divide by zero or describe a stop placed at/above the
    reference price, which isn't a coherent long-position risk figure."""
    if not (_is_finite_positive(reference_price) and _is_finite_positive(stop_loss) and _is_finite_positive(target_price)):
        return None
    risk = reference_price - stop_loss
    if risk <= 0:
        return None
    reward = target_price - reference_price
    return round(reward / risk, 4)


def _compute_user_overrode_recommendation(
    recommended: float | None, user_selected: float | None
) -> bool | None:
    """`None` when there was no recommendation to override against (nothing
    to compare). Otherwise `True` when the user's final value differs from
    the recommended one by more than a cent (money precision), `False`
    when it matches."""
    if not _is_finite_number(recommended):
        return None
    if not _is_finite_number(user_selected):
        return None
    return round(abs(recommended - user_selected), 2) > 0.0


def _compute_verification_levels(ctx: RecommendationContext) -> dict[str, str]:
    """Every field here is CLIENT_REPORTED when present, UNAVAILABLE when
    absent. No field is ever classified SERVER_VERIFIED in this stage — no
    server-side lookup path exists yet to independently corroborate any of
    these values against a stored recommendation record (see module
    docstring). Implemented as a real per-field function so a future stage
    that adds a genuine server-side lookup can flip individual fields
    without redesigning this mechanism."""
    levels = {}
    for field_name in VERIFIABLE_EVIDENCE_FIELDS:
        value = getattr(ctx, field_name)
        levels[field_name] = (
            VerificationLevel.UNAVAILABLE.value if value is None else VerificationLevel.CLIENT_REPORTED.value
        )
    return levels


def classify_evidence_completeness(snapshot: EntrySnapshot) -> tuple[str, list[str], list[str]]:
    """Returns `(completeness, available_fields, missing_fields)` —
    LIMITED/PARTIAL/COMPLETE against `VERIFIABLE_EVIDENCE_FIELDS`. The
    single source of truth for this classification — used both at Buy time
    (this snapshot's own creation response) and later at postmortem time
    (`services.postmortem.deterministic.compute_postmortem`), so the two
    can never independently drift on what "COMPLETE" means."""
    available = sorted(f for f in VERIFIABLE_EVIDENCE_FIELDS if getattr(snapshot, f) is not None)
    missing = sorted(f for f in VERIFIABLE_EVIDENCE_FIELDS if getattr(snapshot, f) is None)
    if len(available) == 0:
        completeness = "LIMITED"
    elif len(available) == len(VERIFIABLE_EVIDENCE_FIELDS):
        completeness = "COMPLETE"
    else:
        completeness = "PARTIAL"
    return completeness, available, missing


def build_entry_snapshot(
    *, paper_trade_id: int, user_id: str, symbol: str, market: str, ctx: RecommendationContext
) -> EntrySnapshot:
    """Pure function: construct the immutable snapshot to be persisted
    alongside a newly created trade. No I/O — the caller is responsible for
    atomic persistence (see api/routers/paper_trading.py's paper_buy)."""
    execution_price = ctx.simulated_execution_price

    slippage_pct = _compute_execution_slippage_pct(ctx.recommendation_reference_price, execution_price)
    range_position = _classify_execution_range_position(
        execution_price, ctx.recommendation_entry_low, ctx.recommendation_entry_high
    )
    reward_to_risk = _compute_reward_to_risk_ratio(
        ctx.recommendation_reference_price, ctx.recommended_stop_loss, ctx.recommended_target_price
    )
    overrode_stop = _compute_user_overrode_recommendation(ctx.recommended_stop_loss, ctx.user_selected_stop_loss)
    overrode_target = _compute_user_overrode_recommendation(ctx.recommended_target_price, ctx.user_selected_target_price)
    user_overrode_recommendation = (
        None if overrode_stop is None and overrode_target is None
        else bool(overrode_stop) or bool(overrode_target)
    )

    return EntrySnapshot(
        paper_trade_id=paper_trade_id,
        user_id=user_id,
        symbol=symbol,
        market=market,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        evidence_source=ctx.evidence_source,
        daily_pick_run_id=ctx.daily_pick_run_id,
        daily_pick_rank=ctx.daily_pick_rank,
        recommendation_signal=ctx.recommendation_signal,
        recommendation_generated_at=ctx.recommendation_generated_at,
        recommendation_reference_price=ctx.recommendation_reference_price,
        recommendation_entry_low=ctx.recommendation_entry_low,
        recommendation_entry_high=ctx.recommendation_entry_high,
        simulated_execution_price=execution_price,
        execution_slippage_pct=slippage_pct,
        execution_range_position=range_position.value,
        recommended_stop_loss=ctx.recommended_stop_loss,
        recommended_target_price=ctx.recommended_target_price,
        user_selected_stop_loss=ctx.user_selected_stop_loss,
        user_selected_target_price=ctx.user_selected_target_price,
        user_overrode_recommendation=user_overrode_recommendation,
        reward_to_risk_ratio=reward_to_risk,
        confidence_score=ctx.confidence_score,
        technical_signal=ctx.technical_signal,
        technical_rsi=ctx.technical_rsi,
        technical_macd_diff=ctx.technical_macd_diff,
        fundamental_score=ctx.fundamental_score,
        sentiment_score=ctx.sentiment_score,
        sentiment_label=ctx.sentiment_label,
        market_regime_trend=ctx.market_regime_trend,
        market_regime_score_adj=ctx.market_regime_score_adj,
        market_regime_reason=ctx.market_regime_reason,
        recommendation_reasoning=ctx.recommendation_reasoning,
        model_version=ctx.model_version,
        verification_levels=_compute_verification_levels(ctx),
    )
