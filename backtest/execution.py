"""
Adapter that lets a Strategy (strategy/base.py) run against the real
matching engine once it exists, instead of strategy/toy_book.py's fake
fill logic.

Why this is written the way it is
----------------------------------
As of this writing, engine-core (the other branch) only has
engine/order.py (Order, Trade dataclasses) committed. engine.OrderBook
itself -- add_limit_order() / cancel_order() / best_bid() / best_ask() /
snapshot() -- is not implemented yet, though engine-core's own
tests/test_engine.py already documents the exact interface it will have.

Rather than `from engine import Order, OrderBook` (which would break this
branch right now, since engine/ doesn't exist here), this module is
written against that interface as a structural/duck-typed contract:

    Order-like:     id, side ("buy"/"sell"), price, quantity, timestamp
    OrderBook-like: add_limit_order(order) -> list[Trade]
                    cancel_order(order_id) -> bool
                    best_bid() -> float | None
                    best_ask() -> float | None
                    snapshot() -> {"bids": [(price, qty), ...],
                                    "asks": [(price, qty), ...]}
    Trade-like:      buy_order_id, sell_order_id, price, quantity, timestamp

Once engine-core is merged into this branch:
    1. Delete the `_EngineOrder` shim below.
    2. Replace it with `from engine.order import Order as _EngineOrder`.
    3. Everything else in this file should keep working unchanged --
       that's the whole point of coding against the interface instead of
       the concrete class.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from strategy.base import BookSnapshot, ExecutionClient, Fill, Side


# ---------------------------------------------------------------------------
# Shim -- delete once engine/order.py exists on this branch (see docstring).
# ---------------------------------------------------------------------------
@dataclass
class _EngineOrder:
    id: int
    side: str
    price: float
    quantity: int
    timestamp: int


@runtime_checkable
class OrderBookProtocol(Protocol):
    """Structural contract for whatever engine-core's OrderBook exposes."""

    def add_limit_order(self, order: Any) -> list: ...
    def cancel_order(self, order_id: int) -> bool: ...
    def best_bid(self) -> float | None: ...
    def best_ask(self) -> float | None: ...
    def snapshot(self) -> dict: ...


class EngineExecutionClient(ExecutionClient):
    """
    Wraps a real (or real-shaped) OrderBook so a Strategy can trade
    against it through the same ExecutionClient interface it already
    uses with ToyExecutionClient -- Strategy subclasses need zero changes
    to run against this instead.

    Usage mirrors toy_book.ToyExecutionClient:

        strategy_holder = [None]
        execution = EngineExecutionClient(order_book, strategy_holder)
        strategy = SimpleMarketMaker(execution, ...)
        strategy_holder[0] = strategy

    One thing the toy client didn't need to handle: a resting order can
    get filled later by *someone else's* order (e.g. a historical/replayed
    order fed into the book by the backtest loop), not just by the order
    the strategy itself just submitted. Call apply_trades() with whatever
    Trade list the engine returns from any add_limit_order() call -- ours
    or an external one -- so those fills also reach the strategy.
    """

    def __init__(self, order_book: OrderBookProtocol, strategy_ref_holder: list):
        self._book = order_book
        self._strategy_holder = strategy_ref_holder
        self._id_counter = itertools.count(1)
        # Only orders THIS client submitted -- used to recognize which
        # trades belong to our strategy when apply_trades() is called.
        self._our_order_sides: dict[int, Side] = {}

    def submit_order(self, side: Side, price: float, qty: float) -> str:
        order_id = next(self._id_counter)
        order = _EngineOrder(
            id=order_id,
            side=side.value,
            price=price,
            quantity=int(qty),
            timestamp=order_id,
        )
        self._our_order_sides[order_id] = side
        trades = self._book.add_limit_order(order)
        self.apply_trades(trades)
        return str(order_id)

    def cancel_order(self, order_id: str) -> None:
        oid = int(order_id)
        self._book.cancel_order(oid)
        self._our_order_sides.pop(oid, None)

    def cancel_all(self) -> None:
        for oid in list(self._our_order_sides):
            self.cancel_order(str(oid))

    def apply_trades(self, trades: list) -> None:
        """
        Feed a batch of Trade objects through this client so fills on our
        resting orders reach the strategy via on_fill(). Safe to call with
        trades that don't involve us at all -- those are ignored. The
        backtest replay loop should call this after every add_limit_order()
        it makes on the shared book, not just the ones this client made.
        """
        strategy = self._strategy_holder[0]
        for trade in trades:
            if trade.buy_order_id in self._our_order_sides:
                self._emit_fill(strategy, trade, self._our_order_sides[trade.buy_order_id])
            if trade.sell_order_id in self._our_order_sides:
                self._emit_fill(strategy, trade, self._our_order_sides[trade.sell_order_id])

    @staticmethod
    def _emit_fill(strategy, trade: Any, side: Side) -> None:
        strategy.on_fill(
            Fill(
                timestamp=float(trade.timestamp),
                side=side,
                price=trade.price,
                qty=trade.quantity,
            )
        )


def book_snapshot_from_engine(order_book: OrderBookProtocol, timestamp: float) -> BookSnapshot | None:
    """
    Build a strategy/base.py BookSnapshot from a real OrderBook.

    Returns None if either side of the book is currently empty (no valid
    mid/spread to quote around yet) -- callers should skip the
    on_book_update() call for that event when this returns None, same as
    you'd do at the very start of a replay before any resting liquidity
    exists.
    """
    best_bid = order_book.best_bid()
    best_ask = order_book.best_ask()
    if best_bid is None or best_ask is None:
        return None

    snapshot = order_book.snapshot()
    bids = snapshot.get("bids") or []
    asks = snapshot.get("asks") or []
    bid_size = bids[0][1] if bids else 0.0
    ask_size = asks[0][1] if asks else 0.0

    return BookSnapshot(
        timestamp=timestamp,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )
