"""
LOBSTER message file parsing + replay driver.

Turns a LOBSTER message file into events fed into a real engine.OrderBook.
See KNOWN_LIMITATIONS.md at the repo root for the full writeup of the
deliberate simplifications made here -- summarized inline below too.

LOBSTER message file format (no header row), verified against LOBSTER's
own public sample data and documentation:

    Time, Type, Order ID, Size, Price, Direction

Event types:
    1 = New limit order submission
    2 = Cancellation (PARTIAL -- reduces size, doesn't remove the order)
    3 = Deletion (TOTAL -- fully removes the order)
    4 = Execution of a visible limit order
    5 = Execution of a hidden limit order
    6 = Cross/auction trade
    7 = Trading halt

Price is stored as an integer, dollar price * 10000 (e.g. raw 911400
means $91.14) -- easy to forget and silently produces prices 10000x too
large if you do.

Direction: 1 = the AFFECTED order is a buy (bid side), -1 = sell (ask
side). For execution events specifically, direction describes the
RESTING order that got hit, not the trade's aggressor -- direction=1 on
an execution means a resting buy order got executed, i.e. an incoming
SELL crossed it. Easy to misread as "a buy trade happened".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from engine import Order, OrderBook, Trade, next_order_id

EVENT_NEW = 1
EVENT_PARTIAL_CANCEL = 2
EVENT_DELETE = 3
EVENT_EXECUTE_VISIBLE = 4
EVENT_EXECUTE_HIDDEN = 5
EVENT_CROSS_TRADE = 6
EVENT_HALT = 7

_PRICE_SCALE = 10_000


@dataclass
class LobsterEvent:
    """One parsed row of a LOBSTER message file. price is already scaled
    to dollars -- the raw-integer/10000 conversion happens in parse_message_line."""

    time: float
    event_type: int
    lobster_order_id: int
    size: int
    price: float
    direction: int  # 1 = affected order is a buy, -1 = sell


def parse_message_line(line: str) -> LobsterEvent:
    """
    Parse one line of a LOBSTER message file (comma-separated, no header,
    exactly 6 columns: Time, Type, Order ID, Size, Price, Direction).
    """
    parts = line.strip().split(",")
    if len(parts) != 6:
        raise ValueError(f"expected 6 columns, got {len(parts)}: {line!r}")

    time_str, type_str, order_id_str, size_str, price_str, direction_str = parts
    return LobsterEvent(
        time=float(time_str),
        event_type=int(type_str),
        lobster_order_id=int(order_id_str),
        size=int(size_str),
        price=int(price_str) / _PRICE_SCALE,
        direction=int(direction_str),
    )


def iter_message_file(path: str) -> Iterator[LobsterEvent]:
    """Lazily parse a LOBSTER message file, one LobsterEvent per line."""
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield parse_message_line(line)


class LobsterReplay:
    """
    Feeds a stream of LobsterEvents into a real engine.OrderBook.

    Translates LOBSTER's own order ids into engine-assigned ids via
    next_order_id() -- the SAME shared id source EngineExecutionClient
    uses -- so a replayed historical order can never collide with an id
    a strategy generates on its own (see backtest/execution.py's
    docstring for the collision bug this pattern exists to prevent).

    KNOWN, DISCLOSED LIMITATIONS -- see KNOWN_LIMITATIONS.md for the full
    writeup. Summary:
      - Type 2 (partial cancel) is treated the same as type 3 (full
        deletion): OrderBook.cancel_order only supports removing an order
        entirely, so a partial cancel just removes the whole thing. This
        makes replayed resting size slightly wrong vs. the real
        historical book. TODO: revisit if/when the engine gets a
        reduce_order() method.
      - Type 5 (hidden execution) is skipped entirely -- the engine has
        no concept of hidden liquidity. Replay will slightly undercount
        real trading activity.
      - Type 6 (cross/auction trade) is skipped entirely -- not a regular
        continuous-book event; out of scope for v1.
      - Type 4's incoming/aggressor order is RECONSTRUCTED, not something
        LOBSTER hands over directly. LOBSTER only records "this resting
        order got hit for this much, at this price" -- not the order that
        hit it. See _synthetic_aggressor().
      - Type 7 (halt) is skipped entirely -- no trading-halt concept in
        the engine either.
    """

    def __init__(self, order_book: OrderBook):
        self.book = order_book
        # LOBSTER's own order_id -> our engine's order_id
        self._id_map: dict[int, int] = {}

    def apply(self, event: LobsterEvent) -> list[Trade]:
        handler = {
            EVENT_NEW: self._handle_new,
            EVENT_PARTIAL_CANCEL: self._handle_cancel_or_delete,
            EVENT_DELETE: self._handle_cancel_or_delete,
            EVENT_EXECUTE_VISIBLE: self._handle_execute_visible,
            EVENT_EXECUTE_HIDDEN: self._handle_skip,
            EVENT_CROSS_TRADE: self._handle_skip,
            EVENT_HALT: self._handle_skip,
        }.get(event.event_type)

        if handler is None:
            raise ValueError(f"unrecognized LOBSTER event type: {event.event_type}")

        return handler(event)

    # -- handlers -----------------------------------------------------

    def _handle_new(self, event: LobsterEvent) -> list[Trade]:
        engine_id = next_order_id()
        self._id_map[event.lobster_order_id] = engine_id
        side = "buy" if event.direction == 1 else "sell"
        order = Order(
            id=engine_id,
            side=side,
            price=event.price,
            quantity=event.size,
            timestamp=_to_engine_timestamp(event.time),
        )
        return self.book.add_limit_order(order)

    def _handle_cancel_or_delete(self, event: LobsterEvent) -> list[Trade]:
        # NOTE: type 2 (partial cancel) is handled identically to type 3
        # (full delete) here -- see class docstring / KNOWN_LIMITATIONS.md.
        engine_id = self._id_map.get(event.lobster_order_id)
        if engine_id is None:
            # never tracked (fully filled earlier, or arrived before our
            # replay window started) -- nothing to do, not an error
            return []
        self.book.cancel_order(engine_id)
        self._id_map.pop(event.lobster_order_id, None)
        return []

    def _handle_execute_visible(self, event: LobsterEvent) -> list[Trade]:
        # direction describes the RESTING order that got hit, not the
        # trade's aggressor: direction=1 means a resting BUY got executed,
        # i.e. an incoming SELL crossed it.
        resting_side = "buy" if event.direction == 1 else "sell"
        aggressor_side = "sell" if resting_side == "buy" else "buy"

        aggressor = self._synthetic_aggressor(event, aggressor_side)
        trades = self.book.add_limit_order(aggressor)

        # If the resting order was fully filled, the engine already
        # removed it from order_locations. Clean up our id_map too, so a
        # later stray cancel/delete referencing this lobster id is a
        # harmless no-op instead of touching some unrelated order that
        # happens to reuse that engine id later.
        engine_id = self._id_map.get(event.lobster_order_id)
        if engine_id is not None and engine_id not in self.book.order_locations:
            self._id_map.pop(event.lobster_order_id, None)

        return trades

    def _synthetic_aggressor(self, event: LobsterEvent, side: str) -> Order:
        """
        LOBSTER records "this resting order got hit for this much, at
        this price" -- not the incoming order that caused it. This
        reconstructs a plausible aggressor: same price and quantity as
        the reported fill, so it crosses the resting order exactly and
        produces one clean trade. This is an INFERRED modeling choice,
        not raw data straight from LOBSTER.
        """
        return Order(
            id=next_order_id(),
            side=side,
            price=event.price,
            quantity=event.size,
            timestamp=_to_engine_timestamp(event.time),
        )

    def _handle_skip(self, event: LobsterEvent) -> list[Trade]:
        return []


def _to_engine_timestamp(lobster_time: float) -> int:
    """
    LOBSTER's Time column is seconds since midnight, as a float (e.g.
    34200.123456789). The engine wants an int timestamp -- this just
    needs to be monotonically increasing for orders in file order, not
    a real epoch time, so microsecond-scale integer is fine.
    """
    return int(lobster_time * 1_000_000)
