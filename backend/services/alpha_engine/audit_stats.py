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
    # The audit's headline p-value: the TWO-WAY (date x symbol) cluster
    # permutation p-value. It is NOT a bootstrap tail proportion.
    block_p_value: float | None = None
    n_clusters: int = 0
    clusters_adequate: bool = False
    minimum_detectable_effect_pp: float | None = None
    inference_permitted: bool = False
    methods_agree: bool | None = None
    notes: list[str] = field(default_factory=list)

    # --- dependence-aware additions (calculation version 2.0.0) -----------
    n_clusters_date: int = 0
    n_clusters_symbol: int = 0
    permutation_p_date: float | None = None
    permutation_p_symbol: float | None = None
    permutation_p_two_way: float | None = None
    icc_date: float | None = None
    icc_symbol: float | None = None
    design_effect: float | None = None
    effective_n_a: float | None = None
    effective_n_b: float | None = None
    # Cluster-ADJUSTED MDE. The plain independence MDE is kept separately and
    # is never presented as if it accounted for clustering.
    mde_independence_pp: float | None = None
    mde_cluster_adjusted_pp: float | None = None
    identifiability: str | None = None
    identifiability_reasons: list[str] = field(default_factory=list)
    jackknife: dict | None = None

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
    # NOTE: this function supplies an INTERVAL only. It deliberately does NOT
    # set `block_p_value`: a bootstrap tail proportion is not a null-based
    # p-value, and presenting one as dependence-aware inference was exactly
    # the defect the independent review flagged. The headline p-value comes
    # from `two_way_cluster_permutation`, a real randomization null.
    res.notes.append(
        "INTERVAL ONLY: date-blocked percentile CI. The p-value reported "
        "elsewhere on this result is the two-way cluster PERMUTATION p-value, "
        "not a bootstrap tail proportion."
    )

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


def _observed_difference(rows, group_key, group_a, group_b, win_key):
    wa = na = wb = nb = 0
    for r in rows:
        g = r[group_key]
        if g == group_a:
            na += 1
            wa += bool(r[win_key])
        elif g == group_b:
            nb += 1
            wb += bool(r[win_key])
    if na == 0 or nb == 0:
        return None, na, nb
    return (wa / na - wb / nb) * 100.0, na, nb


def stratified_permutation_test(
    rows: list[dict],
    *,
    stratum_key: str,
    group_key: str = "group",
    group_a: str = "A",
    group_b: str = "B",
    win_key: str = "is_win",
    draws: int = 10000,
    seed: int = DEFAULT_SEED,
) -> dict:
    """
    A REAL null-based randomization test that conditions on one dependence
    dimension.

    Within each stratum (a session date, or a symbol) the GROUP LABELS are
    randomly permuted among that stratum's rows, holding the stratum's A/B
    counts and its set of outcomes fixed. The overall win-rate difference is
    recomputed on each permutation, and the two-sided p-value is the share of
    permutations whose |difference| is at least the observed |difference|.

    Conditioning on the stratum is what makes this dependence-aware: any
    common shock shared by every row in a stratum (the whole market moving on
    one date; one ticker's own drift across many runs) is held FIXED and
    therefore cannot manufacture significance. This is a genuine permutation
    null, not a bootstrap tail proportion relabelled as one.

    A stratum that is entirely group A or entirely group B contributes no
    randomisation — correctly, since it carries no within-stratum contrast.

    Deterministic given `seed`.
    """
    import numpy as np

    observed, n_a, n_b = _observed_difference(rows, group_key, group_a, group_b, win_key)
    strata: dict[object, list[dict]] = {}
    for r in rows:
        if r[group_key] in (group_a, group_b):
            strata.setdefault(r[stratum_key], []).append(r)
    out = {
        "stratum": stratum_key,
        "n_strata": len(strata),
        "n_informative_strata": sum(
            1 for g in strata.values()
            if any(r[group_key] == group_a for r in g)
            and any(r[group_key] == group_b for r in g)
        ),
        "observed_difference_pp": observed,
        "draws": draws,
        "p_value": None,
    }
    if observed is None:
        out["note"] = "a comparison group is empty — no permutation null exists"
        return out
    if out["n_informative_strata"] == 0:
        out["note"] = (
            "no stratum contains both groups — the group label is perfectly "
            "confounded with the stratum, so no within-stratum contrast exists"
        )
        return out

    # Pre-flatten to arrays for speed: per stratum, the outcome vector and the
    # number of group-A rows to draw.
    packed = []
    for g in strata.values():
        wins = np.array([bool(r[win_key]) for r in g], dtype=bool)
        k_a = int(sum(1 for r in g if r[group_key] == group_a))
        packed.append((wins, k_a))

    rng = np.random.default_rng(seed)
    n_at_least = 0
    abs_obs = abs(observed) - 1e-12
    for _ in range(draws):
        wa = na = wb = nb = 0
        for wins, k_a in packed:
            perm = rng.permutation(wins)
            wa += int(perm[:k_a].sum())
            na += k_a
            wb += int(perm[k_a:].sum())
            nb += len(wins) - k_a
        if na == 0 or nb == 0:
            continue
        if abs((wa / na - wb / nb) * 100.0) >= abs_obs:
            n_at_least += 1
    # +1/+1 correction: a permutation p-value is never exactly zero.
    out["p_value"] = (n_at_least + 1) / (draws + 1)
    return out


def two_way_cluster_permutation(
    rows: list[dict],
    *,
    date_key: str = "cluster_date",
    symbol_key: str = "symbol",
    group_key: str = "group",
    group_a: str = "A",
    group_b: str = "B",
    win_key: str = "is_win",
    draws: int = 10000,
    seed: int = DEFAULT_SEED,
) -> dict:
    """
    Dependence-aware inference accounting for BOTH clustering dimensions.

    The data are clustered two ways at once: every row generated on a session
    date shares that day's market move, AND every row for a ticker shares that
    ticker's own behaviour across many runs. A delete-one-symbol jackknife
    does NOT address this — it is a sensitivity check, not an inference
    method — and a date-only method ignores the repeated-symbol dimension
    entirely.

    METHOD. Two stratified permutation tests are run (see
    `stratified_permutation_test`), one conditioning on session date and one
    conditioning on symbol. The reported two-way p-value is the MAXIMUM of the
    two.

    WHY THE MAXIMUM. Each test is valid against the dependence it conditions
    on but ignores the other. Taking the larger (less significant) of the two
    yields a conservative combined statement: a result only clears the bar if
    it survives BOTH dependence structures. This mirrors the standard
    practical guidance for two-way clustered inference (report the most
    conservative of the one-way answers).

    ASSUMPTIONS AND LIMITATIONS — stated because they bound what may be
    claimed:
      * The combination is CONSERVATIVE BUT NOT EXACT. It does not model
        dependence that acts on date and symbol jointly (e.g. a sector-wide
        shock hitting one subset of tickers on one date) beyond what the two
        separate strata capture.
      * Permuting labels within a stratum assumes the label is exchangeable
        within that stratum under the null. For BUY-vs-NON_BUY this is the
        intended null (the signal carries no information about the outcome).
      * With few date clusters the date-stratified test has limited
        resolution; `cluster_adequacy` gates this separately and
        `assess_identifiability` can veto the comparison outright.
      * Both measures are GROSS of transaction costs; no cost model exists.
    """
    by_date = stratified_permutation_test(
        rows, stratum_key=date_key, group_key=group_key, group_a=group_a,
        group_b=group_b, win_key=win_key, draws=draws, seed=seed)
    by_symbol = stratified_permutation_test(
        rows, stratum_key=symbol_key, group_key=group_key, group_a=group_a,
        group_b=group_b, win_key=win_key, draws=draws, seed=seed + 1)
    ps = [t["p_value"] for t in (by_date, by_symbol) if t["p_value"] is not None]
    return {
        "by_date": by_date,
        "by_symbol": by_symbol,
        # Conservative combination: must survive BOTH dependence structures.
        "p_two_way": max(ps) if len(ps) == 2 else None,
        "method": (
            "Two-way cluster permutation: stratified label permutation "
            "conditioning on session date, and again on symbol; the reported "
            "p-value is the MAXIMUM of the two (conservative, not exact)."
        ),
    }


def _point_biserial(rows, value_key, win_key) -> float | None:
    """
    Correlation between a continuous rank score and the binary win outcome.

    Used as the ranking-lift TREND statistic. Quartile monotonicity is
    descriptive only — four ordered win rates can arise by chance far more
    easily than intuition suggests — so a real trend statistic with a
    dependence-aware null is required before ranking skill may be claimed.
    """
    xs = [float(r[value_key]) for r in rows]
    ys = [1.0 if r[win_key] else 0.0 for r in rows]
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def stratified_permutation_trend(
    rows: list[dict],
    *,
    stratum_key: str,
    value_key: str = "rank_percentile",
    win_key: str = "is_win",
    draws: int = 2000,
    seed: int = DEFAULT_SEED,
) -> dict:
    """
    Randomization test for a monotone rank->outcome trend, conditioning on one
    dependence dimension.

    The OUTCOMES are permuted within each stratum (holding the rank scores
    fixed), so any stratum-wide common shock is held constant. The statistic
    is the point-biserial correlation between rank percentile and winning.
    """
    import numpy as np

    usable = [r for r in rows if r.get(value_key) is not None]
    observed = _point_biserial(usable, value_key, win_key)
    strata: dict[object, list[dict]] = {}
    for r in usable:
        strata.setdefault(r[stratum_key], []).append(r)
    out = {"stratum": stratum_key, "n": len(usable), "n_strata": len(strata),
           "observed_correlation": observed, "draws": draws, "p_value": None}
    if observed is None or len(usable) < 10:
        out["note"] = "too few ranked rows for a trend test"
        return out

    rng = np.random.default_rng(seed)
    packed = [(np.array([float(r[value_key]) for r in g]),
               np.array([1.0 if r[win_key] else 0.0 for r in g]))
              for g in strata.values()]
    n_at_least = 0
    abs_obs = abs(observed) - 1e-12
    for _ in range(draws):
        xs_all, ys_all = [], []
        for xs, ys in packed:
            xs_all.append(xs)
            ys_all.append(rng.permutation(ys))
        x = np.concatenate(xs_all)
        y = np.concatenate(ys_all)
        sx, sy = x.std(), y.std()
        if sx == 0 or sy == 0:
            continue
        r = float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))
        if abs(r) >= abs_obs:
            n_at_least += 1
    out["p_value"] = (n_at_least + 1) / (draws + 1)
    return out


def two_way_trend_test(rows, *, date_key="cluster_date", symbol_key="symbol",
                       value_key="rank_percentile", win_key="is_win",
                       draws: int = 2000, seed: int = DEFAULT_SEED) -> dict:
    """Two-way (date and symbol) conservative trend test — max of the two."""
    d = stratified_permutation_trend(rows, stratum_key=date_key, value_key=value_key,
                                     win_key=win_key, draws=draws, seed=seed)
    s = stratified_permutation_trend(rows, stratum_key=symbol_key, value_key=value_key,
                                     win_key=win_key, draws=draws, seed=seed + 1)
    ps = [t["p_value"] for t in (d, s) if t["p_value"] is not None]
    return {"by_date": d, "by_symbol": s,
            "p_two_way": max(ps) if len(ps) == 2 else None,
            "method": ("Two-way permutation trend test on the point-biserial "
                       "correlation between within-run rank percentile and "
                       "winning; outcomes permuted within date strata and "
                       "within symbol strata; the MAXIMUM p-value is reported.")}


def intracluster_correlation(rows: list[dict], *, cluster_key: str,
                             win_key: str = "is_win") -> dict:
    """
    One-way ANOVA estimate of the intracluster correlation of the binary
    outcome, plus the resulting DESIGN EFFECT.

    The design effect DEFF = 1 + (m_bar - 1) * ICC is the factor by which
    clustering inflates the true sampling variance relative to the
    independence assumption. Effective sample size is n / DEFF. This is what
    makes `cluster_adjusted_mde` genuinely cluster-aware instead of an
    independence formula wearing a cluster-aware label.

    ICC is clamped to [0, 1]: a small negative ANOVA estimate is noise around
    zero, and treating it as negative would DEFLATE the design effect and
    overstate power — the exact direction of error this audit must not make.
    """
    groups: dict[object, list[bool]] = {}
    for r in rows:
        groups.setdefault(r[cluster_key], []).append(bool(r[win_key]))
    k = len(groups)
    n = sum(len(v) for v in groups.values())
    out = {"cluster_key": cluster_key, "n_clusters": k, "n": n,
           "mean_cluster_size": (n / k) if k else None,
           "icc": None, "design_effect": None}
    if k < 2 or n <= k:
        out["note"] = "too few clusters to estimate an ICC"
        return out

    grand = sum(sum(v) for v in groups.values()) / n
    ss_between = sum(len(v) * ((sum(v) / len(v)) - grand) ** 2 for v in groups.values())
    ss_within = sum(sum((x - (sum(v) / len(v))) ** 2 for x in v) for v in groups.values())
    ms_between = ss_between / (k - 1)
    ms_within = ss_within / (n - k)
    # m0: the ANOVA "average" cluster size correcting for unequal sizes.
    sum_sq = sum(len(v) ** 2 for v in groups.values())
    m0 = (n - sum_sq / n) / (k - 1)
    if m0 <= 0 or (ms_between + (m0 - 1) * ms_within) == 0:
        out["note"] = "degenerate ANOVA decomposition"
        return out
    icc = (ms_between - ms_within) / (ms_between + (m0 - 1) * ms_within)
    icc = min(1.0, max(0.0, icc))
    m_bar = n / k
    out["icc"] = icc
    out["m0"] = m0
    out["design_effect"] = 1.0 + (m_bar - 1.0) * icc
    return out


def cluster_adjusted_mde(n_a: int, n_b: int, base_rate: float,
                         design_effect: float | None,
                         alpha: float = 0.05, power: float = 0.80) -> dict:
    """
    Minimum detectable effect that ACTUALLY accounts for clustering.

    The independence MDE is computed first, then the effective sample sizes
    n/DEFF are substituted — which inflates the MDE by sqrt(DEFF). Both
    numbers are returned, explicitly labelled, so a NOT_PROVEN conclusion can
    quote a power statement that is true rather than one that merely says it
    is cluster-adjusted.

    When the design effect cannot be estimated the cluster-adjusted MDE is
    returned as None and the caller must say so — it must NEVER fall back to
    the independence number under a cluster-aware label.
    """
    plain = minimum_detectable_effect(n_a, n_b, base_rate, alpha, power)
    out = {
        "mde_independence_pp": plain,
        "design_effect": design_effect,
        "mde_cluster_adjusted_pp": None,
        "effective_n_a": None,
        "effective_n_b": None,
        "note": (
            "The independence MDE UNDERSTATES the detectable effect because "
            "it ignores date and symbol clustering; only the cluster-adjusted "
            "figure may be quoted alongside a NOT_PROVEN conclusion."
        ),
    }
    if plain is None or design_effect is None or design_effect <= 0:
        out["note"] += (
            " Design effect unavailable — NO cluster-adjusted MDE could be "
            "computed, and the independence figure must not be substituted."
        )
        return out
    eff_a = n_a / design_effect
    eff_b = n_b / design_effect
    out["effective_n_a"] = eff_a
    out["effective_n_b"] = eff_b
    if eff_a < 1 or eff_b < 1 or not (0 < base_rate < 1):
        return out
    z_a, z_b = 1.959964, 0.8416212
    se = math.sqrt(base_rate * (1 - base_rate) * (1 / eff_a + 1 / eff_b))
    out["mde_cluster_adjusted_pp"] = (z_a + z_b) * se * 100.0
    return out


# --- Identifiability ------------------------------------------------------
IDENTIFIABLE = "IDENTIFIABLE"
NOT_IDENTIFIABLE = "NOT_IDENTIFIABLE"


def assess_identifiability(
    *,
    n_a: int,
    n_b: int,
    n_clusters_date: int,
    n_clusters_symbol: int,
    n_runs: int | None = None,
    min_runs: int | None = None,
    min_per_group: int = 30,
    extra_reasons: list[str] | None = None,
) -> tuple[str, list[str]]:
    """
    Decide whether a comparison can be estimated AT ALL.

    This runs BEFORE any claim classification. If it returns NOT_IDENTIFIABLE
    the comparison must be reported as NOT_IDENTIFIABLE — never downgraded
    into PRELIMINARY (which asserts suggestive evidence) and never reported as
    NOT_PROVEN (which asserts a real test was run and came back null).
    Conflating "we could not analyse this" with "we analysed this and found
    nothing" is the specific failure this function exists to prevent.
    """
    reasons: list[str] = list(extra_reasons or [])
    if n_a == 0 or n_b == 0:
        reasons.append(
            f"a comparison group is empty (n_a={n_a}, n_b={n_b}) — no contrast exists")
    if n_a < min_per_group or n_b < min_per_group:
        reasons.append(
            f"a comparison group has fewer than {min_per_group} resolved rows "
            f"(n_a={n_a}, n_b={n_b}) — too few to estimate a win-rate difference")
    if n_clusters_date < MIN_CLUSTERS_FOR_INFERENCE:
        reasons.append(
            f"only {n_clusters_date} session-date clusters "
            f"(< {MIN_CLUSTERS_FOR_INFERENCE}) — too few for dependence-aware inference")
    if n_clusters_symbol < MIN_CLUSTERS_FOR_INFERENCE:
        reasons.append(
            f"only {n_clusters_symbol} symbol clusters "
            f"(< {MIN_CLUSTERS_FOR_INFERENCE}) — too few for dependence-aware inference")
    if min_runs is not None and n_runs is not None and n_runs < min_runs:
        reasons.append(
            f"policy era is immature: only {n_runs} distinct runs "
            f"(< {min_runs} required) — nothing may be estimated within it")
    return (NOT_IDENTIFIABLE if reasons else IDENTIFIABLE), reasons


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


def analyse_comparison(
    label: str,
    flat_rows: list[dict],
    *,
    seed: int = DEFAULT_SEED,
    draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    permutation_draws: int = 2000,
    group_key: str = "group",
    date_key: str = "cluster_date",
    symbol_key: str = "symbol",
    win_key: str = "is_win",
    n_runs: int | None = None,
    min_runs: int | None = None,
    min_per_group: int = 30,
    extra_identifiability_reasons: list[str] | None = None,
) -> EffectResult:
    """
    Run ONE A-vs-B comparison through the complete governed pipeline, in the
    order the contract requires:

        1. identifiability   — can this be estimated at all?
        2. symbol jackknife  — computed HERE, BEFORE any classification, so
                               its sign-stability verdict is an INPUT to the
                               classifier rather than a footnote added after.
        3. date-blocked interval
        4. two-way (date x symbol) cluster permutation p-value
        5. ICC / design effect -> cluster-adjusted MDE
        6. naive baseline, for the agreement check only

    The caller never assembles these itself, which is what prevents the
    jackknife from drifting back out of the classification decision.
    """
    dates = {r[date_key] for r in flat_rows if r[group_key] in ("A", "B")}
    symbols = {r[symbol_key] for r in flat_rows if r[group_key] in ("A", "B")}
    wins_a = sum(1 for r in flat_rows if r[group_key] == "A" and r[win_key])
    n_a = sum(1 for r in flat_rows if r[group_key] == "A")
    wins_b = sum(1 for r in flat_rows if r[group_key] == "B" and r[win_key])
    n_b = sum(1 for r in flat_rows if r[group_key] == "B")

    naive = two_proportion_effect(label, wins_a, n_a, wins_b, n_b)
    res = date_block_bootstrap(label, flat_rows, group_key=group_key,
                               date_key=date_key, win_key=win_key,
                               draws=draws, seed=seed)
    res.naive_p_value = naive.naive_p_value
    res.naive_ci_pp = naive.naive_ci_pp
    res.n_clusters_date = len(dates)
    res.n_clusters_symbol = len(symbols)

    # (1) identifiability, decided before anything is claimed.
    status, reasons = assess_identifiability(
        n_a=n_a, n_b=n_b, n_clusters_date=len(dates),
        n_clusters_symbol=len(symbols), n_runs=n_runs, min_runs=min_runs,
        min_per_group=min_per_group,
        extra_reasons=extra_identifiability_reasons)
    res.identifiability = status
    res.identifiability_reasons = reasons

    # (2) jackknife FIRST — an input to classification, not an afterthought.
    res.jackknife = symbol_cluster_jackknife(
        flat_rows, group_key=group_key, symbol_key=symbol_key, win_key=win_key)

    if status == NOT_IDENTIFIABLE:
        res.inference_permitted = False
        res.notes.append(
            "NOT_IDENTIFIABLE — no inference attempted. This is NOT a null "
            "result and NOT preliminary evidence; nothing may be claimed, "
            "not even a direction.")
        return res

    # (4) real null-based dependence-aware p-value.
    perm = two_way_cluster_permutation(
        flat_rows, date_key=date_key, symbol_key=symbol_key,
        group_key=group_key, win_key=win_key,
        draws=permutation_draws, seed=seed)
    res.permutation_p_date = perm["by_date"]["p_value"]
    res.permutation_p_symbol = perm["by_symbol"]["p_value"]
    res.permutation_p_two_way = perm["p_two_way"]
    res.block_p_value = perm["p_two_way"]
    res.notes.append(perm["method"])

    # (5) design effect -> genuinely cluster-adjusted MDE.
    icc_d = intracluster_correlation(flat_rows, cluster_key=date_key, win_key=win_key)
    icc_s = intracluster_correlation(flat_rows, cluster_key=symbol_key, win_key=win_key)
    res.icc_date = icc_d.get("icc")
    res.icc_symbol = icc_s.get("icc")
    deffs = [d for d in (icc_d.get("design_effect"), icc_s.get("design_effect"))
             if d is not None]
    # Conservative: take the LARGER design effect, i.e. the smaller effective
    # sample. Averaging them would understate the MDE.
    res.design_effect = max(deffs) if deffs else None
    base = ((res.rate_a or 0.0) + (res.rate_b or 0.0)) / 2
    mde = cluster_adjusted_mde(n_a, n_b, base, res.design_effect)
    res.mde_independence_pp = mde["mde_independence_pp"]
    res.mde_cluster_adjusted_pp = mde["mde_cluster_adjusted_pp"]
    res.effective_n_a = mde["effective_n_a"]
    res.effective_n_b = mde["effective_n_b"]
    # `minimum_detectable_effect_pp` is the field older readers look at; point
    # it at the CLUSTER-ADJUSTED number so no consumer can accidentally quote
    # the optimistic independence figure as if it were cluster-aware.
    res.minimum_detectable_effect_pp = mde["mde_cluster_adjusted_pp"]
    res.notes.append(mde["note"])

    return reconcile_methods(res)


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
