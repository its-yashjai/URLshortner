"""App settings, read from environment variables.

Keeping config in env vars (not hard-coded) means the same code runs
locally, in Docker, and in production with different settings.
"""
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _clean_db_url(url: str) -> str:
    """Hosted Postgres (e.g. Neon) adds channel_binding=require to its
    connection strings. asyncpg does not know that option and would send it
    to the server as a setting, which fails, so drop it."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


DATABASE_URL = _clean_db_url(
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/shortener")
)
# Neon's "pooled" host (it contains "-pooler") runs PgBouncer, which cannot keep
# asyncpg's prepared statements between queries, so turn that cache off there.
DB_STATEMENT_CACHE_SIZE = 0 if "-pooler" in DATABASE_URL else 100
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# The public base used to build short links, e.g. https://sho.rt
# On Render, RENDER_EXTERNAL_URL is set automatically to the service's URL.
BASE_URL = (
    os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
).rstrip("/")

DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# v2 settings
CODE_LENGTH = int(os.getenv("CODE_LENGTH", "7"))          # random short-code length
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))  # how long a link stays cached
FLUSH_INTERVAL_SECONDS = float(os.getenv("FLUSH_INTERVAL_SECONDS", "2"))  # click batch interval
