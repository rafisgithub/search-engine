#!/usr/bin/env python3
"""Open-loop sustained test at a fixed arrival rate. 500000/day = 5.8 rps.

  ./bench/sustain.py searxng  5.8 90
  ./bench/sustain.py openserp 5.8 45 --engine duckduckgo
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import sustained

p = argparse.ArgumentParser()
p.add_argument("which", choices=["openserp", "searxng"])
p.add_argument("rps", type=float)
p.add_argument("duration", type=int)
p.add_argument("--engine", default="duckduckgo")
p.add_argument("--bucket", type=int, default=15)
a = p.parse_args()

r = sustained(a.which, a.rps, a.duration, engine=a.engine, bucket=a.bucket, quiet=True)
print(f"{'bucket':>10} {'sent':>5} {'useful':>7} {'useful%':>8}")
for b in r.pop("buckets"):
    print(f"{b['t']:>4}-{b['t']+a.bucket:<5} {b['sent']:>5} {b['useful']:>7} {b['pct']:>7}%")
print(json.dumps(r, indent=2))
