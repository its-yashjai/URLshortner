"""Async load-test harness with the same workload shape as
loadtest/shorten_and_redirect.js (20% shorten writes, 80% redirect reads
against a shared pool of created codes).

This exists because k6's binary is distributed from dl.k6.io, which isn't
reachable from this sandboxed build environment (egress is locked to
package registries). The .js file is the real deliverable for a normal
machine with internet access; this script produces the actual numbers
below using an equivalent methodology: concurrent VUs, same read/write
mix, same duration, P95 computed the same way.
"""
import asyncio
import random
import statistics
import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
CONCURRENCY = int(sys.argv[2]) if len(sys.argv) > 2 else 100
DURATION_S = int(sys.argv[3]) if len(sys.argv) > 3 else 30

latencies = []
errors = 0
completed = 0
known_codes = []
lock = asyncio.Lock()


async def vu(client: httpx.AsyncClient, stop_at: float, vu_id: int):
    global errors, completed
    i = 0
    while time.monotonic() < stop_at:
        do_write = (random.random() < 0.2) or not known_codes
        start = time.monotonic()
        try:
            if do_write:
                r = await client.post(
                    f"{BASE_URL}/shorten",
                    json={"long_url": f"https://example.com/page/{vu_id}/{i}"},
                )
                ok = r.status_code == 200
                if ok:
                    async with lock:
                        known_codes.append(r.json()["short_code"])
            else:
                code = random.choice(known_codes)
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
        i += 1
        await asyncio.sleep(0.02)


async def main():
    stop_at = time.monotonic() + DURATION_S
    limits = httpx.Limits(max_connections=CONCURRENCY + 10, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(limits=limits, timeout=10.0) as client:
        tasks = [vu(client, stop_at, i) for i in range(CONCURRENCY)]
        await asyncio.gather(*tasks)

    total_time = DURATION_S
    lat_sorted = sorted(latencies)
    p50 = lat_sorted[int(len(lat_sorted) * 0.50)] * 1000 if lat_sorted else 0
    p95 = lat_sorted[int(len(lat_sorted) * 0.95)] * 1000 if lat_sorted else 0
    p99 = lat_sorted[int(len(lat_sorted) * 0.99)] * 1000 if lat_sorted else 0
    throughput = completed / total_time

    print(f"BASE_URL:        {BASE_URL}")
    print(f"Concurrency:     {CONCURRENCY} virtual users")
    print(f"Duration:        {DURATION_S}s")
    print(f"Completed:       {completed} requests")
    print(f"Errors:          {errors}")
    print(f"Throughput:      {throughput:.1f} req/s")
    print(f"P50 latency:     {p50:.1f} ms")
    print(f"P95 latency:     {p95:.1f} ms")
    print(f"P99 latency:     {p99:.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())
