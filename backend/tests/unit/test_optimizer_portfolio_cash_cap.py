"""
DP-020 (governed by DPD-005, DECIDED 2026-07-18 — Portfolio cash and
maximum-position contract): the optimizer's hard per-name position cap
must never be relaxed to force full investment.

Root cause: with a fixed `Σw = 1` equality constraint, a portfolio of N
names at cap `max_weight` where `N * max_weight < 1` (e.g. 2 names at a 40%
cap → 80% max investable) is mathematically infeasible. Both the SLSQP
success path and the alpha-proportional fallback path used to paper over
this by renormalising the final weights back up to sum to 1.0 — silently
pushing every position above its stated cap (e.g. [0.4, 0.4] -> [0.5, 0.5]).

These tests prove, deterministically and without any network/production
data, for candidate counts 1 through 6 at the default 40% cap:
  - no position ever exceeds max_weight (SLSQP path and fallback path);
  - weights sum to min(1, N * max_weight), never renormalised past that;
  - N=2 at a 40% cap yields exactly 40%/40% with 20% left unallocated,
    regardless of relative alpha strength between the two candidates;
  - N=3..6 preserve the prior fully-invested behaviour (cap does not bind);
  - N=1 is characterised (not asserted as part of the DP-020 contract) —
    the daily_picks.py call site never routes a single pick through this
    function, so this branch is unreachable in production.
"""

from unittest.mock import patch

import numpy as np
import pytest

from services.alpha_engine import optimizer as opt

MAX_WEIGHT = 0.40


def _cash(weights: list[float]) -> float:
    return round(1.0 - sum(weights), 4)


def _force_fallback_path(monkeypatch):
    """Make the SLSQP branch raise so optimize() falls through to the
    alpha-proportional fallback — used to prove both paths obey the same
    cap/cash rule."""
    monkeypatch.setattr(
        "scipy.optimize.minimize",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("forced SLSQP failure for fallback-path testing")),
    )


class TestOneCandidateCharacterizationOnly:
    """N=1 is NOT part of the DP-020 output contract — daily_picks.py's
    call site hard-codes 50% directly and never calls optimize() for a
    single pick. Documented here only so a future change to that call site
    doesn't silently break this pre-existing, out-of-scope branch."""

    def test_single_candidate_returns_full_allocation_unchanged(self):
        assert opt.optimize([5.0]) == [1.0]

    def test_zero_candidates_returns_empty_list(self):
        assert opt.optimize([]) == []


class TestTwoCandidatesFortyPercentCapForcesTwentyPercentCash:
    """N=2 * 40% cap = 80% max investable — the exact case DP-020 fixes."""

    @pytest.mark.parametrize("alphas", [
        [1.0, 1.0],      # equal alpha
        [5.0, 1.0],      # skewed alpha
        [1.0, 5.0],      # skewed the other way
        [-2.0, 3.0],     # negative + positive alpha
        [10.0, 9.99],    # nearly tied
    ])
    def test_slsqp_path_caps_both_at_forty_percent_with_twenty_percent_cash(self, alphas):
        weights = opt.optimize(alphas, max_weight=MAX_WEIGHT)
        assert len(weights) == 2
        for w in weights:
            assert w <= MAX_WEIGHT + 1e-6, f"position {w} exceeds cap {MAX_WEIGHT}"
        assert weights == pytest.approx([0.4, 0.4], abs=1e-3)
        assert _cash(weights) == pytest.approx(0.2, abs=1e-3)

    @pytest.mark.parametrize("alphas", [
        [1.0, 1.0],
        [5.0, 1.0],
        [-2.0, 3.0],
    ])
    def test_fallback_path_caps_both_at_forty_percent_with_twenty_percent_cash(self, alphas, monkeypatch):
        _force_fallback_path(monkeypatch)
        weights = opt.optimize(alphas, max_weight=MAX_WEIGHT)
        assert len(weights) == 2
        for w in weights:
            assert w <= MAX_WEIGHT + 1e-6, f"position {w} exceeds cap {MAX_WEIGHT}"
        assert weights == pytest.approx([0.4, 0.4], abs=1e-3)
        assert _cash(weights) == pytest.approx(0.2, abs=1e-3)

    def test_cap_never_relaxed_regardless_of_covariance_source(self):
        # Real (non-identity) covariance path, not just the identity fallback.
        rng = np.random.default_rng(7)
        returns_matrix = rng.normal(scale=0.01, size=(30, 2))
        weights = opt.optimize([2.0, 1.0], returns_matrix=returns_matrix, max_weight=MAX_WEIGHT)
        assert all(w <= MAX_WEIGHT + 1e-6 for w in weights)
        assert sum(weights) <= 1.0 + 1e-6
        assert sum(weights) == pytest.approx(0.8, abs=1e-3)


class TestThreeToSixCandidatesPreserveFullInvestment:
    """Whenever N * max_weight >= 1 (N >= 3 at a 40% cap), the cap does not
    bind against full investment — DP-020 must not change this pre-existing,
    correct behaviour."""

    @pytest.mark.parametrize("n", [3, 4, 5, 6])
    def test_slsqp_path_stays_fully_invested_with_no_cap_violation(self, n):
        alphas = [float(i + 1) for i in range(n)]
        weights = opt.optimize(alphas, max_weight=MAX_WEIGHT)
        assert len(weights) == n
        for w in weights:
            assert w <= MAX_WEIGHT + 1e-6
        assert sum(weights) == pytest.approx(1.0, abs=1e-3)
        assert _cash(weights) == pytest.approx(0.0, abs=1e-3)

    @pytest.mark.parametrize("n", [3, 4, 5, 6])
    def test_fallback_path_stays_fully_invested_with_no_cap_violation(self, n, monkeypatch):
        _force_fallback_path(monkeypatch)
        alphas = [float(i + 1) for i in range(n)]
        weights = opt.optimize(alphas, max_weight=MAX_WEIGHT)
        assert len(weights) == n
        for w in weights:
            assert w <= MAX_WEIGHT + 1e-6
        assert sum(weights) == pytest.approx(1.0, abs=1e-3)
        assert _cash(weights) == pytest.approx(0.0, abs=1e-3)

    def test_dominant_alpha_still_respects_cap_at_n_equals_three(self):
        # Pre-existing regression this file also guards: one alpha wildly
        # dominant must not collapse to 100% in a single name even when the
        # cap permits full investment overall.
        weights = opt.optimize([1000.0, 1.0, 1.0], max_weight=MAX_WEIGHT)
        assert max(weights) <= MAX_WEIGHT + 1e-6
        assert sum(weights) == pytest.approx(1.0, abs=1e-3)


class TestBothSolvePathsAgreeOnCashAcrossAllCandidateCounts:
    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
    def test_slsqp_and_fallback_produce_the_same_cash_fraction(self, n, monkeypatch):
        alphas = [float(i + 1) for i in range(n)]
        slsqp_weights = opt.optimize(list(alphas), max_weight=MAX_WEIGHT)
        _force_fallback_path(monkeypatch)
        fallback_weights = opt.optimize(list(alphas), max_weight=MAX_WEIGHT)
        assert _cash(slsqp_weights) == pytest.approx(_cash(fallback_weights), abs=1e-3)
        expected_cash = round(max(0.0, 1.0 - min(1.0, n * MAX_WEIGHT)), 4)
        assert _cash(slsqp_weights) == pytest.approx(expected_cash, abs=1e-3)
        assert _cash(fallback_weights) == pytest.approx(expected_cash, abs=1e-3)


class TestNoPostOptimizationNormalizationRaisesAboveCap:
    """Regression guard against reintroducing `w /= w.sum()` /
    `pos /= pos.sum()` after clipping — the exact DP-020 bug."""

    def test_slsqp_success_path_never_renormalizes_past_the_cap(self):
        # With exactly 2 candidates at the default 40% cap, a buggy
        # `w /= w.sum()` after clipping would push both to 0.5 — assert
        # that never happens, for a wide spread of alpha inputs.
        for alphas in ([1, 1], [100, 1], [1, 100], [0, 0], [-5, -1]):
            weights = opt.optimize(list(map(float, alphas)), max_weight=MAX_WEIGHT)
            assert max(weights) <= MAX_WEIGHT + 1e-6, (
                f"alphas={alphas} produced weights={weights} exceeding cap {MAX_WEIGHT}"
            )

    def test_fallback_path_never_renormalizes_past_the_cap(self, monkeypatch):
        _force_fallback_path(monkeypatch)
        for alphas in ([1, 1], [100, 1], [1, 100], [0, 0], [-5, -1]):
            weights = opt.optimize(list(map(float, alphas)), max_weight=MAX_WEIGHT)
            assert max(weights) <= MAX_WEIGHT + 1e-6, (
                f"alphas={alphas} produced weights={weights} exceeding cap {MAX_WEIGHT}"
            )
