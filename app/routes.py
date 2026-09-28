"""HTTP endpoints for v1 (the naive baseline).

v1 is deliberately simple so it can be measured and then improved.
The known weakness: every redirect does a synchronous UPDATE to count
the click, so each read request also becomes a database write.
"""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app import base62, config, db
from app.schemas import ShortenRequest, ShortenResponse, StatsResponse

router = APIRouter()

# Only accept codes made of base62 characters. Rejecting junk early
# avoids a pointless database round-trip.
CODE_RE = re.compile(r"^[0-9a-zA-Z]{1,16}$")


def _check_code(code: str) -> None:
    if not CODE_RE.match(code):
        raise HTTPException(status_code=404, detail="short code not found")


@router.post("/shorten", response_model=ShortenResponse, status_code=201)
async def shorten(body: ShortenRequest) -> ShortenResponse:
    long_url = str(body.long_url)
    async with db.pool().acquire() as conn:
        # One transaction: insert the row, then derive the code from its id.
        # If anything fails in between, nothing is half-written.
        async with conn.transaction():
            row_id = await conn.fetchval(
                "INSERT INTO urls (long_url) VALUES ($1) RETURNING id", long_url
            )
            code = base62.encode(row_id)
            await conn.execute(
                "UPDATE urls SET short_code = $1 WHERE id = $2", code, row_id
            )
    return ShortenResponse(
        short_code=code,
        short_url=f"{config.BASE_URL}/{code}",
        long_url=long_url,
    )


@router.get("/health")
async def health() -> dict:
    async with db.pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


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
    return StatsResponse(**dict(row))


@router.get("/{code}")
async def redirect(code: str) -> RedirectResponse:
    _check_code(code)
    # v1 BOTTLENECK (on purpose): the lookup and the click count happen in
    # a single synchronous UPDATE ... RETURNING. The user waits for a
    # database *write* just to be redirected, and every click on a popular
    # link fights for the same row lock.
    long_url = await db.pool().fetchval(
        "UPDATE urls SET click_count = click_count + 1 "
        "WHERE short_code = $1 RETURNING long_url",
        code,
    )
    if long_url is None:
        raise HTTPException(status_code=404, detail="short code not found")
    # 302 (temporary) rather than 301 (permanent): browsers cache 301s and
    # would skip our server next time, so we'd stop seeing the clicks.
    return RedirectResponse(url=long_url, status_code=302)
