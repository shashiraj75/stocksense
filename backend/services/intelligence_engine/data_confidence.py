"""
Data Confidence — Intelligence Engine V1 (Epic 007 Phase 3B).

A 0-100 score describing how much a downstream consumer should trust the
data available for a symbol — not a quality/opportunity score (that's a
future component), purely "how complete and fresh is what we have". Feeds
telemetry only in Phase 3B; no production scoring path reads this yet.

Weights are configurable (env-overridable) but ship with defaults that
sum to 100 — price and volume availability are weighted heaviest since a
missing price/volume basically stops every other analysis cold; fresh
history and completeness carry lower per-item weight since a gap there
degrades quality gradually rather than making analysis impossible.
"""
import os
from dataclasses import dataclass

_W_PRICE = float(os.getenv("CONFIDENCE_WEIGHT_PRICE", 25))
_W_VOLUME = float(os.getenv("CONFIDENCE_WEIGHT_VOLUME", 20))
_W_FUNDAMENTALS = float(os.getenv("CONFIDENCE_WEIGHT_FUNDAMENTALS", 20))
_W_FINANCIALS = float(os.getenv("CONFIDENCE_WEIGHT_FINANCIALS", 15))
_W_FRESHNESS = float(os.getenv("CONFIDENCE_WEIGHT_FRESHNESS", 10))
_W_HISTORY = float(os.getenv("CONFIDENCE_WEIGHT_HISTORY", 10))

_TOTAL_WEIGHT = _W_PRICE + _W_VOLUME + _W_FUNDAMENTALS + _W_FINANCIALS + _W_FRESHNESS + _W_HISTORY

# A symbol needs at least this many days of price history for the
# freshness/history components to score at full weight — short of that,
# credit scales down proportionally rather than being all-or-nothing.
_FULL_HISTORY_DAYS = int(os.getenv("CONFIDENCE_FULL_HISTORY_DAYS", 252))  # ~1 trading year
_FRESH_QUOTE_MAX_DAYS = int(os.getenv("CONFIDENCE_FRESH_QUOTE_MAX_DAYS", 1))


@dataclass(frozen=True)
class DataConfidenceInputs:
    price_available: bool
    volume_available: bool
    fundamentals_completeness_pct: float | None  # 0-100, None = treat as 0 (unknown = no credit, not partial credit)
    financials_completeness_pct: float | None    # 0-100
    quote_age_days: float | None                 # None = treat as maximally stale
    history_depth_days: int | None                # None = treat as 0


def compute_data_confidence(inputs: DataConfidenceInputs) -> int:
    """Returns an integer 0-100. Missing data reduces confidence — never
    raises, never fabricates a value for something genuinely unknown
    (unknown scores as 0 credit for that component, not skipped/excluded
    from the denominator, so it's not possible to look artificially
    confident just by having fewer data points collected)."""
    score = 0.0

    if inputs.price_available:
        score += _W_PRICE
    if inputs.volume_available:
        score += _W_VOLUME

    fundamentals_pct = max(0.0, min(100.0, inputs.fundamentals_completeness_pct or 0.0))
    score += _W_FUNDAMENTALS * (fundamentals_pct / 100.0)

    financials_pct = max(0.0, min(100.0, inputs.financials_completeness_pct or 0.0))
    score += _W_FINANCIALS * (financials_pct / 100.0)

    if inputs.quote_age_days is not None:
        freshness_ratio = 1.0 if inputs.quote_age_days <= _FRESH_QUOTE_MAX_DAYS else max(
            0.0, 1.0 - (inputs.quote_age_days - _FRESH_QUOTE_MAX_DAYS) / 10.0
        )
        score += _W_FRESHNESS * freshness_ratio

    history_days = inputs.history_depth_days or 0
    history_ratio = min(1.0, history_days / _FULL_HISTORY_DAYS) if _FULL_HISTORY_DAYS > 0 else 0.0
    score += _W_HISTORY * history_ratio

    # Normalize against configured total weight (in case env overrides don't
    # sum to exactly 100) and clamp — always a clean 0-100 integer.
    normalized = (score / _TOTAL_WEIGHT) * 100.0 if _TOTAL_WEIGHT > 0 else 0.0
    return int(round(max(0.0, min(100.0, normalized))))
