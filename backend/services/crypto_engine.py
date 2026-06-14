"""
Crypto prediction engine — uses technical indicators only (no fundamentals).
Crypto has no P/E, ROE etc., so we lean heavily on price action + sentiment.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary
from services.news_sentiment import NewsSentimentService

_news_svc = NewsSentimentService()

# Crypto-specific RSS feeds (Google News)
CRYPTO_RSS = {
    "BTC":  "https://news.google.com/rss/search?q=Bitcoin+BTC+price&hl=en-US&gl=US&ceid=US:en",
    "ETH":  "https://news.google.com/rss/search?q=Ethereum+ETH+price&hl=en-US&gl=US&ceid=US:en",
    "DEFAULT": "https://news.google.com/rss/search?q=cryptocurrency+crypto+market&hl=en-US&gl=US&ceid=US:en",
}

HORIZON_PERIODS = {"short": "3mo", "medium": "1y", "long": "3y"}

# Fear & Greed proxy: BTC 30d vol vs 90d vol
def _fear_greed(df: pd.DataFrame) -> dict:
    returns = df["Close"].pct_change().dropna()
    vol_30d = returns.iloc[-30:].std() * np.sqrt(365) if len(returns) >= 30 else None
    vol_90d = returns.iloc[-90:].std() * np.sqrt(365) if len(returns) >= 90 else None
    if vol_30d and vol_90d and vol_90d > 0:
        ratio = vol_30d / vol_90d
        if ratio < 0.8:
            label = "GREED"
            score = 70
        elif ratio > 1.2:
            label = "FEAR"
            score = 30
        else:
            label = "NEUTRAL"
            score = 50
    else:
        label = "NEUTRAL"
        score = 50
    return {"label": label, "score": score, "vol_30d": round(vol_30d * 100, 1) if vol_30d else None}


def _on_chain_proxy(df: pd.DataFrame) -> dict:
    """
    Proxy for on-chain signals using price + volume patterns:
    - Rising price + rising volume = accumulation (bullish)
    - Rising price + falling volume = distribution (bearish)
    - Falling price + rising volume = capitulation (contrarian bullish)
    """
    if len(df) < 20:
        return {"signal": "NEUTRAL", "score": 50}

    price_change = (df["Close"].iloc[-1] - df["Close"].iloc[-20]) / df["Close"].iloc[-20]
    vol_change = (df["Volume"].iloc[-5:].mean() - df["Volume"].iloc[-20:-5].mean()) / (df["Volume"].iloc[-20:-5].mean() + 1e-9)

    if price_change > 0.05 and vol_change > 0.2:
        return {"signal": "BUY", "score": 75, "reason": "Price up with rising volume — accumulation"}
    elif price_change > 0.05 and vol_change < -0.2:
        return {"signal": "HOLD", "score": 55, "reason": "Price up but volume declining — weak momentum"}
    elif price_change < -0.05 and vol_change > 0.2:
        return {"signal": "BUY", "score": 60, "reason": "High volume selloff — potential capitulation"}
    elif price_change < -0.05 and vol_change < -0.2:
        return {"signal": "SELL", "score": 30, "reason": "Price down with declining volume — distribution"}
    return {"signal": "NEUTRAL", "score": 50, "reason": "No clear volume signal"}


async def predict_crypto(symbol: str, horizon: str) -> dict:
    yf_symbol = f"{symbol}-USD"
    ticker = yf.Ticker(yf_symbol)
    df = ticker.history(period=HORIZON_PERIODS[horizon])

    if df.empty or len(df) < 30:
        return {"error": f"No data found for {symbol}"}

    df = compute_indicators(df)
    tech = get_signal_summary(df)

    fear_greed = _fear_greed(df)
    on_chain = _on_chain_proxy(df)

    # Fetch crypto news sentiment
    try:
        rss_url = CRYPTO_RSS.get(symbol.upper(), CRYPTO_RSS["DEFAULT"])
        news_data = await _news_svc.get_news_with_sentiment(symbol, "US", 10)
        sentiment = _aggregate_sentiment(news_data["articles"])
    except Exception:
        sentiment = {"score": 50, "label": "NEUTRAL", "bullish": 0, "bearish": 0}

    # Weights — crypto is almost all technicals + sentiment, no fundamentals
    weights = {
        "short":  {"tech": 0.50, "sentiment": 0.25, "onchain": 0.15, "fear": 0.10},
        "medium": {"tech": 0.45, "sentiment": 0.20, "onchain": 0.20, "fear": 0.15},
        "long":   {"tech": 0.35, "sentiment": 0.20, "onchain": 0.25, "fear": 0.20},
    }[horizon]

    tech_score = tech.get("score", 50)
    composite = (
        tech_score           * weights["tech"]
        + sentiment["score"] * weights["sentiment"]
        + on_chain["score"]  * weights["onchain"]
        + fear_greed["score"]* weights["fear"]
    )
    composite = max(0, min(100, composite))

    signal = "BUY" if composite >= 55 else "SELL" if composite <= 45 else "HOLD"
    confidence = min(100, int(abs(composite - 50) * 3.0))

    current_price = float(df["Close"].iloc[-1])
    atr = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])
    target = _estimate_target(current_price, signal, confidence, horizon, df)

    # Trade levels — short: ATR-based; medium/long: proportional to target
    profit_distance = abs(target - current_price)
    if horizon == "short":
        sl_distance = atr * 2.0   # wider for crypto volatility
    elif horizon == "medium":
        sl_distance = max(profit_distance * 0.5, atr * 2.0)
    else:
        sl_distance = max(profit_distance * 0.4, atr * 3.0)

    if signal == "BUY":
        entry_low  = round(current_price - atr * 0.3, 2)
        entry_high = round(current_price + atr * 0.1, 2)
        stop_loss  = round(current_price - sl_distance, 2)
        risk = round(current_price - stop_loss, 2)
        reward = round(target - current_price, 2)
    elif signal == "SELL":
        entry_low  = round(current_price - atr * 0.1, 2)
        entry_high = round(current_price + atr * 0.3, 2)
        stop_loss  = round(current_price + sl_distance, 2)
        risk = round(stop_loss - current_price, 2)
        reward = round(current_price - target, 2)
    else:
        entry_low  = round(current_price - atr * 0.5, 2)
        entry_high = round(current_price + atr * 0.5, 2)
        stop_loss  = round(current_price - sl_distance, 2)
        risk = round(current_price - stop_loss, 2)
        reward = round(abs(target - current_price), 2)

    trade_levels = {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": target,
        "risk_per_share": risk,
        "reward_per_share": reward,
        "risk_reward_ratio": round(reward / risk, 2) if risk > 0 else 0,
    }

    # Build reasoning
    reasoning = []
    reasoning.extend(tech["breakdown"][:3])
    reasoning.append({
        "indicator": "Market Sentiment",
        "signal": fear_greed["label"],
        "reason": f"Volatility regime: {fear_greed['label']} (30d vol {fear_greed.get('vol_30d','?')}% ann.)"
    })
    reasoning.append({
        "indicator": "Volume Analysis",
        "signal": on_chain["signal"],
        "reason": on_chain.get("reason", "Volume analysis")
    })
    if sentiment["label"] != "NEUTRAL":
        reasoning.append({
            "indicator": "News Sentiment",
            "signal": sentiment["label"],
            "reason": f"{sentiment['bullish']} bullish vs {sentiment['bearish']} bearish headlines"
        })

    return {
        "symbol": symbol,
        "market": "CRYPTO",
        "horizon": horizon,
        "signal": signal,
        "confidence": confidence,
        "current_price": round(current_price, 2),
        "target_price": target,
        "trade_levels": trade_levels,
        "reasoning": reasoning,
        "technical": tech,
        "fear_greed": fear_greed,
        "on_chain_proxy": on_chain,
        "sentiment_score": sentiment,
        "composite_score": round(composite, 1),
    }


def _aggregate_sentiment(articles: list) -> dict:
    if not articles:
        return {"score": 50, "label": "NEUTRAL", "bullish": 0, "bearish": 0}
    bullish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BULLISH")
    bearish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BEARISH")
    total = len(articles)
    score = int(50 + (bullish - bearish) / total * 50)
    label = "BULLISH" if score > 60 else "BEARISH" if score < 40 else "NEUTRAL"
    return {"score": score, "label": label, "bullish": bullish, "bearish": bearish}


def _estimate_target(price: float, signal: str, confidence: int, horizon: str, df: pd.DataFrame) -> float:
    conf_factor = max(0.5, confidence / 100)
    atr = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])

    if horizon == "short":
        move = atr * 2.0 * conf_factor
        if signal == "BUY":  return round(price + move, 2)
        if signal == "SELL": return round(price - move, 2)
        return round(price, 2)

    elif horizon == "medium":
        # 90-day historical return projection
        if len(df) >= 90:
            ret_90d = (df["Close"].iloc[-1] - df["Close"].iloc[-90]) / df["Close"].iloc[-90]
        else:
            ret_90d = df["Close"].pct_change().mean() * 90
        projected = price * (1 + ret_90d * conf_factor)
        if signal == "BUY":  return round(max(projected, price * 1.08), 2)
        if signal == "SELL": return round(min(projected, price * 0.88), 2)
        return round(projected, 2)

    else:  # long
        # Crypto 4-year cycle approximation
        if len(df) >= 365:
            ret_1y = (df["Close"].iloc[-1] - df["Close"].iloc[-365]) / df["Close"].iloc[-365]
        else:
            ret_1y = 0.50  # conservative default for crypto
        projected = price * ((1 + max(ret_1y, 0.1)) ** 2)
        if signal == "BUY":  return round(max(projected, price * 1.20), 2)
        if signal == "SELL": return round(min(projected, price * 0.60), 2)
        return round(projected, 2)
