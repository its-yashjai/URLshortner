# Learning notes: v2

Read this after you're comfortable with v1. Same rule: be able to explain it out loud without looking.

---

## 1. The 30-second pitch (updated)

> "I built a URL shortener with FastAPI and Postgres in two measured versions. In v1, every click did a database write, and clicks on a popular link queued for the same row lock. In v2 I added Redis: popular links are served from an in-memory cache, and click counts are buffered in Redis and saved to Postgres in one batch every 2 seconds. I load-tested both on my laptop: throughput went up 17%, the slowest 1% of requests got 45% faster, database writes dropped by 99.9%, and no clicks were lost."

If they ask for more, add the bug story (section 3) and the "it depends where the bottleneck is" point. Both show you measured instead of assuming.

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

**Stats stay accurate:** `/stats` adds up three things: clicks already in Postgres, clicks waiting in `clicks:pending`, and clicks in a snapshot that's **in the middle of being saved**. That third part was a bug I found while benchmarking (see section 3).

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

### Your numbers (your laptop, Docker Desktop on Windows)
50 fake users clicking 30 popular links for 20 seconds. v1 ran 3 times and v2 ran 4 times, then averaged.

| | Clicks per second | p50 (typical click) | p99 (slowest 1%) | Errors |
|---|---|---|---|---|
| **v1** | 857 | 53 ms | 200 ms | 0 |
| **v2** | 999 | 47 ms | 111 ms | 0 |
| **Change** | **+17%** | −12% | **−45%** | |

**What each number means:**
- **Clicks per second (throughput):** how many clicks the server handled every second. Higher is better.
- **p50:** half of all clicks were answered faster than this. It's the "normal" experience.
- **p99:** 99 out of 100 clicks were faster than this. It's the "worst normal" experience.

**Why p99 improved the most:** in v1, the p99 (200 ms) was about 4× the p50 (53 ms). That gap means some clicks were **waiting in line** for the row lock on a popular link. v2 counts clicks in Redis, so that line disappears and the slow tail shrinks by almost half.

**Why the runs varied** (v1: 712 to 1,061, v2: 790 to 1,201): Docker Desktop shares the laptop with everything else running on it. That's why you ran each version several times and averaged. Say this in interviews; it shows you know one run isn't enough.

### Database writes (measured separately, with exact query counting)
| | Clicks | Postgres `UPDATE` statements |
|---|---|---|
| v1 | ~43,000 | **~43,000** (one per click) |
| v2 | ~41,000 | **10** (one batch every 2 seconds) |

That's the **99.9% fewer database writes**. It was counted with `pg_stat_statements`, a Postgres extension that counts exactly how many times each query ran.

### "It depends where the bottleneck is" (bonus point)
On a faster Linux machine, both versions ran at ~2,100 clicks/sec, with **no** speed difference. The reason: the database there was fast, and the single Python app process was the limit (busy at ~91% CPU). v2 only speeds things up when the **database** is the slow part, like on your laptop, or when the database is shared, remote, or under heavy load.

**Toll booth vs bridge:** v2 widens the bridge (the database). If the jam is at the toll booth (the Python app), widening the bridge doesn't help. On your laptop the jam was at the bridge, so it did.

### The bug you found (great interview story)
In one v2 run you sent **15,791** clicks, but `/stats` showed **15,756**, so 35 were "missing."
- **Cause:** during a flush, clicks are moved into a snapshot before being saved to Postgres. For that moment they were in neither `clicks:pending` nor Postgres, so `/stats` didn't count them. They weren't lost, just in transit.
- **Fix:** `/stats` now also counts clicks in any snapshot that's being saved.
- **Proof:** a regression test that fails on the old code (shows 0 instead of 4) and passes on the new code.

> "While benchmarking, I noticed the stats were slightly lower than the clicks I'd sent. I traced it to clicks in transit during the batch save, fixed it, and added a regression test."

### How the numbers were measured
- **autocannon** (`bench.js`): a fast load-testing tool that pretends to be 50 users, times every answer, and counts them.
- **Same laptop, back to back**, so v1 and v2 are compared fairly.
- **No lost clicks:** the clicks the server counted matched the clicks sent on every run.
- My first load tester (Python) was itself maxed out at 100% CPU, so it measured the tester, not the app. That's why I switched to autocannon.

### Resume line
> "Added a Redis cache-aside layer and batched click counting to a FastAPI + PostgreSQL URL shortener, cutting p99 redirect latency by 45% and database writes by 99.9%, with zero lost clicks, validated with load tests."

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
11. **Did v2 make it faster?** On my laptop, yes: +17% throughput and 45% lower p99, averaged over several runs. The biggest win was the slow tail, because the row-lock queue disappeared. On a faster machine where the database wasn't the bottleneck, throughput stayed the same, because the Python process was the limit there.
12. **What would you do next?** Run several stateless copies of the app behind a load balancer like Nginx, because the single Python process is now the limit.
13. **Why run the benchmark several times?** Results varied by up to ~30% between runs because Docker shares the laptop with other programs. Averaging several runs gives a number you can trust.
14. **Tell me about a bug you found.** The in-transit clicks bug in section 3: noticed it in the benchmark output, found the cause, fixed it, and added a regression test.
