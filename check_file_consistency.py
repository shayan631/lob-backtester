"""Does the LOBSTER orderbook file agree with its own message file? (no replay, no state)

For every row i, compare snapshot i-1 -> i. A message may only change the size at ITS OWN
(side, price): submit +size, cancel/delete/visible-exec -size, types 5/6/7 nothing.
Any price visible in both rows whose size changed by something else is a 'silent change'.

    python check_file_consistency.py MESSAGE.csv ORDERBOOK.csv
    python check_file_consistency.py MESSAGE.csv ORDERBOOK.csv --watch 5849900 bid 570 850
"""
import sys
from collections import Counter, defaultdict

from backtest.lobster import ASK_EMPTY, BID_EMPTY, load_messages, load_orderbook

TYPES = {1: "submit", 2: "partial-cancel", 3: "delete", 4: "exec-visible",
         5: "exec-hidden", 6: "cross", 7: "halt"}
msgs = load_messages(sys.argv[1])
snaps, levels = load_orderbook(sys.argv[2])
A = snaps.to_numpy()
M = list(msgs.itertuples(index=False, name=None))


def row_dicts(row):
    asks, bids = {}, {}
    for l in range(levels):
        ap, asz, bp, bsz = row[4 * l: 4 * l + 4]
        if ap != ASK_EMPTY and asz > 0:
            asks[int(ap)] = int(asz)
        if bp != BID_EMPTY and bsz > 0:
            bids[int(bp)] = int(bsz)
    return asks, bids


own_ok = Counter()
own_bad = defaultdict(list)
silent = []
prev = row_dicts(A[0])
for i in range(1, len(M)):
    cur = row_dicts(A[i])
    _, typ, oid, size, px, d = M[i]
    own_side = 1 if d == 1 else 0           # index 1 = bids, 0 = asks
    exp = {1: size, 2: -size, 3: -size, 4: -size}.get(typ, 0)
    for side in (0, 1):
        p, c = prev[side], cur[side]
        for price in p.keys() & c.keys():
            delta = c[price] - p[price]
            if side == own_side and price == px:
                if delta == exp:
                    own_ok[typ] += 1
                else:
                    own_bad[typ].append((i, size, exp, delta))
            elif delta != 0:
                silent.append((i, M[i], "bid" if side else "ask", price, delta))
    prev = cur

print(f"rows compared: {len(M) - 1}")
print("\nAt the message's own price (visible in both rows): size change vs message")
for typ in sorted(set(own_ok) | set(own_bad)):
    bad = own_bad.get(typ, [])
    print(f"  {TYPES.get(typ, typ):15s} ok={own_ok[typ]:7d} bad={len(bad):6d}"
          + (f"   e.g. (idx, msg size, expected delta, actual delta) = {bad[:3]}" if bad else ""))
print(f"\nSILENT CHANGES (a different visible price changed size): {len(silent)}")
for i, msg, side, price, delta in silent[:10]:
    print(f"  idx {i}: msg={msg} -> {side} {price} changed by {delta}")

if "--watch" in sys.argv:
    k = sys.argv.index("--watch")
    wp, wside, lo, hi = int(sys.argv[k + 1]), sys.argv[k + 2], int(sys.argv[k + 3]), int(sys.argv[k + 4])
    print(f"\nexchange size at {wside} {wp}, rows {lo}-{hi} (only rows where it is visible and changed)")
    last = None
    for i in range(lo, hi + 1):
        a, b = row_dicts(A[i])
        sz = (b if wside == "bid" else a).get(wp)
        if sz != last:
            print(f"  idx {i}: {'not visible' if sz is None else sz}   msg={M[i]}")
            last = sz
