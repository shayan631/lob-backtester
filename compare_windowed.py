"""A/B on the same windows: oid-based replay (ReferenceBook) vs a pure price->size replay.

    python compare_windowed.py MESSAGE.csv ORDERBOOK.csv [--window 500] [--examples 5]

The aggregate replay ignores order ids entirely: submit adds size at (side, price),
cancel/delete/visible-exec subtracts it. If it matches the exchange far better than the
oid-based replay, the bug is in the oid bookkeeping, and the examples show where.
"""
import argparse

from backtest.lobster import BUY, SELL, load_messages, load_orderbook, normalize_row, replay
from backtest.reference_book import ReferenceBook

KS = (1, 3, 5, 10)
TYPES = {1: "submit", 2: "partial-cancel", 3: "delete", 4: "exec-visible",
         5: "exec-hidden", 6: "cross", 7: "halt"}

p = argparse.ArgumentParser()
p.add_argument("messages")
p.add_argument("orderbook")
p.add_argument("--window", type=int, default=500)
p.add_argument("--examples", type=int, default=5)
a = p.parse_args()

msgs = load_messages(a.messages)
snaps, levels = load_orderbook(a.orderbook)


def agg_levels(agg):
    asks = sorted(agg[SELL].items())
    bids = sorted(agg[BUY].items(), reverse=True)
    out = []
    for i in range(levels):
        ap, asz = asks[i] if i < len(asks) else (None, 0)
        bp, bsz = bids[i] if i < len(bids) else (None, 0)
        out.append((ap, asz, bp, bsz))
    return out


ref_match = dict.fromkeys(KS, 0)
agg_match = dict.fromkeys(KS, 0)
checked = ref_bad_agg_ok = agg_bad_ref_ok = 0
examples = []

for w in range(0, len(msgs) - 1, a.window):
    m = msgs.iloc[w: w + a.window + 1].reset_index(drop=True)
    s = snaps.iloc[w: w + a.window + 1].reset_index(drop=True)
    if len(m) < 2:
        break
    S = s.to_numpy()
    agg = {BUY: {}, SELL: {}}
    for ap, asz, bp, bsz in normalize_row(S[0], levels):
        if ap is not None:
            agg[SELL][ap] = asz
        if bp is not None:
            agg[BUY][bp] = bsz
    hist = []

    def cb(event, w=w, m=m, S=S, agg=agg, hist=hist, **k):
        global checked, ref_bad_agg_ok, agg_bad_ref_ok
        if event == "apply":
            _, typ, oid, size, px, d = k["msg"]
            side = BUY if d == 1 else SELL
            if typ == 1:
                agg[side][px] = agg[side].get(px, 0) + size
            elif typ in (2, 3, 4):
                left = agg[side].get(px, 0) - size
                if left > 0:
                    agg[side][px] = left
                else:
                    agg[side].pop(px, None)
            hist.append((k["i"], k["msg"], k["status"]))
            del hist[:-6]
        elif event == "check":
            i = k["i"]
            exp = normalize_row(S[i], levels)
            got = agg_levels(agg)
            j = next((x for x, (e, g) in enumerate(zip(exp, got)) if e != g), levels)
            for K in KS:
                if j >= K:
                    agg_match[K] += 1
            checked += 1
            ref_bad = any(d[0] == 0 for d in k["diffs"])
            if ref_bad and j >= 1:
                ref_bad_agg_ok += 1
                if len(examples) < a.examples:
                    examples.append((w + i, k["diffs"][:2], list(hist)))
            if not ref_bad and j < 1:
                agg_bad_ref_ok += 1

    r = replay(m, s, ReferenceBook(), levels, heal=False, prefix_ks=KS, max_mismatches=0, trace=cb)
    for K, c in r.prefix_match.items():
        ref_match[K] += c


def rates(d):
    return ", ".join(f"top-{K}: {d[K] / checked:.4%}" for K in KS)


print(f"rows checked: {checked}  (window={a.window})")
print(f"oid-based replay   : {rates(ref_match)}")
print(f"aggregate replay   : {rates(agg_match)}")
print(f"rows where oid-based top-1 is WRONG but aggregate top-1 is right: {ref_bad_agg_ok}")
print(f"rows where aggregate top-1 is WRONG but oid-based is right:       {agg_bad_ref_ok}")
for idx, diffs, hist in examples:
    print(f"\nexample at global row {idx}:")
    for lv, e, g in diffs:
        print(f"  level {lv + 1}: exchange={e} oid-based={g}")
    for i, msg, status in hist:
        print(f"    [{i}] {TYPES.get(msg[1], msg[1])} oid={msg[2]} size={msg[3]} px={msg[4]} side={msg[5]} -> {status}")
