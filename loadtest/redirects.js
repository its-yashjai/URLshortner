// Fast redirect load test (Node). Usage: npm i autocannon@7 && node loadtest/redirects.js http://localhost:8000 CODE1,CODE2,... 50 20
// (url, comma-separated short codes, connections, seconds). Much faster than bench.py, which becomes the bottleneck itself above ~500 req/s.
const autocannon = require('autocannon');
const [,, url, codesCsv, conns, secs] = process.argv;
const codes = codesCsv.split(',');
let i = 0;
const inst = autocannon({
  url, connections: +conns, duration: +secs,
  requests: [{ method: 'GET', setupRequest: (r) => { r.path = '/' + codes[i++ % codes.length]; return r; } }],
}, (err, res) => {
  if (err) throw err;
  console.log(JSON.stringify({ rps: res.requests.average, p50: res.latency.p50, p97_5: res.latency.p97_5, p99: res.latency.p99, total: res.requests.total, errors: res.errors, timeouts: res.timeouts, status3xx: res['3xx'] }));
});
