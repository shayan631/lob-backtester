"""
Adapter that lets a Strategy actually do buy and sell type shit against a
REAL order book, instead of the fake fills from toy_book.py.

Why it's written the way it is
----------------------------------
Right now (as of writing this), engine-core only has engine/order.py
(Order, Trade dataclasses) done. The actual OrderBook -- add_limit_order(),
cancel_order(), best_bid(), best_ask(), snapshot() -- isn't built yet.
BUT their own tests/test_engine.py already spells out exactly what that
interface is gonna look like, so we're coding against that.

Can't do `from engine import Order, OrderBook` because engine/ doesn't
even exist on this branch yet -- that import would just explode. So
instead this file fakes the shape of it (duck typing, basically "if it
quacks like an OrderBook, we don't care what it actually is"):

    Order-like:     id, side ("buy"/"sell"), price, quantity, timestamp
    OrderBook-like: add_limit_order(order) -> list[Trade]
                    cancel_order(order_id) -> bool
                    best_bid() -> float | None
                    best_ask() -> float | None
                    snapshot() -> {"bids": [(price, qty), ...],
                                    "asks": [(price, qty), ...]}
    Trade-like:      buy_order_id, sell_order_id, price, quantity, timestamp

Once engine-core actually gets merged into this branch:
    1. Yeet the `_EngineOrder` shim below.
    2. Swap it for `from engine.order import Order as _EngineOrder`.
    3. Nothing else here should need to change -- that's the whole point
       of coding against the interface instead of some hardcoded class.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from strategy.base import BookSnapshot, ExecutionClient, Fill, Side


# ---------------------------------------------------------------------------
# Shim -- delete once engine/order.py (and engine/__init__.py, once that
# exists too) shows up on this branch.
#
# IMPORTANT -- this is exactly the piece that needs to change carefully.
# The original version of this file used a PRIVATE itertools.count(1) on
# EngineExecutionClient itself. That's a real bug: engine/order.py already
# defines a shared next_order_id() specifically so every order in the
# system pulls from ONE counter. Two independent counters that both start
# at 1 will collide the instant they both feed orders into the same
# OrderBook -- e.g. a historical order gets id 1, then our own first
# submit_order() also generates id 1, silently overwriting the historical
# order's spot in order_locations. Cancelling "our" order 1 then cancels
# the wrong thing, and the historical order is orphaned on the book with
# no way to reference it again. No error, no warning -- it just quietly
# does the wrong thing.
#
# Fix: route order id generation through one function, injected in here,
# instead of a counter owned by this class. Once engine-core is merged:
#   from engine import next_order_id
#   EngineExecutionClient(order_book, holder, order_id_factory=next_order_id)
# and make sure the historical data loader (Phase 2) calls next_order_id()
# too, rather than reusing LOBSTER's raw order ids directly -- those
# aren't guaranteed to avoid collision with ids generated on our side
# either.
# ---------------------------------------------------------------------------
_local_id_counter = itertools.count(1)


def _next_order_id_shim() -> int:
    """
    Local stand-in for engine.next_order_id(). This is module-level (not
    per-instance), so multiple EngineExecutionClient objects in the same
    process at least won't collide with EACH OTHER by default -- but it's
    still a separate counter from anything else in the system (e.g. a
    data loader) until everyone's wired up to the real shared one. Delete
    this once engine-core is merged in and pass the real next_order_id as
    order_id_factory instead.
    """
    return next(_local_id_counter)


@dataclass
class _EngineOrder:
    id: int
    side: str
    price: float
    quantity: int
    timestamp: int


@runtime_checkable
class OrderBookProtocol(Protocol):
    """Whatever engine-core's real OrderBook ends up being, it needs to at least do this."""

    def add_limit_order(self, order: Any) -> list: ...
    def cancel_order(self, order_id: int) -> bool: ...
    def best_bid(self) -> float | None: ...
    def best_ask(self) -> float | None: ...
    def snapshot(self) -> dict: ...


class EngineExecutionClient(ExecutionClient):
    """
    Wraps a real (or real-shaped) OrderBook so a Strategy can go do its
    buy and sell type shit against it the exact same way it already does
    with ToyExecutionClient -- Strategy subclasses don't need to change
    a single line to run against this instead.

    Used basically the same way as toy_book.ToyExecutionClient:

        strategy_holder = [None]
        execution = EngineExecutionClient(order_book, strategy_holder)
        strategy = SimpleMarketMaker(execution, ...)
        strategy_holder[0] = strategy

    One thing the toy client never had to deal with: a resting order can
    get filled way later by SOMEONE ELSE'S order (like a historical order
    getting replayed into the book by the backtest loop), not just by
    whatever we just submitted ourselves. So call apply_trades() with
    whatever Trade list the engine hands back from ANY add_limit_order()
    call -- ours or somebody else's -- so those fills still make it back
    to the strategy.
    """

    def __init__(
        self,
        order_book: OrderBookProtocol,
        strategy_ref_holder: list,
        order_id_factory=_next_order_id_shim,
    ):
        self._book = order_book
        self._strategy_holder = strategy_ref_holder
        self._next_order_id = order_id_factory
        # only tracking OUR OWN orders here -- how we know which trades
        # are actually "ours" when apply_trades() gets called
        self._our_order_sides: dict[int, Side] = {}

    def submit_order(self, side: Side, price: float, qty: float) -> str:
        order_id = self._next_order_id()
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
        # nuke everything we've got resting
        for oid in list(self._our_order_sides):
            self.cancel_order(str(oid))

    def apply_trades(self, trades: list) -> None:
        """
        Feed it a batch of Trades and it'll ping the strategy's on_fill()
        for any of OUR resting orders that got hit. Trades that have
        nothing to do with us just get ignored, no harm done. The backtest
        replay loop should be calling this after literally every
        add_limit_order() it does on the shared book, not just the ones
        that came from this client.
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
    Turns a real OrderBook's raw state into the BookSnapshot shape
    strategy/base.py actually expects.

    Returns None if either side of the book is empty -- no bid or no ask
    means there's no real mid/spread to quote around yet, so just skip
    calling on_book_update() for that tick. Happens naturally at the very
    start of a replay before any resting liquidity has built up.
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
