"""
Daily Picks Service
Screens Nifty 100 stocks, runs prediction engine on each,
returns top 6 BUY signals per horizon (short/medium/long).
Results cached to picks_cache.json so the endpoint is instant after generation.

Learning Alpha Engine integration:
  - Outcome logger resolves previous predictions against actual returns
  - IC engine provides data-driven factor weights (falls back to academic priors)
  - Regime clustering classifies current market (4 unsupervised clusters)
  - Meta-model predicts expected return when enough training data exists
  - Portfolio optimizer computes optimal allocation weights for final picks
  - Weight adapter retrains IC/model/regime after each run
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import yfinance as yf

from services.prediction_engine import PredictionEngine

def _cache_file(market: str) -> str:
    suffix = "" if market == "IN" else f"_{market.lower()}"
    return os.path.join(os.path.dirname(__file__), f"../picks_cache{suffix}.json")

# Full stock universes for Phase-0 bulk screen
from services.stock_universe import IN_STOCKS as _IN_STOCKS, US_STOCKS as _US_STOCKS
_ALL_NSE_SYMBOLS = [sym for sym, _ in _IN_STOCKS]   # 2 300+ NSE tickers
_ALL_US_SYMBOLS  = [sym for sym, _ in _US_STOCKS]   # 1 500+ US tickers

_UNIVERSE = {"IN": _ALL_NSE_SYMBOLS, "US": _ALL_US_SYMBOLS}
_CURRENCY = {"IN": "₹", "US": "$"}
_REGIME_PROXY = {"IN": "RELIANCE", "US": "AAPL"}

# Nifty 100 as always-included anchor (liquid, index-level stocks)
from services.validation_engine import NIFTY_100 as _NIFTY_100

# Mega-cap US fallback if the live screener fails (mirrors the NIFTY_100 role for IN)
_US_MEGACAP_100 = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B", "AVGO", "LLY",
    "JPM", "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST", "MRK",
    "ABBV", "CVX", "ORCL", "ADBE", "CRM", "BAC", "KO", "PEP", "NFLX", "AMD",
    "WMT", "MCD", "TMO", "CSCO", "ACN", "ABT", "LIN", "DHR", "PFE", "NKE",
    "DIS", "INTC", "TXN", "WFC", "VZ", "PM", "CMCSA", "NEE", "INTU", "COP",
    "UNP", "AMGN", "QCOM", "RTX", "LOW", "HON", "BMY", "UPS", "IBM", "GE",
    "CAT", "SPGI", "AMAT", "BA", "DE", "ELV", "SBUX", "GS", "BLK", "PLD",
    "MDT", "ISRG", "GILD", "ADI", "T", "AXP", "MMC", "SYK", "TJX", "REGN",
    "VRTX", "ETN", "CI", "BKNG", "MO", "ZTS", "CB", "SO", "PGR", "DUK",
    "MU", "SLB", "EOG", "AON", "ITW", "APD", "CME", "FI", "EQIX", "WM",
][:100]

# PICKS_CANDIDATES env var: how many top-momentum stocks to deep-predict (default 50).
# 50 × 3 horizons × ~8s = ~20 min — reliable on Render free tier.
_N_CANDIDATES = int(os.getenv("PICKS_CANDIDATES", 50))

print(f"[picks] Universes: NSE {len(_ALL_NSE_SYMBOLS)} / US {len(_ALL_US_SYMBOLS)} stocks → "
      f"bulk-screen → top {_N_CANDIDATES} candidates for deep prediction")


HORIZON_LABELS = {
    "short":  ("1–5 days",   "short-term"),
    "medium": ("2–4 weeks",  "medium-term"),
    "long":   ("3–6 months", "long-term"),
}


def _build_summary(result: dict, horizon: str, currency: str = "₹") -> str:
    """Compose a human-readable analyst-style summary from prediction engine output."""
    name       = result.get("company_name", result.get("symbol", ""))
    confidence = result.get("confidence", 0)
    price      = result.get("current_price", 0)
    target     = result.get("target_price", 0)
    upside     = round((target - price) / price * 100, 1) if price and target else 0
    period, term = HORIZON_LABELS.get(horizon, ("", ""))

    tech  = result.get("technical", {})
    fund  = result.get("fundamental_score", {})
    sent  = result.get("sentiment_score", {})
    reg   = result.get("market_regime", {})
    glob  = result.get("global_context") or {}

    # Tech strength label
    tech_score = tech.get("score", 50)
    if tech_score >= 70:
        tech_label = "strong bullish technical setup"
    elif tech_score >= 60:
        tech_label = "moderately bullish technical momentum"
    else:
        tech_label = "emerging bullish technical signals"

    # Fundamental label
    fund_score = fund.get("score", 50)
    if fund_score >= 70:
        fund_label = "solid fundamental backing"
    elif fund_score >= 55:
        fund_label = "decent fundamental support"
    else:
        fund_label = "neutral fundamental profile"

    # Sentiment label
    sent_label = ""
    if sent.get("label") == "BULLISH" or sent.get("score", 50) >= 60:
        sent_label = " News sentiment is bullish."
    elif sent.get("label") == "BEARISH" or sent.get("score", 50) <= 40:
        sent_label = " Recent news sentiment leans cautious, but technicals override."

    # Market regime
    regime_note = ""
    reg_trend = reg.get("trend", "")
    if reg_trend == "BULL":
        regime_note = " Domestic market is in an uptrend."
    elif reg_trend == "BEAR":
        regime_note = " Domestic market is under pressure — tight stop-loss recommended."

    # Global macro note
    global_note = ""
    global_score = glob.get("score")
    if global_score is not None:
        levels = glob.get("levels", {})
        changes = glob.get("changes", {})
        vix = levels.get("vix")
        sp500_chg = changes.get("sp500")
        crude_chg = changes.get("crude_brent")
        usdinr = levels.get("usdinr")

        parts = []
        if global_score >= 60:
            parts.append("Global macro environment is supportive")
        elif global_score <= 40:
            parts.append("Global macro headwinds are present")

        if vix and vix > 20:
            parts.append(f"VIX elevated at {vix:.0f} (risk-off)")
        elif vix and vix < 14:
            parts.append(f"VIX calm at {vix:.0f} (risk-on)")

        if sp500_chg is not None and abs(sp500_chg) > 0.5:
            parts.append(f"S&P 500 {sp500_chg:+.1f}%")

        if crude_chg is not None and abs(crude_chg) > 1.0:
            parts.append(f"Brent crude {crude_chg:+.1f}%")

        if usdinr:
            parts.append(f"USD/INR ₹{usdinr:.1f}")

        if parts:
            global_note = " " + "; ".join(parts) + "."

    # Confidence tone
    if confidence >= 70:
        conf_tone = f"with high conviction ({confidence}% AI confidence)"
    elif confidence >= 50:
        conf_tone = f"with moderate confidence ({confidence}% AI confidence)"
    else:
        conf_tone = f"as a speculative opportunity ({confidence}% AI confidence)"

    # Quality factor highlights
    quality_note = ""
    qf = result.get("quality_factors") or {}
    qf_breakdown = qf.get("breakdown") or {}
    val_score  = qf_breakdown.get("valuation", {})
    risk_score = qf_breakdown.get("risk_management", {})
    flow_score = qf_breakdown.get("inst_flow", {})
    piotroski  = qf.get("piotroski")

    quality_parts = []
    if isinstance(val_score, dict) and val_score.get("score", 50) >= 65:
        quality_parts.append("attractively valued")
    elif isinstance(val_score, dict) and val_score.get("score", 50) <= 35:
        quality_parts.append("stretched valuation — risk to monitor")
    if isinstance(risk_score, dict) and risk_score.get("score", 50) >= 65:
        quality_parts.append("strong risk-adjusted return profile")
    if isinstance(flow_score, dict) and flow_score.get("score", 50) >= 65:
        quality_parts.append("institutional accumulation signals present")
    if piotroski is not None and piotroski >= 7:
        quality_parts.append(f"Piotroski F-Score {piotroski}/9 (high-quality financials)")
    if quality_parts:
        quality_note = " " + "; ".join(quality_parts[:2]).capitalize() + "."

    score_band = result.get("score_band", "")
    band_note = f" [{score_band}]" if score_band else ""

    summary = (
        f"{name} is flagged as a {term} BUY {conf_tone}{band_note}. "
        f"The AI engine detects a {tech_label} combined with {fund_label}.{sent_label}"
        f"{regime_note}{global_note}{quality_note} "
        f"Target {currency}{target:,.2f} implies {upside}% upside within {period}."
    )
    return summary


_SCREEN_BATCH_SIZE = int(os.getenv("SCREEN_BATCH_SIZE", 300))  # tickers per download batch
_MIN_MCAP_CR = int(os.getenv("MIN_MCAP_CR", 100))   # minimum market cap in crores INR (IN only)
_MIN_MCAP_USD_M = int(os.getenv("MIN_MCAP_USD_M", 2000))  # minimum market cap in $M (US only)

# Per-market screener config: yfinance exchange codes, ticker suffix, fallback universe
_SCREEN_CONFIG = {
    "IN": {"exchanges": ["NSI"], "suffix": ".NS"},
    "US": {"exchanges": ["NMS", "NYQ", "NGM", "ASE", "PCX"], "suffix": ""},
}


def _get_universe_by_mcap(market: str) -> list[str]:
    """
    Use yfinance equity screener to get stocks above a market-cap floor.
    Falls back to the full static universe if the screener fails.
    """
    cfg = _SCREEN_CONFIG[market]
    full_universe = _UNIVERSE[market]
    if market == "IN":
        min_mcap = _MIN_MCAP_CR * 10_000_000  # 1 Cr INR = 10,000,000 INR
        label = f"≥{_MIN_MCAP_CR}Cr"
    else:
        min_mcap = _MIN_MCAP_USD_M * 1_000_000
        label = f"≥${_MIN_MCAP_USD_M}M"

    try:
        exch_query = yf.EquityQuery("or", [
            yf.EquityQuery("eq", ["exchange", ex]) for ex in cfg["exchanges"]
        ]) if len(cfg["exchanges"]) > 1 else yf.EquityQuery("eq", ["exchange", cfg["exchanges"][0]])
        query = yf.EquityQuery("and", [
            exch_query,
            yf.EquityQuery("gt", ["intradaymarketcap", min_mcap]),
        ])
        result = yf.screen(
            query, sortField="intradaymarketcap", sortAsc=False, count=1000
        )
        quotes = result.get("quotes", [])
        suffix = cfg["suffix"]
        syms = [q["symbol"].replace(suffix, "") if suffix else q["symbol"]
                for q in quotes if q.get("symbol")]
        if syms:
            print(f"[picks] [{market}] mcap filter {label}: {len(syms)} stocks qualify")
            return syms
    except Exception as e:
        print(f"[picks] [{market}] mcap screener failed ({e}), using full universe")
    return full_universe


def _bulk_screen(market: str, n_candidates: int = 50) -> list[str]:
    """
    Phase-0 screener: batched yf.download() → momentum rank.

    1. Filter to stocks with market cap above a floor (per-market threshold)
       using yfinance equity screener — removes illiquid micro-caps.
    2. Batch-download in groups of SCREEN_BATCH_SIZE to avoid OOM on Render
       (512 MB RAM). Free each batch's DataFrame immediately after processing.
    3. Rank by composite momentum and return top n_candidates.

    Falls back to Nifty 100 (IN) / mega-cap 100 (US) if all else fails.
    """
    import math
    suffix = _SCREEN_CONFIG[market]["suffix"]
    fallback = _NIFTY_100 if market == "IN" else _US_MEGACAP_100

    universe = _get_universe_by_mcap(market)
    all_tickers = [s + suffix for s in universe]
    batches = [all_tickers[i:i + _SCREEN_BATCH_SIZE]
               for i in range(0, len(all_tickers), _SCREEN_BATCH_SIZE)]
    print(f"[picks] [{market}] Phase-0: downloading {len(all_tickers)} tickers "
          f"in {len(batches)} batches of {_SCREEN_BATCH_SIZE} …")
    t0 = time.time()
    scores: dict[str, float] = {}

    for batch_idx, tickers in enumerate(batches):
        try:
            df = yf.download(
                tickers, period="6d", interval="1d",
                progress=False, auto_adjust=True, threads=False,  # threads=False saves memory
            )
            if df.empty:
                continue

            close = df["Close"] if "Close" in df.columns.get_level_values(0) else None
            if close is None:
                continue
            close = close.dropna(how="all")
            if len(close) < 2:
                continue

            prev_row = close.iloc[-2]
            last_row = close.iloc[-1]
            if last_row.dropna().shape[0] < len(tickers) * 0.3 and len(close) >= 3:
                prev_row = close.iloc[-3]
                last_row = close.iloc[-2]
            first_row = close.iloc[0]

            for ticker in tickers:
                sym = ticker.replace(suffix, "") if suffix else ticker
                try:
                    p_prev  = float(prev_row.get(ticker, float("nan")))
                    p_last  = float(last_row.get(ticker, float("nan")))
                    p_first = float(first_row.get(ticker, float("nan")))
                    if any(math.isnan(x) or x <= 0 for x in (p_prev, p_last, p_first)):
                        continue
                    ret_1d = (p_last - p_prev) / p_prev
                    ret_5d = (p_last - p_first) / p_first
                    score = 0.60 * ret_1d + 0.40 * ret_5d
                    scores[sym] = score
                except Exception:
                    continue

            del df  # free memory immediately
            print(f"[picks] [{market}] Phase-0 batch {batch_idx+1}/{len(batches)}: "
                  f"{len(scores)} scored so far")
        except Exception as e:
            print(f"[picks] [{market}] Phase-0 batch {batch_idx+1} failed: {e}")
            continue

    elapsed = round(time.time() - t0, 1)
    if not scores:
        print(f"[picks] [{market}] Phase-0: no stocks scored — falling back to anchor list")
        return list(fallback[:n_candidates])

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_syms = [sym for sym, _ in ranked[:n_candidates]]
    print(f"[picks] [{market}] Phase-0 complete in {elapsed}s: {len(scores)} scored, "
          f"top candidates: {top_syms[:10]} …")
    return top_syms


def _predict_stock(symbol: str, horizon: str, market: str = "IN") -> dict | None:
    """
    Run prediction engine for one stock + horizon.
    Returns raw scores for ALL non-rejected stocks (not just BUY) so the
    caller can z-score cross-sectionally across the full universe.
    """
    try:
        import asyncio, random
        # Small jitter between requests to avoid Yahoo Finance rate-limit bursts
        time.sleep(random.uniform(0.3, 0.8))
        engine = PredictionEngine()
        result = asyncio.run(engine.predict(symbol, market, horizon))

        if not result:
            return None

        # Hard quality gate — silently skip, but log
        if result.get("signal") == "REJECTED":
            print(f"[picks] {symbol} REJECTED ({horizon}): {result.get('rejection_reasons', [])}")
            return None

        reasoning = result.get("reasoning", [])
        trade = result.get("trade_levels", {})
        qf = result.get("quality_factors") or {}

        return {
            "symbol":      symbol,
            "name":        result.get("company_name", symbol),
            "signal":      result.get("signal"),
            "price":       result.get("current_price"),
            "target":      result.get("target_price"),
            "stop_loss":   trade.get("stop_loss"),
            "entry_low":   trade.get("entry_low"),
            "entry_high":  trade.get("entry_high"),
            "risk_reward": trade.get("risk_reward_ratio"),
            "confidence":  result.get("confidence"),
            # Raw factor scores — kept for cross-sectional z-scoring
            "tech_score":     result.get("technical", {}).get("score", 50),
            "fund_score":     result.get("fundamental_score", {}).get("score", 50),
            "sentiment_score": result.get("sentiment_score", {}).get("score", 50),
            "quality_score":  qf.get("score") or 50,
            "sentiment":      result.get("sentiment_score", {}).get("label", "NEUTRAL"),
            "reasoning":      reasoning,
            "summary":        _build_summary(result, horizon, _CURRENCY.get(market, "₹")),
            "score_band":     result.get("score_band"),
            "global_context": result.get("global_context"),
            "quality_factors": result.get("quality_factors"),
            "horizon":        horizon,
            # Score-snapshot fields (section 4)
            "composite_score":   result.get("composite_score"),
            "confidence_model":  result.get("confidence_score"),
        }
    except Exception:
        pass
    return None


_FACTOR_KEYS = {
    "tech":      "tech_score",
    "fund":      "fund_score",
    "sentiment": "sentiment_score",
    "quality":   "quality_score",
}


def _write_score_snapshots(raw: dict[str, list], market: str = "IN"):
    """
    Persist one daily score snapshot per (symbol, horizon) for every scored
    stock. No-op unless USE_POSTGRES=1 (score history is Postgres-only, since
    Render's local disk doesn't survive restarts). Best-effort — never blocks
    or fails pick generation.
    """
    if os.getenv("USE_POSTGRES") != "1":
        return
    try:
        from services.postgres_store import log_score_snapshot
    except Exception as e:
        print(f"[snapshots] postgres_store unavailable: {e}")
        return

    snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = 0
    for horizon, items in raw.items():
        for r in items:
            try:
                qf = r.get("quality_factors") or {}
                breakdown = qf.get("breakdown") or {}
                log_score_snapshot(
                    snapshot_date=snapshot_date,
                    symbol=r["symbol"],
                    horizon=horizon,
                    composite_score=r.get("composite_score") or 0.0,
                    signal=r.get("signal"),
                    quality_score=r.get("quality_score"),
                    growth_score=breakdown.get("earnings_revision"),
                    valuation_score=breakdown.get("valuation"),
                    technical_score=r.get("tech_score"),
                    sentiment_score=r.get("sentiment_score"),
                    risk_score=breakdown.get("risk_management"),
                    confidence_score=r.get("confidence_model"),
                    factor_breakdown=breakdown or None,
                )
                written += 1
            except Exception as e:
                print(f"[snapshots] {r.get('symbol')} ({horizon}) failed: {e}")
    print(f"[snapshots] wrote {written} score snapshots for {snapshot_date}")


def _zscore_and_rank(
    items: list[dict],
    ic_weights: dict[str, float],
    regime: dict,
    regime_id: int,
    market: str = "IN",
) -> list[dict]:
    """
    Cross-sectional z-scoring + alpha computation for the full universe.

    Step 1 — z-score each factor across the universe snapshot:
        z_i = (score_i − mean(universe)) / std(universe)

    Step 2 — IC-weighted alpha (data-driven weights from ic_engine):
        combined_alpha = Σ IC_weight_k × z_k

    Step 3 — Meta-model alpha (if model is trained):
        meta_alpha = model.predict([z_k, interactions])
        Final ranking signal = meta_alpha if available, else combined_alpha

    This replaces the old hand-crafted 0.45/0.30/… weight table.
    """
    from services.alpha_engine import meta_model as mm

    if not items:
        return items

    horizon = items[0].get("horizon", "medium")

    # Per-factor cross-sectional statistics
    stats: dict[str, tuple[float, float]] = {}
    for factor, key in _FACTOR_KEYS.items():
        vals = [r[key] for r in items if r.get(key) is not None]
        if len(vals) < 2:
            stats[factor] = (50.0, 1.0)
        else:
            arr = np.array(vals, dtype=float)
            stats[factor] = (float(arr.mean()), float(arr.std()) or 1.0)

    enriched = []
    for row in items:
        zscores: dict[str, float] = {}
        combined_alpha = 0.0
        for factor, key in _FACTOR_KEYS.items():
            raw = float(row.get(key) or 50)
            mu, sigma = stats[factor]
            z = (raw - mu) / sigma
            zscores[factor] = round(z, 3)
            combined_alpha += ic_weights.get(factor, 0.25) * z

        combined_alpha = round(combined_alpha, 4)

        # Meta-model predicted return (if available)
        meta_alpha = mm.predict(
            tech_z=zscores.get("tech", 0),
            fund_z=zscores.get("fund", 0),
            sentiment_z=zscores.get("sentiment", 0),
            quality_z=zscores.get("quality", 0),
            combined_alpha=combined_alpha,
            regime_id=regime_id,
            horizon=horizon,
            market=market,
        )

        # Ranking signal: meta_alpha when available (trained model), else IC alpha
        ranking_alpha = round(meta_alpha, 4) if meta_alpha is not None else combined_alpha

        enriched.append({
            **row,
            "factor_zscores":  zscores,
            "combined_alpha":  combined_alpha,
            "meta_alpha":      round(meta_alpha, 4) if meta_alpha is not None else None,
            "ranking_alpha":   ranking_alpha,
            "regime_label":    regime.get("label", "BULL_CALM"),
        })

    return enriched


def _fetch_returns_matrix(symbols: list[str], market: str = "IN", days: int = 126) -> np.ndarray | None:
    """Fetch daily returns for the selected picks to estimate covariance."""
    try:
        suffix = _SCREEN_CONFIG[market]["suffix"]
        tickers = [s + suffix for s in symbols]
        data = yf.download(tickers, period="6mo", auto_adjust=True,
                           progress=False)["Close"]
        if data.empty:
            return None
        returns = data.pct_change().dropna()
        return returns.values  # (T × N)
    except Exception:
        return None


def generate_picks(market: str = "IN") -> dict:
    """
    Learning Alpha Engine pipeline:

      Phase 0 — Resolve outcomes: log actual returns for past predictions
      Phase 0b— Bulk screen: one yf.download() for the full stock universe → top N candidates
      Phase 1 — Score candidates: run prediction engine on top N momentum stocks
      Phase 2 — Detect regime: classify current market with KMeans clustering
      Phase 3 — IC weights: get data-driven factor weights (academic priors until
                             enough real outcome data accumulates)
      Phase 4 — Z-score + alpha: cross-sectional normalisation + IC-weighted alpha,
                                  with meta-model override when trained
      Phase 5 — Select picks: rank by ranking_alpha; keep top 6 BUY per horizon
      Phase 6 — Optimise: mean-variance portfolio weights for the selected picks
      Phase 7 — Log predictions: store factor z-scores for future IC computation
      Phase 8 — Adapt: retrain IC/meta-model/regime after picks are published

    market: "IN" (NSE, default) or "US" (NYSE/NASDAQ).
    """
    import traceback
    global _last_error
    _last_error[market] = None

    try:
        return _generate_picks_inner(market)
    except Exception as e:
        _last_error[market] = traceback.format_exc()
        print(f"[picks] [{market}] generate_picks CRASHED: {e}\n{_last_error[market]}")
        # Save a minimal payload so the UI shows "no signals today" instead of spinning
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "picks": {"short": [], "medium": [], "long": []},
            "error": str(e),
        }
        try:
            with open(_cache_file(market), "w") as f:
                json.dump(payload, f)
        except Exception:
            pass
        if os.getenv("USE_POSTGRES") == "1":
            try:
                from services.postgres_store import save_picks_to_db
                save_picks_to_db(payload, market=market)
            except Exception:
                pass
        return payload


# Module-level last error per market (exposed via /api/picks/status)
_last_error: dict[str, str | None] = {"IN": None, "US": None}


def _generate_picks_inner(market: str = "IN") -> dict:
    from services.alpha_engine.outcome_logger import resolve_pending_outcomes
    from services.alpha_engine.ic_engine import get_ic_weights
    from services.alpha_engine.regime_cluster import detect_regime
    from services.alpha_engine.optimizer import optimize
    from services.alpha_engine.store import log_prediction
    from services.alpha_engine.weight_adapter import run_adaptation
    from services.global_context import get_global_context

    start = time.time()
    currency = _CURRENCY.get(market, "₹")

    # ── Phase 0: Resolve outcomes from previous prediction runs ──────────────
    resolve_pending_outcomes()

    # ── Global crumb refresh — do this ONCE before bulk fetching ─────────────
    try:
        regime_ticker = "^NSEI" if market == "IN" else "^GSPC"
        if hasattr(yf.utils, "get_crumb"):
            yf.utils.get_crumb(force=True)
        else:
            yf.download(regime_ticker, period="1d", progress=False, auto_adjust=True)
        print(f"[picks] [{market}] Yahoo Finance session refreshed.")
    except Exception as e:
        print(f"[picks] [{market}] Session refresh failed (non-fatal): {e}")

    # ── Phase 0b: Bulk screen the market's stock universe → top N momentum candidates ─
    # One yf.download() call for the full universe (~60s) then rank by
    # composite momentum score. Falls back to an anchor list if download fails.
    candidates = _bulk_screen(market, _N_CANDIDATES)
    print(f"[picks] [{market}] Starting deep prediction for {len(candidates)} candidates × 3 horizons …")

    # ── Phase 2: Detect market regime (done once, shared across all stocks) ──
    try:
        global_ctx_proxy = get_global_context(_REGIME_PROXY.get(market, "RELIANCE"))
    except Exception:
        global_ctx_proxy = {}

    regime = detect_regime(global_ctx_proxy)
    regime_id    = regime["regime_id"]
    regime_label = regime["label"]
    print(f"[picks] [{market}] Regime: {regime_label} — {regime['description']}")

    # ── Phase 1: Deep-predict candidates ─────────────────────────────────────
    # max_workers=1 to avoid Yahoo Finance rate-limiting Render's IP.
    tasks = [(sym, h) for sym in candidates for h in ("short", "medium", "long")]
    raw: dict[str, list] = {"short": [], "medium": [], "long": []}

    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {pool.submit(_predict_stock, sym, h, market): (sym, h) for sym, h in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 30 == 0:
                print(f"[picks] [{market}] {done}/{len(tasks)} done …")
            r = future.result()
            if r:
                raw[r["horizon"]].append(r)

    # ── Score snapshots (section 4) — persist every scored stock for history ──
    # Piggybacks on the universe scan above so we don't re-fetch anything.
    _write_score_snapshots(raw, market)

    # ── Phases 3-6 per horizon ────────────────────────────────────────────────
    picks: dict[str, list] = {}
    alpha_engine_meta: dict[str, dict] = {}  # diagnostics for API

    for horizon in ("short", "medium", "long"):
        items = raw[horizon]
        if not items:
            picks[horizon] = []
            continue

        # Phase 3 — IC weights (regime-adjusted)
        ic_weights = get_ic_weights(
            horizon,
            market=market,
            regime_multipliers=regime.get("weight_multipliers"),
        )
        print(f"[picks] [{market}] {horizon} IC weights: {ic_weights}")

        # Phase 4 — Z-score + alpha
        universe = _zscore_and_rank(items, ic_weights, regime, regime_id, market=market)
        ranked   = sorted(universe, key=lambda x: x.get("ranking_alpha", 0), reverse=True)

        # Quality gates before final selection:
        # 1. Confidence must be >= 25% (0% confidence picks are noise, not signals)
        # 2. Short-term picks must not be overbought (RSI > 75 = likely to pull back)
        # 3. No unfavorable risk/reward or severe governance red flag — these
        #    demote confidence to exactly 30 in the prediction engine (see
        #    _apply_risk_reward_adjustment / _apply_pledge_adjustment), which
        #    clears the >=25% floor above. That floor exists to filter pure
        #    noise, not to let a flagged "avoid"-level red flag back into a
        #    curated "Top 6" list just because it didn't drop low enough.
        def _passes_quality_gate(r: dict, hz: str) -> bool:
            conf = r.get("confidence") or 0
            if conf < 25:
                print(f"[picks] {r['symbol']} ({hz}) filtered: confidence {conf}% < 25%")
                return False
            indicators = {
                item.get("indicator") for item in r.get("reasoning", []) if isinstance(item, dict)
            }
            if "Risk/Reward" in indicators or "Governance Risk" in indicators:
                print(f"[picks] {r['symbol']} ({hz}) filtered: unfavorable risk/reward or governance red flag")
                return False
            if hz == "short":
                reasons = " ".join(
                    item.get("reason", "") if isinstance(item, dict) else str(item)
                    for item in r.get("reasoning", [])
                )
                if "Overbought" in reasons:
                    print(f"[picks] {r['symbol']} ({hz}) filtered: overbought RSI in short-term")
                    return False
            return True

        top_buy = [
            r for r in ranked
            if r.get("signal") == "BUY" and _passes_quality_gate(r, horizon)
        ][:6]

        # Phase 6 — Portfolio optimisation
        if len(top_buy) > 1:
            alphas = [r.get("ranking_alpha", 0) for r in top_buy]
            symbols = [r["symbol"] for r in top_buy]
            ret_matrix = _fetch_returns_matrix(symbols, market)
            port_weights = optimize(
                alphas=alphas,
                returns_matrix=ret_matrix,
                max_weight=0.40,
                risk_aversion=2.0,
                regime_label=regime_label,
            )
            for pick, w in zip(top_buy, port_weights):
                pick["portfolio_weight"] = w
        elif len(top_buy) == 1:
            # Cap single-pick allocation at 50% — 100% in one stock is too aggressive
            top_buy[0]["portfolio_weight"] = 0.50

        picks[horizon] = top_buy
        alpha_engine_meta[horizon] = {
            "ic_weights":  ic_weights,
            "regime":      regime_label,
            "n_scored":    len(universe),
            "n_buy":       sum(1 for r in universe if r.get("signal") == "BUY"),
            "meta_model":  any(r.get("meta_alpha") is not None for r in top_buy),
        }
        print(
            f"[picks] [{market}] {horizon}: {len(universe)} scored, "
            f"{alpha_engine_meta[horizon]['n_buy']} BUY, "
            f"{len(top_buy)} picks | "
            f"meta_model={'on' if alpha_engine_meta[horizon]['meta_model'] else 'off (IC alpha)'}"
        )

    # ── Phase 7: Log predictions to SQLite ────────────────────────────────────
    for horizon, items in picks.items():
        for rank, pick in enumerate(items, start=1):
            try:
                log_prediction(
                    symbol=pick["symbol"],
                    horizon=horizon,
                    factor_zscores=pick.get("factor_zscores", {}),
                    combined_alpha=pick.get("combined_alpha", 0),
                    meta_alpha=pick.get("meta_alpha"),
                    signal=pick.get("signal", "BUY"),
                    price=pick.get("price") or 0.0,
                    regime_label=regime_label,
                    confidence_score=pick.get("confidence"),
                    is_daily_pick=True,
                    pick_rank=rank,
                    market=market,
                )
            except Exception as e:
                print(f"[picks] [{market}] Log error for {pick['symbol']}: {e}")

    elapsed = round(time.time() - start, 1)
    total = sum(len(v) for v in picks.values())
    universe_size = len(_UNIVERSE.get(market, []))
    print(f"[picks] [{market}] Done in {elapsed}s — {total} BUY picks found across "
          f"{len(candidates)} candidates from {universe_size} stocks.")

    payload = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "market":          market,
        "currency":        currency,
        "picks":           picks,
        "alpha_engine":    alpha_engine_meta,
        "regime":          {"label": regime_label, "description": regime["description"]},
        "screened_from":   universe_size,
        "candidates":      len(candidates),
    }

    # Save to disk (best-effort — ephemeral on Render free tier)
    try:
        with open(_cache_file(market), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"[picks] [{market}] Disk cache write failed: {e}")

    # Save to Postgres (survives redeploys)
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import save_picks_to_db
            save_picks_to_db(payload, market=market)
            print(f"[picks] [{market}] Saved to Postgres.")
        except Exception as e:
            print(f"[picks] [{market}] Postgres save failed: {e}")

    # ── Phase 8: Adapt weights in background ─────────────────────────────────
    try:
        import threading
        threading.Thread(target=run_adaptation, args=(market,), daemon=True).start()
    except Exception as e:
        print(f"[weight_adapter] Could not start: {e}")

    # Send to Telegram if configured (IN only — channel is India-focused)
    if market == "IN":
        try:
            from services.telegram_bot import send_picks_to_telegram
            send_picks_to_telegram(picks)
        except Exception as e:
            print(f"[telegram] Error: {e}")

    return payload


def get_cached_picks(market: str = "IN") -> dict | None:
    """
    Return today's picks for a market. Reads from Postgres first (survives
    Render redeploys), falls back to local disk cache.
    """
    # Postgres first
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import load_picks_from_db
            data = load_picks_from_db(market=market)
            if data:
                return data
        except Exception as e:
            print(f"[picks] [{market}] Postgres load failed, falling back to disk: {e}")

    # Disk fallback
    try:
        with open(_cache_file(market)) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def picks_generated_today(market: str = "IN") -> bool:
    """Return True if today's picks (own market's local trading-day date) exist
    and have at least one BUY pick. IN uses IST, US uses DST-aware US/Eastern."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    data = get_cached_picks(market)
    if not data or not data.get("generated_at"):
        return False
    try:
        tz = timezone(timedelta(hours=5, minutes=30)) if market == "IN" else ZoneInfo("America/New_York")
        generated_at = datetime.fromisoformat(
            data["generated_at"].replace("Z", "+00:00")
        ).astimezone(tz)
        today_local = datetime.now(tz).date()
        if generated_at.date() < today_local:
            return False
        # Also require at least one actual pick — empty payload means a prior crash/0-signal run
        picks = data.get("picks", {})
        has_picks = any(len(v) > 0 for v in picks.values())
        return has_picks
    except Exception:
        return False


# Guard to prevent concurrent generation runs (module-level, shared across threads,
# keyed by market so an IN run and a US run can't trip each other's flag).
# Lock makes the check-then-set atomic — plain bool had a TOCTOU race where two
# concurrent POST /picks/generate requests both passed the guard simultaneously.
import threading as _threading
_generating: dict[str, bool] = {"IN": False, "US": False}
_generating_lock = _threading.Lock()
