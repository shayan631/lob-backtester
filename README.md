# LOB Backtester

A limit order book (LOB) matching engine and event-driven backtester,
built to test market-making strategies against real historical tick data.

## Status
Early development — matching engine in progress.

## Why this project
Market-making sits at the core of how exchanges provide liquidity.
This project is our attempt to build the matching engine and backtesting logic from the ground up,
rather than relying on someone else's implementation.

## Architecture
- `engine/` — order book with price-time priority matching (engine-core branch)
- `backtest/` — adapter between `strategy/` and `engine/` (`EngineExecutionClient`),
  plus the eventual event-driven replay loop
- `strategy/` — market-making strategy logic, plus `toy_book.py` (a fake
  execution client for developing strategies before the real engine lands)
- `data/` — LOBSTER sample data (not committed — see setup)

`backtest/execution.py` is written against engine-core's `OrderBook`
interface as a duck-typed contract rather than a hard import, since
`engine/` isn't on this branch yet. Once engine-core merges in, swap the
shim `_EngineOrder` for the real `engine.order.Order` — nothing else in
that file should need to change. See its docstring for details.

## Setup
Coming soon

## Team
Built collaboratively by two UBC students — matching engine and
backtest/strategy split between contributors.