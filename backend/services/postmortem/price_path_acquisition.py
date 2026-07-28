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
from datetime import date, datetime, timedelta, timezone

from services.postmortem.price_path_evidence import (
    BAR_INTERVAL_DAILY,
    ENTRY_BAR_PARTIAL_UNKNOWN,
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    EXIT_BAR_PARTIAL_UNKNOWN,
    PricePathBar,
    PricePathEvidenceBundle,
    STATUS_AMBIGUOUS_RESOLUTION,
    STATUS_COMPLETE,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    UNADJUSTED,
    UNKNOWN_ADJUSTMENT,
)

SOURCE_ID_YFINANCE_DAILY = "yfinance_daily"
SOURCE_TYPE = "APPROVED_EXTERNAL_SOURCE"
# Bumped whenever this module's acquisition/window-construction RULES
# change (not the underlying provider's own data) — independent of
# EVIDENCE_BUNDLE_SCHEMA_VERSION, which tracks the persisted shape.
SOURCE_VERSION = "1.0.0"

_MARKET_SUFFIX = {"US": "", "IN": ".NS"}


def _provider_symbol(symbol: str, market: str) -> str:
    return f"{symbol.upper()}{_MARKET_SUFFIX.get(market, '')}"


def fetch_raw_daily_bars(provider_symbol: str, start: date, end: date) -> list[dict]:
    """The one function in this module that calls yfinance. Returns a
    list of {date, open, high, low, close, volume} dicts, raw and
    unvalidated — build_price_path_evidence does all validation.
    Network/provider errors propagate to the caller uncaught; the
    generation pipeline is responsible for catching them and marking the
    outbox row retryable rather than fabricating evidence."""
    import yfinance as yf

    ticker = yf.Ticker(provider_symbol)
    # end is exclusive in yfinance's own convention — widen by one day so
    # the exit session itself is included.
    df = ticker.history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
        interval="1d", auto_adjust=True,
    )
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "date": idx.date() if hasattr(idx, "date") else idx,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if "Volume" in row and row["Volume"] == row["Volume"] else None,
        })
    return bars


def fetch_split_events(provider_symbol: str, start: date, end: date) -> list[date]:
    """Returns session dates on which a stock split occurred within
    [start, end], per yfinance's own corporate-action history. Used only
    to decide whether SPLIT_ADJUSTED bars remain safely comparable to
    this codebase's own UNADJUSTED paper-trade execution prices — never
    to adjust prices itself (this module never invents a split ratio
    reconciliation; a split in-window means the safe, honest answer is
    LIMITED/UNAVAILABLE evidence, not a "corrected" number)."""
    import yfinance as yf

    ticker = yf.Ticker(provider_symbol)
    splits = ticker.splits
    events = []
    for ts, ratio in splits.items():
        d = ts.date() if hasattr(ts, "date") else ts
        if start <= d <= end and ratio and ratio != 1.0:
            events.append(d)
    return events


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
    acquisition_timestamp: datetime | None = None,
) -> PricePathEvidenceBundle:
    """Pure: no I/O. Turns already-fetched raw daily bars into a
    validated, immutable PricePathEvidenceBundle.

    Window construction (Stage 4): the requested window is
    [entry_date, exit_date] in the market's own local calendar. Every
    bar between the two dates (inclusive) is retained; the entry-date
    and exit-date bars are each tagged with their own boundary policy —
    PARTIAL_UNKNOWN, since this codebase has no tick data to prove the
    bar's high/low occurred after entry (or before exit) rather than
    before entry (or after exit). Same-day entry+exit is the most
    conservative case: both boundary policies apply to the SAME bar.

    Adjustment basis (Stage 5): UNADJUSTED-equivalent when raw_bars came
    from auto_adjust=True (this function's only supported acquisition
    mode) AND no split occurred in-window (in which case split-adjusted
    and unadjusted bars are numerically identical for this window, so
    treating them as safely comparable to this codebase's own UNADJUSTED
    paper-trade prices is honest). If a split DID occur in-window, this
    function cannot safely reconcile the provider's split-adjusted
    history against the paper trade's unadjusted entry/exit prices —
    status becomes AMBIGUOUS_RESOLUTION and adjustment_basis becomes
    UNKNOWN_ADJUSTMENT, with an explicit limitation string, rather than
    silently computing a corrupted MFE/MAE across the split boundary."""
    if acquisition_timestamp is None:
        acquisition_timestamp = datetime.now(timezone.utc)

    entry_date = entry_timestamp.astimezone(market_tzinfo).date()
    exit_date = exit_timestamp.astimezone(market_tzinfo).date()

    limitations: list[str] = []
    source_manifest = {
        "source_id": SOURCE_ID_YFINANCE_DAILY,
        "source_version": SOURCE_VERSION,
        "provider_symbol": _provider_symbol(symbol, market),
        "acquisition_mode": "auto_adjust_true",
    }

    if split_events:
        limitations.append(
            f"a stock split occurred within the holding window ({[d.isoformat() for d in split_events]}) — "
            "split-adjusted provider history cannot be safely reconciled against this codebase's own "
            "unadjusted paper-trade execution prices, so excursion values are not computed for this trade"
        )
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
            acquisition_timestamp=acquisition_timestamp, source_manifest=source_manifest,
            limitations=limitations, bars=(), evidence_hash=_compute_evidence_hash([]),
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

    entry_bar_policy = ENTRY_BAR_PARTIAL_UNKNOWN
    exit_bar_policy = EXIT_BAR_PARTIAL_UNKNOWN

    if not bars:
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
            acquisition_timestamp=acquisition_timestamp, source_manifest=source_manifest,
            limitations=["no valid bars were returned by the provider for the requested window"],
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
        acquisition_timestamp=acquisition_timestamp, source_manifest=source_manifest,
        limitations=limitations, bars=tuple(bars), evidence_hash=_compute_evidence_hash(bars),
    )


def acquire_price_path_evidence(
    *,
    paper_trade_id: int, user_id: str, symbol: str, market: str,
    market_timezone_name: str, market_tzinfo,
    entry_timestamp: datetime, exit_timestamp: datetime,
    fetch_bars_fn=fetch_raw_daily_bars, fetch_splits_fn=fetch_split_events,
) -> PricePathEvidenceBundle:
    """Thin orchestration: resolves the provider symbol, fetches raw bars
    and split events (the only I/O in this module), then delegates to
    the pure build_price_path_evidence. Callers MUST invoke this OUTSIDE
    any open database transaction (Stage 12) — this function never
    accepts a `conn` parameter, by design, so it cannot be called from
    inside one by accident."""
    provider_symbol = _provider_symbol(symbol, market)
    entry_date = entry_timestamp.astimezone(market_tzinfo).date()
    exit_date = exit_timestamp.astimezone(market_tzinfo).date()

    raw_bars = fetch_bars_fn(provider_symbol, entry_date, exit_date)
    split_events = fetch_splits_fn(provider_symbol, entry_date, exit_date)

    return build_price_path_evidence(
        paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
        market_timezone_name=market_timezone_name, market_tzinfo=market_tzinfo,
        entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
        raw_bars=raw_bars, split_events=split_events,
    )
