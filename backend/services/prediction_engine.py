import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary
from services.news_sentiment import NewsSentimentService

MARKET_SUFFIX = {"US": "", "IN": ".NS"}
_news_svc = NewsSentimentService()


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

        info = ticker.info
        fund_score = self._fundamental_score(info, horizon)

        news_data = await _news_svc.get_news_with_sentiment(symbol, market, 10)
        sentiment_score = self._aggregate_sentiment(news_data["articles"])

        signal, confidence, reasoning = self._composite_signal(
            tech_signal, fund_score, sentiment_score, horizon
        )

        current_price = df["Close"].iloc[-1]
        target_price = self._estimate_target(
            current_price, signal, confidence, horizon, df, info
        )

        return {
            "symbol": symbol,
            "market": market,
            "horizon": horizon,
            "signal": signal,
            "confidence": confidence,
            "current_price": round(current_price, 2),
            "target_price": target_price,
            "reasoning": reasoning,
            "technical": tech_signal,
            "fundamental_score": fund_score,
            "sentiment_score": sentiment_score,
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
                reasons.append(f"Negative ROE — unprofitable")

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
                reasons.append(f"Negative profit margins")

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

    def _composite_signal(self, tech, fund, sentiment, horizon):
        # Different weightings per horizon — short term is technical-heavy,
        # long term is fundamentals-heavy
        weights = {
            "short":  {"tech": 0.60, "fund": 0.10, "sentiment": 0.30},
            "medium": {"tech": 0.35, "fund": 0.40, "sentiment": 0.25},
            "long":   {"tech": 0.15, "fund": 0.70, "sentiment": 0.15},
        }[horizon]

        tech_score = 75 if tech["overall"] == "BUY" else 25 if tech["overall"] == "SELL" else 50
        composite = (
            tech_score * weights["tech"]
            + fund["score"] * weights["fund"]
            + sentiment["score"] * weights["sentiment"]
        )

        # Tighter thresholds so we produce clearer signals
        if composite >= 60:
            signal = "BUY"
        elif composite <= 40:
            signal = "SELL"
        else:
            signal = "HOLD"

        # Confidence = how far from neutral (50), scaled to 0-100
        confidence = min(100, int(abs(composite - 50) * 2.5))

        reasoning = []
        reasoning.extend(tech["breakdown"][:2])
        for r in fund["reasons"][:2]:
            reasoning.append({"indicator": "Fundamental", "signal": "INFO", "reason": r})
        if sentiment["label"] != "NEUTRAL":
            reasoning.append({
                "indicator": "Sentiment",
                "signal": sentiment["label"],
                "reason": f"{sentiment['bullish']} bullish vs {sentiment['bearish']} bearish in recent news"
            })

        return signal, confidence, reasoning

    def _estimate_target(self, price, signal, confidence, horizon, df, info):
        """
        Target price uses different methods per horizon:
        - Short:  ATR-based (volatility × multiplier)
        - Medium: Analyst targets or earnings growth projection
        - Long:   Fundamental fair value estimate (P/E based)
        """
        conf_factor = max(0.5, confidence / 100)

        if horizon == "short":
            # ATR over last 14 days
            atr = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
            move = atr * 2.5 * conf_factor
            if signal == "BUY":
                return round(price + move, 2)
            elif signal == "SELL":
                return round(price - move, 2)
            else:
                # HOLD: small range around current price
                return round(price * (1 + 0.02 * conf_factor), 2)

        elif horizon == "medium":
            # Use analyst target price if available, else project 3-month earnings growth
            analyst_target = info.get("targetMeanPrice")
            if analyst_target and analyst_target > 0:
                # Blend analyst target with signal direction
                blend = (analyst_target * 0.7 + price * 0.3)
                if signal == "BUY":
                    return round(max(blend, price * 1.05), 2)
                elif signal == "SELL":
                    return round(min(blend, price * 0.95), 2)
                return round(blend, 2)

            # Fallback: use 3-month historical volatility projection
            monthly_ret = df["Close"].pct_change(21).dropna()
            avg_monthly = monthly_ret.mean()
            projected = price * (1 + avg_monthly * 3 * conf_factor)
            if signal == "BUY":
                return round(max(projected, price * 1.05), 2)
            elif signal == "SELL":
                return round(min(projected, price * 0.92), 2)
            return round(projected, 2)

        else:  # long
            # P/E based fair value: use sector median P/E if own P/E unavailable
            pe = info.get("trailingPE") or info.get("forwardPE")
            eps_growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.08
            analyst_target = info.get("targetMeanPrice")

            if analyst_target and analyst_target > 0:
                # Extrapolate analyst target over 2 years using growth rate
                long_target = analyst_target * ((1 + max(eps_growth, 0.05)) ** 2)
            elif pe and pe > 0:
                # PEG-style: assume EPS grows, apply same P/E
                eps_est = price / pe
                eps_future = eps_est * ((1 + max(eps_growth, 0.05)) ** 3)
                long_target = eps_future * pe
            else:
                # Fallback: compound at estimated growth rate over 3 years
                long_target = price * ((1 + max(eps_growth, 0.05)) ** 3)

            if signal == "BUY":
                return round(max(long_target, price * 1.15), 2)
            elif signal == "SELL":
                return round(min(long_target, price * 0.80), 2)
            return round(long_target, 2)
