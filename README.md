# URL Shortener at Scale

A URL shortener built to demonstrate measured, benchmarked scaling — every phase ends with proof, not a claim.

## Benchmark results (single instance, cache + async writes + Snowflake ids)

| Version     | Setup                                                                  | Throughput (req/s) | P95 Latency  | Notes                                                                                           |
| ----------- | ---------------------------------------------------------------------- | ------------------ | ------------ | ----------------------------------------------------------------------------------------------- |
| v1 (naive)  | single instance, sync click writes, auto-increment id                  | 203.7              | 491.9 ms     | bottleneck: every redirect blocks on a synchronous Postgres UPDATE                              |
| v2 (scaled) | single instance, Redis cache-aside + async click buffer + Snowflake id | **424.9**          | **255.3 ms** | fix applied: cache-aside reads skip Postgres on hit; clicks buffered in Redis, flushed every 2s |

**~2.1× throughput, ~48% P95 latency reduction.** 80 concurrent virtual users, 20s, 30-code hot set, 0 errors on both runs. Full methodology, the Phase 1 mixed-workload baseline, and an honest writeup of what happened when horizontal scaling was benchmarked on single-core hardware: see [`docs/scale-report.md`](docs/scale-report.md).

## Architecture

```
Client → Nginx (round-robin) → 3× FastAPI replica → Redis (cache + click buffer) → Postgres
```

Full component breakdown and design rationale in [`docs/architecture.md`](docs/architecture.md).

## Stack

FastAPI · PostgreSQL · Redis · Nginx · Docker Compose · k6

## Running it

**v2 (scaled) — 3 replicas, cache, async writes:**

```bash
docker compose up --build
# app available at http://localhost:8000
```

**v1 (naive) — single instance, for the baseline comparison:**

```bash
docker compose -f docker-compose.v1.yml up --build
# app available at http://localhost:8000
```

**Locally without Docker**, set up PostgreSQL, copy `.env.example` to `.env`, and run:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The default configuration expects PostgreSQL.

Project link for sharing: [https://github.com/its-yashjai/URLshortner.git](https://github.com/its-yashjai/URLshortner.git)

## API

| Endpoint             | Description                                                               |
| -------------------- | ------------------------------------------------------------------------- |
| `POST /shorten`      | `{"long_url": "..."}` → `{short_code, short_url, long_url}`               |
| `GET /{code}`        | 302 redirect to the long URL                                              |
| `GET /{code}/stats`  | `{short_code, long_url, click_count, created_at}`                         |
| `GET /system/status` | active feature flags for this replica — used by the frontend's live trace |
| `GET /health`        | liveness check                                                            |

## Load testing

```bash
# real k6 script (needs internet access to fetch k6 itself)
k6 run loadtest/shorten_and_redirect.js

# Python asyncio equivalents used to produce the numbers in this repo,
# written for sandboxes where k6's binary isn't reachable
python3 loadtest/bench.py http://localhost:8000 150 30
python3 loadtest/bench_hot_reads.py http://localhost:8000 80 20 30
```

## Tests

```bash
pip install pytest
PYTHONPATH=. pytest app/core/test_snowflake.py -v
```

## Project structure

```
/app            → application code (routers, core: config/db/redis/snowflake/flush worker)
/frontend       → static frontend served at /
/loadtest       → k6 script + Python load-test harnesses
/docs
  architecture.md   → component responsibilities, design rationale, bugs found & fixed
  scale-report.md   → methodology, before/after numbers, honest limitations
docker-compose.yml     → v2 scaled (3 replicas + Nginx + Redis + Postgres)
docker-compose.v1.yml  → v1 naive baseline (single instance)
nginx.conf
```

## Scope

No auth, no user accounts — intentionally out of scope. Files are kept small with one responsibility each, for interview explainability.
