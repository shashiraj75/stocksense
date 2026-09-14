"""
Daily Picks Cross-Run Memory Ratchet fix — subprocess isolation.

Real Railway production evidence (see Documentation/Engineering-Handbook/
Architecture/Daily-Picks-Cross-Run-Memory-Ratchet-Investigation-and-
Subprocess-Isolation-Design.md): the cross-run memory baseline keeps
climbing day-over-day despite two prior in-process remediations (Product
Integrity #023's raw[horizon] release; the 2026-08-24 run-end
release_memory() fix), pointing to glibc allocator arena fragmentation —
not a Python-level leak either fix's gc.collect()/malloc_trim() mechanism
can reclaim. This runs Daily Picks generation in a freshly spawned child
process instead, so the OS's own guaranteed-100%-reclaim-on-exit closes
the gap those two fixes structurally cannot.

Because a spawned ("spawn", never "fork") child re-imports this module
fresh, any monkeypatch applied in the parent test process cannot reach code
that runs *inside* an actually-spawned child — patches only affect the
process that calls them. These tests therefore split into three groups,
each testing what it actually can:

1. generate_picks()'s dispatch logic (the kill switch) — fully testable by
   patching both candidate functions and asserting exactly one is called.
2. _daily_picks_subprocess_target()'s queue-put contract — testable by
   calling it directly (no real subprocess involved) with
   _generate_picks_inner patched, exactly like calling any other function.
3. _generate_picks_isolated()'s outcome-handling logic (the polling loop,
   success/error/killed/timeout branches) — testable via a fully
   controlled fake multiprocessing context, so the *logic* is exercised
   for real without needing a real OS process for every case.

A final, separate smoke test spawns one genuine, trivial real subprocess
(unrelated to Daily Picks internals) purely to confirm "spawn" actually
works in this environment — a real, disclosed environment-capability
check, not a mock.
"""
import multiprocessing
import time
from unittest.mock import patch

import services.daily_picks as dp


# ── 1. generate_picks()'s kill-switch dispatch ──────────────────────────────

def test_subprocess_isolation_disabled_by_default_uses_direct_path():
    fake_payload = {"generated_at": "2026-09-14T00:00:00Z", "picks": {}}

    with patch.dict("os.environ", {}, clear=True), \
         patch.object(dp, "_generate_picks_inner",
                      return_value=(fake_payload, None)) as mock_inner, \
         patch.object(dp, "_generate_picks_isolated") as mock_isolated, \
         patch("threading.Thread"):
        dp.generate_picks("IN", job_id=None)

    mock_inner.assert_called_once()
    mock_isolated.assert_not_called()


def test_subprocess_isolation_enabled_uses_isolated_path():
    fake_payload = {"generated_at": "2026-09-14T00:00:00Z", "picks": {}}

    with patch.dict("os.environ", {"DAILY_PICKS_SUBPROCESS_ISOLATION_ENABLED": "1"}, clear=True), \
         patch.object(dp, "_generate_picks_inner") as mock_inner, \
         patch.object(dp, "_generate_picks_isolated",
                      return_value=(fake_payload, None)) as mock_isolated, \
         patch("threading.Thread"):
        dp.generate_picks("US", job_id=None)

    mock_isolated.assert_called_once()
    mock_inner.assert_not_called()


def test_subprocess_isolation_flag_rejects_non_1_values():
    """Fail-safe: only the literal "1" enables it — mirrors every other
    kill switch in this codebase, never a truthy-string trap."""
    for value in ("true", "yes", "on", "TRUE", "", "0", "2"):
        with patch.dict("os.environ", {"DAILY_PICKS_SUBPROCESS_ISOLATION_ENABLED": value}):
            assert dp._subprocess_isolation_enabled() is (value == "1")


# ── 2. _daily_picks_subprocess_target()'s queue-put contract ───────────────

class _FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def test_subprocess_target_puts_ok_tuple_on_success():
    fake_payload = {"generated_at": "2026-09-14T00:00:00Z", "picks": {}}
    q = _FakeQueue()

    with patch.object(dp, "_generate_picks_inner",
                       return_value=(fake_payload, None)):
        dp._daily_picks_subprocess_target("IN", "job-1", q)

    assert len(q.items) == 1
    status, payload, persisted_at = q.items[0]
    assert status == "ok"
    assert payload == fake_payload
    assert persisted_at is None


def test_subprocess_target_puts_error_tuple_on_exception():
    q = _FakeQueue()

    with patch.object(dp, "_generate_picks_inner",
                       side_effect=RuntimeError("provider stall")):
        dp._daily_picks_subprocess_target("US", "job-2", q)

    assert len(q.items) == 1
    status, message, tb_text = q.items[0]
    assert status == "error"
    assert "provider stall" in message
    assert "RuntimeError" in tb_text


def test_subprocess_target_never_puts_a_raw_exception_object():
    """Only str/traceback-text cross the boundary — an exception object
    itself is not guaranteed picklable, and this must never be how a real
    failure silently fails to report."""
    q = _FakeQueue()

    class _Unpicklable(RuntimeError):
        def __reduce__(self):
            raise TypeError("not picklable, deliberately, for this test")

    with patch.object(dp, "_generate_picks_inner",
                       side_effect=_Unpicklable("boom")):
        dp._daily_picks_subprocess_target("IN", None, q)

    status, message, tb_text = q.items[0]
    assert status == "error"
    assert isinstance(message, str)
    assert isinstance(tb_text, str)


# ── 3. _generate_picks_isolated()'s outcome-handling logic ─────────────────

class _FakeProcessQueue:
    def __init__(self):
        self._items = []

    def empty(self):
        return len(self._items) == 0

    def get(self):
        return self._items.pop(0)

    def _push(self, item):
        self._items.append(item)


class _FakeProcess:
    """Every behavior a test needs is set explicitly by the test itself —
    no implicit "runs real code" path, since this fake never touches the
    OS. `on_start` lets a test simulate the child pushing its result onto
    the shared fake queue at start() time, mimicking a fast real child."""

    def __init__(self, target, args, daemon, *, alive_for_polls=0, exitcode=None, on_start=None):
        self._target, self._args, self.daemon = target, args, daemon
        self._alive_for_polls = alive_for_polls
        self.exitcode = exitcode
        self._on_start = on_start
        self.terminated = False
        self.started = False

    def start(self):
        self.started = True
        if self._on_start:
            self._on_start()

    def is_alive(self):
        if self._alive_for_polls is None:
            return True
        if self._alive_for_polls > 0:
            self._alive_for_polls -= 1
            return True
        return False

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.terminated = True


def _fake_context(process_factory):
    ctx = type("FakeCtx", (), {})()
    ctx.Queue = _FakeProcessQueue
    ctx.Process = process_factory
    return ctx


def test_generate_picks_isolated_returns_payload_on_success():
    fake_payload = {"generated_at": "2026-09-14T00:00:00Z", "picks": {}}

    def factory(target, args, daemon):
        q = args[2]

        def push_result():
            q._push(("ok", fake_payload, None))
        return _FakeProcess(target, args, daemon, alive_for_polls=0, exitcode=0, on_start=push_result)

    with patch.object(multiprocessing, "get_context", return_value=_fake_context(factory)):
        payload, persisted_at = dp._generate_picks_isolated("IN", job_id="job-3")

    assert payload == fake_payload
    assert persisted_at is None


def test_generate_picks_isolated_raises_on_child_error():
    def factory(target, args, daemon):
        q = args[2]

        def push_result():
            q._push(("error", "provider stall", "Traceback ...RuntimeError: provider stall"))
        return _FakeProcess(target, args, daemon, alive_for_polls=0, exitcode=1, on_start=push_result)

    with patch.object(multiprocessing, "get_context", return_value=_fake_context(factory)):
        try:
            dp._generate_picks_isolated("US", job_id="job-4")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "provider stall" in str(e)


def test_generate_picks_isolated_raises_distinctly_when_child_dies_without_result():
    """The one new failure mode this isolation introduces: an OS-level
    kill (e.g. OOM) never runs the child's own exception handler, so
    nothing is ever pushed to the queue. Must be detected via the
    liveness poll, not by waiting out the full timeout."""
    def factory(target, args, daemon):
        # Never alive (already exited by the time is_alive() is checked),
        # exitcode set as a real OS-killed process would show (negative =
        # killed by signal on POSIX).
        return _FakeProcess(target, args, daemon, alive_for_polls=0, exitcode=-9)

    with patch.object(multiprocessing, "get_context", return_value=_fake_context(factory)):
        try:
            dp._generate_picks_isolated("IN", job_id="job-5")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "OS-terminated" in str(e) or "exit code -9" in str(e)


def test_generate_picks_isolated_terminates_and_raises_on_timeout():
    """A process that never dies and never reports a result must not hang
    generate_picks() forever — bounded by _SUBPROCESS_ISOLATION_TIMEOUT_S,
    and the stuck child must be explicitly terminated, not abandoned."""
    created = {}

    def factory(target, args, daemon):
        proc = _FakeProcess(target, args, daemon, alive_for_polls=None, exitcode=None)
        created["proc"] = proc
        return proc

    with patch.object(multiprocessing, "get_context", return_value=_fake_context(factory)), \
         patch.object(dp, "_SUBPROCESS_ISOLATION_TIMEOUT_S", 0.05), \
         patch.object(time, "sleep"):
        try:
            dp._generate_picks_isolated("US", job_id="job-6")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "timed out" in str(e)

    assert created["proc"].terminated is True


# ── 4. Real environment-capability smoke test ───────────────────────────────

def _trivial_spawn_target(q):
    q.put("hello from a real spawned subprocess")


def test_spawn_context_actually_works_in_this_environment():
    """Not a mock — a genuine multiprocessing.get_context("spawn") process,
    confirming this sandbox/CI environment actually permits process
    spawning (some restricted sandboxes disallow it entirely, which would
    make the whole fix a no-op in production despite every test above
    passing). If this test ever fails, that is the real, actionable
    signal — not a false negative to work around."""
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_trivial_spawn_target, args=(q,))
    proc.start()
    try:
        result = q.get(timeout=30)
    finally:
        proc.join(timeout=10)
    assert result == "hello from a real spawned subprocess"
