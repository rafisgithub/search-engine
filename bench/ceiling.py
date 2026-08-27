#!/usr/bin/env python3
"""App-layer ceiling: how fast can each stack serve when upstream is removed?

Capacity here is bounded by upstream anti-bot, not by CPU -- but you can only
claim that after showing the software itself is far faster than the numbers
sustain.py reports. Both modes below make ZERO upstream search calls, so this
is safe to run during a cooldown without delaying engine recovery.

  searxng   ?engines=<suspended>  -- SearXNG sees the engine is suspended and
                                     returns before any HTTP call leaves the box.
                                     Exercises: parse -> orchestrate -> suspend
                                     check -> result container -> JSON encode.
                                     (Suspension state is in-process, NOT in
                                     Valkey -- `valkey-cli DBSIZE` is 0 here.)
  openserp  same query repeated   -- served from the dedicated endpoint cache
                                     (cache.ttl_seconds). Needs ONE successful
                                     cold search to prime, so it only works
                                     while the engine is not blocked.

  ./bench/ceiling.py searxng  --conc 1 2 4 8 16 32 64
  ./bench/ceiling.py openserp --conc 1 2 4 8 16 32 64 --engine duckduckgo
"""
import argparse, json, statistics as st, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OPENSERP, SEARXNG, p95_of, probe

p = argparse.ArgumentParser()
p.add_argument("which", choices=["openserp", "searxng"])
p.add_argument("--conc", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32, 64])
p.add_argument("--seconds", type=float, default=6.0, help="load per concurrency level")
p.add_argument("--engine", default="duckduckgo")
p.add_argument("--suspended", default="startpage",
               help="searxng: an engine currently suspended, so the request "
                    "short-circuits in Valkey instead of going upstream")
a = p.parse_args()

# --- build a URL that provably does not reach the internet -------------------
if a.which == "searxng":
    url = f"{SEARXNG}/search?q=ceiling+probe&format=json&engines={a.suspended}"
    r = probe(url, timeout=30)
    if r["code"] != 200:
        sys.exit(f"searxng not answering: {r}")
    if r["n"] > 0:
        sys.exit(f"'{a.suspended}' is NOT suspended -- it returned {r['n']} results, "
                 f"so this would hit upstream. Pick a suspended engine.")
    print(f"# short-circuit confirmed: 0 results, {r['why']}, {r['t']*1000:.1f}ms")
else:
    url = f"{OPENSERP}/{a.engine}/search?text=ceiling+cache+prime&lang=EN&limit=10"
    r = probe(url, timeout=120)               # cold: primes the cache
    if r["n"] == 0:
        sys.exit(f"cannot prime openserp cache -- cold search returned 0 "
                 f"({r['code']}, {r['why']}). Engine is blocked or rate-limited; "
                 f"retry when ./bench/baseline.py shows it healthy.")
    warm = probe(url, timeout=120)
    print(f"# cold {r['t']:.2f}s ({r['n']} results) -> warm {warm['t']*1000:.1f}ms "
          f"({warm['n']} results) = cache hit")

print(f"{'conc':>5} {'reqs':>7} {'rps':>9} {'p50ms':>8} {'p95ms':>8} {'ok%':>6}")
rows = []
for c in a.conc:
    stop = time.perf_counter() + a.seconds
    res = []

    def worker():
        out = []
        while time.perf_counter() < stop:
            out.append(probe(url, timeout=30))
        return out

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=c) as ex:
        for f in [ex.submit(worker) for _ in range(c)]:
            res.extend(f.result())
    wall = time.perf_counter() - t0

    # For the cache/short-circuit path, HTTP 200 IS the right success signal:
    # we are timing the app, and searxng's short circuit returns 0 results by
    # design. This is the one place `probe`'s result-count gate does not apply.
    ok = [r for r in res if r["code"] == 200]
    lat = sorted(r["t"] for r in ok) or [0]
    row = {"conc": c, "reqs": len(res), "rps": round(len(res) / wall, 1),
           "p50ms": round(1000 * st.median(lat), 1),
           "p95ms": round(1000 * p95_of(lat), 1),
           "ok_pct": round(100 * len(ok) / len(res), 1) if res else 0}
    rows.append(row)
    print(f"{c:>5} {row['reqs']:>7} {row['rps']:>9} {row['p50ms']:>8} "
          f"{row['p95ms']:>8} {row['ok_pct']:>5}%")

best = max(rows, key=lambda r: r["rps"])
print(f"\npeak {best['rps']} rps at concurrency {best['conc']} "
      f"= {int(best['rps']*86400):,}/day of app-layer capacity")
print("NOTE: upstream removed. This is the software ceiling, not search capacity.")
