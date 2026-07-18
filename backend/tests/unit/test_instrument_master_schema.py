"""
Phase C0-A — schema/enum sanity for the offline instrument-master
foundation. Pure logic, no I/O.
"""
import dataclasses

import pytest

from services.instrument_master.enums import (
    ActiveStatus,
    ClassificationStatus,
    EligibilityStatus,
    ExchangeSeries,
    FundamentalsCoverageStatus,
    InstrumentCategory,
    MarketDataStatus,
    MinimumHistoryBand,
)
from services.instrument_master.schema import InstrumentMasterRecord


class TestInstrumentCategoryEnum:
    def test_required_members_present(self):
        expected = {
            "ordinary_common_equity",
            "sme_equity",
            "etf",
            "reit",
            "invit",
            "preference_share",
            "partly_paid_equity",
            "warrant",
            "rights_entitlement",
            "auxiliary_tracking_instrument",
            "unknown_unclassified",
        }
        assert {m.value for m in InstrumentCategory} == expected


class TestExchangeSeriesEnum:
    def test_required_members_present(self):
        assert {m.value for m in ExchangeSeries} == {"EQ", "BE", "BZ", "OTHER", "UNKNOWN"}


class TestClassificationStatusEnum:
    def test_required_members_present(self):
        assert {m.value for m in ClassificationStatus} == {
            "verified",
            "provisional",
            "ambiguous",
            "unavailable",
        }


class TestEligibilityStatusEnum:
    def test_required_members_present(self):
        assert {m.value for m in EligibilityStatus} == {
            "eligible",
            "shadow_only",
            "surveillance_series",
            "insufficient_history",
            "missing_fundamentals",
            "stale_data",
            "inactive",
            "unsupported_instrument",
            "ambiguous_classification",
        }


class TestRecordConstruction:
    def _minimal_record(self, **overrides) -> InstrumentMasterRecord:
        base = dict(
            schema_version=1,
            exchange="NSE",
            symbol="TESTSYM",
            display_name="Test Symbol Limited",
            isin="INTEST0000001",
            series=ExchangeSeries.EQ,
            instrument_category=InstrumentCategory.UNKNOWN_UNCLASSIFIED,
            classification_status=ClassificationStatus.UNAVAILABLE,
            listing_date="2010-01-01",
            active_status=ActiveStatus.UNKNOWN,
            source_identifier="unit_test",
            source_publication_date=None,
            record_version=1,
            eligibility_status=EligibilityStatus.AMBIGUOUS_CLASSIFICATION,
        )
        base.update(overrides)
        return InstrumentMasterRecord(**base)

    def test_constructs_with_required_fields_only(self):
        record = self._minimal_record()
        assert record.symbol == "TESTSYM"
        assert record.isin == "INTEST0000001"
        assert record.eligibility_reason_codes == ()
        assert record.previous_symbol is None
        assert record.symbol_history == ()
        assert record.series_history == ()
        assert record.minimum_history_band == MinimumHistoryBand.UNKNOWN
        assert record.fundamentals_coverage_status == FundamentalsCoverageStatus.UNKNOWN
        assert record.market_data_status == MarketDataStatus.UNKNOWN

    def test_record_is_immutable(self):
        record = self._minimal_record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.symbol = "OTHER"  # type: ignore[misc]

    def test_isin_may_be_none_but_symbol_is_never_treated_as_identity_field(self):
        # A record with no ISIN is still constructible (retained, not
        # dropped) — symbol alone is never elevated to identity status by
        # the schema itself; that determination lives in parser.py.
        record = self._minimal_record(isin=None)
        assert record.isin is None
        assert record.symbol == "TESTSYM"
