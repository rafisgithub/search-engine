#!/usr/bin/env python3
"""Closed-loop burst: N requests at fixed concurrency. Finds the ceiling.

  ./bench/burst.py searxng  48 4
  ./bench/burst.py openserp 48 4 --engine duckduckgo

Run the SAME command at rising concurrency to see whether throughput actually
scales. OpenSERP stays flat at ~1 rps while latency grows linearly -- that is
its per-engine `rate_requests: 60`/min limiter, not saturation.
"""
import argparse, json, statistics as st, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import QUERIES, probe, url_for

p = argparse.ArgumentParser()
p.add_argument("which", choices=["openserp", "searxng"])
p.add_argument("n", type=int)
p.add_argument("conc", type=int)
p.add_argument("--engine", default="duckduckgo", help="openserp only")
p.add_argument("--offset", type=int, default=0,
               help="start index into the query pool; use a fresh offset each "
                    "run so you never replay a query inside OpenSERP's cache")
a = p.parse_args()

urls = [url_for(a.which, q, a.engine) for q in QUERIES[a.offset:a.offset + a.n]]
if len(urls) < a.n:
    sys.exit(f"query pool exhausted: offset {a.offset} + n {a.n} > {len(QUERIES)}")

t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=a.conc) as ex:
    res = list(ex.map(probe, urls))
wall = time.perf_counter() - t0

good = [r for r in res if r["n"] > 0]
lat = sorted(r["t"] for r in good) or [0]
why = {}
for r in res:
    if r["n"] == 0:
        why[r["why"]] = why.get(r["why"], 0) + 1

label = f"{a.which}/{a.engine}" if a.which == "openserp" else "searxng/json"
print(json.dumps({
    "label": label, "conc": a.conc, "n": len(res),
    "useful": len(good), "useful_pct": round(100 * len(good) / len(res), 1),
    "rps_useful": round(len(good) / wall, 2),
    "per_day_useful": int(len(good) / wall * 86400),
    "p50": round(st.median(lat), 2), "p95": round(lat[int(.95 * len(lat)) - 1], 2),
    "mean_results": round(st.mean([r["n"] for r in good]), 1) if good else 0,
    "fail_reasons": why,
}, indent=2))
