"""
Adapter that lets a Strategy actually do buy and sell type shit against a
REAL order book, instead of the fake fills from toy_book.py.

Now wired up to the real engine -- engine.Order, engine.Trade, and
engine.next_order_id are the real deal, not a shim anymore.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from engine import Order, Trade, next_order_id

from strategy.base import BookSnapshot, ExecutionClient, Fill, Side


@runtime_checkable
class OrderBookProtocol(Protocol):
    """Structural contract for engine.OrderBook -- kept even now that the
    real import exists, since it's still useful for tests (FakeOrderBook)
    and keeps this file from hard-depending on the concrete class."""

    def add_limit_order(self, order: Any) -> list: ...
    def cancel_order(self, order_id: int) -> bool: ...
    def best_bid(self) -> float | None: ...
    def best_ask(self) -> float | None: ...
    def snapshot(self) -> dict: ...


class EngineExecutionClient(ExecutionClient):
    """
    Wraps OrderBook so  Strategy can go do its
    buy and sell against it the exact same way it already does
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
        order_id_factory=next_order_id,
    ):
        self._book = order_book
        self._strategy_holder = strategy_ref_holder
        self._next_order_id = order_id_factory
        # only tracking OUR OWN orders here -- how we know which trades
        # are actually "ours" when apply_trades() gets called
        self._our_order_sides: dict[int, Side] = {}

    def submit_order(self, side: Side, price: float, qty: float) -> str:
        order_id = self._next_order_id()
        order = Order(
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
