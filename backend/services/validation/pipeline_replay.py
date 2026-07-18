"""
DP-025 foundation (governed by DPD-009, DECIDED — DISCLOSURE HOLD) —
offline, deterministic post-prediction ranking -> eligibility -> issuer
deduplication -> selection -> portfolio-allocation replay, against an
explicitly supplied, frozen-at-source historical snapshot.

SCOPE (corrected wording — 2026-07-18 hardening follow-up): earlier
documentation in this package described this as replaying production's
full "composite -> confidence -> ranking -> selection -> allocation"
pipeline. That was inaccurate. This module does NOT replay composite-score
or confidence generation — `CandidateSnapshot` IS the already-computed
Phase 1-2 output (signal, confidence, technical/fundamental/sentiment/
quality scores, reasoning), SUPPLIED by the caller, never recomputed here.
See `ReplayResult.stages_supplied_not_replayed` /
`ReplayResult.stages_actually_replayed` for the machine-readable version of
this boundary, and `ReplayResult.full_pipeline_replay` /
`ReplayResult.production_equivalent` — both hardcoded `False` on every
result this module ever produces, in every mode, including a fully
successful STRICT_POINT_IN_TIME completion.

WHAT THIS IS: a first additive foundation proving the ranking/eligibility/
deduplication/selection/allocation stages of the real pipeline can be
replayed deterministically outside production, by calling the ACTUAL
production functions rather than a second, independently-written
approximation (the exact gap DP-025 identifies in validation_engine.py and
backtester.py, both of which compute their own separate composite-score
formula — see this module's call-graph note below).

WHAT THIS IS NOT:
  - NOT a replay of PredictionEngine.predict() or _predict_stock() — the
    composite score, confidence generation and later confidence
    adjustments, target-price computation, and stop-loss/trade-level
    computation are all SUPPLIED inputs on CandidateSnapshot, never
    recomputed. Phase 1-2 point-in-time production replay remains future
    work; DP-025 is not fully resolved by this foundation.
  - NOT a source of point-in-time historical fundamentals (DP-026, open).
  - NOT a survivorship-bias-free historical universe (DP-027, open).
  - NOT a fix for overlapping-window sampling (DP-028, open — this replays
    ONE snapshot, not a rolling series).
  - NOT a transaction-cost/slippage model (DP-029, open).
  - NOT a hit-rate, return, accuracy, or profitability calculator. This
    module computes and publishes none of those numbers.
  - NOT independent proof of historical accuracy or production
    equivalence, even when STRICT_POINT_IN_TIME completes successfully.
    STRICT completion means the supplied snapshot passed THIS HARNESS'S
    OWN declared-integrity requirements (the caller's self-reported
    point_in_time_integrity/synthetic_flags) — it does not mean the
    harness independently verified the data actually was point-in-time.
  - NOT invoked automatically by any production code path, scheduler, or
    API endpoint. It exists only as an explicitly-run offline tool/test
    dependency.

CALL-GRAPH NOTE (read-only investigation performed before writing this
file): production's real execution path is
  _predict_stock() -> PredictionEngine.predict() [Yahoo/news-coupled,
    produces signal/confidence/technical/fundamental/sentiment/quality
    scores + reasoning — SUPPLIED to this module via CandidateSnapshot,
    never invoked by it] -> _zscore_and_rank() [pure, REPLAYED] ->
    _passes_quality_gate() [was a nested closure, now module-level & pure,
    REPLAYED] -> _deduplicate_by_issuer() [pure, REPLAYED] -> per-horizon
    selection [_select_short_term_top_six() / _select_with_tier_quota(),
    both pure, REPLAYED] -> _compute_portfolio_allocation() [pure,
    REPLAYED, wraps optimizer.optimize() which itself only runs when 2+
    candidates reach that stage].
This module starts from the FROZEN OUTPUT of Phase 1-2 (a CandidateSnapshot
IS that frozen output — the network-coupled part this foundation does not
and cannot replay without live access) and reuses every pure function from
_zscore_and_rank() onward, unmodified, via `import services.daily_picks as
dp`. It does not call _predict_stock(), PredictionEngine.predict(),
_fetch_returns_matrix(), _write_score_snapshots(),
_build_alpha_observation_row(), or generate_picks() / _generate_picks_inner()
— all of those are network- or persistence-coupled and are exactly what
this foundation deliberately does NOT invoke. validation_engine.py and
backtester.py are untouched; they remain separate, independent (and, per
DP-025, non-production-equivalent) walk-forward tools.

SIDE-EFFECT PROHIBITION: this module must never call Yahoo Finance,
NSE/BSE providers, news providers, global-context fetching, PostgreSQL,
SQLite, score-snapshot writers, alpha-observation writers, job-state
functions, Telegram, schedulers, background threads, Daily Picks
generation, or model retraining. Guard tests monkeypatch these to raise if
invoked — see test_pipeline_replay_side_effects.py.
"""

from __future__ import annotations

import numpy as np

from services.validation.contracts import (
    MarketSnapshot,
    CandidateSnapshot,
    ReplayMode,
    ReplayStatus,
    PointInTimeIntegrity,
    RejectedCandidate,
    RankedCandidateResult,
    ReplayResult,
    STRICT_MODE_REQUIRED_CATEGORIES,
    STAGES_REPLAYABLE_ON_COMPLETION,
    REJECTION_NOT_BUY_SIGNAL,
    REJECTION_CONFIDENCE_BELOW_FLOOR,
    REJECTION_RISK_REWARD_OR_GOVERNANCE,
    REJECTION_LIQUIDITY_DISTRESS,
    REJECTION_SHORT_TERM_OVERBOUGHT,
    REJECTION_ISSUER_DUPLICATE,
    REJECTION_NOT_SELECTED,
    QUALITY_PROVENANCE_GENUINE,
    QUALITY_PROVENANCE_FALLBACK_NEUTRAL,
    QUALITY_PROVENANCE_UNKNOWN,
    validate_structure,
)

# Dotted-path (or descriptive, where no single function exists) provenance
# for stages this foundation NEVER invokes — always listed under
# production_provenance["not_invoked"] on every result, so a reader can see
# exactly what was supplied instead of replayed, not just infer it from
# absence.
_SUPPLIED_NOT_REPLAYED_PROVENANCE: dict[str, str] = {
    "universe_construction": "services.daily_picks._bulk_screen / _get_universe_by_mcap",
    "market_data_acquisition": "services.daily_picks._predict_stock (yfinance/news)",
    "prediction_engine_composite_calculation": "services.prediction_engine.PredictionEngine.predict (_composite_signal)",
    "signal_generation": "services.prediction_engine.PredictionEngine.predict (_composite_signal)",
    "confidence_generation_and_adjustments": "services.prediction_engine.PredictionEngine._apply_risk_reward_adjustment (and related _apply_* adjustments)",
    "target_price_calculation": "services.prediction_engine.PredictionEngine._estimate_target",
    "stop_loss_trade_level_calculation": "services.prediction_engine.PredictionEngine._trade_levels",
}

_UNRESOLVED_METHODOLOGY_LIMITATIONS = [
    "Composite score and confidence (including all later confidence "
    "adjustments), target-price, and stop-loss/trade-level computation are "
    "SUPPLIED inputs on CandidateSnapshot in this foundation, never "
    "replayed — Phase 1-2 point-in-time production replay remains future work.",
    "DP-026 (open): fundamentals point-in-time integrity is self-declared by "
    "the snapshot author in this foundation, not independently sourced or verified.",
    "DP-027 (open): this foundation does not source or enforce a "
    "survivorship-bias-free historical universe.",
    "DP-028 (open): this foundation replays a single cross-sectional "
    "snapshot; it does not address overlapping-window sampling across a "
    "rolling historical series.",
    "DP-029 (open): no transaction costs or slippage are modelled.",
    "This foundation computes no hit rate, return, accuracy, or "
    "profitability metric of any kind.",
    "full_pipeline_replay and production_equivalent are always False on "
    "every result this module produces, in every mode — including a "
    "successful STRICT_POINT_IN_TIME completion, which means only that the "
    "supplied snapshot passed this harness's own declared-integrity "
    "requirements, not that historical accuracy or production equivalence "
    "has been independently established.",
]

_DIAGNOSTIC_DISCLAIMERS = [
    "DIAGNOSTIC ONLY.",
    "NOT PRODUCTION-EQUIVALENT.",
    "NOT EVIDENCE OF HISTORICAL ACCURACY.",
    "NOT SUITABLE FOR PRODUCTION-LEARNING GRADUATION.",
]

_STRICT_COMPLETION_CLARIFICATION = (
    "STRICT_POINT_IN_TIME completion means the supplied snapshot passed "
    "this harness's own declared-integrity requirements (the caller's "
    "self-reported point_in_time_integrity / synthetic_flags) — it does "
    "NOT mean historical accuracy or production equivalence has been "
    "independently established. See production_equivalent (always False)."
)


def _assess_strict_integrity(snapshot: MarketSnapshot) -> list[str]:
    """Return a list of structured validation-error strings; empty means
    the snapshot satisfies STRICT_POINT_IN_TIME. Never mutates the
    snapshot. Sentiment is deliberately not in the mandatory set — a
    missing sentiment reading is production's own legitimate
    missing-evidence contract (z = 0.0), not a defect."""
    missing: list[str] = []

    if not snapshot.source or not snapshot.source.strip():
        missing.append("snapshot.source (provenance) is missing or empty")
    if not snapshot.as_of or not snapshot.as_of.strip():
        missing.append("snapshot.as_of (timestamp) is missing or empty")

    for category in STRICT_MODE_REQUIRED_CATEGORIES:
        status = snapshot.point_in_time_integrity.get(category, PointInTimeIntegrity.UNKNOWN_PROVENANCE)
        if status != PointInTimeIntegrity.POINT_IN_TIME:
            missing.append(
                f"snapshot-level category {category!r} is {status.value!r}, "
                f"required {PointInTimeIntegrity.POINT_IN_TIME.value!r}"
            )

    for candidate in snapshot.candidates:
        for category, status in candidate.point_in_time_status.items():
            if category == "sentiment":
                continue  # missing/synthetic sentiment is a legitimate production state
            if status != PointInTimeIntegrity.POINT_IN_TIME:
                missing.append(
                    f"candidate {candidate.symbol!r} category {category!r} is "
                    f"{status.value!r}, required {PointInTimeIntegrity.POINT_IN_TIME.value!r}"
                )

    for flag_name, is_set in snapshot.synthetic_flags.items():
        if is_set:
            missing.append(f"synthetic_flags[{flag_name!r}] is set")

    # Quality-provenance fix (2026-07-18): a candidate loaded from a
    # pre-2026-07-18 fixture that never declared quality_raw_score has
    # UNKNOWN quality provenance — this module cannot tell whether its
    # quality_score is genuine evidence or the neutral 50 fallback. That is
    # exactly the kind of gap the "quality" STRICT_MODE_REQUIRED_CATEGORIES
    # entry exists to catch, so it refuses STRICT replay the same way a
    # missing/synthetic quality point_in_time_status would.
    for candidate in snapshot.candidates:
        if not candidate.quality_raw_score_provided:
            missing.append(
                f"candidate {candidate.symbol!r}: quality_raw_score was not supplied "
                f"(pre-2026-07-18 fixture) — quality provenance is unknown and cannot "
                f"be verified as point-in-time"
            )

    return missing


def _classify_gate_rejection(r: dict, horizon: str) -> tuple[str, str]:
    """Order-matched, read-only mirror of the CONDITIONS inside
    dp._passes_quality_gate — used ONLY to label why the real function
    already returned False. Never makes the pass/fail decision itself;
    that authority stays entirely with dp._passes_quality_gate."""
    conf = r.get("confidence") or 0
    if conf < 25:
        return REJECTION_CONFIDENCE_BELOW_FLOOR, f"confidence={conf}"
    indicators = {item.get("indicator") for item in r.get("reasoning", []) if isinstance(item, dict)}
    if "Risk/Reward" in indicators or "Governance Risk" in indicators:
        return REJECTION_RISK_REWARD_OR_GOVERNANCE, "flagged indicator present in reasoning"
    fs_reasons = " ".join(
        item.get("reason", "") for item in r.get("reasoning", [])
        if isinstance(item, dict) and item.get("indicator") == "Financial Strength"
    )
    if "liquidity distress" in fs_reasons.lower():
        return REJECTION_LIQUIDITY_DISTRESS, "Financial Strength liquidity distress flagged"
    if horizon == "short":
        reasons = " ".join(
            item.get("reason", "") if isinstance(item, dict) else str(item)
            for item in r.get("reasoning", [])
        )
        if "Overbought" in reasons:
            return REJECTION_SHORT_TERM_OVERBOUGHT, "overbought RSI in short-term"
    return "UNKNOWN_GATE_REJECTION", "gate returned False but no known condition matched"


def _build_returns_matrix(snapshot: MarketSnapshot, symbols: list[str]) -> tuple[np.ndarray | None, str | None]:
    """Build a (T x N) array aligned to `symbols`' order from the
    snapshot's per-symbol return series, or (None, warning) if
    unavailable/misaligned — mirroring optimizer.optimize()'s own graceful
    degradation to an identity covariance when no usable matrix is
    supplied. Non-finite values are rejected earlier by
    contracts.validate_structure() (a hard structural error in every
    mode); inconsistent lengths across symbols is the one case this
    foundation downgrades to an explicit warning rather than refusing,
    since the optimizer's own graceful degradation already handles it
    safely."""
    if not snapshot.returns_by_symbol or len(symbols) < 2:
        return None, None
    if not all(sym in snapshot.returns_by_symbol for sym in symbols):
        return None, None
    series = [snapshot.returns_by_symbol[sym] for sym in symbols]
    lengths = {len(s) for s in series}
    if len(lengths) != 1:
        return None, (
            f"returns_by_symbol has inconsistent series lengths across the "
            f"selected slate {symbols} ({sorted(lengths)}) — optimizer replay "
            f"fell back to no returns matrix (identity covariance) for this horizon."
        )
    return np.array(series, dtype=float).T, None


def _refuse(snapshot: MarketSnapshot, reasons: list[str], note: str) -> ReplayResult:
    """Build a REFUSED_INCOMPLETE result — used for both structural-
    validation failures (applies in every mode) and STRICT-mode
    declared-integrity failures. No stage is replayed for a refusal."""
    refused = ReplayResult(
        status=ReplayStatus.REFUSED_INCOMPLETE,
        replay_mode=snapshot.replay_mode,
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        market=snapshot.market,
        horizon=snapshot.horizon,
        production_provenance={"invoked": {}, "not_invoked": dict(_SUPPLIED_NOT_REPLAYED_PROVENANCE)},
        input_integrity={k: v.value for k, v in snapshot.point_in_time_integrity.items()},
        stages_actually_replayed=[],
        missing_requirements=reasons,
        warnings=[note],
        unresolved_methodology_limitations=list(_UNRESOLVED_METHODOLOGY_LIMITATIONS),
    )
    refused.result_hash = refused.compute_hash()
    return refused


def replay_snapshot(snapshot: MarketSnapshot) -> ReplayResult:
    """
    Replay one historical cross-sectional snapshot through the REAL
    production ranking/eligibility/deduplication/selection/allocation
    functions. Composite score and confidence generation are NOT replayed
    — CandidateSnapshot supplies their already-computed values. See this
    module's docstring for the full scope statement.

    Structural validation (contracts.validate_structure — market/horizon/
    regime consistency/timestamp format/symbol uniqueness/score finiteness/
    sentiment-quality contradiction checks) applies in BOTH modes and
    refuses the replay if it fails, since a structurally malformed
    snapshot is unsafe to replay under any mode.

    STRICT_POINT_IN_TIME (default, and only after structural validation
    passes): refuses with a structured, machine-readable list of missing
    requirements when any mandatory input category is missing, synthetic,
    current-day-masquerading, or lacking provenance/timestamp. A refused
    result computes no ranking at all. A successful STRICT completion
    means the snapshot passed this harness's own DECLARED-integrity
    requirements — not that historical accuracy was independently proven;
    `production_equivalent` stays False regardless.

    DIAGNOSTIC: proceeds regardless of declared-integrity gaps (but never
    regardless of structural validity), and every result is labelled with
    explicit diagnostic-only disclaimers.

    Production learning is NEVER activated by this function, regardless of
    environment state — production_learning_enabled is passed as a literal
    False on every call. full_pipeline_replay and production_equivalent
    are hardcoded False on every result this function returns, in every
    mode — this function never sets either to True.
    """
    mode = snapshot.replay_mode

    structural_errors = validate_structure(snapshot)
    if structural_errors:
        return _refuse(
            snapshot, structural_errors,
            "Replay refused — snapshot failed structural validation (contracts.validate_structure), "
            "which applies in every replay mode.",
        )

    missing_requirements: list[str] = []
    warnings: list[str] = []

    if mode == ReplayMode.STRICT_POINT_IN_TIME:
        missing_requirements = _assess_strict_integrity(snapshot)
        if missing_requirements:
            return _refuse(
                snapshot, missing_requirements,
                "STRICT_POINT_IN_TIME replay refused — see missing_requirements. " + _STRICT_COMPLETION_CLARIFICATION,
            )
        warnings.append(_STRICT_COMPLETION_CLARIFICATION)
    else:
        warnings.extend(_DIAGNOSTIC_DISCLAIMERS)
        unknown_provenance_symbols = [c.symbol for c in snapshot.candidates if not c.quality_raw_score_provided]
        if unknown_provenance_symbols:
            warnings.append(
                f"UNKNOWN QUALITY PROVENANCE for {unknown_provenance_symbols} — quality_raw_score "
                "was not supplied (pre-2026-07-18 fixture); this module cannot distinguish genuine "
                "quality evidence from the neutral quality_score=50 fallback for these candidates. "
                "Never treated as production-equivalent."
            )

    import services.daily_picks as dp  # local import: keeps this module importable without pulling in the whole app at package-import time

    candidates_by_symbol = {c.symbol: c for c in snapshot.candidates}

    def _quality_provenance(symbol: str) -> str:
        c = candidates_by_symbol.get(symbol)
        if c is None or not c.quality_raw_score_provided:
            return QUALITY_PROVENANCE_UNKNOWN
        return QUALITY_PROVENANCE_GENUINE if c.quality_available else QUALITY_PROVENANCE_FALLBACK_NEUTRAL

    rows = [
        {
            "symbol": c.symbol,
            "horizon": snapshot.horizon,
            "signal": c.signal,
            "confidence": c.confidence,
            "tech_score": c.technical_score,
            "fund_score": c.fundamental_score,
            # DP-009/DP-010 semantics preserved automatically: this is the
            # real _zscore_and_rank(), not a reimplementation — a genuine
            # numeric 0 here is treated as evidence, not coalesced to 50.
            "sentiment_score": c.sentiment_score if c.sentiment_available else None,
            "quality_score": c.quality_score,
            "reasoning": c.reasoning,
            "cap_tier": c.cap_tier,
        }
        for c in snapshot.candidates
    ]

    enriched = dp._zscore_and_rank(
        rows,
        snapshot.ic_weights,
        {"label": snapshot.regime_label},
        snapshot.regime_id,
        market=snapshot.market,
        production_learning_enabled=False,  # never activate production learning from a replay
    )
    ranked = sorted(enriched, key=lambda x: x.get("ranking_alpha", 0), reverse=True)

    ranked_universe = [
        RankedCandidateResult(
            symbol=r["symbol"],
            signal=r.get("signal"),
            confidence=r.get("confidence"),
            ranking_alpha=r.get("ranking_alpha", 0.0),
            combined_alpha=r.get("combined_alpha", 0.0),
            factor_zscores=r.get("factor_zscores", {}),
            meta_alpha=r.get("meta_alpha"),
            meta_alpha_used_for_ranking=bool(r.get("meta_alpha_used_for_ranking", False)),
            quality_provenance=_quality_provenance(r["symbol"]),
        )
        for r in ranked
    ]

    rejected: list[RejectedCandidate] = []
    all_buy: list[dict] = []
    for r in ranked:
        if r.get("signal") != "BUY":
            rejected.append(RejectedCandidate(r["symbol"], "signal_filter", REJECTION_NOT_BUY_SIGNAL, f"signal={r.get('signal')}"))
            continue
        if not dp._passes_quality_gate(r, snapshot.horizon):
            code, detail = _classify_gate_rejection(r, snapshot.horizon)
            rejected.append(RejectedCandidate(r["symbol"], "quality_gate", code, detail))
            continue
        all_buy.append(r)

    all_buy_deduped, n_suppressed = dp._deduplicate_by_issuer(all_buy, snapshot.market)
    kept_symbols = {c["symbol"] for c in all_buy_deduped}
    for c in all_buy:
        if c["symbol"] not in kept_symbols:
            rejected.append(RejectedCandidate(c["symbol"], "issuer_dedup", REJECTION_ISSUER_DUPLICATE, "same issuer group already selected"))

    if snapshot.horizon == "short":
        top_buy = dp._select_short_term_top_six(all_buy_deduped)
        selection_fn = "services.daily_picks._select_short_term_top_six"
    else:
        top_buy = dp._select_with_tier_quota(all_buy_deduped, dp._MEDIUM_LONG_TIER_QUOTA_6)
        selection_fn = "services.daily_picks._select_with_tier_quota"

    top_symbols = {c["symbol"] for c in top_buy}
    for c in all_buy_deduped:
        if c["symbol"] not in top_symbols:
            rejected.append(RejectedCandidate(c["symbol"], "final_selection", REJECTION_NOT_SELECTED, "did not survive final per-horizon slate selection"))

    alphas = [r.get("ranking_alpha", 0) for r in top_buy]
    symbols = [r["symbol"] for r in top_buy]
    # DP-021: optimizer.optimize() is now invoked by
    # _compute_portfolio_allocation() for ANY non-empty slate (n>=1) — the
    # prior n==1 short-circuit was removed so a single candidate obeys the
    # same max_weight cap as every other slate size. Only n==0 short-
    # circuits. Mirror that exact condition here so provenance never
    # falsely claims the optimizer did or didn't run.
    optimizer_will_run = len(symbols) >= 1
    returns_matrix = None
    if len(symbols) > 1:
        returns_matrix, returns_warning = _build_returns_matrix(snapshot, symbols)
        if returns_warning:
            warnings.append(returns_warning)
    weights, cash_pct = dp._compute_portfolio_allocation(alphas, returns_matrix, snapshot.regime_label)
    portfolio_weights = dict(zip(symbols, weights))

    invoked = {
        "ranking": "services.daily_picks._zscore_and_rank",
        "quality_gate": "services.daily_picks._passes_quality_gate",
        "issuer_dedup": "services.daily_picks._deduplicate_by_issuer",
        "selection": selection_fn,
        "portfolio_allocation": "services.daily_picks._compute_portfolio_allocation",
    }
    not_invoked = dict(_SUPPLIED_NOT_REPLAYED_PROVENANCE)
    if optimizer_will_run:
        invoked["optimizer"] = "services.alpha_engine.optimizer.optimize"
    else:
        not_invoked["optimizer"] = (
            f"services.alpha_engine.optimizer.optimize (not called for a "
            f"{len(symbols)}-candidate slate — _compute_portfolio_allocation "
            f"handles 0 candidates without calling it, matching production)"
        )
    production_provenance = {"invoked": invoked, "not_invoked": not_invoked}

    result = ReplayResult(
        status=ReplayStatus.DIAGNOSTIC_COMPLETED if mode == ReplayMode.DIAGNOSTIC else ReplayStatus.COMPLETED,
        replay_mode=mode,
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        market=snapshot.market,
        horizon=snapshot.horizon,
        production_provenance=production_provenance,
        input_integrity={k: v.value for k, v in snapshot.point_in_time_integrity.items()},
        stages_actually_replayed=list(STAGES_REPLAYABLE_ON_COMPLETION),
        missing_requirements=missing_requirements,
        ranked_universe=ranked_universe,
        rejected_candidates=rejected,
        issuer_duplicates_suppressed=n_suppressed,
        selected_slate=symbols,
        portfolio_weights=portfolio_weights,
        cash_unallocated_pct=cash_pct,
        warnings=warnings,
        unresolved_methodology_limitations=list(_UNRESOLVED_METHODOLOGY_LIMITATIONS),
    )
    result.result_hash = result.compute_hash()
    return result


def main(argv: list[str] | None = None) -> int:
    """Optional local CLI. Requires an explicit fixture-file path, defaults
    to STRICT mode, never fetches live/production data, performs no
    persistence, writes only to stdout or a user-specified output path."""
    import argparse
    import json
    import sys

    from services.validation.contracts import snapshot_from_dict

    parser = argparse.ArgumentParser(
        description=(
            "DP-025 pipeline-replay CLI — DIAGNOSTIC ONLY unless the "
            "supplied fixture fully satisfies STRICT_POINT_IN_TIME. Never "
            "fetches live or production data; requires an explicit local "
            "fixture file."
        )
    )
    parser.add_argument("fixture", help="Path to a local JSON snapshot fixture file (required — no default).")
    parser.add_argument(
        "--mode", choices=["strict", "diagnostic"], default="strict",
        help="Replay mode. Defaults to strict (the only mode whose output may be treated as evidence of anything).",
    )
    parser.add_argument("--output", default=None, help="Optional local file path to write the JSON result to. Defaults to stdout.")
    args = parser.parse_args(argv)

    with open(args.fixture) as f:
        raw = json.load(f)
    snapshot = snapshot_from_dict(raw)
    # The CLI's --mode flag is always authoritative over whatever the
    # fixture file itself declares — a fixture cannot opt itself out of the
    # default-strict safety guarantee just by setting its own replay_mode.
    snapshot.replay_mode = ReplayMode.DIAGNOSTIC if args.mode == "diagnostic" else ReplayMode.STRICT_POINT_IN_TIME

    result = replay_snapshot(snapshot)
    output_dict = result.to_json_dict()
    output_dict["result_hash"] = result.result_hash
    output_json = json.dumps(output_dict, indent=2, sort_keys=True)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
    else:
        print(output_json)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
