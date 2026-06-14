import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary
from services.news_sentiment import NewsSentimentService

MARKET_SUFFIX = {"US": "", "IN": ".NS"}
_news_svc = NewsSentimentService()

# Market regime index tickers
REGIME_TICKER = {"US": "^GSPC", "IN": "^NSEI"}


def _market_regime(market: str) -> dict:
    """
    Checks whether the broad market is in an uptrend or downtrend.
    Uses S&P 500 for US, NIFTY 50 for India.
    Returns: {"trend": "BULL"/"BEAR"/"SIDEWAYS", "score_adj": int}
    score_adj is applied to the composite score (+8 in bull, -8 in bear).
    """
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

        return {"trend": trend, "score_adj": adj, "reason": reason}
    except Exception:
        return {"trend": "SIDEWAYS", "score_adj": 0, "reason": "Could not fetch market data"}


def _dynamic_weights(df: pd.DataFrame, horizon: str) -> dict:
    """
    Adjusts horizon weights based on current volatility.
    High volatility → trust fundamentals more (less noise from technicals).
    Low volatility → technicals are more reliable.
    """
    base_weights = {
        "short":  {"tech": 0.60, "fund": 0.10, "sentiment": 0.30},
        "medium": {"tech": 0.35, "fund": 0.40, "sentiment": 0.25},
        "long":   {"tech": 0.15, "fund": 0.70, "sentiment": 0.15},
    }[horizon]

    try:
        # 20-day historical volatility (annualised)
        returns = df["Close"].pct_change().dropna()
        vol_20d = returns.iloc[-20:].std() * np.sqrt(252)

        if vol_20d > 0.35:  # high volatility (>35% annualised)
            # Shift weight from tech → fundamentals
            shift = 0.10
            return {
                "tech": max(0.10, base_weights["tech"] - shift),
                "fund": min(0.80, base_weights["fund"] + shift),
                "sentiment": base_weights["sentiment"],
                "vol_regime": "HIGH",
                "vol_20d": round(vol_20d * 100, 1),
            }
        elif vol_20d < 0.15:  # low volatility (<15% annualised)
            # Technicals are more reliable in calm markets
            shift = 0.08
            return {
                "tech": min(0.75, base_weights["tech"] + shift),
                "fund": max(0.05, base_weights["fund"] - shift),
                "sentiment": base_weights["sentiment"],
                "vol_regime": "LOW",
                "vol_20d": round(vol_20d * 100, 1),
            }
    except Exception:
        pass

    return {**base_weights, "vol_regime": "NORMAL", "vol_20d": 0}


class PredictionEngine:
    async def predict(self, symbol: str, market: str, horizon: str) -> dict:
        suffix = MARKET_SUFFIX.get(market, "")
        ticker = yf.Ticker(symbol + suffix)

        period_map = {"short": "6mo", "medium": "2y", "long": "5y"}
        df = ticker.history(period=period_map[horizon])

        if df.empty:
            return {"error": "No data found for symbol"}

        df = compute_indicators(df)
        tech_signal = get_signal_summary(df)

        try:
            info = ticker.info
        except Exception:
            info = {}
        fund_score = self._fundamental_score(info, horizon)

        news_data = await _news_svc.get_news_with_sentiment(symbol, market, 10)
        sentiment_score = self._aggregate_sentiment(news_data["articles"])

        # Market regime check
        regime = _market_regime(market)

        # Dynamic weights based on volatility
        weights = _dynamic_weights(df, horizon)

        signal, confidence, reasoning = self._composite_signal(
            tech_signal, fund_score, sentiment_score, horizon, weights, regime
        )

        current_price = float(df["Close"].iloc[-1])
        atr = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])

        target_price = self._estimate_target(
            current_price, signal, confidence, horizon, df, info
        )
        trade_levels = self._trade_levels(current_price, signal, target_price, atr, horizon)

        return {
            "symbol": symbol,
            "market": market,
            "horizon": horizon,
            "signal": signal,
            "confidence": confidence,
            "current_price": round(current_price, 2),
            "target_price": target_price,
            "trade_levels": trade_levels,
            "reasoning": reasoning,
            "technical": tech_signal,
            "fundamental_score": fund_score,
            "sentiment_score": sentiment_score,
            "market_regime": regime,
            "weights_used": {k: v for k, v in weights.items() if k in ("tech", "fund", "sentiment", "vol_regime")},
        }

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
            # extend TP if AI target is too close
            take_profit = round(max(target, price + min_tp_distance), 2)
            risk        = round(price - stop_loss, 2)
            reward      = round(take_profit - price, 2)

        elif signal == "SELL":
            entry_low   = round(price - atr * 0.1, 2)
            entry_high  = round(price + atr * 0.3, 2)
            stop_loss   = round(price + sl_distance, 2)
            # extend TP downward if AI target is too close
            take_profit = round(min(target, price - min_tp_distance), 2)
            risk        = round(stop_loss - price, 2)
            reward      = round(price - take_profit, 2)

        else:  # HOLD — show range, no directional trade
            entry_low   = round(price - atr * 0.5, 2)
            entry_high  = round(price + atr * 0.5, 2)
            stop_loss   = round(price - sl_distance, 2)
            take_profit = round(price + min_tp_distance, 2)
            risk        = round(price - stop_loss, 2)
            reward      = round(take_profit - price, 2)

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

    def _fundamental_score(self, info: dict, horizon: str) -> dict:
        score = 50
        reasons = []

        pe = info.get("trailingPE")
        if pe:
            if pe < 15:
                score += 12
                reasons.append(f"Low P/E ({pe:.1f}) — attractively valued")
            elif pe < 25:
                score += 5
                reasons.append(f"Reasonable P/E ({pe:.1f})")
            elif pe > 50:
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

    def _aggregate_sentiment(self, articles: list) -> dict:
        if not articles:
            return {"score": 50, "label": "NEUTRAL", "bullish": 0, "bearish": 0}
        bullish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BULLISH")
        bearish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BEARISH")
        total = len(articles)
        score = int(50 + (bullish - bearish) / total * 50)
        label = "BULLISH" if score > 60 else "BEARISH" if score < 40 else "NEUTRAL"
        return {"score": score, "label": label, "bullish": bullish, "bearish": bearish}

    def _composite_signal(self, tech, fund, sentiment, horizon, weights, regime):
        tech_score = tech.get("score", 50)  # now a full 0-100 score, not just 75/50/25
        composite = (
            tech_score * weights["tech"]
            + fund["score"] * weights["fund"]
            + sentiment["score"] * weights["sentiment"]
        )

        # Apply market regime bias
        composite += regime["score_adj"]
        composite = max(0, min(100, composite))

        # Reduced HOLD band — score 55+ is BUY, 45- is SELL
        if composite >= 55:
            signal = "BUY"
        elif composite <= 45:
            signal = "SELL"
        else:
            signal = "HOLD"

        # Confidence = how far from neutral
        confidence = min(100, int(abs(composite - 50) * 3.0))

        reasoning = []
        reasoning.extend(tech["breakdown"][:3])
        for r in fund["reasons"][:2]:
            reasoning.append({"indicator": "Fundamental", "signal": "INFO", "reason": r})
        reasoning.append({"indicator": "Market Regime", "signal": regime["trend"], "reason": regime["reason"]})
        if sentiment["label"] != "NEUTRAL":
            reasoning.append({
                "indicator": "Sentiment",
                "signal": sentiment["label"],
                "reason": f"{sentiment['bullish']} bullish vs {sentiment['bearish']} bearish in recent news"
            })
        if tech.get("candlestick", {}).get("patterns"):
            reasoning.append({
                "indicator": "Candlestick",
                "signal": tech["candlestick"]["signal"],
                "reason": ", ".join(tech["candlestick"]["patterns"]),
            })

        return signal, confidence, reasoning

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
