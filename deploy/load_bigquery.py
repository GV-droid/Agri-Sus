"""Load the Agri Sus processed tables into BigQuery and create views, ledger/trace tables and the BigQuery ML models.

usage: python deploy/load_bigquery.py --project MY_PROJECT [--dataset agri_sus_mvp] [--location US]
                                      [--skip-bqml] [--with-ee] [--sql-only]

  --with-ee   build climate_projection_ee from the Google Earth Engine tables (run scripts/gee_climate.py first)
              and train the BigQuery ML climate factors on it
  --sql-only  skip the CSV loads and only (re)run the SQL files
"""
from __future__ import annotations

import argparse
import glob
import os
import re

import pandas as pd
from google.cloud import bigquery

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "data", "processed", "app")

# history tables are range-partitioned by agri_year; everything is clustered on its usual filter columns
YEARS = bigquery.RangePartitioning(field="agri_year", range_=bigquery.PartitionRange(start=1990, end=2041, interval=1))
LAYOUT = {
    "yield_training": (YEARS, ["state_name", "crop", "season"]),
    "climate_history_district_year": (YEARS, ["district_code"]),
    "climate_projection_district_year": (None, ["district_code"]),
    "district_crop_candidates": (None, ["district_code", "crop"]),
    "baseline_crop_area_district": (None, ["district_code", "crop"]),
    "district_context": (None, ["state_name", "district_code"]),
    "trade_bilateral_dependencies": (None, ["fiscal_year", "commodity_group", "product"]),
    "gwra_district": (None, ["district_code"]),
}
DESCRIPTIONS = {
    "district_context": "District water, soil, irrigation and land context (CGWB GWRA, Soil Health Card, Land Use Statistics, Agricultural Census via India Data Portal)",
    "district_crop_candidates": "District x crop x season candidates with 2020-24 baseline area and yield (DES APY via India Data Portal)",
    "baseline_crop_area_district": "Baseline district crop area (DES Area-Production-Yield)",
    "yield_training": "District-crop-season yields 2004-24 with weather anomalies, used to train the yield models",
    "climate_history_district_year": "Observed district climate indicators (NASA POWER, IMD) per agricultural year",
    "climate_projection_district_year": "District climate projection 2026-30, SSP2-4.5 (NEX-GDDP-CMIP6 delta method)",
    "msp_latest": "Minimum Support Prices (CACP via India Data Portal)",
    "trade_bilateral_dependencies": "Country-wise pulses and edible-oil imports by fiscal year (DGCIS via India Data Portal)",
    "cost_of_cultivation_state_crop": "Cost of cultivation A2+FL by state and crop (CACP)",
    "national_macro_constraints": "National food-balance and import constraints",
    "crop_agronomy": "Crop agronomy: crop coefficients, duration, nitrogen credit",
    "policy_constants": "Policy and economic constants used by the optimizer",
    "gwra_district": "CGWB Dynamic Ground Water Resources assessment by district",
}


def sql_statements(path: str, subs: dict) -> list[str]:
    sql = open(path).read()
    for k, v in subs.items():
        sql = sql.replace("${" + k + "}", v)
    # drop comment lines first so a ';' inside a comment never splits a statement
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in sql.split(";") if s.strip()]


# CREATE MODEL options that register the model in the Vertex AI Model Registry (needs Vertex AI admin rights)
REGISTRY = re.compile(r",\s*model_registry\s*=\s*'[^']*'\s*,\s*vertex_ai_model_id\s*=\s*'[^']*'", re.I)


def run_sql(bq: bigquery.Client, path: str, subs: dict) -> None:
    name = os.path.basename(path)
    for body in sql_statements(path, subs):
        head = re.sub(r"\s+", " ", body)[:100]
        print(f"running {name}: {head}")
        try:
            rows = list(bq.query(body).result())
        except Exception as e:
            if not REGISTRY.search(body):
                raise
            # still train the model in BigQuery ML when the Vertex AI registration is not allowed in this project
            print(f"    ! Vertex AI Model Registry registration failed ({str(e)[:200]}) - training without it")
            body = REGISTRY.sub("", body)
            rows = list(bq.query(body).result())
        if rows and body.upper().startswith(("SELECT", "WITH")):
            for r in rows[:10]:
                print("   ", dict(r))


def load_tables(bq: bigquery.Client, project: str, dataset: str) -> None:
    for path in sorted(glob.glob(os.path.join(APP, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        df = pd.read_csv(path)
        table_id = f"{project}.{dataset}.{name}"
        part, cluster = LAYOUT.get(name, (None, None))
        cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        if part:
            cfg.range_partitioning = part
        if cluster:
            cfg.clustering_fields = cluster
        # partitioning cannot change on WRITE_TRUNCATE, so replace the table (views re-resolve by name)
        bq.delete_table(table_id, not_found_ok=True)
        bq.load_table_from_dataframe(df, table_id, job_config=cfg).result()
        if name in DESCRIPTIONS:
            t = bq.get_table(table_id)
            t.description = DESCRIPTIONS[name]
            bq.update_table(t, ["description"])
        layout = ("partitioned by agri_year, " if part else "") + (f"clustered by {', '.join(cluster)}" if cluster else "")
        print(f"loaded {name:36s} {len(df):>8,} rows  {layout}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"), required=not os.getenv("GOOGLE_CLOUD_PROJECT"))
    ap.add_argument("--dataset", default=os.getenv("BQ_DATASET", "agri_sus_mvp"))
    ap.add_argument("--location", default=os.getenv("BQ_LOCATION", "US"))
    ap.add_argument("--skip-bqml", action="store_true")
    ap.add_argument("--with-ee", action="store_true", help="build climate_projection_ee from Earth Engine tables")
    ap.add_argument("--sql-only", action="store_true", help="do not reload the CSV tables")
    a = ap.parse_args()
    bq = bigquery.Client(project=a.project)
    ds = bigquery.Dataset(f"{a.project}.{a.dataset}")
    ds.location = a.location
    ds.description = ("Agri Sus - all-India district crop-reallocation policy simulator (India Data Portal, CGWB, "
                      "DGCIS, NASA CMIP6, Google Earth Engine)")
    bq.create_dataset(ds, exists_ok=True)
    if not a.sql_only:
        load_tables(bq, a.project, a.dataset)
    climate = "climate_projection_ee" if a.with_ee else "climate_projection_district_year"
    subs = {"PROJECT": a.project, "DATASET": a.dataset, "CLIMATE_TABLE": climate}
    files = ["01_schema.sql"] + (["04_climate_from_earth_engine.sql"] if a.with_ee else []) + \
            ([] if a.skip_bqml else ["02_bqml_yield_model.sql"])
    for f in files:
        run_sql(bq, os.path.join(ROOT, "bigquery", f), subs)
    print("done")


if __name__ == "__main__":
    main()
