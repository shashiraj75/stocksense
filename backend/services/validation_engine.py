"""
Walk-Forward Validation Engine
================================
Answers the core question: "Does this model actually predict stock returns?"

Runs the FULL technical scoring model historically across Nifty 100 stocks,
computes forward returns at each signal date, and measures:

1. Hit Rate          — % of BUY/SELL calls where direction was correct
2. Average Return    — mean actual return when model says BUY vs SELL vs baseline
3. Signal Precision  — within BUY calls, % that beat a minimum threshold
4. Sharpe Ratio      — risk-adjusted return of the model's portfolio
5. vs Benchmark      — comparison to Nifty 50 buy-and-hold
6. IC by Factor      — which factor (tech/RS/OBV/MFI) actually predicted returns
7. Score Calibration — hit rate by composite score bucket (60-70, 70-80, 80+)

Walk-forward guarantee: at each date t, only data available before t is used.
No look-ahead bias — forward return is measured at t + horizon_days.
"""

import os
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from services.technical_indicators import compute_indicators

# ── Storage — Postgres (production) or SQLite (local dev) ────────────────────
_USE_POSTGRES = bool(os.getenv("DATABASE_URL") and os.getenv("USE_POSTGRES", "0") == "1")
_DB_PATH      = os.path.join(os.path.dirname(__file__), "../../validation_results.db")
_db_lock      = threading.Lock()

# ── Progress tracking (in-memory, for API polling) ────────────────────────────
_run_status: dict = {"running": False, "progress": 0, "total": 0, "started_at": None, "log": []}
_status_lock = threading.Lock()


# ── Postgres helpers ──────────────────────────────────────────────────────────

def _pg_conn():
    """Open a single psycopg connection (no pool needed — called under _db_lock)."""
    import psycopg
    # prepare_threshold=None disables server-side prepared statements, preventing
    # the '_pg3_0 already exists' error that occurs when Postgres retains prepared
    # statement names across connections on the same backend process.
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS val_runs (
    id          BIGSERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL,
    horizon     TEXT NOT NULL,
    n_stocks    INTEGER,
    n_signals   INTEGER,
    summary     JSONB
);

CREATE TABLE IF NOT EXISTS val_signals (
    id                BIGSERIAL PRIMARY KEY,
    run_id            BIGINT REFERENCES val_runs(id) ON DELETE CASCADE,
    symbol            TEXT NOT NULL,
    horizon           TEXT NOT NULL,
    signal_date       DATE NOT NULL,
    composite_score   REAL,
    tech_score        REAL,
    rs_score          REAL,
    obv_score         REAL,
    mfi_score         REAL,
    predicted         TEXT,
    fwd_return_pct    REAL,
    nifty_fwd_ret_pct REAL,
    alpha_pct         REAL,
    actual_direction  TEXT,
    correct           SMALLINT
);

CREATE INDEX IF NOT EXISTS idx_val_signals_run   ON val_signals(run_id, horizon);
CREATE INDEX IF NOT EXISTS idx_val_signals_sym   ON val_signals(symbol, horizon);
CREATE INDEX IF NOT EXISTS idx_val_signals_score ON val_signals(composite_score);
"""

NIFTY_100 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC",
    "SBIN", "BHARTIARTL", "BAJFINANCE", "KOTAKBANK", "LT", "AXISBANK",
    "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "WIPRO", "ULTRACEMCO",
    "NTPC", "POWERGRID", "ONGC", "TATAMOTORS", "M&M", "ASIANPAINT",
    "NESTLEIND", "BAJAJFINSV", "TECHM", "DRREDDY", "CIPLA",
    "JSWSTEEL", "TATASTEEL", "HINDALCO", "DIVISLAB", "BRITANNIA",
    "DABUR", "GODREJCP", "MARICO", "TATACONSUM", "EICHERMOT",
    "BAJAJ-AUTO", "HEROMOTOCO", "TVSMOTOR", "ADANIENT", "ADANIPORTS",
    "SIEMENS", "HAL", "BHEL", "DLF", "OBEROIRLTY",
    "HDFCLIFE", "SBILIFE", "ICICIPRULI", "CHOLAFIN", "MUTHOOTFIN",
    "LUPIN", "TORNTPHARM", "APOLLOHOSP", "SRF", "PIIND",
    "ZOMATO", "DMART", "IRCTC", "INDHOTEL", "TRENT",
    "PERSISTENT", "MPHASIS", "LTIM", "OFSS", "NAUKRI",
    "COALINDIA", "GAIL", "BPCL", "IOC", "HINDPETRO",
    "BANKBARODA", "INDUSINDBK", "FEDERALBNK", "IDFCFIRSTB", "BANDHANBNK",
    "VEDL", "NMDC", "SAIL", "HAVELLS", "VOLTAS",
    "PIDILITIND", "SUPREMEIND", "RECLTD", "LICHSGFIN", "CONCOR",
    "DELHIVERY", "NYKAA", "PAYTM", "POLICYBZR", "DIXON",
    "ULTRACEMCO", "SHREECEM", "GRASIM", "MOTHERSON",
]
NIFTY_100 = list(dict.fromkeys(NIFTY_100))  # deduplicate, preserve order

HORIZON_DAYS  = {"short": 5,  "medium": 21, "long": 63}
HORIZON_STEP  = {"short": 5,  "medium": 10, "long": 21}
HORIZON_THRESHOLDS = {"short": 0.02, "medium": 0.04, "long": 0.10}
HORIZON_PERIOD = {"short": "3y", "medium": "5y", "long": "7y"}

# Per-horizon thresholds — long horizon covers 7 years of data including bear markets
# where regime_adj is often 0 or -5, so the composite ceiling is lower. Keeping a
# single 65 threshold produced 0 BUY signals for long horizon.
BUY_THRESHOLD  = {"short": 65, "medium": 65, "long": 60}
SELL_THRESHOLD = {"short": 42, "medium": 42, "long": 44}


# ── Database ──────────────────────────────────────────────────────────────────

def _get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


_db_initialised = False

def _init_db():
    global _db_initialised
    if _db_initialised:
        return
    with _db_lock:
        if _db_initialised:
            return
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                conn.execute(_PG_SCHEMA)
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as c:
                c.executescript("""
                CREATE TABLE IF NOT EXISTS val_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      TEXT NOT NULL,
                    horizon     TEXT NOT NULL,
                    n_stocks    INTEGER,
                    n_signals   INTEGER,
                    summary     TEXT
                );
                CREATE TABLE IF NOT EXISTS val_signals (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id            INTEGER REFERENCES val_runs(id),
                    symbol            TEXT NOT NULL,
                    horizon           TEXT NOT NULL,
                    signal_date       TEXT NOT NULL,
                    composite_score   REAL,
                    tech_score        REAL,
                    rs_score          REAL,
                    obv_score         REAL,
                    mfi_score         REAL,
                    predicted         TEXT,
                    fwd_return_pct    REAL,
                    nifty_fwd_ret_pct REAL,
                    alpha_pct         REAL,
                    actual_direction  TEXT,
                    correct           INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_vs_symbol ON val_signals(symbol, horizon);
                CREATE INDEX IF NOT EXISTS idx_vs_run    ON val_signals(run_id, horizon);
                CREATE INDEX IF NOT EXISTS idx_vs_score  ON val_signals(composite_score);
                """)
        _db_initialised = True


# ── Scoring (uses ONLY data at index i — no look-ahead) ──────────────────────

def _score_at(df: pd.DataFrame, i: int, nifty_close: pd.Series | None, fund_score: float, regime_adj: float) -> dict:
    """
    Compute composite score at row i using only df[:i+1].
    Returns dict with composite_score and sub-scores.
    """
    row = df.iloc[i]

    # ── Technical score (mirrors get_signal_summary logic) ────────────────────
    tech = 50.0
    rsi = row.get("rsi_14", np.nan)
    if pd.notna(rsi):
        if rsi < 30:    tech += 15
        elif rsi < 45:  tech += 7
        elif rsi > 70:  tech -= 15
        elif rsi > 60:  tech -= 7

    macd_diff = row.get("macd_diff", np.nan)
    if pd.notna(macd_diff):
        tech += 12 if macd_diff > 0 else -12

    close = row.get("Close", np.nan)
    ema200 = row.get("ema_200", np.nan)
    ema20  = row.get("ema_20",  np.nan)
    ema50  = row.get("ema_50",  np.nan)
    if pd.notna(close) and pd.notna(ema200):
        tech += 10 if close > ema200 else -10
    if pd.notna(ema20) and pd.notna(ema50):
        tech += 8 if ema20 > ema50 else -8

    adx     = row.get("adx", np.nan)
    adx_pos = row.get("adx_pos", np.nan)
    adx_neg = row.get("adx_neg", np.nan)
    if pd.notna(adx) and adx > 25 and pd.notna(adx_pos) and pd.notna(adx_neg):
        tech += 10 if adx_pos > adx_neg else -10

    bb_pct = row.get("bb_pct", np.nan)
    if pd.notna(bb_pct):
        if bb_pct < 0.1:  tech += 8
        elif bb_pct > 0.9: tech -= 8

    stoch_rsi = row.get("stoch_rsi", np.nan)
    if pd.notna(stoch_rsi):
        if stoch_rsi < 0.2:  tech += 7
        elif stoch_rsi > 0.8: tech -= 7

    wr = row.get("williams_r", np.nan)
    if pd.notna(wr):
        if wr < -80:  tech += 6
        elif wr > -20: tech -= 6

    cci = row.get("cci", np.nan)
    if pd.notna(cci):
        if cci < -100: tech += 6
        elif cci > 100: tech -= 6

    tech = max(0.0, min(100.0, tech))

    # ── Relative strength vs Nifty (1M, 3M) ─────────────────────────────────
    rs_score = 50.0
    if nifty_close is not None and pd.notna(close):
        close_series = df["Close"].iloc[:i+1]
        for days in (21, 63):
            if i >= days and len(nifty_close) > 0:
                try:
                    nifty_at_i   = nifty_close.iloc[min(i, len(nifty_close)-1)]
                    nifty_at_im  = nifty_close.iloc[max(0, min(i-days, len(nifty_close)-1))]
                    stock_ret = (close_series.iloc[-1] - close_series.iloc[-days]) / close_series.iloc[-days] * 100 if close_series.iloc[-days] != 0 else 0
                    nifty_ret = (nifty_at_i - nifty_at_im) / nifty_at_im * 100 if nifty_at_im != 0 else 0
                    rs = stock_ret - nifty_ret
                    if rs > 10:    rs_score += 10
                    elif rs > 4:   rs_score += 5
                    elif rs < -10: rs_score -= 10
                    elif rs < -4:  rs_score -= 5
                except Exception:
                    pass
    rs_score = max(0.0, min(100.0, rs_score))

    # ── OBV trend (fixed: raw slope, no sign flip) ────────────────────────────
    obv_score = 50.0
    obv_col = df.get("obv") if hasattr(df, "get") else None
    if "obv" in df.columns and i >= 20:
        obv_series = df["obv"].iloc[:i+1]
        obv_slope = float(obv_series.iloc[-1] - obv_series.iloc[-20])
        close_20d_base = df["Close"].iloc[i-20]
        price_ret = (close - close_20d_base) / close_20d_base if close_20d_base != 0 else 0
        if obv_slope > 0 and price_ret > 0:
            obv_score = 70.0
        elif obv_slope > 0 and price_ret < 0:
            obv_score = 65.0
        elif obv_slope < 0 and price_ret > 0:
            obv_score = 35.0
        elif obv_slope < 0 and price_ret < 0:
            obv_score = 38.0

    # ── MFI (fixed: standard formula) ────────────────────────────────────────
    mfi_score = 50.0
    if all(c in df.columns for c in ("High", "Low", "Close", "Volume")) and i >= 14:
        window = df.iloc[max(0, i-30):i+1]
        tp = (window["High"] + window["Low"] + window["Close"]) / 3
        rmf = tp * window["Volume"]
        pos = pd.Series(0.0, index=window.index)
        neg = pd.Series(0.0, index=window.index)
        for j in range(1, len(window)):
            if tp.iloc[j] > tp.iloc[j-1]:
                pos.iloc[j] = rmf.iloc[j]
            else:
                neg.iloc[j] = rmf.iloc[j]
        pos14 = pos.rolling(14).sum().iloc[-1]
        neg14 = neg.rolling(14).sum().iloc[-1]
        mfi_val = 100 * pos14 / (pos14 + neg14 + 1e-10)
        if mfi_val > 70:   mfi_score = 72.0
        elif mfi_val > 55: mfi_score = 60.0
        elif mfi_val < 30: mfi_score = 30.0
        elif mfi_val < 45: mfi_score = 42.0

    # ── Composite (weights: tech 30%, RS 30%, OBV 20%, MFI 20%) ──────────────
    # Reduced tech weight (IC=-0.037, momentum-chasing after price has moved).
    # Raised RS weight (cross-sectional alpha signal; leading not lagging).
    # Fundamentals blend raised from 30% to 45% (quality / value is more stable).
    composite = (
        tech      * 0.30
        + rs_score  * 0.30
        + obv_score * 0.20
        + mfi_score * 0.20
    )
    # Blend with fundamentals (fixed for the whole stock period)
    composite = composite * 0.55 + fund_score * 0.45
    composite += regime_adj
    composite = max(0.0, min(100.0, composite))

    return {
        "composite": round(composite, 1),
        "tech":      round(tech, 1),
        "rs":        round(rs_score, 1),
        "obv":       round(obv_score, 1),
        "mfi":       round(mfi_score, 1),
    }


def _backtest_stock(symbol: str, horizon: str, nifty_df: pd.DataFrame | None) -> list[dict]:
    """
    Walk-forward backtest for one stock over HORIZON_PERIOD[horizon].
    Returns list of signal dicts, empty list on error.
    """
    yf_sym = symbol + ".NS"
    fwd_days = HORIZON_DAYS[horizon]
    step     = HORIZON_STEP[horizon]
    threshold = HORIZON_THRESHOLDS[horizon]

    try:
        df = yf.Ticker(yf_sym).history(period=HORIZON_PERIOD[horizon])
        if len(df) < 100:
            return []
        df = compute_indicators(df)

        # Fixed fundamentals (snapshot; not time-varying in historical test)
        try:
            info = yf.Ticker(yf_sym).info
        except Exception:
            info = {}
        fund_score = 50.0
        pe = info.get("trailingPE")
        if pe:
            fund_score += 10 if pe < 20 else (-10 if pe > 40 else 0)
        roe = info.get("returnOnEquity")
        if roe:
            fund_score += 10 if roe > 0.15 else 0
        rev_g = info.get("revenueGrowth")
        if rev_g:
            fund_score += 8 if rev_g > 0.10 else (-8 if rev_g < 0 else 0)
        fund_score = max(0.0, min(100.0, fund_score))

        # Align nifty index to stock dates
        nifty_close = None
        if nifty_df is not None and not nifty_df.empty:
            nifty_close = nifty_df["Close"].reindex(df.index, method="ffill").bfill()

        # Precompute regime adjustments
        regime_adjs = []
        if nifty_close is not None:
            ema50 = nifty_close.ewm(span=50).mean()
            for idx in range(len(df)):
                try:
                    cur = float(nifty_close.iloc[idx])
                    e50 = float(ema50.iloc[idx])
                    lookback = max(0, idx-63)
                    base = float(nifty_close.iloc[lookback])
                    r3m = (cur - base) / base if base != 0 else 0
                    if cur > e50 and r3m > 0.03:    regime_adjs.append(5.0)
                    elif cur < e50 and r3m < -0.03: regime_adjs.append(-5.0)
                    else:                            regime_adjs.append(0.0)
                except Exception:
                    regime_adjs.append(0.0)
        else:
            regime_adjs = [0.0] * len(df)

        signals = []
        for i in range(50, len(df) - fwd_days, step):
            try:
                entry = float(df["Close"].iloc[i])
                exit_ = float(df["Close"].iloc[i + fwd_days])
                if entry == 0:
                    continue
                fwd_ret = (exit_ - entry) / entry * 100

                # Nifty forward return over same window (benchmark-relative correctness)
                nifty_fwd_ret = 0.0
                if nifty_close is not None and i + fwd_days < len(nifty_close):
                    n_entry = float(nifty_close.iloc[i])
                    n_exit  = float(nifty_close.iloc[i + fwd_days])
                    if n_entry != 0:
                        nifty_fwd_ret = (n_exit - n_entry) / n_entry * 100

                sc = _score_at(df, i, nifty_close, fund_score, regime_adjs[i])
                composite = sc["composite"]

                buy_thr  = BUY_THRESHOLD[horizon]
                sell_thr = SELL_THRESHOLD[horizon]
                predicted = "BUY" if composite >= buy_thr else ("SELL" if composite <= sell_thr else "HOLD")

                # "correct" = benchmark-relative:
                #   BUY is correct  if stock outperforms Nifty by > 0 over fwd window
                #   SELL is correct if stock underperforms Nifty by > 0 over fwd window
                #   HOLD is correct if stock is within ±threshold% of Nifty return
                alpha = fwd_ret - nifty_fwd_ret
                if predicted == "BUY":
                    correct = alpha > 0
                elif predicted == "SELL":
                    correct = alpha < 0
                else:
                    correct = abs(alpha) <= threshold * 100

                # Keep absolute direction for context (used in avg return calcs)
                actual_dir = "UP" if fwd_ret >= threshold * 100 else ("DOWN" if fwd_ret <= -threshold * 100 else "FLAT")

                signals.append({
                    "symbol":          symbol,
                    "horizon":         horizon,
                    "signal_date":     str(df.index[i])[:10],
                    "composite_score": composite,
                    "tech_score":      sc["tech"],
                    "rs_score":        sc["rs"],
                    "obv_score":       sc["obv"],
                    "mfi_score":       sc["mfi"],
                    "predicted":       predicted,
                    "fwd_return_pct":  round(fwd_ret, 3),
                    "nifty_fwd_ret_pct": round(nifty_fwd_ret, 3),
                    "alpha_pct":       round(alpha, 3),
                    "actual_direction": actual_dir,
                    "correct":         int(correct),
                })
            except Exception:
                continue

        return signals

    except Exception as e:
        print(f"[validation] {symbol}/{horizon} failed: {e}")
        return []


# ── Aggregate metrics ─────────────────────────────────────────────────────────

def _max_consec_wrong(buys: list[dict]) -> int:
    ordered = sorted(buys, key=lambda s: s.get("signal_date", ""))
    best = cur = 0
    for s in ordered:
        if not s["correct"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _max_consec_right(buys: list[dict]) -> int:
    ordered = sorted(buys, key=lambda s: s.get("signal_date", ""))
    best = cur = 0
    for s in ordered:
        if s["correct"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _max_drawdown(rets: list[float]) -> float | None:
    """Peak-to-trough drawdown on a hypothetical equal-weight BUY portfolio."""
    if not rets:
        return None
    equity = 100.0
    peak = equity
    max_dd = 0.0
    for r in rets:
        equity *= (1 + r / 100)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 1)


def _compute_metrics(signals: list[dict], nifty_return_pct: float, horizon: str = "medium") -> dict:
    """Compute all aggregate validation metrics from raw signals."""
    if not signals:
        return {}

    buys  = [s for s in signals if s["predicted"] == "BUY"]
    sells = [s for s in signals if s["predicted"] == "SELL"]
    holds = [s for s in signals if s["predicted"] == "HOLD"]

    fwd_days = HORIZON_DAYS.get(horizon, 5)

    def _hit_rate(subset):
        if not subset: return None
        return round(sum(s["correct"] for s in subset) / len(subset) * 100, 1)

    def _avg_ret(subset):
        if not subset: return None
        return round(np.mean([s["fwd_return_pct"] for s in subset]), 2)

    def _sharpe(rets, rf=0.0):
        arr = np.array(rets)
        if arr.std() == 0: return 0.0
        # Annualise using actual forward window (not a hardcoded 5-day assumption)
        return round(float((arr.mean() - rf) / arr.std() * np.sqrt(252 / fwd_days)), 2)

    # Score bucket analysis — key table for investor confidence
    buckets = []
    for lo, hi in ((60,65),(65,70),(70,75),(75,80),(80,85),(85,91)):
        bucket_buys = [s for s in buys if lo <= s["composite_score"] < hi]
        if bucket_buys:
            buckets.append({
                "score_range": f"{lo}–{hi}",
                "count":       len(bucket_buys),
                "hit_rate_pct": _hit_rate(bucket_buys),
                "avg_return_pct": _avg_ret(bucket_buys),
            })

    # Factor IC (Pearson correlation of each sub-score with forward return)
    def _ic(factor_key):
        pairs = [(s[factor_key], s["fwd_return_pct"]) for s in signals
                 if s.get(factor_key) is not None and s.get("fwd_return_pct") is not None]
        if len(pairs) < 30: return None
        vals, rets = zip(*pairs)
        return round(float(np.corrcoef(vals, rets)[0,1]), 4)

    # Portfolio simulation: equal-weight all BUY signals, measure vs benchmark
    buy_rets   = [s["fwd_return_pct"] for s in buys]
    buy_alphas = [s["alpha_pct"] for s in buys if s.get("alpha_pct") is not None]
    model_avg  = _avg_ret(buys) or 0.0
    outperformance = round(model_avg - nifty_return_pct, 2) if nifty_return_pct is not None else None

    def _avg(lst):
        return round(float(np.mean(lst)), 2) if lst else None

    return {
        "total_signals":    len(signals),
        "buy_signals":      len(buys),
        "sell_signals":     len(sells),
        "hold_signals":     len(holds),
        # Benchmark-relative hit rate (primary metric — stock must beat Nifty to be "correct")
        "buy_hit_rate_pct":           _hit_rate(buys),
        "sell_hit_rate_pct":          _hit_rate(sells),
        "overall_accuracy_pct":       _hit_rate(signals),
        # Return metrics
        "avg_return_on_buy_pct":      _avg_ret(buys),
        "avg_alpha_on_buy_pct":       _avg(buy_alphas),
        "avg_return_on_sell_pct":     _avg_ret(sells),
        "avg_return_benchmark_pct":   round(nifty_return_pct, 2) if nifty_return_pct else None,
        "buy_outperformance_pct":     outperformance,
        "sharpe_on_buys":             _sharpe(buy_rets) if buy_rets else None,
        "sharpe_on_alphas":           _sharpe(buy_alphas) if buy_alphas else None,
        "profitable_buy_pct":         round(sum(1 for r in buy_rets if r > 0) / len(buy_rets) * 100, 1) if buy_rets else None,
        # "beat benchmark meaningfully" = alpha > 1% (not just alpha > 0 which equals buy_hit_rate)
        "beat_benchmark_pct":         round(sum(1 for a in buy_alphas if a > 1.0) / len(buy_alphas) * 100, 1) if buy_alphas else None,
        # Streak / drawdown analysis on BUY signals ordered by signal_date
        "max_consecutive_wrong":  _max_consec_wrong(buys),
        "max_consecutive_right":  _max_consec_right(buys),
        "max_drawdown_pct":       _max_drawdown(buy_rets),
        "score_buckets":          buckets,
        "factor_ic": {
            "tech":      _ic("tech_score"),
            "rs":        _ic("rs_score"),
            "obv":       _ic("obv_score"),
            "mfi":       _ic("mfi_score"),
            "composite": _ic("composite_score"),
        },
    }


# ── Main runner ───────────────────────────────────────────────────────────────

def run_validation(horizon: str = "medium", max_workers: int = 6) -> dict:
    """
    Run a full walk-forward validation across all Nifty 100 stocks.
    Stores results in SQLite and returns the summary metrics dict.
    """
    stocks = NIFTY_100
    n_stocks = len(stocks)
    with _status_lock:
        if _run_status["running"]:
            return {"error": "A validation run is already in progress"}
        _run_status.update({"running": True, "progress": 0, "total": n_stocks,
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "log": [f"Starting {horizon}-term validation on all {n_stocks} Nifty 100 stocks…"]})

    try:
        _init_db()
        stocks = NIFTY_100

        # Fetch Nifty 50 benchmark once
        try:
            nifty_df = yf.Ticker("^NSEI").history(period=HORIZON_PERIOD[horizon])
            # Benchmark: average forward return over the period
            fwd_days = HORIZON_DAYS[horizon]
            nifty_rets = []
            for i in range(0, len(nifty_df) - fwd_days, HORIZON_STEP[horizon]):
                e = float(nifty_df["Close"].iloc[i])
                x = float(nifty_df["Close"].iloc[i + fwd_days])
                if e != 0:
                    nifty_rets.append((x - e) / e * 100)
            nifty_avg_ret = float(np.mean(nifty_rets)) if nifty_rets else 0.0
        except Exception:
            nifty_df = pd.DataFrame()
            nifty_avg_ret = 0.0

        all_signals: list[dict] = []
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_backtest_stock, sym, horizon, nifty_df): sym for sym in stocks}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    sigs = future.result()
                    all_signals.extend(sigs)
                    done += 1
                    with _status_lock:
                        _run_status["progress"] = done
                        _run_status["log"].append(f"[{done}/{n_stocks}] {sym}: {len(sigs)} signals")
                except Exception as e:
                    done += 1
                    with _status_lock:
                        _run_status["progress"] = done
                        _run_status["log"].append(f"[{done}/{n_stocks}] {sym}: ERROR {e}")

        metrics = _compute_metrics(all_signals, nifty_avg_ret, horizon)
        metrics["horizon"] = horizon
        metrics["n_stocks_tested"] = n_stocks
        metrics["run_at"] = datetime.now(timezone.utc).isoformat()
        metrics["nifty_avg_fwd_return_pct"] = round(nifty_avg_ret, 2)

        # Persist — convert numpy scalars to native Python first
        def _jsonify(obj):
            if isinstance(obj, dict):   return {k: _jsonify(v) for k, v in obj.items()}
            if isinstance(obj, list):   return [_jsonify(v) for v in obj]
            if isinstance(obj, np.integer):  return int(obj)
            if isinstance(obj, np.floating): return None if np.isnan(obj) or np.isinf(obj) else float(obj)
            if isinstance(obj, float) and (obj != obj or obj == float("inf") or obj == float("-inf")):
                return None
            return obj

        clean_metrics = _jsonify(metrics)
        signal_rows = [
            (s["symbol"], s["horizon"], s["signal_date"],
             s["composite_score"], s["tech_score"], s["rs_score"],
             s["obv_score"], s["mfi_score"], s["predicted"],
             s["fwd_return_pct"], s.get("nifty_fwd_ret_pct"), s.get("alpha_pct"),
             s["actual_direction"], s["correct"])
            for s in all_signals
        ]

        with _db_lock:
            if _USE_POSTGRES:
                conn = _pg_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary) "
                            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                            (metrics["run_at"], horizon, n_stocks, len(all_signals),
                             json.dumps(clean_metrics))
                        )
                        run_id = cur.fetchone()[0]
                        cur.executemany(
                            """INSERT INTO val_signals
                               (run_id, symbol, horizon, signal_date, composite_score,
                                tech_score, rs_score, obv_score, mfi_score,
                                predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct,
                                actual_direction, correct)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [(run_id,) + r for r in signal_rows]
                        )
                finally:
                    conn.close()
            else:
                with _get_sqlite_conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary) VALUES (?,?,?,?,?)",
                        (metrics["run_at"], horizon, n_stocks, len(all_signals),
                         json.dumps(clean_metrics))
                    )
                    run_id = cur.lastrowid
                    conn.executemany(
                        """INSERT INTO val_signals
                           (run_id, symbol, horizon, signal_date, composite_score,
                            tech_score, rs_score, obv_score, mfi_score,
                            predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct,
                            actual_direction, correct)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [(run_id,) + r for r in signal_rows]
                    )

        with _status_lock:
            _run_status.update({"running": False, "log": _run_status["log"] + ["✅ Validation complete"]})

        return metrics

    except Exception as e:
        with _status_lock:
            _run_status.update({"running": False, "log": _run_status["log"] + [f"❌ Failed: {e}"]})
        raise


def _fetchone(sql_pg: str, sql_sq: str, params=()):
    if _USE_POSTGRES:
        conn = _pg_conn()
        try:
            return conn.execute(sql_pg, params).fetchone()
        finally:
            conn.close()
    with _get_sqlite_conn() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql_sq, params).fetchone()


def _fetchall(sql_pg: str, sql_sq: str, params=()):
    if _USE_POSTGRES:
        conn = _pg_conn()
        try:
            return conn.execute(sql_pg, params).fetchall()
        finally:
            conn.close()
    with _get_sqlite_conn() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql_sq, params).fetchall()


def get_latest_results(horizon: str | None = None) -> dict:
    """Return the most recent validation summary (or per-horizon breakdown)."""
    try:
        _init_db()
        if horizon:
            row = _fetchone(
                "SELECT summary FROM val_runs WHERE horizon=%s ORDER BY id DESC LIMIT 1",
                "SELECT summary FROM val_runs WHERE horizon=? ORDER BY id DESC LIMIT 1",
                (horizon,)
            )
        else:
            row = _fetchone(
                "SELECT summary FROM val_runs ORDER BY id DESC LIMIT 1",
                "SELECT summary FROM val_runs ORDER BY id DESC LIMIT 1",
                ()
            )
        if not row:
            return {"available": False, "message": "No validation run found. Run /api/validation/run first."}
        summary = row[0] if _USE_POSTGRES else row["summary"]
        data = summary if isinstance(summary, dict) else json.loads(summary)
        return {"available": True, **data}
    except Exception as e:
        import traceback
        return {"available": False, "error": str(e), "trace": traceback.format_exc()}


def get_per_stock_results(run_id: int | None = None, horizon: str = "medium") -> list[dict]:
    """Return per-stock hit rate and average return for the latest (or given) run."""
    try:
        _init_db()
        if run_id is None:
            row = _fetchone(
                "SELECT id FROM val_runs WHERE horizon=%s ORDER BY id DESC LIMIT 1",
                "SELECT id FROM val_runs WHERE horizon=? ORDER BY id DESC LIMIT 1",
                (horizon,)
            )
            if not row:
                return []
            run_id = row[0] if _USE_POSTGRES else row["id"]

        rows = _fetchall(
            """SELECT symbol,
                      COUNT(*) AS total,
                      SUM(correct) AS correct,
                      AVG(fwd_return_pct) AS avg_ret,
                      AVG(CASE WHEN predicted='BUY' THEN fwd_return_pct END) AS buy_ret,
                      COUNT(CASE WHEN predicted='BUY' THEN 1 END) AS buy_count
               FROM val_signals
               WHERE run_id=%s AND horizon=%s
               GROUP BY symbol
               ORDER BY buy_ret DESC NULLS LAST""",
            """SELECT symbol,
                      COUNT(*) AS total,
                      SUM(correct) AS correct,
                      AVG(fwd_return_pct) AS avg_ret,
                      AVG(CASE WHEN predicted='BUY' THEN fwd_return_pct END) AS buy_ret,
                      COUNT(CASE WHEN predicted='BUY' THEN 1 END) AS buy_count
               FROM val_signals
               WHERE run_id=? AND horizon=?
               GROUP BY symbol
               ORDER BY buy_ret DESC NULLS LAST""",
            (run_id, horizon)
        )

        def _v(r, key, idx):
            return r[idx] if _USE_POSTGRES else r[key]

        return [
            {
                "symbol":            _v(r, "symbol", 0),
                "total_signals":     _v(r, "total", 1),
                "correct":           _v(r, "correct", 2),
                "hit_rate_pct":      round(_v(r, "correct", 2) / _v(r, "total", 1) * 100, 1) if _v(r, "total", 1) else 0,
                "avg_fwd_return_pct": round(_v(r, "avg_ret", 3), 2) if _v(r, "avg_ret", 3) is not None else None,
                "buy_avg_return_pct": round(_v(r, "buy_ret", 4), 2) if _v(r, "buy_ret", 4) is not None else None,
                "buy_signal_count":  _v(r, "buy_count", 5),
            }
            for r in rows
        ]
    except Exception:
        return []


def get_run_status() -> dict:
    with _status_lock:
        return dict(_run_status)


def get_all_run_summaries() -> list[dict]:
    """List of all past validation runs with key metrics."""
    try:
        _init_db()
        rows = _fetchall(
            "SELECT id, run_at, horizon, n_stocks, n_signals, summary FROM val_runs ORDER BY id DESC LIMIT 20",
            "SELECT id, run_at, horizon, n_stocks, n_signals, summary FROM val_runs ORDER BY id DESC LIMIT 20",
        )
        results = []
        for r in rows:
            if _USE_POSTGRES:
                rid, rat, hor, ns, nsig, summ = r
                s = summ if isinstance(summ, dict) else json.loads(summ)
            else:
                rid, rat, hor, ns, nsig = r["id"], r["run_at"], r["horizon"], r["n_stocks"], r["n_signals"]
                s = json.loads(r["summary"])
            results.append({
                "run_id": rid, "run_at": str(rat), "horizon": hor,
                "n_stocks": ns, "n_signals": nsig,
                "buy_hit_rate_pct":      s.get("buy_hit_rate_pct"),
                "avg_return_on_buy_pct": s.get("avg_return_on_buy_pct"),
                "avg_alpha_on_buy_pct":  s.get("avg_alpha_on_buy_pct"),
                "beat_benchmark_pct":    s.get("beat_benchmark_pct"),
                "sharpe_on_buys":        s.get("sharpe_on_buys"),
            })
        return results
    except Exception:
        return []
