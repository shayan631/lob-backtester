"""Plumbing tests for the LOBSTER replay harness (synthetic data only)."""
from backtest.lobster import load_messages, load_orderbook, replay, replay_windowed
from backtest.reference_book import ReferenceBook
from backtest.synth import LEVELS, DropsPartialCancels, deep_day, synth_day


def test_replay_matches_snapshots():
    msgs, snaps = synth_day()
    rep = replay(msgs, snaps, ReferenceBook(), LEVELS)
    assert rep.ok, rep.summary()
    assert rep.n_checked == len(msgs) - 1  # message 0 is skipped when seeding


def test_unseeded_replay_cannot_resolve_pre_open_orders():
    msgs, snaps = synth_day()
    rep = replay(msgs, snaps, ReferenceBook(), LEVELS, seed=False)
    assert rep.unresolved_inside + rep.unresolved_deep > 0 and not rep.ok


def test_validator_catches_a_broken_book():
    msgs, snaps = synth_day()
    rep = replay(msgs, snaps, DropsPartialCancels(), LEVELS)
    assert rep.n_mismatched > 0 and rep.first_mismatches and rep.first_mismatches[0].diffs


def test_csv_roundtrip(tmp_path):
    msgs, snaps = synth_day(n=500)
    mp, op = tmp_path / "m.csv", tmp_path / "o.csv"
    msgs.to_csv(mp, header=False, index=False)
    snaps.to_csv(op, header=False, index=False)
    m2 = load_messages(mp)
    o2, levels = load_orderbook(op)
    assert levels == LEVELS
    assert replay(m2, o2, ReferenceBook(), levels).ok


def test_deep_preexisting_liquidity_breaks_strict_replay_but_heals():
    msgs, snaps = deep_day()
    strict = replay(msgs, snaps, ReferenceBook(), LEVELS, heal=False)
    assert strict.n_mismatched > 0          # this is what the real AAPL run looked like
    healed = replay(msgs, snaps, ReferenceBook(), LEVELS, heal=True)
    assert healed.ok, healed.summary()
    assert healed.healed_levels > 0 and healed.unresolved_deep > 0


def test_healing_does_not_hide_a_broken_book():
    msgs, snaps = deep_day()
    rep = replay(msgs, snaps, DropsPartialCancels(), LEVELS, heal=True)
    assert rep.n_mismatched > 0


def test_replay_can_start_mid_day():
    msgs, snaps = synth_day()
    rep = replay(msgs, snaps, ReferenceBook(), LEVELS, start_index=1500)
    assert rep.ok, rep.summary()
    assert rep.n_checked == len(msgs) - 1501


def test_mid_day_start_with_deep_liquidity():
    msgs, snaps = deep_day()
    rep = replay(msgs, snaps, ReferenceBook(), LEVELS, start_index=3000)
    assert rep.ok, rep.summary()


def test_windowed_top_of_book_is_exact_on_consistent_data():
    msgs, snaps = synth_day()
    rep = replay_windowed(msgs, snaps, ReferenceBook, LEVELS, window=200)
    assert rep.n_windows > 5 and rep.n_checked > 2000
    assert rep.prefix_match[10] == rep.n_checked, rep.summary()


def test_windowed_top5_survives_deep_preexisting_liquidity():
    msgs, snaps = deep_day()
    rep = replay_windowed(msgs, snaps, ReferenceBook, LEVELS, window=200)
    assert rep.prefix_match[5] == rep.n_checked, rep.summary()


def test_windowed_validation_catches_a_broken_book():
    msgs, snaps = synth_day()
    rep = replay_windowed(msgs, snaps, DropsPartialCancels, LEVELS, window=200)
    assert rep.prefix_match[1] < rep.n_checked or rep.prefix_match[5] < rep.n_checked
    assert rep.prefix_match[10] < 0.99 * rep.n_checked


def test_covered_rows_are_exact_even_when_unseeded_depth_surfaces():
    msgs, snaps = deep_day()
    rep = replay_windowed(msgs, snaps, ReferenceBook, LEVELS, window=500)
    assert rep.prefix_match[10] < 0.9 * rep.n_checked          # depth limit hurts the raw rate...
    for K in (1, 3, 5, 10):                                      # ...but never the covered rows
        assert rep.prefix_valid[K] > 0
        assert rep.prefix_valid_match[K] == rep.prefix_valid[K], rep.summary()


def test_covered_rows_still_catch_a_broken_book():
    msgs, snaps = synth_day()
    rep = replay_windowed(msgs, snaps, DropsPartialCancels, LEVELS, window=200)
    assert rep.prefix_valid_match[5] < 0.9 * rep.prefix_valid[5]
