"""
Unit tests for services/logging_config.py.

These tests mutate the root logger's global state (handlers, level), since
that's exactly what configure_logging() does. `_reset_logging_state` isolates
each test from the others and from any logging setup done at module-import
time elsewhere in the suite (e.g. api/main.py calling configure_logging()
on import), so a test running first/last doesn't see different behavior.
"""

import io
import logging

import pytest

from services import logging_config


@pytest.fixture
def _reset_logging_state():
    """Snapshot root logger state, force logging_config back to its
    unconfigured state, run the test, then restore everything — so this
    file's tests can't pollute (or be polluted by) the rest of the suite
    or the real `configure_logging()` call api/main.py makes at import."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_configured = logging_config._CONFIGURED

    root.handlers = []
    logging_config._CONFIGURED = False

    yield

    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging_config._CONFIGURED = saved_configured


class TestConfigureLoggingIdempotent:
    @pytest.mark.unit
    def test_second_call_is_a_no_op(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging_config.configure_logging()
        root = logging.getLogger()
        handler_count_after_first = len(root.handlers)
        assert logging_config._CONFIGURED is True

        # Second call must not add another handler or change anything.
        logging_config.configure_logging()
        assert len(root.handlers) == handler_count_after_first

    @pytest.mark.unit
    def test_flag_is_set_after_first_call_only(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert logging_config._CONFIGURED is False
        logging_config.configure_logging()
        assert logging_config._CONFIGURED is True


class TestLogLevelEnvVarRespected:
    @pytest.mark.unit
    def test_defaults_to_info_when_unset(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging_config.configure_logging()
        assert logging.getLogger().level == logging.INFO

    @pytest.mark.unit
    def test_debug_level_applied(self, _reset_logging_state, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        logging_config.configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    @pytest.mark.unit
    def test_warning_level_applied(self, _reset_logging_state, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        logging_config.configure_logging()
        assert logging.getLogger().level == logging.WARNING

    @pytest.mark.unit
    def test_lowercase_env_value_normalized(self, _reset_logging_state, monkeypatch):
        """os.environ values are user/deploy-config supplied — must not be
        case-sensitive (Railway/Render env var UIs don't enforce casing)."""
        monkeypatch.setenv("LOG_LEVEL", "debug")
        logging_config.configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    @pytest.mark.unit
    def test_invalid_level_falls_back_to_info(self, _reset_logging_state, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "NOT_A_REAL_LEVEL")
        logging_config.configure_logging()
        assert logging.getLogger().level == logging.INFO


class TestNoDuplicateHandlersOnRepeatedCalls:
    @pytest.mark.unit
    def test_ten_repeated_calls_leave_exactly_one_stream_handler(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        for _ in range(10):
            logging_config.configure_logging()
        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if getattr(h, "_stocksense_structured", False)]
        assert len(stream_handlers) == 1

    @pytest.mark.unit
    def test_handler_dedup_check_also_holds_if_configured_flag_is_bypassed(self, _reset_logging_state, monkeypatch):
        """Exercises the `if not any(getattr(h, "_stocksense_structured", False) ...)` guard
        directly (not just the _CONFIGURED early-return) — this is the guard
        that actually matters under uvicorn --reload, where a module can be
        re-imported (re-running module-level code) without the process
        restarting, so _CONFIGURED could plausibly reset to False while a
        handler from the previous import is still attached to the root logger."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging_config.configure_logging()
        assert logging_config._CONFIGURED is True

        # Simulate the reload scenario: flag resets, old handler stays attached.
        logging_config._CONFIGURED = False
        logging_config.configure_logging()

        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if getattr(h, "_stocksense_structured", False)]
        assert len(stream_handlers) == 1


class TestLogOutputFormatStable:
    """Locks in the production debugging format: timestamp, level, logger
    name, message — separated by ' — '. If this format changes, it should
    be an intentional, reviewed change to this test, not an accident."""

    @pytest.mark.unit
    def test_formatter_pattern_is_stable(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging_config.configure_logging()
        root = logging.getLogger()
        handler = next(h for h in root.handlers if getattr(h, "_stocksense_structured", False))
        assert handler.formatter._fmt == "%(asctime)s %(levelname)s %(name)s — %(message)s"
        assert handler.formatter.datefmt == "%Y-%m-%d %H:%M:%S"

    @pytest.mark.unit
    def test_emitted_record_contains_level_name_and_message(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging_config.configure_logging()

        # Swap in an in-memory stream so we can assert on the rendered line
        # without depending on capturing real stdout.
        root = logging.getLogger()
        stream_handler = next(h for h in root.handlers if getattr(h, "_stocksense_structured", False))
        buffer = io.StringIO()
        stream_handler.stream = buffer

        log = logging.getLogger("services.test_module")
        log.warning("[picks] something worth a human's attention")

        output = buffer.getvalue()
        assert "WARNING" in output
        assert "services.test_module" in output
        assert "[picks] something worth a human's attention" in output
        assert "—" in output  # the separator production debugging greps on


class TestGetLoggerConvenienceWrapper:
    @pytest.mark.unit
    def test_get_logger_configures_and_returns_named_logger(self, _reset_logging_state, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert logging_config._CONFIGURED is False
        log = logging_config.get_logger("services.some_module")
        assert logging_config._CONFIGURED is True
        assert log.name == "services.some_module"
