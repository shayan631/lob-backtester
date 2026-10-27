"""Find WHY the replay first disagrees with the exchange.

    python diagnose_lobster.py MESSAGE.csv ORDERBOOK.csv --start-time 34205 --no-heal

Replays until the first mismatch, then prints:
  * the levels that disagree,
  * every message that touched each disagreeing price (with whether its order id
    was known to us, or had to be resolved through the seeded aggregate),
  * every unresolved message before the mismatch,
  * the last few messages before it.
Paste the whole output back.
"""
import argparse
from collections import defaultdict

import numpy as np

from backtest.lobster import BUY, SELL, load_messages, load_orderbook, normalize_row, replay
from backtest.reference_book import ReferenceBook

TYPES = {1: "submit", 2: "partial-cancel", 3: "delete", 4: "exec-visible",
         5: "exec-hidden", 6: "cross", 7: "halt"}

p = argparse.ArgumentParser()
p.add_argument("messages")
p.add_argument("orderbook")
p.add_argument("--start-time", type=float, default=None)
p.add_argument("--no-heal", action="store_true")
p.add_argument("--context", type=int, default=8)
a = p.parse_args()

msgs = load_messages(a.messages)
snaps, levels = load_orderbook(a.orderbook)
start = 0 if a.start_time is None else int(np.searchsorted(msgs["time"].to_numpy(), a.start_time))
seed_row = normalize_row(snaps.to_numpy()[start], levels)
seed_size = {}
for ap, asz, bp, bsz in seed_row:
    if ap is not None:
        seed_size[(SELL, ap)] = asz
    if bp is not None:
        seed_size[(BUY, bp)] = bsz
print(f"seed index {start} (t={msgs['time'].iloc[start]:.6f}); heal={'off' if a.no_heal else 'on'}")

snap_arr = snaps.to_numpy()
touch = defaultdict(list)   # (side, price) -> [(i, msg, status)]
unresolved, heals, recent = [], [], []


def exch_size(i, side, price):
    row = snap_arr[i]
    for lv in range(levels):
        if side == SELL and row[4 * lv] == price:
            return int(row[4 * lv + 1])
        if side == BUY and row[4 * lv + 2] == price:
            return int(row[4 * lv + 3])
    return "beyond top 10"


def trace(event, **k):
    if event == "apply":
        i, msg, status = k["i"], k["msg"], k["status"]
        touch[(msg[5], msg[4])].append((i, msg, status))
        recent.append((i, msg, status))
        if len(recent) > a.context:
            recent.pop(0)
        if status != "ok":
            unresolved.append((i, msg, status))
    elif event == "heal":
        heals.append(k)
        cur = recent[-1][0] if recent else start
        touch[(k["side"], k["price"])].append((cur, ("HEAL", k["short"], k["ours"]), "heal"))


rep = replay(msgs, snaps, ReferenceBook(), levels, start_index=start,
             heal=not a.no_heal, stop_on_mismatch=True, trace=trace)

if not rep.first_mismatches:
    print("no mismatch found:", rep.summary())
    raise SystemExit

m = rep.first_mismatches[0]
print(f"\nFIRST MISMATCH at idx {m.index}: msg={m.message}")
keys = set()
for i, e, g in m.diffs:
    print(f"  level {i + 1}: exchange={e} ours={g}")
    if e[:2] != g[:2]:
        keys |= {(SELL, e[0]), (SELL, g[0])}
    if e[2:] != g[2:]:
        keys |= {(BUY, e[2]), (BUY, g[2])}
keys = {k for k in keys if k[1] is not None}

for side, price in sorted(keys, key=lambda k: (k[0], k[1])):
    name = "ASK" if side == SELL else "BID"
    print(f"\n--- {name} {price}: size at seed = {seed_size.get((side, price), 'not in seeded levels')}")
    for i, msg, status in touch[(side, price)][-30:]:
        ex = exch_size(i, side, price)
        if status == "heal":
            print(f"  [{i}] HEAL: added {msg[1]} shares (ours had {msg[2]})   exchange shows {ex}")
        else:
            print(f"  [{i}] {TYPES.get(msg[1], msg[1])} oid={msg[2]} size={msg[3]} -> {status}   exchange shows {ex}")
    print(f"  [{m.index}] (mismatch)   exchange shows {exch_size(m.index, side, price)}")

print(f"\nunresolved messages before the mismatch: {len(unresolved)}")
for i, msg, status in unresolved[:15]:
    print(f"  [{i}] {TYPES.get(msg[1], msg[1])} oid={msg[2]} size={msg[3]} px={msg[4]} side={msg[5]} -> {status}")
print(f"heals before the mismatch: {len(heals)}")
print("\nlast messages before the mismatch:")
for i, msg, status in recent:
    print(f"  [{i}] {msg} -> {status}")
