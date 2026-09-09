from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from model_prediction.dashboard import common


def test_simultaneous_cache_misses_build_once():
    key = "optimization-concurrency-test"
    common._CACHE.pop(key, None)
    barrier = Barrier(6)
    calls = []
    lock = Lock()
    release = Event()

    def builder():
        with lock:
            calls.append(1)
        release.wait(2)
        return {"value": 42}

    def request():
        barrier.wait(timeout=5)
        return common._cached(key, 30, builder)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(request) for _ in range(6)]
        results = [future.result(timeout=6) for future in futures]
    assert len(calls) == 1
    assert all(result is results[0] for result in results)
    common._CACHE.pop(key, None)


def test_cache_failure_can_retry_and_unrelated_keys_do_not_block():
    key = "optimization-cache-retry"
    common._CACHE.pop(key, None)
    with pytest.raises(ValueError):
        common._cached(key, 30, lambda: (_ for _ in ()).throw(ValueError("failed")))
    assert common._cached(key, 30, lambda: "recovered") == "recovered"
    outer = "optimization-cache-outer"
    inner = "optimization-cache-inner"
    for item in (outer, inner):
        common._CACHE.pop(item, None)
    assert common._cached(outer, 30, lambda: common._cached(inner, 30, lambda: 1)) == 1
    for item in (key, outer, inner):
        common._CACHE.pop(item, None)
