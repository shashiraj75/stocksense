"""
Candidate market-data extraction — Intelligence Engine V1 (Epic 007 Phase
3B-B). Bridges daily_picks.py's already-computed Daily Picks payload into
the `candidate_market_data` shape shadow_run.run_shadow_slice expects.

Design constraint honored: NO production function's return contract was
changed to get this data. `generate_picks()` already returns and persists
`payload` (picks/short/medium/long, each item carrying price, quality_factors,
generation_reference_as_of, etc.) — this module only reads that
already-computed dict, at the exact point in daily_picks.py where it's
already in scope for the Telegram/weight-adapter post-success work. No new
API/provider calls happen here.

Known, disclosed coverage limitation: this only covers the symbols that
made it into payload["picks"] (the ~18 selected picks across 3 horizons,
deduplicated), not the full Phase-0/Phase-1 candidate pool the real
pipeline scans before narrowing down to those picks (that broader pool's
per-symbol price/volume data is computed inside _bulk_screen/_predict_stock
but not returned or retained anywhere by the time generate_picks() returns
— exposing it would require changing those functions' return contracts,
which Phase 3B-B deliberately avoided; a legitimate follow-up if broader
shadow coverage is wanted).

Field-by-field honesty:
  - has_quote / price: real, directly from the payload's own `price` field.
  - has_volume: always True here — volume data isn't retained in the
    payload at all, so this is "unknown, don't gate on it" (fail-open),
    never a fabricated False.
  - trading_status: always None (unknown) — same reasoning.
  - quote_age_days: real, computed from the payload's own
    generation_reference_as_of (preferred) or generated_at timestamp.
  - avg_daily_value / avg_daily_volume / zero_volume_days / free_float_pct:
    always None — not retained in the payload; Liquidity Gate degrades
    gracefully to a price-floor-only check for these candidates (see
    liquidity_gate.py — every None input is simply not gated on).
  - fundamentals_completeness_pct / financials_completeness_pct: real,
    but a proxy — derived from how many of a fixed quality_factors
    sub-field checklist are actually populated in the payload, not a true
    filing/statement-completeness audit (that data doesn't survive to this
    payload either).
  - history_depth_days: always None — not retained in the payload.
"""
from datetime import datetime, timezone

_FUNDAMENTALS_CHECKLIST = ("sector", "piotroski", "altman_z", "altman_zone")
_FINANCIALS_CHECKLIST = ("buffett_total", "buffett_passed", "accruals_ratio")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def build_candidate_market_data_from_payload(payload: dict) -> dict[str, dict]:
    """Returns {symbol: {...}} in exactly the shape
    shadow_run._run_phase3b_gates expects. Never raises — a malformed
    payload (missing "picks", a pick missing expected keys, etc.) is
    handled per-item, worst case producing an empty dict, since this feeds
    a shadow-only path that must never be able to disrupt the caller."""
    now = datetime.now(timezone.utc)
    result: dict[str, dict] = {}

    picks_by_horizon = (payload or {}).get("picks") or {}
    for horizon_picks in picks_by_horizon.values():
        for pick in horizon_picks or []:
            try:
                symbol = pick.get("symbol")
                if not symbol or symbol in result:
                    continue  # first horizon's data wins if a symbol appears in more than one

                price = pick.get("price")
                quality = pick.get("quality_factors") or {}

                as_of = _parse_timestamp(pick.get("generation_reference_as_of")) or _parse_timestamp(pick.get("generated_at"))
                quote_age_days = max(0.0, (now - as_of).total_seconds() / 86400.0) if as_of else None

                fundamentals_present = sum(1 for f in _FUNDAMENTALS_CHECKLIST if quality.get(f) is not None)
                fundamentals_completeness_pct = (fundamentals_present / len(_FUNDAMENTALS_CHECKLIST)) * 100.0

                financials_present = sum(1 for f in _FINANCIALS_CHECKLIST if quality.get(f) is not None)
                financials_completeness_pct = (financials_present / len(_FINANCIALS_CHECKLIST)) * 100.0

                result[symbol] = {
                    "has_quote": price is not None and price > 0,
                    "has_volume": True,
                    "trading_status": None,
                    "quote_age_days": quote_age_days,
                    "price": price,
                    "avg_daily_value": None,
                    "avg_daily_volume": None,
                    "zero_volume_days": None,
                    "free_float_pct": None,
                    "fundamentals_completeness_pct": fundamentals_completeness_pct,
                    "financials_completeness_pct": financials_completeness_pct,
                    "history_depth_days": None,
                }
            except Exception:
                continue  # one malformed pick must never block the rest

    return result
