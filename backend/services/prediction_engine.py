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

        period_map = {"short": "3mo", "medium": "1y", "long": "5y"}
        df = ticker.history(period=period_map[horizon])

        if df.empty:
            return {"error": "No data found for symbol"}

        df = compute_indicators(df)
        tech_signal = get_signal_summary(df)

        # Fundamental scoring (simple rules-based for MVP)
        info = ticker.info
        fund_score = self._fundamental_score(info, horizon)

        # Sentiment
        news_data = await _news_svc.get_news_with_sentiment(symbol, market, 10)
        sentiment_score = self._aggregate_sentiment(news_data["articles"])

        # Weighted composite signal
        signal, confidence, reasoning = self._composite_signal(
            tech_signal, fund_score, sentiment_score, horizon
        )

        current_price = df["Close"].iloc[-1]
        target_price = self._estimate_target(current_price, signal, horizon, df)

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
        score = 50  # neutral baseline
        reasons = []

        pe = info.get("trailingPE")
        if pe and pe < 20:
            score += 10
            reasons.append("Low P/E ratio — potentially undervalued")
        elif pe and pe > 40:
            score -= 10
            reasons.append("High P/E ratio — may be overvalued")

        roe = info.get("returnOnEquity")
        if roe and roe > 0.15:
            score += 10
            reasons.append("Strong ROE > 15%")

        rev_growth = info.get("revenueGrowth")
        if rev_growth and rev_growth > 0.1:
            score += 10
            reasons.append("Revenue growing >10% YoY")
        elif rev_growth and rev_growth < 0:
            score -= 10
            reasons.append("Revenue declining YoY")

        de = info.get("debtToEquity")
        if de and de > 200:
            score -= 10
            reasons.append("High debt-to-equity ratio")

        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _aggregate_sentiment(self, articles: list) -> dict:
        if not articles:
            return {"score": 50, "label": "NEUTRAL"}
        bullish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BULLISH")
        bearish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BEARISH")
        total = len(articles)
        score = int(50 + (bullish - bearish) / total * 50)
        label = "BULLISH" if score > 60 else "BEARISH" if score < 40 else "NEUTRAL"
        return {"score": score, "label": label, "bullish": bullish, "bearish": bearish}

    def _composite_signal(
        self, tech: dict, fund: dict, sentiment: dict, horizon: str
    ) -> tuple[str, int, list]:
        # Weights vary by horizon
        weights = {
            "short":  {"tech": 0.60, "fund": 0.10, "sentiment": 0.30},
            "medium": {"tech": 0.40, "fund": 0.35, "sentiment": 0.25},
            "long":   {"tech": 0.20, "fund": 0.65, "sentiment": 0.15},
        }[horizon]

        tech_score = 75 if tech["overall"] == "BUY" else 25 if tech["overall"] == "SELL" else 50
        composite = (
            tech_score * weights["tech"]
            + fund["score"] * weights["fund"]
            + sentiment["score"] * weights["sentiment"]
        )

        if composite >= 62:
            signal = "BUY"
        elif composite <= 38:
            signal = "SELL"
        else:
            signal = "HOLD"

        confidence = int(abs(composite - 50) * 2)  # 0-100

        reasoning = []
        reasoning.extend(tech["breakdown"][:2])
        reasoning.extend([{"indicator": "Fundamental", "signal": "INFO", "reason": r} for r in fund["reasons"][:2]])
        if sentiment["label"] != "NEUTRAL":
            reasoning.append({"indicator": "Sentiment", "signal": sentiment["label"], "reason": f"{sentiment['bullish']} bullish vs {sentiment['bearish']} bearish news articles"})

        return signal, confidence, reasoning

    def _estimate_target(
        self, price: float, signal: str, horizon: str, df: pd.DataFrame
    ) -> float:
        # Simple ATR-based target for MVP
        atr = df["High"].rolling(14).max().iloc[-1] - df["Low"].rolling(14).min().iloc[-1]
        multipliers = {"short": 1.5, "medium": 4, "long": 10}
        m = multipliers[horizon]
        if signal == "BUY":
            return round(price + atr * m, 2)
        elif signal == "SELL":
            return round(price - atr * m, 2)
        return round(price, 2)
