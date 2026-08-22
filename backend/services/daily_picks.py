"""
Daily Picks Service
Screens Nifty 100 stocks, runs prediction engine on each, and PUBLISHES up to
3 conviction-gated BUY signals per horizon (short/medium/long) — see
_apply_conviction_publication_gate and services/thresholds.py's
DAILY_PICKS_PUBLICATION registry. Internally still selects/evaluates up to 6
eligible BUY candidates per horizon (unchanged ranking/selection); only
those with Model Conviction ("confidence") >= 85.0 are published, capped at
3, in the existing ranking order (0-3 published picks per horizon is a
valid, expected outcome — never backfilled).
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
import logging
import math
import os
import random
import re
import threading
import time
import uuid as _uuid
from datetime import datetime, timezone

import numpy as np
import yfinance as yf

from services.prediction_engine import PredictionEngine
from services.alpha_engine import alpha_observations as _alpha_obs
from services.thresholds import DAILY_PICKS_PUBLICATION as _DP_PUBLICATION

log = logging.getLogger(__name__)

# Learning Alpha Engine remediation, Phase 2A — shadow-only canonical
# cross-sectional alpha observation snapshot. See alpha_observations.py's
# module docstring: nothing in production reads this table yet.


# Keys present on internal candidate dicts (added for alpha_observations
# construction only) that must never leak into the published Daily Picks
# JSON payload — stripped when picks[horizon] is assigned.
_ALPHA_OBS_ONLY_KEYS = {"sentiment_available", "quality_available", "quality_raw_score"}


def _market_local_date(dt: datetime, market: str):
    """Market-local calendar date for a tz-aware UTC datetime — same
    IST/US-Eastern convention already used by picks_generated_today()."""
    from datetime import timedelta as _timedelta
    from zoneinfo import ZoneInfo as _ZoneInfo
    tz = timezone(_timedelta(hours=5, minutes=30)) if market == "IN" else _ZoneInfo("America/New_York")
    return dt.astimezone(tz).date()


def _parse_reference_session_date(as_of: str | None):
    """The date component of generation_reference_as_of (a daily-bar
    timestamp, e.g. pandas Timestamp.isoformat() — already the trading
    session's own date, not a live quote timestamp needing tz conversion)."""
    if not as_of:
        return None
    try:
        return datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _build_alpha_observation_row(
    item: dict, *, run_id: str, market: str, horizon: str,
    run_generated_at: datetime, run_session_date, regime_id, regime_label,
    pick_meta: dict | None = None,
) -> dict | None:
    """
    Build one canonical alpha_observations row from a single _zscore_and_rank
    enriched candidate. Returns None (and logs a structured warning) when
    required generation-reference provenance is missing or reference_price
    is not strictly positive — Part E of the Phase 2A spec explicitly
    forbids fabricating provenance (never substitute current price or 0.0),
    so such a row is simply omitted from this shadow-only table rather than
    persisted with invented values.
    """
    symbol = item.get("symbol")
    ref_price = item.get("generation_reference_price")
    ref_source = item.get("generation_reference_source")
    ref_basis = item.get("generation_reference_price_basis")
    ref_as_of = item.get("generation_reference_as_of")
    ref_session_date = _parse_reference_session_date(ref_as_of)

    if (
        not ref_source or not ref_basis or not ref_session_date
        or ref_price is None or not isinstance(ref_price, (int, float))
        or ref_price <= 0
    ):
        log.warning(
            f"[alpha_observations] [{market}] [{horizon}] {symbol}: missing/invalid "
            f"generation-reference provenance (price={ref_price!r}, source={ref_source!r}, "
            f"basis={ref_basis!r}, as_of={ref_as_of!r}) — omitting from canonical snapshot, "
            "not substituting current price or 0.0."
        )
        return None

    zscores = item.get("factor_zscores") or {}
    return {
        "observation_id": str(_uuid.uuid4()),
        "run_id": run_id,
        "market": market,
        "horizon": horizon,
        "symbol": symbol,
        "run_generated_at": run_generated_at,
        "run_session_date": run_session_date,
        "reference_session_date": ref_session_date,
        "reference_price": float(ref_price),
        "reference_price_source": ref_source,
        "reference_price_basis": ref_basis,
        # Raw scores: never fabricated when the underlying evidence was
        # unavailable — a missing sentiment/quality reading stores NULL here
        # even though _zscore_and_rank() may have used a fallback internally
        # for ranking purposes (that internal fallback is unchanged by this
        # phase; this column just never repeats it as if it were evidence).
        "technical_raw_score": item.get("tech_score"),
        "fundamental_raw_score": item.get("fund_score"),
        "sentiment_raw_score": item.get("sentiment_score") if item.get("sentiment_available") else None,
        # Never the ranking field (`quality_score`, populated by
        # _predict_stock) — quality_raw_score is the genuine pre-fallback
        # source value, kept as its own independent column even though the
        # ranking field (DP-009) now also preserves a genuine 0 correctly.
        "quality_raw_score": item.get("quality_raw_score") if item.get("quality_available") else None,
        "sentiment_available": bool(item.get("sentiment_available", False)),
        "quality_available": bool(item.get("quality_available", False)),
        "technical_zscore": zscores.get("tech", 0.0),
        "fundamental_zscore": zscores.get("fund", 0.0),
        "sentiment_zscore": zscores.get("sentiment", 0.0),
        "quality_zscore": zscores.get("quality", 0.0),
        "composite_score": item.get("composite_score"),
        "ic_combined_alpha": item.get("combined_alpha", 0.0),
        "meta_alpha": item.get("meta_alpha"),
        "ranking_alpha": item.get("ranking_alpha", item.get("combined_alpha", 0.0)),
        "signal": item.get("signal"),
        "signal_confidence": item.get("confidence"),
        # Canonical GLOBAL KMeans regime — never the local per-stock
        # BULL/BEAR/SIDEWAYS trend string PredictionEngine computes.
        "canonical_regime_id": regime_id,
        "canonical_regime_label": regime_label,
        "feature_schema_version": _alpha_obs.FEATURE_SCHEMA_VERSION,
        "regime_schema_version": _alpha_obs.REGIME_SCHEMA_VERSION,
        "is_daily_pick": bool(pick_meta),
        "pick_rank": (pick_meta or {}).get("pick_rank"),
        "portfolio_weight": (pick_meta or {}).get("portfolio_weight"),
    }

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

# ── Daily-Picks-only heuristic US common-equity filter ────────────────────
# Derived deterministically from _US_STOCKS at import time. Kept separate so
# _US_STOCKS / _ALL_US_SYMBOLS remain intact for any non-Daily-Picks features.

# Whole-word, case-insensitive patterns for exchange-traded fund/note exclusion.
# Using \b word boundaries avoids the 'Netflix' false positive that a plain
# .lower()/'etf' substring check would produce ('n-e-t-f' is inside 'netflix').
_RE_ETF = re.compile(r"\bETF\b", re.IGNORECASE)
_RE_ETN = re.compile(r"\bETN\b", re.IGNORECASE)


def _build_us_daily_picks_heuristic_filtered(raw_universe: list) -> list[str]:
    """
    Return only plain US common-equity tickers from the raw static universe.
    No provider calls — purely local name/symbol keyword matching.

    Excluded categories (applied in order; first match wins):
      1. Preferred shares    — ticker symbol contains '$'
      2. ETFs                — name matches whole-word /ETF/ (case-insensitive);
                               whole-word boundary avoids 'Netflix' false positive
      3. ETNs                — name matches whole-word /ETN/ (case-insensitive)
      4. Leveraged/inverse   — name contains ' 2x' or ' 3x' (space-prefixed to
                               avoid 'V2X, Inc. Common Stock' as a false positive;
                               all proper levered products use this spacing)
      5. UltraPro products   — name contains 'ultrapro' (case-insensitive);
                               ProShares 3x levered/inverse products that omit
                               the 'ETF' label in the static universe name,
                               e.g. TQQQ='ProShares UltraPro QQQ'
      6. Daily bull/bear     — name contains 'daily' AND ('bull' OR 'bear')
                               (safety net for any non-ETF levered reset product
                               not caught by rules 4 or 5)
      7. Index funds         — name contains 'index fund' (case-insensitive);
                               catches iShares/FlexShares etc. whose static name
                               says 'Index Fund' instead of 'ETF',
                               e.g. IWM='iShares Russell 2000 Index Fund'
      8. SPACs               — name contains 'acquisition corp',
                               'acquisition corporation', 'acquisition inc', or
                               'special purpose acquisition' (case-insensitive)
      9. Units               — name contains '- unit' or ends with ' units'
                               (case-insensitive)
     10. Closed-end funds    — name contains 'closed-end' or 'closed end'
                               (case-insensitive)

    ADR handling is a separate product decision — not excluded here.
    """
    result: list[str] = []
    for sym, name in raw_universe:
        n = name.lower()
        if "$" in sym:
            continue                                            # preferred share
        if _RE_ETF.search(name):
            continue                                            # exchange-traded fund (whole-word, case-insensitive)
        if _RE_ETN.search(name):
            continue                                            # exchange-traded note (whole-word, case-insensitive)
        if " 2x" in n or " 3x" in n:
            continue                                            # leveraged/inverse daily
        if "ultrapro" in n:
            continue                                            # ProShares 3x levered (non-ETF-labeled)
        if "daily" in n and ("bull" in n or "bear" in n):
            continue                                            # non-ETF levered reset product
        if "index fund" in n:
            continue                                            # investment index fund (non-ETF-labeled)
        if (
            "acquisition corp" in n
            or "acquisition corporation" in n
            or "special purpose acquisition" in n
            or "acquisition inc" in n
        ):
            continue                                            # blank-check SPAC
        if "- unit" in n or n.endswith(" units") or n.endswith("- units"):
            continue                                            # bundled unit instrument
        if "closed-end" in n or "closed end" in n:
            continue                                            # closed-end fund
        result.append(sym)
    return result


_US_DAILY_PICKS_HEURISTIC_FILTERED: list[str] = _build_us_daily_picks_heuristic_filtered(_US_STOCKS)
# Frozen set for O(1) intersection checks inside _get_universe_by_mcap.
_US_DAILY_PICKS_HEURISTIC_FILTERED_SET: frozenset[str] = frozenset(_US_DAILY_PICKS_HEURISTIC_FILTERED)
# Known limitation — not common-equity master:
#   This list is built by local keyword exclusion on static names in US_STOCKS.
#   It is NOT a verified common-equity master: instruments whose static name
#   contains none of the excluded keywords (e.g. QQQ='Invesco QQQ Trust,
#   Series 1', GLD='SPDR Gold Shares') pass through undetected. The live
#   screener's exchange/mcap filter usually removes such instruments before
#   Phase-0; the _US_MEGACAP_100 anchor contains no ETFs or non-equities.
#   Eliminating residual non-equity pass-throughs requires a curated
#   common-equity master (Option B — separate product decision).

# PICKS_CANDIDATES env var: how many top-momentum stocks to deep-predict.
# Raised from 50 to 400 to match _TARGET_UNIVERSE_SIZE (defined further below,
# alongside _TIER_QUOTA) — sized so this step's momentum-rank-then-truncate
# becomes a no-op truncation and the large/mid/small-cap stratification built
# upstream survives intact into Phase 1, rather than being narrowed right back
# down to a small, likely large-cap-skewed handful. Timing is not a hard
# constraint for this deployment (confirmed: ~7-hour runway before market
# open) — 400 × 3 horizons at max_workers=1 is a real, expected increase over
# the old ~20 min figure (roughly 60-90 min), not hidden, just no longer the
# limiting factor it was when Render's free tier made 50 the practical ceiling.
_N_CANDIDATES = int(os.getenv("PICKS_CANDIDATES", 400))


def _phase1_chunk_size() -> int:
    """
    Phase-1 candidate chunk size: never larger than the SEC facts cache's
    entry capacity, so within one chunk a symbol's medium/long-horizon
    predictions always find the companyfacts payload its short-horizon
    prediction just cached (the 2026-07-23/24 incident's SEC-refetch churn
    can't recur even if every symbol in the chunk touches a distinct CIK).
    Reads the adapter's own cap so the two can't silently drift apart;
    falls back to 25 (the cap's current value) if the import ever fails.
    """
    try:
        from services.sec_edgar_adapter import _FACTS_CACHE_MAX
        return max(1, int(_FACTS_CACHE_MAX))
    except Exception:
        return 25

log.info(
    f"[picks] US heuristic-filtered common-equity universe: {len(_US_DAILY_PICKS_HEURISTIC_FILTERED)} "
    f"of {len(_ALL_US_SYMBOLS)} raw symbols "
    f"({len(_ALL_US_SYMBOLS) - len(_US_DAILY_PICKS_HEURISTIC_FILTERED)} ETFs/preferreds/SPACs excluded)"
)
log.info(f"[picks] Universes: NSE {len(_ALL_NSE_SYMBOLS)} / US {len(_US_DAILY_PICKS_HEURISTIC_FILTERED)} heuristic-filtered stocks → "
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

    # Confidence tone — labeled "Model Conviction X/100" (not "% AI
    # confidence"), consistent with the conviction-gated publication
    # policy's terminology; the underlying `confidence` value/scale is
    # unchanged, only this summary sentence's wording.
    if confidence >= 70:
        conf_tone = f"with high conviction (Model Conviction {confidence}/100)"
    elif confidence >= 50:
        conf_tone = f"with moderate confidence (Model Conviction {confidence}/100)"
    else:
        conf_tone = f"as a speculative opportunity (Model Conviction {confidence}/100)"

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
_MIN_MCAP_CR = int(os.getenv("MIN_MCAP_CR", 100))   # IN small-cap junk floor, crores INR
_MIN_MCAP_USD_M_FLOOR = int(os.getenv("MIN_MCAP_USD_M_FLOOR", 100))  # US small-cap junk floor, $M

# Per-market ticker suffix, still needed for yf.download() in _bulk_screen's
# momentum step and for the static NIFTY_100/_US_MEGACAP_100 fallbacks.
_SCREEN_CONFIG = {
    "IN": {"exchanges": ["NSI"], "suffix": ".NS"},
    "US": {"exchanges": ["NMS", "NYQ", "NGM", "ASE", "PCX"], "suffix": ""},
}

# ── Large/Mid/Small cap stratification ────────────────────────────────────
# Sourced from stock_fundamentals_cache (screener.in for IN, yfinance-derived
# for US — both nightly-refreshed, see fundamentals_refresh.py /
# us_fundamentals_refresh.py), replacing yf.screen()-based discovery entirely.
#
# yf.screen()'s market-cap-descending sort with a hard cutoff was, by
# construction, Large+Mid cap only: IN's old 250-cap matches SEBI's own
# rank convention (Large = rank 1-100, Mid = 101-250) almost exactly, and
# US's old $2,000M floor excluded true US small-caps (<$2B) outright. Small
# cap never reached the pipeline in either market, regardless of screener
# health. Stratified sampling below fixes that structurally, not just by
# raising a cutoff.
_TARGET_UNIVERSE_SIZE = 400
# ~40/30/30, normalized from the agreed 45/35/35 (which summed to 115, not 100).
_TIER_QUOTA = {"large": 160, "mid": 120, "small": 120}
# Short-term Phase 5 selection priority: "best performing stock" for
# short-term explicitly means high-conviction first, not tier diversity —
# see _select_with_tier_quota's docstring for the medium/long counterpart.
_SHORT_TERM_CONFIDENCE_PRIORITY = 80
_MIN_HEALTHY_UNIVERSE = 100   # below this the cache is NOT a healthy broad
                              # universe (e.g. the nightly refresh job failed
                              # or hasn't run yet) — falls back to the static list


def _assign_cap_tiers(market: str, ranked: list[tuple[str, float]]) -> dict[str, str]:
    """
    symbol -> "large"/"mid"/"small", from a market-cap-descending
    ``(symbol, market_cap)`` list (fundamentals_cache.get_ranked_universe's
    return shape).

    IN uses SEBI's own rank-based convention (Large = rank 1-100, Mid =
    101-250, Small = 251+) — a rank, not a value, since that's how India's
    own market classifies cap tiers. US uses the standard value-based
    convention (Large > $10B, Mid $2B-$10B, Small < $2B) since US market
    convention is value-based, not rank-based. Both apply the small-cap junk
    floor (_MIN_MCAP_CR / _MIN_MCAP_USD_M_FLOOR) to exclude micro-caps/shells
    from the small tier entirely, rather than letting stratification pull in
    anything with a positive market cap no matter how tiny.
    """
    tiers: dict[str, str] = {}
    if market == "IN":
        for rank, (sym, cap) in enumerate(ranked, start=1):
            if rank <= 100:
                tiers[sym] = "large"
            elif rank <= 250:
                tiers[sym] = "mid"
            elif cap >= _MIN_MCAP_CR:
                tiers[sym] = "small"
            # else: below the junk floor — excluded from every tier
    else:
        for sym, cap_m in ranked:
            if cap_m > 10_000:
                tiers[sym] = "large"
            elif cap_m > 2_000:
                tiers[sym] = "mid"
            elif cap_m >= _MIN_MCAP_USD_M_FLOOR:
                tiers[sym] = "small"
            # else: below the junk floor — excluded from every tier
    return tiers


def _stratified_sample(
    ranked: list[tuple[str, float]], tiers: dict[str, str], quotas: dict[str, int],
) -> tuple[list[str], dict[str, int]]:
    """
    Per tier, take up to ``quotas[tier]`` symbols in market-cap-descending
    order (``ranked`` is already sorted that way). If a tier has fewer
    available symbols than its quota — a real, honest possibility (e.g. a
    thin night for small-cap data in the nightly scrape) — take what's
    available and report the shortfall via the returned tier-count dict;
    never silently backfill from another tier just to hit
    ``_TARGET_UNIVERSE_SIZE`` exactly. Returns ``(symbols, tier_counts)``.
    """
    by_tier: dict[str, list[str]] = {"large": [], "mid": [], "small": []}
    for sym, _cap in ranked:
        tier = tiers.get(sym)
        if tier in by_tier:
            by_tier[tier].append(sym)

    symbols: list[str] = []
    tier_counts: dict[str, int] = {}
    for tier, quota in quotas.items():
        picked = by_tier.get(tier, [])[:quota]
        symbols.extend(picked)
        tier_counts[tier] = len(picked)
    return symbols, tier_counts


# Medium/long-term Phase 5 tier quota, scaled down to the final 6-pick list
# (the 160/120/120 _TIER_QUOTA above governs the much larger Phase 1
# deep-scoring pool, not this final slice). At only 6 slots, an equal split
# is the simplest, most defensible way to guarantee tier diversity survives
# selection — a strict 40/30/30 proportional split of 6 rounds to 2/2/2
# anyway (round(0.4*6)=2, round(0.3*6)=2, remainder=2).
_MEDIUM_LONG_TIER_QUOTA_6 = {"large": 2, "mid": 2, "small": 2}


def _select_with_tier_quota(candidates: list[dict], quotas: dict[str, int]) -> list[dict]:
    """
    Per tier, take the top ``quotas[tier]`` candidates (``candidates`` is
    already alpha-ranked, so "top" means highest ``ranking_alpha`` within
    that tier). If a tier has fewer qualifying candidates than its quota,
    top up from the combined leftover pool (any tier) by alpha, so the final
    list still reaches ``sum(quotas.values())`` whenever the data supports
    it — this is the ONE place a cross-tier backfill is correct, since the
    goal here is a real Top-6 pick list, not preserving an exact
    Phase-1-pool tier distribution. Final list is re-sorted by alpha so tier
    quota affects WHICH stocks are chosen, not the display order once chosen.
    """
    by_tier: dict[str, list[dict]] = {"large": [], "mid": [], "small": []}
    for r in candidates:
        tier = r.get("cap_tier")
        if tier in by_tier:
            by_tier[tier].append(r)
        else:
            by_tier.setdefault("large", []).append(r)  # unknown tier: safest default bucket

    selected: list[dict] = []
    leftover: list[dict] = []
    for tier, quota in quotas.items():
        bucket = by_tier.get(tier, [])
        selected.extend(bucket[:quota])
        leftover.extend(bucket[quota:])

    target = sum(quotas.values())
    if len(selected) < target:
        leftover_sorted = sorted(leftover, key=lambda x: x.get("ranking_alpha", 0), reverse=True)
        selected.extend(leftover_sorted[: target - len(selected)])

    selected.sort(key=lambda x: x.get("ranking_alpha", 0), reverse=True)
    return selected


def _get_universe_by_mcap(
    market: str,
) -> tuple[list[str], str, bool, int | None, dict]:
    """
    Build a large/mid/small-cap-stratified universe from
    stock_fundamentals_cache (the nightly-refreshed, screener.in/yfinance-
    sourced table Multibagger already maintains) instead of a live
    yf.screen() call. Returns ``(symbols, universe_used, universe_degraded,
    cache_raw_count, selection_meta)`` — same 5-tuple shape as before this
    change, so every existing caller/consumer of this function's return
    value is unaffected.

    ``cache_raw_count`` is the number of symbols the cache had a positive
    market cap for, before stratified sampling — the "screener_raw_count"
    slot's new meaning. ``selection_meta`` additionally carries
    ``tier_map`` (symbol -> tier, threaded through to Phase 1 so Phase 5 can
    apply a per-horizon tier rule) and ``tier_counts`` (how many symbols each
    tier actually contributed) — both purely additive, never removing an
    existing key.

    A cache result below ``_MIN_HEALTHY_UNIVERSE`` (refresh job failed or
    hasn't populated this market yet) is NOT a healthy broad universe: falls
    back to the curated static list (``NIFTY_100`` for IN, ``_US_MEGACAP_100``
    for US), labelled truthfully as ``universe_used="static_fallback"``/
    ``"anchor"`` with ``universe_degraded=True`` — identical safety net to
    before, just triggered by an empty/thin cache instead of a screener
    exception.
    """
    from services import fundamentals_cache

    fallback = list(_NIFTY_100) if market == "IN" else list(_US_MEGACAP_100)
    fallback_used = "static_fallback" if market == "IN" else "anchor"

    try:
        ranked = fundamentals_cache.get_ranked_universe(market)
    except Exception as e:
        log.warning("[picks] [%s] fundamentals_cache universe query failed: %s", market, _classify_error(e))
        log.warning(
            "[picks] [%s] DEGRADED UNIVERSE — %s (%d symbols) reason=cache_query_failed",
            market, fallback_used, len(fallback),
        )
        meta = {
            "universe_candidate_count": len(fallback), "attempts": 1,
            "reason": "cache_query_failed", "error_category": "cache_error",
            "tier_map": {}, "tier_counts": {},
        }
        return (fallback, fallback_used, True, None, meta)

    if len(ranked) < _MIN_HEALTHY_UNIVERSE:
        log.warning(
            "[picks] [%s] DEGRADED UNIVERSE — %s (%d symbols) reason=cache_insufficient_symbols "
            "(cache had %d)", market, fallback_used, len(fallback), len(ranked),
        )
        meta = {
            "universe_candidate_count": len(fallback), "attempts": 1,
            "reason": "cache_insufficient_symbols", "error_category": "insufficient_symbols",
            "tier_map": {}, "tier_counts": {},
        }
        return (fallback, fallback_used, True, len(ranked), meta)

    cache_raw_count = len(ranked)  # before US eligibility filtering, per this
                                   # function's own docstring contract — must
                                   # be captured before `ranked` is reassigned
                                   # below, not after.
    if market == "US":
        eligible_set = _US_DAILY_PICKS_HEURISTIC_FILTERED_SET
        ranked = [(sym, cap) for sym, cap in ranked if sym in eligible_set]

    tiers = _assign_cap_tiers(market, ranked)
    symbols, tier_counts = _stratified_sample(ranked, tiers, _TIER_QUOTA)

    log.info(
        "[picks] [%s] stratified universe: %d symbols (large=%d mid=%d small=%d) "
        "from %d cache-ranked candidates",
        market, len(symbols), tier_counts.get("large", 0), tier_counts.get("mid", 0),
        tier_counts.get("small", 0), cache_raw_count,
    )
    meta = {
        "universe_candidate_count": len(symbols), "attempts": 1,
        "reason": "healthy_fundamentals_cache_universe", "error_category": "none",
        "tier_map": tiers, "tier_counts": tier_counts,
    }
    return (symbols, "fundamentals_cache", False, cache_raw_count, meta)


def _classify_error(exc: Exception) -> str:
    """
    Stable, non-secret error category derived from an exception's message —
    never the raw exception text, a stack trace, or provider response
    content (only this category is ever logged/persisted).
    """
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg or "connection" in msg:
        return "transient_error"
    return "non_transient_error"


def _bulk_screen(
    market: str, n_candidates: int = 50, job_id: str | None = None
) -> tuple[list[str], int, str, bool, int | None, dict]:
    """
    Phase-0 screener: batched yf.download() → momentum rank.

    Returns ``(candidates, phase0_universe_size, universe_used, universe_degraded,
    screener_raw_count, selection_meta)``.
    ``phase0_universe_size`` is the ACTUAL count of tickers passed to yf.download
    (the stratified large/mid/small-cap universe from ``_get_universe_by_mcap``)
    and is the value that should appear in ``screened_from`` /
    ``universe_eligible_size`` in the final payload.  ``screener_raw_count``
    is now the count of symbols stock_fundamentals_cache had a positive market
    cap for, before stratified sampling — None when the cache was too thin/
    unavailable and a static fallback was used.  ``selection_meta`` carries
    ``universe_candidate_count``/``attempts``/``reason``/``error_category``/
    ``tier_map``/``tier_counts`` straight through from ``_get_universe_by_mcap``
    — purely additive observability plus the per-symbol tier Phase 5 uses.

    1. ``_get_universe_by_mcap`` supplies a large/mid/small-cap-stratified
       universe sourced from the nightly-refreshed stock_fundamentals_cache
       (screener.in for IN, yfinance-derived for US) — no live screener call.
       ``n_candidates`` (via ``_N_CANDIDATES``) is sized to match that
       stratified pool, so this step's momentum ranking re-orders it but
       doesn't meaningfully truncate it; the tier distribution built upstream
       survives into Phase 1.
    2. Batch-download in groups of SCREEN_BATCH_SIZE to avoid OOM on Render
       (512 MB RAM). Free each batch's DataFrame immediately after processing.
    3. Rank by composite momentum and return top n_candidates.

    Falls back to Nifty 100 (IN) / anchor megacap (US) if no scores result.
    """
    import math
    suffix = _SCREEN_CONFIG[market]["suffix"]
    fallback = _NIFTY_100 if market == "IN" else _US_MEGACAP_100

    universe, universe_used, universe_degraded, _screener_raw_count, _selection_meta = _get_universe_by_mcap(market)
    all_tickers = [s + suffix for s in universe]
    phase0_universe_size = len(all_tickers)
    batches = [all_tickers[i:i + _SCREEN_BATCH_SIZE]
               for i in range(0, len(all_tickers), _SCREEN_BATCH_SIZE)]
    log.info(f"[picks] [{market}] Phase-0: downloading {len(all_tickers)} tickers "
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
            log.info(f"[picks] [{market}] Phase-0 batch {batch_idx+1}/{len(batches)}: "
                  f"{len(scores)} scored so far")
        except Exception as e:
            log.warning(f"[picks] [{market}] Phase-0 batch {batch_idx+1} failed: {e}")
        # Progress after every batch regardless of success/failure
        _try_job_progress(job_id, "phase_0b", batch_idx + 1, len(batches))

    elapsed = round(time.time() - t0, 1)
    if not scores:
        log.info(f"[picks] [{market}] Phase-0: no stocks scored — falling back to anchor list")
        return (list(fallback[:n_candidates]), phase0_universe_size, universe_used, universe_degraded, _screener_raw_count, _selection_meta)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_syms = [sym for sym, _ in ranked[:n_candidates]]
    log.info(f"[picks] [{market}] Phase-0 complete in {elapsed}s: {len(scores)} scored, "
          f"top candidates: {top_syms[:10]} …")
    return (top_syms, phase0_universe_size, universe_used, universe_degraded, _screener_raw_count, _selection_meta)


def _predict_stock(symbol: str, horizon: str, market: str = "IN") -> dict | None:
    """
    Run prediction engine for one stock + horizon.
    Returns raw scores for ALL non-rejected stocks (not just BUY) so the
    caller can z-score cross-sectionally across the full universe.
    """
    try:
        import asyncio, math, random
        # Small jitter between requests to avoid Yahoo Finance rate-limit bursts
        time.sleep(random.uniform(0.3, 0.8))
        engine = PredictionEngine()
        result = asyncio.run(engine.predict(symbol, market, horizon))

        if not result:
            return None

        # Product Integrity #011: an error-shaped result (e.g. timeout,
        # empty history, insufficient data — predict() returns
        # {"error": ..., "code": ...} in several places) is truthy and has
        # no "signal" key, so it previously fell through both checks below
        # and got appended into the batch with mostly None/default fields
        # instead of being cleanly excluded. Skip it the same way a
        # REJECTED result is skipped.
        if result.get("error"):
            log.info(f"[picks] {symbol} data-provider error ({horizon}): "
                      f"{result.get('code')} — {result.get('error')}")
            return None

        # Hard quality gate — silently skip, but log
        if result.get("signal") == "REJECTED":
            log.info(f"[picks] {symbol} REJECTED ({horizon}): {result.get('rejection_reasons', [])}")
            return None

        reasoning = result.get("reasoning", [])
        trade = result.get("trade_levels", {})
        qf = result.get("quality_factors") or {}

        # Wave 0C follow-up (Release Review 7): unavailable news sentiment must
        # be MISSING ranking evidence, not a numeric 50. When data_available is
        # False (stale-only/undated/no articles) — or the compatibility score is
        # non-numeric/non-finite — store None so _zscore_and_rank excludes it
        # from the sentiment cross-section and assigns z = 0.0 (zero rank
        # influence). The service's compatibility score stays in the source
        # response for non-ranking consumers; it never becomes rank evidence.
        _sent = result.get("sentiment_score") or {}
        _sent_raw = _sent.get("score")
        _sent_available = (
            _sent.get("data_available", True)
            and isinstance(_sent_raw, (int, float))
            and not isinstance(_sent_raw, bool)
            and math.isfinite(_sent_raw)
        )

        # Phase 2A (alpha_observations): quality evidence availability AND the
        # genuine pre-fallback source value must be captured at source, not
        # inferred later from a numeric value. _quality_raw reads
        # qf.get("score") directly, once, before the ranking field's own
        # missing-value fallback below (DP-009: now an explicit `is not None`
        # check, so a genuine 0 is preserved there too — this variable
        # remains additional, internal-only metadata for the shadow
        # alpha_observations builder, independent of that ranking field).
        _quality_raw = qf.get("score")
        _quality_available = _quality_raw is not None

        return {
            "symbol":      symbol,
            "name":        result.get("company_name", symbol),
            "signal":      result.get("signal"),
            "price":       result.get("current_price"),
            "target":      result.get("target_price"),
            "stop_loss":   trade.get("stop_loss"),
            "entry_low":   trade.get("entry_low"),
            "entry_high":  trade.get("entry_high"),
            # Release 12A: generation-reference provenance (additive; legacy
            # picks simply lack these keys). Lets the UI prove — instead of
            # assume — that a later quote is comparable before claiming the
            # price "moved" out of the entry zone.
            "generated_at": result.get("generated_at"),
            "generation_reference_price": (result.get("price_reference") or {}).get("price"),
            "generation_reference_source": (result.get("price_reference") or {}).get("source"),
            "generation_reference_price_basis": (result.get("price_reference") or {}).get("price_basis"),
            "generation_reference_as_of": (result.get("price_reference") or {}).get("as_of"),
            # Product Integrity #011: authoritative at generation time (the
            # backend already checked and, where possible, retried) —
            # additive, the frontend's own independent client-side
            # recomputation (sessionFreshness.ts) is unaffected and remains
            # the source of truth for display; these are provenance only.
            "generation_reference_is_stale": (result.get("price_reference") or {}).get("is_stale"),
            "generation_reference_expected_session": (result.get("price_reference") or {}).get("expected_session"),
            # Product Integrity #012 diagnostic (2026-07-17): surfaces why
            # the bhavcopy correction didn't apply on a stale pick, so a
            # failure mode (blocked egress, 404, timeout) is visible in the
            # persisted pick itself instead of a log line that a 1000+
            # symbol run can bury.
            "generation_reference_bhavcopy_failure_reason": (result.get("price_reference") or {}).get("bhavcopy_failure_reason"),
            "risk_reward": trade.get("risk_reward_ratio"),
            "confidence":  result.get("confidence"),
            # Raw factor scores — kept for cross-sectional z-scoring
            "tech_score":     result.get("technical", {}).get("score", 50),
            "fund_score":     result.get("fundamental_score", {}).get("score", 50),
            "sentiment_score": float(_sent_raw) if _sent_available else None,
            # Phase A1 evidence-gap closure (Daily Picks): the SAME governed
            # technical-signal vocabulary (BUY/SELL/HOLD) the Stock
            # Detail/Research path exposes as `technical.overall` — both
            # paths read it from the identical get_signal_summary(df) call
            # inside this same PredictionEngine.predict() invocation, so
            # this is not a derived/thresholded value, it's the authoritative
            # value itself. Always present whenever `result` reached this
            # point (get_signal_summary always returns "overall" — no
            # separate availability gate is needed, unlike sentiment above).
            "technical_signal": result.get("technical", {}).get("overall"),
            # DP-009: explicit None-check, not `or 50` — that truthiness
            # fallback silently turned a genuine 0 into a fabricated neutral
            # 50 (Python's `or` treats 0/0.0 as falsy). A genuine 0 is real
            # evidence and must be preserved; only a truly missing/None
            # score may use the neutral fallback.
            "quality_score":  qf.get("score") if qf.get("score") is not None else 50,
            "sentiment_available": _sent_available,
            "quality_available":   _quality_available,
            # alpha_observations-only: the genuine pre-fallback quality
            # source value (never `or`-coalesced) — see _quality_raw above.
            "quality_raw_score":   _quality_raw,
            "sentiment":      _sent.get("label", "NEUTRAL"),
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
        log.warning(f"[snapshots] postgres_store unavailable: {e}")
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
                    market=market,
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
                log.warning(f"[snapshots] {r.get('symbol')} ({horizon}) failed: {e}")
    log.info(f"[snapshots] wrote {written} score snapshots for {snapshot_date}")


def _zscore_and_rank(
    items: list[dict],
    ic_weights: dict[str, float],
    regime: dict,
    regime_id: int,
    market: str = "IN",
    production_learning_enabled: bool | None = None,
) -> list[dict]:
    """
    Cross-sectional z-scoring + alpha computation for the full universe.

    Step 1 — z-score each factor across the universe snapshot:
        z_i = (score_i − mean(universe)) / std(universe)

    Step 2 — IC-weighted alpha (data-driven weights from ic_engine):
        combined_alpha = Σ IC_weight_k × z_k
        (ic_weights is already containment-gated by the caller — see
        ic_engine.get_production_ic_weights — this function just applies
        whatever weights it's given.)

    Step 3 — Meta-model alpha (if a model artifact exists):
        meta_alpha = model.predict([z_k, interactions])
        This is ALWAYS computed (shadow/diagnostic value — Learning Alpha
        Engine remediation, Phase 1) but only becomes the ranking signal
        when production_learning_enabled is True. While disabled (the
        default), ranking_alpha always equals combined_alpha — the
        meta-model's shadow output is never allowed to overwrite it.

    This replaces the old hand-crafted 0.45/0.30/… weight table.
    """
    from services.alpha_engine import meta_model as mm

    if production_learning_enabled is None:
        from services.alpha_engine.containment import is_production_learning_enabled
        production_learning_enabled = is_production_learning_enabled()

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
            raw_val = row.get(key)
            if raw_val is None:
                # Missing evidence (today only sentiment can be None — set by
                # _predict_stock when news data_available is False). z = 0.0 is
                # "exactly average relative evidence": contributes nothing to
                # combined_alpha and cannot move this stock's rank up or down.
                # The stats pool above already excluded None rows, so it also
                # cannot distort any other stock's z-score. Never coerce back
                # to a numeric 50 — that placeholder is not evidence.
                zscores[factor] = 0.0
                continue
            # DP-010: raw_val is already known not-None here (the branch
            # above returned for that case) — `or 50` on this line was a
            # second, redundant truthiness fallback that only ever fired on
            # a genuine 0/0.0, silently substituting a fabricated neutral 50
            # for real evidence. A present numeric value, including exactly
            # 0, is evidence and must be used as-is.
            raw = float(raw_val)
            mu, sigma = stats[factor]
            z = (raw - mu) / sigma
            zscores[factor] = round(z, 3)
            combined_alpha += ic_weights.get(factor, 0.25) * z

        combined_alpha = round(combined_alpha, 4)

        # Meta-model predicted return — always computed as a shadow/diagnostic
        # value (Learning Alpha Engine remediation, Phase 1), regardless of
        # containment state. Never skipped, since "IC and meta-model
        # calculations may continue only in shadow mode" — it just may not
        # become the ranking signal below.
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

        # Ranking signal: meta_alpha only when production learning is
        # explicitly enabled AND a model is trained; otherwise always the
        # IC-weighted (containment-gated) combined_alpha. The shadow
        # meta_alpha value above is preserved in the output below for
        # observability, but it never overwrites ranking_alpha while
        # contained.
        if production_learning_enabled and meta_alpha is not None:
            ranking_alpha = round(meta_alpha, 4)
        else:
            ranking_alpha = combined_alpha

        enriched.append({
            **row,
            "factor_zscores":  zscores,
            "combined_alpha":  combined_alpha,
            "meta_alpha":      round(meta_alpha, 4) if meta_alpha is not None else None,
            "ranking_alpha":   ranking_alpha,
            "regime_label":    regime.get("label", "BULL_CALM"),
            # Learning Alpha Engine remediation, Phase 1 — observability only.
            # True only when meta_alpha actually determined ranking_alpha
            # above; distinct from "meta_alpha is not None", which just means
            # a shadow value was computed.
            "meta_alpha_used_for_ranking": bool(production_learning_enabled and meta_alpha is not None),
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


# ── Issuer-level deduplication for US Daily Picks final selection ─────────────
# Static local mapping: ticker → canonical issuer group name.
# Prevents two share classes of the same underlying company from both appearing
# in the same horizon's final Daily Picks, which would create undisclosed
# concentrated exposure (e.g. GOOG + GOOGL = double Alphabet exposure).
# No external data source — deterministic and provider-independent.
_US_ISSUER_GROUP: dict[str, str] = {
    "GOOG":  "ALPHABET",
    "GOOGL": "ALPHABET",
    "BRK-A": "BERKSHIRE_HATHAWAY",
    "BRK-B": "BERKSHIRE_HATHAWAY",
    "FOX":   "FOX_CORP",
    "FOXA":  "FOX_CORP",
    "NWS":   "NEWS_CORP",
    "NWSA":  "NEWS_CORP",
}


def _deduplicate_by_issuer(
    ranked_buys: list[dict], market: str
) -> tuple[list[dict], int]:
    """
    Remove duplicate issuer exposure from a ranked BUY list before the
    final per-horizon top-6 slice.

    Applies only when ``_US_ISSUER_GROUP`` is populated and market is "US".
    For non-US markets returns the list unchanged with zero suppressed count.

    Algorithm: walk candidates in existing rank order (highest-alpha first).
    Keep the first qualifying candidate per issuer group; suppress all later
    candidates whose ticker maps to the same group.  Never substitutes or
    creates a candidate — only suppresses extras.

    Returns ``(deduped_list, n_suppressed)``.
    """
    if market != "US":
        return ranked_buys, 0

    seen_groups: set[str] = set()
    deduped: list[dict] = []
    suppressed = 0

    for candidate in ranked_buys:
        sym = candidate.get("symbol", "")
        group = _US_ISSUER_GROUP.get(sym, sym)  # unmapped ticker → own singleton group
        if group in seen_groups:
            suppressed += 1
            log.info(
                f"[picks] [US] issuer dedup: suppressed {sym!r} "
                f"(group={group}) — same issuer already selected"
            )
        else:
            seen_groups.add(group)
            deduped.append(candidate)

    return deduped, suppressed


# DP-025 foundation: the three functions below were extracted verbatim from
# logic that used to live inline (a nested closure and two literal blocks)
# inside _generate_picks_inner's per-horizon loop, so an offline pipeline-
# replay harness (services/validation/pipeline_replay.py) can call the exact
# same eligibility/selection/allocation logic production uses, without
# duplicating it into a second implementation. Each is pure — no network,
# no persistence — and _generate_picks_inner now calls these instead of
# inlining the same logic, so there is exactly one implementation of each
# rule. See test_daily_picks_extracted_helpers_parity.py for proof that
# hoisting these did not change production behaviour.

def _passes_quality_gate(r: dict, hz: str) -> bool:
    """
    Quality gates before final selection:
      1. Confidence must be >= 25% (0% confidence picks are noise, not signals)
      2. Short-term picks must not be overbought (RSI > 75 = likely to pull back)
      3. No unfavorable risk/reward or severe governance red flag — these
         demote confidence to exactly 30 in the prediction engine (see
         _apply_risk_reward_adjustment / _apply_pledge_adjustment), which
         clears the >=25% floor above. That floor exists to filter pure
         noise, not to let a flagged "avoid"-level red flag back into a
         curated "Top 6" list just because it didn't drop low enough.
    """
    conf = r.get("confidence") or 0
    if conf < 25:
        log.info(f"[picks] {r['symbol']} ({hz}) filtered: confidence {conf}% < 25%")
        return False
    indicators = {
        item.get("indicator") for item in r.get("reasoning", []) if isinstance(item, dict)
    }
    if "Risk/Reward" in indicators or "Governance Risk" in indicators:
        log.info(f"[picks] {r['symbol']} ({hz}) filtered: unfavorable risk/reward or governance red flag")
        return False
    # Confirmed live, Epic 002 Sprint #011: the Financial Strength
    # Engine's hard liquidity_distress gate (Sprint #010) is the
    # same severity class as Risk/Reward and Governance Risk above
    # (confidence capped at 30 in the Prediction Engine) -- but
    # without this check, that confidence still clears the >=25%
    # floor above, so a liquidity-distress-flagged company could
    # reach the curated Top 6 list despite a confirmed red flag,
    # exactly what the two checks above already exist to prevent
    # for their own red-flag types. Checking the indicator NAME
    # alone (like the two checks above do) would be wrong here:
    # "Financial Strength" is also the indicator name for a
    # POSITIVE confidence boost (e.g. a fortress-balance-sheet
    # company) -- a blanket name exclusion would wrongly filter
    # out genuinely strong companies too, so this checks for the
    # specific hard-gate phrase instead.
    fs_reasons = " ".join(
        item.get("reason", "") for item in r.get("reasoning", [])
        if isinstance(item, dict) and item.get("indicator") == "Financial Strength"
    )
    if "liquidity distress" in fs_reasons.lower():
        log.info(f"[picks] {r['symbol']} ({hz}) filtered: Financial Strength liquidity distress red flag")
        return False
    if hz == "short":
        reasons = " ".join(
            item.get("reason", "") if isinstance(item, dict) else str(item)
            for item in r.get("reasoning", [])
        )
        if "Overbought" in reasons:
            log.info(f"[picks] {r['symbol']} ({hz}) filtered: overbought RSI in short-term")
            return False
    return True


def _select_short_term_top_six(candidates: list[dict]) -> list[dict]:
    """
    Confidence priority with fill-down (explicit user decision): short-term
    cares about "best performing stock," not tier diversity, and
    specifically wants high-conviction (>80% confidence) calls surfaced
    first. `candidates` is already alpha-ordered, so partitioning into two
    buckets and concatenating preserves alpha as the secondary/tiebreak key
    within each bucket. This naturally fills down to fewer (or zero)
    high-confidence picks on a genuinely weak-conviction day rather than
    diluting the >80% bar to pad the count to 6 — matches this platform's
    existing "an empty/short picks list is a legitimate outcome, not
    something to backfill with noise" convention.
    """
    high_conf = [r for r in candidates if (r.get("confidence") or 0) > _SHORT_TERM_CONFIDENCE_PRIORITY]
    rest = [r for r in candidates if (r.get("confidence") or 0) <= _SHORT_TERM_CONFIDENCE_PRIORITY]
    return (high_conf + rest)[:6]


def _apply_conviction_publication_gate(ranked_candidates: list[dict]) -> tuple[list[dict], dict]:
    """
    Conviction-gated Daily Picks publication policy
    (feature/daily-picks-conviction-gated-publication).

    `ranked_candidates` is the existing, already-selected/already-ordered
    per-horizon slate (e.g. `top_buy` — already eligibility/BUY/quality-
    gated and already in this horizon's deterministic ranking order). This
    function does NOT re-rank, re-score, or re-gate eligibility; it only
    decides, in that existing order, which of those candidates are
    published:

      1. fail closed — a candidate whose `confidence` (reused, relabeled
         "Model Conviction") is missing, non-numeric, non-finite (NaN/
         +-Infinity), or outside the valid 0-100 scale is EXCLUDED, never
         published, regardless of any other field;
      2. retain only candidates with Model Conviction >=
         DAILY_PICKS_PUBLICATION.MIN_CONVICTION_TO_PUBLISH (85.0);
      3. preserve the existing ranking order (no re-sorting);
      4. publish at most DAILY_PICKS_PUBLICATION.MAX_PUBLISHED_PER_HORIZON
         (3) of the retained candidates.

    The threshold is never lowered and gates are never relaxed to fill
    slots — 0, 1, 2, or 3 published picks are all legitimate outcomes.

    Returns (published, meta) where `meta` carries truthful, additive
    publication diagnostics (n_conviction_qualified, n_published,
    conviction_threshold, max_published_per_horizon) for the caller to
    fold into this horizon's alpha_engine_meta without touching the
    pre-existing n_scored/n_buy semantics.
    """
    threshold = _DP_PUBLICATION.MIN_CONVICTION_TO_PUBLISH
    max_published = _DP_PUBLICATION.MAX_PUBLISHED_PER_HORIZON

    qualified: list[dict] = []
    for cand in ranked_candidates:
        conviction = cand.get("confidence")
        if isinstance(conviction, bool) or not isinstance(conviction, (int, float)):
            continue  # missing / non-numeric — fail closed
        if not math.isfinite(conviction):
            continue  # NaN / +-Infinity — fail closed
        if conviction < 0 or conviction > 100:
            continue  # out of the valid 0-100 scale — fail closed
        if conviction < threshold:
            continue
        qualified.append(cand)

    published = qualified[:max_published]
    meta = {
        "n_conviction_qualified": len(qualified),
        "n_published": len(published),
        "conviction_threshold": threshold,
        "max_published_per_horizon": max_published,
    }
    return published, meta


def _build_published_pick_meta(published_buy: list[dict]) -> dict[str, dict]:
    """
    Conviction-gated publication correction (finding 1, follow-up to
    5a006498; extracted as its own testable function per finding 3,
    follow-up to 0f2bbed8).

    Builds the `symbol -> {pick_rank, portfolio_weight}` side dict that
    `_build_alpha_observation_row` reads to decide `is_daily_pick` (via
    `bool(pick_meta)`), `pick_rank`, and `portfolio_weight` for EVERY
    scored candidate in a horizon's `universe`.

    MUST be called with `published_buy` — the conviction-gated, <=3-item,
    already-published subset — never the full up-to-6 `top_buy` selection.
    Calling it with `top_buy` would wrongly mark a conviction-gate-excluded
    or 3-cap-excluded Top-6 candidate as `is_daily_pick=True` with a real
    rank/weight in the alpha_observations evidence trail, even though that
    candidate is absent from the actual `/picks` payload and from Phase 7's
    `log_prediction(is_daily_pick=True)` calls (which only ever iterate the
    published cohort). A candidate not present in `published_buy` simply
    gets no entry in the returned dict, so `_build_alpha_observation_row`
    falls back to its own `is_daily_pick=False`/`pick_rank=None`/
    `portfolio_weight=None` default for it — exactly matching what was
    actually published.
    """
    return {
        pick["symbol"]: {"pick_rank": rank, "portfolio_weight": pick.get("portfolio_weight")}
        for rank, pick in enumerate(published_buy, start=1)
    }


def _compute_portfolio_allocation(
    alphas: list[float],
    returns_matrix,
    regime_label: str,
    max_weight: float = 0.40,
    risk_aversion: float = 2.0,
) -> tuple[list[float], float]:
    """
    DP-020/DP-021 — pure portfolio-allocation step, and the SINGLE
    allocation authority for zero, one, or many candidates: given
    per-candidate ranking alphas (already computed) and an optional returns
    matrix, compute per-candidate weights and the resulting cash/unallocated
    fraction.

    Contains no I/O — callers are responsible for supplying `returns_matrix`
    (e.g. via `_fetch_returns_matrix` in production, or a frozen snapshot
    array in a pipeline replay).

    DP-021: the one-candidate case is NOT special-cased here anymore — it
    delegates to `optimizer.optimize()` exactly like every other candidate
    count, which itself now returns `min(1, max_weight)` for a single
    candidate (previously this function hard-coded 50%, and
    `optimizer.optimize()` separately hard-coded 100% — two different,
    both-wrong answers for the same case; DPD-005's hard-cap contract now
    applies uniformly).
    """
    from services.alpha_engine.optimizer import optimize

    n = len(alphas)
    if n == 0:
        return [], 0.0
    weights = optimize(
        alphas=alphas, returns_matrix=returns_matrix,
        max_weight=max_weight, risk_aversion=risk_aversion, regime_label=regime_label,
    )
    cash_pct = round(max(0.0, 1.0 - sum(weights)), 4)
    return weights, cash_pct


def generate_picks(market: str = "IN", job_id: str | None = None) -> dict:
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
      Phase 8 — Adapt (non-critical, post-persistence): retrain weights in background

    job_id: the durable Postgres job row reserved by the caller.  If provided and
    USE_POSTGRES=1, this function manages the full job lifecycle (running →
    completed/failed) and runs a heartbeat daemon thread.
    market: "IN" (NSE, default) or "US" (NYSE/NASDAQ).
    """
    import traceback
    global _last_error
    _last_error[market] = None

    use_job = bool(job_id and os.getenv("USE_POSTGRES") == "1")

    # ── Heartbeat setup ───────────────────────────────────────────────────────
    _hb_stop = _threading.Event()
    _hb_thread = None

    if use_job:
        try:
            from services.postgres_store import mark_daily_picks_job_running
            mark_daily_picks_job_running(job_id)
        except Exception as e:
            log.warning(f"[picks] [{market}] Could not mark job running: {e}")
        _hb_thread = _threading.Thread(
            target=_heartbeat_loop, args=(job_id, _hb_stop), daemon=True
        )
        _hb_thread.start()

    # Track post-success non-critical work so finally can run it after
    # the job is marked terminal (preventing it from overwriting 'completed').
    _post_success_market = None

    try:
        payload, persisted_at = _generate_picks_inner(market, job_id=job_id)

        # ── Mark job terminal ─────────────────────────────────────────────────
        if use_job:
            try:
                from services.postgres_store import (
                    mark_daily_picks_job_completed,
                    mark_daily_picks_job_failed,
                )
                now = datetime.now(timezone.utc)
                if persisted_at:
                    mark_daily_picks_job_completed(job_id, now, persisted_at)
                else:
                    mark_daily_picks_job_failed(
                        job_id, now,
                        "persistence_failed: Postgres picks save returned False",
                    )
            except Exception as e:
                log.warning(f"[picks] [{market}] Could not mark job terminal: {e}")

        # Phase 8 + Telegram require durable persistence — never run on a failed save
        if persisted_at is not None:
            _post_success_market = market
        return payload

    except Exception as e:
        _last_error[market] = traceback.format_exc()
        log.error(f"[picks] [{market}] generate_picks CRASHED: {e}\n{_last_error[market]}")

        if use_job:
            try:
                from services.postgres_store import mark_daily_picks_job_failed
                mark_daily_picks_job_failed(
                    job_id, datetime.now(timezone.utc),
                    _last_error[market] or str(e),
                )
            except Exception:
                pass

        # US Daily Picks incident (recurring: 07-15 through 07-22) — a
        # failed run used to overwrite BOTH the disk cache file AND the
        # Postgres `daily_picks_cache` "latest payload" row with this empty,
        # error-tagged stand-in, which get_cached_picks()/load_picks_from_db()
        # then served to every user as if it were today's picks: an
        # indefinite blank page, with the LAST GENUINELY SUCCESSFUL payload
        # silently shadowed (still in the table, just no longer "latest").
        # The failure is already durably recorded above via
        # mark_daily_picks_job_failed(job_id, ...) — daily_picks_jobs is the
        # single source of truth for "did today's attempt fail and why".
        # Deliberately NOT written to the disk cache file or
        # daily_picks_cache here: doing so is exactly the defect. The most
        # recent genuinely successful payload (if any) remains untouched and
        # keeps being served — by construction, since nothing failure-path
        # ever writes to either store again — with staleness/attempt-status
        # metadata layered on by the API (see api/routers/picks.py and
        # get_last_attempt_info() below), never silently presented as today's.
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "picks": {"short": [], "medium": [], "long": []},
            "error": str(e),
        }
        return payload

    finally:
        # Stop the heartbeat before any post-success non-critical work.
        _hb_stop.set()
        if _hb_thread is not None:
            _hb_thread.join(timeout=2.0)

        # ── Phase 8 + Telegram: non-critical, post-persistence ────────────────
        # Runs only on success (_post_success_market set).  Isolated here so
        # any exception cannot overwrite the already-written job terminal status.
        if _post_success_market:
            try:
                from services.alpha_engine.weight_adapter import run_adaptation
                _threading.Thread(
                    target=run_adaptation, args=(_post_success_market,), daemon=True
                ).start()
            except Exception as exc:
                log.info(f"[weight_adapter] Could not start: {exc}")
            # Market-aware since the US-notification fix: both IN and US
            # successes notify, each formatted for its own market. Still
            # non-critical — any exception is swallowed here so Telegram can
            # never affect the already-written job terminal status.
            try:
                from services.telegram_bot import send_picks_to_telegram
                send_picks_to_telegram(payload.get("picks", {}), market=_post_success_market)
            except Exception as exc:
                log.warning(f"[telegram] Error: {exc}")

            # Epic 007 Phase 3A — Intelligence Engine V1 shadow slice.
            # Off by default (INTELLIGENCE_ENGINE_SHADOW_ENABLED unset/"0"):
            # this block does not even import the module, so production
            # behavior is byte-for-byte unchanged. Runs only after the real
            # Daily Picks generation already succeeded and persisted above;
            # never affects published picks, the cache file, or job status —
            # any failure here is caught and logged, same isolation pattern
            # as the weight_adapter/telegram calls immediately above.
            if os.getenv("INTELLIGENCE_ENGINE_SHADOW_ENABLED") == "1":
                try:
                    from services.intelligence_engine.shadow_run import run_shadow_slice
                    # Epic 007 Phase 3B-B: derives real (if narrower-than-full-
                    # candidate-pool) market data for the Tradability/Liquidity/
                    # Confidence gates from `payload` — the exact dict this
                    # function already returns and persisted above. No new
                    # fetch, no production function's return contract changed;
                    # see candidate_data.py's own docstring for the full
                    # field-by-field honesty note and known coverage limit.
                    from services.intelligence_engine.candidate_data import build_candidate_market_data_from_payload
                    candidate_market_data = build_candidate_market_data_from_payload(payload)
                    _threading.Thread(
                        target=run_shadow_slice, args=(_post_success_market,),
                        kwargs={"job_id": job_id, "candidate_market_data": candidate_market_data},
                        daemon=True,
                    ).start()
                except Exception as exc:
                    log.warning(f"[intelligence_engine] Could not start shadow slice: {exc}")


# Module-level last error per market (exposed via /api/picks/status)
_last_error: dict[str, str | None] = {"IN": None, "US": None}

# Product Integrity Workstream #001B: the only prior way to tell "the
# scheduled trigger never reached the backend" apart from "it reached the
# backend but generate_picks() crashed before writing anything" was to
# compare generated_at against today's date — which proves SOMETHING
# didn't happen, but not which stage. This records the instant a valid
# POST /api/picks/generate request is accepted, before the background task
# runs, so a future incident can directly distinguish a missed/failed
# GitHub Actions trigger (this stays None or stale) from a trigger that
# reached the backend but never completed (this updates; generated_at
# doesn't). In-memory only, like _generating/_last_error above — does not
# survive a Railway restart; a durable, persisted version is a recommended
# future follow-up if this gap recurs.
_last_trigger_received_at: dict[str, str | None] = {"IN": None, "US": None}


def _generate_picks_inner(
    market: str = "IN", job_id: str | None = None
) -> tuple[dict, datetime | None]:
    """
    Run all mandatory Daily Picks phases (0–7) and persist the payload.

    Returns (payload, persisted_at) where persisted_at is a datetime when
    Postgres save succeeded or None when it failed.  Phase 8 (weight adaptation)
    and Telegram are intentionally NOT run here — they run in generate_picks()
    after the job is marked terminal so they cannot overwrite the job status.
    """
    from services.alpha_engine.ic_engine import get_production_ic_weights, shadow_ic_available
    from services.alpha_engine.meta_model import shadow_available as shadow_meta_model_available
    from services.alpha_engine.regime_cluster import detect_regime
    from services.alpha_engine.store import log_prediction
    from services.alpha_engine.containment import (
        is_production_learning_enabled,
        containment_reason,
        production_alpha_source,
        LEARNING_DATASET_VERSION,
    )
    from services.global_context import get_global_context
    from services.memory_guard import MemoryCircuitBreaker

    start = time.time()
    currency = _CURRENCY.get(market, "₹")
    # See services/memory_guard.py's module docstring for the production
    # incident this protects against. A no-op when no container memory
    # limit is visible (e.g. local/dev), so this never behaves differently
    # in an environment where thresholds can't be safely evaluated.
    _mem_guard = MemoryCircuitBreaker(market, job_id=job_id)
    # 2026-07-23/24 incident observability: the July 23 run was already at
    # ~75% of the container limit BEFORE any Phase 1 work (baseline drift in
    # the long-lived shared process). Record the starting point every run so
    # that drift is visible in healthy runs too, and — for the US run, whose
    # SEC/yfinance-heavy pipeline is the one that actually collides with the
    # ceiling — start from the lowest achievable baseline by clearing the
    # safe rebuildable caches and trimming the allocator up front.
    _mem_guard.observe("run_start")
    if market == "US":
        _mem_guard.release_memory("run_start")
        # Fail fast if cleanup couldn't bring the container back under the
        # abort threshold: without this, the next threshold evaluation is
        # check() at Phase-1 task 30 — i.e. 30 expensive prediction tasks
        # executed by a process already past its abort limit. Abort-only
        # (never re-runs the cleanup that just executed); no-op when no
        # container limit is visible.
        _mem_guard.enforce_abort_threshold("run_start_post_cleanup")

    # Learning Alpha Engine remediation, Phase 1: containment state is fixed
    # for the whole run — computed once, applied to every horizon below, and
    # persisted to daily_picks_jobs (best-effort) for durable observability.
    _production_learning_enabled = is_production_learning_enabled()
    _try_job_containment(
        job_id,
        production_alpha_source=production_alpha_source(),
        shadow_ic_available={h: shadow_ic_available(h, market=market) for h in ("short", "medium", "long")},
        shadow_meta_model_available={h: shadow_meta_model_available(h, market=market) for h in ("short", "medium", "long")},
        containment_reason=containment_reason(),
        learning_dataset_version=LEARNING_DATASET_VERSION,
    )

    # Phase 2A (alpha_observations) run identity: reuse the existing durable
    # Daily Picks job_id as run_id in production. When invoked without a
    # job_id (test/local context), generate exactly ONE fallback run_id here
    # and reuse it for every candidate and horizon in this run — never a
    # different run_id per row.
    _alpha_run_id = job_id or str(_uuid.uuid4())
    _alpha_run_generated_at = datetime.now(timezone.utc)
    _alpha_run_session_date = _market_local_date(_alpha_run_generated_at, market)

    # State: job is now executing — no work counts to report yet.
    # Outcome resolution for past predictions is NOT run here — it is owned
    # exclusively by the dedicated periodic _outcome_resolver_loop (api/main.py),
    # which already covers both markets on its own 6-hour schedule. Running it
    # inline here was a redundant, unbounded blocking call with no cap on
    # backlog size, provider calls, or elapsed time (Product Integrity #002J/#002K).
    _try_job_progress(job_id, "initializing", None, None)

    # ── Global crumb refresh — do this ONCE before bulk fetching ─────────────
    try:
        regime_ticker = "^NSEI" if market == "IN" else "^GSPC"
        if hasattr(yf.utils, "get_crumb"):
            yf.utils.get_crumb(force=True)
        else:
            yf.download(regime_ticker, period="1d", progress=False, auto_adjust=True)
        log.info(f"[picks] [{market}] Yahoo Finance session refreshed.")
    except Exception as e:
        log.warning(f"[picks] [{market}] Session refresh failed (non-fatal): {e}")

    # State: about to resolve the eligible stock universe — no candidate count yet.
    _try_job_progress(job_id, "universe_selection", None, None)

    # ── Phase 0b: Bulk screen the market's stock universe → top N momentum candidates ─
    # One yf.download() call for the eligible universe then rank by composite
    # momentum score. For US: uses _US_DAILY_PICKS_HEURISTIC_FILTERED intersection;
    # never falls back to the raw 12k universe. Falls back to anchor megacap
    # list (US) / Nifty 100 (IN) if all scoring fails.
    candidates, _phase0_universe_size, _universe_used, _universe_degraded, _screener_raw_count, _selection_meta = _bulk_screen(
        market, _N_CANDIDATES, job_id=job_id
    )
    log.info(f"[picks] [{market}] Starting deep prediction for {len(candidates)} candidates × 3 horizons …")
    # Phase 0b complete: record universe metadata and candidate count.
    # Release 12C: also persist the additive selection-observability fields —
    # written only here (Phase 0b), same as universe_used/universe_degraded,
    # so they reflect the actual universe decision, never a later phase's
    # unrelated task counts.
    _try_job_progress(
        job_id, "phase_0b_done", len(candidates), len(candidates),
        universe_used=_universe_used, universe_degraded=_universe_degraded,
        screener_raw_count=_screener_raw_count,
        universe_candidate_count=_selection_meta.get("universe_candidate_count"),
        universe_selection_attempts=_selection_meta.get("attempts"),
        universe_selection_reason=_selection_meta.get("reason"),
        universe_selection_error_category=_selection_meta.get("error_category"),
    )

    # ── Phase 2: Detect market regime (done once, shared across all stocks) ──
    try:
        global_ctx_proxy = get_global_context(_REGIME_PROXY.get(market, "RELIANCE"))
    except Exception:
        global_ctx_proxy = {}

    regime = detect_regime(global_ctx_proxy)
    regime_id    = regime["regime_id"]
    regime_label = regime["label"]
    log.info(f"[picks] [{market}] Regime: {regime_label} — {regime['description']}")

    # State: shortlist of N candidates known; deep-prediction about to begin.
    # No task count to report yet — phase_1 will report 0/total once tasks are built.
    _try_job_progress(job_id, "shortlist_ready", None, None)

    # ── Phase 1: Deep-predict candidates, chunked candidate-major ────────────
    # Sequential (no pools/gather) to avoid Yahoo Finance rate-limiting.
    #
    # Ordering history — this loop has now been restructured twice, in
    # opposite directions, and the second time was evidence-driven:
    #
    # - 2026-07-22: reordered candidate-major -> horizon-major so only one
    #   horizon's result pool (~397 rich dicts) was resident at a time
    #   instead of all three (~1,191). What that analysis missed: each
    #   result dict is only ~12 KB (~15 MB for all three horizons — never
    #   the dominant term), while the reorder made every symbol's SEC
    #   companyfacts lookup miss the 25-entry _facts_cache (the symbol
    #   comes around again ~400 tasks later, long since evicted) — tripling
    #   SEC downloads (4-8 MB JSON text) and parses (15-25 MB of dicts)
    #   from ~400 to ~1,200 per run.
    #
    # - 2026-07-23/24 incident (memory_guard aborts at 240/1188 and
    #   990/1197, 80% of the 8 GB container): root cause was baseline RSS
    #   ratchet from allocator fragmentation, which that tripled SEC churn
    #   feeds directly. Fix: chunked candidate-major — process candidates
    #   in chunks no larger than the SEC facts-cache capacity, and within a
    #   chunk run each symbol's three horizons consecutively, so
    #   companyfacts is fetched at most once per unique symbol per run
    #   (horizons 2 and 3 hit the warm cache). The three horizons' slim
    #   result pools are accumulated across chunks (the accepted ~15 MB),
    #   then Phases 3-6 below run per horizon exactly as before, releasing
    #   each horizon's pool after its own ranking/persistence completes.
    #
    # _predict_stock(symbol, horizon, market) remains a pure function of
    # its three arguments (its only shared state is the bounded, TTL'd
    # _pred_cache/_regime_cache in prediction_engine.py, order-independent),
    # so the SET of (symbol, horizon) calls and their arguments is
    # byte-identical to both previous orderings — this cannot change any
    # score, rank, selection, or published field. See
    # tests/regression/test_daily_picks_horizon_bounded_memory.py and
    # test_daily_picks_sec_fetch_reuse.py for the executable proofs.
    _phase1_task_total = len(candidates) * 3  # deep_prediction_candidates × 3 horizons
    # Threaded through from _get_universe_by_mcap via _bulk_screen's
    # _selection_meta — lets Phase 5 apply a per-horizon tier rule (tier
    # quota for medium/long, ignored entirely for short) without re-deriving
    # cap tiers from scratch or re-querying the cache mid-pipeline.
    _tier_map: dict[str, str] = _selection_meta.get("tier_map") or {}

    _try_job_progress(job_id, "phase_1", 0, _phase1_task_total)
    done = 0  # running total across all three horizons, for truthful phase_1 progress
    _mem_guard.observe("phase_1_start", candidates=len(candidates), task_total=_phase1_task_total)

    # ── Phase 1: chunked candidate-major scoring (see block comment above) ───
    # Chunk size is bounded by the SEC facts cache's own capacity so every
    # symbol's horizons 2/3 are guaranteed a warm-cache window regardless of
    # how many other symbols' facts the chunk touched in between.
    _chunk_size = _phase1_chunk_size()
    _horizon_items: dict[str, list] = {h: [] for h in ("short", "medium", "long")}
    for _chunk_start in range(0, len(candidates), _chunk_size):
        for sym in candidates[_chunk_start:_chunk_start + _chunk_size]:
            for horizon in ("short", "medium", "long"):
                r = _predict_stock(sym, horizon, market)
                done += 1
                if done % 30 == 0:
                    log.info(f"[picks] [{market}] {done}/{_phase1_task_total} done …")
                    _mem_guard.check("phase_1", done, _phase1_task_total)
                if r:
                    r["cap_tier"] = _tier_map.get(r["symbol"])
                    _horizon_items[horizon].append(r)
                _try_job_progress(job_id, "phase_1", done, _phase1_task_total)
                del r

    # ── Phases 3-6 per horizon ────────────────────────────────────────────────
    picks: dict[str, list] = {}
    alpha_engine_meta: dict[str, dict] = {}  # diagnostics for API
    _issuer_duplicates_suppressed = 0  # suppressed display entries across all horizons
    # DP-020 — cash/unallocated fraction per horizon (additive; new top-level
    # payload key, see `payload["portfolio_cash_pct"]` below). 1.0 - sum of
    # that horizon's published portfolio_weight values. Zero whenever the
    # cap permitted full investment (the previous, unchanged behaviour);
    # nonzero only when DPD-005's hard-cap contract left a shortfall
    # unallocated. Not populated for a horizon with 0 published picks.
    _portfolio_cash_pct: dict[str, float] = {}

    for horizon in ("short", "medium", "long"):
        # This horizon's Phase-1 pool, built by the chunked loop above.
        # pop() releases the accumulator's own reference, so once this
        # horizon's Phases 3-6 complete and `items` goes out of scope the
        # pool is collectable — the same per-horizon release the previous
        # structure had, just fed from the accumulator instead of scored
        # inline.
        items: list = _horizon_items.pop(horizon)
        # State: this horizon's ranking/selection about to begin. Checked
        # explicitly per horizon (not just once for the whole run) — this is
        # the exact phase boundary the 2026-07-21 incident's process was
        # killed at.
        _try_job_progress(job_id, "ranking", None, None)
        _mem_guard.check("ranking_entry", done, _phase1_task_total)

        # ── Score snapshots (section 4) — persist every scored stock for
        # history. Written per-horizon, right here, instead of once for all
        # three horizons up front (the 2026-07-21 memory-exhaustion
        # postmortem found the process killed shortly after entering
        # `ranking`, exactly when the old code held the FULL 3-horizon `raw`
        # dict alive during this write loop). Writing one horizon's
        # snapshots immediately after that horizon's slot in `raw` is
        # released keeps the peak retained memory here to one horizon's
        # pool (~400 entries) instead of all three (~1,200).
        _write_score_snapshots({horizon: items}, market)

        if not items:
            picks[horizon] = []
            continue

        # Phase 3 — IC weights (regime-adjusted). Containment-gated: returns
        # the fixed academic-prior weights while production learning is
        # disabled (the default) — see get_production_ic_weights's docstring.
        ic_weights = get_production_ic_weights(
            horizon,
            market=market,
            regime_multipliers=regime.get("weight_multipliers"),
        )
        log.info(
            f"[picks] [{market}] {horizon} IC weights "
            f"({production_alpha_source()}): {ic_weights}"
        )

        # Phase 4 — Z-score + alpha
        universe = _zscore_and_rank(
            items, ic_weights, regime, regime_id, market=market,
            production_learning_enabled=_production_learning_enabled,
        )
        ranked   = sorted(universe, key=lambda x: x.get("ranking_alpha", 0), reverse=True)

        # Quality gate (unchanged) → issuer dedup → per-horizon top-6 slice.
        # DP-025 foundation: _passes_quality_gate is now a module-level pure
        # function (see above _deduplicate_by_issuer) so an offline pipeline-
        # replay harness can call the exact same eligibility rule production
        # uses, instead of a second, potentially-diverging implementation.
        all_buy = [
            r for r in ranked
            if r.get("signal") == "BUY" and _passes_quality_gate(r, horizon)
        ]
        # Issuer-level dedup: prevent two share classes of the same company
        # from both appearing as separate Daily Picks opportunities.
        all_buy_deduped, _n_suppressed = _deduplicate_by_issuer(all_buy, market)
        _issuer_duplicates_suppressed += _n_suppressed

        if horizon == "short":
            # DP-025 foundation: see _select_short_term_top_six's own
            # docstring (module level, above) for the full rationale —
            # unchanged from before extraction.
            top_buy = _select_short_term_top_six(all_buy_deduped)
        else:
            # Medium/long-term: tier quota so the list can't collapse back to
            # all-large-cap even though large caps often score higher alpha
            # on average — the explicit reason this stratification exists.
            top_buy = _select_with_tier_quota(all_buy_deduped, _MEDIUM_LONG_TIER_QUOTA_6)

        # Conviction-gated publication policy
        # (feature/daily-picks-conviction-gated-publication): `top_buy` above
        # is the FULL existing selection (up to 6, already eligibility/BUY/
        # quality-gated and already in this horizon's deterministic ranking
        # order). It still feeds the `universe`-based alpha_observations
        # evidence trail below (every scored candidate gets a row,
        # regardless of `top_buy`/`published_buy` membership) — positions
        # 4-6 are never deleted, only not published. `published_buy` is the
        # subset of `top_buy`, in the same order, that also clears the
        # Model Conviction publication gate (see
        # _apply_conviction_publication_gate docstring): >=
        # DAILY_PICKS_PUBLICATION.MIN_CONVICTION_TO_PUBLISH (85.0), capped
        # at DAILY_PICKS_PUBLICATION.MAX_PUBLISHED_PER_HORIZON (3). This
        # never changes ranking, scoring, or BUY/HOLD/SELL — publication
        # only. `published_buy` (NOT `top_buy`) is what feeds portfolio
        # allocation, `picks[horizon]`, `_pick_meta_by_symbol`
        # (is_daily_pick/pick_rank/portfolio_weight), and Phase 7's
        # `log_prediction(is_daily_pick=True)` below — the published cohort
        # is identical across all four.
        published_buy, _publication_meta = _apply_conviction_publication_gate(top_buy)

        # Phase 6 — Portfolio optimisation. DP-025 foundation: the actual
        # weighting/cash math now lives in the module-level, I/O-free
        # _compute_portfolio_allocation so a pipeline replay can call it
        # directly with a frozen returns matrix instead of fetching one.
        # DP-021: _compute_portfolio_allocation is now the single allocation
        # authority for every non-empty slate size (previously a single
        # qualifying pick bypassed it with a separate 50% hard-code here).
        # Conviction-gated publication: allocation is computed over
        # `published_buy` (what /picks actually shows), not the full
        # up-to-6 `top_buy` — weights for candidates that didn't clear the
        # conviction gate would otherwise be meaningless to a reader who
        # never sees those rows. This is an allocation-of-what's-published
        # change only; it does not touch the ranking/alpha/selection logic
        # above (DPD-005's hard-cap contract, optimizer.optimize(), etc.).
        if published_buy:
            alphas = [r.get("ranking_alpha", 0) for r in published_buy]
            symbols = [r["symbol"] for r in published_buy]
            # A returns matrix is only useful (and only fetched) when there
            # are 2+ names to compute a covariance across — a lone pick's
            # allocation doesn't depend on it.
            ret_matrix = _fetch_returns_matrix(symbols, market) if len(published_buy) > 1 else None
            port_weights, cash_pct = _compute_portfolio_allocation(alphas, ret_matrix, regime_label)
            for pick, w in zip(published_buy, port_weights):
                pick["portfolio_weight"] = w
            _portfolio_cash_pct[horizon] = cash_pct

        # Final-pick selection metadata for the alpha_observations snapshot
        # below, kept OUT of the `top_buy`/`universe`/`published_buy` dicts
        # themselves — those dicts are serialized as-is into the published
        # payload (`picks[horizon] = published_buy`), so adding new keys to
        # them would change the published Daily Picks JSON. portfolio_weight
        # is already set directly on `pick` above (pre-existing behavior,
        # already part of today's payload) — only pick_rank/is_daily_pick
        # are net-new for this phase, and they stay in this side dict
        # instead. Built by `_build_published_pick_meta` (module-level, pure
        # — see its own docstring for why this MUST be `published_buy`, not
        # `top_buy`) so the exact same production logic is directly callable
        # from tests instead of being reimplemented there.
        _pick_meta_by_symbol = _build_published_pick_meta(published_buy)

        # Published-payload safety: `sentiment_available`/`quality_available`
        # (Phase 2A additions to _predict_stock's return dict, needed below
        # to build alpha_observations rows honestly) must NOT appear in the
        # published Daily Picks JSON — strip them from the copies assigned
        # to `picks[horizon]` only; `universe` (used for the snapshot below)
        # keeps the original dicts untouched.
        #
        # Conviction-gated publication: `picks[horizon]` (what /picks
        # actually returns) is now `published_buy` — the Model-Conviction-
        # gated, <=3 subset of `top_buy` — not the full up-to-6 selection.
        # `top_buy` (all positions, including any unpublished 4th-6th) still
        # feeds portfolio allocation above and the alpha_observations
        # evidence trail below, so no candidate evidence is deleted, only
        # not published.
        picks[horizon] = [
            {k: v for k, v in pick.items() if k not in _ALPHA_OBS_ONLY_KEYS}
            for pick in published_buy
        ]

        # ── Phase 2A: shadow-only canonical alpha_observations snapshot ──
        # Built from the COMPLETE enriched cross-sectional universe (every
        # successfully scored, non-rejected candidate — not just the Top-6
        # winners), after z-scoring, final selection, and portfolio
        # optimisation have all completed, so selection metadata is correct
        # at insert time. Persisted once per horizon in a single bulk call.
        # Failure here is logged and swallowed — it must never affect the
        # Daily Picks job lifecycle or the payload already computed above.
        try:
            _obs_rows = [
                row for row in (
                    _build_alpha_observation_row(
                        cand, run_id=_alpha_run_id, market=market, horizon=horizon,
                        run_generated_at=_alpha_run_generated_at,
                        run_session_date=_alpha_run_session_date,
                        regime_id=regime_id, regime_label=regime_label,
                        pick_meta=_pick_meta_by_symbol.get(cand.get("symbol")),
                    )
                    for cand in universe
                )
                if row is not None
            ]
            if _obs_rows:
                _ok = _alpha_obs.save_observations(_obs_rows)
                if not _ok:
                    log.warning(
                        f"[alpha_observations] [{market}] [{horizon}] save_observations "
                        f"reported failure for {len(_obs_rows)} rows (non-fatal, shadow-only)."
                    )
        except Exception as e:
            log.warning(
                f"[alpha_observations] [{market}] [{horizon}] snapshot persistence failed "
                f"(non-fatal, shadow-only): {e}"
            )
        alpha_engine_meta[horizon] = {
            "ic_weights":  ic_weights,
            "regime":      regime_label,
            # n_scored/n_buy keep their pre-existing meaning (unchanged):
            # total scored candidates and total BUY-signal candidates in
            # this horizon's cross-sectional universe. n_published (below)
            # is new and intentionally distinct — do not conflate them.
            "n_scored":    len(universe),
            "n_buy":       sum(1 for r in universe if r.get("signal") == "BUY"),
            # True only when the meta-model actually determined ranking for
            # at least one published pick — not merely that a shadow value
            # was computed. See "meta_alpha_used_for_ranking" per-item.
            "meta_model":  any(r.get("meta_alpha_used_for_ranking") for r in published_buy),
            # Learning Alpha Engine remediation, Phase 1 — observability.
            "production_alpha_source": production_alpha_source(),
            "shadow_meta_model_available": any(r.get("meta_alpha") is not None for r in top_buy),
            "containment_reason": containment_reason(),
            "learning_dataset_version": LEARNING_DATASET_VERSION,
            # Conviction-gated publication policy metadata (additive,
            # backward-compatible) — see _apply_conviction_publication_gate.
            # "confidence" is reused, unchanged, and here explicitly
            # relabeled: it is Model Conviction (a model output on a 0-100
            # scale), never a calibrated win probability or "% chance".
            #
            # DP-035 (2026-08-17): a fresh re-query of the walk-forward
            # backtest table (`val_signals`) found it tracks a different,
            # simplified proxy score than this field, and cannot currently
            # confirm a win-rate lift at this threshold for ANY horizon —
            # see thresholds.py's DAILY_PICKS_PUBLICATION docstring for the
            # full evidence. Stated honestly here rather than implying a
            # validated win-rate claim; the threshold/gate itself is
            # unchanged (no evidence supports a different number).
            #
            # DP-036 (2026-08-18): a real, full-population walk-forward
            # backtest (backend/scripts/conviction_gate_backtest.py, run
            # against the ACTUAL gate field `alpha_observations.signal_
            # confidence` — not val_signals.composite_score) is now
            # resolvable for SHORT horizon
            # specifically (medium is still thin; long has ~zero resolved
            # data until ~2026-10-13). Result: India <85 win rate 51.5% vs
            # >=85 win rate 52.4%; US <85 win rate 60.7% vs >=85 win rate
            # 60.8% (and the >=85 bucket's average realized return is
            # actually LOWER than <85's in the US). This is a definitive,
            # adequately-sampled negative finding for short horizon — not
            # "unconfirmed, not enough data yet" like medium/long — so
            # short horizon gets its own, stronger, distinguishable caveat
            # below. See DP-036 in the Daily-Picks Implementation Register
            # for the full evidence table. The 85.0 threshold and 3-per-
            # horizon cap are UNCHANGED for every horizon, including short:
            # `confidence` was already a load-bearing, authoritative field
            # before this gate existed (DP-034's quality-floor-at-25 and
            # short-horizon >80-priority-bucket precedent), so this is a
            # caveat-honesty fix, not a gate-removal — see DP-036's
            # decision-rationale section for why removing the gate was
            # considered and deliberately not done in this pass.
            "conviction_semantic": (
                "Model Conviction (0-100 scale, not a calibrated win probability). "
                "This threshold has been tested against realized outcomes for "
                "short-horizon picks across the full candidate population and "
                "shows no meaningful "
                "win-rate improvement over lower-conviction picks — publication "
                "filtering for this horizon is currently based on other quality "
                "gates, not a proven conviction advantage."
            ) if horizon == "short" else (
                "Model Conviction (0-100 scale, not a calibrated win probability). "
                "Win-rate correlation at this threshold is not yet confirmed by a "
                "matching backtest — monitor and revisit."
            ),
            **_publication_meta,
        }
        log.info(
            f"[picks] [{market}] {horizon}: {len(universe)} scored, "
            f"{alpha_engine_meta[horizon]['n_buy']} BUY, "
            f"{_publication_meta['n_conviction_qualified']} conviction-qualified (>= "
            f"{_publication_meta['conviction_threshold']}), "
            f"{len(published_buy)} published | "
            f"meta_model={'on' if alpha_engine_meta[horizon]['meta_model'] else 'off (IC alpha)'} | "
            f"alpha_source={alpha_engine_meta[horizon]['production_alpha_source']}"
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
                    _writer_source="daily_picks.phase7_final_pick",
                )
            except Exception as e:
                log.warning(f"[picks] [{market}] Log error for {pick['symbol']}: {e}")

    elapsed = round(time.time() - start, 1)
    total = sum(len(v) for v in picks.values())
    # _phase0_universe_size: the ACTUAL count of symbols passed to Phase-0
    # bulk screening for this run (post-eligibility-filter and post-screener-
    # intersection). Replaces the former `len(_UNIVERSE.get(market, []))` which
    # always returned the raw static-universe size (12,011 for US) regardless
    # of what the screener returned — the root cause of the UI showing
    # "screened from 12,079 US stocks" even when the screener narrowed it.
    log.info(f"[picks] [{market}] Done in {elapsed}s — {total} BUY picks found across "
          f"{len(candidates)} candidates from {_phase0_universe_size} stocks "
          f"(universe_used={_universe_used}, degraded={_universe_degraded}).")

    payload = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "market":            market,
        # source_job_id: durable base-job provenance for this payload. Equals
        # the daily_picks_jobs.job_id supplied to generate_picks() in
        # production endpoint-driven generation. In test/local generation
        # (no durable job_id — job_id param is None), left as None rather
        # than fabricated — never the Alpha Observation fallback run_id
        # (_alpha_run_id above), which exists for a different purpose and
        # is not a durable job-state row this field can be verified against.
        # Consumed by services.premarket_finalizer's fail-closed base
        # validation to confirm which specific base run a premarket
        # finalization is being applied to.
        "source_job_id":     job_id,
        "currency":          currency,
        "picks":             picks,
        "alpha_engine":      alpha_engine_meta,
        "regime":            {"label": regime_label, "description": regime["description"]},
        # DP-020 / DPD-005 (additive, new field): per-horizon cash/unallocated
        # fraction (0-1) left over after portfolio optimisation's hard
        # per-name cap. Zero (the previous, unchanged behaviour) whenever
        # the cap permitted full investment; only nonzero when too few
        # qualifying picks existed to invest 100% without exceeding the cap.
        # Keyed only by horizons that produced at least one published pick.
        "portfolio_cash_pct": _portfolio_cash_pct,
        # ── Legacy field — kept for backward compatibility ─────────────────────
        # Represents the size of the eligible universe entering Phase-0 bulk
        # screening (equivalent to universe_eligible_size below).  New callers
        # should prefer the explicit count fields added below.
        "screened_from":              _phase0_universe_size,
        # ── Truthful pipeline-count fields ─────────────────────────────────────
        # screener_raw_count: symbols returned by Yahoo screener before local
        #   eligibility filtering.  null when screener was not used (anchor or
        #   full-NSE fallback).  Never fabricated from a configured cap.
        "screener_raw_count":         _screener_raw_count,
        # universe_eligible_size: symbols remaining after local US heuristic
        #   eligibility filter (or raw screener count for IN); these are the
        #   symbols that entered Phase-0 bulk momentum scoring.
        "universe_eligible_size":     _phase0_universe_size,
        # universe_target_count: the INTENDED stratified-universe size for
        #   this market — lets the UI distinguish "we aimed for 400 and got
        #   400" from a degraded/truncated run. Same target for both markets
        #   since the large/mid/small stratification is symmetric now.
        "universe_target_count":      _TARGET_UNIVERSE_SIZE,
        # deep_prediction_candidates: symbols actually sent to full PredictionEngine.
        #   Equals min(universe_eligible_size, PICKS_CANDIDATES env-var cap).
        "deep_prediction_candidates": len(candidates),
        # phase_1_task_total: deep_prediction_candidates × evaluated horizons (3).
        "phase_1_task_total":         _phase1_task_total,
        # final_candidate_count: number of unique candidate objects that entered
        #   Phase-1 deep prediction.  Equals deep_prediction_candidates and
        #   payload["candidates"]; each symbol is counted once regardless of
        #   how many horizons it qualifies in.  Never derived from BUY-signal
        #   counts, per-horizon displayed picks, or issuer-dedup pass counts.
        "final_candidate_count":      len(candidates),
        # ── Universe metadata ──────────────────────────────────────────────────
        "universe_used":     _universe_used,   # "screener" | "anchor" | "full_universe"
        "universe_degraded": _universe_degraded,  # True when live screener not used (US)
        "candidates":        len(candidates),  # legacy alias for deep_prediction_candidates
        # ── Release 12C: explicit universe-selection observability ────────────
        # universe_candidate_count: size of the SELECTED source universe
        #   (screener result or fallback list) before Phase-0 momentum
        #   scoring truncates it to n_candidates. Equal to universe_eligible_size
        #   above — kept as its own explicitly-named field per Release 12C's
        #   contract so a task-count field (phase_task_total, below) can never
        #   be mistaken for universe breadth.
        "universe_candidate_count":         _selection_meta.get("universe_candidate_count"),
        # universe_selection_attempts: number of screener attempts actually made
        #   (IN only retries; always 1 for US today).
        "universe_selection_attempts":      _selection_meta.get("attempts"),
        # universe_selection_reason: stable, human-readable machine-safe reason
        #   — e.g. "healthy_screener_universe", "screener_rate_limit_exhausted",
        #   "screener_transient_failure_exhausted", "screener_insufficient_symbols",
        #   "screener_non_transient_error".
        "universe_selection_reason":        _selection_meta.get("reason"),
        # universe_selection_error_category: stable category, never a raw
        #   exception/stack trace — one of "rate_limited", "transient_upstream_error",
        #   "insufficient_symbols", "non_transient_error", "none".
        "universe_selection_error_category": _selection_meta.get("error_category"),
        # phase_task_processed / phase_task_total: explicitly the CURRENT
        #   phase's work-unit counts (deep_prediction_candidates × horizons at
        #   persist time, since Phase-1 is the last countable phase). Never
        #   the universe size — see universe_candidate_count above for that.
        "phase_task_processed":              _phase1_task_total,
        "phase_task_total":                  _phase1_task_total,
        # ── Issuer deduplication metadata ─────────────────────────────────────
        "issuer_dedup_applied":          True,
        # issuer_duplicates_suppressed: total display entries removed across all
        #   horizons because their issuer already appeared earlier in that
        #   horizon's ranked list.  Not the count of unique issuers suppressed.
        "issuer_duplicates_suppressed":  _issuer_duplicates_suppressed,
    }

    # State: payload fully constructed; about to write to durable storage.
    _try_job_progress(job_id, "persisting", None, None)
    _mem_guard.check("persisting")

    # Save to disk (best-effort — ephemeral on Render free tier)
    try:
        with open(_cache_file(market), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        log.warning(f"[picks] [{market}] Disk cache write failed: {e}")

    # Save to Postgres (survives redeploys)
    persisted_at = None
    if os.getenv("USE_POSTGRES") == "1":
        from services.postgres_store import save_picks_to_db
        if save_picks_to_db(payload, market=market):
            persisted_at = datetime.now(timezone.utc)
            log.info(f"[picks] [{market}] Saved to Postgres.")
        else:
            log.warning(f"[picks] [{market}] Postgres save returned False — picks not durably persisted.")

    # Invalidate the read-side TTL cache so the very next request sees this
    # payload rather than a stale cached one for up to _PICKS_CACHE_TTL_SECONDS.
    _invalidate_cached_picks(market)

    # Log-only memory provenance for the run: peak, ending, whether cleanup
    # ran, and malloc_trim availability/invocation (2026-07-23/24 incident).
    _mem_guard.log_summary()

    return payload, persisted_at


# Bounded, market-isolated, thread-safe TTL cache in front of get_cached_picks's
# Postgres read. Added 2026-08 (Supabase egress incident): /api/picks/daily and
# /api/picks/status had zero server-side cache, so every request — including
# frontend polling as often as every 15s — re-read the full picks JSONB blob
# from Postgres. See _invalidate_cached_picks, called immediately after a
# successful persist, for the primary consistency mechanism; the TTL below is
# only a safety net for any gap around that invalidation.
_PICKS_CACHE_TTL_SECONDS = 60
_picks_cache: dict[str, dict] = {}
_picks_cache_expiry: dict[str, float] = {}
_picks_cache_lock = threading.Lock()
_picks_cache_fetch_locks: dict[str, threading.Lock] = {
    "IN": threading.Lock(),
    "US": threading.Lock(),
}


# Lightweight status-metadata cache — separate from _picks_cache above
# because it holds a tiny dict (generated_at + premarket_* fields), not the
# full payload, and is populated by a different, much cheaper Postgres query
# (get_picks_status_metadata, a JSONB path-projection SELECT rather than a
# full-column read). Added 2026-08 (Supabase egress incident): GET
# /api/picks/status must never load the complete Daily Picks JSONB payload,
# even on a cold process cache — see picks_has_today_lightweight below.
_picks_metadata_cache: dict[str, dict] = {}
_picks_metadata_cache_expiry: dict[str, float] = {}
_picks_metadata_fetch_locks: dict[str, threading.Lock] = {
    "IN": threading.Lock(),
    "US": threading.Lock(),
}
_EMPTY_PICKS_METADATA = {
    "generated_at": None, "base_generated_at": None,
    "premarket_finalized_at": None, "premarket_status": None,
    "premarket_finalizer_version": None,
}


def _invalidate_cached_picks(market: str) -> None:
    """Drop the cached payload AND cached status-metadata for a market so
    the next read of either is durable."""
    with _picks_cache_lock:
        _picks_cache.pop(market, None)
        _picks_cache_expiry.pop(market, None)
        _picks_metadata_cache.pop(market, None)
        _picks_metadata_cache_expiry.pop(market, None)


def _fresh_cached_picks(market: str) -> dict | None:
    with _picks_cache_lock:
        cached = _picks_cache.get(market)
        expiry = _picks_cache_expiry.get(market, 0)
        if cached is not None and time.monotonic() < expiry:
            return cached
    return None


def get_cached_picks(market: str = "IN") -> dict | None:
    """
    Return today's picks for a market. Reads from Postgres first (survives
    Render redeploys), falls back to local disk cache.

    A short-TTL in-process cache sits in front of the durable read (see
    _PICKS_CACHE_TTL_SECONDS above) — concurrent callers during a cache miss
    share one in-flight fetch via a per-market lock rather than each issuing
    their own Postgres round-trip (no cache stampede).
    """
    hit = _fresh_cached_picks(market)
    if hit is not None:
        return hit

    fetch_lock = _picks_cache_fetch_locks.setdefault(market, threading.Lock())
    with fetch_lock:
        # Another thread may have populated the cache while we waited on the lock.
        hit = _fresh_cached_picks(market)
        if hit is not None:
            return hit

        data = None
        # Postgres first
        if os.getenv("USE_POSTGRES") == "1":
            try:
                from services.postgres_store import load_picks_from_db
                data = load_picks_from_db(market=market)
            except Exception as e:
                log.warning(f"[picks] [{market}] Postgres load failed, falling back to disk: {e}")

        # Disk fallback
        if data is None:
            try:
                with open(_cache_file(market)) as f:
                    data = json.load(f)
            except FileNotFoundError:
                data = None
            except Exception:
                data = None

        if data is not None:
            with _picks_cache_lock:
                _picks_cache[market] = data
                _picks_cache_expiry[market] = time.monotonic() + _PICKS_CACHE_TTL_SECONDS

        return data


def _fresh_cached_picks_metadata(market: str) -> dict | None:
    with _picks_cache_lock:
        cached = _picks_metadata_cache.get(market)
        expiry = _picks_metadata_cache_expiry.get(market, 0)
        if cached is not None and time.monotonic() < expiry:
            return cached
    return None


def get_picks_status_metadata(market: str = "IN") -> dict:
    """
    Lightweight status-only lookup for GET /api/picks/status — never loads
    the complete Daily Picks JSONB payload (all per-pick objects, factor
    z-scores, evidence text), even on a cold process cache. Added 2026-08
    (Supabase egress incident): /status previously called
    picks_generated_today() (a full-payload read via get_cached_picks) for
    every request, and for US additionally called get_cached_picks("US") a
    second time directly for premarket_* fields.

    Same stampede-safe per-market fetch-lock pattern as get_cached_picks.
    Always returns a dict (never None) — _EMPTY_PICKS_METADATA when nothing
    is cached/persisted yet, never fabricated non-None values.
    """
    hit = _fresh_cached_picks_metadata(market)
    if hit is not None:
        return hit

    fetch_lock = _picks_metadata_fetch_locks.setdefault(market, threading.Lock())
    with fetch_lock:
        hit = _fresh_cached_picks_metadata(market)
        if hit is not None:
            return hit

        meta = None
        if os.getenv("USE_POSTGRES") == "1":
            try:
                from services.postgres_store import get_picks_status_metadata as _pg_status_meta
                meta = _pg_status_meta(market)
            except Exception as e:
                log.warning(f"[picks] [{market}] Postgres status metadata load failed: {e}")

        if meta is None:
            # Local/disk-only dev fallback: this file read is not a durable
            # network egress concern the way the Postgres full-payload read
            # is, so it's fine to derive metadata from the full local
            # payload here rather than adding a second on-disk format.
            data = get_cached_picks(market)
            meta = _picks_metadata_from_payload(data) if data else dict(_EMPTY_PICKS_METADATA)

        with _picks_cache_lock:
            _picks_metadata_cache[market] = meta
            _picks_metadata_cache_expiry[market] = time.monotonic() + _PICKS_CACHE_TTL_SECONDS

        return meta


def _picks_metadata_from_payload(data: dict) -> dict:
    return {
        "generated_at": data.get("generated_at"),
        "base_generated_at": data.get("base_generated_at") or data.get("generated_at"),
        "premarket_finalized_at": data.get("premarket_finalized_at"),
        "premarket_status": data.get("premarket_status"),
        "premarket_finalizer_version": data.get("premarket_finalizer_version"),
    }


def _is_generated_today(generated_at_iso: str | None, market: str) -> bool:
    """Shared date-comparison core of picks_generated_today /
    picks_has_today_lightweight — own market's local trading-day date,
    IST for IN, DST-aware US/Eastern for US."""
    if not generated_at_iso:
        return False
    try:
        generated_at = datetime.fromisoformat(generated_at_iso.replace("Z", "+00:00"))
        return _market_local_date(generated_at, market) >= _market_local_date(datetime.now(timezone.utc), market)
    except Exception:
        return False


def picks_generated_today(market: str = "IN") -> bool:
    """Return True if a genuinely SUCCESSFUL generation exists for today
    (own market's local trading-day date). IN uses IST, US uses DST-aware
    US/Eastern.

    US Daily Picks generation-reliability incident (2026-07-22): this used
    to additionally require at least one non-empty picks bucket, to work
    around the old failure path saving an empty payload that would
    otherwise look like "today's picks" by date alone. That workaround is
    no longer correct now that get_cached_picks()/load_picks_from_db() only
    ever return a status='success' row — a genuine, legitimate zero-BUY day
    IS "today's picks" and must not be reported as ungenerated (which would
    cause the /generate endpoint and any watchdog/retry logic to treat a
    real, valid, completed outcome as a failure needing a retry). See
    save_picks_to_db/load_picks_from_db docstrings in postgres_store.py.
    """
    data = get_cached_picks(market)
    if not data:
        return False
    return _is_generated_today(data.get("generated_at"), market)


def picks_has_today_lightweight(market: str = "IN") -> bool:
    """Same semantics/result as picks_generated_today(), but backed by
    get_picks_status_metadata() — never loads the full payload. Used by
    GET /api/picks/status; picks_generated_today() itself is unchanged and
    still used by generation/catch-up callers that need the full payload
    anyway."""
    meta = get_picks_status_metadata(market)
    return _is_generated_today(meta.get("generated_at"), market)


# Bounded, safe error categories for public API exposure — never a raw
# traceback or provider payload (US Daily Picks generation-reliability
# incident, 2026-07-22, Phase 7 requirement). New categories may be added;
# an unmatched error always falls back to "unknown", never the raw text.
_ERROR_CATEGORY_PATTERNS: list[tuple[str, str]] = [
    ("memorylimiterror", "memory_limit_exceeded"),
    ("memory usage", "memory_limit_exceeded"),
    ("persistence_failed", "persistence_failed"),
    ("timeout", "provider_timeout"),
    ("timed out", "provider_timeout"),
    ("connectionerror", "provider_connection_error"),
    ("rate limit", "provider_rate_limited"),
    ("429", "provider_rate_limited"),
]


def _categorize_error(error_text: str | None) -> str | None:
    """Map a raw internal error string to a small, closed set of safe,
    public category names — never returns the raw text itself."""
    if not error_text:
        return None
    lowered = error_text.lower()
    for needle, category in _ERROR_CATEGORY_PATTERNS:
        if needle in lowered:
            return category
    return "unknown"


def get_generation_attempt_status(market: str = "IN", metadata_only: bool = False) -> dict:
    """
    Failure-safe publication contract (US Daily Picks generation-reliability
    incident, 2026-07-22) — combines the last known-good SUCCESSFUL payload
    (get_cached_picks, now status='success'-only by construction) with the
    latest durable attempt record (daily_picks_jobs, survives Railway
    restarts unlike the in-memory _last_error/_generating) into one bounded,
    public-safe summary. Used by both GET /api/picks/daily (to layer
    stale/attempt metadata onto the served payload) and GET
    /api/picks/status (Phase 7 observability fields).

    metadata_only=True (used by GET /api/picks/status — 2026-08 Supabase
    egress incident): sources generated_at from get_picks_status_metadata()
    instead of get_cached_picks(). Every field below is derived from
    generated_at alone, so this is a zero-behavior-change swap — GET
    /api/picks/daily still passes metadata_only=False (its default) since it
    already needs the full payload for the response body itself, at which
    point reusing that same read here costs nothing extra.

    Never raises — any lookup failure degrades to a field being None/False,
    never breaks the caller.
    """
    result: dict = {
        "has_today": False,
        "stale": False,
        "last_successful_session_date": None,
        "last_successful_generated_at": None,
        "last_attempt_status": None,
        "last_attempt_error_category": None,
        "last_attempt_started_at": None,
        "serving_stale_payload": False,
    }

    if metadata_only:
        meta = get_picks_status_metadata(market)
        generated_at_iso = meta.get("generated_at")
    else:
        data = get_cached_picks(market)
        generated_at_iso = data.get("generated_at") if data else None

    if generated_at_iso:
        try:
            generated_at = datetime.fromisoformat(generated_at_iso.replace("Z", "+00:00"))
            result["last_successful_generated_at"] = generated_at_iso
            result["last_successful_session_date"] = _market_local_date(generated_at, market).isoformat()
            today_local = _market_local_date(datetime.now(timezone.utc), market)
            is_today = _market_local_date(generated_at, market) >= today_local
            result["has_today"] = is_today
            result["stale"] = not is_today
            result["serving_stale_payload"] = not is_today
        except Exception:
            pass

    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import get_latest_daily_picks_job
            job = get_latest_daily_picks_job(market)
            if job:
                result["last_attempt_status"] = job.get("status")
                result["last_attempt_error_category"] = _categorize_error(job.get("last_error"))
                started_at = job.get("started_at")
                result["last_attempt_started_at"] = (
                    started_at.isoformat() if hasattr(started_at, "isoformat") else started_at
                )
        except Exception:
            pass

    return result


_MAX_DAILY_RECOVERY_ATTEMPTS = 3  # scheduled run + at most 2 governed recoveries per session date


def attempt_governed_recovery(market: str, reason: str) -> dict:
    """
    Bounded, safe, non-overlapping recovery trigger (US Daily Picks
    generation-reliability incident, 2026-07-22, Phase 6). Called from the
    US premarket finalizer's schedule when it finds today's base missing or
    stale — the finalizer already runs on a real cron well after the base's
    expected completion time, so it doubles as this watchdog's check point
    instead of requiring a brand-new scheduling mechanism.

    Reuses the EXACT SAME durable reservation path as POST
    /api/picks/generate (try_reserve_daily_picks_job_with_lease) — so
    duplicate-job protection, the Multibagger heavy-resource lease
    arbitration, and the (market) WHERE status IN ('queued','running')
    partial unique index all apply identically; this can never overlap an
    existing active job. Bounded to _MAX_DAILY_RECOVERY_ATTEMPTS total job
    rows per market per session date (scheduled run + governed retries) —
    never retries indefinitely. Never runs generation synchronously —
    launches the same background-thread pattern the HTTP endpoint uses, so
    the caller (the finalizer's async handler) returns immediately.

    Returns a dict describing exactly what happened — never raises.
    """
    if os.getenv("USE_POSTGRES") != "1":
        return {"triggered": False, "reason": "durable_job_state_unavailable"}

    if picks_generated_today(market):
        return {"triggered": False, "reason": "already_fresh"}

    # Daily Picks Scheduler & Completion Reliability Hardening, follow-up
    # correction (2026-08-10): an already_running response is only useful to
    # a caller that needs to MONITOR the active run (e.g. the India
    # watchdog) if it carries the exact durable job_id — never a fabricated
    # one. Both detection paths below (the fast in-process flag and the
    # durable DB lookup) now attach the real job_id from
    # get_active_daily_picks_job(), the same authoritative source
    # /api/picks/status already uses. If the in-memory flag claims a job is
    # running but no corresponding durable active job can be found, that is
    # a genuine state inconsistency (e.g. a crashed process that never
    # cleared its local flag) — this is reported honestly via the existing
    # "precheck_failed" classification (the vocabulary this function
    # already uses for "could not safely determine what to do") rather than
    # inventing a new reason or fabricating a job_id.
    from services.postgres_store import (
        get_active_daily_picks_job,
        count_daily_picks_job_attempts_since,
    )

    with _generating_lock:
        in_memory_running = _generating.get(market, False)
    if in_memory_running:
        try:
            active = get_active_daily_picks_job(market)
        except Exception as e:
            log.warning(f"[picks] [{market}] [watchdog] durable active-job lookup failed: {e}")
            return {"triggered": False, "reason": "precheck_failed"}
        if active and active.get("job_id"):
            return {"triggered": False, "reason": "already_running", "job_id": active["job_id"]}
        log.warning(
            f"[picks] [{market}] [watchdog] in-memory generating flag is set but no durable "
            f"active job was found — state inconsistency, not reporting a fabricated job_id"
        )
        return {"triggered": False, "reason": "precheck_failed"}

    try:
        active = get_active_daily_picks_job(market)
        if active is not None:
            return {"triggered": False, "reason": "already_running", "job_id": active.get("job_id")}

        today_local_midnight_utc = datetime.combine(
            _market_local_date(datetime.now(timezone.utc), market),
            datetime.min.time(),
        ).replace(tzinfo=timezone.utc)
        attempts_today = count_daily_picks_job_attempts_since(market, today_local_midnight_utc)
        if attempts_today >= _MAX_DAILY_RECOVERY_ATTEMPTS:
            log.error(
                f"[picks] [{market}] [watchdog] recovery NOT attempted — "
                f"{attempts_today} attempts already recorded for today's session "
                f"(max {_MAX_DAILY_RECOVERY_ATTEMPTS}). trigger_reason={reason}"
            )
            return {"triggered": False, "reason": "max_attempts_reached", "attempts_today": attempts_today}
    except Exception as e:
        log.warning(f"[picks] [{market}] [watchdog] recovery precheck failed: {e}")
        return {"triggered": False, "reason": "precheck_failed"}

    job_id = str(_uuid.uuid4())
    _HEAVY_RESOURCE = {"IN": "IN_SCREENER_HEAVY", "US": "US_YFINANCE_HEAVY"}[market]
    try:
        from services.postgres_store import try_reserve_daily_picks_job_with_lease
        outcome = try_reserve_daily_picks_job_with_lease(job_id, market, _RUNNER_ID, _HEAVY_RESOURCE)
    except Exception as e:
        log.warning(f"[picks] [{market}] [watchdog] recovery reservation failed: {e}")
        return {"triggered": False, "reason": "reservation_failed"}

    if outcome != "reserved":
        return {"triggered": False, "reason": outcome}

    log.error(  # error level — this IS the "missed scheduled trigger" alert Phase 7 asks for
        f"[picks] [{market}] [watchdog] scheduled generation missing/stale "
        f"(reason={reason}) — starting governed recovery job_id={job_id}"
    )

    with _generating_lock:
        _generating[market] = True

    def _run():
        try:
            generate_picks(market, job_id=job_id)
        finally:
            with _generating_lock:
                _generating[market] = False
            try:
                from services.postgres_store import release_heavy_workload_lease
                release_heavy_workload_lease(job_id)
            except Exception:
                pass

    _threading.Thread(target=_run, daemon=True).start()
    return {"triggered": True, "job_id": job_id, "reason": reason}


# Unique identifier for this process instance.  Used as runner_instance_id in the
# daily_picks_jobs table so a newly-started Railway process can see which rows
# belong to a previous (now-dead) process vs. itself.
import threading as _threading
_RUNNER_ID: str = str(_uuid.uuid4())


def _heartbeat_loop(job_id: str, stop_event: _threading.Event) -> None:
    """
    Daemon thread: write last_runner_heartbeat_at every 30 seconds until
    stop_event is set.  Uses Event.wait() so it exits promptly on shutdown
    rather than sleeping a full interval.  Never changes job status.
    """
    while not stop_event.wait(30):
        try:
            from services.postgres_store import record_daily_picks_job_heartbeat
            record_daily_picks_job_heartbeat(job_id, datetime.now(timezone.utc))
        except Exception:
            pass  # heartbeat failure must never affect generation


def _try_job_progress(
    job_id: str | None,
    phase: str,
    processed: int | None,
    total: int | None,
    **kwargs,
) -> None:
    """Best-effort progress write; silently swallows all errors.

    processed and total may be None for state-only phases (initializing,
    universe_selection, shortlist_ready, ranking, persisting) where no
    countable workload exists yet.  The DB column is INTEGER (nullable).
    """
    if not job_id or os.getenv("USE_POSTGRES") != "1":
        return
    try:
        from services.postgres_store import record_daily_picks_job_progress
        record_daily_picks_job_progress(
            job_id, phase, processed, total, **kwargs
        )
    except Exception:
        pass


def _try_job_containment(job_id: str | None, **fields) -> None:
    """Best-effort persistence of Learning Alpha Engine remediation, Phase 1
    observability fields (production_alpha_source, shadow_ic_available,
    shadow_meta_model_available, containment_reason, learning_dataset_version)
    onto the durable daily_picks_jobs row. Silently swallows all errors —
    never allowed to affect the Daily Picks job lifecycle or payload, same
    isolation pattern as _try_job_progress above. No-op without a durable
    job_id (e.g. test/local calls to _generate_picks_inner directly)."""
    if not job_id or os.getenv("USE_POSTGRES") != "1":
        return
    try:
        from services.postgres_store import record_daily_picks_job_containment
        record_daily_picks_job_containment(job_id, **fields)
    except Exception:
        pass


# Guard to prevent concurrent generation runs (module-level, shared across threads,
# keyed by market so an IN run and a US run can't trip each other's flag).
# Lock makes the check-then-set atomic — plain bool had a TOCTOU race where two
# concurrent POST /picks/generate requests both passed the guard simultaneously.
_generating: dict[str, bool] = {"IN": False, "US": False}
_generating_lock = _threading.Lock()
