"""Shared core for the OpenSERP vs SearXNG capacity benchmarks.

Two things here are load-bearing, and both were mistakes in the first draft:

1. Success means a NON-EMPTY result set, never HTTP 200. Both tools happily
   return 200 with zero results once upstream engines block or get suspended.
   Counting 200s reported ~71 rps of pure failure.

2. Every request uses a UNIQUE query. OpenSERP caches results for
   `cache.ttl_seconds` (120s by default), and a repeated query returns in
   ~1ms instead of ~2s -- that measures the cache, not the search engine.
"""
import itertools, json, os, statistics as st, threading, time
import urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

OPENSERP = os.environ.get("OPENSERP_URL", "http://127.0.0.1:7000").rstrip("/")
SEARXNG = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8081").rstrip("/")

# ~130 terms -> C(130,2) = 8385 unique two-word queries. Long sustained runs
# burn ~600 queries each, so the pool has to be far bigger than the old 1275.
WORDS = """docker compose kubernetes postgres redis nginx python rust golang
terraform ansible grafana prometheus elasticsearch kafka rabbitmq mongodb sqlite
webpack vitejs django flask fastapi laravel symfony spring quarkus dotnet
llvm clang gcc cmake bazel gradle maven npm pnpm yarn deno bun vercel netlify
cloudflare fastly akamai wireguard openvpn nftables systemd podman containerd
graphql grpc protobuf avro parquet duckdb clickhouse cassandra scylla neo4j
airflow dagster dbt spark flink beam presto trino iceberg delta hudi
pytorch tensorflow jax onnx cuda triton vllm langchain qdrant weaviate milvus
pgvector opensearch solr lucene typesense meilisearch algolia
kotlin swift scala elixir erlang haskell ocaml zig nim crystal julia
svelte solidjs qwik astro remix nextjs nuxt angular ember backbone
istio linkerd envoy traefik haproxy consul vault nomad packer vagrant
argocd flux helm kustomize crossplane pulumi cdktf serverless knative
jaeger zipkin opentelemetry loki tempo mimir thanos cortex victoriametrics""".split()
QUERIES = [" ".join(c) for c in itertools.combinations(WORDS, 2)]


def url_for(which, query, engine=None):
    q = query.replace(" ", "+")
    if which == "openserp":
        return f"{OPENSERP}/{engine}/search?text={q}&lang=EN&limit=10"
    return f"{SEARXNG}/search?q={q}&format=json"


def probe(url, timeout=120):
    """Returns {t, code, n, why}. n is the parsed result count -- the only
    field that decides success."""
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bench/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        code = e.code
    except Exception as e:
        return {"t": time.perf_counter() - t0, "code": 0, "n": 0, "why": type(e).__name__}

    dt = time.perf_counter() - t0
    n, why = 0, ""
    try:
        d = json.loads(body)
        n = len(d.get("results", []))
        if not n:
            un = d.get("unresponsive_engines") or []
            why = ",".join(sorted({(u[0] if isinstance(u, list) else str(u)) for u in un})) \
                  or d.get("error", "empty")
    except Exception:
        why = "unparseable"
    return {"t": dt, "code": code, "n": n, "why": why}


# Global cursor so successive runs in one process never replay a query into
# OpenSERP's 120s cache.
_cursor = itertools.count(0)
_cursor_lock = threading.Lock()


def take_queries(n):
    with _cursor_lock:
        start = next(_cursor)
        for _ in range(n - 1):
            next(_cursor)
    if start + n > len(QUERIES):
        raise SystemExit(f"query pool exhausted: needed {n} from {start}, pool is {len(QUERIES)}")
    return QUERIES[start:start + n]


def sustained(which, rps, duration, engine=None, bucket=30, label=None, quiet=False):
    """Open-loop: fire at a FIXED arrival rate for `duration` seconds.

    This models how N/day actually arrives -- steadily -- rather than as a burst
    the tool can absorb and then die on. Buckets over time so collapse stays
    visible instead of being averaged away.
    """
    n = int(rps * duration)
    queries = take_queries(n)
    rows, lock = [], threading.Lock()
    t0 = time.perf_counter()

    def task(query):
        sent = time.perf_counter() - t0
        r = probe(url_for(which, query, engine))
        r["sent"] = sent
        with lock:
            rows.append(r)

    with ThreadPoolExecutor(max_workers=256) as ex:
        for i, q in enumerate(queries):
            delay = (t0 + i / rps) - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            ex.submit(task, q)
    wall = time.perf_counter() - t0

    good = [r for r in rows if r["n"] > 0]
    buckets = []
    for b in range(0, duration + bucket, bucket):
        sub = [r for r in rows if b <= r["sent"] < b + bucket]
        if not sub:
            continue
        g = [r for r in sub if r["n"] > 0]
        buckets.append({"t": b, "sent": len(sub), "useful": len(g),
                        "pct": round(100 * len(g) / len(sub), 1)})
    why = {}
    for r in rows:
        if r["n"] == 0:
            why[r["why"]] = why.get(r["why"], 0) + 1

    lat = sorted(r["t"] for r in good) or [0]
    out = {
        "label": label or (f"{which}/{engine}" if which == "openserp" else "searxng"),
        "target_rps": rps, "duration": duration, "sent": len(rows), "wall": round(wall, 1),
        "useful": len(good), "useful_pct": round(100 * len(good) / len(rows), 1) if rows else 0,
        "achieved_rps": round(len(good) / wall, 2),
        "per_day": int(len(good) / wall * 86400),
        "p50": round(st.median(lat), 2), "p95": round(lat[int(.95 * len(lat)) - 1], 2),
        "mean_results": round(st.mean([r["n"] for r in good]), 1) if good else 0,
        "buckets": buckets, "fail_reasons": why,
        # Held = no bucket collapsed. A run that averages 80% but ends at 0%
        # is NOT sustainable, and the average alone would hide that.
        "held": all(b["pct"] >= 90 for b in buckets) if buckets else False,
    }
    if not quiet:
        print(json.dumps(out), flush=True)
    return out


def baseline(timeout=60):
    """Health of SearXNG's engine pool right now."""
    r = probe(url_for("searxng", "baseline health probe"), timeout=timeout)
    return r["n"]
