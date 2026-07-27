"""
Configuration for the Market Leadership and Trend Context Layer.

Feature flags follow the repository's established env-var convention
(`os.getenv("X") == "1"`, default OFF — see INTELLIGENCE_ENGINE_SHADOW_ENABLED
precedent in daily_picks.py). Flags are read at call time, never cached at
import time, so tests and operators can toggle without process restarts.

Thresholds are module constants (not env-tunable in v1): every value here is
part of the versioned methodology — changing any of them requires bumping the
corresponding methodology version so persisted snapshots remain attributable.
"""
import os

# ── Versions (locked into every output and persisted snapshot) ───────────────
RS_METHODOLOGY_VERSION = "ml-rs-1.0.0"
GROUP_METHODOLOGY_VERSION = "ml-gl-1.0.0"
TREND_METHODOLOGY_VERSION = "ml-tl-1.0.0"
BREADTH_METHODOLOGY_VERSION = "ml-br-1.0.0"
EXPLANATION_CONTRACT_VERSION = "ml-ex-1.0.0"

# v1 universes are static code constants — not point-in-time membership.
UNIVERSE_VERSION = "ul-2026.07"

CLASSIFICATION_SOURCE = "stocksense_heatmap_v1"
CLASSIFICATION_VERSION = "2026.07"
# The classification source has no effective-date history: leadership outputs
# computed for historical dates are NOT point-in-time safe and say so.
CLASSIFICATION_PIT_SAFE = False

# ── Feature flags (all default OFF) ──────────────────────────────────────────

def engine_enabled() -> bool:
    """Master gate — the module may compute at all."""
    return os.getenv("MARKET_LEADERSHIP_ENGINE_ENABLED") == "1"


def shadow_enabled() -> bool:
    """Shadow snapshot computation + telemetry persistence."""
    return engine_enabled() and os.getenv("MARKET_LEADERSHIP_SHADOW_ENABLED") == "1"


def ui_enabled() -> bool:
    """API may return payloads to the frontend component."""
    return engine_enabled() and os.getenv("MARKET_LEADERSHIP_UI_ENABLED") == "1"


def validation_enabled() -> bool:
    """Shadow-experiment harness may read persisted snapshots."""
    return engine_enabled() and os.getenv("MARKET_LEADERSHIP_VALIDATION_ENABLED") == "1"


def scoring_enabled() -> bool:
    """RESERVED — deliberately consumed by no production code path in this
    release. Scoring influence requires Gate 7 approval; wiring this flag
    into any scorer without that approval is a review-blocking defect."""
    return engine_enabled() and os.getenv("MARKET_LEADERSHIP_SCORING_ENABLED") == "1"


# ── Relative Strength (ml-rs-1.0.0) ─────────────────────────────────────────
RS_WINDOWS = {"rs_1m": 21, "rs_3m": 63, "rs_6m": 126, "rs_12m": 252}
# Equal weights: fewest tuned parameters, most interpretable. Reweighting
# requires walk-forward evidence via the shadow harness + version bump.
RS_WINDOW_WEIGHTS = {"rs_1m": 0.25, "rs_3m": 0.25, "rs_6m": 0.25, "rs_12m": 0.25}
# Minimum usable windows for a composite (must include the 3m window).
RS_MIN_WINDOWS = 2
RS_REQUIRED_WINDOW = "rs_3m"
# A window is usable when >= 90% of its expected sessions have bars.
RS_MIN_WINDOW_COVERAGE = 0.90
# rs_trend dead-band in percentage points (mean 21d rel-ret shift).
RS_TREND_DEADBAND_PP = 0.5

# ── Group leadership (ml-gl-1.0.0) ──────────────────────────────────────────
GROUP_MAX_MEMBER_WEIGHT = 0.20      # cap-weight ceiling per member
GROUP_MIN_SCORED_MEMBERS = 5
GROUP_MIN_MEMBER_COVERAGE = 0.60
GROUP_LEADING_SCORE = 70.0
GROUP_LAGGING_SCORE = 40.0
GROUP_BREADTH_LEADING = 0.50        # share of members with RS pct >= 60
GROUP_IMPROVING_RATIO = 0.50
GROUP_RS_STRONG_PCTL = 60.0
GROUP_NEW_HIGH_BAND = 0.05          # within 5% of 252d high

# ── Trend lifecycle (ml-tl-1.0.0) ───────────────────────────────────────────
TREND_MIN_SESSIONS = 210
TREND_MA_SHORT = 20
TREND_MA_MEDIUM = 50
TREND_MA_LONG = 200
TREND_FLAT_SLOPE_PCT_PER_DAY = 0.02       # |slope| below this = flat
TREND_BASE_BAND_PCT = 7.5                 # price within ±7.5% of long MA
TREND_EARLY_MAX_ABOVE_LONG_MA_PCT = 15.0
TREND_EXTENDED_ABOVE_LONG_MA_PCT = 25.0
TREND_EXTENDED_ABOVE_63D_LOW_PCT = 40.0
TREND_HIGH_VOLUME_MULT = 1.5              # vs 50d average volume
TREND_HIGH_VOLUME_DECLINE_PCT = -2.0
TREND_DISTRIBUTION_MIN_EVENTS = 2         # high-volume declines in 21d
TREND_RECENT_CROSS_SESSIONS = 63

# ── Breadth (ml-br-1.0.0) ───────────────────────────────────────────────────
BREADTH_MIN_COVERAGE = 0.60
BREADTH_MIN_SESSIONS = 210
BREADTH_HEALTHY_ABOVE_200 = 0.60
BREADTH_HEALTHY_ABOVE_50 = 0.50
BREADTH_NARROW_ABOVE_50 = 0.45
BREADTH_RECOVERY_FROM = 0.30
BREADTH_RECOVERY_TO = 0.40
BREADTH_RECOVERY_AD = 1.2
BREADTH_WASHOUT_ABOVE_200 = 0.15
BREADTH_WASHOUT_NL_NH_RATIO = 5.0
BREADTH_NEW_HL_WINDOW = 63
BREADTH_AD_WINDOW = 21
BREADTH_DIRECTION_SESSIONS = 5
