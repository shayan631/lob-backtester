"""Print the full message history of given order ids, and scan the whole file for
two suspicious patterns:
  A) a type-1 submit for an order id that is still live (id reuse / modification)
  B) a cancel/execution whose price or side differs from the order's submit

    python oid_history.py MESSAGE.csv 17478659 [more ids...]
"""
import sys

from backtest.lobster import load_messages

msgs = load_messages(sys.argv[1])
ids = [int(x) for x in sys.argv[2:]]
TYPES = {1: "submit", 2: "partial-cancel", 3: "delete", 4: "exec-visible",
         5: "exec-hidden", 6: "cross", 7: "halt"}

for oid in ids:
    sub = msgs[msgs["order_id"] == oid]
    print(f"\n=== order id {oid}: {len(sub)} messages")
    for idx, r in sub.iterrows():
        print(f"  [{idx}] t={r["time"]:.6f} {TYPES.get(int(r["type"]), r["type"])} size={int(r["size"])} "
              f"px={int(r["price"])} side={int(r["direction"])}")

remaining, born = {}, {}
dup_live, px_mismatch = [], []
for idx, (t, typ, oid, size, px, d) in enumerate(msgs.itertuples(index=False, name=None)):
    if typ == 1:
        if remaining.get(oid, 0) > 0:
            dup_live.append((idx, oid, born[oid], (size, px, d)))
        remaining[oid] = size
        born[oid] = (idx, size, px, d)
    elif typ in (2, 3, 4) and oid in born:
        if (px, d) != born[oid][2:]:
            px_mismatch.append((idx, oid, born[oid], (typ, size, px, d)))
        remaining[oid] = 0 if typ == 3 else remaining[oid] - size

print(f"\nA) submits for an order id that was still live: {len(dup_live)}")
for idx, oid, first, now in dup_live[:8]:
    print(f"  [{idx}] oid={oid}: first (idx,size,px,side)={first}  again (size,px,side)={now}")
print(f"\nB) cancel/exec whose price or side differs from its submit: {len(px_mismatch)}")
for idx, oid, first, now in px_mismatch[:8]:
    print(f"  [{idx}] oid={oid}: submit (idx,size,px,side)={first}  this (type,size,px,side)={now}")
