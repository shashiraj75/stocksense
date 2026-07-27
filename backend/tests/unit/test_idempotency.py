"""
Unit tests for services/postmortem/idempotency.py — pure fingerprint
calculation and decision logic. No DB, no HTTP.
"""
import datetime as dt

import pytest

from services.postmortem.idempotency import (
    ExistingIdempotencyRow,
    IdempotencyAction,
    IdempotencyRowStatus,
    STALE_PENDING_THRESHOLD,
    compute_request_fingerprint,
    decide_action,
    validate_idempotency_key_format,
)

UTC = dt.timezone.utc


def _fp(**over):
    d = dict(
        market="US", symbol="AAPL", quantity=1, price=100.0, stop_loss=None,
        target_price=None, trade_management_mode="manual", evidence_source="MANUAL",
        entry_evidence_schema_version="1.0.0",
    )
    d.update(over)
    return compute_request_fingerprint(**d)


@pytest.mark.unit
class TestComputeRequestFingerprint:
    def test_identical_requests_produce_identical_fingerprint(self):
        assert _fp() == _fp()

    def test_different_quantity_produces_different_fingerprint(self):
        assert _fp(quantity=1) != _fp(quantity=2)

    def test_different_symbol_produces_different_fingerprint(self):
        assert _fp(symbol="AAPL") != _fp(symbol="MSFT")

    def test_symbol_and_market_are_case_normalized(self):
        assert _fp(symbol="aapl", market="us") == _fp(symbol="AAPL", market="US")

    def test_float_rounding_noise_does_not_change_fingerprint(self):
        assert _fp(price=100.0) == _fp(price=100.0000001)

    def test_different_stop_loss_produces_different_fingerprint(self):
        assert _fp(stop_loss=90.0) != _fp(stop_loss=95.0)

    def test_none_vs_zero_stop_loss_are_different(self):
        assert _fp(stop_loss=None) != _fp(stop_loss=0.0)

    def test_different_evidence_source_produces_different_fingerprint(self):
        assert _fp(evidence_source="MANUAL") != _fp(evidence_source="DAILY_PICK")

    def test_fingerprint_contains_no_obvious_secret_shape(self):
        # Not a rigorous security proof — just confirms the fingerprint is a
        # plain hex digest, not e.g. a base64-embedded copy of the inputs.
        digest = _fp()
        assert all(c in "0123456789abcdef" for c in digest)
        assert len(digest) == 64  # sha256 hex length


@pytest.mark.unit
class TestValidateIdempotencyKeyFormat:
    def test_valid_uuid_shaped_key_accepted(self):
        validate_idempotency_key_format("550e8400-e29b-41d4-a716-446655440000")

    def test_too_short_key_rejected(self):
        with pytest.raises(ValueError):
            validate_idempotency_key_format("short")

    def test_too_long_key_rejected(self):
        with pytest.raises(ValueError):
            validate_idempotency_key_format("x" * 200)

    def test_disallowed_characters_rejected(self):
        with pytest.raises(ValueError):
            validate_idempotency_key_format("not a valid key!!")

    def test_sql_injection_shaped_key_rejected(self):
        with pytest.raises(ValueError):
            validate_idempotency_key_format("'; DROP TABLE x; --")


@pytest.mark.unit
class TestDecideAction:
    def test_no_existing_row_proceeds_fresh(self):
        assert decide_action(None, "fp-1") == IdempotencyAction.PROCEED_FRESH

    def test_fingerprint_mismatch_is_conflict_regardless_of_status(self):
        for status in (IdempotencyRowStatus.PENDING, IdempotencyRowStatus.COMPLETED, IdempotencyRowStatus.FAILED):
            row = ExistingIdempotencyRow(
                status=status.value, request_fingerprint="fp-old",
                response_body=None, created_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
            )
            assert decide_action(row, "fp-new") == IdempotencyAction.CONFLICT_FINGERPRINT_MISMATCH

    def test_completed_row_replays(self):
        row = ExistingIdempotencyRow(
            status=IdempotencyRowStatus.COMPLETED.value, request_fingerprint="fp-1",
            response_body={"trade_id": 1}, created_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert decide_action(row, "fp-1") == IdempotencyAction.REPLAY_COMPLETED

    def test_failed_row_is_reclaimed(self):
        row = ExistingIdempotencyRow(
            status=IdempotencyRowStatus.FAILED.value, request_fingerprint="fp-1",
            response_body=None, created_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert decide_action(row, "fp-1") == IdempotencyAction.PROCEED_RECLAIMED

    def test_fresh_pending_row_is_still_in_progress(self):
        now = dt.datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
        row = ExistingIdempotencyRow(
            status=IdempotencyRowStatus.PENDING.value, request_fingerprint="fp-1",
            response_body=None, created_at=dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        assert decide_action(row, "fp-1", now=now) == IdempotencyAction.STILL_IN_PROGRESS

    def test_stale_pending_row_is_reclaimed(self):
        row_created = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        now = row_created + STALE_PENDING_THRESHOLD + dt.timedelta(seconds=1)
        row = ExistingIdempotencyRow(
            status=IdempotencyRowStatus.PENDING.value, request_fingerprint="fp-1",
            response_body=None, created_at=row_created,
        )
        assert decide_action(row, "fp-1", now=now) == IdempotencyAction.PROCEED_RECLAIMED

    def test_pending_row_exactly_at_threshold_is_not_yet_stale(self):
        row_created = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        now = row_created + STALE_PENDING_THRESHOLD  # exactly equal, not exceeding
        row = ExistingIdempotencyRow(
            status=IdempotencyRowStatus.PENDING.value, request_fingerprint="fp-1",
            response_body=None, created_at=row_created,
        )
        assert decide_action(row, "fp-1", now=now) == IdempotencyAction.STILL_IN_PROGRESS

    def test_naive_created_at_is_treated_as_utc_not_silently_stripped(self):
        """created_at without tzinfo (defensive case) must still compare
        correctly rather than raising a naive/aware TypeError."""
        row_created = dt.datetime(2026, 1, 1, 0, 0, 0)  # naive
        now = dt.datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
        row = ExistingIdempotencyRow(
            status=IdempotencyRowStatus.PENDING.value, request_fingerprint="fp-1",
            response_body=None, created_at=row_created,
        )
        assert decide_action(row, "fp-1", now=now) == IdempotencyAction.STILL_IN_PROGRESS
