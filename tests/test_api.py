"""End-to-end API tests. They need a running Postgres (DATABASE_URL);
they are skipped automatically if the database can't be reached."""
import asyncpg
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


def _db_available() -> bool:
    import asyncio

    async def probe():
        conn = await asyncpg.connect(config.DATABASE_URL, timeout=2)
        await conn.close()

    try:
        asyncio.run(probe())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # "with" runs the startup/shutdown lifespan
        yield c


def test_shorten_redirect_and_count(client):
    r = client.post("/shorten", json={"long_url": "https://example.com/some/long/path"})
    assert r.status_code == 201
    code = r.json()["short_code"]
    assert r.json()["short_url"].endswith("/" + code)

    for _ in range(3):
        r = client.get(f"/{code}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "https://example.com/some/long/path"

    stats = client.get(f"/{code}/stats").json()
    assert stats["click_count"] == 3


def test_codes_are_unique(client):
    codes = {
        client.post("/shorten", json={"long_url": f"https://example.com/{i}"}).json()["short_code"]
        for i in range(20)
    }
    assert len(codes) == 20


def test_invalid_url_rejected(client):
    assert client.post("/shorten", json={"long_url": "not a url"}).status_code == 422


def test_unknown_and_junk_codes_404(client):
    assert client.get("/zzzzzzzzzz", follow_redirects=False).status_code == 404
    assert client.get("/bad-code!", follow_redirects=False).status_code == 404
    assert client.get("/zzzzzzzzzz/stats").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
