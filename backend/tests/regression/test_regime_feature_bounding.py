"""
DP-018 — bound every regime feature to the semantic model's declared
[0.0, 1.0] domain before prediction, logging, or future retraining.

Root cause: `extract_features()` only ever bounded VIX on one side
(`min(1.0, vix / 40.0)` bounds above, nothing bounds below); the other four
raw normalisations (S&P change, DXY, US10Y yield, Nifty change) were
unbounded on both sides. `SEMANTIC_ANCHOR_CENTROIDS` (DP-017) and every
fitted/loaded KMeans model anchored to them are documented and declared to
operate in a [0,1] feature space — an out-of-domain provider value (a
negative VIX from a bad feed, a DXY spike, an extreme yield print) would
otherwise dominate Euclidean distance to the anchors at prediction time, and
could pull learned centroids outside the domain on a future retrain fit
directly on stored history (including any legacy out-of-domain rows).

This fix introduces one centralized, pure bounding implementation
(`bound_feature_vector` / `bound_feature_matrix`) and routes every path that
produces or consumes a feature vector through it: `extract_features()`
(hence `detect_regime()`'s prediction input, its published `features`, and
what it logs via `log_regime()`), and `retrain_on_history()`'s fit input (on
an in-memory bounded copy — the stored history itself is never rewritten).

These tests prove, without any network/production-DB access and without
ever touching the repository's real regime_kmeans.pkl artifact, that:
  1-6.   bound_feature_vector() is a pure, non-mutating, shape-validated
         clip to [0,1] (in-range unchanged, per-dimension clipping both
         directions, exact boundary values preserved, malformed shape
         raises explicitly).
  7-14.  extract_features() is unchanged for default/in-range inputs and
         correctly clips every documented boundary example (VIX, S&P, DXY,
         US10Y, Nifty) and a mixed multi-dimensional extreme case.
  15-18. detect_regime() passes only bounded features to model.predict(),
         returns only bounded values in result["features"], passes only
         bounded values to log_regime(), and ordinary in-range inputs
         preserve the prior regime prediction (unaffected by this change).
  19-22. retrain_on_history() bounds a legacy/out-of-domain history matrix
         before KMeans.fit(), never mutates the array returned by the
         store, still anchors the fitted model before saving (DP-017), and
         an anchoring failure still prevents save/cache-invalidation.
  23.    SEMANTIC_ANCHOR_CENTROIDS are unchanged and already inside [0,1].
  24.    (Covered by re-running test_regime_cluster_anchoring.py unmodified
         alongside this file — DP-017 anchoring tests remain passing.)
  25-26. No test in this file calls the real store, network, or production
         DB, and no test manually triggers a "live" retrain outside a fully
         mocked history/model path.
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
    """Redirect MODEL_PATH to a throwaway file — the repository's real
    regime_kmeans.pkl must never be read or written by this suite."""
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
# 1-6 — bound_feature_vector(): pure, non-mutating, shape-validated clip
# ---------------------------------------------------------------------------

class TestBoundFeatureVectorPure:
    def test_in_range_vector_unchanged(self):
        v = np.array([0.1, 0.4, 0.6, 0.9, 0.5])
        bounded = rc.bound_feature_vector(v)
        np.testing.assert_allclose(bounded, v)

    def test_returns_copy_does_not_mutate_caller(self):
        v = np.array([-1.0, 2.0, 0.5, 0.5, 0.5])
        original = v.copy()
        bounded = rc.bound_feature_vector(v)
        np.testing.assert_array_equal(v, original)
        assert bounded is not v
        assert not np.array_equal(bounded, v)

    def test_every_dimension_clips_below_zero_to_zero(self):
        v = np.array([-0.1, -5.0, -100.0, -0.0001, -1.0])
        bounded = rc.bound_feature_vector(v)
        np.testing.assert_allclose(bounded, [0.0, 0.0, 0.0, 0.0, 0.0])

    def test_every_dimension_clips_above_one_to_one(self):
        v = np.array([1.1, 5.0, 100.0, 1.0001, 2.0])
        bounded = rc.bound_feature_vector(v)
        np.testing.assert_allclose(bounded, [1.0, 1.0, 1.0, 1.0, 1.0])

    def test_exact_zero_and_one_boundaries_unchanged(self):
        v = np.array([0.0, 1.0, 0.0, 1.0, 0.0])
        bounded = rc.bound_feature_vector(v)
        np.testing.assert_allclose(bounded, v)

    @pytest.mark.parametrize("bad_shape", [
        [0.1, 0.2, 0.3, 0.4],           # too short
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],  # too long
        [],                              # empty
    ])
    def test_malformed_length_raises_value_error(self, bad_shape):
        with pytest.raises(ValueError):
            rc.bound_feature_vector(np.array(bad_shape))

    def test_malformed_2d_shape_raises_value_error(self):
        with pytest.raises(ValueError):
            rc.bound_feature_vector(np.array([[0.1, 0.2, 0.3, 0.4, 0.5]]))


class TestBoundFeatureMatrixPure:
    def test_matrix_clips_each_row_independently(self):
        m = np.array([
            [-1.0, 2.0, 0.5, 0.5, 0.5],
            [0.1, 0.4, 0.6, 0.9, 0.5],
        ])
        bounded = rc.bound_feature_matrix(m)
        np.testing.assert_allclose(bounded[0], [0.0, 1.0, 0.5, 0.5, 0.5])
        np.testing.assert_allclose(bounded[1], m[1])

    def test_matrix_does_not_mutate_caller(self):
        m = np.array([[-1.0, 2.0, 0.5, 0.5, 0.5]])
        original = m.copy()
        rc.bound_feature_matrix(m)
        np.testing.assert_array_equal(m, original)

    def test_wrong_column_count_raises_value_error(self):
        with pytest.raises(ValueError):
            rc.bound_feature_matrix(np.array([[0.1, 0.2, 0.3, 0.4]]))

    def test_1d_input_raises_value_error(self):
        with pytest.raises(ValueError):
            rc.bound_feature_matrix(np.array([0.1, 0.2, 0.3, 0.4, 0.5]))


# ---------------------------------------------------------------------------
# 7-14 — extract_features(): unchanged in-range/default behaviour, correct
# clipping at every documented boundary
# ---------------------------------------------------------------------------

class TestExtractFeaturesDefaultsUnchanged:
    def test_empty_context_uses_documented_defaults(self):
        # VIX 18, S&P 0, DXY 100, US10Y 4, Nifty 0 — all already in [0,1],
        # values must be numerically identical to the pre-DP-018 formulas.
        features = rc.extract_features({})
        expected = np.array([18.0 / 40.0, 0.5, 0.5, 0.4, 0.5])
        np.testing.assert_allclose(features, expected)

    def test_ordinary_in_range_context_unchanged(self):
        ctx = _global_ctx(vix=20.0, sp500=1.0, dxy=102.0, us10y=4.2, nifty50=-0.5)
        features = rc.extract_features(ctx)
        expected = np.array([0.5, 0.6, 0.6, 0.44, 0.45])
        np.testing.assert_allclose(features, expected)


class TestExtractFeaturesBoundaryExamples:
    @pytest.mark.parametrize("vix,expected", [(-5.0, 0.0), (20.0, 0.5), (80.0, 1.0)])
    def test_vix_examples(self, vix, expected):
        features = rc.extract_features(_global_ctx(vix=vix))
        assert features[0] == pytest.approx(expected)

    @pytest.mark.parametrize("sp500,expected", [(-20.0, 0.0), (0.0, 0.5), (20.0, 1.0)])
    def test_sp500_examples(self, sp500, expected):
        features = rc.extract_features(_global_ctx(sp500=sp500))
        assert features[1] == pytest.approx(expected)

    @pytest.mark.parametrize("dxy,expected", [(70.0, 0.0), (100.0, 0.5), (130.0, 1.0)])
    def test_dxy_examples(self, dxy, expected):
        features = rc.extract_features(_global_ctx(dxy=dxy))
        assert features[2] == pytest.approx(expected)

    @pytest.mark.parametrize("us10y,expected", [(-1.0, 0.0), (4.5, 0.5), (10.0, 1.0)])
    def test_us10y_examples(self, us10y, expected):
        features = rc.extract_features(_global_ctx(us10y=us10y))
        assert features[3] == pytest.approx(expected)

    @pytest.mark.parametrize("nifty50,expected", [(-20.0, 0.0), (0.0, 0.5), (20.0, 1.0)])
    def test_nifty_examples(self, nifty50, expected):
        features = rc.extract_features(_global_ctx(nifty50=nifty50))
        assert features[4] == pytest.approx(expected)

    def test_mixed_multidimensional_extremes_all_land_in_range(self):
        ctx = _global_ctx(vix=-50.0, sp500=500.0, dxy=-1000.0, us10y=999.0, nifty50=-999.0)
        features = rc.extract_features(ctx)
        assert features.shape == (5,)
        assert np.all(features >= 0.0)
        assert np.all(features <= 1.0)
        np.testing.assert_allclose(features, [0.0, 1.0, 0.0, 1.0, 0.0])


# ---------------------------------------------------------------------------
# 15-18 — detect_regime(): bounded prediction input, bounded published
# features, bounded log_regime() input, unaffected in-range prediction
# ---------------------------------------------------------------------------

class TestDetectRegimeUsesBoundedFeaturesThroughout:
    def test_predict_receives_bounded_features(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)

        captured = {}

        class _SpyModel:
            def predict(self, X):
                captured["X"] = np.array(X)
                return np.array([0])

        monkeypatch.setattr(rc, "_load_or_init_model", lambda: _SpyModel())

        # Wildly out-of-domain raw inputs — the raw normalisation would be
        # far outside [0,1] without this fix.
        ctx = _global_ctx(vix=500.0, sp500=-999.0, dxy=-500.0, us10y=999.0, nifty50=999.0)
        rc.detect_regime(ctx)

        assert "X" in captured
        assert captured["X"].shape == (1, 5)
        assert np.all(captured["X"] >= 0.0)
        assert np.all(captured["X"] <= 1.0)

    def test_result_features_field_is_bounded(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)
        monkeypatch.setattr(rc, "_load_or_init_model", lambda: None)  # falls back to regime_id 0

        ctx = _global_ctx(vix=-999.0, sp500=999.0, dxy=999.0, us10y=-999.0, nifty50=999.0)
        result = rc.detect_regime(ctx)

        features = result["features"]
        assert len(features) == 5
        assert all(0.0 <= f <= 1.0 for f in features)

    def test_log_regime_receives_bounded_features(self, _tmp_model_path, monkeypatch):
        captured = {}

        def _spy_log_regime(regime_id, label, features):
            captured["features"] = features

        monkeypatch.setattr("services.alpha_engine.store.log_regime", _spy_log_regime)
        monkeypatch.setattr(rc, "_load_or_init_model", lambda: None)

        ctx = _global_ctx(vix=-999.0, sp500=999.0, dxy=-999.0, us10y=999.0, nifty50=-999.0)
        rc.detect_regime(ctx)

        assert "features" in captured
        assert all(0.0 <= f <= 1.0 for f in captured["features"])

    def test_ordinary_in_range_inputs_preserve_prior_prediction(self, _tmp_model_path, monkeypatch):
        # In-range inputs were never affected by clipping before or after
        # DP-018 — this proves the fix is a no-op for the common case.
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)

        # BULL_CALM anchor probe (already used in DP-017's own test suite).
        ctx = _global_ctx(vix=13.2, sp500=1.5, dxy=100.0, us10y=4.0, nifty50=1.5)
        result = rc.detect_regime(ctx)

        assert result["label"] == "BULL_CALM"
        assert result["regime_id"] == 0
        assert result["description"] == rc.REGIME_DESCRIPTIONS["BULL_CALM"]
        assert result["weight_multipliers"] == rc.REGIME_WEIGHT_MULTIPLIERS["BULL_CALM"]


# ---------------------------------------------------------------------------
# 19-22 — retrain_on_history(): bounds legacy history before fit, never
# mutates the store's returned array, still anchors before saving, and an
# anchoring failure still prevents save/cache-invalidation
# ---------------------------------------------------------------------------

class TestRetrainOnHistoryBoundsLegacyData:
    def _out_of_domain_history(self, n=32):
        # A mix of in-domain rows and legacy out-of-domain rows (negative
        # VIX-norm, DXY-norm > 1, etc.) — exactly what a pre-DP-018 stored
        # snapshot could contain.
        rng = np.random.default_rng(7)
        rows = []
        for i in range(n):
            if i % 4 == 0:
                rows.append([-0.5, 2.0, -1.0, 1.5, -2.0])  # out-of-domain
            else:
                center = rc.SEMANTIC_ANCHOR_CENTROIDS[i % 4]
                rows.append((center + rng.normal(scale=0.01, size=5)).tolist())
        return rows

    def test_fit_receives_bounded_matrix(self, _tmp_model_path, monkeypatch):
        history = self._out_of_domain_history()
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)

        captured = {}
        from sklearn.cluster import KMeans
        real_fit = KMeans.fit

        def spy_fit(self, X, *a, **kw):
            captured["X"] = np.array(X)
            return real_fit(self, X, *a, **kw)

        monkeypatch.setattr(KMeans, "fit", spy_fit)

        rc.retrain_on_history()

        assert "X" in captured
        assert np.all(captured["X"] >= 0.0)
        assert np.all(captured["X"] <= 1.0)

    def test_stored_history_array_not_mutated(self, _tmp_model_path, monkeypatch):
        history = self._out_of_domain_history()
        history_arg_capture = {}

        def _get_history():
            arr = np.array(history)
            history_arg_capture["returned"] = arr
            history_arg_capture["snapshot"] = arr.copy()
            return arr

        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", _get_history)

        rc.retrain_on_history()

        # The exact array object returned by the store must be unchanged
        # after retrain_on_history() runs.
        np.testing.assert_array_equal(
            history_arg_capture["returned"], history_arg_capture["snapshot"]
        )

    def test_still_anchors_before_saving(self, _tmp_model_path, monkeypatch):
        history = self._out_of_domain_history()
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
        history = self._out_of_domain_history()
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: history)
        monkeypatch.setattr(
            rc, "anchor_to_semantic_labels",
            lambda model, *a, **kw: (_ for _ in ()).throw(ValueError("simulated failure")),
        )
        save_calls = []
        monkeypatch.setattr(rc, "_save_model", lambda m: save_calls.append(m))

        sentinel = {"regime_id": 1, "label": "BULL_VOLATILE"}
        rc._cache = sentinel
        rc._cache_expiry = rc.time.time() + 1000

        rc.retrain_on_history()

        assert save_calls == []
        assert rc._cache is sentinel


# ---------------------------------------------------------------------------
# 23 — SEMANTIC_ANCHOR_CENTROIDS unchanged and already inside [0,1]
# ---------------------------------------------------------------------------

class TestSemanticAnchorsUnchangedAndInDomain:
    def test_anchors_match_documented_values(self):
        expected = np.array([
            [0.33, 0.65, 0.50, 0.40, 0.65],
            [0.50, 0.55, 0.55, 0.50, 0.55],
            [0.58, 0.40, 0.60, 0.55, 0.40],
            [0.80, 0.20, 0.65, 0.60, 0.20],
        ])
        np.testing.assert_allclose(rc.SEMANTIC_ANCHOR_CENTROIDS, expected)

    def test_anchors_are_entirely_within_bounded_domain(self):
        assert np.all(rc.SEMANTIC_ANCHOR_CENTROIDS >= rc.FEATURE_MIN)
        assert np.all(rc.SEMANTIC_ANCHOR_CENTROIDS <= rc.FEATURE_MAX)
