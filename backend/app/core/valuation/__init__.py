"""Valuation step — consumes projected cash flows and applies methodology-specific discounting.

Kept separate from the projection loop so each methodology stays explicit and independently
testable (CAS §5, §7.1).
"""
