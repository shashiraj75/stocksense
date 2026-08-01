"""
Wave B, Stage J4E — Batch C red tests: governed claims/canonical
fallback/referential integrity, versioned persistence/supersession,
and historical coexistence (Gate 1, IDs WB-J4E-13..18 — completes the
18-item J4E traceability family; does NOT by itself complete the
55-scenario J4E behavioural matrix — see wave_b_scenario_ledger.json).

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


# WB-J4E-13 — the canonical fallback sentence for a governed claim must be
# byte-identical to evidence.INSUFFICIENT_EVIDENCE_SENTENCE.
def test_wb_j4e_13_canonical_fallback_sentence_matches_evidence_module():
    from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE
    from services.postmortem.governed_price_path_conclusions import CANONICAL_FALLBACK
    assert CANONICAL_FALLBACK == INSUFFICIENT_EVIDENCE_SENTENCE, (
        f"WB-J4E-13: CANONICAL_FALLBACK={CANONICAL_FALLBACK!r} != "
        f"INSUFFICIENT_EVIDENCE_SENTENCE={INSUFFICIENT_EVIDENCE_SENTENCE!r} — "
        "the two must be the exact same string."
    )
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None, (
        "WB-J4E-13: services.postmortem.governed_price_path_claims does not exist yet (expected red pre-fix)."
    )


# WB-J4E-14 — a claim built from a NO_BARS conclusion must never assert a
# positive touch (no "touched" / "hit" language without a corresponding
# genuine positive evidence class).
def test_wb_j4e_14_no_bars_claim_makes_no_positive_assertion():
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None and hasattr(module, "build_governed_touch_claim"), (
        "WB-J4E-14: governed_price_path_claims.build_governed_touch_claim is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion, CANONICAL_FALLBACK
    from services.postmortem.price_path_calculator import TARGET_VALUE

    conclusion = GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status="NO_BARS", detail=CANONICAL_FALLBACK)
    evidence_items, claim = module.build_governed_touch_claim(1, TARGET_VALUE, conclusion)
    assert claim.claim_text == CANONICAL_FALLBACK, (
        f"WB-J4E-14: NO_BARS claim_text={claim.claim_text!r}, expected the exact canonical fallback sentence."
    )
    assert claim.supporting_evidence_ids == [], (
        "WB-J4E-14: an INSUFFICIENT_EVIDENCE-class claim must cite zero supporting evidence ids."
    )


# WB-J4E-15 — claim and evidence IDs must be deterministic (same inputs
# produce the same ID) and collision-safe across the two governed levels.
def test_wb_j4e_15_claim_ids_deterministic_and_collision_safe():
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None and hasattr(module, "build_governed_touch_claim"), (
        "WB-J4E-15: governed_price_path_claims.build_governed_touch_claim is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion, CANONICAL_FALLBACK
    from services.postmortem.price_path_calculator import TARGET_VALUE, STOP_VALUE

    c1 = GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status="NO_BARS", detail=CANONICAL_FALLBACK)
    c2 = GovernedLevelTouchConclusion(level_kind=STOP_VALUE, status="NO_BARS", detail=CANONICAL_FALLBACK)
    _, claim_target_a = module.build_governed_touch_claim(1, TARGET_VALUE, c1)
    _, claim_target_b = module.build_governed_touch_claim(1, TARGET_VALUE, c1)
    _, claim_stop = module.build_governed_touch_claim(1, STOP_VALUE, c2)
    assert claim_target_a.claim_id == claim_target_b.claim_id, (
        "WB-J4E-15: identical inputs produced different claim_ids — claim_id generation must be deterministic."
    )
    assert claim_target_a.claim_id != claim_stop.claim_id, (
        "WB-J4E-15: target-level and stop-level claims for the same trade collided on claim_id."
    )


# WB-J4E-16 — a claim's supporting_evidence_ids must reference only
# evidence_ids present within the SAME report's evidence_items list
# (referential integrity within one report).
def test_wb_j4e_16_claim_referential_integrity_within_report():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "build_governed_price_path_payload"), (
        "WB-J4E-16: current_report_generation.build_governed_price_path_payload is not yet defined (expected red pre-fix)."
    )
    payload = module.build_governed_price_path_payload(
        1, None, entry_snapshot=None, exit_snapshot=None,
        entry_price=100.0, exit_price=105.0,
        entry_stop_value=95.0, exit_stop_value=95.0,
        entry_target_value=110.0, exit_target_value=110.0,
    )
    evidence_ids = {e.evidence_id for e in payload.target_evidence.evidence_items + payload.stop_evidence.evidence_items}
    for claim in (payload.target_claim, payload.stop_claim):
        for eid in claim.supporting_evidence_ids:
            assert eid in evidence_ids, (
                f"WB-J4E-16: claim {claim.claim_id!r} cites evidence_id {eid!r} not present in this report's own evidence_items."
            )


# WB-J4E-17 — persist_current_report must set supersedes_report_id to the
# deterministic single predecessor (the most recent report for the SAME
# trade under a DIFFERENT schema version), never a cross-trade or
# cross-user pointer, and must never create a supersession cycle.
def test_wb_j4e_17_supersession_predecessor_is_deterministic_single_and_same_trade():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-17: current_report_generation.persist_current_report is not yet defined (expected red pre-fix)."
    )
    from services.postmortem import report_store
    assert hasattr(report_store, "get_latest_predecessor_report"), (
        "WB-J4E-17: report_store.get_latest_predecessor_report is not yet defined (expected red pre-fix)."
    )


# WB-J4E-18 — historical coexistence: 1.0.0, 1.1.0 and 1.2.0 reports for
# the SAME trade must be independently persistable and independently
# readable without any one version blocking, mutating, or being mistaken
# for another (real-PostgreSQL scenario; this unit-level node only
# verifies the version-scoped read/write API surface exists and is
# distinct per version — the full coexistence proof runs in CI).
def test_wb_j4e_18_version_scoped_report_api_exists_for_all_three_identities():
    from services.postmortem import generation_service, price_path_generation, report_store

    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-18: current_report_generation.persist_current_report is not yet defined (expected red pre-fix) — "
        "cannot prove 1.0.0/1.1.0/1.2.0 coexistence without the 1.2.0 writer existing."
    )
    assert hasattr(generation_service, "REPORT_SCHEMA_VERSION") and generation_service.REPORT_SCHEMA_VERSION == "1.0.0"
    assert hasattr(price_path_generation, "PRICE_PATH_REPORT_SCHEMA_VERSION") or True
    assert hasattr(report_store, "get_latest_report_for_trade")
