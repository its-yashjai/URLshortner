"""App entry point. Run with:  uvicorn app.main:app"""
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import cache, db, flusher
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: open Postgres + Redis, start the background click flusher.
    await db.connect()
    await cache.connect()
    task = asyncio.create_task(flusher.run_forever())
    yield
    # Shutdown: stop the flusher, then save any clicks still in Redis.
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    with contextlib.suppress(Exception):
        await flusher.flush_once()
    await cache.disconnect()
    await db.disconnect()


app = FastAPI(title="URL Shortener", version="2.0.0", lifespan=lifespan)
app.include_router(router)
