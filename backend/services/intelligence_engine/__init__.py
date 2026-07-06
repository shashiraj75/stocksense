"""
Intelligence Engine — StockSense360's next-generation universe/quality
scoring system (Epic 007). Universe Builder (instrument-type/tradability/
liquidity/governance/financial-quality gates, cap/sector diversification)
is one component living inside this engine, not the whole system; future
components (opportunity scoring, risk tiering, etc.) will live alongside it
here as they're built.

Phase 3A ships only the smallest possible slice: the Instrument Type Gate,
run in shadow mode (evaluated and recorded, never affecting what Daily
Picks actually publishes). See shadow_run.run_shadow_slice for the entry
point daily_picks.py calls, and instrument_type_gate.classify_instrument
for the gate itself.
"""
