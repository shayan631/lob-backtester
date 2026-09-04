"""
Event-driven backtest / adapter layer.

This package is the glue between strategy/ (this branch) and engine/
(engine-core branch): it lets a Strategy talk to a real matching engine
through the same ExecutionClient interface it already uses with
strategy/toy_book.py.

See backtest/execution.py for details and the swap-over plan once
engine-core's OrderBook lands on this branch.
"""

from backtest.execution import EngineExecutionClient, book_snapshot_from_engine

__all__ = ["EngineExecutionClient", "book_snapshot_from_engine"]
