"""
Market-integrity classifier and write-boundary contract (Phase 1A.6).

Why this exists: commit 536fd3d fixed a specific defaulting bug where a
resolved `market` was silently dropped at one log_prediction() call site,
falling through to that function's `market="IN"` default. 13,605 legacy
predictions were mislabeled before the fix landed (2026-07-12). The fix
closed that one call site, but the API boundary itself still allowed
`market` to default silently, and nothing at the write boundary ever
checked whether a symbol could plausibly belong to the claimed market at
all. This module is the single shared choke point for that check — used
at the write boundary (log_prediction), by the outcome resolver/manifest
pipeline (to stop treating permanently-unresolvable mislabeled rows as
real backlog), by the historical-repair planner, and by the two
production metric paths that read the predictions table.

An earlier version of this module used `*, market: str` (required,
keyword-only, no default). That is not sufficient on its own: Python
raises TypeError for an omitted keyword-only argument *before the function
body ever runs*, so no structured event could ever be emitted for the
"market omitted" case, and a plain ValueError from a normalize-market
helper was never actually caught by callers' `except MarketConflictError`
blocks (a distinct exception subclass), so it also reached no structured
logging. This version uses an explicit sentinel (MISSING_MARKET) so
omission is a value this module can see, log, and reject on its own
terms, and a single write-boundary entry point (require_explicit_market)
so every failure category — missing, invalid, or conflicting — is
guaranteed to emit its own structured event before raising.

Classification is based solely on services.stock_universe.py's canonical
US_SYMBOLS/IN_SYMBOLS sets — never on how a ticker "looks". A symbol
absent from both static universes is UNKNOWN, not assumed invalid — many
legitimate symbols (newly-listed names, symbols the curated universe just
hasn't caught up with) are never in these lists and must not be rejected
on that basis alone.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from services.stock_universe import IN_SYMBOLS, US_SYMBOLS

MARKETS = ("IN", "US")

# A dedicated object identity, never a valid market string — used as the
# keyword-only default so an omitted `market` argument is a value this
# module can detect (`is MISSING_MARKET`), not a TypeError Python raises
# before any of this module's code runs.
MISSING_MARKET = object()


class SymbolMarketClass(str, Enum):
    IN_ONLY = "IN_ONLY"
    US_ONLY = "US_ONLY"
    BOTH = "BOTH"
    UNKNOWN = "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# Exception hierarchy — distinct, stable domain exceptions for each failure
# category (never a bare ValueError/TypeError once execution reaches this
# module's own validation logic).
# ─────────────────────────────────────────────────────────────────────────────

class MarketIntegrityError(Exception):
    """Base class for every market-integrity domain exception."""


class MissingMarketContextError(MarketIntegrityError):
    """Raised when market was never supplied at all (omitted, or explicitly
    passed as None) — never inferred as IN or any other value."""

    def __init__(self, symbol: str, writer_context: str):
        self.symbol = symbol
        self.writer_context = writer_context
        super().__init__(
            f"market was not supplied for symbol={symbol!r} (writer={writer_context!r}) — "
            f"no default is inferred for a missing value"
        )


class InvalidMarketError(MarketIntegrityError):
    """Raised when market was supplied but is blank, unsupported, or
    malformed (not one of MARKETS after normalization)."""

    def __init__(self, raw_market, symbol: str, writer_context: str):
        self.raw_market = raw_market
        self.symbol = symbol
        self.writer_context = writer_context
        super().__init__(
            f"market={raw_market!r} is not a supported value for symbol={symbol!r} "
            f"(writer={writer_context!r}) — must be one of {MARKETS}"
        )


class MarketSymbolConflictError(MarketIntegrityError):
    """Raised when a symbol's canonical classification definitively
    contradicts the claimed market — a US_ONLY symbol claimed as IN, or an
    IN_ONLY symbol claimed as US. BOTH and UNKNOWN are never conflicts on
    their own (see is_definitive_conflict)."""

    def __init__(self, symbol: str, market: str, classification: SymbolMarketClass, writer_context: str):
        self.symbol = symbol
        self.market = market
        self.classification = classification
        self.writer_context = writer_context
        super().__init__(
            f"symbol {symbol!r} is classified {classification.value} but was claimed as "
            f"market={market!r} (writer={writer_context!r}) — definitive market/symbol conflict"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Structured event / reason-code contract. Every event Phase 1A.6's
# observability requirement names has a stable constant here — tests assert
# against these, never against prose.
# ─────────────────────────────────────────────────────────────────────────────

EVENT_MARKET_CONTEXT_MISSING = "prediction_market_context_missing"
EVENT_MARKET_INVALID = "prediction_market_invalid"
EVENT_MARKET_SYMBOL_CONFLICT = "prediction_market_symbol_conflict"
EVENT_SYMBOL_CLASSIFICATION_UNKNOWN = "prediction_symbol_classification_unknown"
EVENT_RESOLVER_CONFLICT_SKIPPED = "resolver_market_conflict_skipped"
EVENT_MANIFEST_CONFLICT_SKIPPED = "manifest_market_conflict_skipped"
EVENT_MANIFEST_POPULATION_EXHAUSTED = "manifest_candidate_population_exhausted"
EVENT_MANIFEST_GENERATION_REFUSED = "manifest_generation_refused"
EVENT_PERFORMANCE_CONFLICTS_EXCLUDED = "performance_market_conflicts_excluded"

REASON_MARKET_MISSING = "market_context_missing"
REASON_MARKET_INVALID = "market_value_invalid"
REASON_DEFINITIVE_CONFLICT = "definitive_market_symbol_conflict"
REASON_UNKNOWN_CLASSIFICATION = "symbol_classification_unknown"
REASON_ZERO_USABLE_CANDIDATES = "zero_usable_candidates"
REASON_POPULATION_EXHAUSTED_UNDERFILLED = "source_population_exhausted_underfilled"

log = logging.getLogger(__name__)


def emit_market_event(event: str, message: str, *, level: int = logging.INFO,
                       reason_code: str, writer_context: str | None = None,
                       market: str | None = None, raw_symbol: str | None = None,
                       normalized_symbol: str | None = None,
                       classification: SymbolMarketClass | str | None = None,
                       prediction_id=None, logger: logging.Logger | None = None,
                       **extra_fields) -> None:
    """
    Single shared emitter for every market-integrity structured event. Sets
    discrete `extra` fields on the LogRecord (event, reason_code,
    writer_context, market, raw_symbol, normalized_symbol, classification,
    prediction_id, plus any caller-supplied extra_fields such as counts) so
    tests can assert on record attributes, not just message substrings —
    while still producing a normal human-readable log line. Never logs
    credentials, URLs, connection strings, or full row payloads.
    """
    if isinstance(classification, SymbolMarketClass):
        classification = classification.value
    fields = {
        "event": event,
        "reason_code": reason_code,
        "writer_context": writer_context,
        "market": market,
        "raw_symbol": raw_symbol,
        "normalized_symbol": normalized_symbol,
        "classification": classification,
        "prediction_id": prediction_id,
    }
    fields.update(extra_fields)
    (logger or log).log(level, message, extra=fields)


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

def normalize_symbol(symbol: str) -> str:
    """Canonical form for classification/comparison: uppercase, no leading/
    trailing whitespace, exchange suffix stripped if present (e.g. a caller
    that accidentally passes "AAPL.NS" or "RELIANCE.NS" is still classified
    against the bare ticker — the suffix itself carries no market meaning
    independent of this classification, it's applied only at the price-fetch
    layer in outcome_logger._fetch_return). This does NOT attempt any
    class-share alias transformation — see classify_symbol_detailed for
    that, applied only as a second, narrowly-scoped fallback step."""
    s = (symbol or "").strip().upper()
    for suffix in (".NS", ".BO"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


@dataclass(frozen=True)
class SymbolClassification:
    raw_symbol: str
    canonical_symbol: str
    classification: SymbolMarketClass
    normalization_rule: str  # "none" | "suffix_stripped" | "class_share_alias"


def _class_share_alias(symbol: str) -> str | None:
    """A documented, narrow provider-alias transform: some data providers
    format a US class-share ticker with a dot as the class separator
    (e.g. "BRK.A") where the canonical universe stores it with a hyphen
    ("BRK-A"). This replaces only the LAST '.' with '-' and is only ever
    consulted as a fallback when the untransformed symbol doesn't match
    either canonical universe — never applied blindly, never used to
    "correct" an arbitrary dotted symbol that isn't actually a known
    class-share alias in its transformed form."""
    if "." not in symbol:
        return None
    return symbol[: symbol.rfind(".")] + "-" + symbol[symbol.rfind(".") + 1:]


def classify_symbol_detailed(symbol: str) -> SymbolClassification:
    """
    Classify a symbol against the canonical universes, independent of any
    claimed market. Two-step, deterministic, network-free:

      1. Normalize (case/whitespace/known .NS/.BO exchange suffix) and look
         up the result directly against both universes. Direct match always
         wins over the alias fallback below.
      2. Only if step 1 is UNKNOWN and the normalized symbol contains a
         '.', try the documented class-share alias transform (replace the
         final '.' with '-') and look THAT up. The alias form is accepted
         only if it is actually present in a canonical universe — an
         arbitrary dotted symbol whose alias form is also unknown is left
         as UNKNOWN, never silently rewritten.

    Preserves both the raw input and the canonical (post-normalization,
    post-alias-if-applied) symbol in the result, plus which rule (if any)
    produced the final classification.
    """
    raw = symbol
    direct = normalize_symbol(symbol)
    in_hit = direct in IN_SYMBOLS
    us_hit = direct in US_SYMBOLS
    if in_hit or us_hit:
        classification = (
            SymbolMarketClass.BOTH if (in_hit and us_hit)
            else SymbolMarketClass.IN_ONLY if in_hit
            else SymbolMarketClass.US_ONLY
        )
        rule = "suffix_stripped" if direct != (symbol or "").strip().upper() else "none"
        return SymbolClassification(raw, direct, classification, rule)

    alias = _class_share_alias(direct)
    if alias is not None:
        alias_in_hit = alias in IN_SYMBOLS
        alias_us_hit = alias in US_SYMBOLS
        if alias_in_hit or alias_us_hit:
            classification = (
                SymbolMarketClass.BOTH if (alias_in_hit and alias_us_hit)
                else SymbolMarketClass.IN_ONLY if alias_in_hit
                else SymbolMarketClass.US_ONLY
            )
            return SymbolClassification(raw, alias, classification, "class_share_alias")

    return SymbolClassification(raw, direct, SymbolMarketClass.UNKNOWN, "none")


def classify_symbol(symbol: str) -> SymbolMarketClass:
    """Thin convenience wrapper over classify_symbol_detailed for callers
    (resolver, manifest generation, repair planner) that only need the
    classification enum, not the full detail record."""
    return classify_symbol_detailed(symbol).classification


def _is_conflict(classification: SymbolMarketClass, market: str) -> bool:
    if classification == SymbolMarketClass.US_ONLY and market == "IN":
        return True
    if classification == SymbolMarketClass.IN_ONLY and market == "US":
        return True
    return False


def is_definitive_conflict(symbol: str, market: str) -> bool:
    """True only for the two unambiguous conflict cases. A BOTH symbol is
    valid under either explicit market. An UNKNOWN symbol is never treated
    as a conflict by this check."""
    return _is_conflict(classify_symbol(symbol), market)


# ─────────────────────────────────────────────────────────────────────────────
# Write-boundary contract
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MarketValidationResult:
    raw_symbol: str
    canonical_symbol: str
    market: str
    classification: SymbolMarketClass
    is_conflict: bool
    is_unknown: bool
    normalization_rule: str


def require_explicit_market(symbol: str, market, *, writer_context: str = "unknown") -> MarketValidationResult:
    """
    The single write-boundary validation entry point. `market` should be
    passed either a real market string or left at MISSING_MARKET — every
    failure path emits a structured event (see emit_market_event) BEFORE
    raising, and every check here runs before any database access in every
    caller (log_prediction in both backends never touches its connection
    pool/handle until this returns successfully).

    Raises (never a bare ValueError/TypeError once this function starts
    running):
      MissingMarketContextError — market is MISSING_MARKET or None
      InvalidMarketError        — market is blank/unsupported/malformed
      MarketSymbolConflictError — market definitively contradicts the
                                  symbol's canonical classification

    Never raises for UNKNOWN classification — logged as an observable
    event and returned with is_unknown=True; the caller decides how to
    proceed (in practice: persist as claimed).
    """
    if market is MISSING_MARKET or market is None:
        emit_market_event(
            EVENT_MARKET_CONTEXT_MISSING,
            f"[market_integrity] REJECTED — market not supplied for symbol={symbol!r} "
            f"writer={writer_context!r}",
            level=logging.ERROR, reason_code=REASON_MARKET_MISSING,
            writer_context=writer_context, raw_symbol=symbol,
        )
        raise MissingMarketContextError(symbol, writer_context)

    if not isinstance(market, str) or market.strip().upper() not in MARKETS:
        emit_market_event(
            EVENT_MARKET_INVALID,
            f"[market_integrity] REJECTED — invalid market={market!r} for symbol={symbol!r} "
            f"writer={writer_context!r}",
            level=logging.ERROR, reason_code=REASON_MARKET_INVALID,
            writer_context=writer_context, raw_symbol=symbol,
            market=market if isinstance(market, str) else repr(market),
        )
        raise InvalidMarketError(market, symbol, writer_context)

    normalized_market = market.strip().upper()
    detail = classify_symbol_detailed(symbol)

    if _is_conflict(detail.classification, normalized_market):
        emit_market_event(
            EVENT_MARKET_SYMBOL_CONFLICT,
            f"[market_integrity] REJECTED — symbol={symbol!r} market={normalized_market!r} "
            f"classification={detail.classification.value} writer={writer_context!r}",
            level=logging.ERROR, reason_code=REASON_DEFINITIVE_CONFLICT,
            writer_context=writer_context, market=normalized_market,
            raw_symbol=detail.raw_symbol, normalized_symbol=detail.canonical_symbol,
            classification=detail.classification,
        )
        raise MarketSymbolConflictError(symbol, normalized_market, detail.classification, writer_context)

    is_unknown = detail.classification == SymbolMarketClass.UNKNOWN
    if is_unknown:
        emit_market_event(
            EVENT_SYMBOL_CLASSIFICATION_UNKNOWN,
            f"[market_integrity] UNKNOWN symbol persisted as claimed — symbol={symbol!r} "
            f"market={normalized_market!r} writer={writer_context!r}",
            level=logging.WARNING, reason_code=REASON_UNKNOWN_CLASSIFICATION,
            writer_context=writer_context, market=normalized_market,
            raw_symbol=detail.raw_symbol, normalized_symbol=detail.canonical_symbol,
            classification=detail.classification,
        )

    return MarketValidationResult(
        raw_symbol=detail.raw_symbol,
        canonical_symbol=detail.canonical_symbol,
        market=normalized_market,
        classification=detail.classification,
        is_conflict=False,
        is_unknown=is_unknown,
        normalization_rule=detail.normalization_rule,
    )
