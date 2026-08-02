"""
Typed contracts for the Market Leadership and Trend Context Layer.

Pure data definitions — no I/O, no calculation logic. Every contract is
JSON-serializable via `dataclasses.asdict`. String enums are plain `str`
subclasses so serialized payloads stay human-readable and stable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class DataQualityStatus(str, Enum):
    OK = "OK"
    PARTIAL_HISTORY = "PARTIAL_HISTORY"
    STALE = "STALE"
    FAILED = "FAILED"


class RsTrend(str, Enum):
    IMPROVING = "IMPROVING"
    FLAT = "FLAT"
    WEAKENING = "WEAKENING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class GroupState(str, Enum):
    LEADING = "LEADING"
    IMPROVING = "IMPROVING"
    WEAKENING = "WEAKENING"
    LAGGING = "LAGGING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class LeadershipConcentration(str, Enum):
    BROAD = "BROAD"
    MODERATE = "MODERATE"
    CONCENTRATED = "CONCENTRATED"
    DETERIORATING = "DETERIORATING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TrendLifecycleState(str, Enum):
    BASE_FORMING = "BASE_FORMING"
    EARLY_ADVANCE = "EARLY_ADVANCE"
    CONFIRMED_ADVANCE = "CONFIRMED_ADVANCE"
    EXTENDED_ADVANCE = "EXTENDED_ADVANCE"
    DISTRIBUTION_RISK = "DISTRIBUTION_RISK"
    DECLINING = "DECLINING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ExtensionRisk(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class BreadthState(str, Enum):
    HEALTHY = "HEALTHY"
    NARROWING = "NARROWING"
    DETERIORATING = "DETERIORATING"
    RECOVERY = "RECOVERY"
    WASHED_OUT = "WASHED_OUT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ReasonCode(str, Enum):
    ABOVE_RISING_LONG_MA = "ABOVE_RISING_LONG_MA"
    BELOW_LONG_MA = "BELOW_LONG_MA"
    SHORT_MA_ABOVE_MEDIUM_MA = "SHORT_MA_ABOVE_MEDIUM_MA"
    MA_ORDER_BULLISH = "MA_ORDER_BULLISH"
    RS_IMPROVING = "RS_IMPROVING"
    RS_WEAKENING = "RS_WEAKENING"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    VOLUME_CONFIRMATION = "VOLUME_CONFIRMATION"
    EXCESSIVE_EXTENSION = "EXCESSIVE_EXTENSION"
    HIGH_VOLUME_DECLINES = "HIGH_VOLUME_DECLINES"
    LONG_MA_FALLING = "LONG_MA_FALLING"
    LONG_MA_FLAT = "LONG_MA_FLAT"
    HIGHER_HIGHS_HIGHER_LOWS = "HIGHER_HIGHS_HIGHER_LOWS"
    LOWER_LOWS = "LOWER_LOWS"
    RECENT_GOLDEN_CROSS = "RECENT_GOLDEN_CROSS"
    VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"
    NEAR_LONG_MA_BASE = "NEAR_LONG_MA_BASE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING_BARS = "MISSING_BARS"
    STALE_DATA = "STALE_DATA"
    BROAD_GROUP_PARTICIPATION = "BROAD_GROUP_PARTICIPATION"
    CONCENTRATED_GROUP_LEADERSHIP = "CONCENTRATED_GROUP_LEADERSHIP"
    BREADTH_DIVERGENCE = "BREADTH_DIVERGENCE"
    LOW_COVERAGE = "LOW_COVERAGE"


@dataclass
class StockRelativeStrength:
    symbol: str
    market: str                      # "IN" | "US" — never inferred
    stock_rs_score: int | None       # 0-100 (== percentile in v1)
    stock_rs_percentile: float | None
    benchmark_relative_return: float | None   # composite, percentage points
    universe_relative_rank: int | None        # 1 = strongest
    universe_size: int | None
    rs_1m: float | None
    rs_3m: float | None
    rs_6m: float | None
    rs_12m: float | None
    rs_trend: RsTrend
    rs_acceleration: float | None
    data_coverage: float             # 0-1, worst-window bar coverage actually used
    data_quality_status: DataQualityStatus
    calculation_as_of: str           # ISO date of the as-of completed session
    universe_id: str
    universe_version: str
    methodology_version: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GroupLeadership:
    market: str
    group_kind: str                  # "sector" | "industry"
    sector_name: str
    industry_name: str
    sector_rs_score: float | None
    industry_rs_score: float | None
    sector_rank: int | None
    industry_rank: int | None
    sector_rank_change: int | None   # vs previous snapshot; None when unknown
    industry_rank_change: int | None
    leadership_breadth: float | None         # share of members RS pct >= 60
    participation_ratio: float | None        # share above own 50d MA
    median_member_rs: float | None
    weighted_member_rs: float | None         # capped-weight diagnostic
    improving_member_ratio: float | None
    new_high_participation: float | None
    group_trend: str                 # "UP" | "FLAT" | "DOWN" | "UNKNOWN"
    group_state: GroupState
    concentration: LeadershipConcentration
    member_count: int
    scored_member_count: int
    calculation_as_of: str
    classification_source: str
    classification_version: str
    classification_pit_safe: bool
    universe_id: str
    universe_version: str
    methodology_version: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrendLifecycle:
    symbol: str
    market: str
    state: TrendLifecycleState
    extension_risk: ExtensionRisk
    evidence_completeness: float     # 0-1: fraction of rule inputs computable
    price_vs_long_ma_pct: float | None
    long_ma_slope_pct_per_day: float | None
    drawdown_from_252d_high_pct: float | None
    high_volume_decline_count_21d: int | None
    volume_confirmation_ratio_21d: float | None
    calculation_as_of: str
    methodology_version: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BreadthComponent:
    name: str
    value: float | None
    numerator: int | None
    denominator: int | None
    coverage: float | None


@dataclass
class MarketBreadth:
    market: str
    scope: str                       # "market" or a group name
    state: BreadthState
    pct_above_20dma: float | None
    pct_above_50dma: float | None
    pct_above_200dma: float | None
    advance_decline_ratio_21d: float | None
    new_highs_63d: int | None
    new_lows_63d: int | None
    improving_rs_ratio: float | None
    direction_5d: str                # "IMPROVING" | "FLAT" | "DETERIORATING" | "UNKNOWN"
    components: list[BreadthComponent]
    coverage: float                  # scored members / universe members
    universe_size: int
    scored_count: int
    expected_session: str            # latest completed session the calc targets
    calculation_as_of: str
    universe_id: str
    universe_version: str
    methodology_version: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WhyNowExplanation:
    summary: str
    supporting_factors: list[str]
    caution_factors: list[str]
    stock_rs_context: str | None
    industry_context: str | None
    sector_context: str | None
    trend_context: str | None
    breadth_context: str | None
    extension_risk: str
    data_freshness: str
    data_quality: str
    calculation_as_of: str
    methodology_version: str
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
