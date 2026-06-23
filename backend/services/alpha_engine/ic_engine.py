"""
Information Coefficient (IC) Engine.

IC_k = Pearson correlation(factor_k_zscore_at_t, actual_forward_return_at_t+h)

High IC (>0.05) = factor genuinely predicts forward returns.
IC near 0      = factor adds no information.
Negative IC    = contrarian signal.

Before we have 60+ real outcome pairs, we use academic prior ICs from
published factor research — separately for IN and US, since the two
markets have different fundamentals distributions, liquidity, and analyst
coverage, and there's no reason to assume one market's learned weights
transfer to the other (IN priors are Nifty 100 research; US priors are
broad large-cap research — see ACADEMIC_PRIOR_IC below for sources).

Factor weights used in _zscore_universe are derived as:
    weight_k = max(0, IC_k) / Σ max(0, IC_j)

This is IC-proportional allocation — factors with zero or negative IC
get no weight, preventing noise from diluting the signal.
"""

import threading
import time

_lock = threading.Lock()
_cache: dict = {}
_cache_expiry: dict[str, float] = {}
CACHE_TTL = 3600  # recompute IC at most once per hour

MIN_REAL_DATA_ROWS = 60  # minimum outcome pairs before trusting live IC

# Academic prior ICs, kept separate per market — see module docstring.
ACADEMIC_PRIOR_IC: dict[str, dict[str, dict[str, float]]] = {
    "IN": {
        # Sources: Novy-Marx (2013), Fama-French (2015), AQR India research.
        # Short-term: momentum dominant. Long-term: quality/fundamentals dominant.
        "short": {
            "tech":      0.055,   # price momentum strong over 1-5 days
            "fund":      0.018,   # fundamentals slow-moving; weak short IC
            "sentiment": 0.042,   # news sentiment has short-lived but real alpha
            "quality":   0.032,   # quality matters but doesn't manifest immediately
        },
        "medium": {
            "tech":      0.038,
            "fund":      0.048,
            "sentiment": 0.028,
            "quality":   0.058,
        },
        "long": {
            "tech":      0.018,   # momentum mean-reverts; low long IC
            "fund":      0.068,   # fundamentals dominant at 3-6M horizon
            "sentiment": 0.012,
            "quality":   0.072,   # quality (ROIC, FCF) best long-run predictor
        },
    },
    "US": {
        # Sources: Jegadeesh & Titman (1993) momentum, Fama-French 5-factor
        # (2015), AQR Quality-Minus-Junk (Asness/Frazzini/Pedersen 2019).
        # US large-caps are more heavily analyst-covered and more efficiently
        # priced than Indian mid/small-caps, so sentiment IC is somewhat
        # lower here — news is absorbed into price faster. Momentum and
        # quality are both very well-documented anomalies in US equities.
        "short": {
            "tech":      0.052,   # momentum well-documented, slightly weaker than IN small/mid-caps
            "fund":      0.015,
            "sentiment": 0.030,   # faster absorption into price than IN — lower short sentiment IC
            "quality":   0.030,
        },
        "medium": {
            "tech":      0.035,
            "fund":      0.045,
            "sentiment": 0.022,
            "quality":   0.060,   # QMJ factor especially robust in US large-caps
        },
        "long": {
            "tech":      0.015,   # momentum mean-reversion well documented (Jegadeesh-Titman)
            "fund":      0.065,
            "sentiment": 0.010,
            "quality":   0.078,   # Fama-French quality/profitability factor strongest long-run
        },
    },
}


def _compute_live_ic(horizon: str, market: str) -> dict[str, float] | None:
    """
    Compute IC from logged predictions + actual outcomes, for one market.
    Returns None if fewer than MIN_REAL_DATA_ROWS pairs are available.
    """
    try:
        import numpy as np
        from services.alpha_engine.store import get_training_data

        data = get_training_data(horizon, market=market)
        if len(data) < MIN_REAL_DATA_ROWS:
            return None

        priors = ACADEMIC_PRIOR_IC[market][horizon]
        factor_map = [
            ("tech_z",      "tech"),
            ("fund_z",      "fund"),
            ("sentiment_z", "sentiment"),
            ("quality_z",   "quality"),
        ]
        ic_dict: dict[str, float] = {}

        for col, name in factor_map:
            pairs = [(r[col], r["fwd_return"])
                     for r in data if r.get(col) is not None and r.get("fwd_return") is not None]
            if len(pairs) < 30:
                ic_dict[name] = priors[name]
                continue

            vals, rets = zip(*pairs)
            arr_v = np.array(vals, dtype=float)
            arr_r = np.array(rets, dtype=float)
            live_ic = float(np.corrcoef(arr_v, arr_r)[0, 1])
            if np.isnan(live_ic):
                # Zero-variance factor in this sample (all z-scores identical)
                # — fall back to the prior rather than propagate NaN into
                # weighting downstream.
                ic_dict[name] = priors[name]
                continue

            # Bayesian shrinkage: blend live IC with prior as data grows
            # At 60 rows → 0% live weight; at 260 rows → 100% live weight
            n = len(pairs)
            live_weight = min(1.0, (n - MIN_REAL_DATA_ROWS) / 200)
            prior = priors[name]
            ic_dict[name] = round(live_weight * live_ic + (1 - live_weight) * prior, 4)

        return ic_dict

    except Exception as e:
        print(f"[ic_engine] Error computing live IC ({market}): {e}")
        return None


def get_ic_weights(horizon: str, market: str = "IN",
                    regime_multipliers: dict[str, float] | None = None) -> dict[str, float]:
    """
    Return IC-proportional factor weights for the given horizon and market.

    If regime_multipliers is provided (from regime_cluster.detect_regime),
    the weights are further adjusted: BULL regime boosts tech/sentiment;
    BEAR regime boosts quality/fundamentals.

    weight_k = max(0, IC_k × regime_mult_k) / Σ(...)
    """
    global _cache, _cache_expiry

    cache_key = f"{market}:{horizon}:{','.join(f'{k}:{v}' for k,v in sorted((regime_multipliers or {}).items()))}"
    now = time.time()

    with _lock:
        if _cache.get(cache_key) and now < _cache_expiry.get(cache_key, 0):
            return _cache[cache_key]

    ic = _compute_live_ic(horizon, market) or ACADEMIC_PRIOR_IC[market][horizon]

    # Apply regime multipliers (do NOT let them push a factor negative)
    if regime_multipliers:
        ic = {k: max(0.0, v * regime_multipliers.get(k, 1.0)) for k, v in ic.items()}

    # Normalise to sum-to-1 positive weights
    raw = {k: max(0.0, v) for k, v in ic.items()}
    total = sum(raw.values()) or 1.0
    weights = {k: round(v / total, 4) for k, v in raw.items()}

    with _lock:
        _cache[cache_key] = weights
        _cache[f"ic_{market}_{horizon}"] = ic
        _cache_expiry[cache_key] = now + CACHE_TTL

    return weights


def get_ic_values(horizon: str, market: str = "IN") -> dict[str, float]:
    """Return raw IC values (not weights) for display and diagnostics."""
    get_ic_weights(horizon, market)  # populates cache
    with _lock:
        return dict(_cache.get(f"ic_{market}_{horizon}", ACADEMIC_PRIOR_IC[market][horizon]))


def invalidate_cache():
    """Force IC recomputation on next call (called after new outcomes are logged)."""
    global _cache, _cache_expiry
    with _lock:
        _cache = {}
        _cache_expiry = {}
