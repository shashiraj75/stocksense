"""
Historical price-path eligibility governance — Trade Postmortem Sprint
3A, Stage J.

A single, typed, pure decision point evaluated BEFORE any provider
acquisition is attempted (and before any persisted-evidence replay
lookup) — separating four questions this codebase must never conflate:

  1. Trade-record validity   — is the timeline/price/market data on the
                                trade row itself even coherent?
  2. Evidence availability   — can market-data evidence be acquired or
                                replayed at all for this window?
  3. Evidence completeness   — once acquired, is it COMPLETE or PARTIAL?
  4. Overall report ceiling  — the highest report completeness this
                                trade's CONTEXT (entry/exit snapshot
                                presence) can ever honestly support,
                                independent of whether the market-data
                                evidence itself turns out to be perfect.

A valid trade may still have unavailable evidence. Available bars may
still be insufficient evidence (see price_path_calculator.py's own
interior-bars/boundary/level-history discipline, unchanged by this
module). A price-path calculation may be valid while the overall report
remains LIMITED_EVIDENCE because entry-thesis evidence (the entry
snapshot) is missing — this module's own `report_completeness_ceiling`
field is exactly that distinction, never something the price-path
calculation layer decides for itself.

Evidence availability here (the `ELIGIBLE_*` statuses) does NOT gate
the financial close, the Sprint 2 report, or entry-snapshot recovery —
those live entirely outside this module's authority. This module gates
only whether services.postmortem.price_path_generation should be
invoked at all for a given trade, and if so, under what already-known
constraints.

This module is Stage J1A of the two-stage historical decision model —
TRADE-CONTEXT ONLY. It never claims anything about provider/source
availability, evidence compatibility, adjustment-basis compatibility,
or bar completeness — those are Stage J1B's exclusive authority, in
services.postmortem.price_path_evidence_decision. J1A's own
`replay_allowed`/`acquisition_allowed`/`calculation_allowed` mean only
"the trade-context does not itself forbid attempting these" — never
"these will succeed."

(Stage J1B correction: the REPLAY_ONLY status previously declared here
was dead — never returned by evaluate_eligibility, never consumed by
any caller. Removed rather than retained as an unused placeholder; the
real replay-vs-acquisition distinction now lives in
price_path_evidence_decision.COMPATIBLE_REPLAY /
ACQUISITION_REQUIRED, which is the stage that actually has the
information — persisted-evidence compatibility — needed to make it.)
"""

from dataclasses import dataclass, field
from datetime import datetime

ELIGIBLE_COMPLETE_CONTEXT = "ELIGIBLE_COMPLETE_CONTEXT"
ELIGIBLE_PARTIAL_CONTEXT = "ELIGIBLE_PARTIAL_CONTEXT"
INVALID_TRADE_TIMELINE = "INVALID_TRADE_TIMELINE"
INVALID_TRADE_PRICE = "INVALID_TRADE_PRICE"
INVALID_MARKET = "INVALID_MARKET"
INVALID_SYMBOL = "INVALID_SYMBOL"
MISSING_ENTRY_CONTEXT = "MISSING_ENTRY_CONTEXT"
MISSING_EXIT_CONTEXT = "MISSING_EXIT_CONTEXT"
ENTRY_CONTEXT_INVALID = "ENTRY_CONTEXT_INVALID"
EXIT_CONTEXT_INVALID = "EXIT_CONTEXT_INVALID"
MISSING_EXIT_PRICE = "MISSING_EXIT_PRICE"

# A symbol is malformed if it contains anything other than the
# characters real exchange tickers use — letters, digits, '.', '-'
# (e.g. BRK.B, RDS-A). Never a live-network or universe-membership
# check (Stage J4 item 7): current stock-universe membership is
# irrelevant to whether a HISTORICAL trade's own stored symbol is a
# coherent string worth attempting acquisition for.
import re as _re
_VALID_SYMBOL_PATTERN = _re.compile(r"^[A-Za-z0-9.\-]+$")

_SUPPORTED_MARKETS = frozenset({"IN", "US"})

_TERMINAL_STATUSES = frozenset(
    {INVALID_TRADE_TIMELINE, INVALID_TRADE_PRICE, INVALID_MARKET, INVALID_SYMBOL}
)
_ACQUISITION_BLOCKING_STATUSES = _TERMINAL_STATUSES  # never call the provider for these


def _is_finite_positive(value) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value  # NaN check
        and value not in (float("inf"), float("-inf"))
        and value > 0
    )


@dataclass(frozen=True)
class HistoricalPricePathEligibility:
    eligibility_status: str
    acquisition_allowed: bool
    replay_allowed: bool
    calculation_allowed: bool
    report_completeness_ceiling: str  # "COMPLETE" | "LIMITED_EVIDENCE"
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    required_missing_evidence: tuple[str, ...] = field(default_factory=tuple)


def evaluate_eligibility(
    *, market: str, entry_price, exit_price, opened_at: datetime | None, closed_at: datetime | None,
    entry_snapshot_present: bool, exit_snapshot_present: bool,
    symbol: str | None = None, entry_snapshot_invalid: bool = False, exit_snapshot_invalid: bool = False,
) -> HistoricalPricePathEligibility:
    """Pure — no I/O, no database access. Called ONCE, before any
    provider call and before any persisted-evidence replay lookup, so a
    trade that can never be evidenced (bad timeline, bad price,
    unsupported market, malformed symbol) never reaches those layers at
    all — not even a replay attempt, since a replay lookup keyed on an
    invalid window would be meaningless.

    `symbol` is optional (default None skips the check) so existing
    callers that have already validated a non-empty symbol upstream are
    not forced to re-pass it; paper_trading.py's own trade row always
    has one, so it always passes it in production.

    `entry_snapshot_present`/`exit_snapshot_present` mean PRESENT_VALID
    when True. `entry_snapshot_invalid`/`exit_snapshot_invalid` (Stage
    J2's PRESENT_INVALID case) are only meaningful when the
    corresponding `*_present` is False — a present-but-invalid row is
    never usable, exactly like a missing one, but callers get a
    distinct reason code and limitation message so a caller can tell
    "we never captured this" from "we captured something and it's
    corrupt.\""""
    reason_codes: list[str] = []
    limitations: list[str] = []

    if market not in _SUPPORTED_MARKETS:
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_MARKET, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_MARKET,),
            limitations=(f"market {market!r} is not a supported price-path market",),
        )

    if symbol is not None and (not symbol or not _VALID_SYMBOL_PATTERN.match(symbol)):
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_SYMBOL, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_SYMBOL,),
            limitations=(f"symbol {symbol!r} is empty or contains characters no real ticker uses",),
        )

    if opened_at is None or closed_at is None:
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_TRADE_TIMELINE, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_TRADE_TIMELINE,),
            limitations=("entry or exit timestamp is missing on the trade record",),
        )

    if opened_at.tzinfo is None or closed_at.tzinfo is None:
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_TRADE_TIMELINE, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_TRADE_TIMELINE,),
            limitations=("entry or exit timestamp is timezone-naive — never trusted for evidence acquisition",),
        )

    if closed_at < opened_at:
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_TRADE_TIMELINE, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_TRADE_TIMELINE,),
            limitations=("exit timestamp precedes entry timestamp — an incoherent trade timeline",),
        )

    if not _is_finite_positive(entry_price):
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_TRADE_PRICE, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_TRADE_PRICE,),
            limitations=("entry_price is missing or not a finite positive number",),
        )

    # exit_price may legitimately be None (Sprint 2's own indeterminate-
    # P&L preservation, Stage 7) — that is not itself an invalid
    # timeline; it only limits captured_mfe_pct/giveback (already
    # handled by price_path_calculator.compute_excursion's own
    # _is_finite_positive check on exit_price). A non-null but
    # non-finite exit_price IS invalid, since that can only be corrupt
    # data, never a legitimate "unknown" representation.
    if exit_price is not None and not _is_finite_positive(exit_price):
        return HistoricalPricePathEligibility(
            eligibility_status=INVALID_TRADE_PRICE, acquisition_allowed=False, replay_allowed=False,
            calculation_allowed=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(INVALID_TRADE_PRICE,),
            limitations=("exit_price is present but not a finite positive number",),
        )

    # Trade record is coherent — acquisition/replay/calculation are all
    # permitted. The remaining question is purely the CONTEXT ceiling:
    # missing entry/exit snapshots never block price-path market-data
    # work, but they DO cap how complete the overall report can ever
    # honestly be (Stage J2's own required separation).
    missing_evidence: list[str] = []
    ceiling = "COMPLETE"
    if exit_price is None:
        # Stage J3 correction: MFE/signed-MAE/MAE-magnitude may still be
        # calculable from valid bar evidence with a missing exit price,
        # but captured_mfe_pct, giveback, and any complete exit-
        # performance interpretation cannot be — the overall report can
        # never honestly claim COMPLETE while that gap exists, even
        # though this does not itself block acquisition/replay/
        # calculation (compute_excursion's own _is_finite_positive check
        # on exit_price already keeps those specific fields null rather
        # than fabricated).
        reason_codes.append(MISSING_EXIT_PRICE)
        missing_evidence.append("exit_price")
        limitations.append(
            "exit_price is missing — captured MFE, giveback, and complete exit-performance "
            "interpretation cannot be determined reliably"
        )
        ceiling = "LIMITED_EVIDENCE"
    if not entry_snapshot_present:
        if entry_snapshot_invalid:
            reason_codes.append(ENTRY_CONTEXT_INVALID)
            limitations.append(
                "an entry snapshot row exists for this trade but failed validation (ownership/market/version "
                "mismatch) — never used, never repaired during report generation"
            )
        else:
            reason_codes.append(MISSING_ENTRY_CONTEXT)
            limitations.append("no entry snapshot exists for this trade — entry-time thesis evidence is unavailable")
        missing_evidence.append("entry_snapshot")
        ceiling = "LIMITED_EVIDENCE"
    if not exit_snapshot_present:
        if exit_snapshot_invalid:
            reason_codes.append(EXIT_CONTEXT_INVALID)
            limitations.append(
                "an exit snapshot row exists for this trade but failed validation (ownership/market/version "
                "mismatch) — never used, never repaired during report generation"
            )
        else:
            reason_codes.append(MISSING_EXIT_CONTEXT)
            limitations.append(
                "no exit snapshot exists for this trade — this is a historical trade closed before "
                "Sprint 2's durable close lifecycle existed"
            )
        missing_evidence.append("exit_snapshot")
        ceiling = "LIMITED_EVIDENCE"

    status = ELIGIBLE_COMPLETE_CONTEXT if ceiling == "COMPLETE" else ELIGIBLE_PARTIAL_CONTEXT
    return HistoricalPricePathEligibility(
        eligibility_status=status, acquisition_allowed=True, replay_allowed=True, calculation_allowed=True,
        report_completeness_ceiling=ceiling,
        reason_codes=tuple(reason_codes), limitations=tuple(limitations),
        required_missing_evidence=tuple(missing_evidence),
    )
