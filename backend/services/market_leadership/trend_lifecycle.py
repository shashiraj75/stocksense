"""
Trend Lifecycle classification (methodology ml-tl-1.0.0).

Deterministic, auditable rules — no ML classifier in v1 (see architecture
doc §4.3 for the full rule table and rationale). Pure function of an
as-of-sliced price DataFrame; no I/O.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from services.market_leadership.configuration import (
    TREND_MIN_SESSIONS, TREND_MA_SHORT, TREND_MA_MEDIUM, TREND_MA_LONG,
    TREND_FLAT_SLOPE_PCT_PER_DAY, TREND_BASE_BAND_PCT,
    TREND_EARLY_MAX_ABOVE_LONG_MA_PCT, TREND_EXTENDED_ABOVE_LONG_MA_PCT,
    TREND_EXTENDED_ABOVE_63D_LOW_PCT, TREND_HIGH_VOLUME_MULT,
    TREND_HIGH_VOLUME_DECLINE_PCT, TREND_DISTRIBUTION_MIN_EVENTS,
    TREND_RECENT_CROSS_SESSIONS, TREND_METHODOLOGY_VERSION,
)
from services.market_leadership.contracts import (
    TrendLifecycle, TrendLifecycleState, ExtensionRisk, ReasonCode,
)
from services.market_leadership.sessions import slice_as_of


def _slope_pct_per_day(ma: pd.Series, lookback: int = 10) -> float | None:
    if ma is None or len(ma.dropna()) < lookback + 1:
        return None
    recent = ma.dropna().tail(lookback + 1)
    start, end = float(recent.iloc[0]), float(recent.iloc[-1])
    if start == 0 or not np.isfinite(start) or not np.isfinite(end):
        return None
    return (end - start) / start / lookback * 100.0


def _higher_high_higher_low(df: pd.DataFrame, lookback: int = 63) -> bool | None:
    if len(df) < lookback:
        return None
    window = df.tail(lookback)
    mid = len(window) // 2
    first_half, second_half = window.iloc[:mid], window.iloc[mid:]
    return (
        float(second_half["High"].max()) > float(first_half["High"].max())
        and float(second_half["Low"].min()) > float(first_half["Low"].min())
    )


def _lower_lows(df: pd.DataFrame, lookback: int = 63) -> bool | None:
    if len(df) < lookback:
        return None
    window = df.tail(lookback)
    mid = len(window) // 2
    first_half, second_half = window.iloc[:mid], window.iloc[mid:]
    return float(second_half["Low"].min()) < float(first_half["Low"].min())


def _high_volume_decline_count(df: pd.DataFrame, lookback: int = 21) -> int | None:
    if len(df) < 55:  # need the 50d avg-volume baseline plus lookback
        return None
    vol_avg_50 = df["Volume"].rolling(50).mean()
    window = df.tail(lookback).copy()
    window["pct_chg"] = window["Close"].pct_change() * 100.0
    window["vol_avg_50"] = vol_avg_50.reindex(window.index)
    events = window[
        (window["Volume"] >= TREND_HIGH_VOLUME_MULT * window["vol_avg_50"])
        & (window["pct_chg"] <= TREND_HIGH_VOLUME_DECLINE_PCT)
    ]
    return int(len(events))


def _volume_confirmation_ratio(df: pd.DataFrame, lookback: int = 21) -> float | None:
    if len(df) < lookback + 1:
        return None
    window = df.tail(lookback + 1).copy()
    window["chg"] = window["Close"].diff()
    up_vol = window.loc[window["chg"] > 0, "Volume"].sum()
    down_vol = window.loc[window["chg"] < 0, "Volume"].sum()
    if down_vol <= 0:
        return None if up_vol == 0 else float("inf")
    return round(float(up_vol / down_vol), 3)


def compute_trend_inputs(price_df: pd.DataFrame, as_of: date) -> dict:
    """All rule inputs, each independently None-able so evidence_completeness
    reflects exactly what was computable."""
    df = slice_as_of(price_df, as_of) if price_df is not None else None
    if df is None or len(df) < TREND_MIN_SESSIONS:
        return {"insufficient_history": True, "sessions": 0 if df is None else len(df)}

    close = df["Close"]
    ma20 = close.rolling(TREND_MA_SHORT).mean()
    ma50 = close.rolling(TREND_MA_MEDIUM).mean()
    ma200 = close.rolling(TREND_MA_LONG).mean()

    last_close = float(close.iloc[-1])
    last_ma20 = float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else None
    last_ma50 = float(ma50.iloc[-1]) if pd.notna(ma50.iloc[-1]) else None
    last_ma200 = float(ma200.iloc[-1]) if pd.notna(ma200.iloc[-1]) else None

    price_vs_long_pct = (
        (last_close - last_ma200) / last_ma200 * 100.0 if last_ma200 else None
    )
    long_slope = _slope_pct_per_day(ma200)
    medium_slope = _slope_pct_per_day(ma50)

    high_252 = float(close.tail(252).max()) if len(close) >= 60 else float(close.max())
    low_252 = float(close.tail(252).min()) if len(close) >= 60 else float(close.min())
    low_63 = float(close.tail(63).min()) if len(close) >= 63 else low_252
    drawdown_from_high_pct = (
        (last_close - high_252) / high_252 * 100.0 if high_252 else None
    )
    above_63d_low_pct = (
        (last_close - low_63) / low_63 * 100.0 if low_63 else None
    )

    golden_cross_recent = None
    if len(ma50.dropna()) > TREND_RECENT_CROSS_SESSIONS and len(ma200.dropna()) > TREND_RECENT_CROSS_SESSIONS:
        recent_diff = (ma50 - ma200).tail(TREND_RECENT_CROSS_SESSIONS)
        golden_cross_recent = bool((recent_diff.iloc[0] <= 0) and (recent_diff.iloc[-1] > 0))

    return {
        "insufficient_history": False,
        "sessions": len(df),
        "last_close": last_close,
        "ma20": last_ma20, "ma50": last_ma50, "ma200": last_ma200,
        "price_vs_long_ma_pct": None if price_vs_long_pct is None else round(price_vs_long_pct, 2),
        "long_ma_slope_pct_per_day": None if long_slope is None else round(long_slope, 4),
        "medium_ma_slope_pct_per_day": None if medium_slope is None else round(medium_slope, 4),
        "drawdown_from_252d_high_pct": None if drawdown_from_high_pct is None else round(drawdown_from_high_pct, 2),
        "above_63d_low_pct": None if above_63d_low_pct is None else round(above_63d_low_pct, 2),
        "ma_order_bullish": (
            last_ma20 is not None and last_ma50 is not None and last_ma200 is not None
            and last_ma20 > last_ma50 > last_ma200
        ),
        "higher_high_higher_low": _higher_high_higher_low(df),
        "lower_lows": _lower_lows(df),
        "high_volume_decline_count_21d": _high_volume_decline_count(df),
        "volume_confirmation_ratio_21d": _volume_confirmation_ratio(df),
        "golden_cross_recent": golden_cross_recent,
    }


def classify_trend_lifecycle(
    inputs: dict, rs_trend_improving: bool | None = None,
) -> tuple[TrendLifecycleState, ExtensionRisk, list[str], float]:
    """Deterministic classification. Returns (state, extension_risk,
    reason_codes, evidence_completeness)."""
    if inputs.get("insufficient_history"):
        return (
            TrendLifecycleState.INSUFFICIENT_DATA, ExtensionRisk.UNKNOWN,
            [ReasonCode.INSUFFICIENT_HISTORY.value], 0.0,
        )

    keys = [
        "price_vs_long_ma_pct", "long_ma_slope_pct_per_day", "medium_ma_slope_pct_per_day",
        "drawdown_from_252d_high_pct", "above_63d_low_pct", "ma_order_bullish",
        "higher_high_higher_low", "lower_lows", "high_volume_decline_count_21d",
        "volume_confirmation_ratio_21d", "golden_cross_recent",
    ]
    completeness = sum(1 for k in keys if inputs.get(k) is not None) / len(keys)

    price_vs_long = inputs.get("price_vs_long_ma_pct")
    long_slope = inputs.get("long_ma_slope_pct_per_day")
    medium_slope = inputs.get("medium_ma_slope_pct_per_day")
    ma_bullish = inputs.get("ma_order_bullish")
    hh_hl = inputs.get("higher_high_higher_low")
    lower_lows = inputs.get("lower_lows")
    hv_declines = inputs.get("high_volume_decline_count_21d") or 0
    drawdown = inputs.get("drawdown_from_252d_high_pct")
    above_63d_low = inputs.get("above_63d_low_pct")
    golden_cross = inputs.get("golden_cross_recent")

    reasons: list[str] = []
    long_ma_falling = long_slope is not None and long_slope < 0
    long_ma_flat = long_slope is not None and abs(long_slope) < TREND_FLAT_SLOPE_PCT_PER_DAY

    if price_vs_long is None:
        return (
            TrendLifecycleState.INSUFFICIENT_DATA, ExtensionRisk.UNKNOWN,
            [ReasonCode.INSUFFICIENT_HISTORY.value], completeness,
        )

    # DISTRIBUTION_RISK — checked early: warning signs override a bullish-looking MA order.
    if price_vs_long > 0 and (
        hv_declines >= TREND_DISTRIBUTION_MIN_EVENTS
        or (ma_bullish is False and medium_slope is not None and medium_slope < 0)
    ):
        reasons.append(ReasonCode.HIGH_VOLUME_DECLINES.value)
        if long_ma_falling:
            reasons.append(ReasonCode.LONG_MA_FALLING.value)
        return TrendLifecycleState.DISTRIBUTION_RISK, ExtensionRisk.MODERATE, reasons, completeness

    # DECLINING
    if (price_vs_long is not None and price_vs_long < 0 and long_ma_falling) or (
        lower_lows and price_vs_long is not None and price_vs_long < 0
    ):
        reasons.append(ReasonCode.BELOW_LONG_MA.value)
        if long_ma_falling:
            reasons.append(ReasonCode.LONG_MA_FALLING.value)
        if lower_lows:
            reasons.append(ReasonCode.LOWER_LOWS.value)
        return TrendLifecycleState.DECLINING, ExtensionRisk.LOW, reasons, completeness

    # BASE_FORMING
    if (
        long_ma_flat
        and abs(price_vs_long) <= TREND_BASE_BAND_PCT
        and drawdown is not None and drawdown >= -15.0
    ):
        reasons.append(ReasonCode.NEAR_LONG_MA_BASE.value)
        reasons.append(ReasonCode.LONG_MA_FLAT.value)
        reasons.append(ReasonCode.VOLATILITY_CONTRACTION.value)
        return TrendLifecycleState.BASE_FORMING, ExtensionRisk.LOW, reasons, completeness

    # EXTENDED_ADVANCE — checked before CONFIRMED so a strong-but-extended
    # trend is never presented as merely "confirmed" (extension is a
    # separate, non-optional disclosure).
    if price_vs_long > TREND_EXTENDED_ABOVE_LONG_MA_PCT or (
        above_63d_low is not None and above_63d_low > TREND_EXTENDED_ABOVE_63D_LOW_PCT
    ):
        reasons.append(ReasonCode.ABOVE_RISING_LONG_MA.value)
        reasons.append(ReasonCode.EXCESSIVE_EXTENSION.value)
        if ma_bullish:
            reasons.append(ReasonCode.MA_ORDER_BULLISH.value)
        return TrendLifecycleState.EXTENDED_ADVANCE, ExtensionRisk.HIGH, reasons, completeness

    # CONFIRMED_ADVANCE
    if ma_bullish and (hh_hl or long_slope is not None and long_slope > 0):
        reasons.append(ReasonCode.MA_ORDER_BULLISH.value)
        reasons.append(ReasonCode.ABOVE_RISING_LONG_MA.value)
        if hh_hl:
            reasons.append(ReasonCode.HIGHER_HIGHS_HIGHER_LOWS.value)
        if rs_trend_improving:
            reasons.append(ReasonCode.RS_IMPROVING.value)
        return TrendLifecycleState.CONFIRMED_ADVANCE, ExtensionRisk.MODERATE, reasons, completeness

    # EARLY_ADVANCE
    if (
        price_vs_long is not None and 0 <= price_vs_long <= TREND_EARLY_MAX_ABOVE_LONG_MA_PCT
        and (golden_cross or (medium_slope is not None and medium_slope > 0))
    ):
        reasons.append(ReasonCode.SHORT_MA_ABOVE_MEDIUM_MA.value)
        if golden_cross:
            reasons.append(ReasonCode.RECENT_GOLDEN_CROSS.value)
        if rs_trend_improving:
            reasons.append(ReasonCode.RS_IMPROVING.value)
        return TrendLifecycleState.EARLY_ADVANCE, ExtensionRisk.LOW, reasons, completeness

    # No rule fired decisively — default to the most conservative
    # non-INSUFFICIENT_DATA read: BASE_FORMING if near the long MA and
    # DECLINING otherwise, always disclosing low evidence completeness.
    if price_vs_long is not None and abs(price_vs_long) <= TREND_BASE_BAND_PCT * 2:
        reasons.append(ReasonCode.NEAR_LONG_MA_BASE.value)
        return TrendLifecycleState.BASE_FORMING, ExtensionRisk.LOW, reasons, completeness
    reasons.append(ReasonCode.BELOW_LONG_MA.value if (price_vs_long or 0) < 0 else ReasonCode.ABOVE_RISING_LONG_MA.value)
    return TrendLifecycleState.DECLINING, ExtensionRisk.LOW, reasons, completeness


def build_trend_lifecycle(
    symbol: str, market: str, price_df: pd.DataFrame, as_of: date,
    rs_trend_improving: bool | None = None,
) -> TrendLifecycle:
    inputs = compute_trend_inputs(price_df, as_of)
    state, ext_risk, reasons, completeness = classify_trend_lifecycle(inputs, rs_trend_improving)
    return TrendLifecycle(
        symbol=symbol,
        market=market,
        state=state,
        extension_risk=ext_risk,
        evidence_completeness=round(completeness, 4),
        price_vs_long_ma_pct=inputs.get("price_vs_long_ma_pct"),
        long_ma_slope_pct_per_day=inputs.get("long_ma_slope_pct_per_day"),
        drawdown_from_252d_high_pct=inputs.get("drawdown_from_252d_high_pct"),
        high_volume_decline_count_21d=inputs.get("high_volume_decline_count_21d"),
        volume_confirmation_ratio_21d=inputs.get("volume_confirmation_ratio_21d"),
        calculation_as_of=as_of.isoformat(),
        methodology_version=TREND_METHODOLOGY_VERSION,
        reason_codes=reasons,
    )
