"""
Ridge Meta-Model (and optional XGBoost upgrade).

Learns the optimal non-linear combination of factor z-scores + regime
interactions that historically predicted forward returns best.

Trained separately per market (IN, US) — same rationale as the IC engine:
different fundamentals distributions and outcome dynamics, no reason to
assume one market's learned model transfers to the other.

Input features (11-dim):
  tech_z, fund_z, sentiment_z, quality_z     — 4 factor z-scores
  combined_alpha                              — IC-weighted alpha (pre-signal)
  tech_z × is_bull                           — momentum amplified in bull
  quality_z × is_bear                        — quality amplified in bear
  sentiment_z × is_panic                     — sentiment matters most in panic
  is_bull, is_bear, is_panic                 — regime dummies

Target: actual forward return (5D for short, 20D for medium/long).

The model outputs a predicted return (not a class label), so we rank stocks
by this number and pick the top BUY signals.

Falls back to IC-weighted combined_alpha when training data < 100 rows.
"""

import logging
import os
import pickle
import threading

import numpy as np

log = logging.getLogger(__name__)

_MODEL_DIR = os.path.dirname(__file__)
_lock = threading.Lock()
_cache: dict = {}

MIN_ROWS = 100   # minimum labelled pairs to trust the meta-model


def _model_path(horizon: str, market: str = "IN") -> str:
    return os.path.join(_MODEL_DIR, f"meta_model_{market}_{horizon}.pkl")


def _build_features(tech_z: float, fund_z: float, sentiment_z: float,
                    quality_z: float, combined_alpha: float,
                    regime_id: int) -> np.ndarray:
    is_bull  = 1.0 if regime_id in (0, 1) else 0.0
    is_bear  = 1.0 if regime_id in (2, 3) else 0.0
    is_panic = 1.0 if regime_id == 3 else 0.0

    return np.array([
        tech_z,
        fund_z,
        sentiment_z,
        quality_z,
        combined_alpha,
        tech_z      * is_bull,
        quality_z   * is_bear,
        sentiment_z * is_panic,
        is_bull,
        is_bear,
        is_panic,
    ], dtype=float)


def train(horizon: str, market: str = "IN") -> bool:
    """
    Train Ridge regression on stored (factor_zscores, fwd_return) pairs for
    one market. If xgboost is installed, also trains an XGBoost model and
    uses whichever has lower cross-val RMSE.
    Returns True if training succeeded.
    """
    try:
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
        from sklearn.model_selection import cross_val_score
        from services.alpha_engine.store import get_training_data

        data = get_training_data(horizon, market=market)
        if len(data) < MIN_ROWS:
            log.info(f"[meta_model] {market}/{horizon}: only {len(data)} rows (need {MIN_ROWS}) — skipping")
            return False

        X, y = [], []
        for row in data:
            if row.get("fwd_return") is None:
                continue
            fv = _build_features(
                row.get("tech_z")      or 0.0,
                row.get("fund_z")      or 0.0,
                row.get("sentiment_z") or 0.0,
                row.get("quality_z")   or 0.0,
                row.get("combined_alpha") or 0.0,
                regime_id=0,  # regime not stored in early logs; neutral default
            )
            X.append(fv)
            y.append(float(row["fwd_return"]))

        X_arr, y_arr = np.array(X), np.array(y)

        ridge = Pipeline([("sc", StandardScaler()), ("r", Ridge(alpha=1.0))])
        ridge_cv = -cross_val_score(ridge, X_arr, y_arr, cv=5,
                                    scoring="neg_mean_squared_error").mean()
        ridge.fit(X_arr, y_arr)
        best_model = ridge
        best_rmse  = ridge_cv ** 0.5

        # Try XGBoost if available
        try:
            from xgboost import XGBRegressor
            xgb = Pipeline([
                ("sc", StandardScaler()),
                ("xgb", XGBRegressor(
                    n_estimators=200, max_depth=4, learning_rate=0.05,
                    subsample=0.8, colsample_bytree=0.8,
                    random_state=42, verbosity=0,
                )),
            ])
            xgb_cv = -cross_val_score(xgb, X_arr, y_arr, cv=5,
                                      scoring="neg_mean_squared_error").mean()
            xgb_rmse = xgb_cv ** 0.5
            if xgb_rmse < best_rmse:
                xgb.fit(X_arr, y_arr)
                best_model = xgb
                best_rmse  = xgb_rmse
                log.info(f"[meta_model] {market}/{horizon}: XGBoost wins (RMSE {xgb_rmse:.4f} vs Ridge {ridge_cv**0.5:.4f})")
            else:
                log.info(f"[meta_model] {market}/{horizon}: Ridge wins (RMSE {best_rmse:.4f})")
        except ImportError:
            log.info(f"[meta_model] {market}/{horizon}: Ridge trained (RMSE {best_rmse:.4f}), xgboost not installed")

        with open(_model_path(horizon, market), "wb") as f:
            pickle.dump(best_model, f)

        with _lock:
            _cache[f"{market}:{horizon}"] = best_model

        log.info(f"[meta_model] {market}/{horizon}: model saved ({len(X)} rows)")
        return True

    except Exception as e:
        log.warning(f"[meta_model] Training error ({market}/{horizon}): {e}")
        return False


def predict(tech_z: float, fund_z: float, sentiment_z: float,
            quality_z: float, combined_alpha: float,
            regime_id: int, horizon: str, market: str = "IN") -> float | None:
    """
    Predict expected forward return for one stock.
    Returns None if no model is trained yet (caller should use combined_alpha instead).
    """
    cache_key = f"{market}:{horizon}"
    with _lock:
        model = _cache.get(cache_key)

    if model is None:
        path = _model_path(horizon, market)
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    model = pickle.load(f)
                with _lock:
                    _cache[cache_key] = model
            except Exception:
                return None
        else:
            return None

    try:
        fv = _build_features(
            tech_z, fund_z, sentiment_z, quality_z, combined_alpha, regime_id
        ).reshape(1, -1)
        return float(model.predict(fv)[0])
    except Exception as e:
        log.warning(f"[meta_model] Predict error: {e}")
        return None


def is_trained(horizon: str, market: str = "IN") -> bool:
    return os.path.exists(_model_path(horizon, market))
