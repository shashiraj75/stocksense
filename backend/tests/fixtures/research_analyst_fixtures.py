"""
Hand-authored predict()-shaped fixtures for the Research Analyst Phase 1
tests. Shapes mirror the real result dict assembled at the end of
PredictionEngine.predict() (prediction_engine.py's `result = {...}`
block) — authored from that documented shape, NOT captured from any
production endpoint (Phase 1 spec stop condition 7).
"""

import copy


def make_predict_result(market: str = "IN", symbol: str = "RELIANCE", horizon: str = "medium") -> dict:
    """A fully-populated, plain-typed predict()-shaped dict."""
    return {
        "symbol": symbol,
        "market": market,
        "horizon": horizon,
        "generated_at": "2026-07-08T10:00:00+00:00",
        "data_timestamp": "2026-07-07T00:00:00",
        "price_reference": {
            "price": 2950.55,
            "source": "yahoo_daily_history",
            "price_basis": "adjusted_close",
            "as_of": "2026-07-07T00:00:00",
        },
        "signal": "BUY",
        "confidence": 68,
        "score_band": "strong",
        "composite_score": 71.4,
        "factor_contributions": {"technical": 24.1, "fundamental": 30.2, "sentiment": 8.3, "quality": 8.8},
        "confidence_score": 68.0,
        "confidence_band": "high",
        "confidence_breakdown": {"data_completeness": 92.0, "signal_agreement": 74.0},
        "bull_case": ["Revenue growth accelerating", "Sector momentum positive"],
        "bear_case": ["Valuation above sector median"],
        "current_price": 2950.55,
        "target_price": 3320.0,
        "trade_levels": {"entry_low": 2920.0, "entry_high": 2975.0, "stop_loss": 2755.0, "take_profit": 3320.0, "risk_reward": 1.9},
        "reasoning": ["Strong fundamentals", "Uptrend intact"],
        "technical": {"rsi": 58.2, "macd": 12.4, "trend": "UP"},
        "fundamental_score": 76.0,
        "sentiment_score": 61.0,
        "market_regime": {"trend": "BULLISH", "score_adj": 3, "reason": "Index above 50DMA"},
        "global_context": {"score": 62.0, "levels": {"sp500": 6100.0}, "changes": {"sp500_pct": 0.4}},
        "quality_factors": {
            "score": 70.0, "sector": "Energy", "piotroski": 7, "altman_z": 3.4,
            "altman_zone": "safe", "accruals_ratio": -0.02, "buffett_passed": 8,
            "buffett_total": 11, "buffett_checklist": [], "breakdown": {"profitability": 24.0},
        },
        "business_quality": {
            "score": 72.5, "grade": "buy", "confidence": 91.7,
            "strengths": ["Durable moat"], "weaknesses": ["Capital intensity"],
            "risks": [], "explanation": "Quality compounder characteristics.",
        },
        # None for IN (market-structural absence); a real dict for US.
        "financial_strength": None if market == "IN" else {
            "score": 66.0, "grade": "hold", "confidence": 88.0,
            "strengths": ["Coverage ratio"], "weaknesses": [], "risks": [],
            "explanation": "Adequate solvency.",
        },
        "growth_intelligence": {
            "score": 64.0, "grade": "buy", "confidence": 79.0,
            "strengths": ["3Y revenue CAGR"], "weaknesses": [], "risks": [],
            "explanation": "Real, durable growth.",
        },
        "valuation_intelligence": {
            "score": 41.0, "grade": "hold", "confidence": 83.0,
            "strengths": [], "weaknesses": ["Premium to sector"], "risks": [],
            "explanation": "Moderately expensive.",
        },
        "weights_used": {"tech": 0.4, "fund": 0.45, "sentiment": 0.15, "vol_regime": "NORMAL"},
    }


def make_legacy_daily_picks_shaped() -> dict:
    """A legacy Daily-Picks-style dict: bare numeric growth_score/
    valuation_score where the modern engine dicts would be — must never
    become modern evidence (Contract §5.2)."""
    fixture = make_predict_result(market="IN")
    for key in ("growth_intelligence", "valuation_intelligence"):
        fixture.pop(key)
    fixture["growth_score"] = 55.0
    fixture["valuation_score"] = 60.0
    return fixture


def deep_snapshot_of(obj):
    """An independent deep copy used to prove input immutability."""
    return copy.deepcopy(obj)
