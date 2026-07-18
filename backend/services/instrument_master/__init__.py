"""
Offline NSE instrument-master foundation (Phase C0).

This package is deliberately isolated from the rest of the application:
it imports no production persistence, cache, scheduler, or network module,
and nothing in the rest of the codebase imports from it. It exists to
provide a schema, parser, and canonical/deterministic serialization layer
for an NSE common-equity instrument master built entirely from local,
offline fixture files.

Phase C0 scope only: no live NSE/SEBI/exchange/market-data fetching, no
database access, no wiring into Daily Picks, Prediction Engine, or any
other production code path. See Documentation for the phased plan this
package is the foundation of.
"""
