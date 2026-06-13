import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary
from services.prediction_engine import PredictionEngine

MARKET_SUFFIX = {"US": "", "IN": ".NS"}

# How many trading days to look forward per horizon
HORIZON_DAYS = {"short": 7, "medium": 63, "long": 252}

# How many past windows to test
HORIZON_LOOKBACK = {"short": "2y", "medium": "5y", "long": "10y"}

# Step between test windows (in trading days)
HORIZON_STEP = {"short": 5, "medium": 21, "long": 63}


def _signal_from_return(ret: float, horizon: str) -> str:
    """Convert actual return to a signal label for comparison."""
    thresholds = {"short": 0.02, "medium": 0.05, "long": 0.15}
    t = thresholds[horizon]
    if ret >= t:
        return "BUY"
    elif ret <= -t:
        return "SELL"
    return "HOLD"


def _technical_signal_at(df: pd.DataFrame, idx: int) -> str:
    """Run technical signal on a slice of data ending at idx."""
    slice_df = df.iloc[: idx + 1].copy()
    if len(slice_df) < 30:
        return "HOLD"
    try:
        slice_df = compute_indicators(slice_df)
        sig = get_signal_summary(slice_df)
        return sig["overall"]
    except Exception:
        return "HOLD"


def _fundamental_signal(info: dict, horizon: str) -> str:
    """Simple fundamental scoring (same logic as PredictionEngine)."""
    score = 50
    pe = info.get("trailingPE")
    if pe:
        score += 10 if pe < 20 else (-10 if pe > 40 else 0)
    roe = info.get("returnOnEquity")
    if roe:
        score += 10 if roe > 0.15 else 0
    rev_growth = info.get("revenueGrowth")
    if rev_growth:
        score += 10 if rev_growth > 0.10 else (-10 if rev_growth < 0 else 0)

    if score >= 60:
        return "BUY"
    elif score <= 40:
        return "SELL"
    return "HOLD"


def run_backtest(symbol: str, market: str, horizon: str) -> dict:
    suffix = MARKET_SUFFIX.get(market, "")
    ticker = yf.Ticker(symbol + suffix)
    df = ticker.history(period=HORIZON_LOOKBACK[horizon])

    if len(df) < 60:
        return {"error": "Not enough historical data"}

    info = ticker.info
    fwd_days = HORIZON_DAYS[horizon]
    step = HORIZON_STEP[horizon]

    weights = {
        "short":  {"tech": 0.60, "fund": 0.10},
        "medium": {"tech": 0.35, "fund": 0.40},
        "long":   {"tech": 0.15, "fund": 0.70},
    }[horizon]

    results = []
    # Walk forward through history
    for i in range(50, len(df) - fwd_days, step):
        entry_price = df["Close"].iloc[i]
        exit_price = df["Close"].iloc[i + fwd_days]
        actual_return = (exit_price - entry_price) / entry_price
        actual_signal = _signal_from_return(actual_return, horizon)

        tech_sig = _technical_signal_at(df, i)
        fund_sig = _fundamental_signal(info, horizon)

        # Composite
        tech_score = 75 if tech_sig == "BUY" else 25 if tech_sig == "SELL" else 50
        fund_score_val = 65 if fund_sig == "BUY" else 35 if fund_sig == "SELL" else 50
        composite = tech_score * weights["tech"] + fund_score_val * weights["fund"] + 50 * (1 - weights["tech"] - weights["fund"])

        predicted = "BUY" if composite >= 60 else "SELL" if composite <= 40 else "HOLD"
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
    correct = sum(1 for r in results if r["correct"])
    accuracy = round(correct / total * 100, 1)

    buy_results = [r for r in results if r["predicted_signal"] == "BUY"]
    sell_results = [r for r in results if r["predicted_signal"] == "SELL"]
    hold_results = [r for r in results if r["predicted_signal"] == "HOLD"]

    avg_return_on_buy = round(np.mean([r["actual_return_pct"] for r in buy_results]), 2) if buy_results else 0
    avg_return_on_sell = round(np.mean([r["actual_return_pct"] for r in sell_results]), 2) if sell_results else 0

    # Winning trades (BUY correct = positive return, SELL correct = negative return)
    profitable_buys = sum(1 for r in buy_results if r["actual_return_pct"] > 0)
    profitable_sells = sum(1 for r in sell_results if r["actual_return_pct"] < 0)

    return {
        "symbol": symbol,
        "market": market,
        "horizon": horizon,
        "total_tests": total,
        "correct_predictions": correct,
        "accuracy_pct": accuracy,
        "buy_signals_tested": len(buy_results),
        "sell_signals_tested": len(sell_results),
        "hold_signals_tested": len(hold_results),
        "avg_return_on_buy_pct": avg_return_on_buy,
        "avg_return_on_sell_pct": avg_return_on_sell,
        "profitable_buy_calls": profitable_buys,
        "profitable_sell_calls": profitable_sells,
        "forward_window_days": fwd_days,
        "results": results[-30:],  # last 30 for chart display
    }
