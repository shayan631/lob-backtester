"""
A basic market-making strategy: quote symmetrically around the mid price,
skew quotes based on inventory to mean-revert your position toward zero,
and re-quote whenever the book moves enough to matter.
"""

from strategy.base import Strategy, BookSnapshot, Side


class SimpleMarketMaker(Strategy):
    def __init__(
        self,
        execution,
        half_spread: float = 0.01,
        order_qty: float = 10.0,
        max_position: float = 100.0,
        inventory_skew: float = 0.002,
        requote_threshold: float = 0.005,
    ):
        """
        half_spread:        how far from mid to place each quote (in price units)
        order_qty:          size of each quote
        max_position:       stop adding to a side once |position| exceeds this
        inventory_skew:     how much to shift quotes per unit of inventory,
                             to nudge position back toward flat
        requote_threshold:  minimum mid-price move (in price units) before
                             cancelling and re-placing quotes
        """
        super().__init__(execution)
        self.half_spread = half_spread
        self.order_qty = order_qty
        self.max_position = max_position
        self.inventory_skew = inventory_skew
        self.requote_threshold = requote_threshold

        self._last_quote_mid: float | None = None
        self._bid_order_id: str | None = None
        self._ask_order_id: str | None = None

    def on_book_update(self, book: BookSnapshot) -> None:
        mid = book.mid

        # Skip requoting on tiny moves — avoids order-churn / getting picked off
        if (
            self._last_quote_mid is not None
            and abs(mid - self._last_quote_mid) < self.requote_threshold
        ):
            return

        self._cancel_resting_quotes()

        # Skew quotes against current inventory: if long, lower both quotes
        # to encourage selling; if short, raise both to encourage buying.
        skew = -self.position * self.inventory_skew
        bid_price = mid - self.half_spread + skew
        ask_price = mid + self.half_spread + skew

        if self.position < self.max_position:
            self._bid_order_id = self.execution.submit_order(
                Side.BUY, bid_price, self.order_qty
            )
        if self.position > -self.max_position:
            self._ask_order_id = self.execution.submit_order(
                Side.SELL, ask_price, self.order_qty
            )

        self._last_quote_mid = mid

    def _cancel_resting_quotes(self) -> None:
        if self._bid_order_id is not None:
            self.execution.cancel_order(self._bid_order_id)
            self._bid_order_id = None
        if self._ask_order_id is not None:
            self.execution.cancel_order(self._ask_order_id)
            self._ask_order_id = None
