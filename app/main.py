import asyncio
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import Base, engine
from app.core.flush_worker import flush_loop
from app.routers.urls import router as urls_router, REPLICA_NAME

app = FastAPI(title="URL Shortener at Scale")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


@app.on_event("startup")
def on_startup():
    # With 3 replicas starting simultaneously, each calling create_all()
    # at the same instant races on Postgres's catalog (two replicas both
    # see the table missing, both issue CREATE TABLE, one gets a unique
    # violation on pg_type). A session-level advisory lock serializes
    # replicas here: whoever gets the lock first creates the schema,
    # the rest see it already exists and pass through immediately.
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(727271)"))
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(727271)"))

    if settings.ASYNC_CLICK_WRITES:
        asyncio.create_task(flush_loop())


@app.get("/health")
def health():
    return {"status": "ok", "served_by": REPLICA_NAME}


@app.get("/system/status")
def system_status():
    """Exposes the active feature flags so the frontend's live diagram
    reflects the real, currently-running configuration rather than a
    hardcoded description of the architecture."""
    return {
        "served_by": REPLICA_NAME,
        "worker_id": settings.WORKER_ID,
        "snowflake_ids": settings.USE_SNOWFLAKE_ID,
        "async_click_writes": settings.ASYNC_CLICK_WRITES,
        "cache_reads": settings.CACHE_READS,
        "flush_interval_seconds": settings.FLUSH_INTERVAL_SECONDS,
    }


app.include_router(urls_router)

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
