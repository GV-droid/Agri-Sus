"""Build country-wise (bilateral) import dependencies for pulses and edible oils from India Data Portal.

Source: India Data Portal (indiadataportal.com) dataset "Import Trade Statistics" (DGCI&S monthly country x HS8 imports),
CKAN package `import-trade-statistics` — one CSV per region (imports-from-{african,american,asian,european,oceanic}-countries.csv).
Download them with scripts/fetch_idp.sh (section "trade") into data/raw/trade_idp/ (~1 GB, not committed).

Output: data/processed/app/trade_bilateral_dependencies.csv — one row per fiscal year x country x product with
quantity (t), value (USD mn, INR cr) and shares of that product's / group's imports. The optimizer uses the latest
complete fiscal year to turn "cut palm-oil imports from Indonesia by 30%" into a tonnage the plan must replace.

Units are not constant across the portal's history (value_rs switches between INR lakh and INR crore, value_qt between
tonnes and kg), so every row is normalised against the monthly INR/USD rate implied by the data itself and against the
product's plausible landed price band — see `normalise()`.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "trade_idp")
OUT = os.path.join(ROOT, "data", "processed", "app", "trade_bilateral_dependencies.csv")
FILTERED = os.path.join(RAW, "filtered_pulses_oils.csv")

# HS4 -> (group, product label). Oil-bearing seeds are converted to oil-equivalent in the optimizer, not here.
HS4 = {
    713: ("pulses", None),          # resolved to HS6 below
    1507: ("edible_oils", "soybean oil"),
    1508: ("edible_oils", "groundnut oil"),
    1511: ("edible_oils", "palm oil"),
    1512: ("edible_oils", "sunflower / safflower oil"),
    1513: ("edible_oils", "coconut / palm-kernel oil"),
    1514: ("edible_oils", "rapeseed / mustard oil"),
    1515: ("edible_oils", "other vegetable oils"),
}
HS6_PULSES = {
    71310: "peas", 71320: "chickpea (gram)", 71331: "urad / moong", 71332: "urad / moong",
    71333: "kidney beans", 71334: "other beans", 71335: "other beans", 71339: "urad / moong",
    71340: "lentil (masur)", 71350: "other beans", 71360: "tur (pigeon pea)", 71390: "other pulses",
}
# Plausible landed price band (USD/t) used to detect the quantity unit of each row.
PRICE_BAND = {"pulses": (250, 3000), "edible_oils": (400, 3500)}


def filter_raw(paths: list[str]) -> pd.DataFrame:
    keep = []
    cols = {"date", "country_name", "alpha_3_code", "region_name", "hs_code", "commodity", "unit",
            "value_qt", "commodity_qt", "value_rs", "value_dl"}   # the African file names the quantity commodity_qt
    for p in paths:
        n = 0
        for ch in pd.read_csv(p, usecols=lambda c: c in cols, chunksize=500_000, low_memory=False):
            ch = ch.rename(columns={"commodity_qt": "value_qt"})
            hs = pd.to_numeric(ch["hs_code"], errors="coerce")
            hs4 = (hs // 10000).astype("Int64")
            m = hs4.isin(list(HS4))
            if m.any():
                keep.append(ch[m.fillna(False).to_numpy()])
            n += len(ch)
        print(f"  {os.path.basename(p)}: {n:,} rows scanned", file=sys.stderr)
    df = pd.concat(keep, ignore_index=True)
    df["hs_code"] = pd.to_numeric(df["hs_code"], errors="coerce").astype("int64")
    return df


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ("value_qt", "value_rs", "value_dl"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df = df[df["value_dl"] > 0].copy()
    # value_rs is reported in INR lakh in some periods and INR crore in others: the ratio to USD mn tells which.
    # INR/USD lies in 60..95 over 2015-2026, so rs/dl ~ 600..950 => lakh, ~ 6..9.5 => crore.
    ratio = df["value_rs"] / df["value_dl"]
    df["inr_cr"] = np.where(ratio > 60, df["value_rs"] / 100.0, df["value_rs"])
    df["usd_mn"] = df["value_dl"]
    hs4 = df["hs_code"] // 10000
    df["group"] = hs4.map(lambda h: HS4[int(h)][0])
    df["product"] = np.where(hs4 == 713, (df["hs_code"] // 100).map(HS6_PULSES).fillna("other pulses"),
                             hs4.map(lambda h: HS4[int(h)][1] or ""))
    # Quantity unit: tonnes or kg. Pick the interpretation whose implied USD/t lies in the product's price band.
    lo = df["group"].map(lambda g: PRICE_BAND[g][0])
    hi = df["group"].map(lambda g: PRICE_BAND[g][1])
    q = df["value_qt"].where(df["value_qt"] > 0)
    p_t = df["usd_mn"] * 1e6 / q                 # if value_qt is tonnes
    p_kg = df["usd_mn"] * 1e6 / (q / 1000.0)     # if value_qt is kg
    in_t = (p_t >= lo) & (p_t <= hi)
    in_kg = (p_kg >= lo) & (p_kg <= hi)
    unit_kg = df["unit"].astype(str).str.lower().str.startswith("kg")
    # Rows matching neither band (tiny consignments, data slips) fall back to the closest band edge in log space.
    d_t = np.abs(np.log(p_t.clip(lower=1e-9)) - np.log(np.sqrt(lo * hi)))
    d_kg = np.abs(np.log(p_kg.clip(lower=1e-9)) - np.log(np.sqrt(lo * hi)))
    use_kg = np.where(in_kg & ~in_t, True, np.where(in_t & ~in_kg, False, np.where(in_t & in_kg, unit_kg, d_kg < d_t)))
    df["qty_t"] = np.where(use_kg, q / 1000.0, q).astype(float)
    df["qty_t"] = df["qty_t"].fillna(0.0)
    df["fiscal_year"] = df["date"].map(lambda d: f"{d.year}-{str(d.year + 1)[2:]}" if d.month >= 4
                                       else f"{d.year - 1}-{str(d.year)[2:]}")
    return df


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    g = (df.groupby(["fiscal_year", "country_name", "alpha_3_code", "region_name", "group", "product"], as_index=False)
           .agg(qty_t=("qty_t", "sum"), value_usd_mn=("usd_mn", "sum"), value_inr_cr=("inr_cr", "sum"),
                hs_codes=("hs_code", lambda s: " ".join(sorted({str(x)[:6] for x in s}))), months=("date", "nunique")))
    g = g.rename(columns={"country_name": "country", "alpha_3_code": "alpha3", "region_name": "region",
                          "group": "commodity_group"})
    prod_tot = g.groupby(["fiscal_year", "product"])["qty_t"].transform("sum")
    grp_tot = g.groupby(["fiscal_year", "commodity_group"])["qty_t"].transform("sum")
    g["share_of_product_pct"] = (100 * g["qty_t"] / prod_tot.replace(0, np.nan)).round(2)
    g["share_of_group_pct"] = (100 * g["qty_t"] / grp_tot.replace(0, np.nan)).round(2)
    fy_months = df.groupby("fiscal_year")["date"].nunique()
    g["fy_months_covered"] = g["fiscal_year"].map(fy_months).astype(int)
    g = g.drop(columns=["months"])
    for c in ("qty_t", "value_usd_mn", "value_inr_cr"):
        g[c] = g[c].round(3)
    return g.sort_values(["fiscal_year", "commodity_group", "product", "qty_t"], ascending=[True, True, True, False])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refilter", action="store_true", help="re-scan the raw regional files")
    args = ap.parse_args()
    if args.refilter or not os.path.exists(FILTERED):
        paths = sorted(glob.glob(os.path.join(RAW, "imports-from-*-countries.csv")))
        if not paths:
            sys.exit(f"No raw files in {RAW}; run scripts/fetch_idp.sh trade first")
        print(f"Scanning {len(paths)} regional files …", file=sys.stderr)
        filter_raw(paths).to_csv(FILTERED, index=False)
    raw = pd.read_csv(FILTERED)
    df = normalise(raw)
    out = aggregate(df)
    out.to_csv(OUT, index=False)
    full = out[out["fy_months_covered"] == 12]["fiscal_year"].max()
    print(f"wrote {OUT}: {len(out):,} rows, FY {out['fiscal_year'].min()}..{out['fiscal_year'].max()} "
          f"(latest complete FY {full})", file=sys.stderr)


if __name__ == "__main__":
    main()
