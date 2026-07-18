"""
DP-018 finite-value hardening follow-up (not a new DP finding).

Confirmed residual defect: `bound_feature_vector()`/`bound_feature_matrix()`
validated shape and used `np.clip()`, but never validated finiteness. This
left two incorrect states: (1) `NaN` remains `NaN` after `np.clip()`; (2)
`+inf`/`-inf` are silently mapped to `1.0`/`0.0` by `np.clip()`, falsely
representing an invalid/missing provider value as a genuine extreme market
observation. `retrain_on_history()`'s `n_clipped = int(np.sum(X != X_bounded))`
also miscounted a `NaN` row as "changed" even though `np.clip()` leaves NaN
as NaN, after which `KMeans.fit()` could fail outright.

Approved policy implemented here:
  - Live provider extraction: a missing, non-numeric, boolean, NaN, or
    ±infinite value is MISSING/INVALID evidence, not an extreme
    observation — `_finite_float_or_default()` falls back to the exact
    same raw default `extract_features()` already used for a missing
    value (VIX 18.0, S&P 0.0, DXY 100.0, US10Y 4.0, Nifty 0.0), which
    normalise to (0.45, 0.50, 0.50, 0.40, 0.50) after DP-018 bounding.
  - `bound_feature_vector()`/`bound_feature_matrix()` now guarantee every
    returned value is finite and in [0,1], and raise ValueError on any
    non-finite input passed directly — they do NOT decide what a
    non-finite value should default to, since that policy differs by
    caller (live default-fallback vs. historical row-exclusion).
  - Historical retraining: `retrain_on_history()` excludes (not imputes)
    any row containing a non-finite value from the in-memory retraining
    matrix before bounding/fitting, logs one summary count, requires at
    least 30 valid (finite) rows to proceed, and never mutates or rewrites
    the stored history.

These tests prove, without any network/production-DB access and without
ever touching the repository's real regime_kmeans.pkl artifact:
  1-3.   bound_feature_vector() rejects NaN, +inf, -inf.
  4.     bound_feature_matrix() rejects any row containing a non-finite
         value.
  5.     Neither helper mutates its caller.
  6.     All existing finite-clipping tests remain passing (covered by
         re-running test_regime_feature_bounding.py + this file together).
  7-12.  Live invalid-field defaults: VIX NaN -> 18.0/0.45; S&P NaN ->
         0.0/0.50; DXY +inf -> 100.0/0.50; US10Y -inf -> 4.0/0.40; Nifty
         malformed string -> 0.0/0.50; a mixed-invalid snapshot produces
         the exact default vector.
  13-16. detect_regime() receives/returns/logs only finite [0,1] values,
         and ordinary valid inputs are numerically unchanged.
  17-21. Retraining excludes NaN/inf rows, still clips remaining finite
         out-of-domain rows, KMeans receives only finite bounded rows,
         and the store's returned array is never mutated.
  22-25. Retraining continues with >=30 valid rows, safely stops with <30
         valid rows (no save, no cache invalidation), the prior model is
         retained.
  26-27. Successful fitting still anchors (DP-017) before saving; an
         anchoring failure still prevents save/cache-invalidation.
  28.    No test accesses a real model artifact, production DB, or
         provider.
"""

import numpy as np
import pytest

import services.alpha_engine.regime_cluster as rc


@pytest.fixture(autouse=True)
def _reset_regime_cache():
    rc._cache = None
    rc._cache_expiry = 0
    yield
    rc._cache = None
    rc._cache_expiry = 0


@pytest.fixture
def _tmp_model_path(tmp_path, monkeypatch):
    path = tmp_path / "regime_kmeans_test.pkl"
    monkeypatch.setattr(rc, "MODEL_PATH", str(path))
    return path


def _global_ctx(vix=None, sp500=None, dxy=None, us10y=None, nifty50=None):
    ctx = {"levels": {}, "changes": {}}
    if vix is not None:
        ctx["levels"]["vix"] = vix
    if dxy is not None:
        ctx["levels"]["dxy"] = dxy
    if us10y is not None:
        ctx["levels"]["us10y"] = us10y
    if sp500 is not None:
        ctx["changes"]["sp500"] = sp500
    if nifty50 is not None:
        ctx["changes"]["nifty50"] = nifty50
    return ctx


# ---------------------------------------------------------------------------
# 1-5 — bounding helpers reject non-finite input, remain non-mutating
# ---------------------------------------------------------------------------

class TestBoundFeatureVectorRejectsNonFinite:
    def test_rejects_nan(self):
        v = np.array([np.nan, 0.5, 0.5, 0.5, 0.5])
        with pytest.raises(ValueError):
            rc.bound_feature_vector(v)

    def test_rejects_positive_infinity(self):
        v = np.array([0.5, np.inf, 0.5, 0.5, 0.5])
        with pytest.raises(ValueError):
            rc.bound_feature_vector(v)

    def test_rejects_negative_infinity(self):
        v = np.array([0.5, 0.5, -np.inf, 0.5, 0.5])
        with pytest.raises(ValueError):
            rc.bound_feature_vector(v)

    def test_does_not_mutate_caller_even_when_raising(self):
        v = np.array([np.nan, 0.5, 0.5, 0.5, 0.5])
        original = v.copy()
        with pytest.raises(ValueError):
            rc.bound_feature_vector(v)
        assert np.array_equal(v, original, equal_nan=True)


class TestBoundFeatureMatrixRejectsNonFinite:
    def test_rejects_row_containing_nan(self):
        m = np.array([
            [0.1, 0.2, 0.3, 0.4, 0.5],
            [np.nan, 0.2, 0.3, 0.4, 0.5],
        ])
        with pytest.raises(ValueError):
            rc.bound_feature_matrix(m)

    def test_rejects_row_containing_positive_infinity(self):
        m = np.array([[0.1, 0.2, 0.3, np.inf, 0.5]])
        with pytest.raises(ValueError):
            rc.bound_feature_matrix(m)

    def test_rejects_row_containing_negative_infinity(self):
        m = np.array([[0.1, 0.2, 0.3, 0.4, -np.inf]])
        with pytest.raises(ValueError):
            rc.bound_feature_matrix(m)

    def test_does_not_mutate_caller_even_when_raising(self):
        m = np.array([[np.nan, 0.2, 0.3, 0.4, 0.5]])
        original = m.copy()
        with pytest.raises(ValueError):
            rc.bound_feature_matrix(m)
        assert np.array_equal(m, original, equal_nan=True)


# ---------------------------------------------------------------------------
# 7-12 — live invalid-value policy: existing missing-value defaults
# ---------------------------------------------------------------------------

class TestFiniteFloatOrDefaultPure:
    def test_none_uses_default_silently(self):
        value, invalid = rc._finite_float_or_default(None, 18.0)
        assert value == 18.0
        assert invalid is False

    def test_nan_uses_default_and_flags_invalid(self):
        value, invalid = rc._finite_float_or_default(float("nan"), 18.0)
        assert value == 18.0
        assert invalid is True

    def test_positive_infinity_uses_default_and_flags_invalid(self):
        value, invalid = rc._finite_float_or_default(float("inf"), 100.0)
        assert value == 100.0
        assert invalid is True

    def test_negative_infinity_uses_default_and_flags_invalid(self):
        value, invalid = rc._finite_float_or_default(float("-inf"), 4.0)
        assert value == 4.0
        assert invalid is True

    def test_malformed_string_uses_default_and_flags_invalid(self):
        value, invalid = rc._finite_float_or_default("not-a-number", 0.0)
        assert value == 0.0
        assert invalid is True

    def test_bool_rejected_as_market_data(self):
        value, invalid = rc._finite_float_or_default(True, 18.0)
        assert value == 18.0
        assert invalid is True

    def test_finite_numeric_value_passes_through_unchanged(self):
        value, invalid = rc._finite_float_or_default(23.5, 18.0)
        assert value == 23.5
        assert invalid is False

    def test_finite_zero_is_not_treated_as_missing(self):
        # Zero is a legitimate finite value (e.g. a flat trading day) — must
        # not be discarded the way `value or default` would discard it.
        value, invalid = rc._finite_float_or_default(0.0, 18.0)
        assert value == 0.0
        assert invalid is False


class TestExtractFeaturesLiveInvalidValuePolicy:
    def test_vix_nan_uses_default_18_normalized_045(self):
        features = rc.extract_features(_global_ctx(vix=float("nan")))
        assert features[0] == pytest.approx(0.45)

    def test_sp500_nan_uses_default_0_normalized_050(self):
        features = rc.extract_features(_global_ctx(sp500=float("nan")))
        assert features[1] == pytest.approx(0.50)

    def test_dxy_positive_infinity_uses_default_100_normalized_050(self):
        features = rc.extract_features(_global_ctx(dxy=float("inf")))
        assert features[2] == pytest.approx(0.50)

    def test_us10y_negative_infinity_uses_default_4_normalized_040(self):
        features = rc.extract_features(_global_ctx(us10y=float("-inf")))
        assert features[3] == pytest.approx(0.40)

    def test_nifty_malformed_non_numeric_uses_default_0_normalized_050(self):
        features = rc.extract_features(_global_ctx(nifty50="not-a-number"))
        assert features[4] == pytest.approx(0.50)

    def test_mixed_invalid_snapshot_produces_exact_default_vector(self):
        ctx = _global_ctx(
            vix=float("nan"), sp500=float("inf"), dxy=float("-inf"),
            us10y="garbage", nifty50=True,
        )
        features = rc.extract_features(ctx)
        expected = np.array([0.45, 0.50, 0.50, 0.40, 0.50])
        np.testing.assert_allclose(features, expected)

    def test_result_is_always_finite_and_in_range_for_invalid_input(self):
        ctx = _global_ctx(vix=float("nan"), sp500=float("inf"), dxy=float("-inf"))
        features = rc.extract_features(ctx)
        assert np.all(np.isfinite(features))
        assert np.all(features >= 0.0)
        assert np.all(features <= 1.0)


# ---------------------------------------------------------------------------
# 13-16 — detect_regime(): finite-only prediction input, result, log input;
# valid inputs unaffected
# ---------------------------------------------------------------------------

class TestDetectRegimeNeverPropagatesNonFiniteValues:
    def test_predict_receives_only_finite_bounded_values(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)

        captured = {}

        class _SpyModel:
            def predict(self, X):
                captured["X"] = np.array(X)
                return np.array([0])

        monkeypatch.setattr(rc, "_load_or_init_model", lambda: _SpyModel())

        ctx = _global_ctx(vix=float("nan"), sp500=float("inf"), dxy=float("-inf"))
        rc.detect_regime(ctx)

        assert np.all(np.isfinite(captured["X"]))
        assert np.all(captured["X"] >= 0.0)
        assert np.all(captured["X"] <= 1.0)

    def test_result_features_are_finite_and_bounded(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)
        monkeypatch.setattr(rc, "_load_or_init_model", lambda: None)

        ctx = _global_ctx(vix="bad-data", sp500=float("nan"), nifty50=float("inf"))
        result = rc.detect_regime(ctx)

        features = result["features"]
        assert all(np.isfinite(f) for f in features)
        assert all(0.0 <= f <= 1.0 for f in features)

    def test_log_regime_receives_only_finite_bounded_features(self, _tmp_model_path, monkeypatch):
        captured = {}

        def _spy_log_regime(regime_id, label, features):
            captured["features"] = features

        monkeypatch.setattr("services.alpha_engine.store.log_regime", _spy_log_regime)
        monkeypatch.setattr(rc, "_load_or_init_model", lambda: None)

        ctx = _global_ctx(dxy=float("-inf"), us10y=float("nan"))
        rc.detect_regime(ctx)

        assert "features" in captured
        assert all(np.isfinite(f) for f in captured["features"])
        assert all(0.0 <= f <= 1.0 for f in captured["features"])

    def test_never_persists_nan_inf_or_malformed_string_features(self, _tmp_model_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "services.alpha_engine.store.log_regime",
            lambda regime_id, label, features: captured.setdefault("features", features),
        )
        monkeypatch.setattr(rc, "_load_or_init_model", lambda: None)

        ctx = _global_ctx(vix=float("nan"), sp500=float("inf"), dxy=float("-inf"),
                           us10y="garbage", nifty50=True)
        result = rc.detect_regime(ctx)

        for f in result["features"]:
            assert isinstance(f, float)
            assert np.isfinite(f)
        for f in captured["features"]:
            assert np.isfinite(f)

    def test_ordinary_valid_inputs_remain_numerically_unchanged(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)

        ctx = _global_ctx(vix=13.2, sp500=1.5, dxy=100.0, us10y=4.0, nifty50=1.5)
        result = rc.detect_regime(ctx)

        assert result["label"] == "BULL_CALM"
        assert result["regime_id"] == 0
        np.testing.assert_allclose(
            result["features"], [0.33, 0.65, 0.50, 0.40, 0.65], atol=1e-6
        )


# ---------------------------------------------------------------------------
# 17-25 — retraining: exclude non-finite rows, still clip finite ones, fit
# only finite bounded rows, never mutate stored history, 30-row threshold
# ---------------------------------------------------------------------------

class TestRetrainOnHistoryExcludesNonFiniteRows:
    def _mixed_history(self, n_finite=32, n_nan=2, n_pos_inf=1, n_neg_inf=1):
        rng = np.random.default_rng(11)
        rows = []
        for i in range(n_finite):
            center = rc.SEMANTIC_ANCHOR_CENTROIDS[i % 4]
            rows.append((center + rng.normal(scale=0.01, size=5)).tolist())
        for _ in range(n_nan):
            rows.append([float("nan"), 0.5, 0.5, 0.5, 0.5])
        for _ in range(n_pos_inf):
            rows.append([0.5, float("inf"), 0.5, 0.5, 0.5])
        for _ in range(n_neg_inf):
            rows.append([0.5, 0.5, float("-inf"), 0.5, 0.5])
        return rows

    def test_excludes_nan_row(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=32, n_nan=3, n_pos_inf=0, n_neg_inf=0)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        captured = {}
        from sklearn.cluster import KMeans
        real_fit = KMeans.fit

        def spy_fit(self, X, *a, **kw):
            captured["X"] = np.array(X)
            return real_fit(self, X, *a, **kw)

        monkeypatch.setattr(KMeans, "fit", spy_fit)

        rc.retrain_on_history()

        assert captured["X"].shape[0] == 32
        assert np.all(np.isfinite(captured["X"]))

    def test_excludes_positive_and_negative_infinity_rows(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=32, n_nan=0, n_pos_inf=2, n_neg_inf=2)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        captured = {}
        from sklearn.cluster import KMeans
        real_fit = KMeans.fit

        def spy_fit(self, X, *a, **kw):
            captured["X"] = np.array(X)
            return real_fit(self, X, *a, **kw)

        monkeypatch.setattr(KMeans, "fit", spy_fit)

        rc.retrain_on_history()

        assert captured["X"].shape[0] == 32
        assert np.all(np.isfinite(captured["X"]))

    def test_valid_rows_still_clipped_to_unit_domain(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=30, n_nan=1, n_pos_inf=0, n_neg_inf=0)
        history.append([-5.0, 5.0, -5.0, 5.0, -5.0])  # legacy out-of-domain but finite
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        captured = {}
        from sklearn.cluster import KMeans
        real_fit = KMeans.fit

        def spy_fit(self, X, *a, **kw):
            captured["X"] = np.array(X)
            return real_fit(self, X, *a, **kw)

        monkeypatch.setattr(KMeans, "fit", spy_fit)

        rc.retrain_on_history()

        assert np.all(captured["X"] >= 0.0)
        assert np.all(captured["X"] <= 1.0)

    def test_kmeans_receives_only_finite_bounded_rows(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=32, n_nan=2, n_pos_inf=1, n_neg_inf=1)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        rc.retrain_on_history()  # must not raise despite contaminated rows

        # A saved model implies KMeans.fit() succeeded on finite-only input.
        import pickle
        with open(rc.MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        assert np.all(np.isfinite(saved.cluster_centers_))

    def test_stored_history_array_not_mutated(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=32, n_nan=2, n_pos_inf=1, n_neg_inf=1)
        capture = {}

        def _get_history():
            arr = np.array(history)
            capture["returned"] = arr
            capture["snapshot"] = arr.copy()
            return arr

        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", _get_history)

        rc.retrain_on_history()

        np.testing.assert_array_equal(
            capture["returned"], capture["snapshot"], strict=False
        )
        # NaN-aware equality: compare bit patterns since NaN != NaN.
        assert np.array_equal(capture["returned"], capture["snapshot"], equal_nan=True)


class TestRetrainOnHistoryThirtyRowThreshold:
    def _mixed_history(self, n_finite, n_nan):
        rng = np.random.default_rng(3)
        rows = []
        for i in range(n_finite):
            center = rc.SEMANTIC_ANCHOR_CENTROIDS[i % 4]
            rows.append((center + rng.normal(scale=0.01, size=5)).tolist())
        for _ in range(n_nan):
            rows.append([float("nan"), 0.5, 0.5, 0.5, 0.5])
        return rows

    def test_continues_with_at_least_30_valid_rows(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=30, n_nan=5)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)
        save_calls = []
        monkeypatch.setattr(rc, "_save_model", lambda m: save_calls.append(m))

        rc.retrain_on_history()

        assert len(save_calls) == 1

    def test_safely_stops_with_fewer_than_30_valid_rows(self, _tmp_model_path, monkeypatch):
        # 32 raw rows (passes the initial len(history) >= 30 gate) but 5 are
        # NaN, leaving only 27 valid — below the post-exclusion threshold.
        history = self._mixed_history(n_finite=27, n_nan=5)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)
        save_calls = []
        monkeypatch.setattr(rc, "_save_model", lambda m: save_calls.append(m))

        rc.retrain_on_history()  # must not raise

        assert save_calls == []

    def test_safe_stop_does_not_invalidate_cache(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history(n_finite=27, n_nan=5)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        sentinel = {"regime_id": 2, "label": "BEAR_CALM"}
        rc._cache = sentinel
        rc._cache_expiry = rc.time.time() + 1000

        rc.retrain_on_history()

        assert rc._cache is sentinel

    def test_safe_stop_retains_prior_model_artifact(self, _tmp_model_path, monkeypatch):
        import pickle
        from sklearn.cluster import KMeans

        prior = KMeans(n_clusters=4, init=rc.SEMANTIC_ANCHOR_CENTROIDS, n_init=1, random_state=42)
        prior.fit(rc.SEMANTIC_ANCHOR_CENTROIDS)
        with open(_tmp_model_path, "wb") as f:
            pickle.dump(prior, f)
        prior_bytes = _tmp_model_path.read_bytes()

        history = self._mixed_history(n_finite=27, n_nan=5)
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        rc.retrain_on_history()

        assert _tmp_model_path.read_bytes() == prior_bytes


# ---------------------------------------------------------------------------
# 26-27 — successful fit still anchors before save; anchoring failure still
# prevents save/cache-invalidation (with contaminated history present)
# ---------------------------------------------------------------------------

class TestAnchoringStillGovernsSaveWithContaminatedHistory:
    def _mixed_history(self, n_finite=32, n_nan=2):
        rng = np.random.default_rng(5)
        rows = []
        for i in range(n_finite):
            center = rc.SEMANTIC_ANCHOR_CENTROIDS[i % 4]
            rows.append((center + rng.normal(scale=0.01, size=5)).tolist())
        for _ in range(n_nan):
            rows.append([float("nan"), 0.5, 0.5, 0.5, 0.5])
        return rows

    def test_successful_fit_still_anchors_before_saving(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history()
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        call_order = []
        real_anchor = rc.anchor_to_semantic_labels

        def spy_anchor(model, *a, **kw):
            call_order.append("anchor")
            return real_anchor(model, *a, **kw)

        def spy_save(model):
            call_order.append("save")

        monkeypatch.setattr(rc, "anchor_to_semantic_labels", spy_anchor)
        monkeypatch.setattr(rc, "_save_model", spy_save)

        rc.retrain_on_history()

        assert call_order == ["anchor", "save"]

    def test_anchoring_failure_still_prevents_save_and_cache_invalidation(self, _tmp_model_path, monkeypatch):
        history = self._mixed_history()
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)
        monkeypatch.setattr(
            rc, "anchor_to_semantic_labels",
            lambda model, *a, **kw: (_ for _ in ()).throw(ValueError("simulated failure")),
        )
        save_calls = []
        monkeypatch.setattr(rc, "_save_model", lambda m: save_calls.append(m))

        sentinel = {"regime_id": 3, "label": "BEAR_PANIC"}
        rc._cache = sentinel
        rc._cache_expiry = rc.time.time() + 1000

        rc.retrain_on_history()

        assert save_calls == []
        assert rc._cache is sentinel
