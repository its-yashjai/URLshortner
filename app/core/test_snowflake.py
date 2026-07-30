"""Unit tests for the Snowflake generator. Run with: pytest app/core/test_snowflake.py"""
import threading
from app.core.snowflake import SnowflakeGenerator, encode_base62, decode_base62, MAX_WORKER_ID


def test_ids_are_monotonically_increasing_single_thread():
    gen = SnowflakeGenerator(worker_id=1)
    ids = [gen.next_id() for _ in range(10_000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_ids_unique_under_concurrency():
    gen = SnowflakeGenerator(worker_id=2)
    n_threads = 16
    ids_per_thread = 2_000
    results = []
    lock = threading.Lock()

    def worker():
        local_ids = [gen.next_id() for _ in range(ids_per_thread)]
        with lock:
            results.extend(local_ids)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == n_threads * ids_per_thread
    assert len(set(results)) == len(results), "collision detected under concurrent generation"


def test_different_workers_never_collide():
    gen_a = SnowflakeGenerator(worker_id=3)
    gen_b = SnowflakeGenerator(worker_id=4)
    ids_a = {gen_a.next_id() for _ in range(5_000)}
    ids_b = {gen_b.next_id() for _ in range(5_000)}
    assert ids_a.isdisjoint(ids_b)


def test_worker_id_bounds_enforced():
    SnowflakeGenerator(worker_id=0)
    SnowflakeGenerator(worker_id=MAX_WORKER_ID)
    try:
        SnowflakeGenerator(worker_id=MAX_WORKER_ID + 1)
        assert False, "should have raised"
    except ValueError:
        pass
    try:
        SnowflakeGenerator(worker_id=-1)
        assert False, "should have raised"
    except ValueError:
        pass


def test_base62_roundtrip():
    for n in [0, 1, 61, 62, 12345, 9_223_372_036_854_775_807]:
        assert decode_base62(encode_base62(n)) == n


def test_base62_is_url_safe_alphanumeric():
    code = encode_base62(123456789)
    assert code.isalnum()
