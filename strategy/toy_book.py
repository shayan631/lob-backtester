"""
A deliberately simple stand-in for engine/. It is NOT price-time priority,
NOT a real matching engine — it just lets you run a strategy end-to-end and
see fills/PnL while the real engine is being built.

Fill logic: a resting limit order fills if a synthetic "market price" crosses
it (bid fills if market trades at/below your bid, ask fills if market trades
at/above your ask). One fill per order, then it's gone (no partials).

Swap this out for the real engine/ once it exists — Strategy subclasses
should not need to change, only how ToyExecutionClient/ToyBook are built.
"""

import itertools
import random

from strategy.base import BookSnapshot, ExecutionClient, Fill, Side


class ToyExecutionClient(ExecutionClient):
    def __init__(self, strategy_ref_holder):
        # strategy_ref_holder is a 1-element list so we can wire the strategy
        # in after construction (execution client needs a strategy to call
        # on_fill back on; strategy needs an execution client to submit to).
        self._strategy_holder = strategy_ref_holder
        self._orders: dict[str, tuple[Side, float, float]] = {}
        self._id_counter = itertools.count(1)

    def submit_order(self, side: Side, price: float, qty: float) -> str:
        order_id = f"o{next(self._id_counter)}"
        self._orders[order_id] = (side, price, qty)
        return order_id

    def cancel_order(self, order_id: str) -> None:
        self._orders.pop(order_id, None)

    def cancel_all(self) -> None:
        self._orders.clear()

    def try_fill(self, market_price: float, timestamp: float) -> None:
        strategy = self._strategy_holder[0]
        filled_ids = []
        for order_id, (side, price, qty) in self._orders.items():
            if side == Side.BUY and market_price <= price:
                strategy.on_fill(Fill(timestamp, Side.BUY, price, qty))
                filled_ids.append(order_id)
            elif side == Side.SELL and market_price >= price:
                strategy.on_fill(Fill(timestamp, Side.SELL, price, qty))
                filled_ids.append(order_id)
        for order_id in filled_ids:
            self._orders.pop(order_id, None)


def synthetic_mid_price_walk(
    start_price: float = 100.0,
    n_steps: int = 500,
    step_size: float = 0.02,
    seed: int = 7,
):
    """Random walk standing in for real LOBSTER tick data."""
    rng = random.Random(seed)
    price = start_price
    for t in range(n_steps):
        price += rng.uniform(-step_size, step_size)
        spread = 0.02
        yield BookSnapshot(
            timestamp=float(t),
            best_bid=price - spread / 2,
            best_ask=price + spread / 2,
            bid_size=100.0,
            ask_size=100.0,
        )


def synthetic_trade_price(mid: float, rng: random.Random, jitter: float = 0.08) -> float:
    """
    Simulate one aggressive market order trading near (and sometimes through)
    the mid, so resting limit quotes on either side occasionally get hit.
    Wider jitter than the quoting half_spread means fills will happen.
    """
    return mid + rng.uniform(-jitter, jitter)
