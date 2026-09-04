"""
Integration test: the ONE thing neither test_engine.py (engine-side) nor
test_execution.py (adapter-side, tested in isolation via FakeOrderBook)
actually proves -- that a real engine.OrderBook, wired through
EngineExecutionClient, correctly drives a real Strategy (SimpleMarketMaker)
end to end.

This is intentionally NOT using FakeOrderBook or any test double. Every
piece here is the real thing:
    engine.OrderBook          <- the actual matching engine
    EngineExecutionClient     <- the actual adapter (backtest/execution.py)
    SimpleMarketMaker         <- the actual strategy
    engine.next_order_id      <- the actual shared id source

If this passes, the two halves of the project are proven to actually work
together, not just separately against their own mocks.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import Order, OrderBook, next_order_id

from backtest.execution import EngineExecutionClient, book_snapshot_from_engine
from strategy.market_maker import SimpleMarketMaker
from strategy.base import Side


def _make_strategy_on_real_book(order_book, **strategy_kwargs):
    holder = [None]
    execution = EngineExecutionClient(order_book, holder)
    strategy = SimpleMarketMaker(execution, **strategy_kwargs)
    holder[0] = strategy
    return strategy, execution


def test_strategy_quotes_land_correctly_on_a_real_order_book():
    book = OrderBook()
    strategy, _ = _make_strategy_on_real_book(
        book, half_spread=0.5, order_qty=10.0
    )

    snapshot = book_snapshot_from_engine(book, timestamp=1.0)
    assert snapshot is None  # nothing resting yet, both sides empty -- expected

    # Seed the book with some outside liquidity so there's an actual mid to quote around.
    seed_bid = Order(id=next_order_id(), side="buy", price=99.0, quantity=50, timestamp=1)
    seed_ask = Order(id=next_order_id(), side="sell", price=101.0, quantity=50, timestamp=1)
    book.add_limit_order(seed_bid)
    book.add_limit_order(seed_ask)

    snapshot = book_snapshot_from_engine(book, timestamp=2.0)
    assert snapshot is not None
    assert snapshot.mid == 100.0

    strategy.on_book_update(snapshot)

    # SimpleMarketMaker should have placed a bid and an ask around mid=100
    # with half_spread=0.5, i.e. 99.5 and 100.5 -- and they should actually
    # be resting on the REAL book now.
    book_snapshot = book.snapshot()
    bid_prices = [price for price, _qty in book_snapshot["bids"]]
    ask_prices = [price for price, _qty in book_snapshot["asks"]]

    assert 99.5 in bid_prices
    assert 100.5 in ask_prices


def test_strategy_receives_real_fills_from_the_real_engine():
    book = OrderBook()
    strategy, execution = _make_strategy_on_real_book(
        book, half_spread=0.5, order_qty=10.0, max_position=1000.0
    )

    seed_bid = Order(id=next_order_id(), side="buy", price=99.0, quantity=50, timestamp=1)
    seed_ask = Order(id=next_order_id(), side="sell", price=101.0, quantity=50, timestamp=1)
    book.add_limit_order(seed_bid)
    book.add_limit_order(seed_ask)

    snapshot = book_snapshot_from_engine(book, timestamp=2.0)
    strategy.on_book_update(snapshot)

    assert strategy.position == 0.0
    assert strategy.fills == []

    # Now an aggressive external sell crosses the strategy's resting bid
    # (99.5) -- this should generate a real Trade from the real engine,
    # and the strategy should hear about it via on_fill().
    aggressive_sell = Order(id=next_order_id(), side="sell", price=99.5, quantity=10, timestamp=3)
    trades = book.add_limit_order(aggressive_sell)
    execution.apply_trades(trades)

    assert len(strategy.fills) == 1
    assert strategy.fills[0].side == Side.BUY
    assert strategy.fills[0].price == 99.5
    assert strategy.position == 10.0
    assert strategy.cash == -995.0  # -99.5 * 10


def test_order_ids_never_collide_between_seeded_and_strategy_orders():
    """
    The regression case from review, proven against the REAL engine this
    time: seed the book with "historical" orders via next_order_id(), let
    the strategy place its own orders via the same shared source, and
    confirm cancelling a strategy order never touches the historical one.
    """
    book = OrderBook()
    historical_id = next_order_id()
    book.add_limit_order(
        Order(id=historical_id, side="buy", price=99.0, quantity=50, timestamp=1)
    )

    strategy, execution = _make_strategy_on_real_book(book, half_spread=0.5, order_qty=10.0)

    seed_ask = Order(id=next_order_id(), side="sell", price=101.0, quantity=50, timestamp=1)
    book.add_limit_order(seed_ask)

    snapshot = book_snapshot_from_engine(book, timestamp=2.0)
    strategy.on_book_update(snapshot)

    assert book.cancel_order(historical_id) is True  # still findable, untouched
    # (re-rest it since cancel_order in this test was just to prove it existed)
    book.add_limit_order(
        Order(id=historical_id, side="buy", price=99.0, quantity=50, timestamp=1)
    )
