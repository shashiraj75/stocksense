"""
Structured logging setup for the Selection Engine.

SEAR-001 found print()-based output dominating several core modules
(daily_picks.py: 31 prints/0 logging calls, weight_adapter.py: 11/0,
meta_model.py: 7/0) with no log levels, no consistent format, and no way
to filter signal from noise in production — debugging depended on manually
reading Railway's raw stdout stream.

`configure_logging()` is called once at process startup (api/main.py).
Every module should use `logging.getLogger(__name__)` (the stdlib pattern
already correctly used in prediction_engine.py and market_data.py) rather
than print() — this module standardizes the format/level for all of them.
"""

import logging
import os
import sys


_CONFIGURED = False


def configure_logging() -> None:
    """Idempotent — safe to call multiple times (e.g. in tests)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    # Tag our own handler so the dedup check below can't be fooled by an
    # unrelated StreamHandler subclass another library (or a test runner's
    # log-capture plugin) has already attached to the root logger.
    handler._stocksense_structured = True

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if uvicorn's --reload re-imports this module.
    if not any(getattr(h, "_stocksense_structured", False) for h in root.handlers):
        root.addHandler(handler)

    # Quiet noisy third-party libraries unless explicitly debugging.
    for noisy in ("urllib3", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — equivalent to logging.getLogger(name) but
    guarantees configure_logging() has run first (useful in scripts/tests
    that import a service module directly without going through main.py)."""
    configure_logging()
    return logging.getLogger(name)
