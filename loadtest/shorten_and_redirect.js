// k6 load test: mixed write/read workload against the URL shortener.
//
// Workload shape: 20% shorten (write), 80% redirect (read) — approximates
// a realistic shortener where most codes get hit far more often than
// they're created. Run identically against v1 and v2 so the comparison
// in docs/scale-report.md is apples-to-apples.
//
// Usage:
//   BASE_URL=http://localhost:8000 k6 run loadtest/shorten_and_redirect.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

const shortenErrors = new Counter("shorten_errors");
const redirectErrors = new Counter("redirect_errors");

export const options = {
  scenarios: {
    ramping_load: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 50 },
        { duration: "1m", target: 150 },
        { duration: "1m", target: 300 },
        { duration: "1m", target: 300 },
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<500"],
  },
};

// Shared pool of codes created during the ramp, read back by later VUs —
// otherwise a pure-write or pure-read test wouldn't exercise the cache or
// the write buffer the way real traffic does.
const knownCodes = [];

export default function () {
  const doWrite = Math.random() < 0.2 || knownCodes.length === 0;

  if (doWrite) {
    const res = http.post(
      `${BASE_URL}/shorten`,
      JSON.stringify({ long_url: `https://example.com/page/${__VU}/${__ITER}` }),
      { headers: { "Content-Type": "application/json" } }
    );
    const ok = check(res, { "shorten 200": (r) => r.status === 200 });
    if (!ok) {
      shortenErrors.add(1);
    } else {
      const body = JSON.parse(res.body);
      knownCodes.push(body.short_code);
    }
  } else {
    const code = knownCodes[Math.floor(Math.random() * knownCodes.length)];
    const res = http.get(`${BASE_URL}/${code}`, { redirects: 0 });
    const ok = check(res, { "redirect 302": (r) => r.status === 302 });
    if (!ok) redirectErrors.add(1);
  }

  sleep(0.1);
}
