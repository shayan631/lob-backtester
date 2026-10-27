"""Synthetic LOBSTER-format days for testing the replay harness.

Generated from ReferenceBook itself, so they prove the plumbing works; they say
nothing about whether an engine agrees with a real exchange.
"""
import random

import pandas as pd

from backtest.lobster import (
    ASK_EMPTY, BID_EMPTY, BUY, SELL, SUBMIT, PARTIAL_CANCEL, DELETE, EXEC_VISIBLE, EXEC_HIDDEN,
)
from backtest.reference_book import ReferenceBook

LEVELS = 10
MID, TICK = 1_000_000, 100  # prices are dollars * 10_000


def _flat(depth):
    row = []
    for ap, asz, bp, bsz in depth:
        row += [ASK_EMPTY if ap is None else ap, asz, BID_EMPTY if bp is None else bp, bsz]
    return row


def synth_day(n=3000, seed=0, pre_depth=5, pre_n=30, band=8):
    rng = random.Random(seed)
    book = ReferenceBook()
    for oid in range(1, pre_n + 1):  # resting before the file starts: no submit message exists
        side = rng.choice([BUY, SELL])
        px = MID + rng.randint(1, pre_depth) * TICK * (1 if side == SELL else -1)
        book.submit_limit(oid, side, px, rng.randint(10, 100))

    msgs, snaps, t, next_id = [], [], 34200.0, 1000

    def emit(typ, oid, size, price, side):
        nonlocal t
        t += 0.001
        msgs.append((t, typ, oid, size, price, side))
        snaps.append(_flat(book.depth(LEVELS)))

    for i in range(n):
        r = rng.random()
        live = list(book.orders.items())
        if i == 0 or r < 0.35 or not live:
            side = rng.choice([BUY, SELL])
            px = MID + rng.randint(1, band) * TICK * (1 if side == SELL else -1)
            sz = rng.randint(10, 100)
            book.submit_limit(next_id, side, px, sz)
            emit(SUBMIT, next_id, sz, px, side)
            next_id += 1
        elif r < 0.60:
            cands = [(o, v) for o, v in live if v[2] > 1]
            if not cands:
                continue
            oid, (side, px, rem) = rng.choice(cands)
            cs = rng.randint(1, rem - 1)
            book.cancel(oid, cs)
            emit(PARTIAL_CANCEL, oid, cs, px, side)
        elif r < 0.75:
            oid, (side, px, rem) = rng.choice(live)
            book.cancel(oid)
            emit(DELETE, oid, rem, px, side)
        elif r < 0.95:
            side = rng.choice([BUY, SELL])
            same = [(o, v) for o, v in live if v[0] == side]
            if not same:
                continue
            best = (min if side == SELL else max)(v[1] for _, v in same)
            oid, (_, px, rem) = rng.choice([(o, v) for o, v in same if v[1] == best])
            es = rng.randint(1, rem)
            book.execute(oid, es)
            emit(EXEC_VISIBLE, oid, es, px, side)
        else:
            emit(EXEC_HIDDEN, 0, 5, MID, rng.choice([BUY, SELL]))
    return pd.DataFrame(msgs, columns=["time", "type", "order_id", "size", "price", "direction"]), \
        pd.DataFrame(snaps)


def deep_day():
    """Pre-existing orders up to 25 ticks away: far more than LOBSTER's 10 visible levels."""
    return synth_day(n=8000, seed=3, pre_depth=25, pre_n=120, band=4)


class DropsPartialCancels(ReferenceBook):
    def cancel(self, order_id, size=None):
        if size is not None and order_id > 0:
            return  # bug: partial cancels ignored
        super().cancel(order_id, size)
