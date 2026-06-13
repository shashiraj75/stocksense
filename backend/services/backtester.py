import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary

MARKET_SUFFIX = {"US": "", "IN": ".NS"}
REGIME_TICKER = {"US": "^GSPC", "IN": "^NSEI"}

HORIZON_DAYS = {"short": 7, "medium": 63, "long": 252}
HORIZON_LOOKBACK = {"short": "2y", "medium": "5y", "long": "10y"}
HORIZON_STEP = {"short": 5, "medium": 21, "long": 63}


def _signal_from_return(ret: float, horizon: str) -> str:
    thresholds = {"short": 0.02, "medium": 0.05, "long": 0.15}
    t = thresholds[horizon]
    if ret >= t:
        return "BUY"
    elif ret <= -t:
        return "SELL"
    return "HOLD"


def _technical_score_at(df: pd.DataFrame, idx: int) -> float:
    """Returns a 0-100 technical score using the full improved indicator suite."""
    slice_df = df.iloc[: idx + 1].copy()
    if len(slice_df) < 30:
        return 50.0
    try:
        slice_df = compute_indicators(slice_df)
        sig = get_signal_summary(slice_df)
        return sig.get("score", 50.0)
    except Exception:
        return 50.0


def _fundamental_score(info: dict) -> float:
    score = 50.0
    pe = info.get("trailingPE")
    if pe:
        score += 10 if pe < 20 else (-10 if pe > 40 else 0)
    roe = info.get("returnOnEquity")
    if roe:
        score += 10 if roe > 0.15 else 0
    rev_growth = info.get("revenueGrowth")
    if rev_growth:
        score += 10 if rev_growth > 0.10 else (-10 if rev_growth < 0 else 0)
    eps_growth = info.get("earningsGrowth")
    if eps_growth:
        score += 8 if eps_growth > 0.15 else (-8 if eps_growth < -0.10 else 0)
    return max(0, min(100, score))


def _volatility_weights(df: pd.DataFrame, idx: int, horizon: str) -> dict:
    """Dynamic weights based on recent volatility."""
    base = {
        "short":  {"tech": 0.60, "fund": 0.10},
        "medium": {"tech": 0.35, "fund": 0.40},
        "long":   {"tech": 0.15, "fund": 0.70},
    }[horizon]
    try:
        slice_close = df["Close"].iloc[max(0, idx-20):idx+1]
        vol = slice_close.pct_change().std() * np.sqrt(252)
        if vol > 0.35:
            return {"tech": max(0.10, base["tech"] - 0.10), "fund": min(0.80, base["fund"] + 0.10)}
        elif vol < 0.15:
            return {"tech": min(0.75, base["tech"] + 0.08), "fund": max(0.05, base["fund"] - 0.08)}
    except Exception:
        pass
    return base


def _regime_adj(regime_df: pd.DataFrame, idx: int) -> float:
    """Returns score adjustment based on market regime at this point in history."""
    try:
        slice_close = regime_df["Close"].iloc[: idx + 1]
        if len(slice_close) < 50:
            return 0
        ema50 = slice_close.ewm(span=50).mean().iloc[-1]
        current = slice_close.iloc[-1]
        lookback = max(0, len(slice_close) - 63)
        ret_3m = (current - slice_close.iloc[lookback]) / slice_close.iloc[lookback]
        if current > ema50 and ret_3m > 0.03:
            return 8
        elif current < ema50 and ret_3m < -0.03:
            return -8
    except Exception:
        pass
    return 0


def run_backtest(symbol: str, market: str, horizon: str) -> dict:
    suffix = MARKET_SUFFIX.get(market, "")
    ticker = yf.Ticker(symbol + suffix)
    df = ticker.history(period=HORIZON_LOOKBACK[horizon])

    if len(df) < 60:
        return {"error": "Not enough historical data"}

    info = ticker.info
    fwd_days = HORIZON_DAYS[horizon]
    step = HORIZON_STEP[horizon]

    # Fetch regime index for same period
    regime_df = None
    try:
        regime_ticker = yf.Ticker(REGIME_TICKER.get(market, "^GSPC"))
        regime_df = regime_ticker.history(period=HORIZON_LOOKBACK[horizon])
        # Align to same dates as stock df
        regime_df = regime_df.reindex(df.index, method="ffill")
    except Exception:
        regime_df = None

    results = []
    for i in range(50, len(df) - fwd_days, step):
        entry_price = float(df["Close"].iloc[i])
        exit_price = float(df["Close"].iloc[i + fwd_days])
        actual_return = (exit_price - entry_price) / entry_price
        actual_signal = _signal_from_return(actual_return, horizon)

        tech_score = _technical_score_at(df, i)
        fund_score = _fundamental_score(info)
        weights = _volatility_weights(df, i, horizon)

        # Sentiment weight remainder goes to neutral (50)
        sent_weight = 1 - weights["tech"] - weights["fund"]
        composite = (
            tech_score * weights["tech"]
            + fund_score * weights["fund"]
            + 50 * sent_weight
        )

        # Market regime adjustment
        if regime_df is not None:
            composite += _regime_adj(regime_df, i)

        composite = max(0, min(100, composite))

        # Reduced HOLD band (matches live engine)
        predicted = "BUY" if composite >= 55 else "SELL" if composite <= 45 else "HOLD"
        correct = predicted == actual_signal

        results.append({
            "date": str(df.index[i])[:10],
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "actual_return_pct": round(actual_return * 100, 2),
            "predicted_signal": predicted,
            "actual_signal": actual_signal,
            "correct": correct,
        })

    if not results:
        return {"error": "No backtest windows available"}

    total = len(results)
    correct_count = sum(1 for r in results if r["correct"])
    accuracy = round(correct_count / total * 100, 1)

    buy_results = [r for r in results if r["predicted_signal"] == "BUY"]
    sell_results = [r for r in results if r["predicted_signal"] == "SELL"]
    hold_results = [r for r in results if r["predicted_signal"] == "HOLD"]

    avg_return_on_buy = round(np.mean([r["actual_return_pct"] for r in buy_results]), 2) if buy_results else 0
    avg_return_on_sell = round(np.mean([r["actual_return_pct"] for r in sell_results]), 2) if sell_results else 0

    profitable_buys = sum(1 for r in buy_results if r["actual_return_pct"] > 0)
    profitable_sells = sum(1 for r in sell_results if r["actual_return_pct"] < 0)

    return {
        "symbol": symbol,
        "market": market,
        "horizon": horizon,
        "total_tests": total,
        "correct_predictions": correct_count,
        "accuracy_pct": accuracy,
        "buy_signals_tested": len(buy_results),
        "sell_signals_tested": len(sell_results),
        "hold_signals_tested": len(hold_results),
        "avg_return_on_buy_pct": avg_return_on_buy,
        "avg_return_on_sell_pct": avg_return_on_sell,
        "profitable_buy_calls": profitable_buys,
        "profitable_sell_calls": profitable_sells,
        "forward_window_days": fwd_days,
        "results": results[-30:],
    }
