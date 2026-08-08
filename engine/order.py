"""data type for order and trades"""

from dataclasses import dataclass
from itertools import count

_id_counter = count(1)

def next_order_id() -> int:
    """helper for unique order id generation"""
    return next(_id_counter)

@dataclass
class Order:
    """ A single limit order.
    
    remaining_quantity starts equal to quantity and is decremented as the
    order gets filled (fully or partially) by the matching engine.
    """

    id: int
    side: str
    price: float
    quantity: int
    timestamp: int
    remaining_quantity: int = None

    def __post_init__(self):
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.remaining_quantity is None:
            self.remaining_quantity = self.quantity
 
    @property
    def is_filled(self) -> bool:
        return self.remaining_quantity <= 0
    
@dataclass
class Trade:
    """A single trade resulting from an order matched in the book."""
 
    buy_order_id: int
    sell_order_id: int
    price: float
    quantity: int
    timestamp: int