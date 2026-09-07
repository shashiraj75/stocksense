"""
2026-09-07 PR #87 corrective review + timing correction — behavioral
verification of the ACTUAL `_validation_schedule_loop` coroutine
(api/main.py), not just its pure helper functions
(`enabled_validation_combinations`, `compute_missed_validation_combinations`),
which alone do not establish correct execution behavior — they never run
inside the real async loop control flow (sleep -> re-check actual time ->
admit sequentially -> sleep again).

Every test here drives the REAL coroutine with:
  - a mocked `asyncio.sleep` (never actually blocks the test), COUPLED to
  - a mocked, externally-controllable UTC clock (`datetime.now(timezone.utc)`
    inside api/main.py), which every fake sleep call advances by exactly
    the requested duration — so "the loop woke up N minutes late" is
    modeled as a genuine, controllable fact the loop's own re-read of
    `datetime.now()` observes, not merely inferred from sequencing; and
  - a mocked `execute_admitted_validation` (records every call, never
    touches a database, network, or a real validation run).
No production job, no network call, nothing is ever really admitted or
executed. These tests prove scheduler CONTROL FLOW and TIMING-BOUNDARY
behavior only — they do not exercise, and make no claim about, real
database concurrency (that is covered separately by the untouched
TestAdmissionGating/TestStaleRecoveryDuringExecution/etc. classes, which
exercise the real ledger against a real — if isolated/test — database).

The loop is `while True` by design — every test terminates it
deterministically by raising a private marker exception from inside the
mocked `asyncio.sleep` once enough iterations have been observed, then
asserts on the exact sequence of recorded calls and/or the final clock
state.
"""
import asyncio
import contextlib
import datetime as _datetime_module
from datetime import datetime, timedelta, timezone

import pytest


class _StopLoop(BaseException):
    """Marker used to terminate the infinite scheduler loop deterministically.
    Deliberately a BaseException, NOT an Exception subclass — the real
    loop under test has its own `except Exception:` backoff handler
    (unrelated to this test, pre-existing), which would otherwise swallow
    an Exception-based marker and require one extra unwanted iteration
    (an unmocked asyncio.sleep(3600) call) before it actually propagated."""


class _Clock:
    """Mutable fake-"now" holder. `.value` is what
    datetime.now(timezone.utc) returns inside api/main.py once installed
    via `_install_clock_and_sleep` below."""
    def __init__(self, start: datetime):
        self.value = start


def _install_clock_and_sleep(monkeypatch, start: datetime, *, stop_after_calls: int | None = None):
    """Couples a fake, externally-controllable UTC clock to a fake
    `asyncio.sleep`: every sleep call advances the clock by exactly the
    requested duration (as if that much real time had elapsed), and
    `datetime.now(timezone.utc)` calls made from inside api/main.py (a
    local `from datetime import datetime` executed once per
    `_validation_schedule_loop()` invocation) observe that same
    advancing clock — achieved by monkeypatching the `datetime` class
    attribute on the real `datetime` module itself, which that local
    import resolves against at call time.

    Returns (clock, sleep_calls, extra_lateness) — a test can inject
    EXTRA lateness on top of a specific sleep call's own duration via
    `extra_lateness["at_call"] = N; extra_lateness["seconds"] = X`
    (1-indexed call number) to simulate a genuinely delayed wake-up
    (GC pause, suspend, starved event loop) distinct from ordinary
    sleep-duration accounting."""
    clock = _Clock(start)

    class _FakeDateTime(_datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.value.astimezone(tz) if tz is not None else clock.value

    monkeypatch.setattr(_datetime_module, "datetime", _FakeDateTime)

    sleep_calls: list[float] = []
    extra_lateness = {"at_call": None, "seconds": 0.0}

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        advance_by = seconds
        if extra_lateness["at_call"] == len(sleep_calls):
            advance_by += extra_lateness["seconds"]
        clock.value = clock.value + timedelta(seconds=advance_by)
        if stop_after_calls is not None and len(sleep_calls) >= stop_after_calls:
            raise _StopLoop()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return clock, sleep_calls, extra_lateness


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
    next scheduled slot is" across multiple loop iterations without
    depending on real calendar arithmetic. Combined with the fake clock
    above, `now_utc` passed into this stub is itself fake/controlled, so
    real calendar semantics are not exercised by these tests — the
    dedicated `services/market_calendar.py` tests cover that separately
    and are unaffected by this file."""
    import services.market_calendar as mc
    calls = {"n": 0}

    def _fake_next_saturday_1200_utc(now_utc):
        idx = min(calls["n"], len(instants) - 1)
        calls["n"] += 1
        return instants[idx]

    monkeypatch.setattr(mc, "next_saturday_1200_utc", _fake_next_saturday_1200_utc)


# A real Saturday 12:00 UTC and the following one — arbitrary far-future
# dates so no test ever depends on when it happens to run.
SAT1 = datetime(2030, 8, 17, 12, 0, tzinfo=timezone.utc)
SAT2 = datetime(2030, 8, 24, 12, 0, tzinfo=timezone.utc)

# A "start" clock value comfortably before SAT1 — the loop's initial
# `now_utc = datetime.now(timezone.utc)` read, before its first sleep.
BEFORE_SAT1 = SAT1 - timedelta(days=2)


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
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=2)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        # First sleep = the 180s settle delay; second = the actual wait
        # for the weekly slot. The loop is stopped at that second sleep
        # BEFORE any admission has been attempted.
        assert len(sleeps) == 2
        assert admitted == []

    def test_all_enabled_combinations_admitted_under_the_same_scheduled_slot(self, monkeypatch):
        """Once the weekly slot sleep completes (on time, no lateness),
        every currently-enabled combination must be admitted
        sequentially, all sharing the EXACT SAME scheduled_slot identity
        — proving a batch has one durable weekly identity, not nine
        independent ones — with the correct per-horizon schedule_version."""
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100")
        # settle(1) + weekly-wait(1) + 7 combos * 5-min gap(7) = 9 sleeps
        # to get through exactly one full batch (1 short + 6 medium/long);
        # stop right after the last combination's own gap-sleep.
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=9)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 7  # 1 short (nifty100) + 6 medium/long
        assert all(call["scheduled_slot"] == SAT1 for call in admitted)
        assert all(call["trigger_type"] == "scheduler" for call in admitted)
        short_calls = [c for c in admitted if c["horizon"] == "short"]
        medium_long_calls = [c for c in admitted if c["horizon"] in ("medium", "long")]
        assert all(c["schedule_version"] == main_module.SHORT_WEEKLY_SCHEDULE_VERSION for c in short_calls)
        assert all(c["schedule_version"] == "v1" for c in medium_long_calls)
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
        clock = _Clock(BEFORE_SAT1)

        class _FakeDateTime(_datetime_module.datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.value.astimezone(tz) if tz is not None else clock.value

        monkeypatch.setattr(_datetime_module, "datetime", _FakeDateTime)

        async def _fake_sleep(seconds):
            call_log.append(f"sleep({seconds})")
            clock.value = clock.value + timedelta(seconds=seconds)
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
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=8)
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
        represents — including well past Saturday, into Sunday."""
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap,us")
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=11)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 9  # 3 short + 6 medium/long — the full batch ran to completion
        gap_sleeps = [s for s in sleeps if s == 5 * 60]
        assert len(gap_sleeps) == 9
        # By the time the batch finished, the clock has genuinely advanced
        # past Saturday (9 * 5 minutes = 45 minutes of gap sleeps alone,
        # on top of the initial wait) — proving the batch is allowed to
        # run the clock forward well beyond the nominal instant.
        assert clock.value > SAT1


@pytest.mark.unit
class TestSchedulerLoopReschedulesForNextWeek:
    def test_after_a_full_batch_the_loop_sleeps_for_the_next_weekly_slot_not_immediately_again(self, monkeypatch):
        """After finishing one week's batch, the loop must go back to
        sleeping until the NEXT scheduled slot — it must not immediately
        loop back and re-admit the same or a new batch with no wait."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # 1 settle + 1 weekly-wait + 6 combos*(1 gap-sleep) + 1 NEXT weekly-wait = 9
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=9)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1, SAT2])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6  # exactly one week's batch — the second week hasn't started admitting yet
        assert sleeps[-1] != 5 * 60
        assert sleeps[-1] > 5 * 60


@pytest.mark.unit
class TestBatchStartWindow:
    """2026-09-07 PR #87 timing correction — deterministic verification
    that a slot's IDENTITY being stable (fixed before sleeping) is not,
    by itself, what governs whether a batch is allowed to START. These
    tests use the coupled fake clock+sleep so "the wake-up was N minutes
    late" is a genuine, controlled fact the loop's own re-read of
    datetime.now() observes — not merely inferred."""

    def test_wake_up_exactly_at_the_slot_starts_the_batch(self, monkeypatch):
        """Zero lateness — the clock lands exactly on next_run when the
        weekly-wait sleep returns (the ordinary, expected case)."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=8)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6
        assert all(c["scheduled_slot"] == SAT1 for c in admitted)

    def test_spurious_early_wake_up_keeps_waiting_instead_of_starting_early(self, monkeypatch):
        """Defensive case: if the weekly-wait sleep somehow returns
        BEFORE next_run (not expected from asyncio.sleep's own
        lower-bound contract, but handled rather than assumed
        impossible), the loop must sleep the remainder and re-check —
        never admit early. Modeled by giving the 2nd sleep call NEGATIVE
        extra lateness, landing the clock 90s short of next_run; the
        loop's own internal early-wake retry then supplies exactly that
        remaining 90s via a further (mocked) sleep before proceeding."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # settle(1) + weekly-wait, 90s short(1) + internal 90s make-up
        # sleep(1) + 6 combos * gap(6) = 9 sleeps for one full batch.
        clock, sleeps, extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=9)
        extra["at_call"] = 2
        extra["seconds"] = -90  # lands 90s short of next_run
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6  # the batch still ran, just after the make-up sleep
        assert all(c["scheduled_slot"] == SAT1 for c in admitted)
        # The make-up sleep (3rd call) must be the ~90s remainder, not a
        # 5-minute inter-combination gap or the original multi-day wait.
        assert 0 < sleeps[2] <= 90.001

    def test_ordinary_jitter_within_the_grace_period_still_starts_the_batch(self, monkeypatch):
        """A wake-up 2 minutes late (well within the 5-minute
        BATCH_START_GRACE) must still start the batch, using the
        original scheduled_slot identity — proving ordinary jitter is
        tolerated, not treated as missed."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        clock, sleeps, extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=8)
        # The 2nd sleep call is the weekly-wait — add 2 minutes of extra
        # lateness on top of it, simulating a delayed wake-up.
        extra["at_call"] = 2
        extra["seconds"] = 2 * 60
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6  # batch started despite 2 min of lateness
        assert all(c["scheduled_slot"] == SAT1 for c in admitted)

    def test_exact_grace_boundary_still_starts_the_batch(self, monkeypatch):
        """Lateness of EXACTLY BATCH_START_GRACE (5 minutes, inclusive)
        must still start the batch — "beyond the window" means strictly
        greater than the grace period, not at-or-beyond."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        clock, sleeps, extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=8)
        extra["at_call"] = 2
        extra["seconds"] = main_module.BATCH_START_GRACE.total_seconds()  # exactly the grace boundary
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 6

    def test_immediately_beyond_the_grace_boundary_does_not_start_a_batch(self, monkeypatch, caplog):
        """Lateness of BATCH_START_GRACE plus one second must NOT start
        the batch — the slot is recorded as missed instead, and the loop
        moves on to compute the next future Saturday."""
        import api.main as main_module
        import logging
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # stop_after_calls=3, NOT 2: the extra lateness is applied ON the
        # 2nd sleep call (the weekly-wait) itself, so cutting the loop off
        # at exactly that call would raise _StopLoop from INSIDE the fake
        # sleep before the production code's own re-check/missed-slot log
        # logic ever ran — proving nothing about that logic. Letting the
        # loop reach its 3rd sleep (the wait for the NEXT week, entered
        # only after the missed-slot branch has logged and looped back)
        # is what actually exercises the behavior under test.
        clock, sleeps, extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=3)
        extra["at_call"] = 2
        extra["seconds"] = 5 * 60 + 1  # one second beyond the grace boundary
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        with caplog.at_level(logging.WARNING):
            asyncio.run(_run_loop_until_stopped(main_module))

        assert admitted == [], "no combination may be admitted once lateness exceeds the grace window"
        missed_logged = any(
            "missed the batch-start window" in r.message for r in caplog.records
        )
        assert missed_logged

    def test_delayed_sunday_wake_up_starts_no_new_batch(self, monkeypatch):
        """A wake-up so delayed the clock has advanced well into Sunday
        (far beyond any reasonable grace period) must never start a new
        batch — this is the exact "arbitrarily delayed wake-up" scenario
        the finding named."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # stop_after_calls=3 (not 2) — see the identical note in
        # test_immediately_beyond_the_grace_boundary_does_not_start_a_batch:
        # cutting off exactly at the call carrying the extra lateness would
        # raise _StopLoop from inside the fake sleep BEFORE the production
        # code's own re-check/missed-slot logic ever executes, proving
        # nothing. Reaching the 3rd sleep (entered only after that logic
        # has run and looped back) is what actually exercises it.
        clock, sleeps, extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=3)
        extra["at_call"] = 2
        extra["seconds"] = 26 * 3600  # over a day late — well into Sunday
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert admitted == []

    def test_a_batch_started_on_time_continues_into_sunday_unaffected(self, monkeypatch):
        """The start-window check applies ONLY at the moment the batch
        would begin — once genuinely started on time, the batch's own
        5-minute inter-combination gaps carrying the clock past Saturday
        into Sunday must NOT retroactively abort or reject any later
        combination. Uses a large enabled set so the batch's own total
        duration (9 * 5 min = 45 min, still tiny compared to a real
        multi-hour batch, but sufficient to prove the point deterministically)
        does not itself trigger any lateness rejection."""
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap,us")
        clock, sleeps, _extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=11)
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert len(admitted) == 9  # the full batch, uninterrupted
        assert all(c["scheduled_slot"] == SAT1 for c in admitted)

    def test_a_missed_slot_waits_for_the_next_future_saturday_not_an_immediate_retry(self, monkeypatch):
        """After a slot is recorded as missed (delayed wake-up beyond
        grace), the loop must compute and wait for the NEXT future
        Saturday — not retry the same missed slot, and not immediately
        admit anything."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # 1 settle + 1 weekly-wait (misses) + 1 NEXT weekly-wait = 3 sleeps
        # before any admission could possibly happen for week 2.
        clock, sleeps, extra = _install_clock_and_sleep(monkeypatch, BEFORE_SAT1, stop_after_calls=3)
        extra["at_call"] = 2
        extra["seconds"] = 26 * 3600  # missed week 1 entirely
        admitted = _install_recording_admission(monkeypatch)
        _install_fixed_next_saturday(monkeypatch, [SAT1, SAT2])

        asyncio.run(_run_loop_until_stopped(main_module))

        assert admitted == []  # neither week 1 (missed) nor week 2 (not yet due) admitted anything
        # The 3rd sleep is the wait for week 2 — a long wait, not a 5-min gap.
        assert sleeps[-1] != 5 * 60
