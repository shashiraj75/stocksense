"""
Unsupervised regime detection using KMeans on live market features.

4 regime clusters:
  0  BULL_CALM       — low VIX, market trending up: momentum/technicals pay
  1  BULL_VOLATILE   — moderate VIX, market up but choppy: quality + RS key
  2  BEAR_CALM       — elevated VIX, market grinding down: FCF/dividend defence
  3  BEAR_PANIC      — high VIX, sharp sell-off: capital preservation only

Features (5-dim, all normalised to ~0-1):
  vix_norm, market_trend_norm, dxy_norm, rates_norm, local_trend_norm
"""

import os
import pickle
import threading
import time

import numpy as np

MODEL_PATH = os.path.join(os.path.dirname(__file__), "regime_kmeans.pkl")
_lock = threading.Lock()
_cache: dict | None = None
_cache_expiry: float = 0
CACHE_TTL = 3600  # 1 hour — regime doesn't change minute-to-minute

REGIME_LABELS = {
    0: "BULL_CALM",
    1: "BULL_VOLATILE",
    2: "BEAR_CALM",
    3: "BEAR_PANIC",
}

REGIME_DESCRIPTIONS = {
    "BULL_CALM":     "Bull market, low volatility — momentum and quality outperform",
    "BULL_VOLATILE": "Bull market, elevated volatility — sector rotation; relative strength key",
    "BEAR_CALM":     "Bear market, slow grind down — defensive; favour FCF yield and low beta",
    "BEAR_PANIC":    "Bear market, panic selling — capital preservation; very selective, tight stops",
}

# How each regime shifts the alpha weight for each factor
REGIME_WEIGHT_MULTIPLIERS: dict[str, dict[str, float]] = {
    "BULL_CALM":     {"tech": 1.3, "fund": 0.9, "sentiment": 1.1, "quality": 1.0},
    "BULL_VOLATILE": {"tech": 1.0, "fund": 1.0, "sentiment": 0.9, "quality": 1.2},
    "BEAR_CALM":     {"tech": 0.7, "fund": 1.2, "sentiment": 0.8, "quality": 1.4},
    "BEAR_PANIC":    {"tech": 0.5, "fund": 1.1, "sentiment": 0.6, "quality": 1.6},
}


def extract_features(global_ctx: dict) -> np.ndarray:
    """Convert global context dict → 5-dim normalised feature vector."""
    levels  = global_ctx.get("levels", {})
    changes = global_ctx.get("changes", {})

    vix       = float(levels.get("vix") or 18.0)
    sp500_chg = float(changes.get("sp500") or 0.0)
    dxy       = float(levels.get("dxy") or 100.0)
    us10y     = float(levels.get("us10y") or 4.0)
    nifty_chg = float(changes.get("nifty50") or 0.0)

    return np.array([
        min(1.0, vix / 40.0),            # VIX: 0=calm, 1=extreme fear
        (sp500_chg + 5.0) / 10.0,        # S&P trend: 0=down 5%, 1=up 5%
        (dxy - 90.0) / 20.0,             # DXY: 0=90, 1=110
        (us10y - 2.0) / 5.0,             # 10Y yield: 0=2%, 1=7%
        (nifty_chg + 5.0) / 10.0,        # Nifty trend: 0=down 5%, 1=up 5%
    ], dtype=float)


def _load_or_init_model():
    try:
        from sklearn.cluster import KMeans
    except ImportError:
        return None

    if os.path.exists(MODEL_PATH):
        try:
            with open(MODEL_PATH, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass

    # Bootstrap with hand-crafted centroids for the 4 regimes
    # Each row: [vix_norm, sp500_chg_norm, dxy_norm, rates_norm, nifty_norm]
    init_centroids = np.array([
        [0.33, 0.65, 0.50, 0.40, 0.65],  # 0: BULL_CALM (VIX~13, market +3%)
        [0.50, 0.55, 0.55, 0.50, 0.55],  # 1: BULL_VOLATILE (VIX~20, market +1%)
        [0.58, 0.40, 0.60, 0.55, 0.40],  # 2: BEAR_CALM (VIX~23, market -1%)
        [0.80, 0.20, 0.65, 0.60, 0.20],  # 3: BEAR_PANIC (VIX~32, market -3%)
    ])
    km = KMeans(n_clusters=4, init=init_centroids, n_init=1, random_state=42)
    km.fit(init_centroids)
    _save_model(km)
    return km


def _save_model(model):
    try:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
    except Exception:
        pass


def detect_regime(global_ctx: dict) -> dict:
    """
    Classify current market into one of 4 regimes.
    Caches result for 1 hour (regime doesn't flip intraday).
    """
    global _cache, _cache_expiry

    now = time.time()
    with _lock:
        if _cache and now < _cache_expiry:
            return _cache

    features = extract_features(global_ctx)

    try:
        model = _load_or_init_model()
        regime_id = int(model.predict(features.reshape(1, -1))[0]) if model else 0
    except Exception:
        regime_id = 0

    label = REGIME_LABELS.get(regime_id, "BULL_CALM")

    # Log this snapshot for future KMeans retraining
    try:
        from services.alpha_engine.store import log_regime
        log_regime(regime_id, label, features.tolist())
    except Exception:
        pass

    result = {
        "regime_id":          regime_id,
        "label":              label,
        "description":        REGIME_DESCRIPTIONS[label],
        "weight_multipliers": REGIME_WEIGHT_MULTIPLIERS[label],
        "features":           features.tolist(),
    }

    with _lock:
        _cache = result
        _cache_expiry = now + CACHE_TTL

    return result


def retrain_on_history():
    """
    Retrain KMeans on all stored regime feature snapshots.
    Called by the weight_adapter after enough history accumulates.
    """
    try:
        from sklearn.cluster import KMeans
        from services.alpha_engine.store import get_regime_history

        history = get_regime_history()
        if len(history) < 30:
            print(f"[regime] Not enough history to retrain ({len(history)} snapshots)")
            return

        X = np.array(history)
        km = KMeans(n_clusters=4, n_init=10, random_state=42)
        km.fit(X)
        _save_model(km)

        # Invalidate cache so next call uses new model
        global _cache, _cache_expiry
        with _lock:
            _cache = None
            _cache_expiry = 0

        print(f"[regime] KMeans retrained on {len(X)} historical snapshots")
    except Exception as e:
        print(f"[regime] Retrain error: {e}")
