import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import Order, OrderBook


def test_non_crossing_order_rests_on_book():
    book = OrderBook()
    order = Order(id=1, side="buy", price=100, quantity=10, timestamp=1)
    trades = book.add_limit_order(order)

    assert trades == []
    assert book.best_bid() == 100
    assert book.best_ask() is None


def test_crossing_order_generates_trade():
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="sell", price=100, quantity=10, timestamp=1))
    trades = book.add_limit_order(Order(id=2, side="buy", price=100, quantity=10, timestamp=2))

    assert len(trades) == 1
    assert trades[0].price == 100
    assert trades[0].quantity == 10
    assert trades[0].buy_order_id == 2
    assert trades[0].sell_order_id == 1
    assert book.best_bid() is None
    assert book.best_ask() is None


def test_partial_fill_leaves_remainder_resting():
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="sell", price=100, quantity=10, timestamp=1))
    trades = book.add_limit_order(Order(id=2, side="buy", price=100, quantity=15, timestamp=2))

    assert len(trades) == 1
    assert trades[0].quantity == 10
    assert book.best_bid() == 100
    snapshot = book.snapshot()
    assert snapshot["bids"] == [(100, 5)]


def test_resting_order_partially_filled_stays_at_front():
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="sell", price=100, quantity=10, timestamp=1))
    book.add_limit_order(Order(id=2, side="buy", price=100, quantity=4, timestamp=2))
    trades = book.add_limit_order(Order(id=3, side="buy", price=100, quantity=6, timestamp=3))

    assert len(trades) == 1
    assert trades[0].sell_order_id == 1
    assert trades[0].quantity == 6
    assert book.best_ask() is None


def test_cancel_removes_order():
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="buy", price=100, quantity=10, timestamp=1))
    assert book.cancel_order(1) is True
    assert book.best_bid() is None
    assert book.cancel_order(1) is False


def test_price_time_priority():
    """Two orders resting at the same price should fill in arrival order."""
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="sell", price=100, quantity=5, timestamp=1))
    book.add_limit_order(Order(id=2, side="sell", price=100, quantity=5, timestamp=2))

    trades = book.add_limit_order(Order(id=3, side="buy", price=100, quantity=5, timestamp=3))

    assert trades[0].sell_order_id == 1
    assert book.best_ask() == 100
    snapshot = book.snapshot()
    assert snapshot["asks"] == [(100, 5)]


def test_trade_executes_at_resting_price_not_incoming_price():
    """The resting order's price should always win, since it was there
    first and set the terms.
    """
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="sell", price=118, quantity=25, timestamp=1))
    trades = book.add_limit_order(Order(id=2, side="buy", price=120, quantity=25, timestamp=2))

    assert len(trades) == 1
    assert trades[0].price == 118


def test_resting_bid_price_wins_against_incoming_ask():
    """Same idea, other direction: if a bid is resting and a cheaper ask
    comes in and crosses it, the trade should fill at the resting bid's
    price, not the incoming ask's price.
    """
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="buy", price=120, quantity=40, timestamp=1))
    trades = book.add_limit_order(Order(id=2, side="sell", price=115, quantity=40, timestamp=2))

    assert len(trades) == 1
    assert trades[0].price == 120  # resting bid's price, NOT the incoming ask's 115


def test_no_match_when_prices_dont_cross():
    book = OrderBook()
    book.add_limit_order(Order(id=1, side="sell", price=105, quantity=10, timestamp=1))
    trades = book.add_limit_order(Order(id=2, side="buy", price=100, quantity=10, timestamp=2))

    assert trades == []
    assert book.best_bid() == 100
    assert book.best_ask() == 105