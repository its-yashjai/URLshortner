"""Tiny load tester: hammers the redirect endpoint and reports req/s + latency.

Usage:
    python loadtest/bench.py [BASE_URL] [CONCURRENCY] [SECONDS] [HOT_SET]
    python loadtest/bench.py http://localhost:8000 50 20 30

How it works:
  1. Creates HOT_SET short links (a small set of "popular" links, which is
     how real traffic looks: a few links get most of the clicks).
  2. Starts CONCURRENCY virtual users. Each one loops for SECONDS, picking a
     random hot link and requesting it (without following the redirect,
     so we measure OUR server, not example.com).
  3. Prints throughput and p50/p95/p99 latency.

Why p95 and not the average? Averages hide the slow tail. p95 = "95% of
users were at least this fast", which is closer to what users feel.
"""
import asyncio
import random
import statistics
import sys
import time

import httpx


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, round(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[k]


async def main(base, concurrency, seconds, hot_set):
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base, limits=limits, timeout=10) as client:
        codes = []
        for i in range(hot_set):
            r = await client.post("/shorten", json={"long_url": f"https://example.com/bench/{i}"})
            r.raise_for_status()
            codes.append(r.json()["short_code"])

        latencies, errors = [], 0
        deadline = time.perf_counter() + seconds

        async def user():
            nonlocal errors
            while time.perf_counter() < deadline:
                code = random.choice(codes)
                t0 = time.perf_counter()
                try:
                    r = await client.get(f"/{code}", follow_redirects=False)
                    ok = r.status_code == 302
                except httpx.HTTPError:
                    ok = False
                dt = (time.perf_counter() - t0) * 1000
                if ok:
                    latencies.append(dt)
                else:
                    errors += 1

        start = time.perf_counter()
        await asyncio.gather(*(user() for _ in range(concurrency)))
        elapsed = time.perf_counter() - start

    latencies.sort()
    print(f"target        {base}")
    print(f"virtual users {concurrency}   duration {seconds}s   hot set {hot_set} links")
    print(f"requests      {len(latencies)} ok, {errors} errors")
    print(f"throughput    {len(latencies) / elapsed:.1f} req/s")
    if latencies:
        print(f"latency ms    p50 {pct(latencies, 50):.1f}   p95 {pct(latencies, 95):.1f}   "
              f"p99 {pct(latencies, 99):.1f}   mean {statistics.mean(latencies):.1f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    base = args[0] if len(args) > 0 else "http://localhost:8000"
    conc = int(args[1]) if len(args) > 1 else 50
    secs = int(args[2]) if len(args) > 2 else 20
    hot = int(args[3]) if len(args) > 3 else 30
    asyncio.run(main(base, conc, secs, hot))
