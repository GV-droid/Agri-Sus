"""Latency, throughput and Gemini benchmark for a running Agri Sus server (the numbers in docs/PERFORMANCE_REPORT.md).

    python scripts/benchmark.py https://<cloud-run-url>                 # cheap phases: headers get sim_cached
    python scripts/benchmark.py https://<cloud-run-url> sim_new chat    # pick phases
    python scripts/benchmark.py https://<cloud-run-url> all

Phases
    headers      cache / compression headers of the page, its assets and the big API responses
    get          sequential GET latency of every read endpoint
    sim_cached   every preset scenario (pre-solved at start-up), with and without gzip
    sim_new      5 new optimiser solves (fixed budget, least cost, trade shock, zero depletion), then each again
    consistency  a new run, then /api/run/{id} on 12 fresh connections (shows per-instance state when >1 instance)
    conc         cached /api/simulate at 1/5/10/20 parallel users and /api/meta at 1/10/25
    document     the policy document agent: first write (grounded research + Gemini), reuse, regenerate, Hindi;
                 the import-displacement check (cold and cached). Uses a temporary saved policy, deleted afterwards
    chat         the five co-pilot questions of the demo video (Gemini agent, SSE)
    forecast     the Gemini live outlook and the scenario built from it

sim_new, consistency, conc, document, chat and forecast run real solves and Gemini calls: they cost money, scale the
service out, and count towards the per-IP rate limits in backend/app/main.py. Needs `requests`
(pip install requests). Results are printed and appended to benchmark_results.jsonl (set OUT to change the file).
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This script needs the requests package: pip install requests")

if len(sys.argv) < 2 or not sys.argv[1].startswith("http"):
    sys.exit(__doc__)
U = sys.argv[1].rstrip("/")
PHASES = sys.argv[2:] or ["headers", "get", "sim_cached"]
OUT = os.environ.get("OUT", "benchmark_results.jsonl")
S = requests.Session()
S.headers["Accept-Encoding"] = "gzip"


def call(method, path, body=None, gz=True, timeout=600, session=None):
    s = session or S
    h = {"Accept-Encoding": "gzip"} if gz else {"Accept-Encoding": "identity"}
    t0 = time.perf_counter()
    r = s.request(method, U + path, json=body, headers=h, timeout=timeout, stream=True)
    raw = r.raw.read(decode_content=False)
    total = time.perf_counter() - t0
    enc = r.headers.get("content-encoding")
    return {"status": r.status_code, "total": total, "wire": len(raw), "enc": enc, "headers": dict(r.headers),
            "body": gzip.decompress(raw) if enc == "gzip" else raw}


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def emit(d):
    with open(OUT, "a") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(json.dumps(d, ensure_ascii=False), flush=True)


def summ(name, rs, extra=None):
    ok = [r for r in rs if r["status"] == 200]
    t = [r["total"] * 1000 for r in ok]
    d = {"name": name, "n": len(rs), "ok": len(ok), "errors": len(rs) - len(ok)}
    if t:
        d.update({"p50_ms": round(pct(t, 50)), "p90_ms": round(pct(t, 90)), "p99_ms": round(pct(t, 99)),
                  "min_ms": round(min(t)), "max_ms": round(max(t)), "wire_kb": round(ok[0]["wire"] / 1024, 1),
                  "size_kb": round(len(ok[0]["body"]) / 1024, 1), "enc": ok[0]["enc"]})
    d.update(extra or {})
    emit(d)
    return d


def parse(r):
    return json.loads(r["body"]) if r["status"] == 200 else {"detail": r["body"][:300].decode(errors="ignore")}


def baseline_run():
    return parse(call("POST", "/api/simulate", {"scenario": "baseline"}))


def phase_headers():
    keep = ("content-encoding", "cache-control", "content-type", "etag", "alt-svc")
    page = call("GET", "/")
    paths = ["/", "/api/geo", "/api/meta"] + re.findall(r'(?:src|href)="(/assets/[^"]+)"', page["body"].decode())
    for path in paths:
        r = call("GET", path)
        emit({"name": f"headers {path}", "status": r["status"], "wire_kb": round(r["wire"] / 1024, 1),
              "size_kb": round(len(r["body"]) / 1024, 1),
              **{k: v for k, v in r["headers"].items() if k.lower() in keep}})


def phase_get():
    sim = baseline_run()
    rid = sim["run_id"]
    code = next(d["district_code"] for d in sim["districts"]
                if "Sangrur" in str(d.get("district_name", d.get("district", ""))))
    plan = [("health", "/api/health", 30), ("meta", "/api/meta", 20), ("geo", "/api/geo", 10),
            ("districts", "/api/districts", 10), ("model-card", "/api/model-card", 10), ("trade", "/api/trade", 10),
            ("trade/decompose", "/api/trade/decompose?country=Indonesia&product=palm%20oil&pct=100", 10),
            ("district (Sangrur)", f"/api/district/{code}?run_id={rid}", 10),
            ("district candidates", f"/api/district/{code}/candidates?run_id={rid}", 10),
            ("run/{id}", f"/api/run/{rid}", 10), ("index.html", "/?notour", 10)]
    for name, path, n in plan:
        summ(name, [call("GET", path) for _ in range(n)])


def phase_sim_cached():
    for sc in parse(call("GET", "/api/meta"))["scenarios"]:
        sid = sc["id"]
        rs = [call("POST", "/api/simulate", {"scenario": sid}) for _ in range(3)]
        summ(f"simulate cached: {sid}", rs, {"solver_seconds": parse(rs[0]).get("solver", {}).get("seconds")})
    summ("simulate cached: baseline, no gzip",
         [call("POST", "/api/simulate", {"scenario": "baseline"}, gz=False) for _ in range(3)])


def phase_sim_new():
    cases = [("fixed Rs 5,000 cr", "baseline", {"incentive_budget_cr": 5000}),
             ("fixed Rs 7,500 cr", "baseline", {"incentive_budget_cr": 7500}),
             ("least cost, 25% target", "baseline", {"budget_mode": "min_budget"}),
             ("least cost + Indonesia halt + El Nino 2028", "el_nino_2028",
              {"budget_mode": "min_budget", "trade_cuts": [{"country": "Indonesia", "product": "palm oil", "pct": 100}]}),
             ("zero-depletion groundwater, Rs 9,000 cr", "baseline", {"gw_mode": "zero_depletion", "incentive_budget_cr": 9000})]
    for name, sc, ov in cases:
        r = call("POST", "/api/simulate", {"scenario": sc, "overrides": ov})
        b = parse(r)
        sv = b.get("solver", {})
        summ(f"simulate new solve: {name}", [r], {"solver_seconds": sv.get("seconds"), "lp_status": sv.get("status"),
                                                   "variables": sv.get("variables"), "constraints": sv.get("constraints"),
                                                   "detail": b.get("detail")})
        summ(f"simulate same request again: {name}", [call("POST", "/api/simulate", {"scenario": sc, "overrides": ov})])


def phase_consistency():
    budget = 5000 + int(time.time()) % 997  # a request nobody has made before, so the run exists on one instance only
    b = parse(call("POST", "/api/simulate", {"scenario": "baseline", "overrides": {"incentive_budget_cr": budget}}))
    rid = b["run_id"]
    rs = [call("GET", f"/api/run/{rid}", session=requests.Session()) for _ in range(12)]
    statuses = [r["status"] for r in rs]
    summ("run/{new id} on fresh connections", rs, {"statuses": {str(s): statuses.count(s) for s in set(statuses)}})


def phase_conc():
    def burst(name, method, path, payload, conc, n):
        sessions = [requests.Session() for _ in range(conc)]
        for s in sessions:
            s.headers["Accept-Encoding"] = "gzip"
            s.get(U + "/api/health", timeout=60)  # open the connection first; not timed
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(conc) as ex:
            futs = [ex.submit(call, method, path, payload, True, 600, sessions[i % conc]) for i in range(n)]
            rs = []
            for f in futs:
                try:
                    rs.append(f.result())
                except Exception as e:
                    rs.append({"status": 0, "total": 0, "wire": 0, "enc": None, "body": str(e).encode()})
        wall = time.perf_counter() - t0
        summ(f"{name} @ concurrency {conc}", rs, {"wall_s": round(wall, 2),
                                                 "throughput_rps": round(sum(r["status"] == 200 for r in rs) / wall, 2),
                                                 "statuses": sorted({r["status"] for r in rs})})
    for conc, n in [(1, 10), (5, 25), (10, 50), (20, 60)]:
        burst("simulate cached baseline", "POST", "/api/simulate", {"scenario": "baseline"}, conc, n)
    for conc, n in [(1, 20), (10, 100), (25, 150)]:
        burst("meta", "GET", "/api/meta", None, conc, n)


def sse_document(body, timeout=600):
    """POST /api/documents (SSE): time to the response headers, to each finished step and to the document."""
    t0 = time.perf_counter()
    r = S.post(U + "/api/documents", json=body, stream=True, timeout=timeout)
    ev = {"status": r.status_code, "t_headers": time.perf_counter() - t0, "steps": {}, "doc": None, "reused": None,
          "errors": []}
    if r.status_code != 200:
        ev["errors"].append(r.text[:300])
        return ev
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        e = json.loads(line[6:])
        now = round(time.perf_counter() - t0, 1)
        if e.get("type") == "step" and e.get("status") in ("done", "failed", "skipped"):
            ev["steps"][e["id"]] = [e["status"], now]
        elif e.get("type") == "document":
            ev["doc"], ev["reused"] = e["document"], e["reused"]
        elif e.get("type") == "error":
            ev["errors"].append(e.get("message"))
    ev["t_done"] = time.perf_counter() - t0
    return ev


def phase_document():
    """The policy document agent on a temporary saved policy (deleted afterwards): first write (grounded scheme and
    duty research + Gemini writer), reopen, reuse, a regenerate with instructions (research reused) and a Hindi
    version; then the deterministic import-displacement check, cold and cached."""
    rid = baseline_run()["run_id"]
    pol = parse(call("POST", "/api/policies", {"run_id": rid, "name": "Benchmark document policy"}))
    if not pol.get("id"):
        return emit({"name": "document: could not save a temporary policy", "detail": pol})
    run = f"p-{pol['id']}"
    try:
        for name, body in [("first write, India (research + writer)", {"run_id": run}),
                           ("reuse (saved version, no model call)", {"run_id": run}),
                           ("regenerate with instructions (research reused)",
                            {"run_id": run, "regenerate": True, "instructions": "Lead with rice-fallow pulses."}),
                           ("first write, Punjab in Hindi", {"run_id": run, "state": "Punjab", "language": "hi"})]:
            e = sse_document(body)
            d = e["doc"] or {}
            emit({"name": f"document: {name}", "status": e["status"], "t_headers_s": round(e["t_headers"], 2),
                  "t_done_s": round(e.get("t_done", 0), 1), "steps": e["steps"], "reused": e["reused"],
                  "version": d.get("version"), "generator": d.get("generator"), "model": d.get("model"),
                  "words": d.get("words"), "degraded": d.get("degraded"), "citations": len(d.get("citations") or []),
                  "schemes": len(((d.get("research") or {}).get("schemes") or {}).get("schemes") or []),
                  "verdicts": {g["group"]: g["verdict"] for g in (d.get("import_check") or {}).get("groups", [])},
                  "errors": e["errors"]})
        summ("document: GET saved version", [call("GET", f"/api/documents/{run}") for _ in range(5)])
        summ("import check: cold", [call("GET", f"/api/import-check/{rid}?state=Bihar")])
        summ("import check: cached", [call("GET", f"/api/import-check/{rid}?state=Bihar") for _ in range(5)])
    finally:
        call("DELETE", f"/api/policies/{pol['id']}")
        call("DELETE", f"/api/policies/{pol['id']}?purge=true")


phase_brief = phase_document  # the old phase name


def sse_chat(question, lang="en", timeout=420):
    t0 = time.perf_counter()
    r = S.post(U + "/api/agent/chat", json={"messages": [{"role": "user", "content": question}], "language": lang},
               stream=True, timeout=timeout)
    ev = {"status": r.status_code, "t_headers": time.perf_counter() - t0, "tools": [], "text": "", "citations": 0,
          "model": None, "backend": None, "errors": [], "t_first_tool": None, "t_done": None}
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        now = time.perf_counter() - t0
        e = json.loads(line[6:])
        t = e.get("type")
        if t == "model":
            ev["model"], ev["backend"] = e.get("model"), e.get("backend")
        elif t == "tool_call":
            ev["tools"].append(e.get("name"))
            ev["t_first_tool"] = ev["t_first_tool"] or now
        elif t == "text":
            ev["text"] += e.get("text", "")
        elif t == "citations":
            ev["citations"] += len(e.get("citations", e.get("items", e.get("sources", []))) or [])
        elif t == "error":
            ev["errors"].append(e.get("text"))
        elif t == "done":
            ev["t_done"] = now
    ev["t_done"] = ev["t_done"] or time.perf_counter() - t0
    return ev


def phase_chat():
    questions = [
        ("single tool: national scorecard", "What is the national sustainability scorecard for the current plan? Be brief.", "en"),
        ("grounded outlook + re-plan", "What is the latest IMD / ENSO outlook and edible-oil import duty? Re-run the plan with it.", "en"),
        ("compound least-cost what-if", "If Indonesia halts palm-oil exports and El Niño hits in 2028, what is the least annual "
                                        "budget that still cuts imports 25%, with no farmer losing income?", "en"),
        ("plain English to BigQuery SQL", "Which 10 districts have the worst groundwater stage and how much paddy do they grow? "
                                          "Use BigQuery.", "en"),
        ("Hindi", "पंजाब में धान की जगह कौन सी फसल उगाएँ?", "hi")]
    for name, q, lang in questions:
        e = sse_chat(q, lang)
        emit({"name": f"agent: {name}", "status": e["status"], "model": e["model"], "backend": e["backend"],
              "t_headers_s": round(e["t_headers"], 2),
              "t_first_tool_s": round(e["t_first_tool"], 1) if e["t_first_tool"] else None,
              "t_done_s": round(e["t_done"], 1), "tools": e["tools"], "citations": e["citations"],
              "errors": e["errors"], "answer_chars": len(e["text"]), "answer_head": e["text"][:400]})


def phase_forecast():
    r = call("GET", "/api/forecast")
    summ("forecast (Gemini live outlook)", [r], {"detail": parse(r).get("detail")})
    r = call("POST", "/api/simulate", {"scenario": "gemini_outlook"})
    summ("simulate gemini_outlook scenario", [r], {"solver_seconds": parse(r).get("solver", {}).get("seconds")})


if __name__ == "__main__":
    for ph in (["headers", "get", "sim_cached", "sim_new", "consistency", "conc", "document", "chat", "forecast"]
               if PHASES == ["all"] else PHASES):
        if f"phase_{ph}" not in globals():
            sys.exit(f"unknown phase {ph!r}; see: python scripts/benchmark.py")
        print(f"===== {ph} =====", flush=True)
        globals()[f"phase_{ph}"]()
