#!/usr/bin/env python3
"""Is the engine pool healthy enough to trust a measurement?

Run this before AND after every load step. A number measured while engines are
suspended is not a capacity figure, it's a record of the previous run's damage.

  ./bench/baseline.py                 # one probe of each tool
  ./bench/baseline.py --wait          # block until SearXNG recovers
  ./bench/baseline.py --wait --streak 3
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OPENSERP, SEARXNG, probe, url_for

p = argparse.ArgumentParser()
p.add_argument("--wait", action="store_true", help="poll until healthy")
p.add_argument("--healthy", type=int, default=20, help="result count meaning 'recovered'")
p.add_argument("--streak", type=int, default=3,
               help="consecutive healthy probes required; the baseline flaps "
                    "between 0 and 30 as engines cycle out of suspension, so "
                    "one good probe proves nothing")
p.add_argument("--interval", type=int, default=60)
a = p.parse_args()


def check():
    sx = probe(url_for("searxng", "baseline health probe"))
    os_ = probe(url_for("openserp", "baseline health probe", "duckduckgo"))
    return sx, os_


def line(sx, os_):
    return (f"[{time.strftime('%H:%M:%S')}] "
            f"searxng: {sx['n']:>2} results" + (f" ({sx['why']})" if sx["n"] == 0 else "") +
            f"   |   openserp/ddg: {os_['n']:>2} results" + (f" ({os_['why']})" if os_["n"] == 0 else ""))


if not a.wait:
    sx, os_ = check()
    print(line(sx, os_))
    print(f"\nsearxng  {SEARXNG}\nopenserp {OPENSERP}")
    print(f"\nhealthy = {a.healthy}+ results. searxng at {sx['n']} -> "
          + ("READY" if sx["n"] >= a.healthy else "NOT READY, wait before measuring"))
    sys.exit(0 if sx["n"] >= a.healthy else 1)

hits = 0
while True:
    sx, os_ = check()
    if sx["n"] >= a.healthy:
        hits += 1
        print(line(sx, os_) + f"   healthy {hits}/{a.streak}")
        if hits >= a.streak:
            print("\nRECOVERED — safe to run the next step.")
            sys.exit(0)
    else:
        if hits:
            print(line(sx, os_) + "   streak broken, restarting")
        else:
            print(line(sx, os_))
        hits = 0
    time.sleep(a.interval)
