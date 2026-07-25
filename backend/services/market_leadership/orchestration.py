"""
Orchestration — the ONLY I/O site in the Market Leadership module.

Acquires price history (yfinance, the same provider already used
throughout this codebase — no new provider introduced), then calls the
pure calculation modules (relative_strength / group_leadership /
trend_lifecycle / breadth / explanation) and persists results via
persistence.py. Every entry point checks configuration.engine_enabled()
first and returns a "disabled" result immediately when off — no
calculation, no acquisition, no persistence happens with flags off.

v1 universe/group source: reuses heatmap_service.INDIA_SECTORS /
US_SECTORS (already-curated sector groupings, no new classification
source) as both the RS universe and the group-leadership membership —
avoiding a duplicate, differently-defined universe. Benchmark tickers and
market/universe wiring reuse validation_engine's existing conventions.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from services.market_leadership import configuration as cfg
from services.market_leadership import persistence
from services.market_leadership.contracts import RsTrend
from services.market_leadership.relative_strength import (
    compute_relative_return_windows, composite_relative_return,
    rank_universe, classify_rs_trend, build_stock_relative_strength,
)
from services.market_leadership.group_leadership import (
    MemberFact, build_group_leadership, rank_groups,
)
from services.market_leadership.trend_lifecycle import build_trend_lifecycle
from services.market_leadership.breadth import BreadthMemberFact, build_market_breadth
from services.market_leadership.explanation import build_why_now_explanation
from services.market_leadership.sessions import expected_latest_completed_session

log = logging.getLogger(__name__)

BENCHMARK_TICKER = {"IN": "^NSEI", "US": "^GSPC"}

# ── In-process TTL cache for compute_stock_context (Section 17 #10: "prevent
# expensive recalculation on every page request") ───────────────────────────
# Mirrors the established bounded-eviction pattern (market_data.py /
# prediction_engine.py's _cache_set) after that exact "unbounded until an
# OOM incident" class of bug was found twice already in this codebase.
# Key includes methodology/contract versions so a version bump naturally
# invalidates every cached entry without a manual cache-clear.
_STOCK_CONTEXT_CACHE: dict[str, tuple[float, dict]] = {}
_STOCK_CONTEXT_CACHE_MAX = 300
_STOCK_CONTEXT_TTL = 4 * 3600  # 4 hours — RS/trend are computed per completed session, not intraday


def _cache_set(cache: dict, key: str, value: tuple) -> None:
    if key not in cache and len(cache) >= _STOCK_CONTEXT_CACHE_MAX:
        try:
            oldest = min(cache, key=lambda k: cache[k][0])
            del cache[oldest]
        except (ValueError, KeyError):
            pass
    cache[key] = value


def _stock_context_cache_key(symbol: str, market: str, as_of: date) -> str:
    return (
        f"{symbol}:{market}:{as_of.isoformat()}:"
        f"{cfg.RS_METHODOLOGY_VERSION}:{cfg.TREND_METHODOLOGY_VERSION}:{cfg.EXPLANATION_CONTRACT_VERSION}"
    )


def _resolve_yahoo_symbol(symbol: str, market: str) -> str:
    """Reuses validation_engine's already-proven, tested symbol resolution
    (explicit market, never inferred) instead of duplicating the logic."""
    from services.validation_engine import _resolve_yahoo_symbol as _resolve
    return _resolve(symbol, market)


def fetch_price_history(symbol: str, market: str, period: str = "2y") -> pd.DataFrame:
    """The single acquisition adapter — every yfinance call in this module
    goes through here, so tests can monkeypatch `yf` on this module alone."""
    yf_symbol = _resolve_yahoo_symbol(symbol, market)
    return yf.Ticker(yf_symbol).history(period=period)


def _sector_map(market: str) -> dict[str, list[str]]:
    from services.heatmap_service import INDIA_SECTORS, US_SECTORS
    return INDIA_SECTORS if market == "IN" else US_SECTORS


def universe_symbols(market: str) -> list[str]:
    """v1 universe: dedup union of the market's curated sector groupings."""
    sectors = _sector_map(market)
    seen: dict[str, None] = {}
    for members in sectors.values():
        for sym in members:
            seen[sym] = None
    return list(seen)


def compute_market_context(
    market: str, as_of: date | None = None, price_history: dict[str, pd.DataFrame] | None = None,
    benchmark_df: pd.DataFrame | None = None,
) -> dict:
    """Computes RS for every symbol in the v1 universe, ranks the universe,
    aggregates group leadership per sector, and computes market breadth.

    `price_history`/`benchmark_df` are injectable for tests and for a
    caller that has already fetched data elsewhere — when omitted, this
    function performs the acquisition itself via fetch_price_history().
    Returns {"status": "disabled"} immediately if the engine flag is off —
    no acquisition, no calculation happens in that case.
    """
    if not cfg.engine_enabled():
        return {"status": "disabled"}

    started = time.time()
    if as_of is None:
        as_of = expected_latest_completed_session(market, datetime.now(timezone.utc))
        if as_of is None:
            return {"status": "unavailable", "reason": "could_not_determine_expected_session"}

    universe_id = f"stocksense_heatmap_v1_{market.lower()}"
    symbols = universe_symbols(market)

    if benchmark_df is None:
        try:
            benchmark_df = yf.Ticker(BENCHMARK_TICKER[market]).history(period="2y")
        except Exception as e:
            log.warning("[market_leadership] benchmark fetch failed market=%s error=%s", market, e)
            benchmark_df = pd.DataFrame()

    rs_results: dict[str, dict] = {}
    composites: dict[str, float] = {}
    errors: list[str] = []

    for symbol in symbols:
        try:
            price_df = (price_history or {}).get(symbol)
            if price_df is None:
                price_df = fetch_price_history(symbol, market)
            calc = compute_relative_return_windows(price_df, benchmark_df, as_of)
            composite = composite_relative_return(calc["windows"])
            rs_results[symbol] = {"price_df": price_df, "calc": calc, "composite": composite}
            if composite is not None:
                composites[symbol] = composite
        except Exception as e:
            errors.append(f"{symbol}: {e}")
            rs_results[symbol] = {"price_df": None, "calc": None, "composite": None}

    ranks = rank_universe(composites)

    rs_contracts = {}
    for symbol, res in rs_results.items():
        rank_entry = ranks.get(symbol)
        trend = RsTrend.INSUFFICIENT_DATA
        if res["calc"] is not None and res["price_df"] is not None:
            windows = res["calc"]["windows"]
            r1 = windows.get("rs_1m")
            r3 = windows.get("rs_3m")
            if r1 is not None and r3 is not None:
                trend = classify_rs_trend([r1], [r3])
        rs_contracts[symbol] = build_stock_relative_strength(
            symbol, market, res["price_df"], benchmark_df, as_of, universe_id, rank_entry, trend,
        )

    # ── Group leadership ────────────────────────────────────────────────
    sectors = _sector_map(market)
    group_scores: dict[str, float | None] = {}
    group_members: dict[str, list[MemberFact]] = {}
    for group_name, members_syms in sectors.items():
        member_facts = []
        for sym in members_syms:
            contract = rs_contracts.get(sym)
            if contract is None:
                continue
            member_facts.append(MemberFact(
                symbol=sym,
                rs_percentile=contract.stock_rs_percentile,
                rs_trend=contract.rs_trend,
                market_cap=None,
                above_50dma=None,
                near_252d_high=None,
            ))
        group_members[group_name] = member_facts
        scored = [m.rs_percentile for m in member_facts if m.rs_percentile is not None]
        group_scores[group_name] = (sum(scored) / len(scored)) if len(scored) >= cfg.GROUP_MIN_SCORED_MEMBERS else None

    group_ranks = rank_groups(group_scores)
    group_contracts = {}
    for group_name, members in group_members.items():
        group_contracts[group_name] = build_group_leadership(
            market, group_name, members, as_of, universe_id,
            group_scores.get(group_name), group_ranks.get(group_name),
            None,  # rank_change: no prior snapshot comparison in this pass
            "UP" if (group_scores.get(group_name) or 0) >= 55 else "DOWN",
        )

    # ── Breadth ──────────────────────────────────────────────────────────
    breadth_members = []
    for symbol, contract in rs_contracts.items():
        price_df = rs_results[symbol]["price_df"]
        sessions = len(price_df) if price_df is not None else 0
        breadth_members.append(BreadthMemberFact(
            symbol=symbol, sessions=sessions,
            above_20dma=None, above_50dma=None, above_200dma=None,
            is_new_high_63d=None, is_new_low_63d=None,
            rs_trend=contract.rs_trend, return_21d=contract.rs_1m,
        ))
    bench_ret_21d = None
    if benchmark_df is not None and len(benchmark_df) > 21:
        try:
            e = float(benchmark_df["Close"].iloc[-1])
            s = float(benchmark_df["Close"].iloc[-22])
            bench_ret_21d = (e - s) / s * 100.0 if s else None
        except Exception:
            bench_ret_21d = None

    breadth_contract = build_market_breadth(
        market, "market", breadth_members, bench_ret_21d, as_of, as_of, universe_id,
    )

    result = {
        "status": "ok",
        "market": market,
        "as_of": as_of.isoformat(),
        "universe_id": universe_id,
        "rs": {s: c.to_dict() for s, c in rs_contracts.items()},
        "groups": {g: c.to_dict() for g, c in group_contracts.items()},
        "breadth": breadth_contract.to_dict(),
        "symbols_evaluated": len(symbols),
        "symbols_scored": len(composites),
        "symbols_insufficient_data": len(symbols) - len(composites),
        "errors": errors,
    }

    if cfg.shadow_enabled():
        _persist_and_record_telemetry(market, universe_id, as_of, result, started, errors)

    return result


def _persist_and_record_telemetry(market, universe_id, as_of, result, started, errors) -> None:
    try:
        for symbol, rs_dict in result["rs"].items():
            persistence.save_rs_snapshot(
                market, symbol, as_of.isoformat(), universe_id,
                cfg.UNIVERSE_VERSION, cfg.RS_METHODOLOGY_VERSION, rs_dict,
            )
        for group_name, group_dict in result["groups"].items():
            persistence.save_group_snapshot(
                market, group_name, "sector", as_of.isoformat(), universe_id,
                cfg.UNIVERSE_VERSION, cfg.GROUP_METHODOLOGY_VERSION, group_dict,
            )
        persistence.save_breadth_snapshot(
            market, "market", as_of.isoformat(), universe_id,
            cfg.UNIVERSE_VERSION, cfg.BREADTH_METHODOLOGY_VERSION, result["breadth"],
        )
    except Exception as e:
        log.warning("[market_leadership] snapshot persistence failed market=%s error=%s", market, e)

    try:
        from services.market_leadership import telemetry
        telemetry.persist_shadow_run(
            market=market, universe_id=universe_id, universe_version=cfg.UNIVERSE_VERSION,
            methodology_versions={
                "rs": cfg.RS_METHODOLOGY_VERSION, "group": cfg.GROUP_METHODOLOGY_VERSION,
                "trend": cfg.TREND_METHODOLOGY_VERSION, "breadth": cfg.BREADTH_METHODOLOGY_VERSION,
            },
            symbols_evaluated=result["symbols_evaluated"], symbols_scored=result["symbols_scored"],
            symbols_insufficient_data=result["symbols_insufficient_data"],
            groups_evaluated=len(result["groups"]),
            breadth_state=result["breadth"]["state"],
            duration_ms=int((time.time() - started) * 1000),
            source_commit=os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            errors=errors or None,
        )
    except Exception as e:
        log.warning("[market_leadership] telemetry persistence failed market=%s error=%s", market, e)


def compute_stock_context(symbol: str, market: str, as_of: date | None = None) -> dict:
    """Single-symbol context: RS + trend lifecycle + Why Now, reading the
    already-persisted group/breadth snapshot for context (falls back to
    None fields if none exist yet — never recomputes the whole market on a
    per-symbol request). Returns {"status": "disabled"} if flags are off.

    TTL-cached (4h, bounded 300 entries) — a completed-session-scoped
    calculation does not need re-fetching yfinance on every page view.
    """
    if not cfg.engine_enabled():
        return {"status": "disabled"}
    if as_of is None:
        as_of = expected_latest_completed_session(market, datetime.now(timezone.utc))
        if as_of is None:
            return {"status": "unavailable", "reason": "could_not_determine_expected_session"}

    cache_key = _stock_context_cache_key(symbol, market, as_of)
    cached = _STOCK_CONTEXT_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _STOCK_CONTEXT_TTL:
        return cached[1]

    try:
        price_df = fetch_price_history(symbol, market)
    except Exception as e:
        error_result = {"status": "error", "reason": str(e)}
        # Short TTL for errors (2 min) — mirrors prediction_engine.py's
        # _SHORT_TTL_TS convention: don't hammer a failing provider on
        # every page view, but recover quickly once it's healthy again.
        _cache_set(_STOCK_CONTEXT_CACHE, cache_key, (time.time() - (_STOCK_CONTEXT_TTL - 120), error_result))
        return error_result

    try:
        benchmark_df = yf.Ticker(BENCHMARK_TICKER[market]).history(period="2y")
    except Exception:
        benchmark_df = pd.DataFrame()

    universe_id = f"stocksense_heatmap_v1_{market.lower()}"
    calc = compute_relative_return_windows(price_df, benchmark_df, as_of)
    trend_rs = RsTrend.INSUFFICIENT_DATA
    windows = calc["windows"]
    if windows.get("rs_1m") is not None and windows.get("rs_3m") is not None:
        trend_rs = classify_rs_trend([windows["rs_1m"]], [windows["rs_3m"]])

    # No cross-sectional universe rank available for a single-symbol,
    # on-demand request — percentile/rank are honestly None here; a full
    # compute_market_context() run is required for ranked output.
    rs_contract = build_stock_relative_strength(
        symbol, market, price_df, benchmark_df, as_of, universe_id, None, trend_rs,
    )
    trend_contract = build_trend_lifecycle(
        symbol, market, price_df, as_of, rs_trend_improving=(trend_rs == RsTrend.IMPROVING),
    )

    # No persisted group/breadth snapshot is read here — v1's single-symbol
    # endpoint has no group/breadth context (see the docstring above); an
    # unconditional persistence read on every call would be pure waste,
    # working against this function's own caching goal.
    why_now = build_why_now_explanation(rs_contract, None, trend_contract, None)

    result = {
        "status": "ok",
        "market": market,
        "symbol": symbol,
        "as_of": as_of.isoformat(),
        "rs": rs_contract.to_dict(),
        "trend": trend_contract.to_dict(),
        "why_now": why_now.to_dict(),
    }
    _cache_set(_STOCK_CONTEXT_CACHE, cache_key, (time.time(), result))
    return result
