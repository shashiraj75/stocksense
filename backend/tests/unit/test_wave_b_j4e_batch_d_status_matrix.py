"""
Wave B, Stage J4E — Batch D red tests: scenario-matrix expansion under
WB-J4E-14 (claim honesty) and WB-J4E-16 (referential integrity) —
one scenario per governed touch status (7) and one per governed order
status (7) = 14 additional scenarios, closing part of the disclosed
18-vs-55 gap recorded in wave_b_scenario_ledger.json.

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util

import pytest

from services.postmortem.governed_price_path_conclusions import (
    CANONICAL_FALLBACK,
    GOVERNED_ORDER_NEITHER_OBSERVED,
    GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN,
    GOVERNED_ORDER_STOP_ONLY_OBSERVED,
    GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET,
    GOVERNED_ORDER_TARGET_ONLY_OBSERVED,
    GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP,
    GOVERNED_ORDER_UNAVAILABLE,
    GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
    GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
    GOVERNED_TOUCH_NO_VALUE_SUPPLIED,
    GOVERNED_TOUCH_SUPPORTED,
    GovernedLevelTouchConclusion,
    GovernedOrderConclusion,
)
from services.postmortem.price_path_calculator import TARGET_VALUE

_ALL_TOUCH_STATUSES = [
    GOVERNED_TOUCH_NO_VALUE_SUPPLIED,
    GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
    GOVERNED_TOUCH_SUPPORTED,
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS,
    GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
    GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
]

_FALLBACK_TOUCH_STATUSES = {
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS,
    GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
    GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
    GOVERNED_TOUCH_NO_VALUE_SUPPLIED,
    GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
}

_ALL_ORDER_STATUSES = [
    GOVERNED_ORDER_NEITHER_OBSERVED,
    GOVERNED_ORDER_TARGET_ONLY_OBSERVED,
    GOVERNED_ORDER_STOP_ONLY_OBSERVED,
    GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP,
    GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET,
    GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN,
    GOVERNED_ORDER_UNAVAILABLE,
]


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


@pytest.mark.parametrize("touch_status", _ALL_TOUCH_STATUSES)
def test_wb_j4e_14_variant_claim_text_matches_status_class(touch_status):
    """For every governed touch status, the built claim must either be the
    exact canonical fallback (fallback statuses) or a genuine non-fallback
    claim_text (SUPPORTED_TOUCH only) — never a mismatch between status
    class and claim content."""
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None and hasattr(module, "build_governed_touch_claim"), (
        f"WB-J4E-14[{touch_status}]: governed_price_path_claims.build_governed_touch_claim "
        "is not yet defined (expected red pre-fix)."
    )
    detail = CANONICAL_FALLBACK if touch_status in _FALLBACK_TOUCH_STATUSES else "target level was touched"
    conclusion = GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status=touch_status, detail=detail)
    _, claim = module.build_governed_touch_claim(1, TARGET_VALUE, conclusion)
    if touch_status in _FALLBACK_TOUCH_STATUSES:
        assert claim.claim_text == CANONICAL_FALLBACK, (
            f"WB-J4E-14[{touch_status}]: fallback status produced claim_text={claim.claim_text!r}, "
            "expected the exact canonical fallback sentence."
        )
        assert claim.supporting_evidence_ids == [], (
            f"WB-J4E-14[{touch_status}]: fallback-status claim must cite zero supporting evidence."
        )
    else:
        assert claim.claim_text != CANONICAL_FALLBACK, (
            f"WB-J4E-14[{touch_status}]: a genuine SUPPORTED_TOUCH conclusion produced the canonical "
            "fallback sentence instead of a real claim."
        )
        assert claim.supporting_evidence_ids, (
            f"WB-J4E-14[{touch_status}]: a genuine positive claim must cite at least one supporting evidence_id."
        )


@pytest.mark.parametrize("order_status", _ALL_ORDER_STATUSES)
def test_wb_j4e_16_variant_order_claim_referential_integrity(order_status):
    """For every governed order status, the order claim's supporting
    evidence ids must be a subset of the target/stop evidence ids passed
    in — never inventing a reference to evidence that doesn't exist."""
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None and hasattr(module, "build_governed_order_claim"), (
        f"WB-J4E-16[{order_status}]: governed_price_path_claims.build_governed_order_claim "
        "is not yet defined (expected red pre-fix)."
    )
    detail = CANONICAL_FALLBACK if order_status == GOVERNED_ORDER_UNAVAILABLE else "target observed before stop"
    conclusion = GovernedOrderConclusion(status=order_status, detail=detail)
    target_evidence_ids = ["ev-target-1"]
    stop_evidence_ids = ["ev-stop-1"]
    _, claim = module.build_governed_order_claim(1, conclusion, target_evidence_ids, stop_evidence_ids)
    allowed = set(target_evidence_ids) | set(stop_evidence_ids)
    for eid in claim.supporting_evidence_ids:
        assert eid in allowed, (
            f"WB-J4E-16[{order_status}]: order claim cites evidence_id {eid!r} not present in the "
            "target/stop evidence ids passed to the builder."
        )
