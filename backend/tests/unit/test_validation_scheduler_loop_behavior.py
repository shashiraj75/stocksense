"""
2026-09-07 PR #87 corrective review — behavioral verification of the
ACTUAL `_validation_schedule_loop` coroutine (api/main.py), not just its
pure helper functions (`enabled_validation_combinations`,
`compute_missed_validation_combinations`), which alone do not establish
correct execution behavior — they never run inside the real async loop
control flow (sleep -> wake -> admit sequentially -> sleep again).

Every test here drives the REAL coroutine with mocked `asyncio.sleep`
(never actually blocks), a mocked `next_saturday_1200_utc` (controls
"what time it is" without touching a real clock), and a mocked
`execute_admitted_validation` (records every call, never touches a
database, network, or a real validation run). No production job, no
network call, nothing is ever really admitted or executed.

The loop is `while True` by design — every test terminates it
deterministically by raising a private marker exception from inside the
mocked `asyncio.sleep` once enough iterations have been observed, then
asserts on the exact sequence of recorded calls.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest


class _StopLoop(BaseException):
    """Marker used to terminate the infinite scheduler loop deterministically.
    Deliberately a BaseException, NOT an Exception subclass — the real
    loop under test has its own `except Exception:` backoff handler
    (unrelated to this test, pre-existing), which would otherwise swallow
    an Exception-based marker and require one extra unwanted iteration
    (an unmocked asyncio.sleep(3600) call) before it actually propagated."""


def _install_recording_sleep(monkeypatch, *, stop_after_calls: int | None = None):
    """Replaces asyncio.sleep with a non-blocking recorder. If
    `stop_after_calls` is given, raises _StopLoop on the Nth call
    (1-indexed) instead of sleeping — used to terminate the `while True`
    loop deterministically at an exact, known point."""
    calls: list[float] = []

    async def _fake_sleep(seconds):
        calls.append(seconds)
        if stop_after_calls is not None and len(calls) >= stop_after_calls:
            raise _StopLoop()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return calls


def _install_recording_admission(monkeypatch, *, results_by_combo: dict | None = None):
    """Replaces services.validation_engine.execute_admitted_validation
    with a recorder. Returns the list of recorded call kwargs. Never
    touches a database or network."""
    import services.validation_engine as ve
    admitted: list[dict] = []

    def _fake_execute_admitted_validation(**kwargs):
        admitted.append(kwargs)
        key = (kwargs["horizon"], kwargs["universe"])
        if results_by_combo and key in results_by_combo:
            return results_by_combo[key]
        return {"ok": True, "run_id": len(admitted)}

    monkeypatch.setattr(ve, "execute_admitted_validation", _fake_execute_admitted_validation)
    return admitted


def _install_fixed_next_saturday(monkeypatch, instants: list[datetime]):
    """Replaces services.market_calendar.next_saturday_1200_utc with a
    stub that returns successive values from `instants` on each call
    (last value repeats if exhausted) — lets a test control "what the
    next scheduled slot is" across multiple loop iterations without a
    real clock."""
    import services.market_calendar as mc
    calls = {"n": 0}

    def _fake_next_saturday_1200_utc(now_utc):
        idx = min(calls["n"], len(instants) - 1)
        calls["n"] += 1
        return instants[idx]

    monkeypatch.setattr(mc, "next_saturday_1200_utc", _fake_next_saturday_1200_utc)


# Far-future dates — asyncio.sleep is always mocked in these tests, but
# the loop still computes `next_run - datetime.now(timezone.utc)` using
# the REAL wall clock (not mocked) to derive the value it passes to that
# mocked sleep; using real-past dates here would make that arithmetic
# produce a negative number, which happens to still not error (sleep is
# faked) but would defeat any assertion comparing sleep durations.
SAT1 = datetime(2030, 8, 17, 12, 0, tzinfo=timezone.utc)   # a real Saturday
SAT2 = datetime(2030, 8, 24, 12, 0, tzinfo=timezone.utc)   # the following Saturday


async def _run_loop_until_stopped(main_module):
    with contextlib.suppress(_StopLoop):
        await main_module._validation_schedule_loop()


@pytest.mark.unit
class TestSchedulerLoopSequencing:
    def test_no_admission_call_happens_before_the_first_sleep_returns(self, monkeypatch):
        """The loop's very first action is sleeping until the scheduled
        slot — no admission can happen before that sleep call is made."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        sleeps = _install_recording_sleep(monkeypatch, stop_after_calls=2)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        # First sleep = the 180s settle delay; second = the actual wait
        # for the weekly slot. The loop is stopped at that second sleep
        # BEFORE any admission has been attempted.
        assert len(sleeps) == 2
        assert admitted == []

    def test_all_enabled_combinations_admitted_under_the_same_scheduled_slot(self, monkeypatch):
        """Once the weekly slot sleep completes, every currently-enabled
        combination must be admitted sequentially, all sharing the
        EXACT SAME scheduled_slot identity — proving a batch has one
        durable weekly identity, not nine independent ones."""
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100")
        # settle(1) + weekly-wait(1) + 7 combos * 5-min gap(7) = 9 sleeps
        # to get through exactly one full batch (1 short + 6 medium/long);
        # stop right after the last combination's own gap-sleep.
        sleeps = _install_recording_sleep(monkeypatch, stop_after_calls=9)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 7  # 1 short (nifty100) + 6 medium/long
        # Every admission shares the SAME scheduled_slot INSTANT — one
        # durable weekly batch — even though short and medium/long use
        # distinct schedule_version identities (see
        # SHORT_WEEKLY_SCHEDULE_VERSION vs "v1" — a deliberate correction,
        # not an inconsistency: short's old daily-session ledger rows must
        # never be confused with its new weekly identity).
        assert all(call["scheduled_slot"] == SAT1 for call in admitted)
        assert all(call["trigger_type"] == "scheduler" for call in admitted)
        short_calls = [c for c in admitted if c["horizon"] == "short"]
        medium_long_calls = [c for c in admitted if c["horizon"] in ("medium", "long")]
        assert all(c["schedule_version"] == main_module.SHORT_WEEKLY_SCHEDULE_VERSION for c in short_calls)
        assert all(c["schedule_version"] == "v1" for c in medium_long_calls)
        # Every admission in one batch shares the SAME owner identity too.
        owners = {call["owner"] for call in admitted}
        assert len(owners) == 1

    def test_combinations_execute_sequentially_not_concurrently(self, monkeypatch):
        """Each combination is fully awaited (including its own
        completion) before the next one is even started — proven by the
        exact interleaving of sleep calls between admissions: one 5-min
        gap sleep between every pair of admissions, never two admissions
        back-to-back with no intervening sleep."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        call_log: list[str] = []

        async def _fake_sleep(seconds):
            call_log.append(f"sleep({seconds})")
            if len([c for c in call_log if c.startswith("sleep")]) >= 8:
                raise _StopLoop()

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

        import services.validation_engine as ve

        def _fake_execute(**kwargs):
            call_log.append(f"admit({kwargs['horizon']}/{kwargs['universe']})")
            return {"ok": True, "run_id": 1}

        monkeypatch.setattr(ve, "execute_admitted_validation", _fake_execute)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        # After the initial two sleeps (settle + weekly-wait), the pattern
        # must strictly alternate admit, sleep, admit, sleep, ... — never
        # two admits in a row.
        after_initial = call_log[2:]
        admits_indices = [i for i, c in enumerate(after_initial) if c.startswith("admit")]
        for i in admits_indices:
            if i + 1 < len(after_initial):
                assert after_initial[i + 1].startswith("sleep"), (
                    f"expected a 5-min gap sleep immediately after {after_initial[i]}, "
                    f"got {after_initial[i + 1]!r} — combinations may be running concurrently"
                )

    def test_failed_and_successful_admissions_are_both_recorded_and_distinguishable(self, monkeypatch, caplog):
        """A lease-rejected/failed combination must not be silently
        treated as complete — and must not stop the batch from
        continuing to the next combination."""
        import logging
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "")
        sleeps = _install_recording_sleep(monkeypatch, stop_after_calls=8)
        admitted = _install_recording_admission(
            monkeypatch,
            results_by_combo={("medium", "midcap"): {"ok": False, "reason": "already_leased"}},
        )
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        with caplog.at_level(logging.WARNING):
            asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6  # all 6 medium/long combos still attempted
        rejected_logged = any(
            "medium/midcap" in r.message and "rejected/failed" in r.message for r in caplog.records
        )
        assert rejected_logged, "a rejected/failed admission must be logged distinctly, not silently dropped"

    def test_batch_can_continue_past_the_nominal_saturday_instant_without_a_deadline(self, monkeypatch):
        """No wall-clock ceiling is imposed on the OUTER batch loop
        itself — only individual attempts have their own existing
        max_run_duration_seconds (unchanged, untouched by this PR). A
        batch with many combinations and many 5-minute gaps must be
        allowed to keep going regardless of how much real time that
        represents."""
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap,us")
        # 1 settle + 1 weekly-wait + 9 combos * (1 admit + 1 gap-sleep) = 11 sleeps total
        sleeps = _install_recording_sleep(monkeypatch, stop_after_calls=11)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 9  # 3 short + 6 medium/long — the full batch ran to completion
        # Total elapsed "time" the batch represents (ignoring the 180s settle
        # and the weekly wait) is at least 9 * 5 minutes — hours in a real
        # deployment for a larger enabled set — and nothing in the loop
        # aborted it partway through.
        gap_sleeps = [s for s in sleeps if s == 5 * 60]
        assert len(gap_sleeps) == 9


@pytest.mark.unit
class TestSchedulerLoopReschedulesForNextWeek:
    def test_after_a_full_batch_the_loop_sleeps_for_the_next_weekly_slot_not_immediately_again(self, monkeypatch):
        """After finishing one week's batch, the loop must go back to
        sleeping until the NEXT scheduled slot — it must not immediately
        loop back and re-admit the same or a new batch with no wait."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # 1 settle + 1 weekly-wait + 6 combos*(1 gap-sleep) + 1 NEXT weekly-wait = 9
        sleeps = _install_recording_sleep(monkeypatch, stop_after_calls=9)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1, SAT2])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6  # exactly one week's batch — the second week hasn't started admitting yet
        # The final recorded sleep must be a genuinely long wait (the
        # scheduler computes it as next_run - real wall-clock "now", which
        # this test does not mock — only next_saturday_1200_utc's RETURN
        # VALUE is controlled), never the short 5-minute inter-combination
        # gap — proving the loop correctly went back to "wait for next
        # week" rather than re-entering the combination loop immediately.
        assert sleeps[-1] != 5 * 60
        assert sleeps[-1] > 5 * 60
