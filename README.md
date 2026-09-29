# URL Shortener

A URL shortener built in two measured versions. v2 fixes the weaknesses of v1 and is benchmarked before and after on the same machine, so every claim is backed by a number.

| Version | What changes | Status |
|---|---|---|
| **v1: naive baseline** | FastAPI + Postgres, base62 codes from the row id, click counted with a synchronous `UPDATE` on every redirect | ✅ tag `v1` |
| **v2: cache + buffered clicks** | Redis cache-aside for lookups, clicks buffered in Redis and batch-flushed to Postgres, random non-guessable codes, falls back to v1 behaviour if Redis is down | ✅ tag `v2` |

## v1 vs v2 results

Redirect endpoint, 50 connections, 20 s, 30-link hot set, [autocannon](https://github.com/mcollina/autocannon), same 2-core machine, two runs each. Postgres statements counted exactly with `pg_stat_statements`.

| | Throughput | p50 | p99 | Postgres `UPDATE`s during the run |
|---|---|---|---|---|
| **v1** | ~2,150 req/s | 22 ms | 43 ms | **~43,000** (one per click) |
| **v2** | ~2,070 req/s | 22 ms | 44 ms | **10** (one batch every 2 s) |

**What this shows, honestly:**
- **v2 removes the database from the redirect path.** Postgres went from one write per click to one batched write per 2 seconds (≈99.98% fewer write statements), and every redirect was served from the Redis cache (0 Postgres lookups).
- **Throughput did not improve on this machine,** because the database wasn't the limit here. The single Python app process was, at ~91% of one CPU core, for both versions. Postgres on a local disk with a tiny table absorbs 2k updates/s easily. The v2 gain shows up when the database is the scarce resource: shared with other services, remote over a network, or under a much higher write load.
- **No clicks lost:** 100 connections hammering one viral link for 12 s gave identical counts in the stats endpoint and in Postgres after the flush.
- The next bottleneck is the app process itself. Running several stateless copies of the app behind a load balancer would be the natural next step.

An earlier attempt with the Python `loadtest/bench.py` capped both versions at ~500 req/s. Profiling showed the load generator itself pinned at 100% CPU while the server idled, so those numbers measured the tester, not the app.

## Architecture (v2)

```
                        ┌─────────── Redis (memory) ───────────┐
Client ──▶ FastAPI ───▶ │ url:<code>  → long URL (cache, TTL 1h) │
             │          │ clicks:pending → {code: n}  (buffer)  │
             │          └──────────────────┬────────────────────┘
             │ cache miss only              │ every 2 s, one batched UPDATE
             ▼                              ▼
         PostgreSQL  ◀──────────────  background flusher
         (source of truth)
```

## API

| Method | Path | Result |
|---|---|---|
| `POST` | `/shorten` | body `{"long_url": "https://…"}` → `201 {short_code, short_url, long_url}` |
| `GET` | `/{code}` | `302` redirect to the long URL (counts a click) |
| `GET` | `/{code}/stats` | `{short_code, long_url, click_count, created_at}` (includes clicks still buffered) |
| `GET` | `/health` | `{"status": "ok", "redis": "ok" or "down"}` |

## Run it

```bash
docker compose up --build            # Postgres + Redis + app on :8000
curl -X POST localhost:8000/shorten -H 'content-type: application/json' \
     -d '{"long_url":"https://example.com"}'
```

To run v1 for comparison: `git checkout v1 && docker compose up --build`.

## Tests and benchmarks

```bash
PYTHONPATH=. pytest -q                                          # API tests auto-skip if Postgres/Redis are down
npm i autocannon@7
node loadtest/redirects.js http://localhost:8000 CODE1,CODE2 50 20   # url, codes, connections, seconds
```

## Project layout

```
app/
  main.py      app + startup/shutdown (starts the click flusher)
  routes.py    endpoints, cache-aside lookup, Redis-down fallback
  cache.py     Redis: link cache + click buffer
  flusher.py   background batch flush of clicks to Postgres
  codes.py     random non-guessable short codes
  db.py        Postgres pool + schema
  base62.py    base62 alphabet/encoding
  schemas.py   request/response validation
  config.py    env-var settings
tests/         unit + API tests
loadtest/      redirects.js (autocannon), bench.py (simple Python tester)
docs/          LEARN.md (v1), LEARN-v2.md (v2)
```
