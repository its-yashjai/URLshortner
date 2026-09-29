"""Background task that moves buffered click counts from Redis to Postgres.

Every FLUSH_INTERVAL_SECONDS:
  1. RENAME clicks:pending -> clicks:flushing:<unique id>
     RENAME is atomic, so new clicks arriving at this exact moment go into
     a fresh clicks:pending hash and are never lost or double-counted.
  2. Read the snapshot and write ALL counts to Postgres in ONE statement.
  3. Delete the snapshot only after Postgres confirmed the write.

If Postgres is down, the snapshot stays in Redis and is retried on the
next tick, so counts are delayed rather than lost. The accepted risk:
if Redis itself crashes, up to one interval of clicks can be lost. That
is fine for analytics, and would not be fine for money.
"""
import asyncio
import logging
import uuid

from app import cache, config, db

log = logging.getLogger("flusher")
FLUSHING_PREFIX = cache.FLUSHING_PREFIX

BATCH_UPDATE_SQL = """
UPDATE urls AS u
SET click_count = u.click_count + v.n
FROM unnest($1::text[], $2::bigint[]) AS v(code, n)
WHERE u.short_code = v.code
"""


async def flush_once() -> int:
    """Flush buffered clicks. Returns how many clicks were saved."""
    r = cache.client()

    # Snapshot the live buffer (if there is one).
    if await r.exists(cache.PENDING_CLICKS):
        try:
            await r.rename(cache.PENDING_CLICKS, FLUSHING_PREFIX + uuid.uuid4().hex)
        except Exception:
            pass  # buffer vanished between EXISTS and RENAME: nothing to do

    saved = 0
    # Includes snapshots left over from an earlier failed flush.
    async for key in r.scan_iter(match=FLUSHING_PREFIX + "*"):
        counts = await r.hgetall(key)
        if counts:
            codes = list(counts.keys())
            numbers = [int(n) for n in counts.values()]
            await db.pool().execute(BATCH_UPDATE_SQL, codes, numbers)
            saved += sum(numbers)
        await r.delete(key)  # only reached if the UPDATE succeeded
    return saved


async def run_forever() -> None:
    while True:
        await asyncio.sleep(config.FLUSH_INTERVAL_SECONDS)
        try:
            await flush_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("click flush failed; will retry next tick")
