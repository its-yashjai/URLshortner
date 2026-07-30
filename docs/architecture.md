# Architecture

## Component overview

```
                      ┌─────────────┐
                      │   Client    │
                      └──────┬──────┘
                             │
                      ┌──────▼──────┐
                      │    Nginx    │  round-robin, passive health checks
                      └──────┬──────┘
              ┌──────────────┼──────────────┐
        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
        │  app1     │  │  app2     │  │  app3     │  FastAPI, stateless
        │ worker_id1│  │ worker_id2│  │ worker_id3│
        └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
              │              │              │
              └──────┬───────┴───────┬──────┘
                ┌─────▼─────┐   ┌─────▼─────┐
                │   Redis   │   │ Postgres  │
                │ cache +   │   │  urls     │
                │ click buf │   │  table    │
                └───────────┘   └───────────┘
```

## Component responsibilities

- **Nginx**: single entry point, round-robins across the 3 app replicas, marks a replica unhealthy after 3 failed proxy attempts (`max_fails=3 fail_timeout=10s`).
- **FastAPI replicas**: stateless. Each holds its own in-process Snowflake generator seeded with a unique `WORKER_ID` (injected via env per replica in `docker-compose.yml`). No replica-local state survives a restart or matters for correctness — any replica can serve any request.
- **Redis**: two independent uses, both namespaced by key prefix —
  - `url:cache:{code}` — cache-aside for reads. Populated on miss, read first on every redirect.
  - `clicks:pending:{code}` — write-behind buffer for click counts. `INCR`'d synchronously on every redirect, drained by a background task on a fixed interval (`FLUSH_INTERVAL_SECONDS`, default 2s).
- **Postgres**: source of truth for URL mappings and click counts. The `urls` table is the only table in the schema.

## Key design decisions

### Why Snowflake instead of auto-increment
Auto-increment ids force every insert to take a row lock on the same sequence, serializing writes under concurrency — that's the write bottleneck Phase 1's baseline measures. The hand-written generator (`app/core/snowflake.py`) packs a millisecond timestamp, a 10-bit worker id, and a 12-bit per-ms sequence into a 63-bit int, so any replica can mint unique ids without touching a shared counter. Verified unique under concurrent generation and across workers in `app/core/test_snowflake.py`.

### Why no cache invalidation
Short codes are immutable once created — a `short_code → long_url` mapping never changes after `POST /shorten` returns. That means Redis cache-aside here has no invalidation problem to solve: a code is either not cached yet (miss, fetch from Postgres, populate) or cached forever-correctly (hit). This is a deliberate simplicity decision that falls directly out of the domain, not an oversight — a mutable-URL feature would reopen it.

### Why click counts are buffered, not written synchronously
A synchronous `UPDATE urls SET click_count = click_count + 1` on every redirect means every redirect waits on a Postgres round trip (and its WAL fsync) before it can respond. Buffering the increment in Redis (`INCR`) and draining it periodically removes that wait from the request path entirely. The tradeoff: `click_count` in Postgres can lag reality by up to `FLUSH_INTERVAL_SECONDS`. `GET /{code}/stats` accounts for this by adding whatever's still buffered in Redis to the Postgres value, so reads are accurate even between flush cycles — only Postgres itself lags, not what the API reports.

### Concurrent-replica startup race (found during testing)
All 3 replicas call `Base.metadata.create_all()` on startup. Starting them simultaneously means two replicas can both see the `urls` table missing and both issue `CREATE TABLE`, and Postgres's catalog isn't safe against that — one replica gets a unique-violation on `pg_type` and crashes. Fixed with a session-level Postgres advisory lock (`pg_advisory_lock(727271)`) around the `create_all()` call: whoever acquires the lock first creates the schema; the other replicas block briefly, then see the table already exists and pass through. See `app/main.py`.

## Known limitation: this sandbox is single-core

The benchmarks in `docs/scale-report.md` were run in a build sandbox with **1 vCPU**. Horizontal scaling across processes fundamentally needs multiple cores (or multiple machines) to show a throughput gain — on a single core, running 3 replicas instead of 1 adds context-switching and connection-pool overhead without adding compute capacity, and actually measured *worse* than a single instance when tested here. That's not a flaw in the design, it's a property of the hardware it was measured on. The `docker-compose.yml` + `nginx.conf` + `loadtest/shorten_and_redirect.js` are written for a normal multi-core machine, where the replica count is the thing that lets throughput scale past what one process can push. What *is* demonstrated cleanly on this single core is the Redis cache-aside + async write-buffer improvement in isolation (Phase 2/3), since that's a per-request cost reduction rather than a parallelism gain — see the results table in `scale-report.md`.
