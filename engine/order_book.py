"""Limit order book with price-time priority matching.
 
Design:
- Each direction of trade (bids, asks) is a SortedDict: price -> deque of resting orders
  at that price, in arrival order (deque gives O(1) popleft/append, which
  is what gives us time priority within a price level for free).
- SortedDict keeps price levels sorted, so the best price is always at
  one end: bids.peekitem(-1) is the highest bid, asks.peekitem(0) is the
  lowest ask.
- A separate order_locations dict maps order_id -> (side, price), so
  cancel_order is O(1) to find the right price level instead of scanning
  the whole book.
"""

from collections import deque
from sortedcontainers import SortedDict
from .order import Order, Trade

class OrderBook:
    def __init__(self):
        self.bids: SortedDict = SortedDict()
        self.asks: SortedDict = SortedDict()
        self.order_locations: dict[int, tuple[str, float]] = {}
 
    # public API
 
    def add_limit_order(self, order: Order) -> list[Trade]:
        """Submit a limit order. Matches against the opposite side first;
        whatever quantity remains (if any) rests on the book.
        Returns the list of trades generated, if any.
        """
        trades: list[Trade] = []
        opposite = self.asks if order.side == "buy" else self.bids
 
        while order.remaining_quantity > 0 and opposite and self._crosses(order, opposite):
            best_price = opposite.peekitem(0)[0] if order.side == "buy" else opposite.peekitem(-1)[0]
            level = opposite[best_price]
            resting = level[0]  # oldest order
 
            fill_qty = min(order.remaining_quantity, resting.remaining_quantity)
            order.remaining_quantity -= fill_qty
            resting.remaining_quantity -= fill_qty
 
            buy_id = order.id if order.side == "buy" else resting.id
            sell_id = resting.id if order.side == "buy" else order.id
            trades.append(Trade(buy_id, sell_id, best_price, fill_qty, order.timestamp))
 
            if resting.is_filled:
                level.popleft()
                del self.order_locations[resting.id]
                if not level:
                    del opposite[best_price]
            # if resting isn't filled, it stays at the front of the deque
            # (still first in line at this price level)
 
        if order.remaining_quantity > 0:
            self._rest(order)
 
        return trades
 
    def cancel_order(self, order_id: int) -> bool:
        """Remove a resting order from the book. Returns True if it was
        found and cancelled, False if it didn't exist (already filled,
        already cancelled, or never existed).
        """
        location = self.order_locations.get(order_id)
        if location is None:
            return False
 
        side, price = location
        book = self.bids if side == "buy" else self.asks
        level = book[price]
 
        for i, o in enumerate(level):
            if o.id == order_id:
                del level[i]
                break
 
        if not level:
            del book[price]
        del self.order_locations[order_id]
        return True
 
    def best_bid(self) -> float | None:
        return self.bids.peekitem(-1)[0] if self.bids else None
 
    def best_ask(self) -> float | None:
        return self.asks.peekitem(0)[0] if self.asks else None
 
    def snapshot(self, depth: int = 5) -> dict:
        """Top-`depth` price levels on each side, with total resting
        quantity at each level. for debugging.
        """
        # TODO: O(n) bc of list(self.bids.items()), not efficient for larger queues
        bid_levels = list(self.bids.items())[::-1][:depth]
        ask_levels = list(self.asks.items())[:depth]
        return {
            "bids": [(price, sum(o.remaining_quantity for o in q)) for price, q in bid_levels],
            "asks": [(price, sum(o.remaining_quantity for o in q)) for price, q in ask_levels],
        }
 
    # internals 
 
    def _crosses(self, order: Order, opposite: SortedDict) -> bool:
        if order.side == "buy":
            return order.price >= opposite.peekitem(0)[0]
        else:
            return order.price <= opposite.peekitem(-1)[0]
 
    def _rest(self, order: Order) -> None:
        book = self.bids if order.side == "buy" else self.asks
        if order.price not in book:
            book[order.price] = deque()
        book[order.price].append(order)
        self.order_locations[order.id] = (order.side, order.price)