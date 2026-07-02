"""
Shared safe-error-response helper (Product Integrity — UI/UX Truthfulness
Correction Program, Wave 0A).

Every route that can fail on an unexpected server-side exception must:
  1. log the real exception, with context, server-side only; and
  2. return a stable, user-safe message — never str(e), never a traceback,
     never a Python exception class name, never SQL/provider/internal detail.

This module is the single place that pattern is implemented, so every call
site produces the same shape of safe response instead of each route
independently deciding how much of an exception to leak.
"""

import logging


def safe_error_message(log: logging.Logger, context: str, exc: Exception, user_message: str) -> str:
    """
    Log the real exception (with full traceback, via log.exception) under a
    short context tag, and return the given user-safe message unchanged.

    context: short dotted identifier for the failing call site, e.g.
             "alerts.get_alerts" — appears only in server-side logs.
    user_message: the exact string to show the caller/client. Never derived
                  from `exc` — callers must supply a fixed, feature-appropriate
                  message (see the router call sites for examples).
    """
    log.exception(f"[{context}] request failed: {exc.__class__.__name__}")
    return user_message
