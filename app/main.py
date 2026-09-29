"""App entry point. Run with:  uvicorn app.main:app"""
import asyncio
import contextlib
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from app import cache, db, flusher
from app.routes import router

# Container id in Docker (one per replica); INSTANCE_NAME overrides it when
# running several copies on one machine without Docker.
INSTANCE = os.getenv("INSTANCE_NAME") or socket.gethostname()
PAGE = Path(__file__).parent / "static" / "index.html"


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


@app.middleware("http")
async def served_by(request: Request, call_next):
    # Tag every response with the replica that produced it, so you can see
    # Nginx spreading requests (in the demo page, or with `curl -i`).
    response = await call_next(request)
    response.headers["X-Served-By"] = INSTANCE
    return response


@app.get("/", include_in_schema=False)
async def demo_page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


app.include_router(router)
