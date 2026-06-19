"""
Daily Picks Service
Screens Nifty 100 stocks, runs prediction engine on each,
returns top 5 BUY signals per horizon (short/medium/long).
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

CACHE_FILE = os.path.join(os.path.dirname(__file__), "../picks_cache.json")

# Universe: pull from validation_engine's deduplicated Nifty 100 list.
# PICKS_UNIVERSE_LIMIT env var caps the count for constrained environments
# (e.g. Render free tier — set to 25 there, leave unset for full 98-stock run).
from services.validation_engine import NIFTY_100 as _NIFTY_100
# Default 25 stocks: 25×3 horizons = 75 tasks ≈ 10 min on Render free tier.
# Full 98-stock run takes ~40 min and gets killed. Set PICKS_UNIVERSE_LIMIT=98 to override.
_universe_limit = int(os.getenv("PICKS_UNIVERSE_LIMIT", 25))
NIFTY10T = _NIFTY_100[:_universe_limit]
print(f"[picks] Universe: {len(NIFTY10T)}/{len(_NIFTY_100)} stocks "
      f"(set PICKS_UNIVERSE_LIMIT to override; default 25 for reliability)")


HORIZON_LABELS = {
    "short":  ("1–5 days",   "short-term"),
    "medium": ("2–4 weeks",  "medium-term"),
    "long":   ("3–6 months", "long-term"),
}


def _build_summary(result: dict, horizon: str) -> str:
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
        f"Target ₹{target:,.2f} implies {upside}% upside within {period}."
    )
    return summary


def _predict_stock(symbol: str, horizon: str) -> dict | None:
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
        result = asyncio.run(engine.predict(symbol, "IN", horizon))

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
            "risk_reward": trade.get("risk_reward"),
            "confidence":  result.get("confidence"),
            # Raw factor scores — kept for cross-sectional z-scoring
            "tech_score":     result.get("technical", {}).get("score", 50),
            "fund_score":     result.get("fundamental_score", {}).get("score", 50),
            "sentiment_score": result.get("sentiment_score", {}).get("score", 50),
            "quality_score":  qf.get("score") or 50,
            "sentiment":      result.get("sentiment_score", {}).get("label", "NEUTRAL"),
            "reasoning":      reasoning,
            "summary":        _build_summary(result, horizon),
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


def _write_score_snapshots(raw: dict[str, list]):
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


def _fetch_returns_matrix(symbols: list[str], days: int = 126) -> np.ndarray | None:
    """Fetch daily returns for the selected picks to estimate covariance."""
    try:
        tickers = [s + ".NS" for s in symbols]
        data = yf.download(tickers, period="6mo", auto_adjust=True,
                           progress=False)["Close"]
        if data.empty:
            return None
        returns = data.pct_change().dropna()
        return returns.values  # (T × N)
    except Exception:
        return None


def generate_picks() -> dict:
    """
    Learning Alpha Engine pipeline:

      Phase 0 — Resolve outcomes: log actual returns for past predictions
      Phase 1 — Score universe: run prediction engine on all Nifty 100 stocks
      Phase 2 — Detect regime: classify current market with KMeans clustering
      Phase 3 — IC weights: get data-driven factor weights (academic priors until
                             enough real outcome data accumulates)
      Phase 4 — Z-score + alpha: cross-sectional normalisation + IC-weighted alpha,
                                  with meta-model override when trained
      Phase 5 — Select picks: rank by ranking_alpha; keep top 5 BUY per horizon
      Phase 6 — Optimise: mean-variance portfolio weights for the selected picks
      Phase 7 — Log predictions: store factor z-scores for future IC computation
      Phase 8 — Adapt: retrain IC/meta-model/regime after picks are published
    """
    from services.alpha_engine.outcome_logger import resolve_pending_outcomes
    from services.alpha_engine.ic_engine import get_ic_weights
    from services.alpha_engine.regime_cluster import detect_regime
    from services.alpha_engine.optimizer import optimize
    from services.alpha_engine.store import log_prediction
    from services.alpha_engine.weight_adapter import run_adaptation
    from services.global_context import get_global_context

    print(f"[picks] Starting Learning Alpha Engine for {len(NIFTY10T)} stocks × 3 horizons …")
    start = time.time()

    # ── Phase 0: Resolve outcomes from previous prediction runs ──────────────
    resolve_pending_outcomes()

    # ── Global crumb refresh — do this ONCE before bulk fetching ─────────────
    # Running 100 stocks back-to-back expires Yahoo's session mid-run.
    try:
        import yfinance as yf
        # API varies by yfinance version
        if hasattr(yf.utils, "get_crumb"):
            yf.utils.get_crumb(force=True)
        elif hasattr(yf, "download"):
            # Force a lightweight download to refresh the session/crumb
            yf.download("^NSEI", period="1d", progress=False, auto_adjust=True)
        print("[picks] Yahoo Finance session refreshed.")
    except Exception as e:
        print(f"[picks] Session refresh failed (non-fatal): {e}")

    # ── Phase 2: Detect market regime (done once, shared across all stocks) ──
    # We need global context for regime features; use a proxy (no symbol)
    try:
        global_ctx_proxy = get_global_context("RELIANCE")
    except Exception:
        global_ctx_proxy = {}

    regime = detect_regime(global_ctx_proxy)
    regime_id    = regime["regime_id"]
    regime_label = regime["label"]
    print(f"[picks] Regime: {regime_label} — {regime['description']}")

    # ── Phase 1: Score all stocks ─────────────────────────────────────────────
    # max_workers=1 to avoid Yahoo Finance rate-limiting Render's IP.
    # Two parallel workers fire 6+ simultaneous requests which triggers 401s.
    tasks = [(sym, h) for sym in NIFTY10T for h in ("short", "medium", "long")]
    raw: dict[str, list] = {"short": [], "medium": [], "long": []}

    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {pool.submit(_predict_stock, sym, h): (sym, h) for sym, h in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 30 == 0:
                print(f"[picks] {done}/{len(tasks)} done …")
            r = future.result()
            if r:
                raw[r["horizon"]].append(r)

    # ── Score snapshots (section 4) — persist every scored stock for history ──
    # Piggybacks on the universe scan above so we don't re-fetch anything.
    _write_score_snapshots(raw)

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
            regime_multipliers=regime.get("weight_multipliers"),
        )
        print(f"[picks] {horizon} IC weights: {ic_weights}")

        # Phase 4 — Z-score + alpha
        universe = _zscore_and_rank(items, ic_weights, regime, regime_id)
        ranked   = sorted(universe, key=lambda x: x.get("ranking_alpha", 0), reverse=True)
        top_buy  = [r for r in ranked if r.get("signal") == "BUY"][:5]

        # Phase 6 — Portfolio optimisation
        if len(top_buy) > 1:
            alphas = [r.get("ranking_alpha", 0) for r in top_buy]
            symbols = [r["symbol"] for r in top_buy]
            ret_matrix = _fetch_returns_matrix(symbols)
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
            top_buy[0]["portfolio_weight"] = 1.0

        picks[horizon] = top_buy
        alpha_engine_meta[horizon] = {
            "ic_weights":  ic_weights,
            "regime":      regime_label,
            "n_scored":    len(universe),
            "n_buy":       sum(1 for r in universe if r.get("signal") == "BUY"),
            "meta_model":  any(r.get("meta_alpha") is not None for r in top_buy),
        }
        print(
            f"[picks] {horizon}: {len(universe)} scored, "
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
                )
            except Exception as e:
                print(f"[picks] Log error for {pick['symbol']}: {e}")

    payload = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "picks":           picks,
        "alpha_engine":    alpha_engine_meta,
        "regime":          {"label": regime_label, "description": regime["description"]},
    }

    # Save to disk (best-effort — ephemeral on Render free tier)
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"[picks] Disk cache write failed: {e}")

    # Save to Postgres (survives redeploys)
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import save_picks_to_db
            save_picks_to_db(payload)
            print("[picks] Saved to Postgres.")
        except Exception as e:
            print(f"[picks] Postgres save failed: {e}")

    elapsed = round(time.time() - start, 1)
    total = sum(len(v) for v in picks.values())
    print(f"[picks] Done in {elapsed}s — {total} BUY picks found.")

    # ── Phase 8: Adapt weights in background ─────────────────────────────────
    try:
        import threading
        threading.Thread(target=run_adaptation, daemon=True).start()
    except Exception as e:
        print(f"[weight_adapter] Could not start: {e}")

    # Send to Telegram if configured
    try:
        from services.telegram_bot import send_picks_to_telegram
        send_picks_to_telegram(picks)
    except Exception as e:
        print(f"[telegram] Error: {e}")

    return payload


def get_cached_picks() -> dict | None:
    """
    Return today's picks. Reads from Postgres first (survives Render redeploys),
    falls back to local disk cache.
    """
    # Postgres first
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import load_picks_from_db
            data = load_picks_from_db()
            if data:
                return data
        except Exception as e:
            print(f"[picks] Postgres load failed, falling back to disk: {e}")

    # Disk fallback
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def picks_generated_today() -> bool:
    """Return True if today's picks (IST date) are already in cache."""
    from datetime import timedelta
    data = get_cached_picks()
    if not data or not data.get("generated_at"):
        return False
    try:
        IST = timezone(timedelta(hours=5, minutes=30))
        generated_at = datetime.fromisoformat(
            data["generated_at"].replace("Z", "+00:00")
        ).astimezone(IST)
        today_ist = datetime.now(IST).date()
        return generated_at.date() >= today_ist
    except Exception:
        return False


# Guard to prevent concurrent generation runs (module-level, shared across threads)
_generating = False
