"""
Mean-variance portfolio optimizer using scipy.optimize.

Given N stocks with predicted alphas and a return covariance estimate,
find weights w that maximise:

    alpha @ w  −  λ × w @ Σ @ w

subject to:
    Σ w_i = min(1, N × max_w)   (fully invested, capacity-limited)
    0 ≤ w_i ≤ max_w             (long-only, position cap)

Risk aversion λ is increased automatically in bear/panic regimes.
Covariance is estimated with Ledoit-Wolf shrinkage to handle small N.

Falls back to alpha-proportional weights if optimisation fails.

DP-020 / DPD-005 (DECIDED 2026-07-18 — Portfolio cash and maximum-position
contract): when N × max_w < 1 (e.g. 2 picks at a 40% cap → 80% max investable),
the per-name cap is a HARD constraint that is never relaxed to force full
investment. The equality constraint above targets min(1, N × max_w) instead
of a fixed 1.0, so a shortfall of investable capacity is never solved by
letting a position exceed max_w — the shortfall is left unallocated. Callers
that need the actual invested fraction should sum the returned weights;
cash/unallocated is `1.0 - sum(weights)`, always >= 0 (within numeric
tolerance) and never fabricated by scaling weights back up past their cap.
"""

import numpy as np


def _shrunk_covariance(returns_matrix: np.ndarray, shrinkage: float = 0.25) -> np.ndarray:
    """
    Blend sample covariance with diagonal (Ledoit-Wolf style).
    Shrinkage = 0 → pure sample; = 1 → identity scaled by avg variance.
    """
    Σ = np.cov(returns_matrix, rowvar=False)
    μ_var = np.trace(Σ) / Σ.shape[0]
    Σ_target = μ_var * np.eye(Σ.shape[0])
    return (1 - shrinkage) * Σ + shrinkage * Σ_target


def optimize(
    alphas: list[float],
    returns_matrix: np.ndarray | None = None,
    max_weight: float = 0.40,
    risk_aversion: float = 2.0,
    regime_label: str = "BULL_CALM",
) -> list[float]:
    """
    Compute optimal portfolio weights for the selected picks.

    Args:
        alphas         — predicted return signal per stock (higher = more attractive)
        returns_matrix — (T × N) daily returns for covariance (None = use identity)
        max_weight     — maximum position size per stock (default 40%)
        risk_aversion  — higher = more diversified, lower = more concentrated
        regime_label   — adjusts risk aversion for defensive regimes

    Returns:
        list[float] — portfolio weights, length == len(alphas). Sum to
        min(1, N * max_weight) (DP-020): fully invested (sum == 1.0) whenever
        the cap permits it, otherwise capped at max_weight per name with the
        shortfall left as cash/unallocated (never smuggled back onto the
        capped names). Callers needing the cash fraction should compute
        `1.0 - sum(weights)`.
    """
    n = len(alphas)
    if n == 0:
        return []
    if n == 1:
        # Characterization note (DP-020): the daily_picks.py call site never
        # routes a single pick through this function — it hard-codes 50%
        # directly (see _generate_picks_inner) — so this branch is currently
        # unreachable in production and is intentionally left as-is; it is
        # not part of DP-020's output contract.
        return [1.0]

    a = np.array(alphas, dtype=float)

    # DP-020 — hard per-position cap is never relaxed to force full
    # investment. When N * max_weight < 1 (too few names for the cap to
    # allow 100% invested), the fully-invested target itself becomes
    # infeasible; target the maximum investable capacity instead and leave
    # the rest as cash/unallocated.
    target_invested = min(1.0, n * max_weight)

    # Regime-aware risk aversion
    if regime_label == "BULL_VOLATILE":
        risk_aversion *= 1.2
    elif regime_label == "BEAR_CALM":
        risk_aversion *= 1.6
    elif regime_label == "BEAR_PANIC":
        risk_aversion *= 2.5  # very conservative in panic

    # Covariance matrix
    if returns_matrix is not None and returns_matrix.ndim == 2 and returns_matrix.shape[0] >= n + 5:
        try:
            Σ = _shrunk_covariance(returns_matrix)
        except Exception:
            Σ = np.eye(n) * 0.04
    else:
        Σ = np.eye(n) * 0.04   # 20% annual vol assumption

    try:
        from scipy.optimize import minimize

        def neg_utility(w: np.ndarray) -> float:
            return -(float(a @ w) - risk_aversion * float(w @ Σ @ w))

        def neg_utility_grad(w: np.ndarray) -> np.ndarray:
            return -(a - 2.0 * risk_aversion * Σ @ w)

        w0 = np.full(n, target_invested / n)
        bounds = [(0.0, max_weight)] * n
        constraints = [{"type": "eq", "fun": lambda w: w.sum() - target_invested}]

        res = minimize(
            neg_utility, w0, jac=neg_utility_grad,
            method="SLSQP", bounds=bounds, constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 1000},
        )
        if res.success:
            # DP-020: clip to the cap only — never renormalise back up to
            # sum to 1.0. A downward clip cannot push any position above
            # max_weight; any resulting shortfall versus target_invested
            # (numerical noise, or a genuinely infeasible corner case) is
            # simply left unallocated rather than redistributed onto
            # already-capped names.
            w = np.clip(res.x, 0.0, max_weight)
            return [round(float(x), 4) for x in w]
    except Exception as e:
        print(f"[optimizer] SLSQP failed: {e}")

    # Fallback: alpha-proportional weights with iterative max_weight enforcement.
    # The single-pass np.minimum approach fails when one alpha dominates (it caps
    # using pre-cap sum, leaving a single stock at 100% after normalisation).
    pos = np.maximum(a - a.min(), 0)
    if pos.sum() == 0:
        pos = np.ones(n)
    # DP-020: scale to the capacity-limited target, not always 1.0, so the
    # redistribution loop below never has to manufacture more than
    # target_invested worth of demand.
    pos = pos / pos.sum() * target_invested
    # Iterative clipping: redistribute excess weight until all positions respect cap
    for _ in range(20):
        excess = np.maximum(pos - max_weight, 0).sum()
        if excess < 1e-9:
            break
        pos = np.minimum(pos, max_weight)
        uncapped = pos < max_weight
        if uncapped.sum() == 0:
            break
        pos[uncapped] += excess / uncapped.sum()
    # DP-020: final safety clip only — never renormalise back up to sum to
    # 1.0. When every position is already at the cap (uncapped.sum() == 0
    # above), any leftover excess is left unallocated rather than smuggled
    # back onto capped names.
    pos = np.minimum(pos, max_weight)
    return [round(float(x), 4) for x in pos]
