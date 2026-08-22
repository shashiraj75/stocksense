"""
Dependence-aware statistics for the conviction-gate audit.

Motivation
----------
The superseded conviction-accuracy analysis compared a BUY win rate against
an eligible-non-BUY win rate with a naive two-proportion test, and reported
the resulting p-value as if it established an edge. That test assumes the
observations are independent. In `alpha_observations` they are emphatically
not:

  * the same symbol recurs across nearly every run (repeated measures),
  * every row generated on one session date shares that day's market move
    (date clustering — by far the dominant dependence),
  * many market x horizon comparisons were run without correction,
  * subgroups were chosen after seeing the data.

Under those conditions a naive p-value is anticonservative — often by a large
factor. This module therefore provides:

  * `two_proportion_effect` — the effect size (difference in win rate) with a
    naive Wald interval, retained ONLY as a labelled comparison baseline so
    the audit can show how far the naive and dependence-aware answers differ.
  * `date_block_bootstrap` — the primary inference method. Resamples whole
    SESSION DATES with replacement, preserving within-date dependence
    entirely. The resulting interval is the audit's headline uncertainty.
  * `symbol_cluster_jackknife` — a sensitivity check that deletes one symbol
    at a time to show no single ticker drives the result.
  * `holm_correction` — Holm-Bonferroni step-down across a pre-registered
    family of comparisons.
  * `minimum_detectable_effect` / `cluster_adequacy` — power reporting, and a
    fail-closed rule that refuses to emit a significance claim when the number
    of independent clusters (distinct dates) is too small to support one.

Everything is deterministic given `seed`. Only numpy/scipy are used, both
already in backend/requirements.txt — this module adds no new dependency.

IMPORTANT INTERPRETATION RULE enforced by callers: when the naive and the
dependence-aware conclusions disagree, the claim is classified PRELIMINARY or
NOT PROVEN. It is never promoted to PROVEN on the strength of the naive test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

# Minimum number of independent clusters (distinct session dates) required
# before ANY significance claim may be made. Below this, a bootstrap interval
# is itself too unstable to interpret, so the audit reports the effect size
# and explicitly declines to make an inferential claim.
MIN_CLUSTERS_FOR_INFERENCE = 20

DEFAULT_SEED = 20260822
DEFAULT_BOOTSTRAP_DRAWS = 10000


@dataclass
class EffectResult:
    """One comparison's effect size and uncertainty, with an explicit verdict."""

    label: str
    n_a: int
    n_b: int
    rate_a: float | None
    rate_b: float | None
    difference_pp: float | None
    naive_ci_pp: tuple[float, float] | None = None
    naive_p_value: float | None = None
    block_ci_pp: tuple[float, float] | None = None
    block_p_value: float | None = None
    n_clusters: int = 0
    clusters_adequate: bool = False
    minimum_detectable_effect_pp: float | None = None
    inference_permitted: bool = False
    methods_agree: bool | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _rate(successes: int, n: int) -> float | None:
    return (successes / n) if n else None


def two_proportion_effect(
    label: str,
    wins_a: int,
    n_a: int,
    wins_b: int,
    n_b: int,
) -> EffectResult:
    """
    Naive (independence-assuming) two-proportion comparison.

    Retained ONLY as a labelled baseline for comparison against the
    date-blocked result. Its p-value must never be reported on its own as
    evidence of an edge — see this module's docstring.
    """
    rate_a, rate_b = _rate(wins_a, n_a), _rate(wins_b, n_b)
    res = EffectResult(label=label, n_a=n_a, n_b=n_b, rate_a=rate_a, rate_b=rate_b,
                       difference_pp=None)
    if rate_a is None or rate_b is None:
        res.notes.append("empty population — no comparison possible")
        return res
    diff = (rate_a - rate_b) * 100.0
    res.difference_pp = diff

    se = math.sqrt(rate_a * (1 - rate_a) / n_a + rate_b * (1 - rate_b) / n_b)
    if se > 0:
        res.naive_ci_pp = ((diff - 1.959964 * se * 100.0), (diff + 1.959964 * se * 100.0))
        # Pooled two-sided z-test.
        pooled = (wins_a + wins_b) / (n_a + n_b)
        se0 = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
        if se0 > 0:
            z = (rate_a - rate_b) / se0
            res.naive_p_value = math.erfc(abs(z) / math.sqrt(2))
    res.notes.append(
        "NAIVE: assumes independent observations; violated by repeated symbols "
        "and session-date clustering. Baseline only."
    )
    return res


def date_block_bootstrap(
    label: str,
    rows: list[dict],
    *,
    group_key: str = "group",
    group_a: str = "A",
    group_b: str = "B",
    date_key: str = "cluster_date",
    win_key: str = "is_win",
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    seed: int = DEFAULT_SEED,
) -> EffectResult:
    """
    Primary inference: resample whole SESSION DATES with replacement.

    Each bootstrap draw picks `n_dates` dates with replacement and recomputes
    the win-rate difference from every row belonging to the drawn dates. This
    preserves within-date dependence exactly (all rows on a date move
    together), which is the dominant dependence structure in this data.

    The two-sided p-value is the bootstrap proportion of draws whose
    difference falls on the opposite side of zero from the observed
    difference, doubled — a standard percentile-based approximation.

    Deterministic given `seed`.
    """
    import numpy as np

    by_date: dict[object, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r[date_key], []).append(r)
    dates = sorted(by_date, key=str)
    n_clusters = len(dates)

    wins_a = sum(1 for r in rows if r[group_key] == group_a and r[win_key])
    n_a = sum(1 for r in rows if r[group_key] == group_a)
    wins_b = sum(1 for r in rows if r[group_key] == group_b and r[win_key])
    n_b = sum(1 for r in rows if r[group_key] == group_b)

    res = EffectResult(
        label=label, n_a=n_a, n_b=n_b,
        rate_a=_rate(wins_a, n_a), rate_b=_rate(wins_b, n_b),
        difference_pp=None, n_clusters=n_clusters,
    )
    if res.rate_a is None or res.rate_b is None:
        res.notes.append("empty population — no comparison possible")
        return res
    observed = (res.rate_a - res.rate_b) * 100.0
    res.difference_pp = observed
    res.minimum_detectable_effect_pp = minimum_detectable_effect(n_a, n_b,
                                                                 (res.rate_a + res.rate_b) / 2)

    res.clusters_adequate, adequacy_note = cluster_adequacy(n_clusters)
    res.notes.append(adequacy_note)

    if n_clusters < 2:
        res.notes.append("fewer than 2 clusters — bootstrap not defined")
        return res

    rng = np.random.default_rng(seed)
    idx = np.arange(n_clusters)
    diffs: list[float] = []
    for _ in range(draws):
        drawn = rng.choice(idx, size=n_clusters, replace=True)
        wa = na = wb = nb = 0
        for d in drawn:
            for r in by_date[dates[d]]:
                if r[group_key] == group_a:
                    na += 1
                    wa += bool(r[win_key])
                elif r[group_key] == group_b:
                    nb += 1
                    wb += bool(r[win_key])
        if na == 0 or nb == 0:
            continue
        diffs.append((wa / na - wb / nb) * 100.0)

    if len(diffs) < max(100, draws // 10):
        res.notes.append(
            f"only {len(diffs)}/{draws} bootstrap draws produced both groups — "
            "interval unreliable, no inferential claim made"
        )
        return res

    arr = np.sort(np.asarray(diffs))
    res.block_ci_pp = (float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5)))
    share_opposite = float((arr <= 0).mean() if observed > 0 else (arr >= 0).mean())
    res.block_p_value = min(1.0, 2.0 * share_opposite)

    res.inference_permitted = bool(res.clusters_adequate)
    if not res.inference_permitted:
        res.notes.append(
            "FAIL-CLOSED: cluster count below MIN_CLUSTERS_FOR_INFERENCE — "
            "effect size reported, but NO significance claim is permitted."
        )
    res.notes.append(
        "DATE-BLOCKED: resamples whole session dates, preserving within-date "
        "dependence. This is the audit's primary uncertainty estimate."
    )
    return res


def symbol_cluster_jackknife(
    rows: list[dict],
    *,
    group_key: str = "group",
    group_a: str = "A",
    group_b: str = "B",
    symbol_key: str = "symbol",
    win_key: str = "is_win",
) -> dict:
    """
    Delete-one-symbol sensitivity check.

    Recomputes the win-rate difference with each distinct symbol removed in
    turn, and reports the min/max across those refits plus the symbol whose
    removal moves the estimate most. A result that flips sign when a single
    ticker is dropped is not a robust finding, however small its p-value.
    """
    symbols = sorted({r[symbol_key] for r in rows})

    def diff_for(exclude: str | None) -> float | None:
        wa = na = wb = nb = 0
        for r in rows:
            if exclude is not None and r[symbol_key] == exclude:
                continue
            if r[group_key] == group_a:
                na += 1
                wa += bool(r[win_key])
            elif r[group_key] == group_b:
                nb += 1
                wb += bool(r[win_key])
        if na == 0 or nb == 0:
            return None
        return (wa / na - wb / nb) * 100.0

    full = diff_for(None)
    refits = {s: diff_for(s) for s in symbols}
    vals = [v for v in refits.values() if v is not None]
    if not vals or full is None:
        return {"full_difference_pp": full, "n_symbols": len(symbols),
                "note": "insufficient data for jackknife"}
    most = max(refits, key=lambda s: abs((refits[s] if refits[s] is not None else full) - full))
    return {
        "full_difference_pp": full,
        "n_symbols": len(symbols),
        "min_difference_pp": min(vals),
        "max_difference_pp": max(vals),
        "sign_stable": all((v > 0) == (full > 0) for v in vals),
        "most_influential_symbol": most,
        "most_influential_shift_pp": (refits[most] - full) if refits[most] is not None else None,
    }


def holm_correction(p_values: dict[str, float | None], alpha: float = 0.05) -> dict[str, dict]:
    """
    Holm-Bonferroni step-down correction across a family of comparisons.

    Holm is used rather than plain Bonferroni because it is uniformly more
    powerful while making no additional assumption about the dependence
    between the tests — which matters here, since the market x horizon
    comparisons are themselves correlated.

    Entries with a None p-value (no inference permitted) are carried through
    untested and never consume family budget.
    """
    testable = {k: v for k, v in p_values.items() if v is not None}
    m = len(testable)
    out: dict[str, dict] = {
        k: {"raw_p": None, "adjusted_p": None, "reject": False,
            "note": "no p-value — inference not permitted for this comparison"}
        for k, v in p_values.items() if v is None
    }
    if m == 0:
        return out
    ordered = sorted(testable.items(), key=lambda kv: kv[1])
    running = 0.0
    for i, (k, p) in enumerate(ordered):
        adj = min(1.0, max(running, p * (m - i)))
        running = adj
        out[k] = {
            "raw_p": p,
            "adjusted_p": adj,
            "reject": adj < alpha,
            "family_size": m,
            "note": f"Holm-adjusted across {m} pre-registered comparisons",
        }
    return out


def minimum_detectable_effect(n_a: int, n_b: int, base_rate: float,
                              alpha: float = 0.05, power: float = 0.80) -> float | None:
    """
    Smallest win-rate difference (in percentage points) this sample could
    detect at `power`, under the OPTIMISTIC independence assumption.

    Reported so a null result is never mistaken for evidence of no effect.
    Because it assumes independence, the true MDE under date clustering is
    LARGER — callers state that caveat alongside the number.
    """
    if not n_a or not n_b or not (0 < base_rate < 1):
        return None
    z_a, z_b = 1.959964, 0.8416212  # two-sided alpha=0.05, power=0.80
    se = math.sqrt(base_rate * (1 - base_rate) * (1 / n_a + 1 / n_b))
    return (z_a + z_b) * se * 100.0


def cluster_adequacy(n_clusters: int) -> tuple[bool, str]:
    """Whether the independent-cluster count supports an inferential claim."""
    if n_clusters >= MIN_CLUSTERS_FOR_INFERENCE:
        return True, (
            f"{n_clusters} independent session-date clusters "
            f">= {MIN_CLUSTERS_FOR_INFERENCE} — inference permitted."
        )
    return False, (
        f"only {n_clusters} independent session-date clusters "
        f"(< {MIN_CLUSTERS_FOR_INFERENCE}) — effect size may be reported but "
        "NO significance claim is permitted at this cluster count."
    )


def reconcile_methods(result: EffectResult, alpha: float = 0.05) -> EffectResult:
    """
    Set `methods_agree` by comparing the naive and date-blocked verdicts.

    When they disagree, the caller must classify the claim PRELIMINARY or NOT
    PROVEN — never PROVEN. This function records the disagreement; the claim
    level itself is assigned in `conviction_gate_backtest`.
    """
    if result.naive_p_value is None or result.block_p_value is None:
        result.methods_agree = None
        return result
    naive_sig = result.naive_p_value < alpha
    block_sig = result.block_p_value < alpha
    result.methods_agree = naive_sig == block_sig
    if not result.methods_agree:
        result.notes.append(
            "DISAGREEMENT: naive and date-blocked tests reach different "
            "conclusions — claim may not be classified PROVEN."
        )
    return result
