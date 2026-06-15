import asyncio
import time
import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary
from services.news_sentiment import NewsSentimentService
from services.global_context import get_global_context
from services.quality_factors import compute_all_quality_factors

MARKET_SUFFIX = {"US": "", "IN": ".NS"}
_news_svc = NewsSentimentService()

# Market regime index tickers
REGIME_TICKER = {"US": "^GSPC", "IN": "^NSEI"}

# ── Caches ────────────────────────────────────────────────────────────────────
# Prediction cache: { "SYMBOL:MARKET:HORIZON" -> (timestamp, result) }
_pred_cache: dict[str, tuple[float, dict]] = {}
_PRED_TTL = 15 * 60  # 15 minutes — fundamentals don't shift in 15 min

# Market regime cache: { "IN"|"US" -> (timestamp, result) }
_regime_cache: dict[str, tuple[float, dict]] = {}
_REGIME_TTL = 30 * 60  # 30 minutes — broad-market trend changes slowly


def _market_regime(market: str) -> dict:
    """
    Checks whether the broad market is in an uptrend or downtrend.
    Uses S&P 500 for US, NIFTY 50 for India.
    Returns: {"trend": "BULL"/"BEAR"/"SIDEWAYS", "score_adj": int}
    score_adj is applied to the composite score (+8 in bull, -8 in bear).
    """
    cached = _regime_cache.get(market)
    if cached and (time.time() - cached[0]) < _REGIME_TTL:
        return cached[1]
    try:
        ticker = yf.Ticker(REGIME_TICKER.get(market, "^GSPC"))
        df = ticker.history(period="6mo")
        if len(df) < 50:
            return {"trend": "SIDEWAYS", "score_adj": 0, "reason": "Insufficient regime data"}

        close = df["Close"]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        ema200 = close.ewm(span=200).mean().iloc[-1] if len(df) >= 200 else close.ewm(span=len(df)).mean().iloc[-1]
        current = close.iloc[-1]

        # 3-month momentum
        ret_3m = (current - close.iloc[-63]) / close.iloc[-63] if len(df) >= 63 else 0

        if current > ema50 and ret_3m > 0.03:
            trend = "BULL"
            adj = 8
            reason = f"Market in uptrend — {ret_3m*100:.1f}% gain over 3 months"
        elif current < ema50 and ret_3m < -0.03:
            trend = "BEAR"
            adj = -8
            reason = f"Market in downtrend — {ret_3m*100:.1f}% over 3 months"
        else:
            trend = "SIDEWAYS"
            adj = 0
            reason = "Market moving sideways"

        result = {"trend": trend, "score_adj": adj, "reason": reason}
        _regime_cache[market] = (time.time(), result)
        return result
    except Exception:
        return {"trend": "SIDEWAYS", "score_adj": 0, "reason": "Could not fetch market data"}


SCORE_BANDS = [
    (90, "Exceptional Opportunity"),   # BUY — very high confidence
    (80, "Strong Buy Candidate"),       # BUY — high confidence
    (65, "Good Watchlist Stock"),       # BUY — moderate confidence
    (55, "Neutral — Monitor"),          # HOLD
    (0,  "Avoid"),                      # SELL
]

def _score_label(score: int) -> str:
    for threshold, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "Avoid"


def _dynamic_weights(df: pd.DataFrame, horizon: str, regime: dict | None = None) -> dict:
    """
    Adjusts weights based on both volatility AND market regime:

    Volatility adjustment:
      High vol  → trust fundamentals more (technicals get noisy)
      Low vol   → technicals are more reliable

    Regime adjustment (applied on top of vol adjustment):
      BULL      → boost technicals + relative strength (momentum pays)
      BEAR      → boost fundamentals + quality/FCF (defensiveness pays)
      SIDEWAYS  → boost valuation weight (mean reversion pays)
    """
    base_weights = {
        "short":  {"tech": 0.70, "fund": 0.15, "sentiment": 0.15},
        "medium": {"tech": 0.40, "fund": 0.45, "sentiment": 0.15},
        "long":   {"tech": 0.15, "fund": 0.75, "sentiment": 0.10},
    }[horizon]

    vol_regime = "NORMAL"
    vol_20d = 0.0

    try:
        returns = df["Close"].pct_change().dropna()
        vol_20d = float(returns.iloc[-20:].std() * np.sqrt(252))

        if vol_20d > 0.35:
            shift = 0.10
            base_weights = {
                "tech": max(0.10, base_weights["tech"] - shift),
                "fund": min(0.80, base_weights["fund"] + shift),
                "sentiment": base_weights["sentiment"],
            }
            vol_regime = "HIGH"
        elif vol_20d < 0.15:
            shift = 0.08
            base_weights = {
                "tech": min(0.75, base_weights["tech"] + shift),
                "fund": max(0.05, base_weights["fund"] - shift),
                "sentiment": base_weights["sentiment"],
            }
            vol_regime = "LOW"
    except Exception:
        pass

    # ── Regime-aware weight adjustment ───────────────────────────────────────
    regime_trend = (regime or {}).get("trend", "SIDEWAYS")
    regime_shift = 0.08

    if regime_trend == "BULL":
        # Bull market: momentum and technicals pay — boost tech, trim fundamentals
        base_weights["tech"] = min(0.80, base_weights["tech"] + regime_shift)
        base_weights["fund"] = max(0.05, base_weights["fund"] - regime_shift)

    elif regime_trend == "BEAR":
        # Bear market: quality and fundamentals are defensive anchors
        base_weights["fund"] = min(0.85, base_weights["fund"] + regime_shift)
        base_weights["tech"] = max(0.05, base_weights["tech"] - regime_shift)

    # SIDEWAYS: default weights are fine — valuation-based mean reversion
    # is handled by the quality factor valuation sub-score

    # Normalise so weights sum to 1.0
    total = sum(base_weights[k] for k in ("tech", "fund", "sentiment"))
    if total > 0:
        for k in ("tech", "fund", "sentiment"):
            base_weights[k] = round(base_weights[k] / total, 4)

    return {
        **base_weights,
        "vol_regime": vol_regime,
        "vol_20d": round(vol_20d * 100, 1),
        "regime_applied": regime_trend,
    }


def _compute_risk_penalty(info: dict, df: pd.DataFrame, quality: dict | None = None) -> tuple[int, list[str]]:
    """
    Step 3 of the framework: compute a risk penalty to subtract from raw score.
    Risk factors reduce the score — they never add to it.

    Returns (penalty_points, penalty_reasons).
    """
    penalty = 0
    reasons: list[str] = []

    try:
        # High debt
        de = info.get("debtToEquity")
        if de and de > 300:
            penalty += 8
            reasons.append(f"High debt-to-equity ({de:.0f}%) — financial fragility risk")
        elif de and de > 200:
            penalty += 4
            reasons.append(f"Elevated debt-to-equity ({de:.0f}%) — leverage risk")

        # High beta
        beta = info.get("beta")
        if beta and beta > 2.0:
            penalty += 6
            reasons.append(f"High beta ({beta:.2f}) — amplified downside in market corrections")
        elif beta and beta > 1.6:
            penalty += 3
            reasons.append(f"Above-average beta ({beta:.2f}) — sensitive to market swings")

        # Negative FCF
        fcf = info.get("freeCashflow")
        if fcf is not None and fcf < 0:
            penalty += 5
            reasons.append("Negative free cash flow — company burning cash; survival risk in downturns")

        # Negative ROE (loss-making)
        roe = info.get("returnOnEquity")
        if roe and roe < -0.05:
            penalty += 5
            reasons.append(f"Negative ROE ({roe*100:.1f}%) — destroying shareholder value")

        # Max drawdown from risk_management sub-score
        if quality:
            risk_bd = quality.get("breakdown", {}).get("risk_management", {})
            risk_sc = risk_bd.get("score", 50) if isinstance(risk_bd, dict) else 50
            if risk_sc < 35:
                penalty += 5
                reasons.append("Poor risk profile — high drawdown and/or low risk-adjusted returns")
            elif risk_sc < 45:
                penalty += 2

        # Earnings volatility — high EPS std dev signals unpredictability
        if len(df) >= 60:
            try:
                returns = df["Close"].pct_change().dropna()
                quarterly_vol = returns.resample("QE").std() if hasattr(returns.index, "freq") else None
                # Simpler proxy: rolling 63-day vol std
                vol_series = [returns.iloc[max(0,i-63):i].std() for i in range(63, len(returns), 21)]
                if len(vol_series) >= 4:
                    vol_consistency = np.std(vol_series) / (np.mean(vol_series) + 1e-10)
                    if vol_consistency > 0.5:
                        penalty += 4
                        reasons.append("High earnings/return volatility — unpredictable stock behaviour increases execution risk")
                    elif vol_consistency > 0.35:
                        penalty += 2
            except Exception:
                pass

    except Exception:
        pass

    return min(penalty, 20), reasons  # cap penalty at 20 to prevent over-penalising


class PredictionEngine:
    async def predict(self, symbol: str, market: str, horizon: str) -> dict:
        # ── Prediction cache — skip ALL work on repeated requests ───────────────
        cache_key = f"{symbol}:{market}:{horizon}"
        cached = _pred_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _PRED_TTL:
            return cached[1]

        suffix = MARKET_SUFFIX.get(market, "")
        loop = asyncio.get_event_loop()

        # ── Round 1: fetch price history + ticker.info + regime in parallel ─────
        period = {"short": "6mo", "medium": "2y", "long": "5y"}[horizon]

        def _fetch_history():
            return yf.Ticker(symbol + suffix).history(period=period)

        def _fetch_info():
            try:
                return yf.Ticker(symbol + suffix).info
            except Exception:
                return {}

        df, info, regime = await asyncio.gather(
            loop.run_in_executor(None, _fetch_history),
            loop.run_in_executor(None, _fetch_info),
            loop.run_in_executor(None, _market_regime, market),
        )

        if df.empty:
            return {"error": "No data found for symbol"}

        df = compute_indicators(df)
        tech_signal = get_signal_summary(df)
        ratio_score = self._fundamental_score(info, horizon, market)

        # ── Hard quality gate — reject fundamentally broken stocks ──────────────
        gate_passed, gate_reasons = self._quality_gate(info, df, horizon)
        if not gate_passed:
            return {
                "symbol": symbol,
                "market": market,
                "horizon": horizon,
                "signal": "REJECTED",
                "rejection_reasons": gate_reasons,
                "confidence": 0,
                "current_price": round(float(df["Close"].iloc[-1]), 2) if not df.empty else None,
            }

        # ── Round 2: news + global_ctx + quality + deep_fund all in parallel ────
        async def _get_news():
            return await _news_svc.get_news_with_sentiment(symbol, market, 10)

        def _get_global_ctx():
            if market != "IN":
                return {}
            try:
                return get_global_context(symbol)
            except Exception:
                return {}

        def _get_quality():
            if market != "IN":
                return {}
            try:
                return compute_all_quality_factors(symbol, yf.Ticker(symbol + suffix), df, info, horizon)
            except Exception:
                return {}

        def _get_deep_fund():
            if horizon not in ("medium", "long"):
                return None
            return self._deep_fundamental_score(yf.Ticker(symbol + suffix), horizon)

        news_data, global_ctx, quality, deep_score_raw = await asyncio.gather(
            _get_news(),
            loop.run_in_executor(None, _get_global_ctx),
            loop.run_in_executor(None, _get_quality),
            loop.run_in_executor(None, _get_deep_fund),
        )

        sentiment_score = self._aggregate_sentiment(news_data["articles"])

        # Blend deep fundamentals for medium/long
        if deep_score_raw is not None:
            blend = 0.3 if horizon == "medium" else 0.6
            blended = round(ratio_score["score"] * (1 - blend) + deep_score_raw["score"] * blend)
            fund_score = {
                "score": max(0, min(100, blended)),
                "reasons": ratio_score["reasons"] + deep_score_raw["reasons"],
                "deep_available": deep_score_raw["available"],
            }
        else:
            fund_score = ratio_score

        # Analyst consensus + 52W position (fast — computed from info/df already fetched)
        analyst_score = self._analyst_score(info, market)
        week52_score = self._week52_score(df, info)

        # Dynamic weights based on volatility + market regime
        weights = _dynamic_weights(df, horizon, regime=regime)

        signal, confidence, reasoning, score_band = self._composite_signal(
            tech_signal, fund_score, sentiment_score, horizon, weights, regime,
            global_ctx=global_ctx, analyst_score=analyst_score, week52_score=week52_score,
            quality=quality, df=df,
        )

        current_price = float(df["Close"].iloc[-1])
        atr = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])

        target_price = self._estimate_target(
            current_price, signal, confidence, horizon, df, info
        )
        trade_levels = self._trade_levels(current_price, signal, target_price, atr, horizon)

        result = {
            "symbol": symbol,
            "market": market,
            "horizon": horizon,
            "signal": signal,
            "confidence": confidence,
            "score_band": score_band,
            "current_price": round(current_price, 2),
            "target_price": target_price,
            "trade_levels": trade_levels,
            "reasoning": reasoning,
            "technical": tech_signal,
            "fundamental_score": fund_score,
            "sentiment_score": sentiment_score,
            "market_regime": regime,
            "global_context": {
                "score": global_ctx.get("score"),
                "levels": global_ctx.get("levels", {}),
                "changes": global_ctx.get("changes", {}),
            } if global_ctx else None,
            "quality_factors": {
                "score": quality.get("score"),
                "sector": quality.get("sector"),
                "piotroski": quality.get("piotroski"),
                "breakdown": {k: v.get("score") for k, v in quality.get("breakdown", {}).items()},
            } if quality else None,
            "weights_used": {k: v for k, v in weights.items() if k in ("tech", "fund", "sentiment", "vol_regime")},
        }
        _pred_cache[cache_key] = (time.time(), result)
        return result

    def _trade_levels(self, price: float, signal: str, target: float, atr: float, horizon: str) -> dict:
        """
        Trade level logic:
        - Stop loss: ATR-based for short term; proportional for medium/long
        - Take profit: AI target, but extended to guarantee minimum 1.5:1 R:R
        - Entry zone: narrow band around current price based on signal direction
        """
        MIN_RR = 1.5  # never show a trade with R:R worse than 1.5

        # Step 1 — determine stop loss distance (wider for longer horizons)
        profit_distance = abs(target - price)
        if horizon == "short":
            sl_distance = atr * 1.5                              # tight: ~1-2 weeks noise
            trailing_stop_pct = None                             # no trailing for short term
        elif horizon == "medium":
            sl_distance = max(profit_distance * 0.5, atr * 3.0) # wider: 3-month swings
            trailing_stop_pct = 12.0                             # trail 12% below peak
        else:
            sl_distance = max(profit_distance * 0.4, atr * 5.0) # widest: long-term trend
            trailing_stop_pct = 20.0                             # trail 20% below peak
        # Cap: stop loss can never be more than 25% away (prevents negatives on big targets)
        sl_distance = min(sl_distance, price * 0.25)

        # Step 2 — ensure take profit gives at least MIN_RR
        min_tp_distance = sl_distance * MIN_RR

        if signal == "BUY":
            entry_low   = round(price - atr * 0.3, 2)
            entry_high  = round(price + atr * 0.1, 2)
            stop_loss   = round(price - sl_distance, 2)
            take_profit = round(target, 2)
            risk        = round(price - stop_loss, 2)
            reward      = round(take_profit - price, 2)

        elif signal == "SELL":
            entry_low   = round(price - atr * 0.1, 2)
            entry_high  = round(price + atr * 0.3, 2)
            stop_loss   = round(price + sl_distance, 2)
            take_profit = round(target, 2)
            risk        = round(stop_loss - price, 2)
            reward      = round(price - take_profit, 2)

        else:  # HOLD — neutral range around current price
            entry_low   = round(price - atr * 0.5, 2)
            entry_high  = round(price + atr * 0.5, 2)
            stop_loss   = round(price - sl_distance, 2)
            take_profit = round(target, 2)
            risk        = round(price - stop_loss, 2)
            reward      = round(abs(take_profit - price), 2)

        rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        return {
            "signal": signal,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop_pct": trailing_stop_pct,
            "risk_per_share": round(risk, 2),
            "reward_per_share": round(reward, 2),
            "risk_reward_ratio": rr_ratio,
        }

    def _deep_fundamental_score(self, ticker, horizon: str) -> dict:
        """
        Analyses actual financial statements — Income Statement, Balance Sheet, Cash Flow.
        Only called for medium and long term (skipped for short term to keep latency low).
        Returns score 0-100 + list of key findings.
        """
        score = 50
        reasons = []

        try:
            fin = ticker.financials          # P&L — columns = annual periods
            bs  = ticker.balance_sheet       # Balance Sheet
            cf  = ticker.cashflow            # Cash Flow
        except Exception:
            return {"score": 50, "reasons": [], "available": False}

        if fin is None or fin.empty:
            return {"score": 50, "reasons": [], "available": False}

        try:
            # ── Revenue trend (P&L) ──────────────────────────────────────────
            rev_row = None
            for label in ["Total Revenue", "Revenue"]:
                if label in fin.index:
                    rev_row = fin.loc[label].dropna()
                    break
            if rev_row is not None and len(rev_row) >= 2:
                rev_vals = rev_row.sort_index().values  # oldest → newest
                rev_growth_1y = (rev_vals[-1] - rev_vals[-2]) / abs(rev_vals[-2]) if rev_vals[-2] != 0 else 0
                if rev_growth_1y > 0.20:
                    score += 12
                    reasons.append(f"Revenue grew {rev_growth_1y*100:.1f}% YoY — strong top-line growth")
                elif rev_growth_1y > 0.08:
                    score += 6
                    reasons.append(f"Revenue grew {rev_growth_1y*100:.1f}% YoY — steady growth")
                elif rev_growth_1y < -0.05:
                    score -= 10
                    reasons.append(f"Revenue declined {rev_growth_1y*100:.1f}% YoY — top-line pressure")

                # Revenue acceleration: latest growth > prior growth
                if len(rev_vals) >= 3:
                    rev_growth_2y = (rev_vals[-2] - rev_vals[-3]) / abs(rev_vals[-3]) if rev_vals[-3] != 0 else 0
                    if rev_growth_1y > rev_growth_2y + 0.05:
                        score += 6
                        reasons.append("Revenue growth accelerating — momentum building")
                    elif rev_growth_1y < rev_growth_2y - 0.05:
                        score -= 6
                        reasons.append("Revenue growth decelerating — losing momentum")

            # ── Operating Income / EBITDA margin (P&L) ──────────────────────
            op_row = None
            for label in ["Operating Income", "EBIT"]:
                if label in fin.index:
                    op_row = fin.loc[label].dropna()
                    break
            if op_row is not None and rev_row is not None and len(op_row) >= 1:
                op_vals = op_row.sort_index().values
                rev_latest = rev_vals[-1] if rev_row is not None else None
                if rev_latest and rev_latest > 0:
                    op_margin = op_vals[-1] / rev_latest
                    if op_margin > 0.25:
                        score += 10
                        reasons.append(f"Operating margin {op_margin*100:.1f}% — highly profitable")
                    elif op_margin > 0.10:
                        score += 4
                        reasons.append(f"Operating margin {op_margin*100:.1f}% — healthy")
                    elif op_margin < 0:
                        score -= 10
                        reasons.append(f"Operating loss — margin {op_margin*100:.1f}%")

            # ── Free Cash Flow (Cash Flow statement) ─────────────────────────
            if cf is not None and not cf.empty:
                ocf_row = None
                capex_row = None
                for label in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
                    if label in cf.index:
                        ocf_row = cf.loc[label].dropna()
                        break
                for label in ["Capital Expenditure", "Capital Expenditures"]:
                    if label in cf.index:
                        capex_row = cf.loc[label].dropna()
                        break

                if ocf_row is not None and len(ocf_row) >= 1:
                    ocf_vals = ocf_row.sort_index().values
                    capex_vals = capex_row.sort_index().values if capex_row is not None else None

                    fcf_latest = ocf_vals[-1]
                    if capex_vals is not None and len(capex_vals) >= 1:
                        fcf_latest = ocf_vals[-1] - abs(capex_vals[-1])

                    if fcf_latest > 0:
                        score += 10
                        reasons.append(f"Positive free cash flow — company generates real cash")
                        # FCF growing?
                        if len(ocf_vals) >= 2:
                            fcf_prev = ocf_vals[-2] - (abs(capex_vals[-2]) if capex_vals is not None and len(capex_vals) >= 2 else 0)
                            if fcf_latest > fcf_prev * 1.10:
                                score += 6
                                reasons.append("Free cash flow growing — strengthening cash generation")
                    else:
                        score -= 8
                        reasons.append("Negative free cash flow — burning more cash than it generates")

            # ── Balance Sheet: Liquidity & Debt ─────────────────────────────
            if bs is not None and not bs.empty:
                curr_assets = curr_liab = total_debt = cash = None
                for label in ["Current Assets", "Total Current Assets"]:
                    if label in bs.index:
                        curr_assets = bs.loc[label].dropna().sort_index().values[-1]
                        break
                for label in ["Current Liabilities", "Total Current Liabilities"]:
                    if label in bs.index:
                        curr_liab = bs.loc[label].dropna().sort_index().values[-1]
                        break
                for label in ["Total Debt", "Long Term Debt"]:
                    if label in bs.index:
                        total_debt = bs.loc[label].dropna().sort_index().values[-1]
                        break
                for label in ["Cash And Cash Equivalents", "Cash"]:
                    if label in bs.index:
                        cash = bs.loc[label].dropna().sort_index().values[-1]
                        break

                if curr_assets and curr_liab and curr_liab > 0:
                    current_ratio = curr_assets / curr_liab
                    if current_ratio > 2.0:
                        score += 8
                        reasons.append(f"Current ratio {current_ratio:.1f}x — strong liquidity")
                    elif current_ratio > 1.2:
                        score += 3
                        reasons.append(f"Current ratio {current_ratio:.1f}x — adequate liquidity")
                    elif current_ratio < 1.0:
                        score -= 10
                        reasons.append(f"Current ratio {current_ratio:.1f}x — liquidity risk")

                # Net debt check
                if total_debt and cash and rev_row is not None:
                    net_debt = total_debt - cash
                    rev_latest = rev_vals[-1] if rev_row is not None and len(rev_vals) >= 1 else None
                    if rev_latest and rev_latest > 0 and net_debt > rev_latest * 3:
                        score -= 8
                        reasons.append("Net debt exceeds 3x revenue — highly leveraged")
                    elif net_debt < 0:
                        score += 6
                        reasons.append("Net cash position — more cash than debt")

        except Exception:
            pass  # partial data is fine — use whatever we extracted

        return {"score": max(0, min(100, score)), "reasons": reasons, "available": True}

    def _fundamental_score(self, info: dict, horizon: str, market: str = "US") -> dict:
        score = 50
        reasons = []

        pe = info.get("trailingPE")
        if pe:
            # Indian markets structurally trade at higher multiples (Nifty avg ~22-24x)
            pe_cheap   = 18 if market == "IN" else 15
            pe_fair    = 30 if market == "IN" else 25
            pe_stretch = 55 if market == "IN" else 50
            if pe < pe_cheap:
                score += 12
                reasons.append(f"Low P/E ({pe:.1f}) — attractively valued")
            elif pe < pe_fair:
                score += 5
                reasons.append(f"Reasonable P/E ({pe:.1f})")
            elif pe > pe_stretch:
                score -= 12
                reasons.append(f"High P/E ({pe:.1f}) — stretched valuation")

        roe = info.get("returnOnEquity")
        if roe:
            if roe > 0.20:
                score += 12
                reasons.append(f"Strong ROE ({roe*100:.1f}%)")
            elif roe > 0.10:
                score += 5
                reasons.append(f"Decent ROE ({roe*100:.1f}%)")
            elif roe < 0:
                score -= 10
                reasons.append("Negative ROE — unprofitable")

        rev_growth = info.get("revenueGrowth")
        if rev_growth:
            if rev_growth > 0.20:
                score += 12
                reasons.append(f"Strong revenue growth ({rev_growth*100:.1f}% YoY)")
            elif rev_growth > 0.05:
                score += 5
                reasons.append(f"Moderate revenue growth ({rev_growth*100:.1f}% YoY)")
            elif rev_growth < -0.05:
                score -= 10
                reasons.append(f"Revenue declining ({rev_growth*100:.1f}% YoY)")

        de = info.get("debtToEquity")
        if de:
            if de > 300:
                score -= 12
                reasons.append(f"Very high debt-to-equity ({de:.0f}%)")
            elif de > 150:
                score -= 5
                reasons.append(f"Elevated debt-to-equity ({de:.0f}%)")
            elif de < 50:
                score += 5
                reasons.append("Low debt — strong balance sheet")

        profit_margin = info.get("profitMargins")
        if profit_margin:
            if profit_margin > 0.20:
                score += 8
                reasons.append(f"High profit margins ({profit_margin*100:.1f}%)")
            elif profit_margin < 0:
                score -= 8
                reasons.append("Negative profit margins")

        # Earnings per share growth
        eps_growth = info.get("earningsGrowth")
        if eps_growth:
            if eps_growth > 0.20:
                score += 8
                reasons.append(f"Strong EPS growth ({eps_growth*100:.1f}%)")
            elif eps_growth < -0.10:
                score -= 8
                reasons.append(f"EPS declining ({eps_growth*100:.1f}%)")

        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _analyst_score(self, info: dict, market: str = "US") -> dict:
        """Score based on analyst consensus and price target."""
        score = 50
        reasons = []
        try:
            recommendation = info.get("recommendationKey", "")
            num_analysts = info.get("numberOfAnalystOpinions", 0)
            target_mean = info.get("targetMeanPrice")
            target_high = info.get("targetHighPrice")
            target_low  = info.get("targetLowPrice")
            current     = info.get("currentPrice") or info.get("regularMarketPrice")

            rec_map = {
                "strong_buy": (20, "BULLISH", "Strong Buy"),
                "buy":        (12, "BULLISH", "Buy"),
                "hold":       (0,  "NEUTRAL", "Hold"),
                "underperform": (-10, "BEARISH", "Underperform"),
                "sell":       (-18, "BEARISH", "Sell"),
                "strong_sell": (-20, "BEARISH", "Strong Sell"),
            }
            if recommendation in rec_map and num_analysts >= 3:
                adj, sig, label = rec_map[recommendation]
                score += adj
                reasons.append(f"{label} consensus from {num_analysts} analysts")

            if target_mean and current and current > 0:
                upside = (target_mean - current) / current * 100
                cur_sym = "₹" if market == "IN" else "$"
                if upside > 20:
                    score += 8
                    reasons.append(f"Analyst mean target {cur_sym}{target_mean:,.0f} implies {upside:.1f}% upside")
                elif upside > 8:
                    score += 4
                    reasons.append(f"Analyst mean target implies {upside:.1f}% upside")
                elif upside < -10:
                    score -= 8
                    reasons.append(f"Analyst mean target implies {upside:.1f}% downside")

        except Exception:
            pass
        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _week52_score(self, df: pd.DataFrame, info: dict) -> dict:
        """Score based on 52-week price position — breakouts and deep discounts."""
        score = 50
        reasons = []
        try:
            high52 = info.get("fiftyTwoWeekHigh") or float(df["High"].rolling(252).max().iloc[-1])
            low52  = info.get("fiftyTwoWeekLow")  or float(df["Low"].rolling(252).min().iloc[-1])
            current = float(df["Close"].iloc[-1])

            if high52 and low52 and high52 > low52:
                pct_range = (current - low52) / (high52 - low52) * 100

                if pct_range >= 90:
                    score += 12
                    reasons.append(f"Trading near 52-week high ({pct_range:.0f}% of range) — strong momentum, breakout territory")
                elif pct_range >= 70:
                    score += 6
                    reasons.append(f"In upper half of 52-week range ({pct_range:.0f}%) — momentum intact")
                elif pct_range <= 10:
                    # Deep in 52W range — could be value or falling knife
                    score -= 6
                    reasons.append(f"Near 52-week low ({pct_range:.0f}% of range) — watch for support; potential value or continued weakness")
                elif pct_range <= 30:
                    score -= 2
                    reasons.append(f"In lower third of 52-week range ({pct_range:.0f}%) — below trend")

                pct_from_high = (current - high52) / high52 * 100
                if pct_from_high > -5:
                    reasons.append(f"Within {abs(pct_from_high):.1f}% of 52-week high — potential breakout")

        except Exception:
            pass
        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _aggregate_sentiment(self, articles: list) -> dict:
        if not articles:
            return {"score": 50, "label": "NEUTRAL", "bullish": 0, "bearish": 0}
        bullish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BULLISH")
        bearish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BEARISH")
        total = len(articles)
        score = int(50 + (bullish - bearish) / total * 50)
        label = "BULLISH" if score > 60 else "BEARISH" if score < 40 else "NEUTRAL"
        return {"score": score, "label": label, "bullish": bullish, "bearish": bearish}

    def _quality_gate(self, info: dict, df: pd.DataFrame, horizon: str = "medium") -> tuple[bool, list[str]]:
        """
        Hard quality gate — run BEFORE scoring.
        Stocks failing this gate are rejected immediately and never scored.
        Checks for fundamentally broken financials only.
        """
        rejections: list[str] = []

        try:
            # Loss-making with negative operating cash flows
            roe = info.get("returnOnEquity")
            profit_margin = info.get("profitMargins")
            op_cf = info.get("operatingCashflows")

            if (roe is not None and roe < -0.10
                    and profit_margin is not None and profit_margin < -0.05):
                rejections.append(
                    f"Severely loss-making: ROE {roe*100:.1f}%, margins {profit_margin*100:.1f}%"
                )

            # OCF gate only applies to medium/long — growth stocks may have negative OCF short-term
            if horizon != "short" and op_cf is not None and op_cf < 0:
                rejections.append("Negative operating cash flows — core business not generating cash")

            # Extreme leverage — exclude NBFC/banks by checking sector
            sector = (info.get("sector") or "").lower()
            is_financial = any(k in sector for k in ("financial", "bank", "insurance"))
            de = info.get("debtToEquity")
            if not is_financial and de and de > 500:
                rejections.append(
                    f"Extreme leverage (D/E {de:.0f}%) — balance sheet risk too high to score"
                )

        except Exception:
            pass

        return (len(rejections) == 0), rejections

    def _composite_signal(self, tech, fund, sentiment, horizon, weights, regime,
                          global_ctx: dict | None = None,
                          analyst_score: dict | None = None,
                          week52_score: dict | None = None,
                          quality: dict | None = None,
                          df: pd.DataFrame | None = None):
        tech_score = tech.get("score", 50)

        # ── Step 1: Raw composite from core signals ───────────────────────────
        raw_score = (
            tech_score * weights["tech"]
            + fund["score"] * weights["fund"]
            + sentiment["score"] * weights["sentiment"]
        )

        # Local market regime adjustment (±8)
        raw_score += regime["score_adj"]

        # Global macro adjustment — weighted by horizon (short term most sensitive)
        global_adj_weight = {"short": 0.15, "medium": 0.10, "long": 0.05}.get(horizon, 0.10)
        if global_ctx:
            global_base_score = global_ctx.get("score", 50)
            stock_adj = global_ctx.get("stock_score_adj", 0)
            global_contribution = (global_base_score - 50) * global_adj_weight + stock_adj
            raw_score += global_contribution

        # Analyst consensus nudge (±6 max, soft signal)
        if analyst_score:
            raw_score += (analyst_score.get("score", 50) - 50) * 0.08

        # 52-week position nudge (±4 max)
        if week52_score:
            raw_score += (week52_score.get("score", 50) - 50) * 0.06

        # Quality factors — professional-grade signal (±10 max)
        if quality and quality.get("score") is not None:
            raw_score += (quality["score"] - 50) * 0.12

        raw_score = max(0, min(100, raw_score))

        # ── Step 2: Risk penalty — separate deduction step ────────────────────
        # This ensures high-risk stocks can't "score their way" to a BUY signal;
        # risk is always subtracted last so it can override a strong raw signal.
        risk_penalty, penalty_reasons = _compute_risk_penalty(
            info=fund.get("_info", {}),   # passed through if available
            df=df if df is not None else pd.DataFrame(),
            quality=quality,
        )
        # Also probe quality breakdown for fast D/E and FCF checks using fund data
        # (info dict may not be fully accessible here — done best-effort)

        composite = max(0, raw_score - risk_penalty)

        if composite >= 70:
            signal = "BUY"
        elif composite >= 55:
            signal = "HOLD"
        else:
            signal = "SELL"

        if signal == "BUY":
            confidence = min(100, int((composite - 70) * 5))   # 70=0%, 90=100%
        elif signal == "SELL":
            confidence = min(100, int((55 - composite) * 5))   # 55=0%, 33=110%→100%
        else:
            confidence = min(40, int(abs(composite - 62) * 4)) # HOLD: max 40% by design
        score_band = _score_label(int(composite))

        # Build reasoning — most impactful signals first
        reasoning = []

        # Score band (top-line context for the investor)
        reasoning.append({
            "indicator": "AI Score",
            "signal": "INFO",
            "reason": f"Composite score: {composite:.0f}/100 — {score_band}"
                      + (f" (raw {raw_score:.0f} − risk penalty {risk_penalty})" if risk_penalty > 0 else ""),
        })

        # Risk penalty reasons (if any)
        for r in penalty_reasons:
            reasoning.append({"indicator": "Risk Flag", "signal": "BEARISH", "reason": r})

        # Technical breakdown
        reasoning.extend(tech["breakdown"][:3])
        if tech.get("candlestick", {}).get("patterns"):
            reasoning.append({
                "indicator": "Candlestick",
                "signal": tech["candlestick"]["signal"],
                "reason": ", ".join(tech["candlestick"]["patterns"]),
            })

        # 52-week context
        if week52_score:
            for r in week52_score.get("reasons", [])[:1]:
                reasoning.append({"indicator": "Price Level", "signal": "INFO", "reason": r})

        # Fundamentals
        for r in fund["reasons"][:3]:
            reasoning.append({"indicator": "Fundamental", "signal": "INFO", "reason": r})

        # Analyst consensus
        if analyst_score:
            for r in analyst_score.get("reasons", [])[:2]:
                reasoning.append({"indicator": "Analyst", "signal": "INFO", "reason": r})

        # Local market regime
        reasoning.append({"indicator": "Market Regime", "signal": regime["trend"], "reason": regime["reason"]})

        # Global macro — general signals
        if global_ctx:
            for r in global_ctx.get("reasons", [])[:4]:
                reasoning.append(r)
            # Stock-specific macro impact
            for r in global_ctx.get("stock_reasons", [])[:3]:
                reasoning.append(r)

        # Quality factors — top reasons from each dimension
        if quality:
            for r in quality.get("reasons", [])[:6]:
                reasoning.append(r)

        # News sentiment
        if sentiment["label"] != "NEUTRAL":
            reasoning.append({
                "indicator": "Sentiment",
                "signal": sentiment["label"],
                "reason": f"{sentiment['bullish']} bullish vs {sentiment['bearish']} bearish in recent news"
            })

        return signal, confidence, reasoning, score_band

    def _estimate_target(self, price, signal, confidence, horizon, df, info):
        conf_factor = max(0.5, confidence / 100)

        if horizon == "short":
            atr = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
            move = atr * 2.5 * conf_factor
            if signal == "BUY":
                return round(price + move, 2)
            elif signal == "SELL":
                return round(price - move, 2)
            return round(price * (1 + 0.02 * conf_factor), 2)

        elif horizon == "medium":
            analyst_target = info.get("targetMeanPrice")
            if analyst_target and analyst_target > 0:
                blend = (analyst_target * 0.7 + price * 0.3)
                if signal == "BUY":
                    return round(max(blend, price * 1.05), 2)
                elif signal == "SELL":
                    return round(min(blend, price * 0.95), 2)
                return round(blend, 2)

            monthly_ret = df["Close"].pct_change(21).dropna()
            avg_monthly = monthly_ret.mean()
            projected = price * (1 + avg_monthly * 3 * conf_factor)
            if signal == "BUY":
                return round(max(projected, price * 1.05), 2)
            elif signal == "SELL":
                return round(min(projected, price * 0.92), 2)
            return round(projected, 2)

        else:  # long
            pe = info.get("trailingPE") or info.get("forwardPE")
            eps_growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.08
            analyst_target = info.get("targetMeanPrice")

            if analyst_target and analyst_target > 0:
                long_target = analyst_target * ((1 + max(eps_growth, 0.05)) ** 2)
            elif pe and pe > 0:
                eps_est = price / pe
                eps_future = eps_est * ((1 + max(eps_growth, 0.05)) ** 3)
                long_target = eps_future * pe
            else:
                long_target = price * ((1 + max(eps_growth, 0.05)) ** 3)

            if signal == "BUY":
                return round(max(long_target, price * 1.15), 2)
            elif signal == "SELL":
                return round(min(long_target, price * 0.80), 2)
            return round(long_target, 2)
