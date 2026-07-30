"""Central settings. Everything is env-driven so the same image runs
unmodified as a single instance (Phase 1) or as one of N replicas (Phase 2+)."""
import os


class Settings:
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/urlshort"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Snowflake worker id must be unique per replica. In Docker Compose each
    # replica gets this injected via environment (see docker-compose.yml).
    WORKER_ID: int = int(os.getenv("WORKER_ID", "0"))

    # How often the background task flushes buffered click counts to Postgres.
    FLUSH_INTERVAL_SECONDS: float = float(os.getenv("FLUSH_INTERVAL_SECONDS", "2.0"))

    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

    # Feature flags — let Phase 1 and Phase 2+ share one codebase.
    # v1 (naive): auto-increment id, synchronous click writes, no cache.
    # v2 (scaled): Snowflake id, async click buffer, cache-aside reads.
    USE_SNOWFLAKE_ID: bool = os.getenv("USE_SNOWFLAKE_ID", "true").lower() == "true"
    ASYNC_CLICK_WRITES: bool = os.getenv("ASYNC_CLICK_WRITES", "true").lower() == "true"
    CACHE_READS: bool = os.getenv("CACHE_READS", "true").lower() == "true"


settings = Settings()
