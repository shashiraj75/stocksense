"""
StockSense360 Market Leadership and Trend Context Layer.

An isolated, shadow-only bounded module providing:
- Stock Relative Strength Rank (benchmark-relative, universe percentile)
- Sector / Industry Group Leadership (breadth-aware, concentration-capped)
- Trend Lifecycle classification (deterministic, reason-coded)
- Market Breadth context (session-aware, coverage-disclosed)
- "Why Now?" structured explanations (template-rendered from verified fields)

Design rules (see Documentation/Engineering-Handbook/Architecture/
Market-Leadership-Trend-Context-Layer.md):
- Calculation functions are pure and deterministic: no network, no clock
  reads — `as_of` is always an explicit argument.
- India and US are never mixed in one percentile universe.
- Missing data yields INSUFFICIENT_DATA, never a silent neutral score.
- Every output carries methodology/universe versions and coverage.
- All feature flags default OFF; nothing here influences published
  scoring, Daily Picks, Multibagger, Portfolio, Alerts or Paper Trading.
"""
