# Learning notes: v2

Read this after you're comfortable with v1. Same rule: be able to explain it out loud without looking.

---

## 1. The 30-second pitch (updated)

> "I built a URL shortener with FastAPI and Postgres in measured versions. In v1, every click did a database write, so I added Redis in v2. Popular links are served from an in-memory cache, and click counts are buffered in Redis and saved to Postgres in one batch every 2 seconds. On a load test, Postgres went from about 43,000 write statements to 10 for the same traffic, with no lost clicks. Throughput stayed the same on my machine because the single Python process turned out to be the real bottleneck, so the next step would be running several copies of the app behind a load balancer."

That last sentence is a *strength*, not a weakness. It shows you measured instead of assuming.

---

## 2. The 4 changes in v2

### Change 1: Redis cache ("cache-aside")
**Problem in v1:** every click asked Postgres "what's the long URL for code X?"

**Fix:** keep the answer in Redis, which lives in **RAM**, so it's much faster than disk.

**How cache-aside works (3 steps):**
1. Click comes in → ask **Redis** first: "do you have `url:k9Ab2xQ`?"
2. **Hit** → use it. Postgres isn't touched at all.
3. **Miss** → ask Postgres, then **save the answer into Redis** so the next click is a hit.

"Aside" = the cache sits *beside* the database, and **your code** decides when to read and fill it.

**TTL (time to live) = 1 hour:** each cached link expires after an hour unless it's used again. Unpopular links fall out on their own, so Redis memory is only spent on links people actually click.

**Warm on create:** when you shorten a link, v2 puts it in Redis immediately, so even the first click is a hit.

### Change 2: Click buffer + batch flush (the big one)
**Problem in v1:** every click = one `UPDATE` in Postgres, which means a row lock and a disk write.

**Fix:**
- On each click: `HINCRBY clicks:pending <code> 1` in Redis. That's "add 1 to this code's counter" in memory. It's **atomic**, meaning 1,000 simultaneous clicks all get counted correctly, with no lock queue.
- A **background task (the flusher)** runs every 2 seconds and writes all the counts to Postgres in **one** statement.

**Attendance-register analogy:** instead of 500 students each queueing for one pen, one class monitor collects a tally on paper and writes the totals into the register once every 2 seconds.

**How the flush avoids losing clicks (interviewers love this):**
1. `RENAME clicks:pending → clicks:flushing:<random id>`. RENAME is **atomic**, so any click arriving at that exact moment goes into a brand-new `clicks:pending`. Nothing is lost or counted twice.
2. Read the snapshot → run **one** batched `UPDATE` in Postgres.
3. **Only after Postgres confirms**, delete the snapshot. If Postgres was down, the snapshot stays in Redis and is retried next time.

**The accepted risk:** if **Redis** crashes, up to ~2 seconds of clicks can be lost. That's fine for analytics. It would **not** be fine for money. Say this in interviews; it shows you know the tradeoff.

**Stats stay accurate:** `/stats` adds "clicks in Postgres" and "clicks still waiting in Redis," so users see the live number.

### Change 3: Random, non-guessable codes
**Problem in v1:** codes came from the row ID (1, 2, 3 → "1", "2", "3"), so anyone could guess every link.

**Fix:** pick 7 random characters from the 62 base62 characters.
- Uses Python's **`secrets`** module (cryptographically secure). The normal `random` module is predictable, so it's not safe here.
- 62⁷ ≈ **3.5 trillion** possible codes, so clashes are very rare.
- **If a clash happens anyway**, the `UNIQUE` constraint in Postgres rejects the insert, and the code tries a new random code (up to 5 times).
- Bonus: creating a link is now **one INSERT** (v1 needed INSERT + UPDATE).

**Why not a Snowflake ID?** Snowflake IDs are built from the **timestamp**, so they're harder to guess but still somewhat predictable and much longer (~11 characters). Random 7-character codes are shorter and truly unguessable. That was a deliberate tradeoff.

### Change 4: Graceful degradation
If Redis goes down, the redirect **doesn't fail**. It catches the Redis error and falls back to the v1 way (read + update Postgres directly). Slower, but the site stays up. The `/health` endpoint reports `"redis": "down"` so you'd notice.

---

## 3. The benchmark story (know this well)

| | Throughput | p99 latency | Postgres UPDATEs in 20 s |
|---|---|---|---|
| v1 | ~2,150 req/s | 43 ms | **~43,000** |
| v2 | ~2,070 req/s | 44 ms | **10** |

**What to say:**
1. "v2 cut Postgres write statements from about 43,000 to 10 for the same load. That's one batch every 2 seconds instead of one write per click. All redirects came from the Redis cache."
2. "Throughput stayed about the same on my 2-core machine. I checked why: the single Python app process was at ~91% CPU in both versions, so the database wasn't the limit on this hardware."
3. "The v2 gain matters when the database *is* the limit: when it's shared, remote over a network, or under much heavier load. And the next bottleneck is clearly the app process, so the next step would be running several copies of the app."
4. "I also found my first load tester (Python) was itself maxed out at 100% CPU, capping both versions at ~500 req/s. So I switched to autocannon, a faster Node.js tool."

Points 2 and 4 are **gold**. They show you don't blindly trust numbers. Interviewers remember candidates who say "my hypothesis was wrong, and here's how I found out."

**How the numbers were measured:**
- **autocannon:** a fast load-testing tool. 50 connections, 20 seconds, 30 links.
- **`pg_stat_statements`:** a Postgres extension that counts exactly how many times each query ran.
- **No lost clicks:** 100 connections on one viral link for 12 seconds, and the Postgres count matched exactly after the flush.

**Before putting numbers on your resume:** rerun on your laptop. A safe resume line that doesn't depend on hardware:
> "Cut Postgres writes on the redirect path by 99.9% using a Redis cache-aside layer and batched click flushing, with zero lost clicks under concurrent load."

---

## 4. Interview questions for v2 (practise out loud)

1. **What is cache-aside?** Check the cache, on a miss read the DB and fill the cache.
2. **Why is Redis fast?** It keeps data in memory (RAM), not because it's "NoSQL."
3. **What's a TTL, and why use one?** It's an expiry time. Unpopular links fall out of the cache, which saves memory.
4. **What if a link's URL changes but the old one is still cached?** Stale data for up to the TTL. Fix: delete the cache key when the link is updated (called *cache invalidation*). v2 has no edit feature, so it isn't a problem yet.
5. **How do you count clicks without writing to Postgres every time?** `HINCRBY` in Redis, then a batched flush every 2 seconds.
6. **What if Redis crashes?** You lose up to ~2 seconds of click counts. That's acceptable for analytics. You could enable Redis persistence (AOF) to shrink that window.
7. **What if Postgres is down during a flush?** The snapshot stays in Redis and is retried on the next tick. Delayed, not lost.
8. **Why RENAME before reading the buffer?** It's atomic, so new clicks go to a fresh buffer and nothing is lost or double-counted.
9. **How are random codes kept unique?** The UNIQUE constraint rejects a clash, and the app retries with a new code.
10. **Why `secrets` and not `random`?** `random` is predictable. `secrets` is cryptographically secure.
11. **Did v2 make it faster?** Not in throughput on my machine. The app process was the bottleneck. It removed ~99.9% of DB writes, which matters when the DB is the scarce resource.
12. **What would you do next?** Run several stateless copies of the app behind a load balancer like Nginx, because the single Python process is now the limit.
