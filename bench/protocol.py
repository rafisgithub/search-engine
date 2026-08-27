#!/usr/bin/env python3
"""Production capacity protocol: find the SUSTAINABLE rate for each tool.

Burst ceilings are useless for capacity planning -- both tools absorb a burst
and then collapse. What matters is the highest fixed arrival rate each one can
hold for a long window without any bucket falling over.

Protocol, with real cooldowns between steps so each run doesn't measure the
previous run's damage:

  A  openserp/duckduckgo  @ 1.0 rps   -- its per-engine limiter cap
  B  searxng              @ 1.0 rps   -- same rate, apples to apples
  C  searxng              @ adaptive  -- push up if B held, back off if not
  D  openserp 3 engines   @ 1.0 each  -- its real scale-out path

Usage:  ./bench/protocol.py [--minutes 8] [--out results.json]
"""
import argparse, json, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import baseline, sustained

p = argparse.ArgumentParser()
p.add_argument("--minutes", type=float, default=8.0, help="duration of each load step")
p.add_argument("--cooldown", type=int, default=240, help="min seconds between steps")
p.add_argument("--healthy", type=int, default=20, help="baseline results meaning 'recovered'")
p.add_argument("--out", default="bench/results.json")
a = p.parse_args()
DUR = int(a.minutes * 60)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_healthy(why, max_wait, streak=1):
    """Cooldown: fixed floor, then poll until SearXNG's engine pool recovers.
    Without this, step N measures the damage step N-1 did.

    `streak` consecutive healthy probes are required: the baseline flaps
    between 0 and 30 while engines cycle in and out of suspension, so one good
    probe proves nothing."""
    log(f"cooldown ({why}): floor {a.cooldown}s")
    time.sleep(a.cooldown)
    deadline, hits = time.time() + max_wait, 0
    while time.time() < deadline:
        n = baseline()
        if n >= a.healthy:
            hits += 1
            log(f"  baseline={n} healthy ({hits}/{streak})")
            if hits >= streak:
                return n
        else:
            if hits:
                log(f"  baseline={n} -- streak broken, restarting")
            hits = 0
            log(f"  baseline={n} (<{a.healthy}), waiting 60s")
        time.sleep(60)
    n = baseline()
    log(f"  proceeding un-recovered: baseline={n} -- results below are a LOWER BOUND")
    return n


def par_openserp(engines, dur):
    """Run several OpenSERP engines concurrently; each has its own rate limiter,
    so aggregate throughput is the sum."""
    out, lock = [], threading.Lock()

    def one(eng, rps):
        r = sustained("openserp", rps, dur, engine=eng,
                      label=f"openserp/{eng}", quiet=True)
        with lock:
            out.append(r)

    ts = [threading.Thread(target=one, args=(e, r)) for e, r in engines]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


results = []
log(f"start: {a.minutes}min steps, {a.cooldown}s min cooldown")
b0 = baseline()
log(f"initial searxng baseline: {b0} results")
if b0 < a.healthy:
    wait_healthy("pre-flight", max_wait=2700, streak=3)

# --- A: OpenSERP at its per-engine cap -------------------------------------
log("STEP A: openserp/duckduckgo @ 1.0 rps")
A = sustained("openserp", 1.0, DUR, engine="duckduckgo", label="A openserp/ddg @1.0", quiet=True)
log(f"  -> useful {A['useful_pct']}%  {A['achieved_rps']} rps  {A['per_day']}/day  held={A['held']}")
results.append(A)
wait_healthy("after A", max_wait=900)

# --- B: SearXNG at the same rate -------------------------------------------
log("STEP B: searxng @ 1.0 rps")
B = sustained("searxng", 1.0, DUR, label="B searxng @1.0", quiet=True)
log(f"  -> useful {B['useful_pct']}%  {B['achieved_rps']} rps  {B['per_day']}/day  held={B['held']}")
results.append(B)
wait_healthy("after B", max_wait=900)

# --- C: SearXNG, adaptive ---------------------------------------------------
rate_c = 2.5 if B["held"] else 0.5
log(f"STEP C: searxng @ {rate_c} rps ({'pushing up' if B['held'] else 'backing off'})")
C = sustained("searxng", rate_c, DUR, label=f"C searxng @{rate_c}", quiet=True)
log(f"  -> useful {C['useful_pct']}%  {C['achieved_rps']} rps  {C['per_day']}/day  held={C['held']}")
results.append(C)
wait_healthy("after C", max_wait=900)

# --- D: OpenSERP scale-out across engines -----------------------------------
# Each engine at its own configured limit: baidu is throttled to 6/min in
# openserp/docker-compose.yaml, the others to 60/min.
ENGINES = [("duckduckgo", 1.0), ("bing", 1.0), ("baidu", 0.1)]
log("STEP D: openserp " + " ".join(f"{e}@{r}rps" for e, r in ENGINES))
D = par_openserp(ENGINES, DUR)
agg_rps = round(sum(r["achieved_rps"] for r in D), 2)
agg = {"label": "D openserp 3-engine aggregate", "target_rps": 2.1, "duration": DUR,
       "sent": sum(r["sent"] for r in D), "useful": sum(r["useful"] for r in D),
       "useful_pct": round(100 * sum(r["useful"] for r in D) / max(1, sum(r["sent"] for r in D)), 1),
       "achieved_rps": agg_rps, "per_day": int(agg_rps * 86400),
       "held": all(r["held"] for r in D), "parts": D}
log(f"  -> aggregate {agg_rps} rps  {agg['per_day']}/day  held={agg['held']}")
for r in D:
    log(f"     {r['label']}: {r['useful_pct']}% useful, {r['achieved_rps']} rps, held={r['held']}")
results.append(agg)

Path(a.out).write_text(json.dumps(results, indent=2))
log(f"wrote {a.out}")

print("\n" + "=" * 78)
print(f"{'step':<32} {'useful%':>8} {'rps':>7} {'per-day':>11} {'held':>6}")
print("-" * 78)
for r in results:
    print(f"{r['label']:<32} {r['useful_pct']:>7}% {r['achieved_rps']:>7} "
          f"{r['per_day']:>11,} {str(r['held']):>6}")
print("=" * 78)
print("held=True means no 30s bucket dropped below 90% useful -> sustainable.")
print("held=False means it collapsed mid-run -> the per-day figure is fiction.")
