"""Glue between the replay loop (backtest/lobster.py) and engine.OrderBook.

Design notes (also worth putting in the README):
  * Prices: LOBSTER ints (dollars * 10_000) <-> the engine's float dollars.
    The same int always maps to the same float (int / 10_000), so float keys in
    the engine's SortedDict are stable. depth() converts back with round().
  * Historical executions are NOT replayed through the matching engine. The
    engine has no "this resting order lost N shares" operation, so execute()
    and partial cancel reduce the resting order's remaining_quantity in place.
    The order keeps its queue position, matching LOBSTER semantics.
  * Historical submissions DO go through add_limit_order. On a book that is in
    sync with the exchange they never cross. If one does, the engine would match
    it and silently change the book, so we count those trades in `crossed`;
    any non-zero value means the replay has desynced.
  * Ids: LOBSTER ids and the replay's negative synthetic ids are used as-is;
    engine.next_order_id is not involved.
"""
from __future__ import annotations

from itertools import islice
from typing import Optional

from engine.order import Order
from engine.order_book import OrderBook

from backtest.lobster import Level

SCALE = 10_000


class EngineAdapter:
    def __init__(self, book: Optional[OrderBook] = None) -> None:
        self.book = book if book is not None else OrderBook()
        self._seq = 0
        self.crossed = 0  # trades produced by historical submissions (should stay 0)

    def submit_limit(self, order_id: int, side: int, price: int, size: int) -> None:
        self._seq += 1
        order = Order(
            id=order_id,
            side="buy" if side == 1 else "sell",
            price=price / SCALE,
            quantity=size,
            timestamp=self._seq,
        )
        self.crossed += len(self.book.add_limit_order(order))

    def cancel(self, order_id: int, size: Optional[int] = None) -> None:
        if size is None:
            self.book.cancel_order(order_id)
        else:
            self._reduce(order_id, size)

    def execute(self, order_id: int, size: int) -> None:
        self._reduce(order_id, size)

    def has_order(self, order_id: int) -> bool:
        return order_id in self.book.order_locations

    def depth(self, levels: int) -> list[Level]:
        asks = [(round(p * SCALE), sum(o.remaining_quantity for o in q))
                for p, q in islice(self.book.asks.items(), levels)]
        bids = [(round(p * SCALE), sum(o.remaining_quantity for o in q))
                for p, q in islice(reversed(self.book.bids.items()), levels)]
        out = []
        for i in range(levels):
            ap, asz = asks[i] if i < len(asks) else (None, 0)
            bp, bsz = bids[i] if i < len(bids) else (None, 0)
            out.append((ap, asz, bp, bsz))
        return out

    def _reduce(self, order_id: int, size: int) -> None:
        loc = self.book.order_locations.get(order_id)
        if loc is None:
            return
        side, price = loc
        level = (self.book.bids if side == "buy" else self.book.asks)[price]
        order = next(o for o in level if o.id == order_id)
        order.remaining_quantity -= min(size, order.remaining_quantity)
        if order.remaining_quantity <= 0:
            self.book.cancel_order(order_id)
