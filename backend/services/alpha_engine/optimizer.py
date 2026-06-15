"""
Mean-variance portfolio optimizer using scipy.optimize.

Given N stocks with predicted alphas and a return covariance estimate,
find weights w that maximise:

    alpha @ w  −  λ × w @ Σ @ w

subject to:
    Σ w_i = 1          (fully invested)
    0 ≤ w_i ≤ max_w    (long-only, position cap)

Risk aversion λ is increased automatically in bear/panic regimes.
Covariance is estimated with Ledoit-Wolf shrinkage to handle small N.

Falls back to alpha-proportional weights if optimisation fails.
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
        list[float] — portfolio weights summing to 1.0, length == len(alphas)
    """
    from scipy.optimize import minimize

    n = len(alphas)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    a = np.array(alphas, dtype=float)

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

    def neg_utility(w: np.ndarray) -> float:
        return -(float(a @ w) - risk_aversion * float(w @ Σ @ w))

    def neg_utility_grad(w: np.ndarray) -> np.ndarray:
        return -(a - 2.0 * risk_aversion * Σ @ w)

    w0 = np.ones(n) / n
    bounds = [(0.0, max_weight)] * n
    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]

    try:
        res = minimize(
            neg_utility, w0, jac=neg_utility_grad,
            method="SLSQP", bounds=bounds, constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 1000},
        )
        if res.success:
            w = np.clip(res.x, 0.0, max_weight)
            w /= w.sum()
            return [round(float(x), 4) for x in w]
    except Exception as e:
        print(f"[optimizer] SLSQP failed: {e}")

    # Fallback: alpha-proportional weights (softmax-like, positive only)
    pos = np.maximum(a - a.min(), 0)
    if pos.sum() == 0:
        pos = np.ones(n)
    pos = np.minimum(pos, pos.sum() * max_weight)
    pos /= pos.sum()
    return [round(float(x), 4) for x in pos]
