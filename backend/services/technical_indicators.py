import pandas as pd
import numpy as np
import ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds RSI, MACD, Bollinger Bands, EMA, ATR, ADX, Stoch RSI, Williams %R, CCI, VWAP, OBV."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Momentum
    df["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    df["stoch_k"] = ta.momentum.StochasticOscillator(high, low, close).stoch()
    df["stoch_rsi"] = ta.momentum.StochRSIIndicator(close).stochrsi_k()
    df["williams_r"] = ta.momentum.WilliamsRIndicator(high, low, close).williams_r()
    df["cci"] = ta.trend.CCIIndicator(high, low, close).cci()

    # Trend
    macd = ta.trend.MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"] = macd.macd_diff()
    df["ema_20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema_50"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df["ema_200"] = ta.trend.EMAIndicator(close, window=200).ema_indicator()

    # ADX — trend strength (values > 25 = strong trend, < 20 = sideways)
    adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
    df["adx"] = adx_ind.adx()
    df["adx_pos"] = adx_ind.adx_pos()
    df["adx_neg"] = adx_ind.adx_neg()

    # Volatility
    bb = ta.volatility.BollingerBands(close)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_pct"] = bb.bollinger_pband()  # 0=at lower band, 1=at upper band
    df["atr"] = ta.volatility.AverageTrueRange(high, low, close).average_true_range()

    # Volume
    df["obv"] = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    df["vol_sma_20"] = volume.rolling(20).mean()

    # VWMA-20 (Volume-Weighted Moving Average, 20 bars) — often labelled VWAP in UI
    df["vwap"] = (close * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, float("nan"))

    return df


def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """Detects key candlestick reversal patterns from the last few candles."""
    patterns = []
    signal = "NEUTRAL"

    if len(df) < 3:
        return {"patterns": patterns, "signal": signal}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    o, h, l, c = last["Open"], last["High"], last["Low"], last["Close"]
    po, ph, pl, pc = prev["Open"], prev["High"], prev["Low"], prev["Close"]
    p2o, p2c = prev2["Open"], prev2["Close"]

    body = abs(c - o)
    full_range = h - l
    upper_wick = h - max(c, o)
    lower_wick = min(c, o) - l

    # Doji — indecision
    if full_range > 0 and body / full_range < 0.1:
        patterns.append("Doji (indecision)")

    # Hammer (bullish reversal) — small body at top, long lower wick
    # Valid on red OR green candles; prior downtrend confirmed by prev close < prev2 close
    if lower_wick > body * 2 and upper_wick < body * 0.5 and pc < p2c:
        patterns.append("Hammer (bullish reversal)")
        signal = "BULLISH"

    # Shooting Star (bearish reversal) — small body at bottom, long upper wick
    if upper_wick > body * 2 and lower_wick < body * 0.5 and c < o:
        patterns.append("Shooting Star (bearish reversal)")
        signal = "BEARISH"

    # Bullish Engulfing — green candle engulfs previous red candle
    if c > o and pc < po and c > po and o < pc:
        patterns.append("Bullish Engulfing")
        signal = "BULLISH"

    # Bearish Engulfing — red candle engulfs previous green candle
    if c < o and pc > po and o > pc and c < po:
        patterns.append("Bearish Engulfing")
        signal = "BEARISH"

    # Morning Star (bullish 3-candle): prev2=big red, prev=small body, last=big green
    prev_body = abs(pc - po)
    prev2_body = abs(p2c - p2o)
    if p2c < p2o and prev_body < prev2_body * 0.5 and c > o and body > prev2_body * 0.6:
        patterns.append("Morning Star (bullish)")
        signal = "BULLISH"

    # Evening Star (bearish 3-candle): prev2=big green, prev=small body, last=big red
    if p2c > p2o and prev_body < prev2_body * 0.5 and c < o and body > prev2_body * 0.6:
        patterns.append("Evening Star (bearish)")
        signal = "BEARISH"

    return {"patterns": patterns, "signal": signal}


def get_volume_signal(df: pd.DataFrame) -> dict:
    """Checks if recent volume confirms price direction."""
    if len(df) < 21:
        return {"confirmed": False, "reason": "Insufficient data", "signal": "NEUTRAL"}

    last = df.iloc[-1]
    avg_vol = last["vol_sma_20"]
    cur_vol = last["Volume"]
    price_up = last["Close"] > last["Open"]

    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
    high_volume = vol_ratio >= 1.2  # 20% above average

    obv_slope = df["obv"].iloc[-5:].diff().mean()

    if price_up and high_volume:
        signal = "BUY"
        confirmed = True
        reason = f"Price up with {vol_ratio:.1f}x avg volume — strong buying pressure"
    elif not price_up and high_volume:
        signal = "SELL"
        confirmed = True
        reason = f"Price down with {vol_ratio:.1f}x avg volume — strong selling pressure"
    elif high_volume is False:
        signal = "HOLD"
        confirmed = False
        reason = f"Volume below average ({vol_ratio:.1f}x) — weak conviction"
    else:
        signal = "NEUTRAL"
        confirmed = False
        reason = "Average volume — no confirmation"

    return {
        "signal": signal,
        "confirmed": confirmed,
        "vol_ratio": round(vol_ratio, 2),
        "obv_trend": "UP" if obv_slope > 0 else "DOWN",
        "reason": reason,
    }


def _safe(val, default=0):
    """Return default if val is NaN or None."""
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        return val
    except Exception:
        return default


def get_signal_summary(df: pd.DataFrame) -> dict:
    """Derives a scored technical signal from the last row of indicators."""
    last = df.iloc[-1]
    signals = []
    score = 50  # start neutral

    # --- RSI ---
    rsi = _safe(last["rsi_14"], 50)
    if rsi < 30:
        signals.append({"indicator": "RSI", "signal": "BUY", "reason": f"Oversold (RSI {rsi:.0f})"})
        score += 15
    elif rsi < 45:
        signals.append({"indicator": "RSI", "signal": "BUY", "reason": f"RSI recovering ({rsi:.0f})"})
        score += 7
    elif rsi > 70:
        signals.append({"indicator": "RSI", "signal": "SELL", "reason": f"Overbought (RSI {rsi:.0f})"})
        score -= 15
    elif rsi > 60:
        signals.append({"indicator": "RSI", "signal": "SELL", "reason": f"RSI elevated ({rsi:.0f})"})
        score -= 7

    # --- MACD ---
    if _safe(last.get("macd_diff"), 0) > 0:
        signals.append({"indicator": "MACD", "signal": "BUY", "reason": "MACD above signal line"})
        score += 12
    else:
        signals.append({"indicator": "MACD", "signal": "SELL", "reason": "MACD below signal line"})
        score -= 12

    # --- EMA Trend ---
    close = last["Close"]
    ema20, ema50, ema200 = last["ema_20"], last["ema_50"], last["ema_200"]

    if close > ema200:
        signals.append({"indicator": "EMA200", "signal": "BUY", "reason": "Price above 200 EMA (bull market)"})
        score += 10
    else:
        signals.append({"indicator": "EMA200", "signal": "SELL", "reason": "Price below 200 EMA (bear market)"})
        score -= 10

    if ema20 > ema50:
        signals.append({"indicator": "EMA Cross", "signal": "BUY", "reason": "EMA20 above EMA50 (golden cross zone)"})
        score += 8
    else:
        signals.append({"indicator": "EMA Cross", "signal": "SELL", "reason": "EMA20 below EMA50 (death cross zone)"})
        score -= 8

    # --- ADX — only count trend signals when trend is strong ---
    adx = _safe(last.get("adx"), 0)
    adx_pos = _safe(last.get("adx_pos"), 0)
    adx_neg = _safe(last.get("adx_neg"), 0)
    if adx > 25:
        if adx_pos > adx_neg:
            signals.append({"indicator": "ADX", "signal": "BUY", "reason": f"Strong uptrend (ADX {adx:.0f})"})
            score += 10
        else:
            signals.append({"indicator": "ADX", "signal": "SELL", "reason": f"Strong downtrend (ADX {adx:.0f})"})
            score -= 10
    else:
        signals.append({"indicator": "ADX", "signal": "HOLD", "reason": f"Weak trend / sideways (ADX {adx:.0f})"})

    # --- Bollinger Bands ---
    bb_pct = _safe(last.get("bb_pct"), 0.5)
    if bb_pct < 0.1:
        signals.append({"indicator": "Bollinger", "signal": "BUY", "reason": "Price near lower band — oversold"})
        score += 8
    elif bb_pct > 0.9:
        signals.append({"indicator": "Bollinger", "signal": "SELL", "reason": "Price near upper band — overbought"})
        score -= 8

    # --- Stochastic RSI ---
    stoch_rsi = _safe(last.get("stoch_rsi"), 0.5)
    if stoch_rsi < 0.2:
        signals.append({"indicator": "StochRSI", "signal": "BUY", "reason": f"StochRSI oversold ({stoch_rsi:.2f})"})
        score += 7
    elif stoch_rsi > 0.8:
        signals.append({"indicator": "StochRSI", "signal": "SELL", "reason": f"StochRSI overbought ({stoch_rsi:.2f})"})
        score -= 7

    # --- Williams %R ---
    wr = _safe(last.get("williams_r"), -50)
    if wr < -80:
        signals.append({"indicator": "Williams %R", "signal": "BUY", "reason": f"Oversold (W%R {wr:.0f})"})
        score += 6
    elif wr > -20:
        signals.append({"indicator": "Williams %R", "signal": "SELL", "reason": f"Overbought (W%R {wr:.0f})"})
        score -= 6

    # --- CCI ---
    cci = _safe(last.get("cci"), 0)
    if cci < -100:
        signals.append({"indicator": "CCI", "signal": "BUY", "reason": f"CCI oversold ({cci:.0f})"})
        score += 6
    elif cci > 100:
        signals.append({"indicator": "CCI", "signal": "SELL", "reason": f"CCI overbought ({cci:.0f})"})
        score -= 6

    # Clamp score
    score = max(0, min(100, score))

    # Candlestick patterns
    candle = detect_candlestick_patterns(df)
    if candle["signal"] == "BULLISH":
        score = min(100, score + 5)
        signals.append({"indicator": "Candle", "signal": "BUY", "reason": ", ".join(candle["patterns"])})
    elif candle["signal"] == "BEARISH":
        score = max(0, score - 5)
        signals.append({"indicator": "Candle", "signal": "SELL", "reason": ", ".join(candle["patterns"])})

    # Volume signal
    vol_sig = get_volume_signal(df)
    if vol_sig["confirmed"]:
        if vol_sig["signal"] == "BUY":
            score = min(100, score + 8)
            signals.append({"indicator": "Volume", "signal": "BUY", "reason": vol_sig["reason"]})
        elif vol_sig["signal"] == "SELL":
            score = max(0, score - 8)
            signals.append({"indicator": "Volume", "signal": "SELL", "reason": vol_sig["reason"]})
    else:
        signals.append({"indicator": "Volume", "signal": "HOLD", "reason": vol_sig["reason"]})

    # Score → signal with wider thresholds to reduce HOLD bias
    # Determined AFTER all score adjustments (candlestick + volume)
    if score >= 58:
        overall = "BUY"
    elif score <= 42:
        overall = "SELL"
    else:
        overall = "HOLD"

    return {
        "overall": overall,
        "score": round(score, 1),
        "breakdown": signals,
        "rsi": round(rsi, 2),
        "macd_diff": round(_safe(last.get("macd_diff"), 0), 4),
        "adx": round(adx, 1),
        "candlestick": candle,
        "volume": vol_sig,
    }
