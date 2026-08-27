#!/usr/bin/env python3
"""Open-loop sustained test at a fixed arrival rate. 500000/day = 5.8 rps.

  ./bench/sustain.py searxng  5.8 90
  ./bench/sustain.py openserp 5.8 45 --engine duckduckgo
  ./bench/sustain.py openserp 2.5 180 --offset 2000   # fresh queries, no cache hits
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import seed_cursor, sustained

p = argparse.ArgumentParser()
p.add_argument("which", choices=["openserp", "searxng"])
p.add_argument("rps", type=float)
p.add_argument("duration", type=int)
p.add_argument("--engine", default=None,
               help="openserp: required (duckduckgo/bing/google/...). "
                    "searxng: optional; pins one engine instead of the whole general category.")
p.add_argument("--bucket", type=int, default=15)
p.add_argument("--offset", type=int, default=0,
               help="start index into the query pool. The cursor is per-process, "
                    "so two runs in a row both start at 0 and the second one "
                    "measures OpenSERP's 120s cache instead of the engine. "
                    "Give every run a fresh offset (>= previous offset + rps*duration).")
a = p.parse_args()

if a.which == "openserp" and not a.engine:
    sys.exit("openserp needs --engine (duckduckgo/bing/google/yandex/baidu/ecosia)")

seed_cursor(a.offset)
r = sustained(a.which, a.rps, a.duration, engine=a.engine, bucket=a.bucket, quiet=True)
print(f"{'bucket':>10} {'sent':>5} {'useful':>7} {'useful%':>8}")
for b in r.pop("buckets"):
    print(f"{b['t']:>4}-{b['t']+a.bucket:<5} {b['sent']:>5} {b['useful']:>7} {b['pct']:>7}%")
print(json.dumps(r, indent=2))
