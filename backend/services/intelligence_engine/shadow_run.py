"""
Intelligence Engine V1 — shadow-run orchestration (Epic 007 Phase 3A).

run_shadow_slice(market, job_id) is the only entry point daily_picks.py
calls. It:
  1. Reads the same static universe list (IN_STOCKS/US_STOCKS) Daily Picks
     already has in memory — no new provider/API calls, no new data
     dependency.
  2. Runs the Instrument Type Gate (Universe Builder component) over every
     (symbol, name) pair.
  3. Persists aggregate telemetry only (counts, a small exclusion sample) —
     never a per-symbol pass/fail list, keeping the new table small.

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


def run_shadow_slice(market: str, job_id: str | None = None) -> dict | None:
    """Runs on a daemon thread from daily_picks.py — an unhandled exception
    here would otherwise only print to stderr and vanish, so everything is
    wrapped in one try/except that logs and returns None rather than
    raising, guaranteeing this can never surface as a crash anywhere the
    real Daily Picks pipeline (or anything monitoring it) would notice."""
    try:
        return _run_shadow_slice_inner(market, job_id)
    except Exception as e:
        log.warning(f"[intelligence_engine] [{market}] shadow slice failed: {e}")
        return None


def _run_shadow_slice_inner(market: str, job_id: str | None) -> dict:
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
            )
            log.info(
                f"[intelligence_engine] [{market}] shadow slice persisted: "
                f"{passed_count}/{raw_count} passed instrument-type gate"
            )
        except Exception as e:
            log.warning(f"[intelligence_engine] [{market}] shadow telemetry persistence failed: {e}")
    else:
        log.info(
            f"[intelligence_engine] [{market}] shadow slice computed but not persisted "
            f"(USE_POSTGRES not set): {passed_count}/{raw_count} passed"
        )

    return telemetry
