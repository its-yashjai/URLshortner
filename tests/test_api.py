"""End-to-end API tests. They need a running Postgres (DATABASE_URL) and
Redis (REDIS_URL); they are skipped automatically if either is unreachable."""
import asyncio

import asyncpg
import pytest
import redis
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app import cache, config, flusher
from app.main import app


def _services_available() -> bool:
    async def probe():
        conn = await asyncpg.connect(config.DATABASE_URL, timeout=2)
        await conn.close()

    try:
        asyncio.run(probe())
        redis.from_url(config.REDIS_URL, socket_timeout=2).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _services_available(), reason="Postgres or Redis not reachable"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # "with" runs the startup/shutdown lifespan
        yield c


def _shorten(client, url="https://example.com/some/long/path"):
    r = client.post("/shorten", json={"long_url": url})
    assert r.status_code == 201
    return r.json()["short_code"]


def test_shorten_redirect_and_count(client):
    code = _shorten(client)
    for _ in range(3):
        r = client.get(f"/{code}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "https://example.com/some/long/path"
    # Clicks are still buffered in Redis, but stats already include them.
    assert client.get(f"/{code}/stats").json()["click_count"] == 3


def test_codes_are_random_and_unique(client):
    codes = [_shorten(client, f"https://example.com/{i}") for i in range(30)]
    assert len(set(codes)) == 30
    assert all(len(c) == config.CODE_LENGTH for c in codes)
    assert codes != sorted(codes)  # not sequential


def test_flush_moves_clicks_to_postgres(client):
    code = _shorten(client)
    for _ in range(5):
        client.get(f"/{code}", follow_redirects=False)

    async def run():
        await flusher.flush_once()

    client.portal.call(run)  # run on the app's own event loop
    row_count = client.portal.call(_db_click_count, code)
    assert row_count == 5
    # After the flush nothing is pending, and stats still say 5.
    assert client.get(f"/{code}/stats").json()["click_count"] == 5


async def _db_click_count(code):
    from app import db
    return await db.pool().fetchval("SELECT click_count FROM urls WHERE short_code=$1", code)


def test_redis_down_falls_back_to_postgres(client, monkeypatch):
    code = _shorten(client)

    async def broken(*args, **kwargs):
        raise RedisError("simulated outage")

    monkeypatch.setattr(cache, "get_cached_url", broken)
    r = client.get(f"/{code}", follow_redirects=False)
    assert r.status_code == 302  # still works, the v1 way
    assert client.portal.call(_db_click_count, code) == 1


def test_invalid_url_rejected(client):
    assert client.post("/shorten", json={"long_url": "not a url"}).status_code == 422


def test_unknown_and_junk_codes_404(client):
    assert client.get("/zzzzzzzzzz", follow_redirects=False).status_code == 404
    assert client.get("/bad-code!", follow_redirects=False).status_code == 404
    assert client.get("/zzzzzzzzzz/stats").status_code == 404


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["redis"] == "ok" and body["instance"]


def test_stats_include_clicks_mid_flush(client):
    """Regression: clicks being saved (in a flushing snapshot) must still
    show in /stats. Found while benchmarking: stats dipped during a flush."""
    code = _shorten(client)
    for _ in range(4):
        client.get(f"/{code}", follow_redirects=False)

    async def start_flush_without_finishing():
        # Step 1 of a flush only: move the live buffer into a snapshot.
        await cache.client().rename(cache.PENDING_CLICKS, cache.FLUSHING_PREFIX + "test")

    client.portal.call(start_flush_without_finishing)
    assert client.get(f"/{code}/stats").json()["click_count"] == 4

    async def finish():
        await flusher.flush_once()

    client.portal.call(finish)
    assert client.get(f"/{code}/stats").json()["click_count"] == 4


def test_only_one_replica_flushes_at_a_time(client):
    """With several replicas, two flushers running at once must not
    double-count. The Redis lock lets only one of them do the work."""
    code = _shorten(client)
    for _ in range(6):
        client.get(f"/{code}", follow_redirects=False)

    async def two_flushers_at_once():
        return await asyncio.gather(flusher.flush_once(), flusher.flush_once())

    results = client.portal.call(two_flushers_at_once)
    assert sorted(results) == [0, 6]  # one did the work, the other skipped
    assert client.portal.call(_db_click_count, code) == 6


def test_demo_page_and_served_by_header(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Which copy answered" in r.text
    assert r.headers["X-Served-By"]
