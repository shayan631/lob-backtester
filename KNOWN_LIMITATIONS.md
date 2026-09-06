# Known Limitations

Deliberate, disclosed simplifications in this project -- not bugs. Written
down so they're a documented choice, not a surprise someone finds later.

## Engine (`engine/`)
- No market orders -- only limit orders are supported.
- No FIX protocol or any real exchange connectivity -- this is a
  standalone in-process matching engine.

## LOBSTER loader (`backtest/lobster_loader.py`)

- **Partial cancels (type 2) are treated as full deletions (type 3).**
  `OrderBook.cancel_order` only supports removing an order entirely --
  there's no `reduce_order` / partial-cancel method on the engine yet.
  This means replayed resting size at a price level will sometimes be
  slightly smaller than the real historical book, since a partial cancel
  removes the whole order instead of shrinking it.
  `# TODO`: worth a shared decision with engine-core on whether a
  `reduce_order(order_id, new_quantity)` method is worth adding.

- **Hidden order executions (type 5) are skipped entirely.** The engine
  has no concept of hidden/iceberg liquidity at all -- adding one is a
  real scope expansion, not a quick fix. Skipping these means replay
  slightly undercounts real trading activity that happened in the
  original historical session.

- **Cross/auction trades (type 6) are skipped entirely.** These aren't
  regular continuous-trading-session events (they happen at market
  open/close auctions), and folding them into normal price-time-priority
  matching would need separate handling. Out of scope for v1.

- **Trading halts (type 7) are skipped entirely.** The engine has no
  halt/resume concept.

- **The type-4 (visible execution) aggressor order is RECONSTRUCTED, not
  raw data.** LOBSTER records "this resting order got hit for this much,
  at this price" -- it does NOT hand you the incoming order that caused
  the execution. `LobsterReplay._synthetic_aggressor()` builds a
  plausible incoming order (same price and quantity as the reported
  fill) so it crosses the resting order exactly and produces one clean
  trade. This is a modeling choice, not something LOBSTER guarantees is
  literally what happened on the original exchange.

## Next real-world test

None of the above has been run against an actual LOBSTER file yet --
only against hand-built synthetic message-file-style test data (see
`tests/test_lobster_loader.py`). The AAPL sample from
[lobsterdata.com](https://lobsterdata.com/) (or an equivalent public
mirror) should be run through `LobsterReplay` next to see what real-world
edge case breaks first -- there's usually at least one the docs don't
fully prepare you for.
