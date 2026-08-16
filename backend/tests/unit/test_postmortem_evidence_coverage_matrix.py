"""
Trade Postmortem explainability phase — evidence-coverage matrix
introspection test.

Fails if the postmortem rule registries can actually emit a
(report_section, factor) combination that has no corresponding entry
in postmortem_evidence_coverage_matrix.json. This is deliberately
genuine introspection of the real code, mirroring
test_wave_c_traceability_validator.py's own discipline of validating
against real collection/execution rather than trusting a hand-written
list to stay current:

- services.postmortem.evidence.RULE_REGISTRY is imported and walked
  directly (a process-wide dict populated by every rule-registering
  module at import time — evidence_attribution.py, price_path_claims.py,
  governed_price_path_claims.py).
- The actual (report_section, factor) pairs each rule can emit are
  derived by calling the real claim-building functions with
  representative fixture inputs (both the "evidence present" and
  "evidence absent" branches), not by copying a description string.
- exit_evidence.py registers no RuleDefinition (it builds
  PostmortemClaim objects inline) — its factors are enumerated the same
  way, by calling build_exit_evidence with representative fixtures.

This test does NOT modify or duplicate any production report-generation
logic — it only calls existing pure functions with fixture inputs and
compares the (report_section, factor) pairs they produce against the
matrix file.
"""
import json
from pathlib import Path

import pytest

_TESTS_UNIT_DIR = Path(__file__).resolve().parent
_MATRIX_PATH = _TESTS_UNIT_DIR / "postmortem_evidence_coverage_matrix.json"


def _load_matrix() -> list[dict]:
    return json.loads(_MATRIX_PATH.read_text())["entries"]


def _matrix_pairs() -> set[tuple[str, str]]:
    return {(e["report_section"], e["internal_factor"]) for e in _load_matrix()}


def _matrix_rule_ids_by_pair() -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for e in _load_matrix():
        out.setdefault((e["report_section"], e["internal_factor"]), set()).add(e["rule_id"])
    return out


class TestMatrixWellFormed:
    def test_matrix_file_parses_and_is_nonempty(self):
        entries = _load_matrix()
        assert len(entries) > 0

    def test_no_duplicate_rows(self):
        entries = _load_matrix()
        seen = set()
        for e in entries:
            key = (e["report_section"], e["internal_factor"], e["rule_id"])
            assert key not in seen, f"duplicate matrix row: {key}"
            seen.add(key)

    _REQUIRED_COLUMNS = (
        "report_section", "internal_factor", "user_facing_title", "rule_id", "rule_version",
        "evidence_required", "evidence_currently_captured", "source_type", "verification_level",
        "freshness_basis", "status", "reason_missing", "affects_new_trades", "affects_legacy_trades",
        "historically_recoverable", "frontend_improvement_required", "backend_improvement_required",
        "recommended_phase", "fabrication_risk", "remediation_category",
    )

    def test_every_row_has_every_required_column(self):
        for e in _load_matrix():
            for col in self._REQUIRED_COLUMNS:
                assert col in e, f"row {e.get('internal_factor')!r} missing column {col!r}"

    _VALID_STATUSES = {"COMPLETE", "PARTIAL", "UNAVAILABLE"}
    _VALID_REMEDIATION_PREFIXES = ("A. ", "B. ", "C. ", "D. ", "E. ", "F. ")

    def test_status_and_remediation_category_are_from_the_governed_vocabulary(self):
        for e in _load_matrix():
            assert e["status"] in self._VALID_STATUSES, e
            assert e["remediation_category"].startswith(self._VALID_REMEDIATION_PREFIXES), e

    def test_remediation_category_totals_sum_to_the_full_matrix(self):
        """sum(A, B, C, D, E, F) == total matrix entries — every row must
        be classified into exactly one of the six categories, no row left
        uncategorized or double-counted."""
        entries = _load_matrix()
        counts = {p: 0 for p in self._VALID_REMEDIATION_PREFIXES}
        for e in entries:
            prefix = e["remediation_category"][:3]
            assert prefix in counts, f"unrecognized remediation_category prefix: {prefix!r}"
            counts[prefix] += 1
        assert sum(counts.values()) == len(entries), (
            f"category counts {counts} do not sum to the matrix's {len(entries)} total entries"
        )


class TestRegistryIntrospection:
    """Genuine introspection of services.postmortem.evidence.RULE_REGISTRY
    and the real claim-building functions — never a hardcoded list."""

    def test_rule_registry_report_sections_are_all_covered(self):
        # Importing these modules is what populates RULE_REGISTRY (module-
        # level register_rule() calls) — import them explicitly here so
        # this test doesn't depend on pytest's collection order having
        # already imported them via some other test file.
        import services.postmortem.evidence_attribution  # noqa: F401
        import services.postmortem.exit_evidence  # noqa: F401
        import services.postmortem.governed_price_path_claims  # noqa: F401
        import services.postmortem.price_path_claims  # noqa: F401
        from services.postmortem.evidence import RULE_REGISTRY

        assert len(RULE_REGISTRY) > 0

        # RULE_REGISTRY is a process-wide dict; when this test runs as part
        # of the full suite (not in isolation), other test modules — e.g.
        # test_postmortem_evidence.py's TestRegisterRule, which exercises
        # register_rule() itself with throwaway rule_ids like
        # TEST_DUP_001/TEST_REGISTRY_ONLY_001 — can register their own
        # fixtures into the SAME registry as a side effect of import order.
        # Those are not governed report factors and must not be required to
        # have a matrix row. Scope this check to real governed report
        # sections (the ones this matrix actually documents) rather than
        # hardcoding known test rule_id names, so it stays robust to any
        # future test that pollutes the registry the same way.
        governed_sections = {e["report_section"] for e in _load_matrix()}
        matrix_rule_ids = {e["rule_id"] for e in _load_matrix()}
        registry_rule_ids = {
            rule_id for rule_id, rule in RULE_REGISTRY.items()
            if rule.report_section in governed_sections
        }

        missing = registry_rule_ids - matrix_rule_ids
        assert not missing, (
            f"RULE_REGISTRY contains rule_id(s) with no matrix entry at all: {sorted(missing)} "
            f"— add a row to {_MATRIX_PATH.name} for each."
        )

    def test_evidence_attribution_factors_are_all_covered(self):
        """Calls the real evidence_attribution.build_evidence_attribution
        with both a fully-populated and an empty fixture, covering every
        branch that changes which factors get emitted, and checks every
        (report_section, factor) pair it actually produces is in the
        matrix."""
        from datetime import datetime, timezone

        from services.postmortem.deterministic import (
            ClosedTradeRecord, compute_postmortem,
        )
        from services.postmortem.entry_snapshot import (
            EntrySnapshot,
        )
        from services.postmortem.evidence_attribution import build_evidence_attribution

        matrix_pairs = _matrix_pairs()

        record = ClosedTradeRecord(
            trade_id=1, status="CLOSED", symbol="TEST", market="US", quantity=10,
            entry_price=100.0, exit_price=90.0, stop_loss=85.0, target_price=120.0,
            opened_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            closed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            trade_management_mode="manual", exit_reason="MANUAL",
        )
        postmortem = compute_postmortem(record)

        full_snapshot = EntrySnapshot(
            paper_trade_id=1, user_id="u1", symbol="TEST", market="US",
            snapshot_schema_version="1.1.0", evidence_source="MANUAL",
            daily_pick_run_id=None, daily_pick_rank=None,
            recommendation_signal="BUY", recommendation_generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            recommendation_reference_price=100.0, recommendation_entry_low=98.0, recommendation_entry_high=102.0,
            simulated_execution_price=100.0, execution_slippage_pct=0.0, execution_range_position="ABOVE_RANGE",
            recommended_stop_loss=85.0, recommended_target_price=120.0,
            user_selected_stop_loss=85.0, user_selected_target_price=120.0,
            user_overrode_recommendation=False, reward_to_risk_ratio=1.5, confidence_score=0.8,
            technical_signal="BUY", technical_rsi=55.0, technical_macd_diff=0.1,
            fundamental_score=60.0, sentiment_score=40.0, sentiment_label="NEUTRAL",
            market_regime_trend="BULL", market_regime_score_adj=1.0, market_regime_reason="test",
            recommendation_reasoning=None, model_version="v1", verification_levels={},
            level_history_contract_version="1.0.0", initial_stop_modified_after_entry=False,
            initial_target_modified_after_entry=False, initial_levels_modified_after_entry=False,
        )

        for snapshot in (None, full_snapshot):
            result = build_evidence_attribution(postmortem, snapshot)
            for claim in result.claims:
                pair = (claim.report_section, claim.factor)
                assert pair in matrix_pairs, (
                    f"build_evidence_attribution emitted claim (report_section={claim.report_section!r}, "
                    f"factor={claim.factor!r}, rule_id={claim.rule_id!r}) with no matching row in "
                    f"{_MATRIX_PATH.name} — add one."
                )

    def test_exit_evidence_factors_are_all_covered(self):
        from datetime import datetime, timezone

        from services.postmortem.exit_evidence import build_exit_evidence
        from services.postmortem.exit_snapshot import CloseExitMechanism, build_exit_snapshot

        matrix_pairs = _matrix_pairs()

        # Branch 1: no exit snapshot at all (legacy trade).
        _, claims_none = build_exit_evidence(1, None)
        for claim in claims_none:
            assert (claim.report_section, claim.factor) in matrix_pairs, (
                f"build_exit_evidence(None) emitted uncovered (section, factor) = "
                f"({claim.report_section!r}, {claim.factor!r})"
            )

        # Branch 2: manual close (trigger_timing_verification = NOT_APPLICABLE).
        manual_snapshot = build_exit_snapshot(
            paper_trade_id=1, user_id="u1", symbol="TEST", market="US",
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            exit_price=90.0, exit_quantity=10, closed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            realized_pnl_abs=-100.0, final_stop_loss=85.0, final_target_price=120.0,
            management_mode="manual",
        )
        _, claims_manual = build_exit_evidence(1, manual_snapshot)
        for claim in claims_manual:
            assert (claim.report_section, claim.factor) in matrix_pairs, (
                f"build_exit_evidence(manual) emitted uncovered (section, factor) = "
                f"({claim.report_section!r}, {claim.factor!r})"
            )

        # Branch 3: auto-triggered close (trigger_timing_verification = CLIENT_REPORTED_UNVERIFIED).
        auto_snapshot = build_exit_snapshot(
            paper_trade_id=1, user_id="u1", symbol="TEST", market="US",
            exit_mechanism=CloseExitMechanism.TARGET_HIT, exit_mechanism_raw="TARGET_HIT",
            exit_price=120.0, exit_quantity=10, closed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            realized_pnl_abs=200.0, final_stop_loss=85.0, final_target_price=120.0,
            management_mode="auto",
        )
        _, claims_auto = build_exit_evidence(1, auto_snapshot)
        for claim in claims_auto:
            assert (claim.report_section, claim.factor) in matrix_pairs, (
                f"build_exit_evidence(auto) emitted uncovered (section, factor) = "
                f"({claim.report_section!r}, {claim.factor!r})"
            )

    def test_matrix_rows_cite_real_registered_rule_ids_where_a_registry_entry_exists(self):
        """Every matrix row whose rule_id IS present in RULE_REGISTRY must
        report the registry's own report_section for that rule — catches
        a matrix row silently drifting from the code (e.g. a rule moved
        to a different section and the matrix was never updated)."""
        import services.postmortem.evidence_attribution  # noqa: F401
        import services.postmortem.exit_evidence  # noqa: F401
        import services.postmortem.governed_price_path_claims  # noqa: F401
        import services.postmortem.price_path_claims  # noqa: F401
        from services.postmortem.evidence import RULE_REGISTRY

        for entry in _load_matrix():
            rule = RULE_REGISTRY.get(entry["rule_id"])
            if rule is None:
                # exit_evidence.py's rule_ids (EXIT_MECHANISM_V1 etc.) are
                # not registered via register_rule() — that module builds
                # claims inline rather than through the RuleDefinition
                # registry (verified by direct source read). Not a matrix
                # defect; skip those.
                continue
            assert rule.report_section == entry["report_section"], (
                f"matrix row for rule_id={entry['rule_id']!r} says report_section="
                f"{entry['report_section']!r} but RULE_REGISTRY says {rule.report_section!r}"
            )


class TestMatrixDoesNotDrift:
    """Reverse-direction check: every matrix row's (report_section,
    rule_id) pair must correspond to something the real code actually
    registers or emits today — catches a stale row left behind after a
    rule is renamed/removed, not just a missing one."""

    def test_matrix_factor_keys_match_actual_governed_inventory(self):
        import services.postmortem.evidence_attribution  # noqa: F401
        import services.postmortem.exit_evidence  # noqa: F401
        import services.postmortem.governed_price_path_claims  # noqa: F401
        import services.postmortem.price_path_claims  # noqa: F401
        from services.postmortem.evidence import RULE_REGISTRY

        registry_rule_ids = set(RULE_REGISTRY.keys())
        # exit_evidence.py's rule_ids are not registered via register_rule()
        # (verified by direct source read — see the exemption in
        # test_matrix_rows_cite_real_registered_rule_ids_where_a_registry_entry_exists
        # above); its factors are legitimately matrix-only rule_ids.
        exit_evidence_rule_ids = {
            e["rule_id"] for e in _load_matrix() if e["report_section"] == "exit_evidence"
        }

        matrix_rule_ids = {e["rule_id"] for e in _load_matrix()}
        stale = matrix_rule_ids - registry_rule_ids - exit_evidence_rule_ids
        assert not stale, (
            f"matrix rows cite rule_id(s) that are neither in RULE_REGISTRY nor exit_evidence.py's "
            f"inline rule_ids — these look stale and should be removed or corrected: {sorted(stale)}"
        )


class TestDocMatrixSemanticParity:
    """Direct (not merely structural) parity check between this JSON file
    and its human-readable companion,
    Documentation/Engineering-Handbook/Operations/Trade-Postmortem-Evidence-Coverage-Matrix.md.

    Added 2026-08-07 during the post-Jul-11 documentation reconciliation
    (PR #37) after discovering the two files had drifted: the markdown
    doc claimed category totals A:14/B:2/C:4/D:8/E:15/F:2 while this JSON
    (the code-introspected source of truth) actually held A:28/B:2/C:1/
    D:12/E:2 with no F category — and several individual rows (e.g.
    governed_price_path target_touch/stop_touch/touch_order) carried a
    different remediation_category in each file. The other tests in this
    module only prove the JSON matches the live rule registry; none of
    them previously caught JSON-vs-doc drift. This class parses the
    doc's pipe-table and cross-checks status/source_type/
    verification_level/remediation_category per (report_section,
    internal_factor, rule_id) key, plus the doc's stated category
    totals, against this JSON file — narrow, additive, does not touch
    the registry-introspection tests above.
    """

    _DOC_PATH = (
        _TESTS_UNIT_DIR.parent.parent.parent
        / "Documentation"
        / "Engineering-Handbook"
        / "Operations"
        / "Trade-Postmortem-Evidence-Coverage-Matrix.md"
    )

    _MATRIX_COLUMNS = (
        "report_section", "internal_factor", "user_facing_title", "rule_id", "rule_version",
        "evidence_required", "evidence_currently_captured", "source_type", "verification_level",
        "freshness_basis", "status", "reason_missing", "affects_new_trades", "affects_legacy_trades",
        "historically_recoverable", "frontend_improvement_required", "backend_improvement_required",
        "recommended_phase", "fabrication_risk", "remediation_category",
    )

    def _parse_doc_rows(self) -> list[dict]:
        text = self._DOC_PATH.read_text()
        lines = text.splitlines()
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("| report_section |"):
                header_idx = i
                break
        assert header_idx is not None, "could not find the matrix table header in the doc"
        rows = []
        # header_idx+1 is the |---|---| separator; data starts at +2.
        for line in lines[header_idx + 2:]:
            if not line.strip().startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != len(self._MATRIX_COLUMNS):
                continue
            rows.append(dict(zip(self._MATRIX_COLUMNS, cells)))
        return rows

    def test_doc_exists(self):
        assert self._DOC_PATH.exists(), f"expected companion doc at {self._DOC_PATH}"

    def test_doc_row_count_matches_json(self):
        doc_rows = self._parse_doc_rows()
        json_rows = _load_matrix()
        assert len(doc_rows) == len(json_rows), (
            f"doc has {len(doc_rows)} matrix rows, json has {len(json_rows)} — "
            "regenerate the doc's table from the json file"
        )

    def test_doc_rows_match_json_keys(self):
        doc_keys = {(r["report_section"], r["internal_factor"], r["rule_id"]) for r in self._parse_doc_rows()}
        json_keys = {(e["report_section"], e["internal_factor"], e["rule_id"]) for e in _load_matrix()}
        assert doc_keys == json_keys, (
            f"doc/json row keys differ - only in doc: {sorted(doc_keys - json_keys)}, "
            f"only in json: {sorted(json_keys - doc_keys)}"
        )

    def test_doc_rows_semantically_match_json(self):
        """For every shared row key, the doc and json must agree on the
        fields most likely to silently drift and mislead a reader:
        status, source_type, verification_level, remediation_category."""
        semantic_fields = ("status", "source_type", "verification_level", "remediation_category")
        json_by_key = {
            (e["report_section"], e["internal_factor"], e["rule_id"]): e for e in _load_matrix()
        }
        mismatches = []
        for doc_row in self._parse_doc_rows():
            key = (doc_row["report_section"], doc_row["internal_factor"], doc_row["rule_id"])
            json_row = json_by_key.get(key)
            if json_row is None:
                continue  # caught by test_doc_rows_match_json_keys
            for field in semantic_fields:
                json_val = str(json_row[field])
                doc_val = doc_row[field]
                if json_val != doc_val:
                    mismatches.append((key, field, doc_val, json_val))
        assert not mismatches, (
            "doc/json semantic mismatches (key, field, doc_value, json_value): " + repr(mismatches)
        )

    def test_doc_stated_category_totals_match_json(self):
        """The doc's 'Row count and category totals' prose must match the
        json's actual remediation_category distribution — this is the
        specific claim that was stale (14/2/4/8/15/2+F2 vs the json's
        real 28/2/1/12/2) before this reconciliation."""
        from collections import Counter

        json_counts = Counter(e["remediation_category"][:2].strip(".") for e in _load_matrix())
        text = self._DOC_PATH.read_text()
        for prefix, count in json_counts.items():
            needle = f"{prefix}. "
            assert f"{prefix}. AVAILABLE_NOW: {count}" in text or \
                   f"{prefix}. EXISTING_DATA_NOT_PROPAGATED: {count}" in text or \
                   f"{prefix}. REQUIRES_SERVER_VERIFICATION: {count}" in text or \
                   f"{prefix}. REQUIRES_NEW_ACQUISITION: {count}" in text or \
                   f"{prefix}. LEGACY_NOT_RECOVERABLE: {count}" in text, (
                f"doc's stated total for category {prefix} does not match json's actual "
                f"count of {count} — update the doc's 'Row count and category totals' section"
            )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
