"""The real engine, driven through EngineAdapter, must reproduce a synthetic LOBSTER day."""
from backtest.engine_adapter import EngineAdapter
from backtest.lobster import replay, replay_windowed
from backtest.reference_book import ReferenceBook
from backtest.synth import LEVELS, deep_day, synth_day


def test_engine_reproduces_synthetic_day():
    msgs, snaps = synth_day()
    adapter = EngineAdapter()
    rep = replay(msgs, snaps, adapter, LEVELS)
    assert rep.ok, rep.summary()
    assert adapter.crossed == 0


def test_engine_reproduces_day_with_deep_preexisting_liquidity():
    msgs, snaps = deep_day()
    adapter = EngineAdapter()
    rep = replay(msgs, snaps, adapter, LEVELS)
    assert rep.ok, rep.summary()
    assert adapter.crossed == 0


def test_engine_mid_day_start():
    msgs, snaps = synth_day()
    adapter = EngineAdapter()
    rep = replay(msgs, snaps, adapter, LEVELS, start_index=1500)
    assert rep.ok, rep.summary()
    assert adapter.crossed == 0


def test_engine_windowed_matches_reference_exactly():
    msgs, snaps = deep_day()
    eng = replay_windowed(msgs, snaps, EngineAdapter, LEVELS, window=200)
    ref = replay_windowed(msgs, snaps, ReferenceBook, LEVELS, window=200)
    assert eng.prefix_match == ref.prefix_match and eng.n_checked == ref.n_checked


def test_engine_covered_rows_match_reference():
    msgs, snaps = deep_day()
    eng = replay_windowed(msgs, snaps, EngineAdapter, LEVELS, window=500)
    ref = replay_windowed(msgs, snaps, ReferenceBook, LEVELS, window=500)
    assert eng.prefix_valid == ref.prefix_valid
    assert eng.prefix_valid_match == ref.prefix_valid_match == ref.prefix_valid
