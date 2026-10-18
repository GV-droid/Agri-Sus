#!/usr/bin/env python3
"""Google Earth Engine climate pipeline for Agri Sus.

Recomputes, inside Earth Engine, the district climate indicators the optimizer and the yield model use, for all
785 district polygons of India:

  history : CHIRPS daily rainfall (UCSB-CHG/CHIRPS/DAILY, 0.05 deg) + ERA5-Land daily max/min temperature
            (ECMWF/ERA5_LAND/DAILY_AGGR, 0.1 deg), agricultural years 2001-2024
  cmip6   : NASA NEX-GDDP-CMIP6 (NASA/GDDP-CMIP6, bias-corrected daily, 0.25 deg), SSP2-4.5;
            baseline agri years 2005-2013 (historical run) vs the 2026-2030 plan window (ssp245)

Indicators per agricultural year Y (Jun Y .. May Y+1), identical to scripts/fetch_climate.py, but averaged over the
whole district polygon instead of sampled at its centroid:
  kharif (Jun-Sep) rain / mean Tmax / mean Tmin / Hargreaves ET0, longest dry spell (< 2.5 mm/day),
  heavy-rain days (>= 64.5 mm), rabi (Nov-Mar) rain / Tmax / Tmin / ET0, terminal-heat days (Feb-Mar Tmax >= 32 C),
  harvest-window (Oct-Nov) extreme-rain days.

Two ways to run (both need a Google Cloud project registered for Earth Engine, see docs/DEPLOY.md):

  # 1. server-side: Earth Engine batch tasks write straight into BigQuery (recommended, no laptop needed)
  python scripts/gee_climate.py --project MY_PROJECT --mode export --dataset agri_sus_mvp
  #    -> tables ee_climate_history and ee_cmip6_<model>_<period>; then bigquery/04_climate_from_earth_engine.sql
  #       builds `climate_projection_ee`, and the app uses it when CLIMATE_TABLE=climate_projection_ee

  # 2. interactive: pulls results in small batches and writes CSVs (optionally also loads them into BigQuery)
  python scripts/gee_climate.py --project MY_PROJECT --mode local [--years 2020-2024] [--load-bq]
  #    -> data/raw/ee/climate_hist_district_year.csv and data/raw/ee/climate_cmip6_district_year.csv
  #       (same columns as scripts/fetch_climate.py; copy them to data/raw/ and run build_features.py to rebuild)
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "data/processed/geo/districts_india.geojson"
OUT = ROOT / "data/raw/ee"

MODELS = ["MRI-ESM2-0", "EC-Earth3", "IITM-ESM"]
HIST_YEARS = list(range(2001, 2025))
PERIODS = {"baseline": ("historical", list(range(2005, 2014))), "future": ("ssp245", list(range(2026, 2031)))}
VARS = ["kharif_rain_mm", "kharif_tmax_c", "kharif_tmin_c", "kharif_max_dry_spell_days", "kharif_heavy_rain_days",
        "rabi_rain_mm", "rabi_tmax_c", "rabi_tmin_c", "terminal_heat_days", "harvest_extreme_rain_days",
        "kharif_et0_mm", "rabi_et0_mm"]
SCALE_M = 5000  # reduce at 5 km so even small districts get pixels from the 0.25 deg CMIP6 grid

ee = None  # imported lazily so --help works without the earthengine-api package


def init(project: str) -> None:
    global ee
    try:
        import ee as _ee
    except ImportError:
        sys.exit("earthengine-api is not installed: pip install -r scripts/requirements-gcp.txt")
    ee = _ee
    try:
        ee.Initialize(project=project)
    except Exception as e:  # noqa: BLE001 - first run on a machine without stored credentials
        print(f"Earth Engine needs authorisation ({str(e).splitlines()[0]}); opening the sign-in flow...", file=sys.stderr)
        ee.Authenticate()
        ee.Initialize(project=project)


# ----------------------------------------------------------------------------------------------- districts
def _round(c):
    return [_round(x) for x in c] if isinstance(c[0], list) else [round(c[0], 3), round(c[1], 3)]


def district_features() -> list:
    """One ee.Feature per district polygon (MultiPolygon, planar lon/lat like the GeoJSON), coordinates rounded."""
    gj = json.loads(GEO.read_text())
    feats = []
    for f in gj["features"]:
        code = f["properties"].get("district_code")
        g = f["geometry"]
        if code is None or not g:
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        feats.append(ee.Feature(ee.Geometry.MultiPolygon(_round(polys), None, False), {"district_code": int(code)}))
    return feats


# ----------------------------------------------------------------------------------------------- daily inputs
def observed():
    prcp = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").select(["precipitation"], ["prcp"])
    temp = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
            .select(["temperature_2m_max", "temperature_2m_min"], ["tmax", "tmin"])
            .map(lambda i: ee.Image(i.subtract(273.15).copyProperties(i, ["system:time_start"]))))
    return prcp, temp


def cmip6(model: str, scenario: str):
    col = ee.ImageCollection("NASA/GDDP-CMIP6").filter(
        ee.Filter.And(ee.Filter.eq("model", model), ee.Filter.eq("scenario", scenario)))

    def conv(i):
        return ee.Image(ee.Image.cat([i.select("pr").multiply(86400).rename("prcp"),  # kg m-2 s-1 -> mm/day
                                      i.select("tasmax").subtract(273.15).rename("tmax"),
                                      i.select("tasmin").subtract(273.15).rename("tmin")])
                        .copyProperties(i, ["system:time_start"]))
    c = col.map(conv)
    return c.select("prcp"), c.select(["tmax", "tmin"])


# ----------------------------------------------------------------------------------------------- indicators
def et0(img):
    """Hargreaves reference evapotranspiration (mm/day), FAO-56 eq. 52 with extraterrestrial radiation eq. 21."""
    img = ee.Image(img)
    doy = ee.Date(img.get("system:time_start")).getRelative("day", "year").add(1)
    ang = doy.multiply(2 * math.pi / 365)
    dr = ang.cos().multiply(0.033).add(1)
    dec = ang.subtract(1.39).sin().multiply(0.409)
    lat = ee.Image.pixelLonLat().select("latitude").multiply(math.pi / 180)
    ws = lat.tan().multiply(dec.tan()).multiply(-1).clamp(-1, 1).acos()
    ra = (ws.multiply(lat.sin()).multiply(dec.sin()).add(lat.cos().multiply(dec.cos()).multiply(ws.sin()))
          .multiply(dr.multiply(24 * 60 / math.pi * 0.082 * 0.408)))
    tmax, tmin = img.select("tmax"), img.select("tmin")
    return (ra.multiply(tmax.add(tmin).divide(2).add(17.8)).multiply(tmax.subtract(tmin).max(0).sqrt())
            .multiply(0.0023).max(0).rename("et0"))


def longest_dry_spell(prcp):
    """Pixel-wise longest run of days with < 2.5 mm rain."""
    init_ = ee.Image.constant([0, 0]).rename(["cur", "best"]).toFloat()

    def step(img, acc):
        acc = ee.Image(acc)
        cur = acc.select("cur").add(1).multiply(ee.Image(img).lt(2.5))
        return cur.addBands(acc.select("best").max(cur)).rename(["cur", "best"])
    return ee.Image(prcp.sort("system:time_start").iterate(step, init_)).select("best")


def indicators(prcp, temp, y: int):
    d = ee.Date.fromYMD
    kh, rb, fm, on = (d(y, 6, 1), d(y, 10, 1)), (d(y, 11, 1), d(y + 1, 4, 1)), (d(y + 1, 2, 1), d(y + 1, 4, 1)), \
        (d(y, 10, 1), d(y, 12, 1))
    pk, pr, po = prcp.filterDate(*kh), prcp.filterDate(*rb), prcp.filterDate(*on)
    tk, tr, tf = temp.filterDate(*kh), temp.filterDate(*rb), temp.filterDate(*fm)

    def days(col, thr):
        return col.map(lambda i: ee.Image(i).gte(thr)).sum()
    return ee.Image.cat([
        pk.sum().rename("kharif_rain_mm"),
        tk.select("tmax").mean().rename("kharif_tmax_c"),
        tk.select("tmin").mean().rename("kharif_tmin_c"),
        longest_dry_spell(pk).rename("kharif_max_dry_spell_days"),
        days(pk, 64.5).rename("kharif_heavy_rain_days"),
        pr.sum().rename("rabi_rain_mm"),
        tr.select("tmax").mean().rename("rabi_tmax_c"),
        tr.select("tmin").mean().rename("rabi_tmin_c"),
        days(tf.select("tmax"), 32).rename("terminal_heat_days"),
        days(po, 64.5).rename("harvest_extreme_rain_days"),
        tk.map(et0).sum().rename("kharif_et0_mm"),
        tr.map(et0).sum().rename("rabi_et0_mm"),
    ])


def reduce(img, fc, props: dict):
    return (img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=SCALE_M, tileScale=4)
            .map(lambda f: f.set(props).setGeometry(None)))


def jobs(years_hist):
    """(table, props, prcp, temp, years) for the history and every CMIP6 model x period."""
    out = []
    if years_hist:
        p, t = observed()
        out.append(("ee_climate_history", {"source": "CHIRPS + ERA5-Land"}, p, t, years_hist))
    for m in MODELS:
        for period, (scen, yrs) in PERIODS.items():
            p, t = cmip6(m, scen)
            out.append((f"ee_cmip6_{re.sub(r'[^a-z0-9]+', '_', m.lower())}_{period}",
                        {"model": m, "period": period}, p, t, yrs))
    return out


# ----------------------------------------------------------------------------------------------- run modes
def run_export(project: str, dataset: str, todo, feats, wait: bool, skip_existing: bool = False) -> list:
    """Start one Earth Engine -> BigQuery export task per table; returns the tables whose task failed."""
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery
    bq = bigquery.Client(project=project)
    bq.create_dataset(bigquery.Dataset(f"{project}.{dataset}"), exists_ok=True)

    def has_rows(table_id: str) -> bool:
        try:
            return (bq.get_table(table_id).num_rows or 0) > 0
        except NotFound:
            return False

    try:  # re-running after a disconnect: wait for our tasks that are still running instead of duplicating them
        running = {t.config["description"]: t for t in ee.batch.Task.list() if t.config and t.active()}
    except Exception:  # noqa: BLE001
        running = {}
    fc = ee.FeatureCollection(feats)
    tasks = []
    for table, props, p, t, years in todo:
        table_id, desc = f"{project}.{dataset}.{table}", f"agrisus_{table}"[:100]
        if desc in running:
            print(f"{table}: Earth Engine task already running - waiting for it")
            tasks.append((table, running[desc]))
            continue
        if skip_existing and has_rows(table_id):
            print(f"{table}: already in BigQuery - skipped")
            continue
        bq.delete_table(table_id, not_found_ok=True)  # rerunnable: no duplicate rows
        col = ee.FeatureCollection([reduce(indicators(p, t, y), fc, dict(props, agri_year=y)) for y in years]).flatten()
        task = ee.batch.Export.table.toBigQuery(
            collection=col, description=desc, table=table_id,
            overwrite=True, selectors=["district_code", "agri_year", *props.keys(), *VARS])
        task.start()
        tasks.append((table, task))
        print(f"started Earth Engine task for {table} ({len(years)} years)")
    print("Tasks run on Google's servers (typically 20-90 min); watch them at https://code.earthengine.google.com/tasks")
    failed = []
    while wait and tasks:
        time.sleep(60)
        for table, task in list(tasks):
            st = task.status()
            if st["state"] in ("COMPLETED", "FAILED", "CANCELLED"):
                print(f"{table}: {st['state']} {st.get('error_message', '')}")
                tasks.remove((table, task))
                if st["state"] != "COMPLETED":
                    failed.append(table)
        if tasks:
            print(f"  still running: {', '.join(t for t, _ in tasks)}", flush=True)
    return failed


def run_local(todo, feats, batch: int, workers: int):
    import pandas as pd
    batches = [ee.FeatureCollection(feats[i:i + batch]) for i in range(0, len(feats), batch)]
    work = [(table, props, p, t, y, b) for table, props, p, t, years in todo for y in years for b in batches]
    print(f"{len(work)} Earth Engine requests ({len(feats)} districts in {len(batches)} batches)")

    def one(w):
        table, props, p, t, y, b = w
        for i in range(6):
            try:
                fc = reduce(indicators(p, t, y), b, dict(props, agri_year=y)).getInfo()
                return table, [f["properties"] for f in fc["features"]]
            except Exception as e:  # noqa: BLE001 - quota / timeout: back off and retry
                print(f"  retry {i + 1} {table} {y}: {str(e)[:120]}", file=sys.stderr)
                time.sleep(15 * (i + 1))
        raise RuntimeError(f"Earth Engine kept failing for {table} {y}")
    rows = {}
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(one, w) for w in work]
        for n, fu in enumerate(as_completed(futs), 1):
            table, r = fu.result()
            rows.setdefault(table, []).extend(r)
            if n % 20 == 0 or n == len(futs):
                print(f"  {n}/{len(futs)} done")
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {}
    hist = pd.DataFrame(rows.pop("ee_climate_history", []))
    if len(hist):
        frames["ee_climate_history"] = hist.reindex(columns=["district_code", "agri_year", *VARS]).round(2)
        frames["ee_climate_history"].to_csv(OUT / "climate_hist_district_year.csv", index=False)
    cm = pd.DataFrame([r for v in rows.values() for r in v])
    if len(cm):
        frames["ee_cmip6_all"] = cm.reindex(columns=["district_code", "model", "period", "agri_year", *VARS]).round(2)
        frames["ee_cmip6_all"].to_csv(OUT / "climate_cmip6_district_year.csv", index=False)
    print(f"wrote {', '.join(str(p.relative_to(ROOT)) for p in OUT.glob('*.csv'))}")
    return frames


def load_bq(project: str, dataset: str, frames: dict) -> None:
    from google.cloud import bigquery
    bq = bigquery.Client(project=project)
    for table, df in frames.items():
        cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", clustering_fields=["district_code"],
                                     range_partitioning=bigquery.RangePartitioning(
                                         field="agri_year", range_=bigquery.PartitionRange(start=2000, end=2032, interval=1)))
        bq.load_table_from_dataframe(df, f"{project}.{dataset}.{table}", job_config=cfg).result()
        print(f"loaded {project}.{dataset}.{table} ({len(df):,} rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="Google Cloud project registered for Earth Engine")
    ap.add_argument("--dataset", default="agri_sus_mvp", help="BigQuery dataset (export / --load-bq)")
    ap.add_argument("--mode", choices=["export", "local"], default="export")
    ap.add_argument("--what", choices=["all", "history", "cmip6"], default="all")
    ap.add_argument("--years", help="limit history years, e.g. 2020-2024 (quick test)")
    ap.add_argument("--models", help=f"comma-separated CMIP6 models (default {','.join(MODELS)})")
    ap.add_argument("--batch", type=int, default=60, help="districts per request in local mode")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-wait", action="store_true", help="export mode: start the tasks and exit")
    ap.add_argument("--skip-existing", action="store_true", help="export mode: keep tables already in BigQuery")
    ap.add_argument("--load-bq", action="store_true", help="local mode: also load the CSVs into BigQuery")
    a = ap.parse_args()
    if a.models:
        MODELS[:] = [m.strip() for m in a.models.split(",") if m.strip()]
    yrs = HIST_YEARS
    if a.years:
        lo, _, hi = a.years.partition("-")
        yrs = list(range(int(lo), int(hi or lo) + 1))
    init(a.project)
    feats = district_features()
    todo = jobs(yrs if a.what in ("all", "history") else [])
    if a.what == "history":
        todo = todo[:1]
    elif a.what == "cmip6":
        todo = [j for j in todo if j[0] != "ee_climate_history"]
    if a.mode == "export":
        failed = run_export(a.project, a.dataset, todo, feats, wait=not a.no_wait, skip_existing=a.skip_existing)
        if failed:
            sys.exit(f"Earth Engine export failed for: {', '.join(failed)}")
    else:
        frames = run_local(todo, feats, a.batch, a.workers)
        if a.load_bq:
            load_bq(a.project, a.dataset, frames)


if __name__ == "__main__":
    main()
