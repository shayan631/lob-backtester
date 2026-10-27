"""Replay a LOBSTER day and compare the book to the exchange after every message.

    python validate_lobster.py data/AAPL_..._message_10.csv data/AAPL_..._orderbook_10.csv
    python validate_lobster.py MSG OB --engine      # test your real engine
"""
import argparse
import sys

from backtest.lobster import load_messages, load_orderbook, replay

p = argparse.ArgumentParser()
p.add_argument("messages")
p.add_argument("orderbook")
p.add_argument("--engine", action="store_true", help="use EngineAdapter instead of ReferenceBook")
p.add_argument("--stop", action="store_true", help="stop at first mismatch")
p.add_argument("--no-heal", action="store_true", help="strict mode: no reconciliation of deep pre-existing liquidity")
p.add_argument("--start-time", type=float, default=None,
               help="seconds after midnight; seed from the first snapshot at/after this time (34200 = 09:30:00)")
p.add_argument("--window", type=int, default=0,
               help="windowed mode: re-seed from the exchange snapshot every N messages (recommended: 500)")
p.add_argument("--check-every", type=int, default=1)
a = p.parse_args()

msgs = load_messages(a.messages)
snaps, levels = load_orderbook(a.orderbook)

start_index = 0
if a.start_time is not None:
    import numpy as np
    start_index = int(np.searchsorted(msgs["time"].to_numpy(), a.start_time))
    print(f"seeding from index {start_index} (t={msgs['time'].iloc[start_index]:.6f}); earlier messages skipped")

if a.engine:
    from backtest.engine_adapter import EngineAdapter
    book = EngineAdapter()
else:
    from backtest.reference_book import ReferenceBook
    book = ReferenceBook()

if a.window > 0:
    from backtest.lobster import replay_windowed
    if a.engine:
        from backtest.engine_adapter import EngineAdapter
        factory = EngineAdapter
    else:
        from backtest.reference_book import ReferenceBook
        factory = ReferenceBook
    rep = replay_windowed(msgs, snaps, factory, levels, window=a.window)
    print(rep.summary())
    sys.exit(0)

rep = replay(msgs, snaps, book, levels, check_every=a.check_every, stop_on_mismatch=a.stop, heal=not a.no_heal, start_index=start_index)
print(rep.summary())
sys.exit(0 if rep.ok else 1)
