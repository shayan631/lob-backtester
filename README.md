# LOB Backtester

A limit order book (LOB) matching engine and event-driven backtester,
built to test market-making strategies against real historical tick data.

## Status
Phase 1 complete — real matching engine, strategy layer, and the adapter
between them are all merged and integration-tested together on this branch.

## Why this project
Market-making sits at the core of how exchanges provide liquidity.
This project is our attempt to build the matching engine and backtesting logic from the ground up,
rather than relying on someone else's implementation.

## Architecture
- `engine/` — real order book with price-time priority matching
  (`Order`, `Trade`, `next_order_id`, `OrderBook`). Requires `sortedcontainers`.
- `backtest/` — adapter between `strategy/` and `engine/` (`EngineExecutionClient`,
  `book_snapshot_from_engine`); the event-driven replay loop is the next piece to build
- `strategy/` — market-making strategy logic (`SimpleMarketMaker`), plus
  `toy_book.py` (a fake execution client, still handy for developing
  strategies without touching the real engine)
- `data/` — LOBSTER sample data (not committed — see setup)

`backtest/execution.py` talks to `engine.OrderBook` through `EngineExecutionClient`,
using `engine.next_order_id` as the single shared id source for every order in the
system — strategy-submitted or historical/replayed — so two independent counters can
never collide and silently corrupt `order_locations`. See the module's docstring for
the full story.

`tests/test_integration.py` proves the whole chain end to end against the real
engine (no test doubles): a real `OrderBook`, wired through `EngineExecutionClient`,
correctly driving a real `SimpleMarketMaker`.

## Setup
```
pip install pytest sortedcontainers
python -m pytest -v
```

## Team
Built collaboratively by two UBC students — matching engine and
backtest/strategy split between contributors.