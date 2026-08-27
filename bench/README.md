# Capacity benchmark: OpenSERP vs SearXNG

Answers "how many searches per day can this actually serve?" — where a *served*
search means a **non-empty result set**, not an HTTP 200.

---

## Run it yourself, step by step

Six steps. The waiting in step 3 is not optional — it is the difference between
a capacity number and a record of the previous step's damage.

### 1. Start both stacks

```sh
cd /home/python/search-engine
(cd openserp && docker compose up -d)     # :7000
(cd searxing && docker compose up -d)     # :8081 (see searxing/.env)
```

### 2. Enable SearXNG's JSON API — it is 403 by default

Skip this and every SearXNG request scores as a failure.

```sh
docker exec -i -u root searxng-core sh -c 'cat >> /etc/searxng/settings.yml' <<'YAML'

search:
  formats:
    - html
    - json
YAML
(cd searxing && docker compose restart core) && sleep 15
```

Verify both answer:

```sh
curl -s 'http://127.0.0.1:7000/duckduckgo/search?text=hello+world&limit=10' | jq '.results|length'
curl -s 'http://127.0.0.1:8081/search?q=hello+world&format=json'          | jq '.results|length'
```

### 3. Wait for a healthy baseline

```sh
./bench/baseline.py            # one-shot: are we ready?
./bench/baseline.py --wait     # block until 3 consecutive healthy probes
```

Healthy is 20+ results. If it reads 0–10, engines are suspended and **any number
you measure now is worthless**. `--wait` polls every 60s until it clears.

### 4. Find the ceiling — closed-loop burst

Run the same command at rising concurrency and watch whether throughput moves:

```sh
./bench/burst.py openserp 48  4 --engine duckduckgo --offset 0
./bench/burst.py openserp 48 12 --engine duckduckgo --offset 100
./bench/burst.py openserp 48 24 --engine duckduckgo --offset 200

./bench/burst.py searxng  48  4 --offset 300
./bench/burst.py searxng  48 12 --offset 400
./bench/burst.py searxng  48 24 --offset 500
```

OpenSERP stays flat near 1 rps while p50 climbs 3.3s → 11.8s → 20.9s. That is
its per-engine `rate_requests: 60`/min limiter in `openserp/config.yaml` — a
queue, not saturation. Re-check `./bench/baseline.py` between runs.

### 5. Find what actually holds — open-loop sustained

This is the one that answers the per-day question, because that load arrives
steadily rather than as a burst the tool can absorb and then die on.
`500000/day = 5.8 rps`.

```sh
./bench/baseline.py --wait
./bench/sustain.py openserp 1.0 480 --engine duckduckgo    # 8 min at 1 rps

./bench/baseline.py --wait
./bench/sustain.py searxng  1.0 480                        # same rate, fair comparison

./bench/baseline.py --wait
./bench/sustain.py searxng  2.5 480                        # push until it breaks
```

Read the **bucket table**, not the average:

```
    bucket  sent  useful  useful%
   0-15       87      52    59.8%
  15-30       87       0     0.0%     <-- collapsed here
  30-45       87       0     0.0%
```

A run that averages 40% but ends at 0% is not 40% capacity, it is a collapse.
Only a run where *every* bucket stays high is a rate you can deploy against.

### 6. Or run the whole protocol unattended

```sh
./bench/protocol.py --minutes 8 --out bench/results.json
```

Steps A–D with automatic cooldowns between, ending in a comparison table with a
`held` column. `held=True` means no 30s bucket fell below 90% useful. Takes
~45 min of load plus recovery time. `--minutes 4` halves it.

---

## Configuration

```sh
export OPENSERP_URL=http://127.0.0.1:7000
export SEARXNG_URL=http://127.0.0.1:8081
```

To move SearXNG off 8080, set `SEARXNG_PORT` **in `searxing/.env`**, not in your
shell. Only `.env` is passed into the container, so a shell variable republishes
the host port while the app keeps listening on 8080 — you get `connection reset`.

## Two traps that produce fake numbers

**Always pass a fresh `--offset`.** OpenSERP caches results for
`cache.ttl_seconds` (120s). Replaying a query returns in ~1ms instead of ~2s, so
overlapping runs measure the cache: an early draft reported 68 rps that way. The
pool holds 9,591 unique queries; give each run a higher offset. `protocol.py`
tracks a global cursor and never reuses one.

**Never count HTTP 200 as success.** Both tools return 200 with zero results once
upstream blocks them — that misread 71 rps of pure failure as throughput. Every
script here gates on parsed result count and reports `fail_reasons`.

## Cooling down between runs

A hard run gets the IP rate-limited, and SearXNG stacks its own backoff on top.
Check `docker compose logs core` in `searxing/` for `suspended_time`: 180s for
brave / google cse / wikipedia, **3600s for startpage** after a CAPTCHA. Results
stay depressed long after the load stops.

Note that OpenSERP and SearXNG hit *different* endpoints for the same engine —
OpenSERP drives a headless browser, SearXNG calls `html.duckduckgo.com` — so one
can be blocked while the other still works. `./bench/baseline.py` shows both.

## Files

| file | what it does |
|---|---|
| `common.py` | query pool, `probe()`, `sustained()` — success = non-empty results |
| `baseline.py` | engine-pool health; `--wait` blocks until recovered |
| `burst.py` | closed-loop, N requests at fixed concurrency — finds the ceiling |
| `sustain.py` | open-loop at a fixed arrival rate — finds what holds |
| `protocol.py` | full A–D comparison with cooldowns, writes `results.json` |
