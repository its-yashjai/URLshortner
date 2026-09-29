"""Postgres connection pool and schema.

A *pool* keeps a handful of open connections and lends them out per
request. Opening a fresh TCP + auth handshake on every request would
cost milliseconds each time, so pooling is the first, cheapest win.
"""
import asyncpg

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
    id          BIGSERIAL PRIMARY KEY,
    short_code  TEXT UNIQUE,
    long_url    TEXT NOT NULL,
    click_count BIGINT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
# Notes on the schema:
# - BIGSERIAL: auto-increment 64-bit id (internal only since v2).
# - short_code is UNIQUE, which also gives it a B-tree index, so the
#   redirect lookup (WHERE short_code = $1) is O(log n), not a full scan.
#   In v2 the same constraint also catches random-code collisions.
# - short_code is nullable only for compatibility with v1, which learned
#   the id after the INSERT and filled the code in afterwards.

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        config.DATABASE_URL,
        min_size=config.DB_POOL_MIN,
        max_size=config.DB_POOL_MAX,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised")
    return _pool
