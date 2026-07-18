"""
DP-019 — deterministic, offline, shadow-only A/B comparator: how do the
current hand-set `REGIME_WEIGHT_MULTIPLIERS` change ranking, final
selection, and portfolio allocation versus a no-multiplier baseline, for
one explicitly supplied `MarketSnapshot`?

No owner decision was required to build this comparator (it evaluates the
existing, already-decided multiplier policy — it does not change or
approve it), and none was made. `REGIME_WEIGHT_MULTIPLIERS`, production
ranking, production factor weights, production-learning containment, and
every DP-017/DP-018 regime behaviour are all UNCHANGED and UNTOUCHED by
this module.

WHAT THIS EVALUATES: the currently CONTAINED production weighting path —
    academic-prior IC values (services.alpha_engine.ic_engine.ACADEMIC_PRIOR_IC)
    -> optional regime multipliers (REGIME_WEIGHT_MULTIPLIERS)
    -> normalised factor weights (ic_engine._prior_weights, the real
       production helper — NOT reimplemented here)
Every result carries `weight_source = "contained_academic_prior"`. This
module NEVER calls `get_ic_weights()`, `_compute_live_ic()`, any
prediction/outcome store, or any production-database/environment-dependent
live-IC path — it therefore says nothing about a future live-learned IC
weight regime, and never claims to.

HOW THE TWO BRANCHES ARE ISOLATED: Branch A (MULTIPLIER_ON) and Branch B
(MULTIPLIER_OFF) are both built via `copy.deepcopy()` of the SAME supplied
snapshot, differing in exactly one field — `ic_weights`. Every other field
(candidates, their order, market, horizon, regime_id, regime_label,
regime_weight_multipliers, returns_by_symbol, point-in-time declarations)
is byte-identical between the two branches by construction — deepcopy of
one shared source, not two independently assembled snapshots. Each branch
is then replayed through the REAL, unmodified
`services.validation.pipeline_replay.replay_snapshot()` — this module never
reimplements ranking, quality-gate, issuer-dedup, selection, or portfolio-
allocation logic. The optimizer's regime-label risk-aversion adjustment is
NOT neutralised (both branches supply the same `regime_label`) — DP-019
isolates the factor-weight-multiplier effect only, not every regime-aware
behaviour in the platform.

SIDE-EFFECT PROHIBITION: this module performs no network access, no
persistence, never activates production learning (replay_snapshot() always
passes `production_learning_enabled=False`), never invokes Daily Picks
generation, and never retrains any model. It is not invoked by any
production code path, scheduler, or API endpoint.

INTERPRETATION: every result is a SINGLE-SNAPSHOT sensitivity comparison.
It measures behavioural impact (rank/slate/portfolio change), never
predictive value — no forward return, hit rate, accuracy, or profitability
is calculated anywhere in this module, and `production_equivalent`,
`performance_validated`, and `multiplier_policy_approved` are hardcoded
False on every result, in every mode, and can never become True here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from enum import Enum

from services.validation.contracts import MarketSnapshot, ReplayMode, ReplayStatus, ReplayResult
from services.validation.pipeline_replay import replay_snapshot


class ABStatus(str, Enum):
    COMPLETED = "completed"
    REFUSED_INVALID_INPUT = "refused_invalid_input"
    REFUSED_UNDERLYING_REPLAY = "refused_underlying_replay"


#: The only weight source this first comparator evaluates — see module
#: docstring's "WHAT THIS EVALUATES" section.
WEIGHT_SOURCE_CONTAINED_ACADEMIC_PRIOR = "contained_academic_prior"

_REQUIRED_MULTIPLIER_KEYS: tuple[str, ...] = ("tech", "fund", "sentiment", "quality")
_MULTIPLIER_TOLERANCE = 1e-6
#: ic_engine._prior_weights() rounds each weight to 4 decimal places.
_IC_WEIGHT_TOLERANCE = 1e-4

#: Explicitly documented test-only/diagnostic escape hatch (per DP-019's
#: "Diagnostic synthetic fixtures may omit production-evidence matching
#: only through one explicitly documented test-only or diagnostic
#: contract" requirement). Set `snapshot.synthetic_flags[<this key>] = True`
#: AND `snapshot.replay_mode == ReplayMode.DIAGNOSTIC` to skip the
#: ic_weights-matches-current-production check (requirement #7). Any result
#: produced this way remains DIAGNOSTIC and carries the same
#: non-production-equivalence disclaimers as every other diagnostic result
#: — this flag never grants STRICT-mode bypass, and never changes
#: `production_equivalent` (always False regardless).
DP019_IC_WEIGHTS_DIAGNOSTIC_ONLY_FLAG = "dp019_ic_weights_diagnostic_only"

DP019_LIMITATIONS: tuple[str, ...] = (
    "This is a single-snapshot sensitivity comparison, not a multi-snapshot or longitudinal study.",
    "It measures behavioral impact (ranking/selection/allocation change), not predictive value.",
    "No forward return is calculated.",
    "No hit rate is calculated.",
    "No accuracy is calculated.",
    "No profitability is calculated.",
    "It does not establish that the current regime multiplier policy improves outcomes.",
    "It does not approve or reject any multiplier value.",
    "DP-026 through DP-029 remain unresolved.",
    "Production learning remains disabled and is never activated by this comparator.",
    "Historical point-in-time integrity is only as strong as the supplied snapshot "
    "(see the underlying replay's own input_integrity/warnings).",
)


@dataclass
class RegimeMultiplierABResult:
    status: str
    snapshot_id: str
    as_of: str
    market: str
    horizon: str
    regime_id: int
    regime_label: str
    weight_source: str = WEIGHT_SOURCE_CONTAINED_ACADEMIC_PRIOR
    multiplier_map: dict[str, float] = field(default_factory=dict)
    multiplier_on_weights: dict[str, float] = field(default_factory=dict)
    multiplier_off_weights: dict[str, float] = field(default_factory=dict)
    multiplier_on_replay: ReplayResult | None = None
    multiplier_off_replay: ReplayResult | None = None
    rank_deltas: list[dict] = field(default_factory=list)
    selected_slate_overlap: int = 0
    selected_slate_overlap_ratio: float = 0.0
    selected_only_with_multiplier: list[str] = field(default_factory=list)
    selected_only_without_multiplier: list[str] = field(default_factory=list)
    selected_in_both: list[str] = field(default_factory=list)
    selected_order_changed: bool = False
    issuer_duplicates_suppressed_delta: int = 0
    rejection_reason_diffs: list[dict] = field(default_factory=list)
    portfolio_weight_deltas: dict[str, float] = field(default_factory=dict)
    cash_unallocated_delta: float | None = None
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: list(DP019_LIMITATIONS))
    # Fixed False on EVERY result this module ever produces, in every mode.
    # Never set to True anywhere in this module.
    production_equivalent: bool = False
    performance_validated: bool = False
    multiplier_policy_approved: bool = False
    result_hash: str = ""

    def to_json_dict(self) -> dict:
        """Deterministic, JSON-serializable representation (enums -> their
        .value, nested dataclasses -> plain dicts). Does NOT include
        result_hash (compute that from this dict's own serialization)."""
        def _default(o):
            if isinstance(o, Enum):
                return o.value
            raise TypeError(f"not JSON-serializable: {type(o)!r}")
        d = asdict(self)
        d.pop("result_hash", None)
        return json.loads(json.dumps(d, default=_default, sort_keys=True))

    def compute_hash(self) -> str:
        """Stable SHA-256 hex digest of the deterministic JSON
        representation — same snapshot must always produce the same hash."""
        payload = json.dumps(self.to_json_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_finite_nonnegative_number(x) -> bool:
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and x >= 0


def _validate_regime_and_multipliers(snapshot: MarketSnapshot) -> tuple[list[str], dict[str, float] | None, str | None]:
    """Requirements #1-6. Returns (errors, production_multiplier_map,
    expected_regime_label). production_multiplier_map is None if a
    prerequisite error prevents deriving it (e.g. unknown regime)."""
    errors: list[str] = []

    if snapshot.market not in ("IN", "US"):
        errors.append(f"market {snapshot.market!r} is not one of ('IN', 'US')")

    if snapshot.horizon not in ("short", "medium", "long"):
        errors.append(f"horizon {snapshot.horizon!r} is not one of ('short', 'medium', 'long')")

    from services.alpha_engine.regime_cluster import REGIME_LABELS, REGIME_WEIGHT_MULTIPLIERS

    if snapshot.regime_id not in REGIME_LABELS:
        errors.append(
            f"regime_id {snapshot.regime_id!r} is not in production's REGIME_LABELS "
            f"(valid ids: {sorted(REGIME_LABELS)})"
        )
        return errors, None, None

    expected_label = REGIME_LABELS[snapshot.regime_id]
    if snapshot.regime_label != expected_label:
        errors.append(
            f"regime_label {snapshot.regime_label!r} does not match production's label "
            f"for regime_id {snapshot.regime_id!r} ({expected_label!r})"
        )
        return errors, None, expected_label

    production_map = REGIME_WEIGHT_MULTIPLIERS.get(expected_label)
    if production_map is None:
        errors.append(f"no production REGIME_WEIGHT_MULTIPLIERS entry for label {expected_label!r}")
        return errors, None, expected_label

    supplied_map = snapshot.regime_weight_multipliers or {}
    supplied_keys = set(supplied_map.keys())
    required_keys = set(_REQUIRED_MULTIPLIER_KEYS)
    missing_keys = required_keys - supplied_keys
    extra_keys = supplied_keys - required_keys

    if missing_keys:
        errors.append(f"snapshot.regime_weight_multipliers is missing key(s): {sorted(missing_keys)}")
    if extra_keys:
        errors.append(f"snapshot.regime_weight_multipliers has unexpected extra key(s): {sorted(extra_keys)}")

    for k in sorted(required_keys & supplied_keys):
        v = supplied_map[k]
        if not _is_finite_nonnegative_number(v):
            errors.append(f"snapshot.regime_weight_multipliers[{k!r}]={v!r} is not a finite non-negative number")

    if not missing_keys and not extra_keys and not errors:
        for k in _REQUIRED_MULTIPLIER_KEYS:
            supplied_v = supplied_map.get(k)
            prod_v = production_map.get(k)
            if not math.isclose(supplied_v, prod_v, rel_tol=0, abs_tol=_MULTIPLIER_TOLERANCE):
                errors.append(
                    f"snapshot.regime_weight_multipliers[{k!r}]={supplied_v!r} does not match current "
                    f"production REGIME_WEIGHT_MULTIPLIERS[{expected_label!r}][{k!r}]={prod_v!r} within "
                    f"tolerance {_MULTIPLIER_TOLERANCE} — the snapshot may represent a stale or "
                    f"different multiplier configuration."
                )

    return errors, dict(production_map), expected_label


def _validate_ic_weights_evidence(snapshot: MarketSnapshot, on_weights: dict[str, float]) -> list[str]:
    """Requirement #7. Skipped only via the explicitly documented
    DP019_IC_WEIGHTS_DIAGNOSTIC_ONLY_FLAG, and only in DIAGNOSTIC mode."""
    is_diagnostic_bypass = (
        bool(snapshot.synthetic_flags.get(DP019_IC_WEIGHTS_DIAGNOSTIC_ONLY_FLAG))
        and snapshot.replay_mode == ReplayMode.DIAGNOSTIC
    )
    if is_diagnostic_bypass:
        return []

    errors: list[str] = []
    supplied = snapshot.ic_weights or {}

    for k, expected_v in on_weights.items():
        supplied_v = supplied.get(k)
        if supplied_v is None or not _is_finite_nonnegative_number(supplied_v) or not math.isclose(
            supplied_v, expected_v, rel_tol=0, abs_tol=_IC_WEIGHT_TOLERANCE
        ):
            errors.append(
                f"snapshot.ic_weights[{k!r}]={supplied_v!r} does not match current contained-production "
                f"MULTIPLIER_ON weight {expected_v!r} within tolerance {_IC_WEIGHT_TOLERANCE} — the "
                f"snapshot may represent a different weight source, configuration version, or "
                f"production-learning state than DP-019's current-production comparator supports."
            )

    extra = set(supplied.keys()) - set(on_weights.keys())
    if extra:
        errors.append(f"snapshot.ic_weights has unexpected extra key(s) not in the production weight set: {sorted(extra)}")

    return errors


def _refuse_invalid_input(snapshot: MarketSnapshot, errors: list[str]) -> RegimeMultiplierABResult:
    result = RegimeMultiplierABResult(
        status=ABStatus.REFUSED_INVALID_INPUT.value,
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        market=snapshot.market,
        horizon=snapshot.horizon,
        regime_id=snapshot.regime_id,
        regime_label=snapshot.regime_label,
        validation_errors=errors,
        warnings=[
            "Refused before running any comparison — the supplied snapshot failed DP-019's "
            "input-validation checks (requirements #1-7). No rank, slate, or portfolio "
            "comparison was computed."
        ],
    )
    result.result_hash = result.compute_hash()
    return result


def _build_branch_snapshot(base: MarketSnapshot, ic_weights: dict[str, float]) -> MarketSnapshot:
    """Deep-copies `base` and swaps only `ic_weights` — every other field
    (candidates, their order, market, horizon, regime_id, regime_label,
    regime_weight_multipliers, returns_by_symbol, point-in-time
    declarations) is byte-identical to `base` by construction. Never
    mutates `base` itself."""
    branch = copy.deepcopy(base)
    branch.ic_weights = dict(ic_weights)
    return branch


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _rank_index(ranked_universe) -> dict[str, tuple[int, object]]:
    """1-based rank, in the replay's own resulting (tie-broken) order."""
    return {r.symbol: (i + 1, r) for i, r in enumerate(ranked_universe)}


def _rejection_index(rejected_candidates) -> dict[str, tuple[str, str]]:
    return {r.symbol: (r.stage, r.reason_code) for r in rejected_candidates}


def compare_regime_multiplier(snapshot: MarketSnapshot) -> RegimeMultiplierABResult:
    """
    DP-019 — the single public entry point. Never mutates `snapshot`.
    Validates input (requirements #1-7), then builds two independent
    branch-snapshot copies differing only in `ic_weights`, replays each
    through the real `pipeline_replay.replay_snapshot()`, and reports a
    behavioral A/B comparison. See module docstring for the full contract.
    """
    validation_errors, production_multiplier_map, expected_label = _validate_regime_and_multipliers(snapshot)
    if validation_errors:
        return _refuse_invalid_input(snapshot, validation_errors)

    from services.alpha_engine.ic_engine import _prior_weights

    on_weights = _prior_weights(snapshot.horizon, snapshot.market, production_multiplier_map)
    off_weights = _prior_weights(snapshot.horizon, snapshot.market, None)

    ic_errors = _validate_ic_weights_evidence(snapshot, on_weights)
    if ic_errors:
        return _refuse_invalid_input(snapshot, ic_errors)

    on_snapshot = _build_branch_snapshot(snapshot, on_weights)
    off_snapshot = _build_branch_snapshot(snapshot, off_weights)

    on_replay = replay_snapshot(on_snapshot)
    off_replay = replay_snapshot(off_snapshot)

    base_kwargs = dict(
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        market=snapshot.market,
        horizon=snapshot.horizon,
        regime_id=snapshot.regime_id,
        regime_label=snapshot.regime_label,
        multiplier_map=dict(production_multiplier_map),
        multiplier_on_weights=dict(on_weights),
        multiplier_off_weights=dict(off_weights),
        multiplier_on_replay=on_replay,
        multiplier_off_replay=off_replay,
    )

    if on_replay.status == ReplayStatus.REFUSED_INCOMPLETE or off_replay.status == ReplayStatus.REFUSED_INCOMPLETE:
        # Refusal propagation — never manufacture a rank/slate/portfolio
        # comparison from a refused underlying replay.
        result = RegimeMultiplierABResult(
            status=ABStatus.REFUSED_UNDERLYING_REPLAY.value,
            warnings=_dedup_preserve_order(list(on_replay.warnings) + list(off_replay.warnings) + [
                "Refused — at least one underlying replay branch (MULTIPLIER_ON and/or "
                "MULTIPLIER_OFF) returned REFUSED_INCOMPLETE. See multiplier_on_replay/"
                "multiplier_off_replay.missing_requirements for the underlying reasons. "
                "No rank, slate, or portfolio comparison was computed."
            ]),
            **base_kwargs,
        )
        result.result_hash = result.compute_hash()
        return result

    on_idx = _rank_index(on_replay.ranked_universe)
    off_idx = _rank_index(off_replay.ranked_universe)
    on_selected = set(on_replay.selected_slate)
    off_selected = set(off_replay.selected_slate)

    all_symbols = sorted(set(on_idx) | set(off_idx))
    rank_deltas = []
    for symbol in all_symbols:
        on_rank, on_r = on_idx.get(symbol, (None, None))
        off_rank, off_r = off_idx.get(symbol, (None, None))
        on_alpha = on_r.combined_alpha if on_r is not None else None
        off_alpha = off_r.combined_alpha if off_r is not None else None
        # Convention: positive rank_delta = improved (numerically lower,
        # i.e. better) rank when multipliers are enabled.
        rank_delta = (off_rank - on_rank) if (on_rank is not None and off_rank is not None) else None
        alpha_delta = (on_alpha - off_alpha) if (on_alpha is not None and off_alpha is not None) else None
        rank_deltas.append({
            "symbol": symbol,
            "rank_with_multiplier": on_rank,
            "rank_without_multiplier": off_rank,
            "rank_delta": rank_delta,
            "combined_alpha_with_multiplier": on_alpha,
            "combined_alpha_without_multiplier": off_alpha,
            "combined_alpha_delta": alpha_delta,
            "selected_with_multiplier": symbol in on_selected,
            "selected_without_multiplier": symbol in off_selected,
        })

    selected_in_both = sorted(on_selected & off_selected)
    selected_only_with_multiplier = sorted(on_selected - off_selected)
    selected_only_without_multiplier = sorted(off_selected - on_selected)
    union = on_selected | off_selected
    overlap_ratio = (len(selected_in_both) / len(union)) if union else 1.0

    common_set = on_selected & off_selected
    common_order_on = [s for s in on_replay.selected_slate if s in common_set]
    common_order_off = [s for s in off_replay.selected_slate if s in common_set]
    selected_order_changed = common_order_on != common_order_off

    on_rej = _rejection_index(on_replay.rejected_candidates)
    off_rej = _rejection_index(off_replay.rejected_candidates)
    rejection_reason_diffs = []
    for symbol in sorted(set(on_rej) | set(off_rej)):
        on_v = on_rej.get(symbol)
        off_v = off_rej.get(symbol)
        if on_v != off_v:
            rejection_reason_diffs.append({
                "symbol": symbol,
                "rejection_with_multiplier": {"stage": on_v[0], "reason_code": on_v[1]} if on_v else None,
                "rejection_without_multiplier": {"stage": off_v[0], "reason_code": off_v[1]} if off_v else None,
            })

    all_portfolio_symbols = sorted(set(on_replay.portfolio_weights) | set(off_replay.portfolio_weights))
    portfolio_weight_deltas = {
        symbol: round(
            on_replay.portfolio_weights.get(symbol, 0.0) - off_replay.portfolio_weights.get(symbol, 0.0), 6
        )
        for symbol in all_portfolio_symbols
    }
    cash_unallocated_delta = None
    if on_replay.cash_unallocated_pct is not None and off_replay.cash_unallocated_pct is not None:
        cash_unallocated_delta = round(on_replay.cash_unallocated_pct - off_replay.cash_unallocated_pct, 6)

    result = RegimeMultiplierABResult(
        status=ABStatus.COMPLETED.value,
        rank_deltas=rank_deltas,
        selected_slate_overlap=len(selected_in_both),
        selected_slate_overlap_ratio=round(overlap_ratio, 6),
        selected_only_with_multiplier=selected_only_with_multiplier,
        selected_only_without_multiplier=selected_only_without_multiplier,
        selected_in_both=selected_in_both,
        selected_order_changed=selected_order_changed,
        issuer_duplicates_suppressed_delta=(
            on_replay.issuer_duplicates_suppressed - off_replay.issuer_duplicates_suppressed
        ),
        rejection_reason_diffs=rejection_reason_diffs,
        portfolio_weight_deltas=portfolio_weight_deltas,
        cash_unallocated_delta=cash_unallocated_delta,
        warnings=_dedup_preserve_order(list(on_replay.warnings) + list(off_replay.warnings)),
        **base_kwargs,
    )
    result.result_hash = result.compute_hash()
    return result


def main(argv: list[str] | None = None) -> int:
    """Optional local CLI. Requires an explicit fixture-file path, defaults
    to STRICT mode via the underlying snapshot contract, never fetches
    live/production data, performs no persistence, writes only to stdout
    or a user-specified local output path. SHADOW/DIAGNOSTIC ONLY — see
    DP019_LIMITATIONS and the module docstring."""
    import argparse
    import json as _json

    from services.validation.contracts import snapshot_from_dict

    parser = argparse.ArgumentParser(
        description=(
            "DP-019 regime-multiplier A/B comparator CLI — SHADOW ONLY, DIAGNOSTIC/"
            "SENSITIVITY-ANALYSIS ONLY. Never fetches live or production data; requires "
            "an explicit local fixture file. Does not approve, reject, or validate the "
            "performance of any multiplier policy."
        )
    )
    parser.add_argument("fixture", help="Path to a local JSON MarketSnapshot fixture file (required — no default).")
    parser.add_argument(
        "--mode", choices=["strict", "diagnostic"], default="strict",
        help="Underlying replay mode for both branches. Defaults to strict.",
    )
    parser.add_argument("--output", default=None, help="Optional local file path to write the JSON result to. Defaults to stdout.")
    args = parser.parse_args(argv)

    with open(args.fixture) as f:
        raw = _json.load(f)
    snapshot = snapshot_from_dict(raw)
    snapshot.replay_mode = ReplayMode.DIAGNOSTIC if args.mode == "diagnostic" else ReplayMode.STRICT_POINT_IN_TIME

    result = compare_regime_multiplier(snapshot)
    output_dict = result.to_json_dict()
    output_dict["result_hash"] = result.result_hash
    output_json = _json.dumps(output_dict, indent=2, sort_keys=True)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
    else:
        print(output_json)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
