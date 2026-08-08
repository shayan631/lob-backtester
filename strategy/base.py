"""
Base strategy interface.

This defines the contract every strategy implements. It's written against a
*reasonable guess* of what the matching engine (engine/) will expose, based
on the README:

    - order book has price-time priority
    - backtest is event-driven (replay loop feeds events one at a time)

When the real engine lands, only the adapter glue (how OrderBook/Order are
constructed, how submit_order() talks to the matching engine) should need to
change. The strategy subclasses below should not need to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class BookSnapshot:
    """
    Minimal view of the order book a strategy needs at each event.
    Adjust field names once the real engine/ module defines its own types —
    this is the seam to adapt.
    """
    timestamp: float
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid


@dataclass
class Fill:
    timestamp: float
    side: Side
    price: float
    qty: float


class ExecutionClient(ABC):
    """
    Whatever the backtest/replay loop injects into a strategy so it can act.
    A toy implementation is in toy_book.py; the real one will eventually
    wrap engine/'s matching engine.
    """

    @abstractmethod
    def submit_order(self, side: Side, price: float, qty: float) -> str:
        """Submit a limit order. Returns an order id."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel a resting order."""

    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel all of this strategy's resting orders."""


class Strategy(ABC):
    """
    Subclass this. The backtest loop will call on_book_update() every time
    the order book changes, and on_fill() whenever one of your orders gets
    matched.
    """

    def __init__(self, execution: ExecutionClient):
        self.execution = execution
        self.position: float = 0.0
        self.cash: float = 0.0
        self.fills: list[Fill] = []

    @abstractmethod
    def on_book_update(self, book: BookSnapshot) -> None:
        """Called on every book event. Put your quoting/trading logic here."""

    def on_fill(self, fill: Fill) -> None:
        """Default fill handling: track position and cash. Override/extend as needed."""
        self.fills.append(fill)
        if fill.side == Side.BUY:
            self.position += fill.qty
            self.cash -= fill.price * fill.qty
        else:
            self.position -= fill.qty
            self.cash += fill.price * fill.qty

    def mark_to_market(self, mid_price: float) -> float:
        """PnL if you closed the position right now at mid_price."""
        return self.cash + self.position * mid_price
