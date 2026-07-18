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

import itertools
import os
import pickle
import threading
import time
from typing import NamedTuple

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

# DP-018 — the semantic model (SEMANTIC_ANCHOR_CENTROIDS, and every fitted/
# loaded KMeans model anchored to them) is declared and documented as
# operating in a [0,1] feature space. Only the VIX term was ever actually
# bounded on both sides (`min(1.0, vix / 40.0)` bounds above; nothing
# bounded it below, and the other four raw normalisations were unbounded on
# both sides). An out-of-domain provider value (e.g. a negative VIX from a
# bad feed, a DXY spike) would otherwise dominate Euclidean distance to the
# anchors/centroids and, on retrain, could pull learned centroids outside
# the domain the anchors and downstream logic assume. [0,1] is the sole
# approved domain because it is already the documented normalisation/anchor
# domain — no new economically arbitrary threshold is introduced here.
FEATURE_MIN = 0.0
FEATURE_MAX = 1.0
_N_FEATURES = 5


def bound_feature_vector(features: np.ndarray) -> np.ndarray:
    """
    DP-018 — clip a single 5-dim regime feature vector into the semantic
    model's declared [FEATURE_MIN, FEATURE_MAX] domain. Returns a new array
    (`np.clip` without `out=` never touches its input); the caller's array
    is never mutated. Raises ValueError for any shape other than exactly
    (5,) — a malformed vector must fail explicitly here rather than being
    silently truncated or padded into a wrong-length feature vector.

    DP-018 hardening: also raises ValueError if any value is non-finite
    (NaN, +inf, -inf). `np.clip` alone leaves NaN as NaN and silently maps
    +inf/-inf to 1.0/0.0 — falsely representing an invalid/missing provider
    value as a genuine extreme market observation. This helper is a generic
    bounding primitive; it deliberately does NOT decide what a non-finite
    input should default to, because that policy differs between live
    provider extraction (fall back to the existing missing-value default)
    and historical retraining (exclude the contaminated row entirely) — see
    `extract_features()` and `retrain_on_history()`.
    """
    arr = np.asarray(features, dtype=float)
    if arr.shape != (_N_FEATURES,):
        raise ValueError(
            f"bound_feature_vector: expected a ({_N_FEATURES},) feature vector, got shape {arr.shape}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            f"bound_feature_vector: all values must be finite, got {arr.tolist()}"
        )
    return np.clip(arr, FEATURE_MIN, FEATURE_MAX)


def bound_feature_matrix(features: np.ndarray) -> np.ndarray:
    """
    DP-018 — matrix form of `bound_feature_vector`, for bounding an entire
    (n, 5) historical feature matrix at once before retraining. Returns a
    new array; never mutates the caller's array. Raises ValueError unless
    the input is exactly 2-D with 5 columns, or if any value is non-finite
    (NaN, +inf, -inf) — see `bound_feature_vector`'s docstring for why this
    helper never silently substitutes a default for a non-finite value.
    Callers with potentially-contaminated data (e.g. `retrain_on_history()`)
    must exclude non-finite rows before calling this.
    """
    arr = np.asarray(features, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != _N_FEATURES:
        raise ValueError(
            f"bound_feature_matrix: expected an (n, {_N_FEATURES}) feature matrix, got shape {arr.shape}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "bound_feature_matrix: all values must be finite (found non-finite value(s) in the input)"
        )
    return np.clip(arr, FEATURE_MIN, FEATURE_MAX)

# DP-017 — canonical semantic-anchor centroids, in REGIME_LABELS ID order.
# This is the single source of truth for "what each semantic regime ID
# means" in feature space. Used both to bootstrap the very first model (so
# a fresh install starts in semantic order) and, more importantly, as the
# reference anchors that `anchor_to_semantic_labels()` matches every
# fitted/loaded KMeans model's (arbitrarily-ordered) cluster_centers_
# against — because raw KMeans cluster integer IDs carry no stable meaning
# across a refit, this is what keeps ID 0 meaning BULL_CALM after a
# retrain, rather than silently becoming whatever the solver happened to
# label cluster 0 that time.
# Each row: [vix_norm, sp500_chg_norm, dxy_norm, rates_norm, nifty_norm]
SEMANTIC_ANCHOR_CENTROIDS = np.array([
    [0.33, 0.65, 0.50, 0.40, 0.65],  # 0: BULL_CALM (VIX~13, market +3%)
    [0.50, 0.55, 0.55, 0.50, 0.55],  # 1: BULL_VOLATILE (VIX~20, market +1%)
    [0.58, 0.40, 0.60, 0.55, 0.40],  # 2: BEAR_CALM (VIX~23, market -1%)
    [0.80, 0.20, 0.65, 0.60, 0.20],  # 3: BEAR_PANIC (VIX~32, market -3%)
])

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


def _finite_float_or_default(value, default: float) -> tuple[float, bool]:
    """
    DP-018 hardening — convert a raw provider value into a finite float for
    live regime feature extraction. `None`/missing evidence silently uses
    `default` (unchanged pre-existing behaviour). A *present but invalid*
    value — a bool (never legitimate market data), a non-numeric/malformed
    value, `NaN`, or `+inf`/`-inf` — also falls back to `default` (the same
    existing missing-value default; never a new imputed value), but is
    reported back as invalid so the caller can log which fields were bad.
    Never raises during ordinary live feature extraction.

    Returns (value, was_invalid). `was_invalid` is False for both a clean
    finite value and a simply-missing (`None`) value — only True when a
    present value had to be discarded as unusable.
    """
    if value is None:
        return default, False
    if isinstance(value, bool):
        return default, True
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default, True
    if not np.isfinite(f):
        return default, True
    return f, False


def extract_features(global_ctx: dict) -> np.ndarray:
    """Convert global context dict → 5-dim normalised feature vector,
    bounded to the semantic model's declared [0,1] domain (DP-018).

    DP-018 hardening: a missing, non-numeric, or non-finite (NaN/±inf)
    provider value is treated as missing/invalid EVIDENCE, not a genuine
    extreme market observation — it falls back to the same existing
    missing-value default already used when a value is absent."""
    levels  = global_ctx.get("levels", {})
    changes = global_ctx.get("changes", {})

    vix,       vix_invalid   = _finite_float_or_default(levels.get("vix"), 18.0)
    sp500_chg, sp500_invalid = _finite_float_or_default(changes.get("sp500"), 0.0)
    dxy,       dxy_invalid   = _finite_float_or_default(levels.get("dxy"), 100.0)
    us10y,     us10y_invalid = _finite_float_or_default(levels.get("us10y"), 4.0)
    nifty_chg, nifty_invalid = _finite_float_or_default(changes.get("nifty50"), 0.0)

    invalid_fields = [name for name, bad in (
        ("vix", vix_invalid), ("sp500", sp500_invalid), ("dxy", dxy_invalid),
        ("us10y", us10y_invalid), ("nifty50", nifty_invalid),
    ) if bad]
    if invalid_fields:
        print(
            f"[regime] Invalid (non-finite/non-numeric) provider value(s) for "
            f"{invalid_fields} — using existing missing-value defaults."
        )

    raw = np.array([
        min(1.0, vix / 40.0),            # VIX: 0=calm, 1=extreme fear
        (sp500_chg + 5.0) / 10.0,        # S&P trend: 0=down 5%, 1=up 5%
        (dxy - 90.0) / 20.0,             # DXY: 0=90, 1=110
        (us10y - 2.0) / 5.0,             # 10Y yield: 0=2%, 1=7%
        (nifty_chg + 5.0) / 10.0,        # Nifty trend: 0=down 5%, 1=up 5%
    ], dtype=float)
    return bound_feature_vector(raw)


class AnchorResult(NamedTuple):
    """Metadata from anchor_to_semantic_labels(), for logging/testing."""
    mapping: dict          # raw cluster ID (pre-anchoring) -> semantic ID
    permutation: tuple     # permutation[semantic_id] == raw_cluster_id
    is_identity: bool      # True if raw IDs already matched semantic IDs
    total_distance: float  # sum of ||learned centroid - matched anchor|| at the chosen assignment


def anchor_to_semantic_labels(model, anchors: np.ndarray = SEMANTIC_ANCHOR_CENTROIDS) -> AnchorResult:
    """
    DP-017 — deterministically match a fitted KMeans model's (arbitrarily
    ordered) cluster_centers_ to the canonical semantic anchors, and reorder
    the model in place so its raw cluster IDs become semantic IDs.

    KMeans cluster integer IDs have no stable meaning across a refit — the
    solver may hand what is economically BEAR_PANIC the integer 0 on one fit
    and 3 on the next. REGIME_LABELS is a fixed ID->name table, so an
    unanchored retrain can silently invert what every downstream ID means
    (label, description, weight multipliers, bull/panic dummies, ranking).

    This performs a brute-force minimum-total-distance assignment over all
    4! = 24 permutations (cheap at n=4, avoids a new Hungarian-algorithm
    dependency) between the model's learned centroids and the semantic
    anchor centroids, then:
      - reorders `model.cluster_centers_` so index i now IS semantic ID i,
        meaning a future `model.predict(...)` call returns semantic IDs
        directly with no separate remapping step at call time;
      - remaps `model.labels_` (if present) from raw to semantic IDs, so
        already-fitted labels stay consistent with the reordered centres;
      - leaves every other fitted attribute (inertia_, n_iter_, ...)
        untouched;
      - makes NO mutation at all when the mapping is already the identity
        (raw IDs already equalled semantic IDs) — callers relying on
        object/attribute stability in that case are unaffected.

    Raises ValueError (fail explicitly, never silently) if `model` does not
    expose a `cluster_centers_` array shaped (4, len(anchors[0])) — callers
    (bootstrap/retrain/load) must catch this and refuse to use or persist
    the unanchored model rather than trust an unverifiable mapping.
    """
    centers = getattr(model, "cluster_centers_", None)
    if centers is None:
        raise ValueError("anchor_to_semantic_labels: model has no cluster_centers_")
    centers = np.asarray(centers, dtype=float)
    anchors = np.asarray(anchors, dtype=float)

    if centers.ndim != 2:
        raise ValueError(f"anchor_to_semantic_labels: cluster_centers_ must be 2-D, got shape {centers.shape}")
    n_clusters, n_features = centers.shape
    n_anchors, anchor_features = anchors.shape
    if n_clusters != n_anchors:
        raise ValueError(
            f"anchor_to_semantic_labels: expected {n_anchors} cluster centres, got {n_clusters}"
        )
    if n_features != anchor_features:
        raise ValueError(
            f"anchor_to_semantic_labels: expected {anchor_features}-dim centroids, got {n_features}-dim"
        )

    # Brute-force minimum-total-distance assignment. `perm[semantic_id] ==
    # raw_cluster_id` for each candidate permutation; pick the permutation
    # that minimises the summed distance between each semantic anchor and
    # its assigned learned centroid. Deterministic: itertools.permutations
    # is lexicographic, and the first (lowest) minimum found wins ties.
    best_perm = None
    best_cost = None
    for perm in itertools.permutations(range(n_clusters)):
        cost = sum(
            float(np.linalg.norm(centers[perm[semantic_id]] - anchors[semantic_id]))
            for semantic_id in range(n_clusters)
        )
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_perm = perm

    # raw_to_semantic[raw_cluster_id] = semantic_id
    raw_to_semantic = {raw_id: semantic_id for semantic_id, raw_id in enumerate(best_perm)}
    is_identity = all(raw_to_semantic[i] == i for i in range(n_clusters))

    if not is_identity:
        model.cluster_centers_ = centers[list(best_perm)]
        labels = getattr(model, "labels_", None)
        if labels is not None:
            mapping_array = np.array([raw_to_semantic[i] for i in range(n_clusters)])
            model.labels_ = mapping_array[np.asarray(labels)]

    return AnchorResult(
        mapping=raw_to_semantic,
        permutation=best_perm,
        is_identity=is_identity,
        total_distance=float(best_cost),
    )


def _load_or_init_model():
    try:
        from sklearn.cluster import KMeans
    except ImportError:
        return None

    if os.path.exists(MODEL_PATH):
        try:
            with open(MODEL_PATH, "rb") as f:
                loaded = pickle.load(f)
            # DP-017 — an artifact on disk may predate anchoring, or may
            # have been produced by an unanchored retrain before this
            # safeguard existed. Anchor it IN MEMORY every time it's loaded
            # so a historic permutation can never silently invert regime
            # meaning — but never write the corrected model back to disk
            # here; loading must stay a read-only operation. If the mapping
            # is already identity (the common case), anchor_to_semantic_labels
            # makes no mutation at all, so this is a no-op for a healthy
            # artifact.
            anchor_to_semantic_labels(loaded)
            return loaded
        except Exception as e:
            print(f"[regime] Discarding unusable/unanchorable model artifact: {e}")

    # Bootstrap with the canonical semantic-anchor centroids — fitting
    # directly on the anchors themselves, in semantic-ID order, so a fresh
    # install starts already anchored. Still run it through the same
    # anchoring helper as every other path (rather than assuming that),
    # since it's the single safeguard this task guarantees for every model
    # this module ever hands to detect_regime().
    km = KMeans(n_clusters=4, init=SEMANTIC_ANCHOR_CENTROIDS, n_init=1, random_state=42)
    km.fit(SEMANTIC_ANCHOR_CENTROIDS)
    try:
        anchor_to_semantic_labels(km)
    except ValueError as e:
        print(f"[regime] Bootstrap anchoring failed, refusing to save: {e}")
        return None
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

        # DP-018 hardening — a stored snapshot may predate finite-value
        # validation, or a provider bug could have logged NaN/inf before
        # this safeguard existed. A non-finite row is contaminated
        # evidence, not an extreme observation to impute or clip — it is
        # excluded from the in-memory retraining dataset entirely rather
        # than guessed at. Stored history is never rewritten or repaired.
        finite_row_mask = np.all(np.isfinite(X), axis=1)
        n_excluded = int(len(X) - int(np.sum(finite_row_mask)))
        if n_excluded:
            print(
                f"[regime] Excluded {n_excluded} historical row(s) containing "
                f"non-finite (NaN/inf) feature values from retraining "
                f"(stored history left unmodified)."
            )
        X_valid = X[finite_row_mask]

        if len(X_valid) < 30:
            print(
                f"[regime] Not enough valid (finite) history to retrain after "
                f"excluding non-finite rows ({len(X_valid)} valid, need >= 30) "
                f"— prior model artifact retained; cache not invalidated."
            )
            return

        # DP-018 — fit on a bounded in-memory copy of the (now finite-only)
        # rows so any legacy out-of-domain-but-finite snapshot cannot pull
        # learned centroids outside the semantic [0,1] domain. `X`/`X_valid`
        # (and the stored history they came from) are never mutated or
        # written back.
        X_bounded = bound_feature_matrix(X_valid)
        n_clipped = int(np.sum(X_valid != X_bounded))
        if n_clipped:
            print(
                f"[regime] Bounded {n_clipped} out-of-domain historical feature "
                f"value(s) into [0,1] before retraining (stored history left unmodified)."
            )
        km = KMeans(n_clusters=4, n_init=10, random_state=42)
        km.fit(X_bounded)

        # DP-017 / DPD-007 safety constraint — a retrained model's raw
        # cluster IDs carry no stable meaning until matched back to the
        # canonical semantic anchors. Never persist (or invalidate the
        # cache for) a retrained model unless anchoring succeeds: an
        # anchoring failure here means we cannot verify what the new IDs
        # mean, so the prior, already-anchored artifact stays in place and
        # in use, and the working cached production result is left intact.
        try:
            anchor_result = anchor_to_semantic_labels(km)
        except ValueError as e:
            print(
                f"[regime] Retraining REJECTED — cluster anchoring failed: {e}. "
                "Prior model artifact retained; cache not invalidated."
            )
            return

        _save_model(km)

        # Invalidate cache so next call uses new model
        global _cache, _cache_expiry
        with _lock:
            _cache = None
            _cache_expiry = 0

        print(
            f"[regime] KMeans retrained on {len(X_bounded)} historical snapshots "
            f"(anchored: identity={anchor_result.is_identity}, "
            f"total_distance={anchor_result.total_distance:.4f})"
        )
    except Exception as e:
        print(f"[regime] Retrain error: {e}")
