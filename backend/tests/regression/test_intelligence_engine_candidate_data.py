"""Epic 007 Phase 3B-B — build_candidate_market_data_from_payload unit
tests. Pure-function tests only; synthetic payloads shaped exactly like a
real generate_picks() return value, no network/database involved."""
import pytest
from datetime import datetime, timezone, timedelta

from services.intelligence_engine.candidate_data import build_candidate_market_data_from_payload


def _pick(symbol, price=100.0, generated_at=None, as_of=None, quality_factors=None):
    return {
        "symbol": symbol,
        "price": price,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "generation_reference_as_of": as_of,
        "quality_factors": quality_factors or {},
    }


@pytest.mark.regression
class TestBuildCandidateMarketData:
    def test_extracts_price_and_marks_quote_available(self):
        payload = {"picks": {"medium": [_pick("AAPL", price=150.0)]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["AAPL"]["price"] == 150.0
        assert result["AAPL"]["has_quote"] is True

    def test_zero_or_negative_price_marks_quote_unavailable(self):
        payload = {"picks": {"medium": [_pick("XYZ", price=0)]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["XYZ"]["has_quote"] is False

    def test_missing_price_marks_quote_unavailable(self):
        pick = _pick("XYZ")
        pick["price"] = None
        payload = {"picks": {"medium": [pick]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["XYZ"]["has_quote"] is False

    def test_deduplicates_symbol_across_horizons_keeping_first(self):
        payload = {
            "picks": {
                "short": [_pick("AAPL", price=100.0)],
                "medium": [_pick("AAPL", price=999.0)],  # should be ignored — short's data wins
            }
        }
        result = build_candidate_market_data_from_payload(payload)
        assert len(result) == 1
        assert result["AAPL"]["price"] == 100.0

    def test_quote_age_computed_from_generation_reference_as_of(self):
        as_of = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        payload = {"picks": {"medium": [_pick("AAPL", as_of=as_of)]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["AAPL"]["quote_age_days"] is not None
        assert 2.9 < result["AAPL"]["quote_age_days"] < 3.1

    def test_quote_age_falls_back_to_generated_at_when_as_of_missing(self):
        generated_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        payload = {"picks": {"medium": [_pick("AAPL", generated_at=generated_at, as_of=None)]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["AAPL"]["quote_age_days"] is not None
        assert 0.9 < result["AAPL"]["quote_age_days"] < 1.1

    def test_fundamentals_completeness_scales_with_populated_fields(self):
        full = _pick("A", quality_factors={"sector": "Tech", "piotroski": 8, "altman_z": 3.5, "altman_zone": "safe"})
        empty = _pick("B", quality_factors={})
        payload = {"picks": {"medium": [full, empty]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["A"]["fundamentals_completeness_pct"] == 100.0
        assert result["B"]["fundamentals_completeness_pct"] == 0.0

    def test_volume_and_liquidity_fields_always_none_not_fabricated(self):
        payload = {"picks": {"medium": [_pick("AAPL")]}}
        result = build_candidate_market_data_from_payload(payload)
        assert result["AAPL"]["avg_daily_value"] is None
        assert result["AAPL"]["avg_daily_volume"] is None
        assert result["AAPL"]["zero_volume_days"] is None
        assert result["AAPL"]["free_float_pct"] is None
        assert result["AAPL"]["history_depth_days"] is None
        # has_volume/trading_status are unknowns, so they must fail-open
        # (True / None), never a fabricated negative signal.
        assert result["AAPL"]["has_volume"] is True
        assert result["AAPL"]["trading_status"] is None

    def test_empty_payload_returns_empty_dict(self):
        assert build_candidate_market_data_from_payload({}) == {}
        assert build_candidate_market_data_from_payload({"picks": {}}) == {}

    def test_malformed_pick_does_not_break_the_rest(self):
        payload = {"picks": {"medium": [{"not_a_symbol_field": True}, _pick("AAPL")]}}
        result = build_candidate_market_data_from_payload(payload)
        assert "AAPL" in result
        assert len(result) == 1

    def test_none_payload_does_not_raise(self):
        assert build_candidate_market_data_from_payload(None) == {}
