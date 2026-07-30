"""Isolates the cache-aside read benefit specifically: pre-creates N codes,
then hammers GET /{code} repeatedly against that fixed hot set. This mirrors
Phase 3's actual target scenario (hot reads on existing codes) instead of
the mixed create+read workload in bench.py, which spends real time on
cold-cache misses that dilute the caching signal on a single shared core.
"""
import asyncio
import random
import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
CONCURRENCY = int(sys.argv[2]) if len(sys.argv) > 2 else 80
DURATION_S = int(sys.argv[3]) if len(sys.argv) > 3 else 20
HOT_SET_SIZE = int(sys.argv[4]) if len(sys.argv) > 4 else 30

latencies = []
errors = 0
completed = 0
lock = asyncio.Lock()


async def vu(client: httpx.AsyncClient, codes, stop_at: float):
    global errors, completed
    while time.monotonic() < stop_at:
        code = random.choice(codes)
        start = time.monotonic()
        try:
            r = await client.get(f"{BASE_URL}/{code}", follow_redirects=False)
            ok = r.status_code == 302
        except Exception:
            ok = False
        elapsed = time.monotonic() - start
        async with lock:
            latencies.append(elapsed)
            completed += 1
            if not ok:
                errors += 1


async def main():
    async with httpx.AsyncClient(timeout=10.0) as client:
        codes = []
        for i in range(HOT_SET_SIZE):
            r = await client.post(
                f"{BASE_URL}/shorten", json={"long_url": f"https://example.com/hot/{i}"}
            )
            codes.append(r.json()["short_code"])
        # One warm-up pass so cache-aside configs populate Redis before timing starts.
        for c in codes:
            await client.get(f"{BASE_URL}/{c}", follow_redirects=False)

        stop_at = time.monotonic() + DURATION_S
        tasks = [vu(client, codes, stop_at) for _ in range(CONCURRENCY)]
        await asyncio.gather(*tasks)

    lat_sorted = sorted(latencies)
    p50 = lat_sorted[int(len(lat_sorted) * 0.50)] * 1000 if lat_sorted else 0
    p95 = lat_sorted[int(len(lat_sorted) * 0.95)] * 1000 if lat_sorted else 0
    p99 = lat_sorted[int(len(lat_sorted) * 0.99)] * 1000 if lat_sorted else 0
    throughput = completed / DURATION_S

    print(f"BASE_URL:        {BASE_URL}")
    print(f"Concurrency:     {CONCURRENCY} virtual users")
    print(f"Duration:        {DURATION_S}s")
    print(f"Hot set size:    {HOT_SET_SIZE} codes")
    print(f"Completed:       {completed} requests")
    print(f"Errors:          {errors}")
    print(f"Throughput:      {throughput:.1f} req/s")
    print(f"P50 latency:     {p50:.1f} ms")
    print(f"P95 latency:     {p95:.1f} ms")
    print(f"P99 latency:     {p99:.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())
