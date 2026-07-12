"""
Learning Alpha Engine remediation, Phase 1 — Production Containment and
Evidence Preservation.

A completed read-only forensic audit (2026-07-12) found that confirmed,
in-zone Daily Picks underperformed manual paper trades, and identified
several confirmed data-integrity defects in the legacy learning path:
historical market-label contamination (fixed same-day by 536fd3d),
~99% raw-[0,1] values in India's stored "z-score" columns, a ~1.52x
duplicate-inflated IC-training join, and completely empty canonical
alpha_observations/factor_ic_history tables. None of that has been
cleaned or retrained on yet — see the audit report for detail.

This module is the single on/off switch for whether the IC engine's
live-data adaptation and the meta-model are trusted for PRODUCTION Daily
Picks ranking. It intentionally does not touch the prediction model,
targets, confidence formula, validation engine, candidate screener, or
portfolio optimizer — those still use their own existing logic.

Default (unset or any value other than exactly "1"): production learning
influence is DISABLED — Daily Picks ranking falls back to the existing
fixed academic-prior IC weights (ic_engine.ACADEMIC_PRIOR_IC), the same
per-market/per-horizon table already used before any live data existed.
IC live-adaptation and the meta-model keep computing in shadow mode (for
diagnostics/observability) — including the IC cache staying invalidated
after each run so shadow values reflect the latest resolved outcomes —
but their output never overwrites what production ranking actually used.

No Learning Alpha IC/meta-model production artifact is trained or
overwritten while containment is active. Regime KMeans remains independent
and unchanged by this containment — it trains on regime_log's global macro
features (VIX/DXY/US10Y, Nifty/S&P trend), not the legacy
predictions/outcomes join this containment quarantines, and always runs
regardless of this flag (see weight_adapter.run_adaptation).

To re-enable production learning once clean canonical data has
accumulated and passed walk-forward validation, set:
    LEARNING_ALPHA_PRODUCTION_ENABLED=1
"""

import os

ENV_VAR = "LEARNING_ALPHA_PRODUCTION_ENABLED"

# Bump this only when a clean, post-quarantine canonical dataset (built on
# alpha_observations, not the contaminated legacy predictions/outcomes join)
# has accumulated and passed walk-forward validation — not before.
LEARNING_DATASET_VERSION = "legacy-quarantined-2026-07-12"

_CONTAINMENT_REASON = (
    "Phase 1 forensic audit (2026-07-12) found confirmed market-label "
    "contamination, ~99% raw-[0,1] values in stored India z-score columns, "
    "a ~1.52x duplicate-inflated IC-training join, and empty canonical "
    "alpha_observations/factor_ic_history tables. Production Daily Picks "
    "ranking is quarantined from this legacy learning data pending a clean "
    "canonical dataset and walk-forward validation."
)


def is_production_learning_enabled() -> bool:
    """
    True only when LEARNING_ALPHA_PRODUCTION_ENABLED is set to exactly "1".
    Unset, "0", "false", or any other value means disabled — the safe default.
    """
    return os.getenv(ENV_VAR, "0") == "1"


def containment_reason() -> str | None:
    """Human-readable reason production learning is quarantined, or None
    when production learning is explicitly enabled."""
    if is_production_learning_enabled():
        return None
    return _CONTAINMENT_REASON


def production_alpha_source() -> str:
    """What actually drove the last/next production ranking computation."""
    return "live_ic_meta_model" if is_production_learning_enabled() else "fixed_academic_prior"
