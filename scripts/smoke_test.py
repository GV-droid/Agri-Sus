"""End-to-end API smoke test for a running Agri Sus server (stdlib only).

    python scripts/smoke_test.py                       # http://localhost:8080
    python scripts/smoke_test.py https://<cloud-run-url>

Checks the invariants the UI and the agent rely on: every scenario solves, the plan beats "do nothing" in every
year, the hard guardrails hold (farmer income >= today in every district, cereal floor, budget), levers move the
plan in the expected direction, mandate scenarios apply their lever, input validation rejects bad requests, and
the decision trace, policy brief and co-pilot respond. Also covers the policy-instrument mix (fixed budget and
least-cost modes), the fiscal ledger and MROPI tiers, bilateral trade shocks (India Data Portal import statistics),
next-crop recommendations, the BigQuery ML champion/challenger state, saved policies (snapshot, reopen without
re-solving, rename, soft delete, restore, purge), shared chat sessions, multi-country x multi-commodity trade shocks
and plan horizons other than four years. Takes ~4 minutes on a cold server.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  - ' + detail if detail else ''}", flush=True)


def call(path: str, body: dict | None = None, timeout: int = 300):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def send(method: str, path: str, body: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def simulate(scenario: str = "baseline", **overrides):
    s, r = call("/api/simulate", {"scenario": scenario, "overrides": overrides})
    if s != 200:
        raise RuntimeError(f"simulate {scenario} {overrides} -> HTTP {s}: {r}")
    return r


def chat(question: str) -> list:
    req = urllib.request.Request(BASE + "/api/agent/chat", data=json.dumps(
        {"messages": [{"role": "user", "content": question}]}).encode(), headers={"content-type": "application/json"})
    evs = []
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            line = line.decode().strip()
            if line.startswith("data: "):
                evs.append(json.loads(line[6:]))
    return evs


def main() -> int:
    s, h = call("/api/health")
    check("health", s == 200, str(h)[:80])
    s, meta = call("/api/meta")
    check("meta: all-India coverage", s == 200 and meta["n_districts"] >= 700 and len(meta["scenarios"]) >= 5,
          f"{meta.get('n_districts')} districts, {len(meta.get('scenarios', []))} scenarios")

    _, geo = call("/api/geo")
    _, dists = call("/api/districts")
    codes = {d["district_code"] for d in dists}
    feats = geo["features"]
    unmapped = [f["properties"] for f in feats if f["properties"].get("data_code") is None]
    broken = [f for f in feats if f["properties"].get("data_code") not in (None, *codes)]
    check("geo: every polygon maps to a modelled district or an explained no-data area",
          not broken and len(unmapped) <= 20 and all(p.get("data_note") for p in unmapped),
          f"{len(feats)} polygons, {len(unmapped)} no-data, {len(broken)} broken")

    base = None
    for sc in [x["id"] for x in meta["scenarios"]]:
        t = time.time()
        r = simulate(sc)
        p = r["params"]
        ks = r["kpis"]
        beats = all(k["assi"] > k["assi_status_quo"] for k in ks)
        income = all(k["districts_below_income_floor"] == 0 and k["farmer_income_vs_today_pct"] >= 99.9 for k in ks)
        cereal = all(k["guardrail_cereal_ok"] for k in ks)
        budget = all(k["incentive_cr"] <= p["incentive_budget_cr"] + 1 for k in ks)
        stress = all(x["plan_beats_status_quo"] for x in r["robustness"])
        k30 = ks[-1]
        check(f"{sc}: plan beats do-nothing every year; guardrails hold; stress tests pass",
              r["solver"]["status"] == "OPTIMAL" and beats and income and cereal and budget and stress
              and len(r["districts"]) == meta["n_districts"] and len(r.get("lever_status", {})) >= 8,
              f"ASSI 2030 {k30['assi_status_quo']} -> {k30['assi']}, water {k30['water_saved_bcm']} BCM, "
              f"imports -${k30['import_saving_usd_bn']}bn, income {k30['farmer_income_vs_today_pct']}% ({time.time() - t:.0f}s)")
        if sc == "baseline":
            base = r

    def monotone(xs: list, tol: float = 1.0) -> bool:
        return all(b >= a - tol for a, b in zip(xs, xs[1:])) or all(b <= a + tol for a, b in zip(xs, xs[1:]))

    yrs = sorted(base["districts"][0]["years"])
    zigzag = [d["district_name"] for d in base["districts"]
              if not all(monotone([0.0] + [d["years"][y][key] or 0.0 for y in yrs])
                         for key in ("rice_area_change_ha", "legume_area_change_ha"))]
    k_by_year = base["kpis"]
    check("pathway: every district moves steadily towards its 2030 mix (no switch-backs)",
          not zigzag and all(a["area_shifted_mha"] <= b["area_shifted_mha"] for a, b in zip(k_by_year, k_by_year[1:])),
          f"{len(zigzag)} districts zig-zag" + (f" (e.g. {', '.join(zigzag[:3])})" if zigzag else "")
          + f"; area shifted {' -> '.join(str(k['area_shifted_mha']) for k in k_by_year)} Mha")

    k0 = base["kpis"][-1]
    t = time.time()
    again = simulate("baseline", max_shift_pct=12, cereal_floor_pct=101.5, gw_mode="cap80", incentive_budget_cr=6000)
    check("cache: same effective parameters reuse the run", again["run_id"] == base["run_id"] and time.time() - t < 5)

    lo, hi = simulate(incentive_budget_cr=3000)["kpis"][-1], simulate(incentive_budget_cr=12000)["kpis"][-1]
    check("lever: more incentive budget -> more water saved and a higher index",
          lo["water_saved_bcm"] < k0["water_saved_bcm"] < hi["water_saved_bcm"] and lo["assi"] <= k0["assi"] <= hi["assi"],
          f"water {lo['water_saved_bcm']} < {k0['water_saved_bcm']} < {hi['water_saved_bcm']} BCM")
    seed = simulate(crop_growth_cap_pct=10)
    check("lever: seed & market scale-up -> bigger import saving",
          seed["kpis"][-1]["import_saving_usd_bn"] > k0["import_saving_usd_bn"],
          f"${k0['import_saving_usd_bn']}bn -> ${seed['kpis'][-1]['import_saving_usd_bn']}bn")
    check("lever status: budget limiting, land-shift cap not limiting (explains a no-change re-run)",
          base["lever_status"]["incentive_budget_cr"]["state"] == "binding"
          and base["lever_status"]["max_shift_pct"]["state"] == "slack")
    zero = simulate("zero_gw_depletion")
    check("zero-depletion mandate applies and saves more groundwater",
          zero["params"]["gw_mode"] == "zero_depletion" and zero["kpis"][-1]["water_saved_bcm"] > k0["water_saved_bcm"],
          f"{k0['water_saved_bcm']} -> {zero['kpis'][-1]['water_saved_bcm']} BCM")
    preset = {x["id"]: x.get("levers", {}) for x in meta["scenarios"]}
    check("meta tells the UI which lever a scenario presets", preset.get("zero_gw_depletion") == {"gw_mode": "zero_depletion"})

    s1, _ = call("/api/simulate", {"scenario": "baseline", "overrides": {"gw_mode": "drop table"}})
    s2, _ = call("/api/simulate", {"scenario": "nope", "overrides": {}})
    s3, _ = call("/api/district/999999")
    check("validation: bad lever, unknown scenario and unknown district are rejected", (s1, s2, s3) == (400, 400, 404),
          f"{s1}/{s2}/{s3}")

    # ---- policy instruments, fiscal ledger and MROPI
    ye = meta.get("yield_engine") or {}
    check("meta: instruments, budget modes, trade data, yield engine and climate source",
          {i["id"] for i in meta.get("instruments", [])} == {"dbt", "seed_kits", "price_support"}
          and {m["id"] for m in meta.get("budget_modes", [])} == {"fixed", "min_budget"}
          and len(meta.get("trade", {}).get("products", [])) >= 5
          and ye.get("active") in ("agronomic_prior", "bqml_boosted_tree") and bool(meta.get("climate_source")),
          f"trade FY{meta.get('trade', {}).get('fiscal_year')}, yield engine {ye.get('active')} ({ye.get('mode')}), "
          f"climate: {meta.get('climate_source')}")
    pm = base["policy_mix"]
    outlays_ok = all(abs(sum(k["outlay_by_instrument_cr"].values()) - k["incentive_cr"]) <= 1
                     and k["incentive_cr"] <= k["budget_cr"] + 1 for k in base["kpis"])
    dist_total = sum(sum(d["years"]["2030"]["outlay_cr"].values()) for d in base["districts"])
    fl = k0["fiscal_ledger"]
    check("fixed budget: instrument outlays add up, stay within budget and match the district ledger",
          pm["mode"] == "fixed" and outlays_ok and abs(dist_total - k0["incentive_cr"]) <= 0.01 * k0["incentive_cr"] + 1
          and fl["public_return_ratio"] > 1,
          f"2030 {', '.join(f'{a} Rs {v:,.0f} cr' for a, v in k0['outlay_by_instrument_cr'].items())}; "
          f"public return {fl['public_return_ratio']}x")
    tiers = {t["tier"]: t for t in pm["mropi_tiers"]}
    funded = [d["years"]["2030"] for d in base["districts"] if sum(d["years"]["2030"]["outlay_cr"].values()) >= 0.01]
    tier_ok = all((y["mropi_tier"] == "Tier 1") == (y["mropi"] is not None and y["mropi"] >= 10) for y in funded)
    check("MROPI: every district is tiered and funded districts are tiered by return per rupee",
          sum(t["districts"] for t in tiers.values()) == meta["n_districts"] and tier_ok
          and abs(sum(t["outlay_cr"] for t in tiers.values()) - k0["incentive_cr"]) <= 1,
          ", ".join(f"{n} {t['districts']}" for n, t in tiers.items()))
    least = simulate(budget_mode="min_budget")
    path = [x["budget_cr"] for x in least["policy_mix"]["budget_path_cr"]]
    check("least-cost mode: smallest budget path that meets the import target, rising each year",
          least["policy_mix"]["target_met"] and least["kpis"][-1]["import_target_shortfall_usd_bn"] == 0
          and all(a < b for a, b in zip(path, path[1:])) and least["policy_mix"]["min_budget_cr"] == path[-1],
          "Rs " + " -> ".join(f"{x:,.0f}" for x in path) + " cr")
    dbt = simulate(instruments=["dbt"])
    kd = dbt["kpis"][-1]
    others = sum(v for a, v in kd["outlay_by_instrument_cr"].items() if a != "dbt")
    check("instrument mix: DBT-only uses only DBT; the full mix saves more imports for the same budget",
          others == 0 and kd["incentive_cr"] <= kd["budget_cr"] + 1 and k0["import_saving_usd_bn"] > kd["import_saving_usd_bn"],
          f"DBT only ASSI {kd['assi']}, ${kd['import_saving_usd_bn']}bn vs full mix ASSI {k0['assi']}, ${k0['import_saving_usd_bn']}bn")

    # ---- bilateral trade shocks (India Data Portal import statistics)
    _, tr = call("/api/trade?product=palm%20oil")
    palm = tr["products"][0]
    _, dec = call("/api/trade/decompose?country=Indonesia&product=palm%20oil&pct=30")
    check("trade data: palm-oil suppliers with shares and HHI; a 30% Indonesian cut becomes tonnes and hectares",
          palm["suppliers"][0]["country"] == "Indonesia" and 0 < palm["hhi"] <= 1
          and sum(x["share_pct"] for x in palm["suppliers"]) <= 100.5
          and abs(dec["volume_kt"] - 0.3 * dec["base_qty_kt"]) < 1 and dec["substitutes"][0]["hectares_needed_mha"] > 0,
          f"FY{tr['fiscal_year']}: Indonesia {palm['suppliers'][0]['share_pct']}%, HHI {palm['hhi']}; 30% = {dec['volume_kt']:,.0f} kt")
    halt = simulate("indonesia_palm_halt")
    cut, grp = halt["trade"]["cuts"][0], halt["trade"]["groups"][0]
    y30 = grp["years"][-1]
    check("trade shock: Indonesia palm-oil halt is replaced from Indian fields; shortfall and alternatives reported",
          cut["country"] == "Indonesia" and cut["volume_kt"] > 0 and cut["alternatives"]
          and all(a["country"] != "Indonesia" for a in cut["alternatives"])
          and 0 < y30["achieved_cut_kt"] <= y30["required_cut_kt"] + 0.5
          and abs(y30["required_cut_kt"] - y30["achieved_cut_kt"] - y30["shortfall_kt"]) < 1
          and all(k["assi"] > k["assi_status_quo"] for k in halt["kpis"]),
          f"2030 needs {y30['required_cut_kt']:,.0f} kt, plan covers {y30['achieved_cut_kt']:,.0f} kt, "
          f"short {y30['shortfall_kt']:,.0f} kt; alternatives {', '.join(a['country'] for a in cut['alternatives'][:3])}")
    s4, msg = call("/api/simulate", {"scenario": "baseline",
                                     "overrides": {"trade_cuts": [{"country": "Atlantis", "product": "palm oil", "pct": 50}]}})
    s5, _ = call("/api/trade/decompose?country=Atlantis&product=palm%20oil")
    check("trade validation: an unknown supplier is rejected with the real suppliers listed",
          s4 == 400 and "Indonesia" in str(msg) and s5 == 400, f"{s4}/{s5}")

    # ---- next-crop recommendation
    amr = next(d for d in dists if d["district_name"] == "Amritsar")
    s, nc = call(f"/api/district/{amr['district_code']}/candidates?run_id={base['run_id']}&year=2027")
    seas = {x["season"]: x for x in nc.get("seasons", [])} if s == 200 else {}
    rec = {k: (v.get("recommended") or {}).get("crop") for k, v in seas.items()}
    s6, _ = call(f"/api/district/{amr['district_code']}/candidates?year=2031")
    check("next crop: Amritsar 2027 gets a ranked recommendation per season that moves off paddy",
          set(seas) == {"Kharif", "Rabi"} and all(len(v["candidates"]) >= 2 for v in seas.values())
          and rec.get("Kharif") not in (None, "rice") and rec.get("Rabi") is not None and s6 == 400,
          f"Kharif {rec.get('Kharif')}, Rabi {rec.get('Rabi')}")

    pun = next(d for d in dists if d["district_name"] == "Sangrur")
    s, tr = call(f"/api/district/{pun['district_code']}?run_id={base['run_id']}")
    t_ = tr["trace"] if s == 200 else {}
    check("decision trace: moves, reasons, rejected alternatives and today's crop mix",
          s == 200 and t_.get("moves_2030") and t_.get("rejected_alternatives") is not None
          and any(x["year"] == 0 for x in t_.get("rotation_timeline", [])),
          f"{len(t_.get('moves_2030', []))} moves")
    # ---- plan horizon: 2-8 years from 2027 (default 4)
    six = simulate(horizon_years=6)
    ys = [k["year"] for k in six["kpis"]]
    sh_years = sorted({int(t["id"][-4:]) for t in six.get("robustness", []) if t["id"][-4:].isdigit()})
    check("horizon: a 6-year plan runs 2027-2032, keeps the guardrails and places stress tests inside the plan",
          ys == list(range(2027, 2033)) and six.get("plan_years") == ys and six["kpis"][-1]["assi"] > six["kpis"][-1]["assi_status_quo"]
          and all(2027 <= y <= 2032 for y in sh_years) and simulate(horizon_years=4)["run_id"] == base["run_id"],
          f"years {ys[0]}-{ys[-1]}, ASSI {six['kpis'][-1]['assi']}, stress years {sh_years}")
    s7, _ = call("/api/simulate", {"scenario": "baseline", "overrides": {"horizon_years": 40}})
    s8, m_ = call("/api/meta")
    check("horizon: meta advertises 2-8 years and out-of-range values are clamped, never solved as given",
          s8 == 200 and m_.get("horizon", {}).get("max") == 8 and s7 in (200, 429), f"meta {m_.get('horizon')}, 40 years -> {s7}")

    # ---- whole-plan totals (least-cost mode lists the total cost over the span)
    lc = simulate(budget_mode="min_budget")
    tot = (lc.get("policy_mix") or {}).get("totals") or {}
    by_year = sum(float(x["outlay_cr"]) for x in lc["policy_mix"]["budget_path_cr"])
    check("least-cost mode reports the total cost over the whole plan",
          tot.get("outlay_cr", 0) > 0 and tot.get("years") == 4 and abs(by_year - tot["outlay_cr"]) < 2,
          f"total Rs {tot.get('outlay_cr', 0):,.0f} cr over {tot.get('first_year')}-{tot.get('last_year')}, returns Rs {tot.get('returns_cr', 0):,.0f} cr")

    # ---- multi-country x multi-commodity trade shock
    cuts = [{"country": "Indonesia", "product": "palm oil", "pct": 40}, {"country": "Malaysia", "product": "palm oil", "pct": 40},
            {"country": "Canada", "product": "lentils", "pct": 60}]
    sM, mt = call("/api/simulate", {"scenario": "baseline", "overrides": {"trade_cuts": cuts}})
    got = [(c["country"], c["product"]) for c in (mt.get("trade", {}).get("cuts", []) if sM == 200 else [])]
    check("trade shock: several countries and commodities at once",
          sM == 200 and len(mt["params"].get("trade_cuts", [])) == 3 and len(got) == 3, f"{sM}: {got}")

    # ---- saved policies: snapshot -> reopen without re-solving -> rename -> soft delete -> restore -> purge
    s, pol = call("/api/policies", {"run_id": base["run_id"], "name": "Smoke test policy", "note": "created by smoke_test.py"})
    pid = pol.get("id", "") if s == 200 and isinstance(pol, dict) else ""
    t0 = time.time()
    s2, run = call(f"/api/run/p-{pid}") if pid else (0, {})
    dt = time.time() - t0
    s3, trp = call(f"/api/district/{pun['district_code']}?run_id=p-{pid}") if pid else (0, {})
    check("policy: saved snapshot reopens with identical results, without re-solving",
          s2 == 200 and run["kpis"] == base["kpis"] and len(run["districts"]) == len(base["districts"])
          and (run.get("policy") or {}).get("name") == "Smoke test policy" and s3 == 200
          and trp["trace"].get("moves_2030") == t_.get("moves_2030") and dt < 15,
          f"id {pid}, reopened in {dt:.1f}s")
    s4, _ = call("/api/policies", {"run_id": base["run_id"], "name": "Smoke test policy"})
    s5, lst = call("/api/policies")
    mine = [p for p in (lst.get("policies", []) if s5 == 200 else []) if p["id"] == pid]
    s6, ren = send("PATCH", f"/api/policies/{pid}", {"name": "Smoke test policy (renamed)"})
    s7, dl = send("DELETE", f"/api/policies/{pid}")
    s8, rs = send("PATCH", f"/api/policies/{pid}", {"deleted": False})
    s9a, _ = send("DELETE", f"/api/policies/{pid}?purge=true")  # a live policy cannot be purged in one request
    send("DELETE", f"/api/policies/{pid}")
    s9, pg = send("DELETE", f"/api/policies/{pid}?purge=true")
    s10, _ = call(f"/api/run/p-{pid}")
    check("policy library: list, rename, soft delete, restore and purge",
          s4 == 200 and len(mine) == 1 and (mine[0].get("summary") or {}).get("assi") is not None
          and s6 == 200 and ren.get("name", "").endswith("(renamed)") and s7 == 200 and dl.get("deleted") is True
          and s8 == 200 and rs.get("deleted") is False and s9a == 409 and s9 == 200 and s10 == 404,
          f"store {lst.get('store', {}).get('documents')} + {lst.get('store', {}).get('snapshots')}")
    b1, _ = call("/api/run/p-NOT_VALID!")
    b2, _ = send("PATCH", "/api/policies/..%2Fx", {"name": "x"})
    b3, _ = call("/api/policies", {"run_id": "../../etc", "name": "x"})
    b4, _ = call("/api/chats/zzz")
    check("policy/chat ids are validated", b1 in (400, 404) and b2 in (400, 404, 405) and b3 in (400, 422) and b4 == 404, f"{b1}/{b2}/{b3}/{b4}")

    s, b = call("/api/brief", {"run_id": base["run_id"], "state": "Punjab"})
    check("policy brief", s == 200 and len(b.get("markdown", "")) > 400 and "Punjab" in b["markdown"], b.get("model", "") if s == 200 else str(b))

    evs = chat("Which state saves the most groundwater and what does it cost?")
    text = "".join(e.get("text", "") for e in evs if e.get("type") == "text")
    check("co-pilot streams tool calls and an answer", any(e["type"] == "tool_call" for e in evs)
          and evs[-1]["type"] == "done" and len(text) > 80, text[:90].replace("\n", " "))
    evs = chat("What if Indonesia stops selling palm oil to India?")
    text = "".join(e.get("text", "") for e in evs if e.get("type") == "text")
    tools = [e["name"] for e in evs if e["type"] == "tool_call"]
    check("co-pilot answers a trade shock with the trade tool and the re-optimised plan",
          any("trade" in t for t in tools) and evs[-1]["type"] == "done" and "Indonesia" in text,
          f"tools: {', '.join(tools)}")

    # ---- shared chat sessions: every exchange is saved; sessions can be listed, reopened and deleted
    sid = next((e["id"] for e in evs if e["type"] == "session"), None)
    saved = any(e["type"] == "session" and e.get("saved") for e in evs)
    s1, cl = call("/api/chats")
    s2, cs = call(f"/api/chats/{sid}") if sid else (0, {})
    s3, _ = send("DELETE", f"/api/chats/{sid}") if sid else (0, {})
    s4, _ = call(f"/api/chats/{sid}") if sid else (0, {})
    check("chat sessions: saved, listed, reopened and deleted",
          saved and s1 == 200 and any(c["id"] == sid for c in cl.get("chats", [])) and s2 == 200
          and len(cs.get("messages", [])) == 2 and s3 == 200 and s4 == 404, f"session {sid}")

    # ---- the co-pilot creates a policy when asked, saves it and offers it to open
    evs = chat("Create a policy called \"Smoke copilot policy\" with a 9000 crore incentive budget and save it")
    pe = [e for e in evs if e["type"] == "policy"]
    ok = bool(pe) and pe[0].get("run_id", "").startswith("p-") and evs[-1]["type"] == "done"
    if pe:
        s5, pr = call(f"/api/run/{pe[0]['run_id']}")
        ok = ok and s5 == 200 and pr["params"].get("incentive_budget_cr") == 9000
        send("DELETE", f"/api/policies/{pe[0]['id']}")
        send("DELETE", f"/api/policies/{pe[0]['id']}?purge=true")
    for e in evs:
        if e["type"] == "session":
            send("DELETE", f"/api/chats/{e['id']}")
            break
    check("co-pilot creates and saves a policy from a conversation", ok,
          f"{pe[0]['name'] if pe else 'no policy event'}")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed against {BASE}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
