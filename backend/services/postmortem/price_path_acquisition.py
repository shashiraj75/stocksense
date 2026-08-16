"""
Price-path evidence acquisition — Trade Postmortem Sprint 3A, Stages 4/5/12.

Split deliberately into a pure bundle-construction function
(`build_price_path_evidence` — no I/O, exhaustively unit-testable with
hand-built raw bar fixtures) and a thin provider-fetch wrapper
(`fetch_raw_daily_bars` — the only function in this module that touches
yfinance). Callers (the generation pipeline) inject `fetch_raw_daily_bars`
as a parameter into `acquire_price_path_evidence` rather than this module
importing yfinance at call time in the pure path, so tests never need a
real network call.

Stage 12's own explicit requirement — "do not fetch market data inside
the trade-close transaction" — is enforced by construction: nothing in
this module accepts or opens a database connection. The caller (generation
service) is responsible for calling this module OUTSIDE any
`with conn.transaction():` block, then persisting the returned bundle in
its own, separate, short transaction.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from services.postmortem.price_path_evidence import (
    BAR_INTERVAL_DAILY,
    ENTRY_BAR_PARTIAL_UNKNOWN,
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    EXIT_BAR_PARTIAL_UNKNOWN,
    PricePathBar,
    PricePathEvidenceBundle,
    PricePathEvidenceError,
    STATUS_AMBIGUOUS_RESOLUTION,
    STATUS_COMPLETE,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    UNADJUSTED,
    UNKNOWN_ADJUSTMENT,
)
# Stage J3 — every version constant below is now a re-export of
# price_path_identity's own authoritative value, not an independent
# literal. price_path_identity.py has zero imports from this module (or
# any other price_path_* module), so this import direction is safe and
# will never become circular.
from services.postmortem.price_path_identity import (
    BOUNDARY_POLICY_VERSION,
    SOURCE_ID_YFINANCE_DAILY,
    SOURCE_MANIFEST_SCHEMA_VERSION,
    SOURCE_VERSION,
    SYMBOL_NORMALIZATION_VERSION,
)

# Stage D0 terminology correction: yfinance is an unofficial, unlicensed
# third-party dependency this codebase has never run a production-
# authority or licensing review on. Calling it APPROVED_EXTERNAL_SOURCE
# would falsely imply that review happened. EXTERNAL_UNOFFICIAL_DAILY
# states plainly what it is; SOURCE_SCOPE states plainly what it is
# permitted to be used for here — bounded, replayable evidence
# acquisition only, never as a production-authoritative price feed for
# any other part of this codebase (P&L, execution, portfolio valuation).
SOURCE_TYPE = "EXTERNAL_UNOFFICIAL_DAILY"
SOURCE_SCOPE = "BOUNDED_EVIDENCE_ACQUISITION_ONLY"

_MARKET_SUFFIX = {"US": "", "IN": ".NS"}

# Stage D bounding: a single acquisition call must never request an
# unbounded window or return an unbounded number of bars. These are
# evidence-acquisition bounds only — they do not limit how long a paper
# trade may be held; a longer hold is simply evidence-unavailable beyond
# this window rather than silently fetched in full.
MAX_ACQUISITION_WINDOW_DAYS = 1500  # ~4 calendar years of daily sessions
MAX_ACQUISITION_BARS = 1100         # generous upper bound for ~4 years of trading days
FETCH_TIMEOUT_SECONDS = 10


class AcquisitionWindowTooLargeError(ValueError):
    """Raised BEFORE any provider call is attempted — an oversized window
    is rejected at the boundary, never silently truncated or fetched in
    full and truncated after the fact."""


class PriceProviderAcquisitionError(RuntimeError):
    """Sanitized failure envelope (Stage D item 7) — the underlying
    provider exception's raw message/stack (which may embed HTTP
    internals, provider-specific error text, or transient infrastructure
    detail) is never propagated verbatim to callers. `code` is a stable,
    small vocabulary the generation pipeline can branch on (e.g. to mark
    an outbox row retryable) without string-matching provider text."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _provider_symbol(symbol: str, market: str) -> str:
    return f"{symbol.upper()}{_MARKET_SUFFIX.get(market, '')}"


def _require_timezone_aware_trade_timestamps(entry_timestamp, exit_timestamp) -> None:
    """Stage J3B.2 — input-contract guard, not timestamp repair. Rejects
    a naive (or effectively naive) entry/exit timestamp BEFORE any
    `.astimezone()` call, provider I/O, or market-window construction —
    closing the host-timezone-dependent fetch-window gap characterized
    in Stage J3B.1 (a naive timestamp previously reached
    `.astimezone(market_tzinfo)` and could drive a real provider fetch
    with a wrong, host-TZ-dependent window before
    PricePathEvidenceBundle.__post_init__ eventually rejected it).

    Never assumes UTC, IST, ET, or the host timezone; never attaches a
    timezone to a naive value; never converts. A value is rejected as
    effectively naive both when `tzinfo is None` and when a `tzinfo`
    object is present but its own `utcoffset()` returns None (a
    pathological but real distinction per the datetime contract)."""
    for name, ts in (("entry_timestamp", entry_timestamp), ("exit_timestamp", exit_timestamp)):
        if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
            raise PricePathEvidenceError(f"{name} must be timezone-aware")


def _resolve_boundary_policies(
    market: str, entry_timestamp: datetime, exit_timestamp: datetime, limitations: list[str],
) -> tuple[str, str]:
    """Pre-Stage-H Correction 1 — resolves real entry/exit boundary
    policy from services.postmortem.session_boundary's session-aware
    classification instead of the previous hardcoded PARTIAL_UNKNOWN for
    every trade. Appends any limitations produced (including the
    standing EARLY_CLOSE_UNSUPPORTED disclosure) to the caller's shared
    limitations list. Falls back to PARTIAL_UNKNOWN for any market
    session_boundary doesn't recognize, or for the after-close/
    before-open edge cases where the boundary-day bar isn't post-entry
    (or is post-exit) evidence at all — window-shifting to the adjacent
    trading session is not yet wired into requested_window_start/end
    construction, so this stays conservative rather than silently
    mis-widening the window."""
    from services.postmortem import session_boundary

    try:
        entry_result = session_boundary.classify_entry_boundary(market, entry_timestamp)
        exit_result = session_boundary.classify_exit_boundary(market, exit_timestamp)
    except session_boundary.SessionBoundaryError:
        return ENTRY_BAR_PARTIAL_UNKNOWN, EXIT_BAR_PARTIAL_UNKNOWN

    for lim in entry_result["limitations"] + exit_result["limitations"]:
        if lim not in limitations:
            limitations.append(lim)

    entry_policy = entry_result["policy"] or ENTRY_BAR_PARTIAL_UNKNOWN
    exit_policy = exit_result["policy"] or EXIT_BAR_PARTIAL_UNKNOWN
    return entry_policy, exit_policy


def _check_bounded_window(start: date, end: date) -> None:
    if end < start:
        raise AcquisitionWindowTooLargeError(f"end date {end} precedes start date {start}")
    window_days = (end - start).days + 1
    if window_days > MAX_ACQUISITION_WINDOW_DAYS:
        raise AcquisitionWindowTooLargeError(
            f"requested acquisition window is {window_days} days, exceeding the "
            f"{MAX_ACQUISITION_WINDOW_DAYS}-day bound — evidence acquisition is refused, not truncated"
        )


# Pre-Stage-H Correction 2 — pinned, explicit acquisition arguments.
# auto_adjust=False is used deliberately: yfinance's own documented
# contract for auto_adjust=False is that Open/High/Low/Close are the
# RAW, unadjusted exchange-reported values (only a separate "Adj Close"
# column is adjusted). Sprint 3A previously used auto_adjust=True and
# then ASSUMED its composite-adjusted OHLC was "just split-adjusted" —
# that assumption was never actually established by provider
# documentation, and is exactly what this correction removes. Raw OHLC
# under auto_adjust=False needs no adjustment-basis reconciliation claim
# at all to be compared against this codebase's own unadjusted
# paper-trade execution prices — it simply IS unadjusted, by the
# provider's own contract for that argument.
ACQUISITION_AUTO_ADJUST = False
ACQUISITION_BACK_ADJUST = False
ACQUISITION_ACTIONS = True
ACQUISITION_REPAIR = False
ACQUISITION_PREPOST = False  # regular session only — no pre/post-market bars
ACQUISITION_TIMEZONE_BEHAVIOR = "provider_local_exchange_timezone_normalized_to_date_only"

# Stage 1 hardening: yf.Ticker.history() has no threads/progress/ignore_tz
# parameters — those belong to yf.download(), which this module does not
# use (Ticker.history() is a single-symbol call by construction, so the
# MultiIndex-column concern yf.download() can introduce for multi-symbol
# requests does not arise here either). Documented explicitly rather than
# passed as no-op arguments that would silently do nothing.
_NOT_APPLICABLE_TO_TICKER_HISTORY = ("threads", "progress", "ignore_tz")


def _provider_library_version() -> str:
    try:
        import yfinance as yf
        return getattr(yf, "__version__", "unknown")
    except Exception:
        return "unknown"


def fetch_raw_daily_bars(
    provider_symbol: str, start: date, end: date, *, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> list[dict]:
    """The one function in this module that calls yfinance. Returns a
    list of {date, open, high, low, close, volume, adj_close, dividend}
    dicts, raw and unvalidated — build_price_path_evidence does all
    validation. Uses the pinned, explicit acquisition arguments above
    (auto_adjust=False, actions=True) rather than any implicit default,
    per Pre-Stage-H Correction 2. Provider/network errors are caught
    here and re-raised as a sanitized PriceProviderAcquisitionError
    (Stage D item 7/8) — the caller never sees raw provider exception
    internals, and this function never returns a partially-built or
    fabricated bar list on failure."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(provider_symbol)
        # yfinance's own convention: start is inclusive, end is EXCLUSIVE.
        # Widening by one day here is only how the exit session gets INTO
        # the provider response at all — it is never allowed to let a
        # LATER session leak into the persisted evidence. That exact
        # exclusion is enforced downstream in build_price_path_evidence's
        # `if d < entry_date or d > exit_date: continue` filter, which
        # runs against the ORIGINAL (non-widened) exit date — this
        # function's own [start, end] widening is purely a provider-call
        # convenience the caller must never mistake for the persisted
        # window itself.
        df = ticker.history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=ACQUISITION_AUTO_ADJUST, back_adjust=ACQUISITION_BACK_ADJUST,
            actions=ACQUISITION_ACTIONS, repair=ACQUISITION_REPAIR, prepost=ACQUISITION_PREPOST,
            timeout=timeout,
        )
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            # Defensive: yf.Ticker.history() is single-symbol by
            # construction and should never return a MultiIndex, but if a
            # future yfinance version changes that, fail loudly rather
            # than silently misreading columns.
            raise PriceProviderAcquisitionError(
                "PROVIDER_UNEXPECTED_COLUMN_SHAPE",
                f"expected a single-symbol flat column index for {provider_symbol}, got a MultiIndex",
            )
        bars = []
        for idx, row in df.iterrows():
            bar_date = idx.tz_localize(None).date() if hasattr(idx, "tz_localize") and idx.tzinfo is not None else (
                idx.date() if hasattr(idx, "date") else idx
            )
            adj_close = float(row["Adj Close"]) if "Adj Close" in row and row["Adj Close"] == row["Adj Close"] else None
            dividend = float(row["Dividends"]) if "Dividends" in row and row["Dividends"] == row["Dividends"] else 0.0
            bars.append({
                "date": bar_date,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if "Volume" in row and row["Volume"] == row["Volume"] else None,
                "adj_close": adj_close,
                "dividend": dividend,
            })
    except PriceProviderAcquisitionError:
        raise
    except Exception as exc:
        raise PriceProviderAcquisitionError(
            "PROVIDER_FETCH_FAILED", f"daily bar acquisition failed for {provider_symbol}"
        ) from exc
    if len(bars) > MAX_ACQUISITION_BARS:
        raise PriceProviderAcquisitionError(
            "PROVIDER_RESPONSE_TOO_LARGE",
            f"provider returned {len(bars)} bars, exceeding the {MAX_ACQUISITION_BARS}-bar bound",
        )
    return bars


def fetch_dividend_events(
    provider_symbol: str, start: date, end: date, *, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> list[date]:
    """Returns session dates on which a dividend was paid within
    [start, end]. Recorded in the manifest for completeness (Correction
    2's own requirement) — unlike a split, a dividend does not distort
    RAW (auto_adjust=False) OHLC values, so its presence is disclosed
    but does not by itself force UNKNOWN_ADJUSTMENT the way a split
    does."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(provider_symbol)
        dividends = ticker.dividends
        events = []
        for ts, amount in dividends.items():
            d = ts.date() if hasattr(ts, "date") else ts
            if start <= d <= end and amount and amount != 0.0:
                events.append(d)
    except Exception as exc:
        raise PriceProviderAcquisitionError(
            "PROVIDER_FETCH_FAILED", f"dividend-event acquisition failed for {provider_symbol}"
        ) from exc
    return events


def fetch_split_events(
    provider_symbol: str, start: date, end: date, *, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> list[date]:
    """Returns session dates on which a stock split occurred within
    [start, end], per yfinance's own corporate-action history. Used only
    to decide whether SPLIT_ADJUSTED bars remain safely comparable to
    this codebase's own UNADJUSTED paper-trade execution prices — never
    to adjust prices itself (this module never invents a split ratio
    reconciliation; a split in-window means the safe, honest answer is
    LIMITED/UNAVAILABLE evidence, not a "corrected" number). Errors are
    sanitized identically to fetch_raw_daily_bars."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(provider_symbol)
        splits = ticker.splits
        events = []
        for ts, ratio in splits.items():
            d = ts.date() if hasattr(ts, "date") else ts
            if start <= d <= end and ratio and ratio != 1.0:
                events.append(d)
    except Exception as exc:
        raise PriceProviderAcquisitionError(
            "PROVIDER_FETCH_FAILED", f"split-event acquisition failed for {provider_symbol}"
        ) from exc
    return events


def _count_requested_weekdays(entry_date: date, exit_date: date) -> int:
    """Mon-Fri count within [entry_date, exit_date], inclusive. Deliberately
    does NOT subtract market holidays — this codebase's holiday calendars
    (services.market_hours.NSE_EXTRA_HOLIDAYS / _us_fixed_holidays) are
    internal to that module and scoped to "is trading open right now"
    checks, not a general date-range session-counter, and reusing them
    here would silently couple this manifest's honesty to a calendar that
    itself needs annual manual refresh. Naming this field
    `requested_trading_weekday_count` rather than "expected trading
    sessions" is the explicit, honest boundary: weekends are excluded,
    holidays are not — never claim more precision than that."""
    count = 0
    d = entry_date
    one_day = timedelta(days=1)
    while d <= exit_date:
        if d.weekday() < 5:
            count += 1
        d += one_day
    return count


def _compute_manifest_integrity_hash(manifest: dict) -> str:
    """Separate from _compute_evidence_hash (bars only) — this covers the
    manifest's own provenance/configuration fields, so a manifest could be
    tampered with or corrupted independently of the bar data and still be
    caught. Excludes ONLY `manifest_integrity_hash` itself (the field
    being computed cannot hash itself) — every other field, INCLUDING
    `unresolved_basis_limitations`, is covered. Callers MUST call this
    only through finalize_source_manifest, after every limitation for the
    return path in question is already known — never on a still-mutable
    manifest."""
    hashable = {k: v for k, v in manifest.items() if k != "manifest_integrity_hash"}
    canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def finalize_source_manifest(source_manifest: dict, final_limitations: list[str]) -> dict:
    """Stage J Final-Closure-Correction — the one place
    `manifest_integrity_hash` is ever computed. Must be called exactly
    once per return path, only after every limitation applicable to that
    path (dividend/split disclosure, boundary-policy limitations, the
    no-bars message, the partial-window gap message) has already been
    appended to `final_limitations` — never before.

    Returns a NEW dict (the input `source_manifest` is never mutated) so
    a caller cannot accidentally keep touching the pre-finalization
    object after this returns. `unresolved_basis_limitations` becomes an
    immutable SNAPSHOT copy of `final_limitations` — not the same list
    object a caller might still hold a mutable reference to — so a
    finalized manifest can never silently change out from under its own
    hash. This is also what guarantees the bundle's own `limitations`
    field and the manifest's `unresolved_basis_limitations` always
    contain identical final content: both are built from the exact same
    `final_limitations` list at the exact same point in time."""
    finalized = dict(source_manifest)
    finalized["unresolved_basis_limitations"] = list(final_limitations)
    finalized.pop("manifest_integrity_hash", None)
    finalized["manifest_integrity_hash"] = _compute_manifest_integrity_hash(finalized)
    return finalized


def verify_source_manifest_integrity(manifest: dict) -> bool:
    """Deterministic CORRUPTION/DRIFT detection, not cryptographic
    authentication. This recomputes an ordinary SHA-256 over every field
    except `manifest_integrity_hash` and compares against the stored
    value — it proves the manifest's OWN fields are still internally
    self-consistent with its own recorded hash (catches accidental
    corruption, a partial write, a manual SQL edit that touched fields
    but not the hash, or a legacy row from before this hash existed).
    It provides NO protection against an attacker capable of rewriting
    BOTH the manifest content AND its stored hash together — that would
    require a keyed MAC or a hash computed and verified against a
    value the row's own writer cannot also control, neither of which
    this function claims to be. Returns False (never raises) for a
    manifest missing the hash field entirely — an unhashed manifest
    cannot be verified, not trivially "valid". Verifies correctly
    against a manifest that has round-tripped through PostgreSQL JSONB
    storage (dict -> JSON -> dict), since it operates on whatever dict
    it is actually given, not a cached in-memory original."""
    stored = manifest.get("manifest_integrity_hash")
    if not stored:
        return False
    return _compute_manifest_integrity_hash(manifest) == stored


# --- Stage J Final Semantic Reconciliation, Stage 2 --------------------
# A manifest hash a caller never checks is not a production integrity
# control, only a testable helper. This makes it load-bearing: before
# ANY compatible persisted evidence row is used for replay, its manifest
# must pass this validation, or the replay is refused fail-closed
# (never silently repaired, never re-derived from current data).
MANIFEST_INTEGRITY_VIOLATION = "MANIFEST_INTEGRITY_VIOLATION"

# Every field a persisted manifest must carry for replay to be
# considered — a legacy row persisted before Stage J-F3 added these
# fields will be missing several of these and correctly fails closed,
# never treated as compatible merely because the old four-part
# (paper_trade_id, evidence_bundle_version, source_id, source_version)
# identity still matches.
_REQUIRED_MANIFEST_FIELDS_FOR_REPLAY = (
    "source_manifest_schema_version", "source_id", "source_type", "source_scope",
    "production_authoritative", "source_version", "provider", "provider_library_version",
    "interval", "auto_adjust", "back_adjust", "actions", "repair", "prepost",
    "timezone_behavior", "raw_ohlc_basis_claim", "adjusted_close_available",
    "split_event_manifest", "dividend_event_manifest", "provider_symbol", "trade_symbol",
    "symbol_normalization_version", "market", "acquisition_mode",
    "provider_request_start", "provider_exclusive_request_end", "end_widening_reason",
    "boundary_policy_version", "requested_trading_weekday_count",
    "unresolved_basis_limitations", "manifest_integrity_hash",
)

_SUPPORTED_SOURCE_MANIFEST_SCHEMA_VERSIONS = frozenset({SOURCE_MANIFEST_SCHEMA_VERSION})
_SUPPORTED_SYMBOL_NORMALIZATION_VERSIONS = frozenset({SYMBOL_NORMALIZATION_VERSION})
_SUPPORTED_BOUNDARY_POLICY_VERSIONS = frozenset({BOUNDARY_POLICY_VERSION})


@dataclass(frozen=True)
class ManifestCompatibilityDecision:
    """WHETHER a persisted evidence row's manifest may be trusted for
    replay. Distinct from HistoricalEvidenceAcquisitionDecision (WHERE
    evidence came from, price_path_evidence_decision.py) and
    HistoricalEvidenceQualityDecision (WHETHER the bundle's own data is
    usable) — this is a THIRD, prior gate: is the manifest itself
    internally consistent and trustworthy at all, checked before either
    of those other two decisions ever consult the row's content."""

    compatible: bool
    reason_code: str | None
    detail: str = ""


def validate_manifest_compatibility(evidence) -> ManifestCompatibilityDecision:
    """Pure, no I/O. `evidence` is an already-persisted evidence row
    (duck-typed: anything exposing .source_manifest, .source_id,
    .source_type, .source_version, .provider_symbol, .market, .symbol,
    .bar_interval, .requested_window_start — both
    PricePathEvidenceBundle and price_path_store's PersistedPricePathEvidence
    satisfy this). Checked BEFORE classify_acquired_evidence ever runs on
    a replay path — a manifest that fails this is never even classified
    for quality, since its own provenance cannot be trusted enough to
    ask that question."""
    manifest = evidence.source_manifest
    if not isinstance(manifest, dict):
        return ManifestCompatibilityDecision(False, MANIFEST_INTEGRITY_VIOLATION, "source_manifest is not a mapping")

    for field in _REQUIRED_MANIFEST_FIELDS_FOR_REPLAY:
        if field not in manifest:
            return ManifestCompatibilityDecision(
                False, MANIFEST_INTEGRITY_VIOLATION, f"missing required manifest field: {field}"
            )

    if not verify_source_manifest_integrity(manifest):
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest_integrity_hash failed verification"
        )

    if manifest["source_manifest_schema_version"] not in _SUPPORTED_SOURCE_MANIFEST_SCHEMA_VERSIONS:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "unsupported source_manifest_schema_version"
        )
    if manifest["symbol_normalization_version"] not in _SUPPORTED_SYMBOL_NORMALIZATION_VERSIONS:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "unsupported symbol_normalization_version"
        )
    if manifest["boundary_policy_version"] not in _SUPPORTED_BOUNDARY_POLICY_VERSIONS:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "unsupported boundary_policy_version"
        )

    # Pinned acquisition arguments — never varies by trade; a manifest
    # declaring anything else is either corrupted or from a acquisition
    # configuration this codebase no longer runs.
    pinned = (
        ("auto_adjust", ACQUISITION_AUTO_ADJUST), ("back_adjust", ACQUISITION_BACK_ADJUST),
        ("actions", ACQUISITION_ACTIONS), ("repair", ACQUISITION_REPAIR), ("prepost", ACQUISITION_PREPOST),
    )
    for key, expected in pinned:
        if manifest[key] != expected:
            return ManifestCompatibilityDecision(
                False, MANIFEST_INTEGRITY_VIOLATION, f"manifest {key}={manifest[key]!r} does not match pinned {expected!r}"
            )

    # Fixed-value contract checks (Stage J3, Stage 5 items 10-12) — these
    # never vary by trade or acquisition; a manifest declaring anything
    # else is corrupted or from a policy this codebase no longer runs.
    if manifest.get("production_authoritative") is not False:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest production_authoritative is not exactly False"
        )
    if manifest.get("source_scope") != SOURCE_SCOPE:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest source_scope does not equal BOUNDED_EVIDENCE_ACQUISITION_ONLY"
        )
    if manifest.get("source_type") != SOURCE_TYPE:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest source_type does not equal EXTERNAL_UNOFFICIAL_DAILY"
        )

    # Identity fields must match the evidence row's OWN top-level
    # fields — never re-derived from current data (Stage 2 item 6); this
    # only ever compares the manifest against the SAME row's own other
    # columns, both already persisted at acquisition time.
    identity_checks = (
        ("source_id", evidence.source_id), ("source_type", evidence.source_type),
        ("source_version", evidence.source_version), ("provider_symbol", evidence.provider_symbol),
        ("market", evidence.market), ("interval", evidence.bar_interval),
    )
    for key, expected in identity_checks:
        if manifest.get(key) != expected:
            return ManifestCompatibilityDecision(
                False, MANIFEST_INTEGRITY_VIOLATION, f"manifest {key} does not match evidence row's own {key}"
            )
    if manifest.get("trade_symbol") != evidence.symbol.upper():
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest trade_symbol does not match evidence symbol"
        )
    if manifest.get("provider_request_start") != evidence.requested_window_start.isoformat():
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION,
            "manifest provider_request_start does not match evidence requested_window_start",
        )

    # Stage 5 item 7 — provider-exclusive request end must equal
    # requested_window_end + one calendar day, the exact widening
    # fetch_raw_daily_bars actually applies (see finalize_source_manifest's
    # own end_widening_reason text) — never a different, unexplained gap.
    expected_exclusive_end = (evidence.requested_window_end + timedelta(days=1)).isoformat()
    if manifest.get("provider_exclusive_request_end") != expected_exclusive_end:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION,
            "manifest provider_exclusive_request_end does not equal requested_window_end + 1 day",
        )

    # Stage 5 item 8 — split/dividend dates must be valid ISO dates
    # inside the requested window — never a date outside the window the
    # evidence claims to cover, and never an unparseable value.
    for manifest_key in ("split_event_manifest", "dividend_event_manifest"):
        event_dates = manifest.get(manifest_key)
        if not isinstance(event_dates, list):
            return ManifestCompatibilityDecision(
                False, MANIFEST_INTEGRITY_VIOLATION, f"manifest {manifest_key} is not a list"
            )
        for raw_date in event_dates:
            try:
                parsed = date.fromisoformat(raw_date)
            except (TypeError, ValueError):
                return ManifestCompatibilityDecision(
                    False, MANIFEST_INTEGRITY_VIOLATION, f"manifest {manifest_key} contains a non-ISO-date value"
                )
            if not (evidence.requested_window_start <= parsed <= evidence.requested_window_end):
                return ManifestCompatibilityDecision(
                    False, MANIFEST_INTEGRITY_VIOLATION,
                    f"manifest {manifest_key} contains a date outside the requested window",
                )

    # Stage 5 item 9 — manifest limitations must equal the evidence
    # row's own persisted limitations exactly (both are built from the
    # SAME final_limitations list at finalize_source_manifest time — see
    # that function's own docstring; a divergence here means the row was
    # corrupted after persistence, independent of the hash check above).
    if list(manifest.get("unresolved_basis_limitations", [])) != list(evidence.limitations):
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION,
            "manifest unresolved_basis_limitations does not match evidence row's own limitations",
        )

    # adjusted_close_available must be a real bool, not a truthy stand-in
    # (Stage 5's "type" requirement for this field).
    if not isinstance(manifest.get("adjusted_close_available"), bool):
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest adjusted_close_available is not a boolean"
        )
    if not isinstance(manifest.get("provider_library_version"), str) or not manifest["provider_library_version"]:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest provider_library_version is missing or not a string"
        )

    # Stage J3A, Stage 5 — semantic-VALUE checks (not merely presence)
    # for fields whose exact pinned value matters, not just that a value
    # exists.
    fixed_semantic_values = (
        ("provider", "yfinance"),
        ("timezone_behavior", ACQUISITION_TIMEZONE_BEHAVIOR),
        ("raw_ohlc_basis_claim", "PROVIDER_RETURNED_UNADJUSTED_OHLC"),
        ("acquisition_mode", "auto_adjust_false"),
    )
    for key, expected in fixed_semantic_values:
        if manifest.get(key) != expected:
            return ManifestCompatibilityDecision(
                False, MANIFEST_INTEGRITY_VIOLATION, f"manifest {key}={manifest.get(key)!r} does not equal pinned {expected!r}"
            )

    weekday_count = manifest.get("requested_trading_weekday_count")
    if not isinstance(weekday_count, int) or isinstance(weekday_count, bool) or weekday_count < 0:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION, "manifest requested_trading_weekday_count is not a non-negative integer"
        )
    expected_weekday_count = _count_requested_weekdays(evidence.requested_window_start, evidence.requested_window_end)
    if weekday_count != expected_weekday_count:
        return ManifestCompatibilityDecision(
            False, MANIFEST_INTEGRITY_VIOLATION,
            "manifest requested_trading_weekday_count does not match the deterministic count for the requested window",
        )

    return ManifestCompatibilityDecision(True, None)


# Stage J3A — a compatible evidence row's own manifest can be perfectly
# self-consistent and STILL be the wrong row: same paper_trade_id/user_id
# (the SQL lookup's own scope) but internally corrupted/substituted to
# describe a different market, symbol, or window than the CURRENT
# persisted paper trade actually has. validate_manifest_compatibility
# alone cannot catch this — it never consults anything about "the
# current trade," only the row's own internal agreement. This is a
# distinct reason code (not MANIFEST_INTEGRITY_VIOLATION) so the two
# failure classes stay diagnosable apart, even though both fail closed
# identically.
REPLAY_TRADE_CONTEXT_MISMATCH = "REPLAY_TRADE_CONTEXT_MISMATCH"


def validate_replay_compatibility(
    evidence, *, expected_trade_id: int, expected_user_id: str, expected_symbol: str, expected_market: str,
    expected_market_timezone: str, expected_entry_timestamp: datetime, expected_exit_timestamp: datetime,
    expected_requested_window_start: date, expected_requested_window_end: date,
) -> ManifestCompatibilityDecision:
    """Pure, no I/O. Superset of validate_manifest_compatibility: first
    runs every internal-consistency/hash/schema-version check that
    function already performs, THEN compares the row against the
    CURRENT persisted paper trade's own facts — never the current stock
    universe, never a re-derived/inferred symbol rename or delisting,
    only the trade's own already-persisted columns the caller passes in.
    A row that passes validate_manifest_compatibility but fails here is
    exactly the "internally consistent, wrong trade" case Stage J3A was
    opened to close."""
    manifest_decision = validate_manifest_compatibility(evidence)
    if not manifest_decision.compatible:
        return manifest_decision

    manifest = evidence.source_manifest

    if evidence.paper_trade_id != expected_trade_id:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.paper_trade_id does not match the current trade"
        )
    if evidence.user_id != expected_user_id:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.user_id does not match the current user"
        )
    if evidence.symbol.upper() != expected_symbol.upper():
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.symbol does not match the current trade's symbol"
        )
    if evidence.market != expected_market:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.market does not match the current trade's market"
        )
    expected_provider_symbol = _provider_symbol(expected_symbol, expected_market)
    if evidence.provider_symbol != expected_provider_symbol or manifest.get("provider_symbol") != expected_provider_symbol:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH,
            "evidence.provider_symbol does not match deterministic normalization of the current symbol/market",
        )
    if evidence.market_timezone != expected_market_timezone:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.market_timezone does not match the current market"
        )
    if evidence.entry_timestamp != expected_entry_timestamp:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.entry_timestamp does not match the current trade's opened_at"
        )
    if evidence.exit_timestamp != expected_exit_timestamp:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH, "evidence.exit_timestamp does not match the current trade's closed_at"
        )
    if evidence.requested_window_start != expected_requested_window_start:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH,
            "evidence.requested_window_start does not match the current trade's market-local entry date",
        )
    if evidence.requested_window_end != expected_requested_window_end:
        return ManifestCompatibilityDecision(
            False, REPLAY_TRADE_CONTEXT_MISMATCH,
            "evidence.requested_window_end does not match the current trade's market-local exit date",
        )

    return ManifestCompatibilityDecision(True, None)


def _compute_evidence_hash(bars: list[PricePathBar]) -> str:
    """Deterministic for identical bars — same discipline as Sprint 2's
    report_store.compute_evidence_hash: sorted-key JSON of exactly the
    bar content, SHA-256 hashed. Used for debugging/dedup visibility, not
    the version-triple uniqueness boundary (the DB unique index is)."""
    canonical = json.dumps(
        [
            {
                "session_date": b.session_date.isoformat(),
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "adjustment_basis": b.adjustment_basis,
            }
            for b in bars
        ],
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_price_path_evidence(
    *,
    paper_trade_id: int,
    user_id: str,
    symbol: str,
    market: str,
    market_timezone_name: str,
    market_tzinfo,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    raw_bars: list[dict],
    split_events: list[date],
    dividend_events: list[date] | None = None,
    acquisition_timestamp: datetime | None = None,
) -> PricePathEvidenceBundle:
    """Pure: no I/O. Turns already-fetched raw daily bars into a
    validated, immutable PricePathEvidenceBundle.

    Window construction (Stage 4, refined by Pre-Stage-H Correction 1):
    the requested window is [entry_date, exit_date] in the market's own
    local calendar. Every bar between the two dates (inclusive) is
    retained; the entry-date and exit-date bars are each tagged with a
    REAL session-boundary policy computed by
    services.postmortem.session_boundary — ENTRY_BAR_INCLUDED_FULL /
    EXIT_BAR_INCLUDED_FULL when the trade timestamp lands exactly at
    official session open/close, PARTIAL_UNKNOWN otherwise. See
    _resolve_boundary_policies.

    Adjustment basis (Correction 2): raw_bars are expected to come from
    auto_adjust=False acquisition (this function's own contract, not
    merely a caller convention) — under yfinance's own documented
    contract for that argument, Open/High/Low/Close are UNADJUSTED
    exchange-reported values, directly comparable to this codebase's own
    unadjusted paper-trade prices without needing any adjustment-basis
    reconciliation claim. A split occurring in-window is still the one
    disqualifying fact this function checks for: a split changes what
    "unadjusted" even means across the boundary (pre-split prices are on
    a different share-count basis than post-split prices), so
    excursion values are never computed across a split regardless of
    which acquisition mode produced the bars — status becomes
    AMBIGUOUS_RESOLUTION and adjustment_basis becomes UNKNOWN_ADJUSTMENT,
    with an explicit limitation string, rather than silently computing a
    corrupted MFE/MAE across the split boundary."""
    _require_timezone_aware_trade_timestamps(entry_timestamp, exit_timestamp)

    if acquisition_timestamp is None:
        acquisition_timestamp = datetime.now(timezone.utc)
    if dividend_events is None:
        dividend_events = []

    entry_date = entry_timestamp.astimezone(market_tzinfo).date()
    exit_date = exit_timestamp.astimezone(market_tzinfo).date()

    # The +1-day widening fetch_raw_daily_bars actually sends as its
    # provider `end=` argument (yfinance's start-inclusive/end-exclusive
    # convention) — recomputed here (not passed in) because
    # build_price_path_evidence is pure/no-I/O and must stay reproducible
    # from persisted trade facts alone; this is the same arithmetic
    # fetch_raw_daily_bars itself uses, so the manifest's disclosed value
    # always matches what was actually requested.
    provider_exclusive_request_end = exit_date + timedelta(days=1)

    limitations: list[str] = []
    any_adj_close = any(
        raw.get("adj_close") is not None and raw.get("adj_close") == raw.get("adj_close")  # NaN != NaN
        for raw in raw_bars
    )
    source_manifest = {
        "source_manifest_schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "source_id": SOURCE_ID_YFINANCE_DAILY,
        "source_type": SOURCE_TYPE,
        "source_scope": SOURCE_SCOPE,
        "production_authoritative": False,
        "source_version": SOURCE_VERSION,
        "provider": "yfinance",
        "provider_library_version": _provider_library_version(),
        "interval": BAR_INTERVAL_DAILY,
        "auto_adjust": ACQUISITION_AUTO_ADJUST,
        "back_adjust": ACQUISITION_BACK_ADJUST,
        "actions": ACQUISITION_ACTIONS,
        "repair": ACQUISITION_REPAIR,
        "prepost": ACQUISITION_PREPOST,
        "timezone_behavior": ACQUISITION_TIMEZONE_BEHAVIOR,
        # PROVIDER_RETURNED_UNADJUSTED_OHLC, not "raw exchange-authoritative
        # prices" — this codebase does not claim yfinance IS the exchange
        # of record, only that it was ASKED for and RETURNED data under
        # the auto_adjust=False contract (see SOURCE_TYPE/SOURCE_SCOPE
        # above for the separate, already-explicit non-authoritative
        # disclosure).
        "raw_ohlc_basis_claim": "PROVIDER_RETURNED_UNADJUSTED_OHLC",
        "adjusted_close_available": any_adj_close,
        "split_event_manifest": [d.isoformat() for d in split_events],
        "dividend_event_manifest": [d.isoformat() for d in dividend_events],
        "provider_symbol": _provider_symbol(symbol, market),
        "trade_symbol": symbol.upper(),
        "symbol_normalization_version": SYMBOL_NORMALIZATION_VERSION,
        "market": market,
        "acquisition_mode": "auto_adjust_false",
        "provider_request_start": entry_date.isoformat(),
        "provider_exclusive_request_end": provider_exclusive_request_end.isoformat(),
        "end_widening_reason": (
            "yfinance's Ticker.history() treats `end` as exclusive; widened by one day "
            "purely so the exit session itself is included in the provider response — "
            "requested_window_end on the bundle remains the original, non-widened exit date"
        ),
        "boundary_policy_version": BOUNDARY_POLICY_VERSION,
        "requested_trading_weekday_count": _count_requested_weekdays(entry_date, exit_date),
        # NOT populated here — `unresolved_basis_limitations` and
        # `manifest_integrity_hash` are only ever set once, by
        # finalize_source_manifest, after every limitation for the
        # actual return path is known. Setting them here (as a prior
        # pass did) meant the hash was computed BEFORE dividend/split/
        # boundary-policy/no-bars/partial-window limitations were even
        # appended — a hash that excluded mutable final content, exactly
        # the defect this restructuring fixes.
    }

    if dividend_events:
        limitations.append(
            f"a dividend was paid within the holding window ({[d.isoformat() for d in dividend_events]}) — "
            "disclosed for completeness; raw (auto_adjust=False) OHLC values are not distorted by a "
            "dividend the way they would be by a split, so this does not by itself block computation"
        )

    if split_events:
        limitations.append(
            f"a stock split occurred within the holding window ({[d.isoformat() for d in split_events]}) — "
            "a split changes what 'unadjusted' means across the boundary (different share-count basis "
            "before vs after), so excursion values are not computed for this trade"
        )
        final_manifest = finalize_source_manifest(source_manifest, limitations)
        return PricePathEvidenceBundle(
            evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
            paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
            source_id=SOURCE_ID_YFINANCE_DAILY, source_type=SOURCE_TYPE, source_version=SOURCE_VERSION,
            provider_symbol=_provider_symbol(symbol, market), price_adjustment_basis=UNKNOWN_ADJUSTMENT,
            bar_interval=BAR_INTERVAL_DAILY, market_timezone=market_timezone_name,
            entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
            entry_bar_policy=ENTRY_BAR_PARTIAL_UNKNOWN, exit_bar_policy=EXIT_BAR_PARTIAL_UNKNOWN,
            requested_window_start=entry_date, requested_window_end=exit_date,
            observed_window_start=None, observed_window_end=None,
            bars_expected=None, bars_observed=0, missing_bar_count=None,
            data_completeness=STATUS_AMBIGUOUS_RESOLUTION, freshness_basis="acquired_at_generation_time",
            acquisition_timestamp=acquisition_timestamp, source_manifest=final_manifest,
            limitations=list(limitations), bars=(), evidence_hash=_compute_evidence_hash([]),
        )

    # Deduplicate and sort defensively — a provider returning a
    # duplicate/out-of-order row must never propagate into the immutable
    # bundle (Stage 15 test items #18/#19); the LATER of two rows for the
    # same date wins (arbitrary but deterministic — a provider should
    # never legitimately return two rows for one date in the first
    # place, so this is a defensive tie-break, not a real code path).
    by_date: dict[date, dict] = {}
    for raw in raw_bars:
        by_date[raw["date"]] = raw
    ordered_dates = sorted(by_date.keys())

    bars: list[PricePathBar] = []
    for d in ordered_dates:
        if d < entry_date or d > exit_date:
            continue
        raw = by_date[d]
        vals = (raw["open"], raw["high"], raw["low"], raw["close"])
        if not all(v == v and math.isfinite(v) for v in vals):
            # NaN/inf close (the documented prediction_engine.py
            # placeholder-row bug this codebase has already hit once) —
            # skip the bar rather than let it reach PricePathBar's own
            # construction guard (which would raise and abort the whole
            # bundle for one bad row).
            continue
        bars.append(PricePathBar(
            timestamp=datetime(d.year, d.month, d.day, tzinfo=market_tzinfo),
            interval=BAR_INTERVAL_DAILY,
            open=raw["open"], high=raw["high"], low=raw["low"], close=raw["close"],
            volume=raw.get("volume"), session_date=d,
            source_id=SOURCE_ID_YFINANCE_DAILY, adjustment_basis=UNADJUSTED,
            verification_level="DIRECTLY_OBSERVED",
        ))

    entry_bar_policy, exit_bar_policy = _resolve_boundary_policies(
        market, entry_timestamp, exit_timestamp, limitations,
    )

    if not bars:
        # Appended to the SHARED `limitations` list (never a separate
        # literal) — a prior pass built a brand-new one-item list here,
        # which silently diverged from source_manifest's own final
        # unresolved_basis_limitations whenever a dividend or
        # boundary-policy limitation had already been recorded above.
        limitations.append("no valid bars were returned by the provider for the requested window")
        final_manifest = finalize_source_manifest(source_manifest, limitations)
        return PricePathEvidenceBundle(
            evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
            paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
            source_id=SOURCE_ID_YFINANCE_DAILY, source_type=SOURCE_TYPE, source_version=SOURCE_VERSION,
            provider_symbol=_provider_symbol(symbol, market), price_adjustment_basis=UNADJUSTED,
            bar_interval=BAR_INTERVAL_DAILY, market_timezone=market_timezone_name,
            entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
            entry_bar_policy=entry_bar_policy, exit_bar_policy=exit_bar_policy,
            requested_window_start=entry_date, requested_window_end=exit_date,
            observed_window_start=None, observed_window_end=None,
            bars_expected=None, bars_observed=0, missing_bar_count=None,
            data_completeness=STATUS_UNAVAILABLE, freshness_basis="acquired_at_generation_time",
            acquisition_timestamp=acquisition_timestamp, source_manifest=final_manifest,
            limitations=list(limitations),
            bars=(), evidence_hash=_compute_evidence_hash([]),
        )

    observed_start = bars[0].session_date
    observed_end = bars[-1].session_date
    expected_calendar_days = (exit_date - entry_date).days + 1
    completeness = STATUS_COMPLETE if (observed_start == entry_date and observed_end == exit_date) else STATUS_PARTIAL
    if completeness == STATUS_PARTIAL:
        limitations.append(
            f"requested window {entry_date}..{exit_date} but provider only returned bars for "
            f"{observed_start}..{observed_end} — some sessions are missing (weekends/holidays are expected "
            "absences, not evidence gaps; this limitation covers unexplained gaps within the observed range)"
        )

    final_manifest = finalize_source_manifest(source_manifest, limitations)
    return PricePathEvidenceBundle(
        evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
        paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
        source_id=SOURCE_ID_YFINANCE_DAILY, source_type=SOURCE_TYPE, source_version=SOURCE_VERSION,
        provider_symbol=_provider_symbol(symbol, market), price_adjustment_basis=UNADJUSTED,
        bar_interval=BAR_INTERVAL_DAILY, market_timezone=market_timezone_name,
        entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
        entry_bar_policy=entry_bar_policy, exit_bar_policy=exit_bar_policy,
        requested_window_start=entry_date, requested_window_end=exit_date,
        observed_window_start=observed_start, observed_window_end=observed_end,
        bars_expected=expected_calendar_days, bars_observed=len(bars),
        missing_bar_count=None,  # non-trading days are expected absences, not "missing" — never counted here
        data_completeness=completeness, freshness_basis="acquired_at_generation_time",
        acquisition_timestamp=acquisition_timestamp, source_manifest=final_manifest,
        limitations=list(limitations), bars=tuple(bars), evidence_hash=_compute_evidence_hash(bars),
    )


def acquire_price_path_evidence(
    *,
    paper_trade_id: int, user_id: str, symbol: str, market: str,
    market_timezone_name: str, market_tzinfo,
    entry_timestamp: datetime, exit_timestamp: datetime,
    fetch_bars_fn=fetch_raw_daily_bars, fetch_splits_fn=fetch_split_events,
    fetch_dividends_fn=fetch_dividend_events,
) -> PricePathEvidenceBundle:
    """Thin orchestration: resolves the provider symbol, fetches raw
    bars, split events and dividend events (the only I/O in this
    module), then delegates to the pure build_price_path_evidence.
    Callers MUST invoke this OUTSIDE any open database transaction
    (Stage 12) — this function never accepts a `conn` parameter, by
    design, so it cannot be called from inside one by accident."""
    _require_timezone_aware_trade_timestamps(entry_timestamp, exit_timestamp)

    provider_symbol = _provider_symbol(symbol, market)
    entry_date = entry_timestamp.astimezone(market_tzinfo).date()
    exit_date = exit_timestamp.astimezone(market_tzinfo).date()

    _check_bounded_window(entry_date, exit_date)

    raw_bars = fetch_bars_fn(provider_symbol, entry_date, exit_date)
    split_events = fetch_splits_fn(provider_symbol, entry_date, exit_date)
    dividend_events = fetch_dividends_fn(provider_symbol, entry_date, exit_date)

    return build_price_path_evidence(
        paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
        market_timezone_name=market_timezone_name, market_tzinfo=market_tzinfo,
        entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
        raw_bars=raw_bars, split_events=split_events, dividend_events=dividend_events,
    )


# --- Stage F: price-basis / corporate-action compatibility classification ---
# A separate, explicit classification layer on top of the bundle's own
# coarser price_adjustment_basis field (UNADJUSTED/UNKNOWN_ADJUSTMENT).
# This does not change bundle construction — it gives callers (report
# rendering, docs, tests) a documented, six-state vocabulary for exactly
# why a given evidence bundle's prices are or are not safely comparable
# to the paper trade's own unadjusted execution prices.
COMPATIBLE_UNADJUSTED = "COMPATIBLE_UNADJUSTED"
COMPATIBLE_SPLIT_ADJUSTED = "COMPATIBLE_SPLIT_ADJUSTED"
BASIS_MISMATCH = "BASIS_MISMATCH"
SPLIT_IN_WINDOW = "SPLIT_IN_WINDOW"
BASIS_UNKNOWN_ADJUSTMENT = "UNKNOWN_ADJUSTMENT"
BASIS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# Correction 2's expanded vocabulary — distinct from the four names
# above where the distinction actually matters (a raw auto_adjust=False
# fetch needs no reconciliation "claim" the way an auto_adjust=True
# fetch's COMPATIBLE_UNADJUSTED conclusion still implicitly does).
UNADJUSTED_PROVIDER_OHLC = "UNADJUSTED_PROVIDER_OHLC"
SPLIT_RECONCILED = "SPLIT_RECONCILED"
COMPOSITE_ADJUSTED_UNKNOWN_BASIS = "COMPOSITE_ADJUSTED_UNKNOWN_BASIS"
DIVIDEND_ADJUSTMENT_PRESENT = "DIVIDEND_ADJUSTMENT_PRESENT"


def evaluate_basis_compatibility(
    *, acquisition_mode: str | None, split_events: list[date], bars_observed: int,
    dividend_events: list[date] | None = None,
) -> str:
    """Stage F / Correction 2 — never assumes no split occurred. A split
    anywhere in the requested window is the single most important fact
    and takes priority over every other consideration, since this
    codebase has no reconciled split-ratio adjustment logic (see
    build_price_path_evidence's own docstring — a split in-window
    already forces UNKNOWN_ADJUSTMENT at the bundle level; this function
    names that same fact under this module's own reporting vocabulary).

    acquisition_mode is read from the persisted source_manifest — never
    re-derived — so replay of an already-persisted evidence bundle
    reproduces the identical compatibility result without any new
    provider call (Stage D item 5 / Stage F's own "same persisted
    evidence reproduces the same result" requirement).

    A missing split event is checked (split_events is empty) but that
    absence is NOT by itself treated as proof of a compatible basis for
    any acquisition mode other than auto_adjust_false — a composite
    "auto_adjust_true"-style feed remains COMPATIBLE_UNADJUSTED only
    because this codebase's own numerically-identical-absent-a-split
    reasoning is documented and tested (see the acquisition_mode ==
    'auto_adjust_true' branch below); any OTHER/unrecognized adjusted
    mode is COMPOSITE_ADJUSTED_UNKNOWN_BASIS, not silently assumed
    compatible."""
    if split_events:
        return SPLIT_IN_WINDOW
    if bars_observed <= 0:
        return BASIS_INSUFFICIENT_EVIDENCE
    if acquisition_mode == "auto_adjust_false":
        # This module's own current, pinned acquisition mode (Correction
        # 2): yfinance's documented contract for auto_adjust=False is
        # that OHLC are the raw, unadjusted exchange-reported values — no
        # reconciliation claim is even needed here, unlike the
        # auto_adjust_true branch below.
        return UNADJUSTED_PROVIDER_OHLC
    if acquisition_mode == "auto_adjust_true":
        # Retained for backward-compatible replay of any evidence
        # persisted before Correction 2 switched the pinned mode. Absent
        # any split in-window (checked above), split-adjusted and
        # raw/unadjusted values are numerically identical for this
        # window — safely comparable to this codebase's own unadjusted
        # paper-trade execution prices.
        return COMPATIBLE_UNADJUSTED
    if acquisition_mode == "total_return_adjusted":
        # Never used for trade-path execution analysis (Stage F's own
        # explicit prohibition) — always a mismatch against unadjusted
        # execution prices, split or no split.
        return BASIS_MISMATCH
    if acquisition_mode is None:
        return BASIS_INSUFFICIENT_EVIDENCE
    return COMPOSITE_ADJUSTED_UNKNOWN_BASIS
