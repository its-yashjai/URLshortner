"""Periodic background task: drains the Redis click-count buffer into
Postgres. This is what lets the redirect endpoint stay non-blocking —
the write-behind happens here, off the request path, on a fixed cadence."""
import asyncio
import logging

# pyrefly: ignore [missing-import]
from sqlalchemy import update

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.redis_client import redis_client, CLICK_BUFFER_PREFIX
from app.models import ShortURL

logger = logging.getLogger("flush_worker")


def flush_once() -> int:
    """Scan pending click-count keys, atomically pop each one's value, and
    apply it to Postgres as a single UPDATE per code. Returns rows flushed."""
    flushed = 0
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor=cursor, match=f"{CLICK_BUFFER_PREFIX}*", count=200)
        for key in keys:
            # GETDEL is atomic — a concurrent redirect that INCRs right
            # after this either lands in the next flush cycle or, in the
            # rare race where it lands mid-GETDEL, is still captured by
            # Redis's atomicity guarantee on the single key.
            pending = redis_client.getdel(key)
            if not pending:
                continue
            short_code = key[len(CLICK_BUFFER_PREFIX):]
            delta = int(pending)
            db = SessionLocal()
            try:
                db.execute(
                    update(ShortURL)
                    .where(ShortURL.short_code == short_code)
                    .values(click_count=ShortURL.click_count + delta)
                )
                db.commit()
                flushed += 1
            except Exception:
                logger.exception("failed to flush clicks for %s", short_code)
                db.rollback()
                # Put the delta back so it isn't lost on next flush.
                redis_client.incrby(key, delta)
            finally:
                db.close()
        if cursor == 0:
            break
    return flushed


async def flush_loop():
    while True:
        await asyncio.sleep(settings.FLUSH_INTERVAL_SECONDS)
        try:
            flush_once()
        except Exception:
            logger.exception("flush_loop iteration failed")
