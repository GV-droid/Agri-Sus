#!/usr/bin/env python3
"""Fetch CGWB Dynamic Ground Water Resource Assessment (GWRA) district data from IN-GRES.

IN-GRES (ingres.iith.ac.in) exposes the same open JSON endpoint its public map uses
(`gec/getBusinessDataForUserOpen`). Querying a STATE location returns one record per
district with recharge, extraction (draft), extractable resource and stage of extraction.
Units are hectare-metres (ham); 1 ham = 10,000 m3.

Usage: python3 scripts/fetch_gwra.py [year ...]   (default: 2024-2025 2023-2024 2022-2023)
Writes data/raw/gwra_district.csv. Stdlib only.
"""
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

API = "https://ingres.iith.ac.in/api/gec/getBusinessDataForUserOpen"
INDIA = "ffce954d-24e1-494b-ba7e-0931d8ad6085"
OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "gwra_district.csv"


def post(body, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(API, json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  retry {i+1}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))


def body(name, loctype, uuid, year):
    return {"locname": name, "loctype": loctype, "view": "admin", "locuuid": uuid, "component": "recharge",
            "period": "annual", "category": "all", "computationType": "normal", "year": year,
            "stateuuid": None, "verificationStatus": 1, "approvalLevel": 1, "parentuuid": INDIA,
            "mapOnClickParams": "false"}


def g(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def flatten(rec, state, year):
    cat = rec.get("category") or {}
    blocks = {}
    for v in (rec.get("reportSummary") or {}).values():
        for lvl in v.values():
            for k, n in lvl.items():
                blocks[k] = blocks.get(k, 0) + n
    return {
        "assessment_year": year,
        "state_name": state,
        "district_name": rec["locationName"],
        "location_uuid": rec.get("locationUUID"),
        "category": cat.get("total") if isinstance(cat, dict) else cat,
        "stage_of_extraction_pct": g(rec, "stageOfExtraction", "total"),
        "annual_recharge_ham": g(rec, "rechargeData", "total", "total"),
        "recharge_rainfall_ham": g(rec, "rechargeData", "rainfall", "total"),
        "recharge_canal_ham": g(rec, "rechargeData", "canal", "total"),
        "recharge_surface_irrigation_ham": g(rec, "rechargeData", "surface_irrigation", "total"),
        "recharge_gw_irrigation_ham": g(rec, "rechargeData", "gw_irrigation", "total"),
        "extractable_resource_ham": g(rec, "currentAvailabilityForAllPurposes", "total"),
        "extraction_total_ham": g(rec, "draftData", "total", "total"),
        "extraction_irrigation_ham": g(rec, "draftData", "agriculture", "total"),
        "extraction_domestic_ham": g(rec, "draftData", "domestic", "total"),
        "extraction_industry_ham": g(rec, "draftData", "industry", "total"),
        "future_use_availability_ham": g(rec, "availabilityForFutureUse", "total"),
        "natural_discharge_ham": g(rec, "loss", "total"),
        "rainfall_mm": g(rec, "rainfall", "total"),
        "recharge_worthy_area_ha": g(rec, "area", "recharge_worthy", "totalArea"),
        "command_area_ha": g(rec, "area", "total", "commandArea"),
        "blocks_safe": blocks.get("safe", 0),
        "blocks_semi_critical": blocks.get("semi_critical", 0),
        "blocks_critical": blocks.get("critical", 0),
        "blocks_over_exploited": blocks.get("over_exploited", 0),
        "blocks_saline": blocks.get("saline", 0),
    }


def main():
    years = sys.argv[1:] or ["2024-2025", "2023-2024", "2022-2023"]
    rows = []
    for year in years:
        states = post(body("INDIA", "COUNTRY", INDIA, year))
        states = [s for s in states if s.get("locationName") != "total" and s.get("locationUUID")]
        print(f"{year}: {len(states)} states", file=sys.stderr)
        for s in states:
            dists = post(body(s["locationName"], "STATE", s["locationUUID"], year)) or []
            dists = [d for d in dists if d.get("locationName") != "total"]
            rows += [flatten(d, s["locationName"], year) for d in dists]
            time.sleep(0.3)
        print(f"  -> {len(rows)} district rows so far", file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
