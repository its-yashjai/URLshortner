import os

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_db
from app.core.redis_client import redis_client, CLICK_BUFFER_PREFIX, URL_CACHE_PREFIX
from app.core.snowflake import SnowflakeGenerator, encode_base62
from app.models import ShortURL
from app.schemas import ShortenRequest, ShortenResponse, StatsResponse

router = APIRouter()

# One generator per process. WORKER_ID is injected per-replica via env
# (see docker-compose.yml) so replicas never generate colliding ids.
_snowflake = SnowflakeGenerator(worker_id=settings.WORKER_ID)

# Identifies which replica served a request — used only to make the
# horizontal-scaling behavior visible in the frontend's live diagram.
REPLICA_NAME = os.getenv("REPLICA_NAME", f"worker-{settings.WORKER_ID}")


@router.post("/shorten", response_model=ShortenResponse)
def shorten(payload: ShortenRequest, response: Response, db: Session = Depends(get_db)):
    long_url = str(payload.long_url)

    if settings.USE_SNOWFLAKE_ID:
        new_id = _snowflake.next_id()
        short_code = encode_base62(new_id)
        record = ShortURL(id=new_id, short_code=short_code, long_url=long_url, click_count=0)
        db.add(record)
        db.commit()
    else:
        # v1 naive path: let Postgres assign the id via auto-increment,
        # then encode it. This requires a round trip to learn the id
        # before we know the short_code, and it's this row's identity
        # column that serializes concurrent inserts.
        record = ShortURL(long_url=long_url, click_count=0)
        db.add(record)
        db.commit()
        db.refresh(record)
        short_code = encode_base62(record.id)
        record.short_code = short_code
        db.commit()

    response.headers["X-Served-By"] = REPLICA_NAME
    return ShortenResponse(
        short_code=short_code,
        short_url=f"{settings.BASE_URL}/{short_code}",
        long_url=long_url,
    )


@router.get("/{short_code}")
def redirect(short_code: str, response: Response, db: Session = Depends(get_db)):
    long_url = None
    cache_hit = False

    if settings.CACHE_READS:
        cached = redis_client.get(f"{URL_CACHE_PREFIX}{short_code}")
        if cached is not None:
            long_url = cached
            cache_hit = True

    if long_url is None:
        record = db.execute(
            select(ShortURL).where(ShortURL.short_code == short_code)
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="short_code not found")
        long_url = record.long_url
        if settings.CACHE_READS:
            # Codes are immutable once created — no invalidation needed, ever.
            redis_client.set(f"{URL_CACHE_PREFIX}{short_code}", long_url)

    if settings.ASYNC_CLICK_WRITES:
        # Buffer the increment in Redis; a background task flushes it to
        # Postgres periodically. The redirect never waits on a DB write.
        redis_client.incr(f"{CLICK_BUFFER_PREFIX}{short_code}")
    else:
        # v1 naive path: block the redirect on a synchronous Postgres write.
        record = db.execute(
            select(ShortURL).where(ShortURL.short_code == short_code)
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="short_code not found")
        record.click_count += 1
        db.commit()

    response.headers["X-Served-By"] = REPLICA_NAME
    response.headers["X-Cache"] = "HIT" if cache_hit else "MISS"
    return RedirectResponse(url=long_url, status_code=302, headers=response.headers)


@router.get("/{short_code}/stats", response_model=StatsResponse)
def stats(short_code: str, db: Session = Depends(get_db)):
    record = db.execute(
        select(ShortURL).where(ShortURL.short_code == short_code)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="short_code not found")

    click_count = record.click_count
    if settings.ASYNC_CLICK_WRITES:
        # Add whatever's still buffered in Redis but not yet flushed, so
        # stats reads are accurate even between flush cycles.
        pending = redis_client.get(f"{CLICK_BUFFER_PREFIX}{short_code}")
        if pending:
            click_count += int(pending)

    return StatsResponse(
        short_code=record.short_code,
        long_url=record.long_url,
        click_count=click_count,
        created_at=record.created_at,
    )
