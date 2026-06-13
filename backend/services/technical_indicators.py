import pandas as pd
import ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds RSI, MACD, Bollinger Bands, EMA, ATR to an OHLCV dataframe."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Momentum
    df["rsi_14"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    df["stoch_k"] = ta.momentum.StochasticOscillator(high, low, close).stoch()

    # Trend
    macd = ta.trend.MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"] = macd.macd_diff()
    df["ema_20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema_50"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df["ema_200"] = ta.trend.EMAIndicator(close, window=200).ema_indicator()

    # Volatility
    bb = ta.volatility.BollingerBands(close)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["atr"] = ta.volatility.AverageTrueRange(high, low, close).average_true_range()

    # Volume
    df["obv"] = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()

    return df


def get_signal_summary(df: pd.DataFrame) -> dict:
    """Derives a simple technical signal from the last row of indicators."""
    last = df.iloc[-1]
    signals = []

    if last["rsi_14"] < 30:
        signals.append({"indicator": "RSI", "signal": "BUY", "reason": "Oversold (<30)"})
    elif last["rsi_14"] > 70:
        signals.append({"indicator": "RSI", "signal": "SELL", "reason": "Overbought (>70)"})

    if last["macd_diff"] > 0:
        signals.append({"indicator": "MACD", "signal": "BUY", "reason": "MACD crossed above signal"})
    else:
        signals.append({"indicator": "MACD", "signal": "SELL", "reason": "MACD below signal"})

    if last["Close"] > last["ema_200"]:
        signals.append({"indicator": "EMA200", "signal": "BUY", "reason": "Price above 200 EMA"})
    else:
        signals.append({"indicator": "EMA200", "signal": "SELL", "reason": "Price below 200 EMA"})

    buy_count = sum(1 for s in signals if s["signal"] == "BUY")
    sell_count = sum(1 for s in signals if s["signal"] == "SELL")

    if buy_count > sell_count:
        overall = "BUY"
    elif sell_count > buy_count:
        overall = "SELL"
    else:
        overall = "HOLD"

    return {
        "overall": overall,
        "breakdown": signals,
        "rsi": round(last["rsi_14"], 2),
        "macd_diff": round(last["macd_diff"], 4),
    }
