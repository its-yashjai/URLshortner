# Learning notes: v1

Read this with the code open. Goal: by the end of the week you can explain every line **and** why it exists, without looking.

---

## 1. The 30-second pitch (memorise this)

> "I built a URL shortener in phases to practise measured scaling. v1 is a plain FastAPI + Postgres service where each short code is the base62 encoding of the row's id. I benchmarked it and found the bottleneck: every redirect does a synchronous database write to count the click. In v2 I fix that with a Redis cache and batched click writes, and compare the before and after numbers on the same machine."

---

## 2. How a request flows

**Creating a link: `POST /shorten`**
1. Pydantic checks that `long_url` is a real http/https URL. If not, the API returns 422 and our code never runs.
2. We borrow a connection from the **pool** and open a **transaction**.
3. `INSERT … RETURNING id` gives us the new row's id, e.g. `125`.
4. `base62.encode(125)` → `"21"`. We save that as `short_code`.
5. Commit, then return `{short_code, short_url, long_url}` with status 201.

**Using a link: `GET /{code}`**
1. Reject codes containing non-base62 characters right away (404, no DB call).
2. One SQL statement does the lookup and the click count together:
   `UPDATE urls SET click_count = click_count + 1 WHERE short_code = $1 RETURNING long_url`
3. If no row came back → 404. Otherwise → **302** redirect to `long_url`.

---

## 3. Every design decision and its "because"

| Decision | Because | Tradeoff / what an interviewer might poke |
|---|---|---|
| **FastAPI** | Async, fast, auto-validates input with Pydantic, gives free docs at `/docs` | Flask would work too, but it's sync by default |
| **PostgreSQL** | Reliable, ACID, unique constraints, and the data is simple rows | A key-value store (DynamoDB, Cassandra) scales writes more easily at massive scale |
| **asyncpg + connection pool** | Opening a new connection per request costs a TCP + auth handshake; a pool reuses a few open connections | Pool too small → requests queue up; too big → Postgres struggles. `DB_POOL_MAX=10` |
| **Base62 of the id** | Guaranteed unique (ids never repeat), so no collision checks; all chars are URL-safe | **Codes are sequential and guessable**: anyone can enumerate every link. v2 fixes this |
| **Why 62 characters?** | `0-9 a-z A-Z` = 62. 7 chars → 62⁷ ≈ **3.5 trillion** codes | Standard Base64 adds `+` and `/`, which have special meaning in URLs. A URL-safe variant (base64url, RFC 4648) swaps them for `-` and `_`, so it would also work; base62 simply avoids symbols entirely, so codes are easy to read, type and copy |
| **`short_code UNIQUE`** | Also creates a B-tree **index**, so lookups are O(log n) instead of scanning the table | Every insert also updates the index (tiny cost) |
| **Insert then update in a transaction** | We only know the id after inserting; the transaction makes the two steps all-or-nothing | Two statements per create. Could precompute ids from a sequence instead |
| **302, not 301** | Browsers cache 301 (permanent) forever and skip our server next time, so we'd lose click counts | 301 would reduce our server load. It's a product choice: analytics vs load |
| **Config from env vars** | Same code runs locally, in Docker, and in prod | — |
| **Code-format regex check** | Junk like `/bad-code!` gets a 404 without touching the DB | — |

---

## 4. The bottleneck (the most important part to understand)

Look at the redirect SQL again. It's an `UPDATE`, so:

1. **A read became a write.** Redirects are ~99% of traffic, and each one writes to disk (Postgres writes to its WAL log).
2. **Row-lock contention.** A popular link = one row. 50 users clicking it at once all need that row's lock, so they wait in line. That's why p95 (310 ms) is ~4.5× the p50 (68 ms): some requests sit in the lock queue.
3. **No caching.** Even the lookup part goes to Postgres every time, for data that almost never changes.

**The v2 fix, in one breath:** read the long URL from Redis (memory, ~sub-millisecond) and just do `INCR clicks:<code>` in Redis. A background task flushes the counts to Postgres every few seconds in one batched `UPDATE`. The redirect no longer waits on the database at all.

---

## 5. Your v1 numbers and how to talk about them

- 50 concurrent virtual users, 20 s, 30 "hot" links, 0 errors: **~484 req/s, p50 68 ms, p95 310 ms**.
- **Why a hot set of 30 links?** Real traffic is skewed: a few links get most clicks. It also stresses the row-lock problem on purpose.
- **Why p95, not the average?** Averages hide the slow tail. p95 = "95% of users were at least this fast."
- **Honest caveat:** measured on a 2-core machine with the load generator on the same box, so the numbers are a relative baseline for v1 vs v2, not a capacity claim. Saying this in an interview makes you sound *more* credible, not less.
- **Before putting numbers on your resume,** re-run the benchmark on your own laptop so they're genuinely yours.

---

## 6. Interview questions to practise (answer out loud)

1. Walk me through what happens when I click a short link.
2. Why base62? How many URLs can 7 characters hold?
3. What's wrong with using the auto-increment id as the code? *(guessable; also leaks how many links exist)*
4. How would you avoid guessable codes? *(random codes + collision check, or a Snowflake-style id, or shuffling/encrypting the id)*
5. 301 vs 302, and which did you pick and why?
6. Why is your redirect slow under load? *(sync write + row-lock contention on hot rows)*
7. How would you make it faster? *(cache-aside with Redis, buffer click counts, batch flush)*
8. If you buffer clicks in Redis and Redis crashes, what happens? *(you lose up to one flush interval of counts, which is acceptable for analytics; mention Redis persistence/AOF if it matters)*
9. What is a connection pool and why does it matter?
10. What does the `UNIQUE` constraint give you besides uniqueness? *(an index)*
11. How would you scale to 100 million links / 10,000 req/s? *(cache, stateless replicas behind a load balancer, read replicas, later sharding by code)*
12. Two users shorten the same long URL. Do they get the same code? *(in v1, no, each gets its own. Discuss dedup and its tradeoffs)*

When you can answer all 12 without notes, you own v1. Then we build v2.
