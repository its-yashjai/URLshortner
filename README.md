# URL Shortener

A URL shortener built in measured phases. Each version fixes one bottleneck of the previous one and is benchmarked before and after, so every improvement is backed by a number.

| Version | What changes | Status |
|---|---|---|
| **v1: naive baseline** | FastAPI + Postgres, base62 codes from the row id, click counted with a synchronous `UPDATE` on every redirect | ✅ done |
| v2: caching + async clicks | Redis cache-aside for lookups, clicks buffered and flushed in batches, non-guessable ids | planned |
| v3: horizontal scale | Stateless replicas behind Nginx, load-tested | planned |

## v1 baseline numbers

Redirect endpoint, 50 concurrent virtual users, 20 s, 30-link hot set, 0 errors (two runs):

| Throughput | p50 | p95 | p99 |
|---|---|---|---|
| ~484 req/s | 68 ms | 310 ms | ~525–555 ms |

Measured on a 2-core Linux box with the load generator on the same machine, so treat these as a **relative baseline** for comparing v1 vs v2 on the same hardware, not as an absolute capacity claim. Re-run `loadtest/bench.py` on your own machine before quoting numbers.

## Architecture (v1)

```
Client ──HTTP──▶ FastAPI (uvicorn) ──asyncpg pool──▶ PostgreSQL
                  POST /shorten      INSERT row, encode id → base62 code
                  GET  /{code}       UPDATE click_count … RETURNING long_url  → 302
```

## API

| Method | Path | Result |
|---|---|---|
| `POST` | `/shorten` | body `{"long_url": "https://…"}` → `201 {short_code, short_url, long_url}` |
| `GET` | `/{code}` | `302` redirect to the long URL (counts a click) |
| `GET` | `/{code}/stats` | `{short_code, long_url, click_count, created_at}` |
| `GET` | `/health` | `{"status": "ok"}` if the DB answers |

## Run it

With Docker:

```bash
docker compose up --build
curl -X POST localhost:8000/shorten -H 'content-type: application/json' \
     -d '{"long_url":"https://example.com"}'
```

Without Docker (needs a local Postgres):

```bash
pip install -r requirements.txt
cp .env.example .env   # then export the variables, or set them in your shell
uvicorn app.main:app --reload
```

## Tests and benchmark

```bash
PYTHONPATH=. pytest -q                                    # API tests auto-skip if Postgres is down
python loadtest/bench.py http://localhost:8000 50 20 30   # url, users, seconds, hot-set size
```

## Project layout

```
app/
  main.py      app + startup/shutdown
  routes.py    the 4 endpoints
  db.py        connection pool + schema
  base62.py    id ⇄ short code
  schemas.py   request/response validation
  config.py    env-var settings
tests/         unit + API tests
loadtest/      bench.py load tester
docs/LEARN.md  design decisions + interview Q&A
```

Out of scope on purpose: auth, custom aliases, expiry. Each file is small and single-purpose so the whole thing is easy to explain.
