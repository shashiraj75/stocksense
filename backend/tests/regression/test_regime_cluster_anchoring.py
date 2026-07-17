"""
DP-017 — deterministic KMeans cluster-ID anchoring to semantic regime
labels (governed by DPD-007 — Regime model retraining policy, DECIDED —
SAFETY CONSTRAINT).

Root cause: KMeans cluster integer IDs are arbitrary and have no stable
meaning across a refit. REGIME_LABELS is a fixed {0: "BULL_CALM", ...} ID
table. Before this fix, `retrain_on_history()` saved a freshly refit model
with no verification that raw ID 0 still corresponded to BULL_CALM in
feature space — a retrain could silently invert every downstream regime
label, description, weight multiplier, and any bull/panic dummy variable.

These tests prove, without any network/production-DB access and without
ever touching the repository's real regime_kmeans.pkl artifact, that:
  1. Identity case — centres already in semantic order stay unchanged.
  2. Simple permutation — anchoring restores semantic ordering; a
     prediction near each semantic anchor returns its intended ID.
  3. All 24 raw-centre permutations resolve to the same four meanings.
  4. labels_ is correctly remapped from raw IDs to semantic IDs.
  5. _save_model() is called only after anchoring succeeds during retrain.
  6. Malformed centre shape / wrong cluster count is rejected explicitly,
     and does not reach _save_model().
  7. A permuted artifact loaded from disk is corrected in memory before
     detect_regime() uses it — and loading never writes back to disk.
  8. Bootstrap initialization uses the canonical semantic anchors and its
     mapping is identity.
  9. Downstream label/description/weight_multipliers stay consistent with
     each other after anchoring (no cross-field inversion).
  10. No production-learning containment flag is touched by any of this.
"""

import pickle

import numpy as np
import pytest

import services.alpha_engine.regime_cluster as rc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_fitted_kmeans(centroid_order):
    """Fit a real sklearn KMeans whose raw cluster IDs land in exactly
    `centroid_order` (a sequence of 4 row-indices into
    SEMANTIC_ANCHOR_CENTROIDS), by using those rows as the init centroids
    with n_init=1. Since the fit data equals the init centroids, the fit
    barely moves them — this gives a real, working .predict() without
    depending on network/production data."""
    from sklearn.cluster import KMeans
    init = rc.SEMANTIC_ANCHOR_CENTROIDS[list(centroid_order)]
    km = KMeans(n_clusters=4, init=init, n_init=1, random_state=42)
    km.fit(init)
    return km


class _FakeModel:
    """Minimal stand-in exposing only what anchor_to_semantic_labels reads
    — used for shape-validation and labels_-remap tests where a real
    sklearn fit isn't needed."""
    def __init__(self, cluster_centers_, labels_=None):
        self.cluster_centers_ = np.array(cluster_centers_, dtype=float)
        if labels_ is not None:
            self.labels_ = np.array(labels_)


@pytest.fixture(autouse=True)
def _reset_regime_cache():
    """Every test gets a clean module-level cache — detect_regime() caches
    for an hour, which would otherwise leak state between tests."""
    rc._cache = None
    rc._cache_expiry = 0
    yield
    rc._cache = None
    rc._cache_expiry = 0


@pytest.fixture
def _tmp_model_path(tmp_path, monkeypatch):
    """Redirect MODEL_PATH to a throwaway file for the duration of a test —
    the repository's real regime_kmeans.pkl must never be read or written
    by this test suite."""
    path = tmp_path / "regime_kmeans_test.pkl"
    monkeypatch.setattr(rc, "MODEL_PATH", str(path))
    return path


# ---------------------------------------------------------------------------
# 1 & 3 — identity and all-permutations
# ---------------------------------------------------------------------------

class TestAnchorToSemanticLabelsPermutations:
    def test_identity_case_centres_already_in_semantic_order(self):
        km = _real_fitted_kmeans([0, 1, 2, 3])
        before = km.cluster_centers_.copy()
        result = rc.anchor_to_semantic_labels(km)
        assert result.is_identity is True
        assert result.mapping == {0: 0, 1: 1, 2: 2, 3: 3}
        np.testing.assert_allclose(km.cluster_centers_, before)

    @pytest.mark.parametrize("perm", list(__import__("itertools").permutations(range(4))))
    def test_all_24_permutations_resolve_to_same_four_semantic_meanings(self, perm):
        km = _real_fitted_kmeans(perm)
        result = rc.anchor_to_semantic_labels(km)
        # After anchoring, row i of cluster_centers_ must be close to
        # SEMANTIC_ANCHOR_CENTROIDS[i], regardless of which raw order the
        # model started in.
        for semantic_id in range(4):
            np.testing.assert_allclose(
                km.cluster_centers_[semantic_id],
                rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id],
                atol=1e-6,
            )
        # And predicting a point at each semantic anchor returns that ID.
        for semantic_id in range(4):
            probe = rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id].reshape(1, -1)
            assert int(km.predict(probe)[0]) == semantic_id
        # mapping must be a true bijection over {0,1,2,3}
        assert sorted(result.mapping.values()) == [0, 1, 2, 3]
        assert sorted(result.mapping.keys()) == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# 2 — simple permutation, with label lookups
# ---------------------------------------------------------------------------

class TestSimplePermutationRestoresLabels:
    def test_reversed_centres_still_yield_correct_labels_after_anchoring(self):
        # Raw cluster 0 was fit on the BEAR_PANIC anchor, raw 3 on BULL_CALM
        # — a full reversal, the worst-case permutation.
        km = _real_fitted_kmeans([3, 2, 1, 0])
        result = rc.anchor_to_semantic_labels(km)
        assert result.is_identity is False
        # raw cluster 0 (fit on BEAR_PANIC=semantic 3) must map to semantic 3
        assert result.mapping[0] == 3
        assert result.mapping[3] == 0

        for semantic_id, name in rc.REGIME_LABELS.items():
            probe = rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id].reshape(1, -1)
            predicted_id = int(km.predict(probe)[0])
            assert predicted_id == semantic_id
            assert rc.REGIME_LABELS[predicted_id] == name


# ---------------------------------------------------------------------------
# 4 — labels_ remapping
# ---------------------------------------------------------------------------

class TestLabelsRemapping:
    def test_labels_are_converted_from_raw_to_semantic_ids(self):
        # Fake model: raw cluster order is a full reversal of the anchors
        # (raw 0 == anchor 3, raw 1 == anchor 2, raw 2 == anchor 1, raw 3 ==
        # anchor 0), with 6 fitted sample labels using raw IDs.
        centers = rc.SEMANTIC_ANCHOR_CENTROIDS[[3, 2, 1, 0]]
        raw_labels = np.array([0, 0, 1, 2, 3, 3])
        model = _FakeModel(centers, labels_=raw_labels)

        result = rc.anchor_to_semantic_labels(model)
        assert result.is_identity is False

        expected_semantic = np.array([result.mapping[r] for r in raw_labels])
        np.testing.assert_array_equal(model.labels_, expected_semantic)
        # raw 0 mapped to semantic 3, raw 3 mapped to semantic 0 (full reversal)
        assert result.mapping[0] == 3
        assert result.mapping[3] == 0
        assert list(model.labels_) == [3, 3, 2, 1, 0, 0]

    def test_identity_case_leaves_labels_untouched(self):
        centers = rc.SEMANTIC_ANCHOR_CENTROIDS.copy()
        raw_labels = np.array([0, 1, 2, 3, 0])
        model = _FakeModel(centers, labels_=raw_labels)
        original_labels_obj = model.labels_

        result = rc.anchor_to_semantic_labels(model)
        assert result.is_identity is True
        # no mutation at all when identity — same array object, same values
        assert model.labels_ is original_labels_obj
        np.testing.assert_array_equal(model.labels_, raw_labels)

    def test_model_without_labels_attribute_does_not_error(self):
        centers = rc.SEMANTIC_ANCHOR_CENTROIDS[[1, 0, 3, 2]]
        model = _FakeModel(centers)  # no labels_ at all
        result = rc.anchor_to_semantic_labels(model)
        assert result.is_identity is False
        assert not hasattr(model, "labels_")


# ---------------------------------------------------------------------------
# 6 — malformed input rejected explicitly
# ---------------------------------------------------------------------------

class TestAnchoringFailureRejectsMalformedInput:
    def test_wrong_cluster_count_raises_value_error(self):
        model = _FakeModel(rc.SEMANTIC_ANCHOR_CENTROIDS[:3])  # only 3 clusters
        with pytest.raises(ValueError):
            rc.anchor_to_semantic_labels(model)

    def test_wrong_feature_dimension_raises_value_error(self):
        model = _FakeModel(rc.SEMANTIC_ANCHOR_CENTROIDS[:, :4])  # 4-dim, not 5
        with pytest.raises(ValueError):
            rc.anchor_to_semantic_labels(model)

    def test_missing_cluster_centers_raises_value_error(self):
        class _NoCenters:
            pass
        with pytest.raises(ValueError):
            rc.anchor_to_semantic_labels(_NoCenters())

    def test_non_2d_cluster_centers_raises_value_error(self):
        model = _FakeModel(np.array([0.1, 0.2, 0.3, 0.4, 0.5]))  # 1-D
        with pytest.raises(ValueError):
            rc.anchor_to_semantic_labels(model)


# ---------------------------------------------------------------------------
# 5 & 6 (integration) — retrain_on_history() save ordering
# ---------------------------------------------------------------------------

class TestRetrainOnHistorySaveOrdering:
    def _history_rows(self, n_per_cluster=10, order=(0, 1, 2, 3)):
        """>=30 synthetic feature snapshots clustered tightly around the
        (possibly permuted) semantic anchors, so a real KMeans fit lands
        cleanly on 4 well-separated clusters."""
        rng = np.random.default_rng(42)
        rows = []
        for raw_id, semantic_id in enumerate(order):
            center = rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id]
            for _ in range(n_per_cluster):
                rows.append((center + rng.normal(scale=0.01, size=5)).tolist())
        return rows

    def test_save_model_called_only_after_successful_anchoring(self, _tmp_model_path, monkeypatch):
        call_order = []
        monkeypatch.setattr(
            "services.alpha_engine.store.get_regime_history",
            lambda: self._history_rows(),
        )
        real_anchor = rc.anchor_to_semantic_labels

        def spy_anchor(model, *a, **kw):
            call_order.append("anchor")
            return real_anchor(model, *a, **kw)

        def spy_save(model):
            call_order.append("save")

        monkeypatch.setattr(rc, "anchor_to_semantic_labels", spy_anchor)
        monkeypatch.setattr(rc, "_save_model", spy_save)

        rc.retrain_on_history()

        assert call_order == ["anchor", "save"], f"expected anchor before save, got {call_order}"

    def test_anchoring_failure_prevents_save_and_preserves_prior_artifact(self, _tmp_model_path, monkeypatch):
        # Write a "prior artifact" to the tmp path so we can prove it survives.
        prior_model = _real_fitted_kmeans([0, 1, 2, 3])
        with open(_tmp_model_path, "wb") as f:
            pickle.dump(prior_model, f)
        prior_bytes = _tmp_model_path.read_bytes()

        monkeypatch.setattr(
            "services.alpha_engine.store.get_regime_history",
            lambda: self._history_rows(),
        )

        def failing_anchor(model, *a, **kw):
            raise ValueError("simulated anchoring failure")

        save_mock_calls = []
        monkeypatch.setattr(rc, "anchor_to_semantic_labels", failing_anchor)
        monkeypatch.setattr(rc, "_save_model", lambda m: save_mock_calls.append(m))

        rc.retrain_on_history()  # must not raise — errors are caught and logged

        assert save_mock_calls == [], "_save_model must not be called when anchoring fails"
        assert _tmp_model_path.read_bytes() == prior_bytes, "prior artifact must be untouched"

    def test_anchoring_failure_does_not_invalidate_cache(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr(
            "services.alpha_engine.store.get_regime_history",
            lambda: self._history_rows(),
        )
        monkeypatch.setattr(
            rc, "anchor_to_semantic_labels",
            lambda model, *a, **kw: (_ for _ in ()).throw(ValueError("boom")),
        )
        sentinel = {"regime_id": 2, "label": "BEAR_CALM"}
        rc._cache = sentinel
        rc._cache_expiry = rc.time.time() + 1000

        rc.retrain_on_history()

        assert rc._cache is sentinel, "cache must be left intact when anchoring fails"

    def test_not_enough_history_returns_before_any_fit(self, _tmp_model_path, monkeypatch):
        monkeypatch.setattr("services.alpha_engine.store.get_regime_history", lambda: [[0.1] * 5] * 5)
        save_mock_calls = []
        monkeypatch.setattr(rc, "_save_model", lambda m: save_mock_calls.append(m))
        rc.retrain_on_history()
        assert save_mock_calls == []


# ---------------------------------------------------------------------------
# 7 — loaded historic artifact corrected in memory, never rewritten to disk
# ---------------------------------------------------------------------------

class TestLoadedArtifactAnchoredInMemoryOnly:
    def test_permuted_artifact_is_corrected_in_memory_on_load(self, _tmp_model_path):
        permuted = _real_fitted_kmeans([2, 3, 0, 1])
        with open(_tmp_model_path, "wb") as f:
            pickle.dump(permuted, f)
        on_disk_before = _tmp_model_path.read_bytes()

        loaded = rc._load_or_init_model()

        # In-memory model now predicts semantic IDs directly.
        for semantic_id in range(4):
            probe = rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id].reshape(1, -1)
            assert int(loaded.predict(probe)[0]) == semantic_id

        # Loading must never write back to disk.
        assert _tmp_model_path.read_bytes() == on_disk_before

    def test_detect_regime_uses_corrected_semantic_id_for_permuted_artifact(self, _tmp_model_path, monkeypatch):
        # raw cluster 0 was fit on BEAR_PANIC (semantic 3) — the worst case:
        # an unanchored load would report BULL_CALM for what is actually a
        # panic regime.
        permuted = _real_fitted_kmeans([3, 2, 1, 0])
        with open(_tmp_model_path, "wb") as f:
            pickle.dump(permuted, f)

        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)

        # global_ctx engineered to produce a feature vector at the
        # BEAR_PANIC anchor: [0.80, 0.20, 0.65, 0.60, 0.20]
        global_ctx = {
            "levels": {"vix": 32.0, "dxy": 103.0, "us10y": 5.0},
            "changes": {"sp500": -3.0, "nifty50": -3.0},
        }
        result = rc.detect_regime(global_ctx)

        assert result["regime_id"] == 3
        assert result["label"] == "BEAR_PANIC"
        assert result["description"] == rc.REGIME_DESCRIPTIONS["BEAR_PANIC"]
        assert result["weight_multipliers"] == rc.REGIME_WEIGHT_MULTIPLIERS["BEAR_PANIC"]

    def test_identity_artifact_produces_no_mutation(self, _tmp_model_path):
        identity_model = _real_fitted_kmeans([0, 1, 2, 3])
        centers_before = identity_model.cluster_centers_.copy()
        with open(_tmp_model_path, "wb") as f:
            pickle.dump(identity_model, f)

        loaded = rc._load_or_init_model()
        np.testing.assert_allclose(loaded.cluster_centers_, centers_before)


# ---------------------------------------------------------------------------
# 8 — bootstrap uses the canonical anchors, identity mapping
# ---------------------------------------------------------------------------

class TestBootstrapUsesCanonicalAnchors:
    def test_bootstrap_creates_identity_mapped_model_and_saves_it(self, _tmp_model_path):
        assert not _tmp_model_path.exists()
        model = rc._load_or_init_model()
        assert model is not None
        for semantic_id in range(4):
            probe = rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id].reshape(1, -1)
            assert int(model.predict(probe)[0]) == semantic_id
        assert _tmp_model_path.exists(), "bootstrap must persist the anchored model"

    def test_bootstrap_reload_from_saved_artifact_stays_identity(self, _tmp_model_path):
        rc._load_or_init_model()  # creates + saves
        reloaded = rc._load_or_init_model()  # loads from disk this time
        for semantic_id in range(4):
            probe = rc.SEMANTIC_ANCHOR_CENTROIDS[semantic_id].reshape(1, -1)
            assert int(reloaded.predict(probe)[0]) == semantic_id


# ---------------------------------------------------------------------------
# 9 — downstream label/description/weight_multipliers consistency
# ---------------------------------------------------------------------------

class TestDownstreamConsistencyAfterAnchoring:
    @pytest.mark.parametrize("perm", [(0, 1, 2, 3), (3, 2, 1, 0), (1, 3, 0, 2), (2, 0, 3, 1)])
    def test_every_semantic_id_maps_to_matching_label_description_multipliers(
        self, _tmp_model_path, monkeypatch, perm
    ):
        permuted = _real_fitted_kmeans(perm)
        with open(_tmp_model_path, "wb") as f:
            pickle.dump(permuted, f)
        monkeypatch.setattr("services.alpha_engine.store.log_regime", lambda *a, **kw: None)

        anchor_probes = {
            "BULL_CALM":     {"levels": {"vix": 13.2, "dxy": 100.0, "us10y": 4.0}, "changes": {"sp500": 1.5, "nifty50": 1.5}},
            "BEAR_PANIC":    {"levels": {"vix": 32.0, "dxy": 103.0, "us10y": 5.0}, "changes": {"sp500": -3.0, "nifty50": -3.0}},
        }
        for expected_label, global_ctx in anchor_probes.items():
            rc._cache = None
            rc._cache_expiry = 0
            result = rc.detect_regime(global_ctx)
            assert result["label"] == expected_label
            assert rc.REGIME_LABELS[result["regime_id"]] == expected_label
            assert result["description"] == rc.REGIME_DESCRIPTIONS[expected_label]
            assert result["weight_multipliers"] == rc.REGIME_WEIGHT_MULTIPLIERS[expected_label]


# ---------------------------------------------------------------------------
# 10 — containment untouched
# ---------------------------------------------------------------------------

class TestContainmentUntouchedByAnchoring:
    def test_no_production_learning_flag_touched(self, _tmp_model_path, monkeypatch):
        from services.alpha_engine import containment
        monkeypatch.delenv(containment.ENV_VAR, raising=False)
        assert containment.is_production_learning_enabled() is False

        km = _real_fitted_kmeans([3, 1, 0, 2])
        rc.anchor_to_semantic_labels(km)
        monkeypatch.setattr(
            "services.alpha_engine.store.get_regime_history",
            lambda: [[0.5] * 5] * 40,
        )
        rc.retrain_on_history()

        # still unset, and containment still reports disabled
        import os
        assert os.environ.get(containment.ENV_VAR) is None
        assert containment.is_production_learning_enabled() is False
