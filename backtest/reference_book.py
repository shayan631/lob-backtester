"""Tiny aggregate-level book implementing the BookAdapter protocol.

Not a matching engine: it tracks per-order size and per-price totals only.
Use it to prove the replay/validation plumbing works, then swap in the real
engine via EngineAdapter and see whether it agrees with the exchange too.
"""
from __future__ import annotations

from typing import Optional

from backtest.lobster import BUY, SELL


class ReferenceBook:
    def __init__(self) -> None:
        self.orders: dict[int, list] = {}            # id -> [side, price, size]
        self.levels = {BUY: {}, SELL: {}}            # side -> price -> total size

    def submit_limit(self, order_id: int, side: int, price: int, size: int) -> None:
        self.orders[order_id] = [side, price, size]
        self.levels[side][price] = self.levels[side].get(price, 0) + size

    def cancel(self, order_id: int, size: Optional[int] = None) -> None:
        side, price, rem = self.orders[order_id]
        take = rem if size is None else min(size, rem)
        self._reduce(order_id, side, price, rem, take)

    def execute(self, order_id: int, size: int) -> None:
        self.cancel(order_id, size)

    def _reduce(self, order_id, side, price, rem, take) -> None:
        self.levels[side][price] -= take
        if self.levels[side][price] <= 0:
            del self.levels[side][price]
        if take >= rem:
            del self.orders[order_id]
        else:
            self.orders[order_id][2] = rem - take

    def has_order(self, order_id: int) -> bool:
        return order_id in self.orders

    def depth(self, levels: int):
        asks = sorted(self.levels[SELL].items())
        bids = sorted(self.levels[BUY].items(), reverse=True)
        out = []
        for i in range(levels):
            ap, asz = asks[i] if i < len(asks) else (None, 0)
            bp, bsz = bids[i] if i < len(bids) else (None, 0)
            out.append((ap, asz, bp, bsz))
        return out
