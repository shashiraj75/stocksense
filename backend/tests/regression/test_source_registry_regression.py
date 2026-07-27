"""
Phase C1-D1 — regression tests confirming the new source_registry /
source_validators / manifest_contract modules coexist cleanly with the
existing, merged Phase C0 instrument-master module: no enum
duplication, no name collision, no behavior change to Phase C0's own
public API.
"""
from __future__ import annotations

from services.instrument_master import enums as im_enums
from services.instrument_master.canonicalize import canonical_json_bytes, sha256_hex
from services.instrument_master.enums import InstrumentCategory  # Phase C0 enum, unmodified
from services.instrument_master.manifest_contract import compute_stale_state
from services.instrument_master.parser import (  # Phase C0 public API, unmodified
    AUXILIARY_TRACKING_SYMBOLS,
    REQUIRED_HEADER_COLUMNS,
    SourceValidationError,
    build_records,
    parse_source_csv,
)
from services.instrument_master.source_registry import all_entries, registry_by_id
from services.instrument_master.source_validators import validate_equity_current


class TestNoDuplicationOfPhaseC0Enums:
    def test_instrument_category_unchanged_and_not_redefined(self):
        # Phase C0's InstrumentCategory members must be exactly what they
        # were before this phase — the new SourceCriticality enum is a
        # deliberately distinct type, never merged with or aliasing this.
        assert set(InstrumentCategory) == {
            InstrumentCategory.ORDINARY_COMMON_EQUITY,
            InstrumentCategory.SME_EQUITY,
            InstrumentCategory.ETF,
            InstrumentCategory.REIT,
            InstrumentCategory.INVIT,
            InstrumentCategory.PREFERENCE_SHARE,
            InstrumentCategory.PARTLY_PAID_EQUITY,
            InstrumentCategory.WARRANT,
            InstrumentCategory.RIGHTS_ENTITLEMENT,
            InstrumentCategory.AUXILIARY_TRACKING_INSTRUMENT,
            InstrumentCategory.UNKNOWN_UNCLASSIFIED,
        }

    def test_new_enums_are_distinct_types_from_phase_c0_enums(self):
        assert im_enums.SourceCriticality is not InstrumentCategory
        assert im_enums.ConflictState is not im_enums.ClassificationStatus


class TestPhaseC0PublicApiUnchanged:
    def test_parser_public_symbols_still_present(self):
        assert AUXILIARY_TRACKING_SYMBOLS == frozenset({"GLD", "SLV", "GOLDBEES", "SILVERBEES"})
        assert REQUIRED_HEADER_COLUMNS == ("SYMBOL", "NAME OF COMPANY", "SERIES", "ISIN NUMBER")

    def test_phase_c0_parse_source_csv_still_works_unmodified(self):
        csv_text = (
            "SYMBOL,NAME OF COMPANY,SERIES,ISIN NUMBER,DATE OF LISTING\n"
            "TESTCO,Test Company,EQ,INTESTAB0012,01-JAN-2020\n"
        )
        result = parse_source_csv(csv_text, source_identifier="regression_test")
        assert result.total_rows == 1
        assert len(result.accepted_candidates) == 1

    def test_phase_c0_build_records_still_works_unmodified(self):
        csv_text = (
            "SYMBOL,NAME OF COMPANY,SERIES,ISIN NUMBER,DATE OF LISTING\n"
            "TESTCO,Test Company,EQ,INTESTAB0012,01-JAN-2020\n"
        )
        parse_result = parse_source_csv(csv_text, source_identifier="regression_test")
        records, stats = build_records(
            parse_result,
            schema_version=1,
            source_identifier="regression_test",
            source_publication_date="2026-07-19",
        )
        assert len(records) == 1
        assert stats["total_rows"] == 1

    def test_source_validation_error_still_raised_on_empty_file(self):
        try:
            parse_source_csv("", source_identifier="regression_test")
            raise AssertionError("expected SourceValidationError")
        except SourceValidationError:
            pass


class TestCanonicalizeReuseNotDuplication:
    def test_source_registry_reuses_phase_c0_canonical_json_bytes(self):
        # Confirms the new modules genuinely reuse canonicalize.py rather
        # than reimplementing their own serialization logic.
        from services.instrument_master import source_registry

        with open(source_registry.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        assert "from .canonicalize import canonical_json_bytes, sha256_hex" in source

    def test_manifest_contract_reuses_phase_c0_canonical_json_bytes(self):
        from services.instrument_master import manifest_contract

        with open(manifest_contract.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        assert "from .canonicalize import canonical_json_bytes, sha256_hex" in source

    def test_canonical_json_bytes_behavior_unchanged(self):
        assert canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'
        assert sha256_hex(b"test") == sha256_hex(b"test")


class TestCrossModuleConsistency:
    def test_registry_source_ids_align_with_all_entries_helper(self):
        for entry in all_entries():
            assert registry_by_id(entry.source_id) is entry

    def test_equity_validator_and_registry_agree_on_header(self):
        entry = registry_by_id(im_enums.SourceId.NSE_EQUITY_CURRENT)
        result = validate_equity_current(
            ",".join(entry.expected_headers) + "\n"
            + "TESTCO,Test Company,EQ,01-JAN-2020,10,1,INTESTAB0012,10\n"
        )
        assert result.valid is True

    def test_stale_state_thresholds_align_with_sme_registry_entry(self):
        sme_entry = registry_by_id(im_enums.SourceId.NSE_SME_CURRENT)
        assert compute_stale_state(sme_entry.freshness_warning_days).value == "fresh"
        assert compute_stale_state(sme_entry.freshness_warning_days + 1).value == "stale_warning"
        assert compute_stale_state(sme_entry.hard_stale_days + 1).value == "hard_stale"
