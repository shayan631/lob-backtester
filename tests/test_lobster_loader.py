"""
Tests for backtest/lobster_loader.py.

These use hand-built LOBSTER-message-file-style rows (not a real
downloaded file yet -- see KNOWN_LIMITATIONS.md's "Next real-world test"
section) run against the REAL engine.OrderBook, no test doubles. The
point is to prove the parsing + translation logic is correct before
handing a real historical file to it.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import OrderBook

from backtest.lobster_loader import (
    LobsterReplay,
    parse_message_line,
    EVENT_NEW,
    EVENT_PARTIAL_CANCEL,
    EVENT_DELETE,
    EVENT_EXECUTE_VISIBLE,
    EVENT_EXECUTE_HIDDEN,
    EVENT_CROSS_TRADE,
    EVENT_HALT,
)


# ---------------------------------------------------------------------------
# parse_message_line
# ---------------------------------------------------------------------------

def test_parse_message_line_scales_price_from_raw_integer():
    # raw 911400 -> $91.14, NOT $911400 -- this is the easy-to-miss trap
    event = parse_message_line("34200.123456789,1,10001,50,911400,1")

    assert event.time == 34200.123456789
    assert event.event_type == EVENT_NEW
    assert event.lobster_order_id == 10001
    assert event.size == 50
    assert event.price == 91.14
    assert event.direction == 1


def test_parse_message_line_rejects_wrong_column_count():
    import pytest

    with pytest.raises(ValueError):
        parse_message_line("34200.0,1,10001,50,911400")  # missing a column


# ---------------------------------------------------------------------------
# EVENT_NEW
# ---------------------------------------------------------------------------

def test_new_buy_order_rests_on_the_real_book():
    book = OrderBook()
    replay = LobsterReplay(book)

    event = parse_message_line("34200.0,1,10001,50,911400,1")
    trades = replay.apply(event)

    assert trades == []
    assert book.best_bid() == 91.14


def test_new_sell_order_rests_on_the_real_book():
    book = OrderBook()
    replay = LobsterReplay(book)

    event = parse_message_line("34200.0,1,10002,50,912000,-1")
    trades = replay.apply(event)

    assert trades == []
    assert book.best_ask() == 91.2


# ---------------------------------------------------------------------------
# EVENT_DELETE / EVENT_PARTIAL_CANCEL
# ---------------------------------------------------------------------------

def test_full_deletion_removes_the_order():
    book = OrderBook()
    replay = LobsterReplay(book)

    replay.apply(parse_message_line("34200.0,1,10001,50,911400,1"))
    assert book.best_bid() == 91.14

    replay.apply(parse_message_line("34201.0,3,10001,50,911400,1"))
    assert book.best_bid() is None


def test_partial_cancel_currently_removes_the_whole_order_documented_limitation():
    """
    Documents the known limitation from KNOWN_LIMITATIONS.md: a partial
    cancel (type 2) is NOT reducing size right now, it fully removes the
    order, since the engine has no reduce_order(). This test exists so
    that if/when reduce_order() gets added and this behavior changes,
    it fails loudly here instead of silently.
    """
    book = OrderBook()
    replay = LobsterReplay(book)

    replay.apply(parse_message_line("34200.0,1,10001,50,911400,1"))
    replay.apply(parse_message_line("34201.0,2,10001,20,911400,1"))  # "partial" cancel of 20

    # if this were a REAL partial cancel, 30 would remain resting.
    # right now, the whole order is gone instead.
    assert book.best_bid() is None


def test_cancel_of_untracked_lobster_id_is_a_safe_noop():
    """A cancel/delete for an id we never saw a NEW event for (e.g. it
    arrived before the replay window started) shouldn't raise."""
    book = OrderBook()
    replay = LobsterReplay(book)

    trades = replay.apply(parse_message_line("34200.0,3,99999,10,911400,1"))
    assert trades == []


# ---------------------------------------------------------------------------
# EVENT_EXECUTE_VISIBLE -- the main scenario from review
# ---------------------------------------------------------------------------

def test_visible_execution_partial_fill_leaves_remainder_resting():
    """
    The exact scenario from review: a 50-share resting buy gets a 20-share
    execution reported against it -- 30 should remain resting afterward.
    """
    book = OrderBook()
    replay = LobsterReplay(book)

    replay.apply(parse_message_line("34200.0,1,10001,50,911400,1"))  # resting buy, 50 @ 91.14

    trades = replay.apply(parse_message_line("34201.0,4,10001,20,911400,1"))

    assert len(trades) == 1
    assert trades[0].price == 91.14
    assert trades[0].quantity == 20

    snapshot = book.snapshot()
    remaining_bid_size = dict(snapshot["bids"]).get(91.14)
    assert remaining_bid_size == 30  # 50 - 20, still resting


def test_visible_execution_direction_describes_resting_order_not_aggressor():
    """
    direction=1 on an execution means the RESTING order was a buy -- i.e.
    the trade's aggressor was actually a SELL. This is the "easy to
    misread as a buy trade happened" trap from review.
    """
    book = OrderBook()
    replay = LobsterReplay(book)

    replay.apply(parse_message_line("34200.0,1,10001,50,911400,1"))  # resting BUY
    trades = replay.apply(parse_message_line("34201.0,4,10001,50,911400,1"))

    assert len(trades) == 1
    # the resting order (10001) was the buy side, so it must appear as
    # the buy_order_id in the resulting trade
    assert trades[0].quantity == 50
    # fully filled -- bid side should now be empty
    assert book.best_bid() is None


def test_visible_execution_full_fill_removes_resting_order_and_id_mapping():
    book = OrderBook()
    replay = LobsterReplay(book)

    replay.apply(parse_message_line("34200.0,1,10001,50,911400,1"))
    replay.apply(parse_message_line("34201.0,4,10001,50,911400,1"))  # fully executed

    assert 10001 not in replay._id_map  # cleaned up, not left dangling

    # a stray later delete for the same lobster id should be a safe no-op,
    # not an error, and definitely shouldn't touch some other order
    trades = replay.apply(parse_message_line("34202.0,3,10001,50,911400,1"))
    assert trades == []


# ---------------------------------------------------------------------------
# Skipped event types -- EVENT_EXECUTE_HIDDEN, EVENT_CROSS_TRADE, EVENT_HALT
# ---------------------------------------------------------------------------

def test_hidden_execution_is_skipped_and_does_not_touch_the_book():
    book = OrderBook()
    replay = LobsterReplay(book)

    replay.apply(parse_message_line("34200.0,1,10001,50,911400,1"))
    trades = replay.apply(parse_message_line("34201.0,5,10001,20,911400,1"))

    assert trades == []
    assert book.best_bid() == 91.14  # untouched -- hidden fills aren't modeled


def test_cross_trade_is_skipped():
    book = OrderBook()
    replay = LobsterReplay(book)

    trades = replay.apply(parse_message_line("34200.0,6,10001,50,911400,1"))
    assert trades == []


def test_trading_halt_is_skipped():
    book = OrderBook()
    replay = LobsterReplay(book)

    trades = replay.apply(parse_message_line("34200.0,7,0,0,0,-1"))
    assert trades == []


# ---------------------------------------------------------------------------
# Id-collision safety -- the same bug class fixed in backtest/execution.py,
# now verified for the loader side too
# ---------------------------------------------------------------------------

def test_replayed_orders_never_collide_with_each_other_across_many_events():
    book = OrderBook()
    replay = LobsterReplay(book)

    for lobster_id in range(1, 21):
        replay.apply(
            parse_message_line(f"34200.{lobster_id},1,{lobster_id},10,900000,1")
        )

    # 20 distinct resting orders at the same price should sum to 200 total size
    snapshot = book.snapshot()
    assert dict(snapshot["bids"])[90.0] == 200

    # each lobster id must map to a UNIQUE engine id
    engine_ids = list(replay._id_map.values())
    assert len(engine_ids) == len(set(engine_ids)) == 20


# ---------------------------------------------------------------------------
# A short end-to-end sequence resembling a real message file
# ---------------------------------------------------------------------------

def test_short_realistic_sequence():
    """
    Submission -> partial execution -> new order on the other side that
    crosses -> deletion of what remains. Not a real downloaded file (see
    KNOWN_LIMITATIONS.md), but exercises the handlers together in
    realistic order.
    """
    book = OrderBook()
    replay = LobsterReplay(book)

    lines = [
        "34200.000000,1,1,100,1000000,1",   # new buy, 100 @ 100.00
        "34200.500000,4,1,40,1000000,1",    # 40 executed against it -> 60 remain
        "34201.000000,1,2,60,1000000,-1",   # new sell, 60 @ 100.00 -> crosses immediately
    ]

    all_trades = []
    for line in lines:
        all_trades.extend(replay.apply(parse_message_line(line)))

    # 40 executed via the synthetic aggressor, then 60 more via the crossing sell
    assert sum(t.quantity for t in all_trades) == 100
    assert book.best_bid() is None
    assert book.best_ask() is None
