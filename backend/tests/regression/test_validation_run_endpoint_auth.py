"""
V-SEC1 — POST /api/validation/run authentication.

Root cause (forensic audit, read-only, prior session): the manual HTTP
trigger for walk-forward validation was reachable with zero application-
level authentication while the backend is publicly reachable — any caller
could POST /api/validation/run and consume provider/CPU/Railway resources
at will, or inject an off-schedule run.

Design: reuses the project's already-established shared admin-secret
convention — same PICKS_SECRET env var, same X-Secret header, same
fail-closed comparison already proven for /api/picks/generate (weaker
`!=`) and hardened further for /api/predictions/debug/state (Release 14B:
both sides must be non-empty after stripping before any comparison is
attempted). This router reads its own module-level secret independently
(not imported from picks.py/predictions.py) — same single-file isolation
rationale predictions.py's _DEBUG_SECRET comment already documents — and
additionally uses hmac.compare_digest for constant-time comparison, which
neither existing convention does.

No PICKS_SECRET *value* appears anywhere in this file — only the fixed,
non-production string "test-validation-secret" used purely as a
deterministic test fixture value, monkeypatched directly onto the module
attribute (never read from the real environment).

Every test mocks run_validation — no test contacts a real provider, runs a
real validation, or writes production data.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.routers.validation as validation_router
import services.validation_engine as ve


TEST_SECRET = "test-validation-secret"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Deterministic secret for these tests; validation job-slot state is
    reset before and after so no test leaks 'running' into another."""
    monkeypatch.setattr(validation_router, "_VALIDATION_RUN_SECRET", TEST_SECRET)
    with ve._status_lock:
        saved = dict(ve._run_status)
        ve._run_status.clear()
        ve._run_status.update({"running": False, "progress": 0, "total": 0, "started_at": None, "log": []})
    yield
    with ve._status_lock:
        ve._run_status.clear()
        ve._run_status.update(saved)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture
def mock_run_validation(monkeypatch):
    """Replaces the real backtest with a no-op that never touches a
    provider, never runs real signal computation, and never writes to a
    database — used to prove call/no-call behavior, not to exercise
    run_validation() itself (that's covered elsewhere)."""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return {"fake": True}

    monkeypatch.setattr(validation_router, "run_validation", _fake, raising=False)
    # trigger_validation imports run_validation as a local name inside the
    # function body (`from services.validation_engine import run_validation, ...`),
    # so patch it at the source module too — whichever the implementation
    # resolves to, this fixture must intercept it.
    monkeypatch.setattr(ve, "run_validation", _fake)
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Authentication matrix — router-level (real TestClient, real HTTP call)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestRunEndpointAuthentication:

    def test_missing_credential_rejected(self, client, mock_run_validation):
        resp = client.post("/api/validation/run?horizon=short&universe=us")
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_blank_credential_rejected(self, client, mock_run_validation):
        resp = client.post("/api/validation/run?horizon=short&universe=us", headers={"X-Secret": ""})
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_incorrect_credential_rejected(self, client, mock_run_validation):
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": "totally-wrong-secret"},
        )
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_malformed_credential_whitespace_only_rejected(self, client, mock_run_validation):
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": "   "},
        )
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_credential_with_trailing_newline_injection_rejected(self, client, mock_run_validation):
        # Not a valid match even though it "contains" the secret — must be
        # an exact match after stripping, not a substring/prefix match.
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": f"{TEST_SECRET}\nextra"},
        )
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_correct_credential_accepted(self, client, mock_run_validation):
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": TEST_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "started"

    def test_correct_credential_with_incidental_whitespace_still_matches(self, client, mock_run_validation):
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": f"  {TEST_SECRET}  "},
        )
        assert resp.status_code == 200

    def test_401_response_has_no_sensitive_detail(self, client, mock_run_validation):
        resp = client.post("/api/validation/run?horizon=short&universe=us")
        assert resp.status_code == 401
        body = resp.json()
        detail = str(body.get("detail", ""))
        assert TEST_SECRET not in detail
        assert "PICKS_SECRET" not in detail
        assert "already_running" not in detail

    def test_credential_never_accepted_via_query_parameter(self, client, mock_run_validation):
        # A secret passed as a query param instead of a header must not
        # authenticate — headers only, never leaked into URLs/access logs.
        resp = client.post(f"/api/validation/run?horizon=short&universe=us&x_secret={TEST_SECRET}")
        assert resp.status_code == 401
        assert mock_run_validation == []


# ─────────────────────────────────────────────────────────────────────────────
# Fail-closed when the required secret is absent from configuration
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestFailsClosedWhenSecretUnconfigured:

    def test_unconfigured_secret_rejects_even_a_blank_header_match(self, client, mock_run_validation, monkeypatch):
        # The exact "blank secret + blank header" trap that /api/picks/
        # generate's plain `!=` comparison would let through — this
        # endpoint must not repeat that weaker pattern.
        monkeypatch.setattr(validation_router, "_VALIDATION_RUN_SECRET", "")
        resp = client.post("/api/validation/run?horizon=short&universe=us", headers={"X-Secret": ""})
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_unconfigured_secret_rejects_any_header(self, client, mock_run_validation, monkeypatch):
        monkeypatch.setattr(validation_router, "_VALIDATION_RUN_SECRET", "")
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": "anything-at-all"},
        )
        assert resp.status_code == 401
        assert mock_run_validation == []

    def test_whitespace_only_configured_secret_rejects_everything(self, client, mock_run_validation, monkeypatch):
        monkeypatch.setattr(validation_router, "_VALIDATION_RUN_SECRET", "   ")
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": "   "},
        )
        assert resp.status_code == 401
        assert mock_run_validation == []


# ─────────────────────────────────────────────────────────────────────────────
# Rejected calls never claim the job slot / never invoke run_validation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestRejectedCallsConsumeNoResources:

    def test_rejected_call_does_not_claim_job_slot(self, client, mock_run_validation):
        client.post("/api/validation/run?horizon=short&universe=us")  # no header
        status = ve.get_run_status()
        assert status["running"] is False
        assert status.get("job") is None

    def test_rejected_call_never_invokes_run_validation(self, client, mock_run_validation):
        for headers in (
            {},
            {"X-Secret": ""},
            {"X-Secret": "wrong"},
        ):
            client.post("/api/validation/run?horizon=short&universe=us", headers=headers)
        assert mock_run_validation == []

    def test_accepted_call_invokes_run_validation_exactly_once(self, client, mock_run_validation):
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": TEST_SECRET},
        )
        assert resp.status_code == 200
        assert len(mock_run_validation) == 1
        assert mock_run_validation[0]["horizon"] == "short"
        assert mock_run_validation[0]["universe"] == "us"


# ─────────────────────────────────────────────────────────────────────────────
# Existing concurrency/claim protection unchanged
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestConcurrencyProtectionUnchangedByAuth:

    def test_authenticated_call_while_another_run_is_active_still_reports_already_running(
        self, client, mock_run_validation
    ):
        with ve._status_lock:
            ve._run_status.update({
                "running": True, "progress": 1, "total": 200,
                "started_at": "2026-08-11T00:00:00+00:00", "log": [],
                "job": ve._new_job_identity(
                    market="US", universe_id="us", horizon="short",
                    benchmark="^GSPC", total=200, trigger_type="api",
                ),
            })
        resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": TEST_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "already_running"
        assert mock_run_validation == []

    def test_unauthenticated_call_while_another_run_is_active_is_still_401_not_already_running(
        self, client, mock_run_validation
    ):
        # Auth must be checked BEFORE the concurrency check — an
        # unauthenticated caller must never learn that a run is active.
        with ve._status_lock:
            ve._run_status.update({
                "running": True, "progress": 1, "total": 200,
                "started_at": "2026-08-11T00:00:00+00:00", "log": [],
                "job": ve._new_job_identity(
                    market="US", universe_id="us", horizon="short",
                    benchmark="^GSPC", total=200, trigger_type="api",
                ),
            })
        resp = client.post("/api/validation/run?horizon=short&universe=us")
        assert resp.status_code == 401
        assert "already_running" not in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# Internal scheduled execution is unaffected — proven from source, not just
# behaviorally, since the scheduler never goes through this HTTP route.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestInternalSchedulerUnaffected:

    def test_scheduler_loop_calls_run_validation_directly_not_via_http(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "run_validation(" in src
        assert "/api/validation/run" not in src
        assert "trigger_validation" not in src
        assert "X-Secret" not in src
        assert "_VALIDATION_RUN_SECRET" not in src

    def test_no_internal_caller_in_main_goes_through_the_http_route_or_its_secret(self):
        # _catchup_validation is a local closure defined inside the FastAPI
        # lifespan function (not a module-level attribute), so it can't be
        # isolated via inspect.getsource(main_module._catchup_validation).
        # Scanning the whole module source is an equally valid — and more
        # complete — proof: if neither the literal route path nor the
        # secret-check machinery appears ANYWHERE in main.py, then no
        # internal scheduler/catch-up path (however it's structured) can be
        # going through the newly-protected HTTP route.
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        assert "/api/validation/run" not in src
        assert "trigger_validation" not in src
        assert "X-Secret" not in src
        assert "_VALIDATION_RUN_SECRET" not in src
        # Sanity: both internal triggers ARE present and call run_validation
        # directly, proving this isn't a false pass from a stripped-down file.
        assert "_validation_schedule_loop" in src
        assert "_catchup_validation" in src
        assert src.count("run_validation(") >= 3  # scheduler loop, long-horizon block, catch-up

    def test_run_validation_itself_has_no_new_auth_requirement(self):
        # The core function the scheduler calls must remain callable with
        # zero knowledge of HTTP/secrets — auth lives only at the router.
        import inspect
        sig = inspect.signature(ve.run_validation)
        assert "x_secret" not in sig.parameters
        assert "secret" not in sig.parameters


# ─────────────────────────────────────────────────────────────────────────────
# No secret material anywhere in source, logs, or this test file itself
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestNoSecretDisclosure:

    def test_endpoint_source_never_prints_or_logs_the_secret_value(self):
        import inspect
        src = inspect.getsource(validation_router)
        # The variable name is fine to reference; its value must never be
        # interpolated into a log/print/response statement.
        for banned in ("log.info(_VALIDATION_RUN_SECRET", "print(_VALIDATION_RUN_SECRET",
                       "f\"{_VALIDATION_RUN_SECRET", "f'{_VALIDATION_RUN_SECRET"):
            assert banned not in src

    def test_env_var_name_is_picks_secret_reused_not_a_new_convention(self):
        import inspect
        src = inspect.getsource(validation_router)
        assert 'os.getenv("PICKS_SECRET"' in src
