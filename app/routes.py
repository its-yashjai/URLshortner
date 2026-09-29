"""HTTP endpoints (v2).

What changed from v1:
  * Short codes are random (non-guessable) instead of base62(row id).
  * Redirects read from a Redis cache first (cache-aside) and only fall
    back to Postgres on a cache miss.
  * Clicks are counted in Redis and saved to Postgres in batches by the
    background flusher, so a redirect never waits on a database write.
  * If Redis is down, the app degrades to the v1 behaviour instead of failing.
"""
import logging
import os
import re
import socket

import asyncpg
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import RedirectResponse
from redis.exceptions import RedisError

from app import cache, codes, config, db
from app.schemas import ShortenRequest, ShortenResponse, StatsResponse

router = APIRouter()
log = logging.getLogger("routes")

CODE_RE = re.compile(r"^[0-9a-zA-Z]{1,16}$")
MAX_CODE_ATTEMPTS = 5


def _check_code(code: str) -> None:
    if not CODE_RE.match(code):
        raise HTTPException(status_code=404, detail="short code not found")


@router.post("/shorten", response_model=ShortenResponse, status_code=201)
async def shorten(body: ShortenRequest) -> ShortenResponse:
    long_url = str(body.long_url)
    for _ in range(MAX_CODE_ATTEMPTS):
        code = codes.random_code(config.CODE_LENGTH)
        try:
            # One INSERT (v1 needed INSERT + UPDATE). If the random code
            # already exists, the UNIQUE constraint rejects it and we retry.
            await db.pool().execute(
                "INSERT INTO urls (short_code, long_url) VALUES ($1, $2)",
                code,
                long_url,
            )
        except asyncpg.UniqueViolationError:
            continue
        try:
            await cache.cache_url(code, long_url)  # warm the cache
        except RedisError:
            pass  # cache is optional; Postgres already has the link
        return ShortenResponse(
            short_code=code, short_url=f"{config.BASE_URL}/{code}", long_url=long_url
        )
    raise HTTPException(status_code=503, detail="could not allocate a short code")


@router.get("/health")
async def health() -> dict:
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    try:
        redis_ok = bool(await cache.client().ping())
    except RedisError:
        redis_ok = False
    # "instance" shows which replica answered, handy for seeing Nginx
    # spread requests across the copies of the app.
    return {
        "status": "ok",
        "redis": "ok" if redis_ok else "down",
        "instance": os.getenv("INSTANCE_NAME") or socket.gethostname(),
    }


@router.get("/{code}/stats", response_model=StatsResponse)
async def stats(code: str) -> StatsResponse:
    _check_code(code)
    row = await db.pool().fetchrow(
        "SELECT short_code, long_url, click_count, created_at "
        "FROM urls WHERE short_code = $1",
        code,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="short code not found")
    data = dict(row)
    try:
        # Add clicks still waiting in Redis, so stats are up to date.
        data["click_count"] += await cache.pending_clicks(code)
    except RedisError:
        pass
    return StatsResponse(**data)


async def _click_v1(code: str) -> str | None:
    """The v1 path: look up AND count the click with one synchronous
    Postgres write. Returns the long URL, or None if the code is unknown."""
    return await db.pool().fetchval(
        "UPDATE urls SET click_count = click_count + 1 "
        "WHERE short_code = $1 RETURNING long_url",
        code,
    )


async def _click_v2(code: str) -> str | None:
    """The v2 path: look up via the Redis cache, count the click in Redis.
    Falls back to the v1 path if Redis is unavailable."""
    try:
        long_url = await _lookup_cached(code)
        if long_url is not None:
            await cache.record_click(code)  # in-memory, no DB write, no row lock
        return long_url
    except RedisError:
        # Graceful degradation: Redis is down, so do it the v1 way.
        log.warning("redis unavailable, falling back to postgres for %s", code)
        return await _click_v1(code)


@router.get("/bench/{mode}/{code}", status_code=204, include_in_schema=False)
async def bench_click(mode: str, code: str) -> Response:
    """Used by the demo page's live benchmark: runs exactly the v1 or v2
    click path (same code as a real redirect) and returns 204 No Content,
    so the browser measures the server's work, not following a redirect."""
    _check_code(code)
    if mode not in ("v1", "v2"):
        raise HTTPException(status_code=404, detail="mode must be v1 or v2")
    long_url = await (_click_v1(code) if mode == "v1" else _click_v2(code))
    if long_url is None:
        raise HTTPException(status_code=404, detail="short code not found")
    return Response(status_code=204)


@router.get("/{code}")
async def redirect(code: str) -> RedirectResponse:
    _check_code(code)
    long_url = await _click_v2(code)
    if long_url is None:
        raise HTTPException(status_code=404, detail="short code not found")
    return RedirectResponse(url=long_url, status_code=302)


async def _lookup_cached(code: str) -> str | None:
    """Cache-aside: try Redis, on a miss read Postgres and fill Redis."""
    long_url = await cache.get_cached_url(code)
    if long_url is not None:
        return long_url  # cache HIT: Postgres not touched at all
    long_url = await db.pool().fetchval(
        "SELECT long_url FROM urls WHERE short_code = $1", code
    )
    if long_url is not None:
        await cache.cache_url(code, long_url)  # next click will be a hit
    return long_url
