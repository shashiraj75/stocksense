"""
Prepared one-shot validation worker (validation_worker.py) — NOT wired
into any live trigger path (see the file's own module docstring and
Documentation/Engineering-Handbook/Operations/
Validation-Memory-Architecture-Review.md). These tests verify the
script's own contract in isolation: it cannot activate a disabled
market/universe, it delegates entirely to the existing
execute_admitted_validation() (no new validation logic), and its exit
code correctly reflects success/failure/rejection.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

import validation_worker


def _run_main(argv, exec_result):
    with patch.object(sys, "argv", ["validation_worker.py"] + argv), \
         patch("services.validation_engine.execute_admitted_validation",
               return_value=exec_result) as mock_exec:
        code = validation_worker.main()
    return code, mock_exec


def test_successful_run_exits_zero():
    code, mock_exec = _run_main(
        ["--horizon", "medium", "--universe", "nifty100"],
        {"ok": True, "run_id": 123},
    )
    assert code == 0
    mock_exec.assert_called_once()
    _, kwargs = mock_exec.call_args
    assert kwargs["horizon"] == "medium"
    assert kwargs["universe"] == "nifty100"


def test_rejected_admission_exits_nonzero_by_default():
    code, _ = _run_main(
        ["--horizon", "long", "--universe", "us"],
        {"ok": False, "reason": "lease_already_held"},
    )
    assert code == 1


def test_rejected_admission_exits_zero_with_allow_rejected_flag():
    code, _ = _run_main(
        ["--horizon", "long", "--universe", "us", "--allow-rejected"],
        {"ok": False, "reason": "lease_already_held"},
    )
    assert code == 0


def test_default_trigger_type_is_worker():
    _, mock_exec = _run_main(
        ["--horizon", "medium", "--universe", "midcap"],
        {"ok": True, "run_id": 1},
    )
    _, kwargs = mock_exec.call_args
    assert kwargs["trigger_type"] == "worker"


def test_custom_trigger_type_is_passed_through():
    _, mock_exec = _run_main(
        ["--horizon", "medium", "--universe", "midcap", "--trigger-type", "migration-test"],
        {"ok": True, "run_id": 1},
    )
    _, kwargs = mock_exec.call_args
    assert kwargs["trigger_type"] == "migration-test"


@pytest.mark.parametrize("bad_horizon", ["short", "SHORT", "Medium", "all", ""])
def test_disabled_or_unrecognized_horizon_is_rejected_at_argument_parsing(bad_horizon):
    """--horizon is restricted to exactly (medium, long) — argparse
    `choices=` rejects anything else, including "short" (this worker is
    deliberately out of scope for short-horizon validation, which has its
    own separate, env-gated auto-schedule) before any admission or
    execution logic ever runs."""
    with patch.object(sys, "argv", [
        "validation_worker.py", "--horizon", bad_horizon, "--universe", "nifty100",
    ]), patch("services.validation_engine.execute_admitted_validation") as mock_exec:
        with pytest.raises(SystemExit) as exc_info:
            validation_worker.main()
        assert exc_info.value.code != 0
        mock_exec.assert_not_called()


@pytest.mark.parametrize("bad_universe", ["nasdaq100", "smallcap", "ALL", ""])
def test_unrecognized_universe_is_rejected_at_argument_parsing(bad_universe):
    """--universe is restricted to exactly the same three universes the
    live scheduler already uses (nifty100, midcap, us) — no market/
    universe outside the currently-enabled set can ever be requested,
    regardless of what's typed on the command line."""
    with patch.object(sys, "argv", [
        "validation_worker.py", "--horizon", "medium", "--universe", bad_universe,
    ]), patch("services.validation_engine.execute_admitted_validation") as mock_exec:
        with pytest.raises(SystemExit) as exc_info:
            validation_worker.main()
        assert exc_info.value.code != 0
        mock_exec.assert_not_called()


def test_exception_from_execute_admitted_validation_propagates_nonzero():
    """An unhandled exception must not be swallowed into a false success
    exit code — the process manager (Railway) needs a genuine non-zero
    exit to know the run failed."""
    with patch.object(sys, "argv", [
        "validation_worker.py", "--horizon", "medium", "--universe", "nifty100",
    ]), patch("services.validation_engine.execute_admitted_validation",
              side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            validation_worker.main()
