"""LOBSTER loader, replay driver, and book validator.

LOBSTER format (https://lobsterdata.com/info/DataStructure.php):

message file   : time, type, order_id, size, price, direction
orderbook file : ask_px_1, ask_sz_1, bid_px_1, bid_sz_1, ... (one row per message,
                 the book state AFTER that message)

types     : 1 submit | 2 partial cancel | 3 delete | 4 visible execution |
            5 hidden execution | 6 cross trade | 7 trading halt
direction : side of the RESTING order (1 = buy, -1 = sell), also for executions
price     : dollars * 10_000 (kept as int here, never float)
empty lvl : ask price 9_999_999_999 / bid price -9_999_999_999, size 0

Approximation (document it in the README): historical executions are replayed
BY ORDER ID, i.e. "order X lost `size` shares", not by price-time matching. That
keeps the replayed book identical to the tape even when strategy orders sit in
the queue, at the cost of ignoring that a strategy order ahead of X would have
been filled first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import pandas as pd

ASK_EMPTY = 9_999_999_999
BID_EMPTY = -9_999_999_999

SUBMIT, PARTIAL_CANCEL, DELETE, EXEC_VISIBLE, EXEC_HIDDEN, CROSS, HALT = 1, 2, 3, 4, 5, 6, 7
BUY, SELL = 1, -1

Level = tuple  # (ask_px | None, ask_sz, bid_px | None, bid_sz)


class BookAdapter(Protocol):
    """The only surface the replay loop needs from an order book."""

    def submit_limit(self, order_id: int, side: int, price: int, size: int) -> None: ...
    def cancel(self, order_id: int, size: Optional[int] = None) -> None:
        """Remove `size` shares (or the whole order if size is None)."""
    def execute(self, order_id: int, size: int) -> None:
        """Historical execution: order loses `size` shares (full removal at 0)."""
    def has_order(self, order_id: int) -> bool: ...
    def depth(self, levels: int) -> list[Level]:
        """Aggregated book, best level first. None price + 0 size for empty levels."""


# ---------------------------------------------------------------- loading
def load_messages(path: str) -> pd.DataFrame:
    return pd.read_csv(
        path, header=None,
        names=["time", "type", "order_id", "size", "price", "direction"],
    )


def load_orderbook(path: str) -> tuple[pd.DataFrame, int]:
    ncols = pd.read_csv(path, header=None, nrows=1).shape[1]
    levels = ncols // 4
    cols = []
    for i in range(1, levels + 1):
        cols += [f"ask_px_{i}", f"ask_sz_{i}", f"bid_px_{i}", f"bid_sz_{i}"]
    return pd.read_csv(path, header=None, names=cols), levels


def normalize_row(row, levels: int) -> list[Level]:
    out = []
    for i in range(levels):
        ap, asz, bp, bsz = row[4 * i: 4 * i + 4]
        out.append((
            None if ap == ASK_EMPTY or asz == 0 else int(ap), int(asz),
            None if bp == BID_EMPTY or bsz == 0 else int(bp), int(bsz),
        ))
    return out


def normalize_depth(depth: list[Level]) -> list[Level]:
    return [
        (None if asz == 0 else ap, int(asz), None if bsz == 0 else bp, int(bsz))
        for ap, asz, bp, bsz in depth
    ]


# ---------------------------------------------------------------- reporting
@dataclass
class Mismatch:
    index: int
    message: tuple
    diffs: list  # (level_index, expected_level, got_level), differing levels only


@dataclass
class Report:
    n_messages: int = 0
    n_checked: int = 0
    n_mismatched: int = 0
    unresolved_inside: int = 0   # unknown id at a price INSIDE the seeded window: suspicious
    unresolved_deep: int = 0     # unknown id at a price beyond the seeded window: expected
    healed_levels: int = 0       # times unseen deep liquidity was reconciled from the snapshot
    healed_shares: int = 0
    n_windows: int = 0
    prefix_match: dict = field(default_factory=dict)   # K -> rows whose top-K levels match exactly
    prefix_valid: dict = field(default_factory=dict)   # K -> rows where top-K cannot be affected by unseeded depth
    prefix_valid_match: dict = field(default_factory=dict)  # K -> covered rows whose top-K match exactly
    first_mismatches: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.n_mismatched == 0 and self.unresolved_inside == 0

    def summary(self) -> str:
        s = (f"{self.n_messages} messages, {self.n_checked} book checks, "
             f"{self.n_mismatched} mismatches\n"
             f"unresolved ids: {self.unresolved_inside} inside seeded window (suspicious), "
             f"{self.unresolved_deep} beyond it (expected)\n"
             f"healed: {self.healed_levels} level events, {self.healed_shares} shares "
             f"(pre-existing liquidity deeper than the seeded window)")
        if self.prefix_match:
            rate = ", ".join(f"top-{k}: {self.prefix_match.get(k, 0) / max(self.n_checked, 1):.4%}"
                             for k in sorted(self.prefix_match))
            s += f"\nexact-match rate over {self.n_checked} checked rows ({self.n_windows} windows): {rate}"
            cov = "; ".join(
                f"top-{k}: {self.prefix_valid.get(k, 0) / max(self.n_checked, 1):.1%} of rows covered, "
                f"{self.prefix_valid_match.get(k, 0) / max(self.prefix_valid.get(k, 0), 1):.4%} exact"
                for k in sorted(self.prefix_match))
            s += ("\ncovered rows only (top-K could not be affected by levels deeper than the seed): " + cov)
        if self.healed_levels > 100 + 0.05 * max(self.n_checked, 1):
            s += ("\nWARNING: reconciliation is firing on a large share of checks. "
                  "That is not 'deep pre-existing liquidity'; do not trust this run.")
        for m in self.first_mismatches:
            s += f"\n  idx {m.index} msg={m.message}"
            for i, exp, got in m.diffs[:4]:
                s += f"\n    level {i + 1}: expected={exp} got={got}"
        return s


# ---------------------------------------------------------------- replay
OK, DEEP, INSIDE = "ok", "deep", "inside"


def _covered(got: list, K: int, bound: dict) -> bool:
    """True if our top-K cannot be affected by levels deeper than the seed.

    Every level worse than the seed's worst price is unseeded, so if our K-th best
    level on each side is at or inside that price, no unseeded level can belong in
    the top K. A side that had fewer than `levels` levels at seed (bound None) was
    seeded completely, so it is always covered.
    """
    ap, _, bp, _ = got[K - 1]
    ok_ask = bound[SELL] is None or (ap is not None and ap <= bound[SELL])
    ok_bid = bound[BUY] is None or (bp is not None and bp >= bound[BUY])
    return ok_ask and ok_bid


def _diff(expected: list, got: list) -> list:
    return [(i, e, g) for i, (e, g) in enumerate(zip(expected, got)) if e != g]


def _apply(adapter, sy, outside, typ, oid, size, price, side) -> str:
    """Apply one message. Returns OK, DEEP or INSIDE (unresolvable id, and where)."""
    if typ == SUBMIT:
        adapter.submit_limit(oid, side, price, size)
        return OK
    if typ not in (PARTIAL_CANCEL, DELETE, EXEC_VISIBLE):
        return OK  # 5 hidden exec, 6 cross, 7 halt: no effect on the visible book
    if adapter.has_order(oid):
        if typ == EXEC_VISIBLE:
            adapter.execute(oid, size)
        elif typ == PARTIAL_CANCEL:
            adapter.cancel(oid, size)
        else:
            adapter.cancel(oid)
        return OK
    # Order predates the file (or lies deeper than the seed window): it lives
    # inside the synthetic aggregate orders at this (side, price).
    entries = sy.get((side, price), [])
    remaining = size
    for e in entries:
        if remaining <= 0:
            break
        take = min(remaining, e[1])
        if typ == EXEC_VISIBLE:
            adapter.execute(e[0], take)
        else:
            adapter.cancel(e[0], take)
        e[1] -= take
        remaining -= take
    sy[(side, price)] = [e for e in entries if e[1] > 0]
    if remaining <= 0:
        return OK
    return DEEP if outside(side, price) else INSIDE


def replay(
    messages: pd.DataFrame,
    snapshots: pd.DataFrame,
    adapter: BookAdapter,
    levels: int,
    seed: bool = True,
    start_index: int = 0,
    heal: bool = True,
    check_every: int = 1,
    max_mismatches: int = 5,
    stop_on_mismatch: bool = False,
    on_event: Optional[Callable[[int, tuple, BookAdapter], None]] = None,
    trace: Optional[Callable[..., None]] = None,
    prefix_ks: tuple = (),
) -> Report:
    """Replay a LOBSTER day through `adapter`, comparing to the exchange book.

    seed : build the starting book from snapshot `start_index` (one synthetic aggregate
           order per level, negative ids) and replay from the next message, because a
           snapshot is the state AFTER its message. Messages before start_index are
           skipped entirely. Use start_index to avoid periods the message file does
           not describe well, such as the opening cross.
    heal : LOBSTER only shows `levels` levels, so orders resting deeper than that
           at the start cannot be seeded. When such liquidity later surfaces, the
           exchange snapshot shows size we never saw. With heal=True, a SHORTFALL
           at a price worse than every seeded level is added as synthetic
           pre-existing liquidity (and counted in the report). Shortfalls inside
           the seeded window, and any excess size, are never healed: those are
           real errors. Healing weakens the check at deep prices, which is why
           it is reported.
    on_event(i, msg, adapter) runs after each applied message: this is where a
           strategy gets to look at the book and submit orders.
    trace(event, **info) is a debugging hook (see diagnose_lobster.py).
    """
    msgs = list(messages.itertuples(index=False, name=None))
    snaps = snapshots.to_numpy()
    rep = Report(n_messages=len(msgs))
    sy: dict = {}                       # (side, price) -> [[synthetic_id, remaining], ...]
    bound = {BUY: None, SELL: None}     # worst seeded price per side (None = whole side known)
    next_sid = [0]

    def outside(side: int, price: int) -> bool:
        b = bound[side]
        if b is None:
            return False
        return price > b if side == SELL else price < b

    def add_synthetic(side: int, price: int, size: int) -> None:
        next_sid[0] -= 1
        adapter.submit_limit(next_sid[0], side, price, size)
        sy.setdefault((side, price), []).append([next_sid[0], size])

    start = 0
    if seed and len(msgs) > start_index:
        start = start_index + 1
        n_ask = n_bid = 0
        worst_ask = worst_bid = None
        for ap, asz, bp, bsz in normalize_row(snaps[start_index], levels):
            if ap is not None:
                add_synthetic(SELL, ap, asz)
                n_ask, worst_ask = n_ask + 1, ap
            if bp is not None:
                add_synthetic(BUY, bp, bsz)
                n_bid, worst_bid = n_bid + 1, bp
        if n_ask == levels:
            bound[SELL] = worst_ask
        if n_bid == levels:
            bound[BUY] = worst_bid
        if trace:
            trace('seed', bound=dict(bound), asks=n_ask, bids=n_bid)
    else:
        heal = False

    def do_heal(expected: list) -> None:
        deep = {}
        for ap, asz, bp, bsz in normalize_depth(adapter.depth(levels * 2)):
            if ap is not None:
                deep[(SELL, ap)] = asz
            if bp is not None:
                deep[(BUY, bp)] = bsz
        for ap, asz, bp, bsz in expected:
            for side, px, sz in ((SELL, ap, asz), (BUY, bp, bsz)):
                if px is not None and outside(side, px):
                    short = sz - deep.get((side, px), 0)
                    if short > 0:
                        add_synthetic(side, px, short)
                        if trace:
                            trace('heal', side=side, price=px, short=short, ours=deep.get((side, px), 0))
                        rep.healed_levels += 1
                        rep.healed_shares += short

    for i in range(start, len(msgs)):
        msg = msgs[i]
        _, typ, oid, size, price, side = msg
        status = _apply(adapter, sy, outside, typ, oid, size, price, side)
        if status == INSIDE:
            rep.unresolved_inside += 1
        elif status == DEEP:
            rep.unresolved_deep += 1
        if trace:
            trace('apply', i=i, msg=msg, status=status)
        if on_event is not None:
            on_event(i, msg, adapter)
        if i % check_every == 0:
            rep.n_checked += 1
            expected = normalize_row(snaps[i], levels)
            got = normalize_depth(adapter.depth(levels))
            if expected != got and heal:
                do_heal(expected)
                got = normalize_depth(adapter.depth(levels))
            if prefix_ks:
                j = next((k for k, (e, g) in enumerate(zip(expected, got)) if e != g), levels)
                for K in prefix_ks:
                    if j >= K:
                        rep.prefix_match[K] = rep.prefix_match.get(K, 0) + 1
                    if _covered(got, K, bound):
                        rep.prefix_valid[K] = rep.prefix_valid.get(K, 0) + 1
                        if j >= K:
                            rep.prefix_valid_match[K] = rep.prefix_valid_match.get(K, 0) + 1
            if trace:
                trace('check', i=i, diffs=_diff(expected, got))
            if expected != got:
                rep.n_mismatched += 1
                if len(rep.first_mismatches) < max_mismatches:
                    rep.first_mismatches.append(Mismatch(i, msg, _diff(expected, got)))
                if stop_on_mismatch:
                    break
    return rep


def replay_windowed(
    messages: pd.DataFrame,
    snapshots: pd.DataFrame,
    make_adapter: Callable[[], BookAdapter],
    levels: int,
    window: int = 500,
    prefix_ks: tuple = (1, 3, 5, 10),
) -> Report:
    """Re-seed from the exchange snapshot every `window` messages and replay the next
    `window` messages on a FRESH book, comparing after every message.

    Why: the message file does not explain every change deep in the book (an order
    can vanish below the top 10 with no message), so an all-day replay drifts. Each
    window starts from the true state, so such changes only affect the window they
    occur in. Reports the fraction of checked rows whose top-K levels match exactly,
    for each K in prefix_ks. No reconciliation is used.
    """
    total = Report()
    n = len(messages)
    for w in range(0, n - 1, window):
        m = messages.iloc[w: w + window + 1].reset_index(drop=True)
        s = snapshots.iloc[w: w + window + 1].reset_index(drop=True)
        if len(m) < 2:
            break
        r = replay(m, s, make_adapter(), levels, heal=False, prefix_ks=prefix_ks, max_mismatches=0)
        total.n_windows += 1
        total.n_messages += r.n_messages
        total.n_checked += r.n_checked
        total.n_mismatched += r.n_mismatched
        total.unresolved_inside += r.unresolved_inside
        total.unresolved_deep += r.unresolved_deep
        for src, dst in ((r.prefix_match, total.prefix_match),
                         (r.prefix_valid, total.prefix_valid),
                         (r.prefix_valid_match, total.prefix_valid_match)):
            for k, c in src.items():
                dst[k] = dst.get(k, 0) + c
    return total
