"""App settings, read from environment variables.

Keeping config in env vars (not hard-coded) means the same code runs
locally, in Docker, and in production with different settings.
"""
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/shortener"
)
# The public base used to build short links, e.g. https://sho.rt
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
