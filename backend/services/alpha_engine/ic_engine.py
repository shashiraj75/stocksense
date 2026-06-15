"""
Information Coefficient (IC) Engine.

IC_k = Pearson correlation(factor_k_zscore_at_t, actual_forward_return_at_t+h)

High IC (>0.05) = factor genuinely predicts forward returns.
IC near 0      = factor adds no information.
Negative IC    = contrarian signal.

Before we have 60+ real outcome pairs, we use academic prior ICs from
published factor research on Indian large-cap equities (Nifty 100 universe).

Factor weights used in _zscore_universe are derived as:
    weight_k = max(0, IC_k) / Σ max(0, IC_j)

This is IC-proportional allocation — factors with zero or negative IC
get no weight, preventing noise from diluting the signal.
"""

import threading
import time

_lock = threading.Lock()
_cache: dict = {}
_cache_expiry: float = 0
CACHE_TTL = 3600  # recompute IC at most once per hour

MIN_REAL_DATA_ROWS = 60  # minimum outcome pairs before trusting live IC

# Academic prior ICs for Indian large-cap (Nifty 100) universe.
# Sources: Novy-Marx (2013), Fama-French (2015), AQR India research.
# Short-term: momentum dominant. Long-term: quality/fundamentals dominant.
ACADEMIC_PRIOR_IC: dict[str, dict[str, float]] = {
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
}


def _compute_live_ic(horizon: str) -> dict[str, float] | None:
    """
    Compute IC from logged predictions + actual outcomes.
    Returns None if fewer than MIN_REAL_DATA_ROWS pairs are available.
    """
    try:
        import numpy as np
        from services.alpha_engine.store import get_training_data

        data = get_training_data(horizon)
        if len(data) < MIN_REAL_DATA_ROWS:
            return None

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
                ic_dict[name] = ACADEMIC_PRIOR_IC[horizon][name]
                continue

            vals, rets = zip(*pairs)
            arr_v = np.array(vals, dtype=float)
            arr_r = np.array(rets, dtype=float)
            live_ic = float(np.corrcoef(arr_v, arr_r)[0, 1])

            # Bayesian shrinkage: blend live IC with prior as data grows
            # At 60 rows → 0% live weight; at 260 rows → 100% live weight
            n = len(pairs)
            live_weight = min(1.0, (n - MIN_REAL_DATA_ROWS) / 200)
            prior = ACADEMIC_PRIOR_IC[horizon][name]
            ic_dict[name] = round(live_weight * live_ic + (1 - live_weight) * prior, 4)

        return ic_dict

    except Exception as e:
        print(f"[ic_engine] Error computing live IC: {e}")
        return None


def get_ic_weights(horizon: str, regime_multipliers: dict[str, float] | None = None) -> dict[str, float]:
    """
    Return IC-proportional factor weights for the given horizon.

    If regime_multipliers is provided (from regime_cluster.detect_regime),
    the weights are further adjusted: BULL regime boosts tech/sentiment;
    BEAR regime boosts quality/fundamentals.

    weight_k = max(0, IC_k × regime_mult_k) / Σ(...)
    """
    global _cache, _cache_expiry

    cache_key = f"{horizon}:{','.join(f'{k}:{v}' for k,v in sorted((regime_multipliers or {}).items()))}"
    now = time.time()

    with _lock:
        if _cache.get(cache_key) and now < _cache_expiry:
            return _cache[cache_key]

    ic = _compute_live_ic(horizon) or ACADEMIC_PRIOR_IC[horizon]

    # Apply regime multipliers (do NOT let them push a factor negative)
    if regime_multipliers:
        ic = {k: max(0.0, v * regime_multipliers.get(k, 1.0)) for k, v in ic.items()}

    # Normalise to sum-to-1 positive weights
    raw = {k: max(0.0, v) for k, v in ic.items()}
    total = sum(raw.values()) or 1.0
    weights = {k: round(v / total, 4) for k, v in raw.items()}

    with _lock:
        _cache[cache_key] = weights
        _cache[f"ic_{horizon}"] = ic
        _cache_expiry = now + CACHE_TTL

    return weights


def get_ic_values(horizon: str) -> dict[str, float]:
    """Return raw IC values (not weights) for display and diagnostics."""
    get_ic_weights(horizon)  # populates cache
    with _lock:
        return dict(_cache.get(f"ic_{horizon}", ACADEMIC_PRIOR_IC[horizon]))


def invalidate_cache():
    """Force IC recomputation on next call (called after new outcomes are logged)."""
    global _cache, _cache_expiry
    with _lock:
        _cache = {}
        _cache_expiry = 0
