"""
Unit tests for services/postmortem/outbox_queue_health.py — Wave C, WC-O
(corrected) §O2 (typed snapshot) and §O4 (noise-bounded transition/
reminder logging), using the injectable clock so behavior is
deterministic without real time passing.
"""
import logging

import pytest

from services.postmortem.outbox_queue_health import (
    QueueHealthLogState,
    QueueHealthSnapshot,
    REMINDER_INTERVAL_SECONDS,
    apply_transition_and_log,
)


def _snapshot(**overrides):
    base = dict(
        pending=0, generating_active=0, generating_expired_lease=0, failed_retryable=0,
        failed_terminal=0, oldest_pending_age_seconds=None, oldest_generating_age_seconds=None,
        oldest_failed_retryable_age_seconds=None, generating_over_poll_window_count=0,
    )
    base.update(overrides)
    return QueueHealthSnapshot(**base)


@pytest.mark.unit
class TestExpiredLeaseTransitionLogging:
    def test_first_non_zero_warns_immediately(self, caplog):
        caplog.set_level(logging.WARNING)
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        assert "[outbox_worker_expired_lease] count=3" in caplog.text

    def test_unchanged_non_zero_count_is_suppressed_before_the_reminder_interval(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        caplog.clear()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0 + 10)
        assert "[outbox_worker_expired_lease]" not in caplog.text

    def test_increased_count_warns_again_even_within_the_reminder_interval(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        caplog.clear()
        apply_transition_and_log(_snapshot(generating_expired_lease=5), state=state, now=1000.0 + 10)
        assert "[outbox_worker_expired_lease] count=5" in caplog.text

    def test_reminder_fires_after_the_documented_interval(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        caplog.clear()
        apply_transition_and_log(
            _snapshot(generating_expired_lease=3), state=state, now=1000.0 + REMINDER_INTERVAL_SECONDS + 1,
        )
        assert "[outbox_worker_expired_lease] count=3" in caplog.text

    def test_no_early_reminder_just_before_the_interval_elapses(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        caplog.clear()
        apply_transition_and_log(
            _snapshot(generating_expired_lease=3), state=state, now=1000.0 + REMINDER_INTERVAL_SECONDS - 1,
        )
        assert "[outbox_worker_expired_lease]" not in caplog.text

    def test_return_to_zero_resets_state(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        apply_transition_and_log(_snapshot(generating_expired_lease=0), state=state, now=1000.0 + 5)
        assert state.last_expired_lease_warning_at is None
        assert state.last_expired_lease_count == 0

    def test_warns_immediately_after_a_new_transition_following_a_reset(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=3), state=state, now=1000.0)
        apply_transition_and_log(_snapshot(generating_expired_lease=0), state=state, now=1000.0 + 5)
        caplog.clear()
        apply_transition_and_log(_snapshot(generating_expired_lease=2), state=state, now=1000.0 + 6)
        assert "[outbox_worker_expired_lease] count=2" in caplog.text

    def test_never_warns_while_count_stays_zero(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(generating_expired_lease=0), state=state, now=1000.0)
        assert "[outbox_worker_expired_lease]" not in caplog.text


@pytest.mark.unit
class TestTerminalFailureTransitionLogging:
    """Same transition/reminder machinery, independent state — a
    terminal-failure warning must never suppress or interfere with an
    expired-lease warning in the same cycle."""

    def test_independent_from_expired_lease_tracking(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(
            _snapshot(generating_expired_lease=1, failed_terminal=2), state=state, now=1000.0,
        )
        assert "[outbox_worker_expired_lease] count=1" in caplog.text
        assert "[outbox_worker_terminal_failure_backlog] count=2" in caplog.text

    def test_terminal_failure_reminder_after_interval(self, caplog):
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(failed_terminal=4), state=state, now=1000.0)
        caplog.clear()
        apply_transition_and_log(_snapshot(failed_terminal=4), state=state, now=1000.0 + 5)
        assert "[outbox_worker_terminal_failure_backlog]" not in caplog.text
        apply_transition_and_log(
            _snapshot(failed_terminal=4), state=state, now=1000.0 + REMINDER_INTERVAL_SECONDS + 1,
        )
        assert "[outbox_worker_terminal_failure_backlog] count=4" in caplog.text


@pytest.mark.unit
class TestHeartbeat:
    def test_first_call_always_emits_the_heartbeat_line(self, caplog):
        caplog.set_level(logging.INFO)
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(pending=5), state=state, now=1000.0)
        assert "[outbox_worker_queue_depth] pending=5" in caplog.text

    def test_null_ages_render_as_the_literal_null_token(self, caplog):
        caplog.set_level(logging.INFO)
        state = QueueHealthLogState()
        apply_transition_and_log(_snapshot(), state=state, now=1000.0)
        assert "oldest_pending_age_s=null" in caplog.text
