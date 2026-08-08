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
- `engine/` — order book with price-time priority matching
- `backtest/` — event-driven replay loop
- `strategy/` — market-making strategy logic
- `data/` — LOBSTER sample data (not committed — see setup)

## Setup
Coming soon

## Team
Built collaboratively by two UBC students — matching engine and
backtest/strategy split between contributors.