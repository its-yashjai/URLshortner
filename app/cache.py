"""Redis connection plus the two v2 tricks: the link cache and the click buffer.

Redis keeps data in memory (RAM), so reads and writes take well under a
millisecond, versus a disk-backed Postgres write that must be made durable.

Keys used:
  url:<code>      -> the long URL (cache-aside, expires after CACHE_TTL_SECONDS)
  clicks:pending  -> a Redis hash {code: clicks not yet saved to Postgres}
"""
import redis.asyncio as redis

from app import config

URL_KEY = "url:{}"
PENDING_CLICKS = "clicks:pending"

_client: redis.Redis | None = None


async def connect() -> None:
    global _client
    _client = redis.from_url(config.REDIS_URL, decode_responses=True)
    await _client.ping()


async def disconnect() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("redis not initialised")
    return _client


async def get_cached_url(code: str) -> str | None:
    return await client().get(URL_KEY.format(code))


async def cache_url(code: str, long_url: str) -> None:
    # TTL: unpopular links fall out of the cache on their own, so memory
    # is spent only on links people actually click.
    await client().set(URL_KEY.format(code), long_url, ex=config.CACHE_TTL_SECONDS)


async def record_click(code: str) -> None:
    # HINCRBY is atomic inside Redis: 1,000 simultaneous clicks all land,
    # with no lost updates and no row lock to queue on.
    await client().hincrby(PENDING_CLICKS, code, 1)


FLUSHING_PREFIX = "clicks:flushing:"


async def pending_clicks(code: str) -> int:
    """Clicks for `code` not yet saved in Postgres.

    Counts both the live buffer AND any snapshot the flusher is in the
    middle of saving. Without the second part, /stats briefly under-counts
    during each flush (clicks are "in transit" between Redis and Postgres).
    """
    r = client()
    total = int(await r.hget(PENDING_CLICKS, code) or 0)
    async for key in r.scan_iter(match=FLUSHING_PREFIX + "*"):
        total += int(await r.hget(key, code) or 0)
    return total
