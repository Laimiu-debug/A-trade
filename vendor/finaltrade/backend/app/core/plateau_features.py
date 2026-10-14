"""Bounded feature reuse within one parameter scan; never cache trade outcomes."""
from functools import lru_cache
from copy import deepcopy
from threading import RLock


def feature_readers(build_row, calc_snapshot, profile_hash):
    locks = [RLock() for _ in range(64)]
    @lru_cache(maxsize=8192)
    def row_for(symbol, as_of):
        return build_row(symbol, as_of)

    @lru_cache(maxsize=4096)
    def snapshot_for(symbol, window_days, as_of, profile):
        row = row_for(symbol, as_of)
        return calc_snapshot(row, window_days, as_of_date=as_of) if row is not None else {}

    def read_row(symbol, as_of=None):
        return deepcopy(row_for(symbol, as_of))

    def read_snapshot(row, window_days, as_of=None):
        # A caller may enrich a snapshot; never expose the cached mutable object.
        key = (row.symbol, int(window_days), as_of, profile_hash())
        # lru_cache alone allows simultaneous misses to repeat the same expensive work.
        with locks[hash(key) % len(locks)]:
            return deepcopy(snapshot_for(*key))

    return read_row, read_snapshot
