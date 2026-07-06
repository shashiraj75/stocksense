"""
Instrument Type Gate — Intelligence Engine V1, Universe Builder component.

Classifies a (symbol, name) pair from the static stock universe as a plain
common-equity instrument or one of several non-equity categories (ETF,
ETN, preferred share, leveraged/inverse product, SPAC, unit trust,
closed-end fund, index fund). Pure, local, name/symbol keyword matching —
no network calls, no provider dependency, safe to run over the entire
static universe in milliseconds.

This generalizes the same category list daily_picks.py's
_build_us_daily_picks_heuristic_filtered already applies for US Daily
Picks candidate selection (that function is UNCHANGED by this module —
Phase 3A does not touch published Daily Picks selection at all). Two
differences from that function:
  1. It returns a *reason*, not just pass/fail, so shadow telemetry can
     report excluded_counts_by_reason and instrument_type_counts.
  2. It runs for both IN and US — IN currently has no equivalent filter
     at all, so this shadow slice is the first visibility into how many
     non-equity instruments exist in the IN static universe too.

Known limitation (documented, not fixed here — flagged in the Epic 007
Phase 2 design spec as a V2+ improvement): this is name/symbol keyword
matching, not a structured instrument-type field (e.g. yfinance's
`quoteType`). A genuine equity whose name happens to contain one of these
keywords could be misclassified. No such case is known to exist in the
current static universe; this is a structural risk, not an observed bug.
"""
import re
from dataclasses import dataclass

_RE_ETF = re.compile(r"\bETF\b", re.IGNORECASE)
_RE_ETN = re.compile(r"\bETN\b", re.IGNORECASE)

# Order matters — first match wins, same as the existing US heuristic.
INSTRUMENT_TYPE_EQUITY = "equity"
INSTRUMENT_TYPE_PREFERRED = "preferred_share"
INSTRUMENT_TYPE_ETF = "etf"
INSTRUMENT_TYPE_ETN = "etn"
INSTRUMENT_TYPE_LEVERAGED = "leveraged_inverse"
INSTRUMENT_TYPE_INDEX_FUND = "index_fund"
INSTRUMENT_TYPE_SPAC = "spac"
INSTRUMENT_TYPE_UNIT = "unit_trust"
INSTRUMENT_TYPE_CLOSED_END = "closed_end_fund"


@dataclass(frozen=True)
class InstrumentClassification:
    symbol: str
    instrument_type: str
    passed: bool  # True only for INSTRUMENT_TYPE_EQUITY


def classify_instrument(symbol: str, name: str) -> InstrumentClassification:
    """Classify one (symbol, name) pair. Never raises — a malformed/empty
    name is classified as equity (fail-open on the gate, since this is a
    shadow-only slice; excluding on bad data would understate raw_count
    for reasons unrelated to instrument type)."""
    name = name or ""
    n = name.lower()

    if "$" in symbol:
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_PREFERRED, False)
    if _RE_ETF.search(name):
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_ETF, False)
    if _RE_ETN.search(name):
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_ETN, False)
    if " 2x" in n or " 3x" in n or "ultrapro" in n or ("daily" in n and ("bull" in n or "bear" in n)):
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_LEVERAGED, False)
    if "index fund" in n:
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_INDEX_FUND, False)
    if (
        "acquisition corp" in n
        or "acquisition corporation" in n
        or "special purpose acquisition" in n
        or "acquisition inc" in n
    ):
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_SPAC, False)
    if "- unit" in n or n.endswith(" units") or n.endswith("- units"):
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_UNIT, False)
    if "closed-end" in n or "closed end" in n:
        return InstrumentClassification(symbol, INSTRUMENT_TYPE_CLOSED_END, False)

    return InstrumentClassification(symbol, INSTRUMENT_TYPE_EQUITY, True)
