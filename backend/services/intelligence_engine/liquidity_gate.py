"""
Liquidity Gate — Intelligence Engine V1, Universe Builder component
(Epic 007 Phase 3B).

Rejects only obviously non-investable securities on liquidity grounds — a
real order in a genuine, actively-traded stock should be able to fill
without materially moving the price. This is deliberately a coarse,
permissive gate (per the brief: "reject only obvious non-investable
securities"), not the precision liquidity scoring a full execution-quality
system would need — that's a different, future problem.

All thresholds are configurable via environment variables with sensible
defaults, never hardcoded constants in the gate logic itself — mirrors the
existing MIN_MCAP_CR/MIN_MCAP_USD_M pattern in daily_picks.py.
"""
import os
from dataclasses import dataclass

LIQUIDITY_OK = "OK"
LIQUIDITY_LOW_AVG_VALUE = "LOW_AVG_DAILY_VALUE"
LIQUIDITY_LOW_AVG_VOLUME = "LOW_AVG_DAILY_VOLUME"
LIQUIDITY_TOO_MANY_ZERO_VOLUME_DAYS = "TOO_MANY_ZERO_VOLUME_DAYS"
LIQUIDITY_PRICE_TOO_LOW = "PRICE_TOO_LOW"
LIQUIDITY_FREE_FLOAT_TOO_LOW = "FREE_FLOAT_TOO_LOW"


@dataclass(frozen=True)
class LiquidityThresholds:
    min_avg_daily_value: float   # in the market's own currency (₹ for IN, $ for US)
    min_avg_daily_volume: float  # shares/day
    max_zero_volume_days: int    # out of the lookback window supplied by the caller
    min_price: float             # per-share/unit floor — catches true penny-stock noise
    min_free_float_pct: float | None  # None = don't gate on float at all (many sources lack it)


def default_thresholds(market: str) -> LiquidityThresholds:
    """Reads env vars per market, falling back to conservative-but-permissive
    defaults if unset. These are intentionally coarse — see module docstring."""
    prefix = "IN" if market == "IN" else "US"
    return LiquidityThresholds(
        min_avg_daily_value=float(os.getenv(f"LIQUIDITY_MIN_AVG_DAILY_VALUE_{prefix}", 2_00_00_000 if market == "IN" else 2_000_000)),
        min_avg_daily_volume=float(os.getenv(f"LIQUIDITY_MIN_AVG_DAILY_VOLUME_{prefix}", 50_000 if market == "IN" else 100_000)),
        max_zero_volume_days=int(os.getenv("LIQUIDITY_MAX_ZERO_VOLUME_DAYS", 2)),
        min_price=float(os.getenv(f"LIQUIDITY_MIN_PRICE_{prefix}", 10.0 if market == "IN" else 1.0)),
        min_free_float_pct=(
            float(os.getenv("LIQUIDITY_MIN_FREE_FLOAT_PCT"))
            if os.getenv("LIQUIDITY_MIN_FREE_FLOAT_PCT") is not None
            else None
        ),
    )


@dataclass(frozen=True)
class LiquidityResult:
    symbol: str
    passed: bool
    reason: str  # LIQUIDITY_OK when passed is True


def check_liquidity(
    symbol: str,
    market: str,
    *,
    avg_daily_value: float | None,
    avg_daily_volume: float | None,
    zero_volume_days: int | None,
    price: float | None,
    free_float_pct: float | None = None,
    thresholds: LiquidityThresholds | None = None,
) -> LiquidityResult:
    """Any input left as None is treated as "unknown, don't gate on it" —
    this gate rejects on positive evidence of illiquidity, not on missing
    inputs (data completeness is Data Confidence's job, not this gate's)."""
    t = thresholds or default_thresholds(market)

    if price is not None and price < t.min_price:
        return LiquidityResult(symbol, False, LIQUIDITY_PRICE_TOO_LOW)
    if avg_daily_value is not None and avg_daily_value < t.min_avg_daily_value:
        return LiquidityResult(symbol, False, LIQUIDITY_LOW_AVG_VALUE)
    if avg_daily_volume is not None and avg_daily_volume < t.min_avg_daily_volume:
        return LiquidityResult(symbol, False, LIQUIDITY_LOW_AVG_VOLUME)
    if zero_volume_days is not None and zero_volume_days > t.max_zero_volume_days:
        return LiquidityResult(symbol, False, LIQUIDITY_TOO_MANY_ZERO_VOLUME_DAYS)
    if free_float_pct is not None and t.min_free_float_pct is not None and free_float_pct < t.min_free_float_pct:
        return LiquidityResult(symbol, False, LIQUIDITY_FREE_FLOAT_TOO_LOW)

    return LiquidityResult(symbol, True, LIQUIDITY_OK)
