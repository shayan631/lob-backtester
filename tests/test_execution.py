"""
Tests for backtest/execution.py.

These use FakeOrderBook, a minimal test double shaped exactly like what
engine-core's tests/test_engine.py implies OrderBook will look like
(add_limit_order / cancel_order / best_bid / best_ask / snapshot, with
price-time priority matching). It is NOT the real engine -- it exists so
this adapter can be tested and pushed now, independent of engine-core's
progress. Once the real engine.OrderBook lands, re-point these tests at
it (or add a second parametrized run against the real thing) to confirm
the adapter still holds up.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import itertools
from dataclasses import dataclass

from backtest.execution import EngineExecutionClient, book_snapshot_from_engine, _EngineOrder
from strategy.base import Fill, Side


@dataclass
class _Trade:
    buy_order_id: int
    sell_order_id: int
    price: float
    quantity: int
    timestamp: int


class FakeOrderBook:
    """Simplified price-time-priority book, just enough to exercise the adapter."""

    def __init__(self):
        self._bids: list[dict] = []  # each: {id, price, qty}, sorted best-first
        self._asks: list[dict] = []
        self._ts = 0

    def add_limit_order(self, order) -> list:
        self._ts += 1
        trades = []
        book_side, resting_side = (
            (self._asks, "ask") if order.side == "buy" else (self._bids, "bid")
        )
        remaining = order.quantity

        while remaining > 0 and book_side:
            best = book_side[0]
            crosses = (
                order.side == "buy" and order.price >= best["price"]
            ) or (order.side == "sell" and order.price <= best["price"])
            if not crosses:
                break

            fill_qty = min(remaining, best["qty"])
            buy_id = order.id if order.side == "buy" else best["id"]
            sell_id = best["id"] if order.side == "buy" else order.id
            trades.append(_Trade(buy_id, sell_id, best["price"], fill_qty, self._ts))

            remaining -= fill_qty
            best["qty"] -= fill_qty
            if best["qty"] <= 0:
                book_side.pop(0)

        if remaining > 0:
            resting_book = self._bids if order.side == "buy" else self._asks
            resting_book.append({"id": order.id, "price": order.price, "qty": remaining})
            resting_book.sort(
                key=lambda o: -o["price"] if order.side == "buy" else o["price"]
            )

        return trades

    def cancel_order(self, order_id) -> bool:
        for book_side in (self._bids, self._asks):
            for i, o in enumerate(book_side):
                if o["id"] == order_id:
                    book_side.pop(i)
                    return True
        return False

    def best_bid(self):
        return self._bids[0]["price"] if self._bids else None

    def best_ask(self):
        return self._asks[0]["price"] if self._asks else None

    def snapshot(self) -> dict:
        return {
            "bids": [(o["price"], o["qty"]) for o in self._bids],
            "asks": [(o["price"], o["qty"]) for o in self._asks],
        }


def _make_client():
    holder = [None]
    book = FakeOrderBook()
    client = EngineExecutionClient(book, holder)
    return book, client, holder


class _RecordingStrategy:
    """Bare-bones stand-in for strategy.base.Strategy, just to capture on_fill calls."""

    def __init__(self):
        self.fills: list[Fill] = []

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)


def test_non_crossing_order_rests_and_produces_no_fill():
    book, client, holder = _make_client()
    holder[0] = _RecordingStrategy()

    order_id = client.submit_order(Side.BUY, price=100.0, qty=10)

    assert order_id == "1"
    assert book.best_bid() == 100.0
    assert book.best_ask() is None
    assert holder[0].fills == []


def test_crossing_order_fills_immediately():
    book, client, holder = _make_client()
    strategy = _RecordingStrategy()
    holder[0] = strategy

    client.submit_order(Side.SELL, price=100.0, qty=10)
    client.submit_order(Side.BUY, price=100.0, qty=10)

    assert len(strategy.fills) == 2  # our resting sell AND our incoming buy both fill
    sides = {f.side for f in strategy.fills}
    assert sides == {Side.BUY, Side.SELL}
    assert all(f.price == 100.0 and f.qty == 10 for f in strategy.fills)


def test_resting_order_filled_later_via_apply_trades():
    """
    Simulates the case a lone submit_order() call can't cover: our quote
    rests, then an EXTERNAL order (e.g. from historical replay, not routed
    through this client) crosses it later. The backtest loop is expected
    to call apply_trades() with whatever the engine returns from that
    external add_limit_order() call.
    """
    book, client, holder = _make_client()
    strategy = _RecordingStrategy()
    holder[0] = strategy

    client.submit_order(Side.BUY, price=100.0, qty=10)
    assert strategy.fills == []  # nothing crosses yet

    external_order = type(
        "ExternalOrder", (), {"id": 999, "side": "sell", "price": 100.0, "quantity": 10}
    )()
    trades = book.add_limit_order(external_order)
    client.apply_trades(trades)

    assert len(strategy.fills) == 1
    assert strategy.fills[0].side == Side.BUY
    assert strategy.fills[0].price == 100.0


def test_cancel_order_removes_from_book():
    book, client, holder = _make_client()
    holder[0] = _RecordingStrategy()

    order_id = client.submit_order(Side.BUY, price=100.0, qty=10)
    client.cancel_order(order_id)

    assert book.best_bid() is None


def test_cancel_all_clears_every_live_order():
    book, client, holder = _make_client()
    holder[0] = _RecordingStrategy()

    client.submit_order(Side.BUY, price=99.0, qty=5)
    client.submit_order(Side.SELL, price=101.0, qty=5)
    client.cancel_all()

    assert book.best_bid() is None
    assert book.best_ask() is None


def test_book_snapshot_from_engine_returns_none_when_one_side_empty():
    book, client, _ = _make_client()
    client.submit_order(Side.BUY, price=100.0, qty=10)

    assert book_snapshot_from_engine(book, timestamp=1.0) is None


def test_book_snapshot_from_engine_builds_snapshot_once_both_sides_exist():
    book, client, _ = _make_client()
    client.submit_order(Side.BUY, price=99.0, qty=10)
    client.submit_order(Side.SELL, price=101.0, qty=7)

    snap = book_snapshot_from_engine(book, timestamp=2.0)

    assert snap is not None
    assert snap.best_bid == 99.0
    assert snap.best_ask == 101.0
    assert snap.bid_size == 10
    assert snap.ask_size == 7
    assert snap.mid == 100.0


# ---------------------------------------------------------------------------
# Regression tests for the order-id-collision bug flagged in review: the
# original EngineExecutionClient had its own PRIVATE itertools.count(1),
# so any two independent id sources feeding the same OrderBook (e.g. a
# historical order and the strategy's own first order) could both hand
# out id=1, silently corrupting whichever order got overwritten and
# leaving it uncancellable through the normal API.
# ---------------------------------------------------------------------------

def test_default_id_shim_is_shared_across_client_instances():
    """
    Two EngineExecutionClient instances with no explicit order_id_factory
    should NOT hand out the same first id -- the default shim is
    module-level now, not per-instance.
    """
    book = FakeOrderBook()
    client_a = EngineExecutionClient(book, [None])
    client_b = EngineExecutionClient(book, [None])

    id_a = client_a.submit_order(Side.BUY, price=99.0, qty=5)
    id_b = client_b.submit_order(Side.SELL, price=105.0, qty=5)

    assert id_a != id_b


def test_shared_id_factory_prevents_historical_and_strategy_orders_colliding():
    """
    This is the scenario from review, but fixed: a historical order and
    the strategy's own order both pull ids from ONE shared factory (what
    engine.next_order_id() is for), so they can never collide, and
    cancelling our own order never touches the historical one.
    """
    shared_counter = itertools.count(1)

    def shared_next_id():
        return next(shared_counter)

    book = FakeOrderBook()
    historical_id = shared_next_id()
    book.add_limit_order(
        _EngineOrder(id=historical_id, side="buy", price=99.0, quantity=10, timestamp=1)
    )

    client = EngineExecutionClient(book, [None], order_id_factory=shared_next_id)
    strategy_order_id = client.submit_order(Side.SELL, price=105.0, qty=5)

    assert int(strategy_order_id) != historical_id

    client.cancel_order(strategy_order_id)
    assert book.best_bid() == 99.0  # historical bid untouched
    assert book.best_ask() is None  # our ask is the one that got cancelled


def test_unshared_id_factories_collide_documents_the_original_bug():
    """
    Documents the failure mode the fix exists to prevent -- NOT something
    we want. If a historical order is fed in via its own counter starting
    at 1, and the strategy's client is (deliberately, for this test) given
    its own separate counter that ALSO starts at 1, they collide: the
    strategy's order silently reuses the historical order's id, and
    cancelling "our" order actually removes the historical one instead.
    """
    book = FakeOrderBook()
    book.add_limit_order(_EngineOrder(id=1, side="buy", price=99.0, quantity=10, timestamp=1))

    unshared_counter = itertools.count(1)
    client = EngineExecutionClient(book, [None], order_id_factory=lambda: next(unshared_counter))
    strategy_order_id = client.submit_order(Side.SELL, price=105.0, qty=5)

    assert strategy_order_id == "1"  # collided with the historical order's id

    client.cancel_order(strategy_order_id)
    assert book.best_bid() is None  # wrong! the historical bid is what got removed
    assert book.best_ask() == 105.0  # our order is still resting, now orphaned
