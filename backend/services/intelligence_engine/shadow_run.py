"""
Intelligence Engine V1 — shadow-run orchestration (Epic 007 Phase 3A + 3B).

run_shadow_slice(market, job_id) is the only entry point daily_picks.py
calls. It:
  1. Reads the same static universe list (IN_STOCKS/US_STOCKS) Daily Picks
     already has in memory — no new provider/API calls, no new data
     dependency.
  2. Runs the Instrument Type Gate (Universe Builder component) over every
     (symbol, name) pair — Phase 3A, unchanged.
  3. If market data is available (see below), also runs Tradability and
     Liquidity gates plus Data Confidence scoring per candidate — Phase 3B.
  4. Persists aggregate telemetry only (counts, small samples) — never a
     per-symbol pass/fail list, keeping the table small.

Phase 3B-B data-availability note (read before assuming these gates see
the full candidate pool): daily_picks.py's shadow-run call site now passes
a real `candidate_market_data` map, built by
intelligence_engine.candidate_data.build_candidate_market_data_from_payload
from the already-computed Daily Picks `payload` (no new fetch, no
production function's return contract changed — see that module's own
docstring for the exact fields and their honesty notes). This covers only
the symbols that made it into payload["picks"] — the ~18 selected picks
across 3 horizons, not the full Phase-0/Phase-1 candidate pool the real
pipeline scans before narrowing down to those picks (that broader pool's
data lives inside _bulk_screen/_predict_stock and isn't retained anywhere
by the time generate_picks() returns; exposing it would require changing
those functions' return contracts, deliberately avoided this pass). When
`candidate_market_data` is empty or not supplied — e.g. a completely
failed generation, or a future/alternate caller — these three gates
report `"available": False` in telemetry rather than fabricating results.

This function must never raise in a way that could affect the caller's
own control flow in an unexpected way; daily_picks.py additionally wraps
its call site in its own try/except per the project's established
non-critical-post-persistence pattern (see generate_picks's Phase 8/
Telegram block), but this module is defensive on its own terms too.
"""
import logging
import os

log = logging.getLogger(__name__)

UNIVERSE_VERSION = "intelligence-v1-shadow"

# How many excluded (symbol, name, reason) examples to keep per run — enough
# to spot-check the gate's behavior without the table growing unbounded.
_SAMPLE_SIZE = 10


def run_shadow_slice(
    market: str,
    job_id: str | None = None,
    candidate_market_data: dict[str, dict] | None = None,
) -> dict | None:
    """candidate_market_data: optional {symbol: {...}} map with keys the
    Tradability/Liquidity/Confidence gates understand (trading_status,
    has_quote, has_volume, quote_age_days, avg_daily_value,
    avg_daily_volume, zero_volume_days, price, free_float_pct,
    fundamentals_completeness_pct, financials_completeness_pct,
    history_depth_days). None (the current, only call site today) means
    those three gates are skipped and reported as unavailable — see the
    module docstring above for why.

    Runs on a daemon thread from daily_picks.py — an unhandled exception
    here would otherwise only print to stderr and vanish, so everything is
    wrapped in one try/except that logs and returns None rather than
    raising, guaranteeing this can never surface as a crash anywhere the
    real Daily Picks pipeline (or anything monitoring it) would notice."""
    try:
        return _run_shadow_slice_inner(market, job_id, candidate_market_data)
    except Exception as e:
        log.warning(f"[intelligence_engine] [{market}] shadow slice failed: {e}")
        return None


def _run_shadow_slice_inner(
    market: str, job_id: str | None, candidate_market_data: dict[str, dict] | None
) -> dict:
    from services.intelligence_engine.instrument_type_gate import classify_instrument
    from services.intelligence_engine.telemetry import persist_shadow_run
    from services.stock_universe import IN_STOCKS, US_STOCKS

    raw_universe = IN_STOCKS if market == "IN" else US_STOCKS
    raw_count = len(raw_universe)

    instrument_type_counts: dict[str, int] = {}
    excluded_counts_by_reason: dict[str, int] = {}
    sample_exclusions: list[dict] = []
    passed_count = 0

    for symbol, name in raw_universe:
        result = classify_instrument(symbol, name)
        instrument_type_counts[result.instrument_type] = instrument_type_counts.get(result.instrument_type, 0) + 1
        if result.passed:
            passed_count += 1
        else:
            excluded_counts_by_reason[result.instrument_type] = excluded_counts_by_reason.get(result.instrument_type, 0) + 1
            if len(sample_exclusions) < _SAMPLE_SIZE:
                sample_exclusions.append({"symbol": symbol, "name": name, "reason": result.instrument_type})

    evaluated_count = raw_count  # every symbol in the raw universe is evaluated; the gate never skips one
    excluded_count = evaluated_count - passed_count

    (tradability_passed, tradability_failed, liquidity_passed, liquidity_failed,
     data_confidence_average, sample_liquidity_rejections, gate_failure_reasons) = _run_phase3b_gates(
        market, candidate_market_data
    )

    # top_failure_reasons merges Phase 3A's instrument-type exclusions with
    # Phase 3B's tradability/liquidity failure reasons into one combined,
    # sorted view — a single place to see "what's excluding the most
    # symbols right now" regardless of which gate did the excluding.
    combined_reasons = dict(excluded_counts_by_reason)
    for reason, count in gate_failure_reasons.items():
        combined_reasons[reason] = combined_reasons.get(reason, 0) + count
    top_failure_reasons = dict(sorted(combined_reasons.items(), key=lambda kv: kv[1], reverse=True)[:10])

    telemetry = {
        "market": market,
        "universe_version": UNIVERSE_VERSION,
        "raw_count": raw_count,
        "evaluated_count": evaluated_count,
        "passed_count": passed_count,
        "excluded_count": excluded_count,
        "excluded_counts_by_reason": excluded_counts_by_reason,
        "instrument_type_counts": instrument_type_counts,
        "sample_exclusions": sample_exclusions,
        "generation_job_id": job_id,
        "tradability_passed": tradability_passed,
        "tradability_failed": tradability_failed,
        "liquidity_passed": liquidity_passed,
        "liquidity_failed": liquidity_failed,
        "data_confidence_average": data_confidence_average,
        "top_failure_reasons": top_failure_reasons,
        "sample_liquidity_rejections": sample_liquidity_rejections,
    }

    if os.getenv("USE_POSTGRES") == "1":
        try:
            persist_shadow_run(
                market=market,
                universe_version=UNIVERSE_VERSION,
                raw_count=raw_count,
                evaluated_count=evaluated_count,
                passed_count=passed_count,
                excluded_count=excluded_count,
                excluded_counts_by_reason=excluded_counts_by_reason,
                instrument_type_counts=instrument_type_counts,
                sample_exclusions=sample_exclusions,
                source_commit=os.getenv("RAILWAY_GIT_COMMIT_SHA"),
                generation_job_id=job_id,
                tradability_passed=tradability_passed,
                tradability_failed=tradability_failed,
                liquidity_passed=liquidity_passed,
                liquidity_failed=liquidity_failed,
                data_confidence_average=data_confidence_average,
                top_failure_reasons=top_failure_reasons,
                sample_liquidity_rejections=sample_liquidity_rejections,
            )
            log.info(
                f"[intelligence_engine] [{market}] shadow slice persisted: "
                f"{passed_count}/{raw_count} passed instrument-type gate"
                + (
                    f"; {tradability_passed}/{tradability_passed + tradability_failed} tradability, "
                    f"{liquidity_passed}/{liquidity_passed + liquidity_failed} liquidity"
                    if tradability_passed is not None else "; tradability/liquidity not evaluated (no market data)"
                )
            )
        except Exception as e:
            log.warning(f"[intelligence_engine] [{market}] shadow telemetry persistence failed: {e}")
    else:
        log.info(
            f"[intelligence_engine] [{market}] shadow slice computed but not persisted "
            f"(USE_POSTGRES not set): {passed_count}/{raw_count} passed"
        )

    return telemetry


def _run_phase3b_gates(
    market: str, candidate_market_data: dict[str, dict] | None
) -> tuple[int | None, int | None, int | None, int | None, float | None, list[dict], dict[str, int]]:
    """Returns (tradability_passed, tradability_failed, liquidity_passed,
    liquidity_failed, data_confidence_average, sample_liquidity_rejections,
    gate_failure_reasons). All-None-and-empty when candidate_market_data is
    None — see this file's module docstring for exactly why that's the
    case at every call site today."""
    if not candidate_market_data:
        return None, None, None, None, None, [], {}

    from services.intelligence_engine.tradability_gate import check_tradability
    from services.intelligence_engine.liquidity_gate import check_liquidity
    from services.intelligence_engine.data_confidence import compute_data_confidence, DataConfidenceInputs

    tradability_passed = 0
    tradability_failed = 0
    liquidity_passed = 0
    liquidity_failed = 0
    confidence_scores: list[int] = []
    sample_liquidity_rejections: list[dict] = []
    gate_failure_reasons: dict[str, int] = {}

    for symbol, data in candidate_market_data.items():
        trad = check_tradability(
            symbol,
            is_valid_symbol=data.get("is_valid_symbol", True),
            trading_status=data.get("trading_status"),
            has_quote=data.get("has_quote", True),
            has_volume=data.get("has_volume", True),
            quote_age_days=data.get("quote_age_days"),
        )
        if trad.passed:
            tradability_passed += 1
        else:
            tradability_failed += 1
            gate_failure_reasons[trad.reason] = gate_failure_reasons.get(trad.reason, 0) + 1

        liq = check_liquidity(
            symbol, market,
            avg_daily_value=data.get("avg_daily_value"),
            avg_daily_volume=data.get("avg_daily_volume"),
            zero_volume_days=data.get("zero_volume_days"),
            price=data.get("price"),
            free_float_pct=data.get("free_float_pct"),
        )
        if liq.passed:
            liquidity_passed += 1
        else:
            liquidity_failed += 1
            gate_failure_reasons[liq.reason] = gate_failure_reasons.get(liq.reason, 0) + 1
            if len(sample_liquidity_rejections) < _SAMPLE_SIZE:
                sample_liquidity_rejections.append({"symbol": symbol, "reason": liq.reason})

        confidence_scores.append(compute_data_confidence(DataConfidenceInputs(
            price_available=data.get("has_quote", True),
            volume_available=data.get("has_volume", True),
            fundamentals_completeness_pct=data.get("fundamentals_completeness_pct"),
            financials_completeness_pct=data.get("financials_completeness_pct"),
            quote_age_days=data.get("quote_age_days"),
            history_depth_days=data.get("history_depth_days"),
        )))

    data_confidence_average = round(sum(confidence_scores) / len(confidence_scores), 1) if confidence_scores else None

    return (
        tradability_passed, tradability_failed, liquidity_passed, liquidity_failed,
        data_confidence_average, sample_liquidity_rejections, gate_failure_reasons,
    )
