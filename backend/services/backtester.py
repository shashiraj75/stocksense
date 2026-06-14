import yfinance as yf
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators

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


def _tech_score_from_row(row: pd.Series) -> float:
    """
    Derive a 0-100 technical score directly from a precomputed indicator row.
    Avoids recomputing indicators for every window — runs in O(1) per window.
    """
    score = 50.0

    # RSI
    rsi = row.get("rsi_14", np.nan)
    if pd.notna(rsi):
        if rsi < 30:   score += 15
        elif rsi < 45: score += 7
        elif rsi > 70: score -= 15
        elif rsi > 60: score -= 7

    # MACD
    macd_diff = row.get("macd_diff", np.nan)
    if pd.notna(macd_diff):
        score += 12 if macd_diff > 0 else -12

    # EMA200
    close = row.get("Close", np.nan)
    ema200 = row.get("ema_200", np.nan)
    ema20  = row.get("ema_20", np.nan)
    ema50  = row.get("ema_50", np.nan)
    if pd.notna(close) and pd.notna(ema200):
        score += 10 if close > ema200 else -10
    if pd.notna(ema20) and pd.notna(ema50):
        score += 8 if ema20 > ema50 else -8

    # ADX
    adx     = row.get("adx", np.nan)
    adx_pos = row.get("adx_pos", np.nan)
    adx_neg = row.get("adx_neg", np.nan)
    if pd.notna(adx) and adx > 25 and pd.notna(adx_pos) and pd.notna(adx_neg):
        score += 10 if adx_pos > adx_neg else -10

    # Bollinger %B
    bb_pct = row.get("bb_pct", np.nan)
    if pd.notna(bb_pct):
        if bb_pct < 0.1:  score += 8
        elif bb_pct > 0.9: score -= 8

    # StochRSI
    stoch_rsi = row.get("stoch_rsi", np.nan)
    if pd.notna(stoch_rsi):
        if stoch_rsi < 0.2:  score += 7
        elif stoch_rsi > 0.8: score -= 7

    # Williams %R
    wr = row.get("williams_r", np.nan)
    if pd.notna(wr):
        if wr < -80:  score += 6
        elif wr > -20: score -= 6

    # CCI
    cci = row.get("cci", np.nan)
    if pd.notna(cci):
        if cci < -100: score += 6
        elif cci > 100: score -= 6

    return max(0.0, min(100.0, score))


def _fundamental_score(info: dict) -> float:
    score = 50.0
    pe = info.get("trailingPE")
    if pe: score += 10 if pe < 20 else (-10 if pe > 40 else 0)
    roe = info.get("returnOnEquity")
    if roe: score += 10 if roe > 0.15 else 0
    rev_growth = info.get("revenueGrowth")
    if rev_growth: score += 10 if rev_growth > 0.10 else (-10 if rev_growth < 0 else 0)
    eps_growth = info.get("earningsGrowth")
    if eps_growth: score += 8 if eps_growth > 0.15 else (-8 if eps_growth < -0.10 else 0)
    return max(0.0, min(100.0, score))


def _volatility_weights(vol_20d: float, horizon: str) -> dict:
    base = {
        "short":  {"tech": 0.60, "fund": 0.10},
        "medium": {"tech": 0.35, "fund": 0.40},
        "long":   {"tech": 0.15, "fund": 0.70},
    }[horizon]
    if vol_20d > 0.35:
        return {"tech": max(0.10, base["tech"] - 0.10), "fund": min(0.80, base["fund"] + 0.10)}
    elif vol_20d < 0.15:
        return {"tech": min(0.75, base["tech"] + 0.08), "fund": max(0.05, base["fund"] - 0.08)}
    return base


def _build_regime_series(regime_df: pd.DataFrame, stock_index) -> pd.Series:
    """Precompute regime score adjustment for each date."""
    if regime_df is None or regime_df.empty:
        return pd.Series(0.0, index=stock_index)

    regime_close = regime_df["Close"].reindex(stock_index, method="ffill").bfill()
    ema50 = regime_close.ewm(span=50).mean()

    adjs = []
    for i in range(len(stock_index)):
        try:
            cur = regime_close.iloc[i]
            e50 = ema50.iloc[i]
            lookback = max(0, i - 63)
            ret_3m = (cur - regime_close.iloc[lookback]) / regime_close.iloc[lookback] if regime_close.iloc[lookback] != 0 else 0
            if cur > e50 and ret_3m > 0.03:
                adjs.append(8.0)
            elif cur < e50 and ret_3m < -0.03:
                adjs.append(-8.0)
            else:
                adjs.append(0.0)
        except Exception:
            adjs.append(0.0)

    return pd.Series(adjs, index=stock_index)


def _build_vol_series(close: pd.Series) -> pd.Series:
    """Precompute annualised 20-day rolling volatility for each date."""
    return close.pct_change().rolling(20).std() * np.sqrt(252)


def run_backtest(symbol: str, market: str, horizon: str) -> dict:
    suffix = MARKET_SUFFIX.get(market, "")
    ticker = yf.Ticker(symbol + suffix)
    df = ticker.history(period=HORIZON_LOOKBACK[horizon])

    if len(df) < 60:
        return {"error": "Not enough historical data"}

    # ── Precompute ALL indicators once ──────────────────────────────────────
    df = compute_indicators(df)

    info = ticker.info
    fwd_days = HORIZON_DAYS[horizon]
    step = HORIZON_STEP[horizon]
    fund_score = _fundamental_score(info)

    # Precompute volatility & regime series
    vol_series = _build_vol_series(df["Close"])

    regime_df = None
    try:
        regime_df = yf.Ticker(REGIME_TICKER.get(market, "^GSPC")).history(period=HORIZON_LOOKBACK[horizon])
    except Exception:
        pass
    regime_series = _build_regime_series(regime_df, df.index)

    # ── Walk-forward loop — O(n) now instead of O(n²) ───────────────────────
    results = []
    for i in range(50, len(df) - fwd_days, step):
        entry_price = float(df["Close"].iloc[i])
        exit_price  = float(df["Close"].iloc[i + fwd_days])
        actual_return = (exit_price - entry_price) / entry_price
        actual_signal = _signal_from_return(actual_return, horizon)

        tech_score = _tech_score_from_row(df.iloc[i])
        vol_20d    = float(vol_series.iloc[i]) if pd.notna(vol_series.iloc[i]) else 0.20
        weights    = _volatility_weights(vol_20d, horizon)

        sent_weight = 1 - weights["tech"] - weights["fund"]
        composite = (
            tech_score   * weights["tech"]
            + fund_score * weights["fund"]
            + 50         * sent_weight
        )
        composite += float(regime_series.iloc[i])
        composite = max(0.0, min(100.0, composite))

        predicted = "BUY" if composite >= 55 else "SELL" if composite <= 45 else "HOLD"
        correct   = predicted == actual_signal

        results.append({
            "date": str(df.index[i])[:10],
            "entry_price": round(entry_price, 2),
            "exit_price":  round(exit_price, 2),
            "actual_return_pct": round(actual_return * 100, 2),
            "predicted_signal":  predicted,
            "actual_signal":     actual_signal,
            "correct": correct,
        })

    if not results:
        return {"error": "No backtest windows available"}

    total         = len(results)
    correct_count = sum(1 for r in results if r["correct"])
    accuracy      = round(correct_count / total * 100, 1)

    buy_results  = [r for r in results if r["predicted_signal"] == "BUY"]
    sell_results = [r for r in results if r["predicted_signal"] == "SELL"]
    hold_results = [r for r in results if r["predicted_signal"] == "HOLD"]

    avg_return_on_buy  = round(np.mean([r["actual_return_pct"] for r in buy_results]),  2) if buy_results  else 0
    avg_return_on_sell = round(np.mean([r["actual_return_pct"] for r in sell_results]), 2) if sell_results else 0
    profitable_buys    = sum(1 for r in buy_results  if r["actual_return_pct"] > 0)
    profitable_sells   = sum(1 for r in sell_results if r["actual_return_pct"] < 0)

    return {
        "symbol": symbol, "market": market, "horizon": horizon,
        "total_tests": total, "correct_predictions": correct_count,
        "accuracy_pct": accuracy,
        "buy_signals_tested":  len(buy_results),
        "sell_signals_tested": len(sell_results),
        "hold_signals_tested": len(hold_results),
        "avg_return_on_buy_pct":  avg_return_on_buy,
        "avg_return_on_sell_pct": avg_return_on_sell,
        "profitable_buy_calls":  profitable_buys,
        "profitable_sell_calls": profitable_sells,
        "forward_window_days": fwd_days,
        "results": results[-30:],
    }
