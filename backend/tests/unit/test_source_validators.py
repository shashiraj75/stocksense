"""
Phase C1-D1 — source-specific offline validator tests.

All fixture files live under
backend/tests/fixtures/instrument_master/source_registry/ and are
small, hand-authored synthetic CSVs — never a complete downloaded NSE
dataset.
"""
from __future__ import annotations

import os

import pytest

from services.instrument_master.enums import SourceId, ValidationResultStatus
from services.instrument_master.source_validators import (
    SymbolChangeRecord,
    ValidationResult,
    dedupe_name_history_rows,
    is_strict_valid_isin,
    parse_symbol_change_row,
    validate_equity_current,
    validate_etf,
    validate_idr,
    validate_il_series,
    validate_invit,
    validate_name_history,
    validate_preference,
    validate_reit,
    validate_sme,
    validate_symbol_history,
    validate_warrant,
)

FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "instrument_master", "source_registry"
)


def _read(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _read_bytes(name: str) -> bytes:
    # Text-mode reading applies universal-newline translation (CRLF -> LF)
    # by default, which would corrupt an exact-byte-baseline fixture such
    # as il_series_valid_empty.csv. Read raw bytes for any fixture whose
    # exact byte representation (not just its text content) matters.
    with open(os.path.join(FIXTURE_DIR, name), "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Strict ISIN validation
# ---------------------------------------------------------------------------


class TestStrictIsinValidation:
    @pytest.mark.parametrize(
        "isin",
        ["INTESTAB0012", "INTESTCD0026", "INTESTEF0030", "INTESTGH0044", "INE019A04016", "INE086A13016", "INE028L21018"],
    )
    def test_valid_synthetic_and_real_evidenced_isins_accepted(self, isin):
        assert is_strict_valid_isin(isin) is True

    @pytest.mark.parametrize(
        "isin",
        [
            None,
            "",
            "   ",
            "INTESTAB001",  # too short
            "INTESTAB00123",  # too long
            "IN TESTAB0012",  # embedded space
            "ine019a04016",  # lowercase
            "XX0000000000",  # placeholder, wrong check digit
            "INTESTAB0013",  # wrong check digit (should be 2)
            " INTESTAB0012",  # leading whitespace not stripped by caller
            "INTESTAB0012 ",  # trailing whitespace not stripped by caller
        ],
    )
    def test_invalid_isins_rejected(self, isin):
        assert is_strict_valid_isin(isin) is False

    def test_no_silent_truncation(self):
        # A 13-character string must not be silently truncated to 12 and
        # accepted — it must be rejected outright.
        assert is_strict_valid_isin("INTESTAB0012X") is False

    def test_valid_non_in_iso6166_isin_intentionally_accepted(self):
        # Documented policy (Phase C1-D3 amendment): this validator
        # checks general ISO 6166 structural/check-digit validity only,
        # not an IN-only country restriction. A structurally valid,
        # independently-documented, publicly-known non-IN ISIN (Apple
        # Inc., US-issued) is intentionally accepted here — this is not
        # a bug, it is the documented scope of this general-purpose
        # validator, distinct from any future NSE-specific policy layer.
        assert is_strict_valid_isin("US0378331005") is True


# ---------------------------------------------------------------------------
# EQUITY_L.csv
# ---------------------------------------------------------------------------


class TestEquityCurrentValidator:
    def test_valid_fixture_accepted(self):
        r = validate_equity_current(_read("valid_equity.csv"))
        assert r.valid is True
        assert r.status == ValidationResultStatus.VALID
        assert r.record_count == 1
        assert r.source_id == SourceId.NSE_EQUITY_CURRENT

    def test_malformed_header_rejected(self):
        r = validate_equity_current(_read("malformed_header.csv"))
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_HEADER

    def test_missing_field_rejected(self):
        r = validate_equity_current(_read("extra_missing_fields.csv"))
        assert r.valid is False
        assert r.error_count >= 1

    def test_invalid_isin_rejected(self):
        r = validate_equity_current(_read("invalid_isins.csv"))
        assert r.valid is False
        assert any(e.code == "invalid_isin" for e in r.errors)

    def test_duplicate_isin_rejected(self):
        r = validate_equity_current(_read("duplicate_isins.csv"))
        assert r.valid is False
        assert r.isin_duplicate_count == 2
        assert r.record_count == 0

    def test_non_csv_html_payload_rejected(self):
        r = validate_equity_current(_read("non_csv_html_payload.html"))
        assert r.valid is False
        assert r.status == ValidationResultStatus.UNEXPECTED_NON_CSV_CONTENT

    def test_unicode_normalization_accepted(self):
        r = validate_equity_current(_read("unicode_and_whitespace.csv"))
        assert r.valid is True

    def test_series_membership_never_asserted_as_taxonomy_proof(self):
        # Structural check only: the validator result carries no
        # instrument_category/classification field at all — confirms
        # this module makes no taxonomy claim.
        r = validate_equity_current(_read("valid_equity.csv"))
        assert not hasattr(r, "instrument_category")


# ---------------------------------------------------------------------------
# ETF / SME / REIT / InvIT
# ---------------------------------------------------------------------------


class TestEtfValidator:
    def test_valid_fixture_accepted(self):
        r = validate_etf(_read("valid_etf.csv"))
        assert r.valid is True
        assert r.source_id == SourceId.NSE_ETF_CURRENT


class TestSmeValidator:
    def test_valid_fixture_accepted(self):
        r = validate_sme(_read("valid_sme.csv"))
        assert r.valid is True
        assert r.source_id == SourceId.NSE_SME_CURRENT

    def test_correct_url_accepted(self):
        r = validate_sme(
            _read("valid_sme.csv"),
            source_url="https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
        )
        assert r.valid is True

    def test_incorrect_legacy_url_rejected(self):
        wrong_url = _read("wrong_sme_url_contract.txt").strip()
        r = validate_sme(_read("valid_sme.csv"), source_url=wrong_url)
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_TRANSPORT_METADATA

    def test_evil_host_with_correct_path_substring_rejected(self):
        # Phase C1-D2 finding: the prior substring-containment check let
        # an attacker-controlled host pass merely by containing the
        # correct path fragment. The corrected implementation requires
        # exact host+path equality.
        evil_url = "https://evil.example/emerge/corporates/content/SME_EQUITY_L.csv"
        r = validate_sme(_read("valid_sme.csv"), source_url=evil_url)
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_TRANSPORT_METADATA

    def test_correct_path_inside_query_parameter_rejected(self):
        evil_url = "https://evil.example/x?real=/emerge/corporates/content/SME_EQUITY_L.csv"
        r = validate_sme(_read("valid_sme.csv"), source_url=evil_url)
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_TRANSPORT_METADATA

    def test_correct_url_with_trailing_whitespace_rejected(self):
        correct = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
        r = validate_sme(_read("valid_sme.csv"), source_url=correct + " ")
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_TRANSPORT_METADATA

    def test_correct_url_with_trailing_slash_rejected(self):
        correct = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
        r = validate_sme(_read("valid_sme.csv"), source_url=correct + "/")
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_TRANSPORT_METADATA

    def test_correct_url_with_query_string_rejected(self):
        correct = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
        r = validate_sme(_read("valid_sme.csv"), source_url=correct + "?x=1")
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_TRANSPORT_METADATA

    def test_exact_correct_url_accepted(self):
        correct = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
        r = validate_sme(_read("valid_sme.csv"), source_url=correct)
        assert r.valid is True

    # -- Phase C1-A3: live-format trailing-empty-field correction --------

    def test_trailing_empty_field_accepted_and_dropped(self):
        # Confirmed live shape (C1-A2): every row, including the header,
        # carries a trailing comma producing an 8th, always-empty field.
        r = validate_sme(_read("sme_trailing_empty_field.csv"))
        assert r.valid is True
        assert r.status == ValidationResultStatus.VALID
        assert r.record_count == 1
        assert r.observed_max_field_count == 8  # raw width, before trimming
        assert r.observed_header == ("SYMBOL", "NAME_OF_COMPANY", "SERIES", "DATE_OF_LISTING", "PAID_UP_VALUE", "ISIN_NUMBER", "FACE_VALUE")

    def test_trailing_non_empty_field_rejected(self):
        r = validate_sme(_read("sme_trailing_nonempty_field.csv"))
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_FIELD_COUNT
        assert any(
            e.code == "invalid_field_count" and "unexpected non-empty trailing field" in e.detail
            for e in r.errors
        )
        assert r.record_count == 0

    def test_more_than_eight_raw_fields_rejected(self):
        r = validate_sme(_read("sme_too_many_fields.csv"))
        assert r.valid is False
        assert any(e.code == "invalid_field_count" for e in r.errors)
        assert r.record_count == 0

    def test_seven_field_fixture_without_trailing_comma_still_accepted(self):
        # Backward compatible: a row with exactly the declared 7 fields
        # (no trailing comma at all) must remain valid — the tolerance is
        # additive, not a new requirement.
        r = validate_sme(_read("valid_sme.csv"))
        assert r.valid is True
        assert r.record_count == 1

    def test_trailing_empty_field_tolerance_is_source_id_scoped(self):
        # EQUITY_L's contract must NOT tolerate an SME-shaped trailing
        # empty field — confirms optional_trailing_empty_field is never
        # silently applied outside validate_sme().
        equity_with_trailing_comma = _read("valid_equity.csv").replace("10\n", "10,\n")
        r = validate_equity_current(equity_with_trailing_comma)
        assert r.valid is False


class TestReitInvitValidator:
    def test_reit_footnote_row_filtered_not_treated_as_security(self):
        r = validate_reit(_read("valid_reit.csv"))
        assert r.valid is True
        assert r.record_count == 1  # footnote row must not inflate this

    def test_invit_valid_fixture_accepted(self):
        r = validate_invit(_read("valid_invit.csv"))
        assert r.valid is True
        assert r.record_count == 1

    def test_known_footnote_row_recognized_and_ignored(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "Note: the Market lot is updated periodically and this footnote row has no valid ISIN\n"
        )
        r = validate_reit(text)
        assert r.valid is True
        assert r.record_count == 1
        assert r.error_count == 0

    def test_whitespace_only_blank_line_ignored(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "   \n"
        )
        r = validate_reit(text)
        assert r.valid is True
        assert r.record_count == 1
        assert r.error_count == 0

    def test_truncated_genuine_data_row_produces_an_error_not_a_silent_drop(self):
        # Correction 2 (Phase C1-D2 finding): a truncated real security
        # row -- one real field short of the expected 8, NOT a footnote --
        # must surface as an error, never be silently dropped.
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "BROKENROW,Missing Fields,RR,01-JAN-2020,100,1\n"
        )
        r = validate_reit(text)
        assert r.valid is False
        assert r.error_count == 1
        assert r.warning_count == 0
        assert any(e.code == "invalid_field_count" for e in r.errors)
        assert r.record_count == 1  # the one genuinely valid row is still preserved

    def test_status_reflects_actual_error_code_not_default_invalid_isin(self):
        # Phase C1-D5 cleanup (non-blocking finding from Phase C1-D4):
        # a row rejected for a field-count reason must report
        # status=INVALID_FIELD_COUNT, not the previous default of
        # INVALID_ISIN, which was cosmetically incorrect (the error
        # code itself, "invalid_field_count", was always correct).
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "BROKENROW,Missing Fields,RR,01-JAN-2020,100,1\n"
        )
        r = validate_reit(text)
        assert r.status == ValidationResultStatus.INVALID_FIELD_COUNT
        assert all(e.code == "invalid_field_count" for e in r.errors)

    def test_wrong_width_row_with_isin_like_value_still_produces_an_error(self):
        # An ISIN-shaped value appearing in a malformed row must NOT be
        # treated as evidence of a footnote -- only the exact evidence-
        # backed footnote marker text qualifies.
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "BROKEN,Missing,RR,INTESTIJ0057\n"
        )
        r = validate_reit(text)
        assert r.valid is False
        assert any(e.code == "invalid_field_count" for e in r.errors)

    def test_wrong_width_row_without_isin_and_without_footnote_marker_produces_an_error(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "SomeRandomSingleFieldTextThatIsNotTheKnownFootnote\n"
        )
        r = validate_reit(text)
        assert r.valid is False
        assert any(e.code == "invalid_field_count" for e in r.errors)

    def test_result_never_reports_valid_when_unrecognized_malformed_row_exists(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHREIT,Synthetic REIT Test,RR,01-JAN-2020,100,1,INTESTGH0044,100\n"
            "AlsoNotAFootnote\n"
        )
        r = validate_reit(text)
        assert r.valid is False

    def test_invit_truncated_row_also_produces_an_error(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "SYNTHINVIT,Synthetic InvIT Test,IV,01-JAN-2020,100,1,INTESTIJ0057,100\n"
            "TRUNCATED,Missing,IV,01-JAN-2020,100,1\n"
        )
        r = validate_invit(text)
        assert r.valid is False
        assert any(e.code == "invalid_field_count" for e in r.errors)


# ---------------------------------------------------------------------------
# PREF.csv / WARRANT.csv trailing-ISIN contract
# ---------------------------------------------------------------------------


class TestPrefWarrantTrailingIsinContract:
    def test_pref_trailing_isin_accepted(self):
        r = validate_preference(_read("pref_trailing_isin.csv"))
        assert r.valid is True
        assert r.record_count == 1
        assert r.observed_max_field_count == 15  # 14 declared + 1 trailing ISIN

    def test_pref_wrong_field_count_rejected(self):
        r = validate_preference(_read("pref_wrong_field_count.csv"))
        assert r.valid is False
        assert any(e.code == "invalid_field_count" for e in r.errors)

    def test_warrant_trailing_isin_accepted(self):
        r = validate_warrant(_read("warrant_trailing_isin.csv"))
        assert r.valid is True
        assert r.record_count == 1
        assert r.observed_max_field_count == 6  # 5 declared + 1 trailing ISIN

    def test_pref_and_warrant_contracts_are_independent(self):
        # Applying PREF's validator to WARRANT's fixture must fail on
        # header mismatch — there is no shared "accept extra columns"
        # generic rule that would make this pass by coincidence.
        r = validate_preference(_read("warrant_trailing_isin.csv"))
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_HEADER

    def test_no_other_validator_tolerates_a_trailing_extra_field(self):
        # EQUITY_L's contract must NOT accept a PREF-shaped trailing
        # extra column — confirms the tolerance is source-ID-scoped only.
        equity_with_extra_field = _read("valid_equity.csv").replace(
            "10\n", "10,EXTRA\n"
        )
        r = validate_equity_current(equity_with_extra_field)
        assert r.valid is False

    def test_pref_trailing_field_must_be_isin_shaped_not_arbitrary(self):
        r = validate_preference(_read("pref_invalid_trailing_isin.csv"))
        assert r.valid is False
        assert any(e.code == "invalid_isin" for e in r.errors)

    def test_warrant_trailing_field_must_be_isin_shaped_not_arbitrary(self):
        r = validate_warrant(_read("warrant_invalid_trailing_isin.csv"))
        assert r.valid is False
        assert any(e.code == "invalid_isin" for e in r.errors)


# ---------------------------------------------------------------------------
# PREF.csv per-redemption-tranche grain (Phase C1-A3)
# ---------------------------------------------------------------------------


class TestPreferenceTrancheGrain:
    def test_two_tranches_same_isin_both_retained(self):
        # Live-confirmed shape: one symbol (JSWSTEEL, observed), same
        # trailing ISIN, distinct REDEMPTION DATE per row. Both rows are
        # real, distinct records — never collapsed by ISIN alone.
        r = validate_preference(_read("pref_two_tranches_same_isin.csv"))
        assert r.valid is True
        assert r.record_count == 2
        assert r.error_count == 0

    def test_exact_duplicate_tranche_is_flagged_and_excluded(self):
        # Same ISIN AND same REDEMPTION DATE AND same CONVERSION DATE
        # repeated verbatim — a genuine duplicate, correctly rejected
        # (unlike the two-distinct-tranches case above).
        r = validate_preference(_read("pref_exact_duplicate_tranche.csv"))
        assert r.valid is False
        assert r.record_count == 0
        assert any(e.code == "duplicate_tranche" for e in r.errors)

    def test_single_tranche_fixture_still_accepted(self):
        # Regression guard: the original single-row PREF fixture must
        # still validate cleanly under the new tranche-aware validator.
        r = validate_preference(_read("pref_trailing_isin.csv"))
        assert r.valid is True
        assert r.record_count == 1

    def test_warrant_still_uses_isin_only_identity_not_tranche_composite(self):
        # WARRANT.csv has no live evidence of a repeated-ISIN pattern —
        # confirms validate_warrant() was NOT switched to the PREF
        # tranche-composite identity model.
        text = (
            "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, MARKET LOT\n"
            "SYNTHWARR,Synthetic Warrant Test,W1,01-JAN-2015,1,INTESTMN0071\n"
            "SYNTHWARR,Synthetic Warrant Test,W1,02-JAN-2015,1,INTESTMN0071\n"
        )
        r = validate_warrant(text)
        assert r.valid is False
        assert any(e.code == "duplicate_isin" for e in r.errors)
        assert r.record_count == 0


# ---------------------------------------------------------------------------
# IDR_W9.csv
# ---------------------------------------------------------------------------


class TestIdrValidator:
    def test_footnote_and_blank_rows_excluded_from_records(self):
        r = validate_idr(_read("idr_footnote_and_blank.csv"))
        assert r.valid is True
        assert r.record_count == 1  # only the one real security row counts

    def test_truncated_genuine_data_row_produces_an_error_not_a_silent_drop(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE*\n"
            "SYNTHIDR,Synthetic IDR Test PLC,DR,01-JAN-2010,1,1,INTESTOP0085,1\n"
            "TRUNCATEDROW,Missing One Field,DR,01-JAN-2010,1,1,INTESTOP0085\n"
        )
        r = validate_idr(text)
        assert r.valid is False
        assert r.error_count == 1
        assert r.warning_count == 0
        assert any(e.code == "invalid_field_count" for e in r.errors)
        assert r.record_count == 1

    def test_asterisk_footnote_recognized_and_ignored(self):
        text = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE*\n"
            "SYNTHIDR,Synthetic IDR Test PLC,DR,01-JAN-2010,1,1,INTESTOP0085,1\n"
            "* Face value is denominated in the issuer's home currency\n"
        )
        r = validate_idr(text)
        assert r.valid is True
        assert r.record_count == 1
        assert r.error_count == 0


# ---------------------------------------------------------------------------
# IL series
# ---------------------------------------------------------------------------


class TestIlSeriesValidator:
    def test_fixture_is_the_exact_reviewed_20_byte_crlf_baseline(self):
        # Guards against the fixture itself silently drifting.
        raw = _read_bytes("il_series_valid_empty.csv")
        assert raw == b"Symbol,Se,Sec Name\r\n"
        assert len(raw) == 20

    def test_header_only_is_valid_empty(self):
        r = validate_il_series(_read_bytes("il_series_valid_empty.csv"))
        assert r.valid is True
        assert r.status == ValidationResultStatus.VALID_EMPTY
        assert r.record_count == 0

    def test_str_input_exact_crlf_baseline_also_accepted(self):
        r = validate_il_series("Symbol,Se,Sec Name\r\n")
        assert r.valid is True
        assert r.status == ValidationResultStatus.VALID_EMPTY

    def test_truncated_header_is_not_valid_empty(self):
        r = validate_il_series("Symbol,Se\n")  # missing "Sec Name" column
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_HEADER

    def test_header_only_rule_is_exclusive_to_il_series(self):
        # An empty-body EQUITY_L.csv (header only, zero data rows) must
        # NOT be treated as valid_empty — that exception is IL-series-only.
        header_only_equity = (
            "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, "
            "MARKET LOT, ISIN NUMBER, FACE VALUE\n"
        )
        r = validate_equity_current(header_only_equity)
        # EQUITY_L's contract has no valid_empty_allowed exception, so an
        # empty body should not produce VALID_EMPTY.
        assert r.status != ValidationResultStatus.VALID_EMPTY

    def test_18_byte_no_newline_body_rejected(self):
        r = validate_il_series(b"Symbol,Se,Sec Name")
        assert r.valid is False
        assert r.status != ValidationResultStatus.VALID_EMPTY

    def test_19_byte_lf_only_body_rejected(self):
        r = validate_il_series(b"Symbol,Se,Sec Name\n")
        assert r.valid is False
        assert r.status != ValidationResultStatus.VALID_EMPTY

    def test_bom_prefixed_body_rejected(self):
        r = validate_il_series(b"\xef\xbb\xbfSymbol,Se,Sec Name\r\n")
        assert r.valid is False
        assert r.status != ValidationResultStatus.VALID_EMPTY

    def test_extra_crlf_rejected(self):
        r = validate_il_series(b"Symbol,Se,Sec Name\r\n\r\n")
        assert r.valid is False
        assert r.status != ValidationResultStatus.VALID_EMPTY

    def test_whitespace_only_second_row_rejected(self):
        r = validate_il_series(b"Symbol,Se,Sec Name\r\n \r\n")
        assert r.valid is False
        assert r.status != ValidationResultStatus.VALID_EMPTY

    def test_altered_header_rejected(self):
        r = validate_il_series(b"Symbol,Se,SecName\r\n")
        assert r.valid is False
        assert r.status == ValidationResultStatus.INVALID_HEADER

    def test_partial_data_row_rejected(self):
        r = validate_il_series(b"Symbol,Se,Sec Name\r\nAAA,X\r\n")
        assert r.valid is False
        assert r.status != ValidationResultStatus.VALID_EMPTY


# ---------------------------------------------------------------------------
# symbolchange.csv / namechange.csv
# ---------------------------------------------------------------------------


class TestSymbolHistoryValidator:
    def test_headerless_four_field_record_accepted(self):
        r = validate_symbol_history(_read("symbol_change_headerless.csv"))
        assert r.valid is True
        assert r.record_count == 1

    def test_never_produces_isin_field(self):
        r = validate_symbol_history(_read("symbol_change_headerless.csv"))
        assert not hasattr(r, "isin")

    def test_malformed_date_rejected(self):
        bad = "Some Co Name,OLDSYM,NEWSYM,2020-01-01\n"  # wrong date shape
        r = validate_symbol_history(bad)
        assert r.valid is False
        assert any(e.code == "malformed_date" for e in r.errors)

    # -- Phase C1-A3: corrected field-order regression -------------------

    def test_realistic_row_maps_fields_in_the_corrected_order(self):
        # Exact shape observed live during C1-A2 reconnaissance:
        # "<company/scheme name>,<old_symbol>,<new_symbol>,<date>" — NOT
        # the previously (incorrectly) declared
        # (old_symbol, new_symbol, company_name, date) ordering.
        row = ["NIPPON INDIA MF - NIPPON INDIA Dual Advantage FTF Sr. x Plan E - GO", "RDAXEDG", "NDAXEDG", "30-OCT-2019"]
        record = parse_symbol_change_row(row, line_number=1)
        assert isinstance(record, SymbolChangeRecord)
        assert record.company_or_scheme_name == "NIPPON INDIA MF - NIPPON INDIA Dual Advantage FTF Sr. x Plan E - GO"
        assert record.old_symbol == "RDAXEDG"
        assert record.new_symbol == "NDAXEDG"
        assert record.effective_date == "30-OCT-2019"

    def test_realistic_company_rename_row_maps_correctly(self):
        # A second, differently-shaped realistic row (plain company
        # rename, not a scheme rename) — confirms the mapping is not
        # coincidentally correct only for scheme-style names.
        row = ["360 ONE WAM LIMITED", "IIFLWAM", "360ONE", "23-JAN-2023"]
        record = parse_symbol_change_row(row, line_number=1)
        assert record.company_or_scheme_name == "360 ONE WAM LIMITED"
        assert record.old_symbol == "IIFLWAM"
        assert record.new_symbol == "360ONE"
        assert record.effective_date == "23-JAN-2023"

    def test_validator_uses_the_same_named_mapping_for_date_validation(self):
        # validate_symbol_history() must reject a malformed date found in
        # the actual 4th (effective_date) position — proves the
        # validator's date check goes through parse_symbol_change_row()
        # rather than a raw, independently-indexed row[N] lookup that
        # could silently drift from the named mapping.
        bad_date_in_correct_position = "Realistic Co Name,OLDSYM,NEWSYM,2019-10-30\n"
        r = validate_symbol_history(bad_date_in_correct_position)
        assert r.valid is False
        assert any(e.code == "malformed_date" for e in r.errors)

    def test_parse_symbol_change_row_rejects_wrong_width(self):
        with pytest.raises(ValueError):
            parse_symbol_change_row(["only", "three", "fields"], line_number=1)

    def test_registry_secondary_identity_fields_match_the_corrected_order(self):
        from services.instrument_master.source_registry import registry_by_id

        entry = registry_by_id(SourceId.NSE_SYMBOL_HISTORY)
        assert entry.secondary_identity_fields == (
            "company_or_scheme_name",
            "old_symbol",
            "new_symbol",
            "effective_date",
        )


class TestNameHistoryValidator:
    def test_exact_duplicate_rows_deterministically_deduplicated(self):
        r = validate_name_history(_read("name_change_exact_duplicates.csv"))
        assert r.valid is True
        assert r.record_count == 1  # 2 identical rows -> 1 deduplicated record
        assert r.warning_count == 1
        assert any(w.code == "exact_duplicate_history_row" for w in r.warnings)
        # The old, ambiguous code must never be used for this warning —
        # it is easily confused with the unrelated ConflictState.DUPLICATE_SOURCE_RECORD.
        assert not any(w.code == "duplicate_source_record" for w in r.warnings)

    def test_dedup_is_order_independent(self):
        text_a = _read("name_change_exact_duplicates.csv")
        lines = text_a.splitlines()
        header, row1, row2 = lines[0], lines[1], lines[2]
        text_b = "\n".join([header, row2, row1]) + "\n"
        r_a = validate_name_history(text_a)
        r_b = validate_name_history(text_b)
        assert r_a.record_count == r_b.record_count
        assert r_a.warning_count == r_b.warning_count

    def test_conflicting_previous_name_retained_and_flagged(self):
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "SYNTHCO,Old Name A,New Name,01-JAN-2020\n"
            "SYNTHCO,Old Name B,New Name,01-JAN-2020\n"  # same symbol+date, different prev name
        )
        r = validate_name_history(text)
        assert r.valid is True
        assert r.record_count == 2  # both variants retained, never merged
        assert r.warning_count == 1
        assert any(w.code == "conflicting_history_rows" for w in r.warnings)

    def test_conflicting_new_name_retained_and_flagged(self):
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "SYNTHCO,Old Name,New Name A,01-JAN-2020\n"
            "SYNTHCO,Old Name,New Name B,01-JAN-2020\n"  # same symbol+date, different new name
        )
        r = validate_name_history(text)
        assert r.valid is True
        assert r.record_count == 2
        assert r.warning_count == 1
        assert any(w.code == "conflicting_history_rows" for w in r.warnings)

    def test_multiple_conflicts_each_flagged_distinctly(self):
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "AAA,Old A1,New A,01-JAN-2020\n"
            "AAA,Old A2,New A,01-JAN-2020\n"
            "BBB,Old B,New B1,02-JAN-2020\n"
            "BBB,Old B,New B2,02-JAN-2020\n"
        )
        r = validate_name_history(text)
        assert r.valid is True
        assert r.record_count == 4
        conflict_warnings = [w for w in r.warnings if w.code == "conflicting_history_rows"]
        assert len(conflict_warnings) == 2  # one per conflicting (symbol, date) key

    def test_three_way_conflict_under_one_key(self):
        # Phase C1-D5 cleanup: locks the three-way-conflict behavior the
        # Phase C1-D4 re-audit confirmed correct by hand but that no
        # shipped test previously exercised.
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "AAA,Old A1,New A,01-JAN-2020\n"
            "AAA,Old A2,New A,01-JAN-2020\n"
            "AAA,Old A3,New A,01-JAN-2020\n"
        )
        r = validate_name_history(text)
        assert r.valid is True
        assert r.record_count == 3
        assert r.warning_count == 1
        conflict_warning = next(w for w in r.warnings if w.code == "conflicting_history_rows")
        assert "3 conflicting variants" in conflict_warning.detail

    def test_exact_duplicate_mixed_with_conflict_in_same_file(self):
        # Phase C1-D5 cleanup: locks the mixed exact-duplicate-plus-
        # conflict behavior the Phase C1-D4 re-audit confirmed correct
        # by hand but that no shipped test previously exercised.
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "AAA,Old A1,New A,01-JAN-2020\n"
            "AAA,Old A1,New A,01-JAN-2020\n"  # exact dup of the row above
            "AAA,Old A2,New A,01-JAN-2020\n"  # conflicts with the group
        )
        r = validate_name_history(text)
        assert r.valid is True
        assert r.record_count == 2
        assert r.warning_count == 2
        codes = {w.code for w in r.warnings}
        assert codes == {"exact_duplicate_history_row", "conflicting_history_rows"}

    def test_multi_key_warning_ordering_is_deterministic(self):
        # Warnings must be ordered by key (not insertion/appearance order)
        # so re-running against the same input always produces the same
        # warning sequence.
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "ZZZ,Old Z1,New Z,05-JAN-2020\n"
            "ZZZ,Old Z2,New Z,05-JAN-2020\n"
            "AAA,Old A1,New A,01-JAN-2020\n"
            "AAA,Old A2,New A,01-JAN-2020\n"
        )
        r = validate_name_history(text)
        conflict_warnings = [w for w in r.warnings if w.code == "conflicting_history_rows"]
        assert len(conflict_warnings) == 2
        # AAA sorts before ZZZ regardless of appearance order in the input.
        assert "AAA" in conflict_warnings[0].detail
        assert "ZZZ" in conflict_warnings[1].detail

    def test_conflict_detection_is_input_order_independent(self):
        text_a = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "SYNTHCO,Old Name A,New Name,01-JAN-2020\n"
            "SYNTHCO,Old Name B,New Name,01-JAN-2020\n"
        )
        text_b = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "SYNTHCO,Old Name B,New Name,01-JAN-2020\n"
            "SYNTHCO,Old Name A,New Name,01-JAN-2020\n"
        )
        r_a = validate_name_history(text_a)
        r_b = validate_name_history(text_b)
        assert r_a.record_count == r_b.record_count
        assert r_a.warning_count == r_b.warning_count

    def test_complete_serialized_result_equal_after_equivalent_row_reordering(self):
        import dataclasses
        import json

        text_a = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "AAA,Old A1,New A,01-JAN-2020\n"
            "AAA,Old A2,New A,01-JAN-2020\n"
            "BBB,Old B,New B,02-JAN-2020\n"
        )
        lines = text_a.splitlines()
        text_b = "\n".join([lines[0], lines[3], lines[1], lines[2]]) + "\n"
        r_a = validate_name_history(text_a)
        r_b = validate_name_history(text_b)
        d_a = json.dumps(dataclasses.asdict(r_a), default=str, sort_keys=True)
        d_b = json.dumps(dataclasses.asdict(r_b), default=str, sort_keys=True)
        assert d_a == d_b

    def test_conflicting_rows_never_automatically_establish_identity(self):
        # The result carries no identity/merge field of any kind for
        # conflicting rows -- confirms no automatic identity linkage.
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "SYNTHCO,Old Name A,New Name,01-JAN-2020\n"
            "SYNTHCO,Old Name B,New Name,01-JAN-2020\n"
        )
        r = validate_name_history(text)
        assert not hasattr(r, "resolved_identity")
        assert not hasattr(r, "isin")


# ---------------------------------------------------------------------------
# namechange.csv ordered, first-occurrence-wins dedup (Phase C1-A3)
# ---------------------------------------------------------------------------


class TestDedupeNameHistoryRows:
    def test_exact_duplicates_removed_keeping_first_occurrence(self):
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "AAA,Old A,New A,01-JAN-2020\n"
            "BBB,Old B,New B,02-JAN-2020\n"
            "AAA,Old A,New A,01-JAN-2020\n"  # exact duplicate of row 1
        )
        result = dedupe_name_history_rows(text)
        assert result == [
            ("AAA", "Old A", "New A", "01-JAN-2020"),
            ("BBB", "Old B", "New B", "02-JAN-2020"),
        ]

    def test_near_duplicates_are_never_collapsed(self):
        # Same symbol and date, but a different NCH_PREV_NAME -- must be
        # retained as its own distinct entry, not treated as a duplicate.
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "SYNTHCO,Old Name A,New Name,01-JAN-2020\n"
            "SYNTHCO,Old Name B,New Name,01-JAN-2020\n"
        )
        result = dedupe_name_history_rows(text)
        assert len(result) == 2
        assert ("SYNTHCO", "Old Name A", "New Name", "01-JAN-2020") in result
        assert ("SYNTHCO", "Old Name B", "New Name", "01-JAN-2020") in result

    def test_stable_input_order_preserved(self):
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "ZZZ,Old Z,New Z,05-JAN-2020\n"
            "AAA,Old A,New A,01-JAN-2020\n"
            "MMM,Old M,New M,03-JAN-2020\n"
        )
        result = dedupe_name_history_rows(text)
        # Input order (Z, A, M), never re-sorted alphabetically or by date.
        assert [row[0] for row in result] == ["ZZZ", "AAA", "MMM"]

    def test_matches_validate_name_history_record_count(self):
        text = _read("name_change_exact_duplicates.csv")
        deduped = dedupe_name_history_rows(text)
        r = validate_name_history(text)
        assert len(deduped) == r.record_count

    def test_malformed_rows_silently_skipped_here_not_double_reported(self):
        text = (
            "NCH_SYMBOL, NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT\n"
            "AAA,Old A,New A,01-JAN-2020\n"
            "TRUNCATED,Missing Field\n"
        )
        result = dedupe_name_history_rows(text)
        assert result == [("AAA", "Old A", "New A", "01-JAN-2020")]
