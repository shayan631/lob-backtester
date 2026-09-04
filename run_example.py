"""
Run the SimpleMarketMaker strategy against a synthetic random-walk price
series, using the toy execution client. This proves the strategy logic
works end-to-end before the real matching engine exists.

Usage:
    python run_example.py
"""

import random

from strategy.market_maker import SimpleMarketMaker
from strategy.toy_book import (
    ToyExecutionClient,
    synthetic_mid_price_walk,
    synthetic_trade_price,
)


def main():
    strategy_holder = [None]  # filled in below, see ToyExecutionClient docstring
    execution = ToyExecutionClient(strategy_holder)
    strategy = SimpleMarketMaker(
        execution,
        half_spread=0.05,
        order_qty=10.0,
        max_position=100.0,
    )
    strategy_holder[0] = strategy

    trade_rng = random.Random(42)
    last_mid = None
    for book in synthetic_mid_price_walk(n_steps=500):
        strategy.on_book_update(book)
        # simulate an aggressive trade near mid, to test whether it hits our quotes
        trade_price = synthetic_trade_price(book.mid, trade_rng)
        execution.try_fill(trade_price, book.timestamp)
        last_mid = book.mid

    pnl = strategy.mark_to_market(last_mid)
    print(f"Fills:         {len(strategy.fills)}")
    print(f"Final position:{strategy.position:.2f}")
    print(f"Cash:          {strategy.cash:.2f}")
    print(f"Mark-to-market PnL: {pnl:.2f}")


if __name__ == "__main__":
    main()
