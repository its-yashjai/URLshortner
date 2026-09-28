"""App entry point. Run with:  uvicorn app.main:app"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once on startup and once on shutdown (not per request).
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(title="URL Shortener", version="1.0.0", lifespan=lifespan)
app.include_router(router)
