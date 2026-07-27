"""
Phase C0-A — parsing / identity / classification tests for the offline
instrument-master foundation. All inputs are local fixture files under
tests/fixtures/instrument_master/ — no network, no database.
"""
import os

import pytest

from services.instrument_master.enums import (
    ClassificationStatus,
    EligibilityStatus,
    ExchangeSeries,
    InstrumentCategory,
)
from services.instrument_master.parser import (
    SourceValidationError,
    build_records,
    load_secondary_classifications,
    parse_source_csv,
)

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "instrument_master"
)


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
class TestParsingHeaders:
    def test_correct_headers_parse_cleanly(self):
        result = parse_source_csv(_read("fixture_extra_columns.csv"), source_identifier="t")
        assert result.total_rows == 1
        assert len(result.rejected) == 0

    def test_reordered_headers_parse_cleanly(self):
        result = parse_source_csv(_read("fixture_reordered_headers.csv"), source_identifier="t")
        assert result.total_rows == 1
        assert len(result.rejected) == 0
        candidate = result.accepted_candidates[0]
        assert candidate.symbol == "SYNRE01"
        assert candidate.isin == "INZZZ0200001"
        assert candidate.series == ExchangeSeries.EQ

    def test_missing_required_header_raises_source_validation_error(self):
        with pytest.raises(SourceValidationError) as exc_info:
            parse_source_csv(_read("fixture_missing_isin_column.csv"), source_identifier="t")
        assert "ISIN NUMBER" in str(exc_info.value)

    def test_empty_file_raises_source_validation_error(self):
        with pytest.raises(SourceValidationError):
            parse_source_csv(_read("fixture_empty.csv"), source_identifier="t")

    def test_extra_columns_are_tolerated_and_ignored(self):
        result = parse_source_csv(_read("fixture_extra_columns.csv"), source_identifier="t")
        candidate = result.accepted_candidates[0]
        assert candidate.symbol == "SYNEX01"


class TestParsingRows:
    def test_malformed_row_is_rejected_not_raised(self):
        result = parse_source_csv(_read("fixture_malformed_row.csv"), source_identifier="t")
        assert result.total_rows == 2
        assert len(result.accepted_candidates) == 1
        assert len(result.rejected) == 1
        assert result.rejected[0].reason_code == "malformed_row"

    def test_unicode_company_name_parses_correctly(self):
        result = parse_source_csv(_read("fixture_unicode.csv"), source_identifier="t")
        assert len(result.rejected) == 0
        candidate = result.accepted_candidates[0]
        assert "日本語テスト" in candidate.display_name
        assert "Ünïcödé" in candidate.display_name

    def test_whitespace_is_normalized(self):
        result = parse_source_csv(_read("fixture_25.csv"), source_identifier="t")
        ws_candidate = next(c for c in result.accepted_candidates if c.symbol == "SYNWS01")
        assert ws_candidate.symbol == "SYNWS01"  # no leading/trailing spaces
        assert ws_candidate.display_name == "Synthetic Whitespace Limited"
        assert ws_candidate.series == ExchangeSeries.EQ


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
class TestIdentity:
    def _fixture_25_records(self, secondary=None, prior_map=None, prior_generated_at=None):
        parsed = parse_source_csv(_read("fixture_25.csv"), source_identifier="fixture_25")
        return build_records(
            parsed,
            schema_version=1,
            source_identifier="fixture_25",
            source_publication_date="2026-01-01",
            secondary_classifications=secondary,
            prior_candidates_by_isin=prior_map,
            prior_generated_at=prior_generated_at,
            current_generated_at="2026-07-19T00:00:00Z",
        )

    def test_isin_is_stable_identity_across_symbol_case(self):
        records, _ = self._fixture_25_records()
        mixed_case = next(r for r in records if r.symbol == "SYNMC01")
        assert mixed_case.isin == "INZZZ0100012"
        assert mixed_case.series == ExchangeSeries.EQ  # normalized from lowercase "eq"

    def test_symbol_is_never_used_as_permanent_identity(self):
        records, _ = self._fixture_25_records()
        # Two records legitimately share the same symbol string in this
        # fixture (duplicate-symbol scenario) with two different ISINs —
        # if symbol were identity this would be a contradiction; it isn't,
        # because symbol is never identity.
        same_symbol = [r for r in records if r.symbol == "SYNDUPSYM"]
        assert len(same_symbol) == 2
        assert {r.isin for r in same_symbol} == {"INZZZ0100005", "INZZZ0100006"}

    def test_duplicate_isin_flagged_ambiguous_not_dropped(self):
        records, stats = self._fixture_25_records()
        dup_isin_records = [r for r in records if r.isin == "INZZZ0100004"]
        assert len(dup_isin_records) == 2  # retained, never silently dropped
        for r in dup_isin_records:
            assert r.eligibility_status == EligibilityStatus.AMBIGUOUS_CLASSIFICATION
            assert "duplicate_isin" in r.eligibility_reason_codes
        assert stats["duplicate_isin_detections"] == 2

    def test_duplicate_symbol_flagged_ambiguous_not_dropped(self):
        records, stats = self._fixture_25_records()
        dup_symbol_records = [r for r in records if r.symbol == "SYNDUPSYM"]
        for r in dup_symbol_records:
            assert "duplicate_symbol" in r.eligibility_reason_codes
        assert stats["duplicate_symbol_detections"] == 2

    def test_missing_isin_retained_but_flagged_unsupported(self):
        records, _ = self._fixture_25_records()
        no_isin = next(r for r in records if r.symbol == "SYNNOISIN")
        assert no_isin.isin is None
        assert no_isin.eligibility_status == EligibilityStatus.UNSUPPORTED_INSTRUMENT
        assert no_isin.eligibility_reason_codes == ("missing_isin",)

    def test_symbol_rename_detected_via_prior_snapshot(self):
        prior_parsed = parse_source_csv(_read("fixture_25_prior.csv"), source_identifier="prior")
        prior_map = {c.isin: c for c in prior_parsed.accepted_candidates if c.isin}
        records, _ = self._fixture_25_records(
            prior_map=prior_map, prior_generated_at="2026-01-01T00:00:00Z"
        )
        renamed = next(r for r in records if r.isin == "INZZZ0100001")
        assert renamed.symbol == "SYNCEQ01"
        assert renamed.previous_symbol == "SYNCEQ01OLD"
        assert len(renamed.symbol_history) == 1
        assert renamed.symbol_history[0].symbol == "SYNCEQ01OLD"
        assert renamed.symbol_history[0].effective_from == "2026-01-01T00:00:00Z"
        assert renamed.symbol_history[0].effective_to == "2026-07-19T00:00:00Z"

    def test_series_movement_detected_via_prior_snapshot(self):
        prior_parsed = parse_source_csv(_read("fixture_25_prior.csv"), source_identifier="prior")
        prior_map = {c.isin: c for c in prior_parsed.accepted_candidates if c.isin}
        records, _ = self._fixture_25_records(
            prior_map=prior_map, prior_generated_at="2026-01-01T00:00:00Z"
        )
        moved = next(r for r in records if r.isin == "INZZZ0100009")
        assert moved.series == ExchangeSeries.EQ
        assert len(moved.series_history) == 1
        assert moved.series_history[0].series == "BE"

    def test_no_history_without_prior_snapshot(self):
        records, _ = self._fixture_25_records()
        clean = next(r for r in records if r.isin == "INZZZ0100001")
        assert clean.symbol_history == ()
        assert clean.previous_symbol is None


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
class TestClassification:
    def _fixture_25_records_with_secondary(self):
        parsed = parse_source_csv(_read("fixture_25.csv"), source_identifier="fixture_25")
        secondary = load_secondary_classifications(_read("fixture_secondary_classifications.csv"))
        return build_records(
            parsed,
            schema_version=1,
            source_identifier="fixture_25",
            source_publication_date="2026-01-01",
            secondary_classifications=secondary,
        )

    def test_eq_series_alone_never_implies_ordinary_common_equity(self):
        records, _ = self._fixture_25_records_with_secondary()
        # SYNCEQ01 is a plain EQ row with no secondary-source entry.
        plain_eq = next(r for r in records if r.symbol == "SYNCEQ01")
        assert plain_eq.series == ExchangeSeries.EQ
        assert plain_eq.instrument_category == InstrumentCategory.UNKNOWN_UNCLASSIFIED
        assert plain_eq.classification_status == ClassificationStatus.UNAVAILABLE

    def test_be_and_bz_reason_coded_as_surveillance_not_instrument_type(self):
        records, _ = self._fixture_25_records_with_secondary()
        be_record = next(r for r in records if r.series == ExchangeSeries.BE)
        bz_record = next(r for r in records if r.series == ExchangeSeries.BZ)
        assert be_record.eligibility_status == EligibilityStatus.SURVEILLANCE_SERIES
        assert be_record.eligibility_reason_codes == ("t2t_series_be",)
        assert bz_record.eligibility_status == EligibilityStatus.SURVEILLANCE_SERIES
        assert bz_record.eligibility_reason_codes == ("t2t_series_bz",)
        # Series, not instrument taxonomy: category remains unclassified.
        assert be_record.instrument_category == InstrumentCategory.UNKNOWN_UNCLASSIFIED
        assert bz_record.instrument_category == InstrumentCategory.UNKNOWN_UNCLASSIFIED

    def test_unknown_evidence_remains_unknown_not_defaulted(self):
        records, _ = self._fixture_25_records_with_secondary()
        gs_record = next(r for r in records if r.series == ExchangeSeries.OTHER)
        assert gs_record.instrument_category == InstrumentCategory.UNKNOWN_UNCLASSIFIED
        assert gs_record.classification_status == ClassificationStatus.UNAVAILABLE
        assert gs_record.eligibility_status == EligibilityStatus.AMBIGUOUS_CLASSIFICATION

    def test_no_substring_or_name_based_primary_classification(self):
        records, _ = self._fixture_25_records_with_secondary()
        false_positive_symbols = ["SYNETFX", "SYNTRX", "SYNPFX", "SYNRTX", "SYNWRX"]
        for symbol in false_positive_symbols:
            record = next(r for r in records if r.symbol == symbol)
            # Company names deliberately contain ETF/TRUST/PREF/RIGHT/
            # WARRANT substrings — none may drive instrument_category.
            assert record.instrument_category == InstrumentCategory.UNKNOWN_UNCLASSIFIED, symbol
            assert record.classification_status == ClassificationStatus.UNAVAILABLE, symbol

    def test_auxiliary_instruments_isolated_by_exact_symbol_not_name(self):
        records, _ = self._fixture_25_records_with_secondary()
        goldbees = next(r for r in records if r.symbol == "GOLDBEES")
        assert goldbees.instrument_category == InstrumentCategory.AUXILIARY_TRACKING_INSTRUMENT
        assert goldbees.classification_status == ClassificationStatus.VERIFIED
        assert goldbees.eligibility_status == EligibilityStatus.SHADOW_ONLY
        assert goldbees.eligibility_reason_codes == ("auxiliary_tracking_instrument_excluded",)

    def test_verified_ordinary_equity_from_secondary_source_is_eligible(self):
        records, _ = self._fixture_25_records_with_secondary()
        verified = next(r for r in records if r.isin == "INZZZ0100020")
        assert verified.instrument_category == InstrumentCategory.ORDINARY_COMMON_EQUITY
        assert verified.classification_status == ClassificationStatus.VERIFIED
        assert verified.eligibility_status == EligibilityStatus.ELIGIBLE
        assert verified.eligibility_reason_codes == ()

    def test_provisional_classification_remains_visibly_provisional(self):
        records, _ = self._fixture_25_records_with_secondary()
        provisional = next(r for r in records if r.isin == "INZZZ0100021")
        assert provisional.classification_status == ClassificationStatus.PROVISIONAL
        # Provisional is never silently upgraded to eligible.
        assert provisional.eligibility_status == EligibilityStatus.AMBIGUOUS_CLASSIFICATION
        assert provisional.eligibility_reason_codes == ("unclassified_instrument_category",)

    def test_verified_non_equity_from_secondary_source_is_unsupported(self):
        records, _ = self._fixture_25_records_with_secondary()
        verified_etf = next(r for r in records if r.isin == "INZZZ0100022")
        assert verified_etf.instrument_category == InstrumentCategory.ETF
        assert verified_etf.classification_status == ClassificationStatus.VERIFIED
        assert verified_etf.eligibility_status == EligibilityStatus.UNSUPPORTED_INSTRUMENT
        assert verified_etf.eligibility_reason_codes == ("confirmed_non_equity_instrument",)
