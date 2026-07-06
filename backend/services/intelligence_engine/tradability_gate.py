"""
Tradability Gate — Intelligence Engine V1, Universe Builder component
(Epic 007 Phase 3B).

Determines whether a security is realistically investable *right now* —
distinct from the Instrument Type Gate (is this a stock at all) and the
Liquidity Gate (can a real order fill without moving the price). A stock
can be a perfectly good, liquid common equity and still fail Tradability
today because it's halted, suspended, delisted, or the data source simply
has nothing current to say about it.

Pure function, no network calls — operates on whatever quote/status data
the caller already has in hand (see the module docstring in shadow_run.py
for why this gate doesn't fetch its own data in Phase 3B's shadow slice).
"""
from dataclasses import dataclass

# Reason codes — every failure is one of these, never a free-text string,
# so telemetry's excluded_counts_by_reason/top_failure_reasons stays a
# small, stable, aggregable set of keys instead of drifting over time.
TRADABILITY_OK = "OK"
TRADABILITY_SUSPENDED = "SUSPENDED"
TRADABILITY_HALTED = "HALTED"
TRADABILITY_DELISTED = "DELISTED"
TRADABILITY_INACTIVE = "INACTIVE"
TRADABILITY_MISSING_QUOTE = "MISSING_QUOTE"
TRADABILITY_MISSING_VOLUME = "MISSING_VOLUME"
TRADABILITY_STALE_PRICING = "STALE_PRICING"
TRADABILITY_INVALID_SYMBOL = "INVALID_SYMBOL"

# How many calendar days old a quote can be before it's considered stale —
# configurable, not hardcoded logic (env override for the same reason the
# existing MIN_MCAP_CR/MIN_MCAP_USD_M constants in daily_picks.py are
# env-configurable: thresholds are operational tuning, not code changes).
import os
STALE_QUOTE_MAX_DAYS = int(os.getenv("TRADABILITY_STALE_QUOTE_MAX_DAYS", 5))


@dataclass(frozen=True)
class TradabilityResult:
    symbol: str
    passed: bool
    reason: str  # TRADABILITY_OK when passed is True


def check_tradability(
    symbol: str,
    *,
    is_valid_symbol: bool = True,
    trading_status: str | None = None,  # e.g. "suspended" | "halted" | "delisted" | "inactive" | "active" | None
    has_quote: bool = True,
    has_volume: bool = True,
    quote_age_days: float | None = None,
) -> TradabilityResult:
    """All parameters are optional/best-effort — a caller with partial data
    (e.g. no trading_status feed at all) should pass what it has and leave
    the rest at the permissive defaults; this gate fails a symbol only on
    positive evidence of a problem, never on the mere absence of a signal
    (fail-open on missing inputs, same principle as Instrument Type Gate's
    empty-name handling)."""
    if not is_valid_symbol:
        return TradabilityResult(symbol, False, TRADABILITY_INVALID_SYMBOL)

    status = (trading_status or "").strip().lower()
    if status == "suspended":
        return TradabilityResult(symbol, False, TRADABILITY_SUSPENDED)
    if status == "halted":
        return TradabilityResult(symbol, False, TRADABILITY_HALTED)
    if status == "delisted":
        return TradabilityResult(symbol, False, TRADABILITY_DELISTED)
    if status == "inactive":
        return TradabilityResult(symbol, False, TRADABILITY_INACTIVE)

    if not has_quote:
        return TradabilityResult(symbol, False, TRADABILITY_MISSING_QUOTE)
    if not has_volume:
        return TradabilityResult(symbol, False, TRADABILITY_MISSING_VOLUME)
    if quote_age_days is not None and quote_age_days > STALE_QUOTE_MAX_DAYS:
        return TradabilityResult(symbol, False, TRADABILITY_STALE_PRICING)

    return TradabilityResult(symbol, True, TRADABILITY_OK)
