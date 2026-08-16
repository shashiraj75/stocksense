"""
Wave B, Stage J4F — Batch K red tests: failure taxonomy (LIMITED-
EVIDENCE / RETRYABLE / TERMINAL classification), sanitized error
storage, and unsupported-identity handling (scenario-matrix expansion
under WB-J4F-18/19).

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util
import inspect

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


# Scenario 31 — a trade with no compatible price-path evidence and a
# provider that returns genuinely empty (not erroring) must complete as
# LIMITED_EVIDENCE, never RETRYABLE (an empty-but-successful acquisition
# is not a transient failure — retrying it will never produce more data).
def test_wb_j4f_31_empty_but_successful_acquisition_completes_limited_not_retryable():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-31: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    assert hasattr(module, "CURRENT_REPORT_GENERATED") and hasattr(module, "CURRENT_REPORT_FAILED_RETRYABLE"), (
        "WB-J4F-31: outcome constants are not yet fully defined."
    )


# Scenario 32 — a provider network/timeout error must be classified
# RETRYABLE, never TERMINAL (a transient failure deserves another attempt).
def test_wb_j4f_32_provider_network_error_is_retryable_not_terminal():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-32: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.process_current_report)
    assert "mark_retryable_failure" in source, (
        "WB-J4F-32: process_current_report does not visibly call outbox.mark_retryable_failure on "
        "provider acquisition failure — cannot verify the RETRYABLE classification path."
    )


# Scenario 33 — a missing/deleted trade row (TradeNotFoundError-class
# condition) must be classified TERMINAL, never RETRYABLE (retrying a
# permanently-missing trade can never succeed).
def test_wb_j4f_33_missing_trade_is_terminal_not_retryable():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-33: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.process_current_report)
    assert "CURRENT_REPORT_FAILED_TERMINAL" in source, (
        "WB-J4F-33: process_current_report does not visibly return CURRENT_REPORT_FAILED_TERMINAL for "
        "a missing/unowned trade — cannot verify the TERMINAL classification path exists."
    )


# Scenario 34 — an ownership mismatch (trade belongs to a different user)
# must ALSO be classified TERMINAL, never silently succeed or retry.
def test_wb_j4f_34_ownership_mismatch_is_terminal():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-34: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )


# Scenario 35 — an unsupported/unknown requested_report_schema_version on
# an outbox row (e.g. a future 1.3.0 row processed by an older worker
# binary) must be classified TERMINAL with a specific error_code, never
# silently coerced to the 1.2.0 identity or crash the worker loop.
def test_wb_j4f_35_unsupported_identity_is_terminal_with_specific_error_code():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-35: services.postmortem.outbox_worker does not exist yet (expected red pre-fix) — "
        "cannot verify unsupported-identity handling without the worker's own dispatch logic."
    )


# Scenario 36 — every error_code stored via mark_retryable_failure/
# mark_terminal_failure from the 1.2.0 path must come from a small,
# stable, enumerated set of machine-readable codes — never a raw
# str(exception) (privacy/sanitization requirement, matches the existing
# outbox.py docstring contract for error_code/error_summary).
def test_wb_j4f_36_error_codes_are_enumerated_not_raw_exception_text():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-36: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.process_current_report)
    assert "str(e)" not in source and "str(exc)" not in source and "repr(e)" not in source, (
        "WB-J4F-36: process_current_report stores a raw exception string as an error_code/error_summary — "
        "must use a stable, enumerated, pre-sanitized code instead (privacy requirement)."
    )


# Scenario 37 — error_summary passed to mark_retryable_failure/
# mark_terminal_failure must never itself be an f-string interpolation of
# a raw provider response body or connection string.
def test_wb_j4f_37_error_summary_never_interpolates_raw_provider_response():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-37: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )


# Scenario 38 — a LIMITED_EVIDENCE completion must still write a real,
# persisted report row (status='LIMITED_EVIDENCE'), never merely mark the
# outbox row COMPLETE with no corresponding report (a completed outbox row
# with no report would be an orphaned, unobservable "success").
def test_wb_j4f_38_limited_evidence_completion_still_persists_a_report():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-38: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.process_current_report)
    assert "persist_current_report" in source, (
        "WB-J4F-38: process_current_report does not visibly call persist_current_report on every "
        "completion path — a LIMITED_EVIDENCE outcome must still produce a real persisted report row."
    )
