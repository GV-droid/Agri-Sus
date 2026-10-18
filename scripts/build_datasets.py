"""Clean + harmonise the raw AgriSus datasets into analysis-ready tables.

Inputs  : data/raw/*  (fetched by scripts/fetch_idp.sh and scripts/fetch_trade.py)
Outputs : data/processed/*.csv  (all-India, LGD district codes)
          data/processed/mvp/*.csv (7 MVP states, one-row-per-district feature store)

Run: .venv/bin/python scripts/build_datasets.py
"""
import os
import warnings

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw")
REF = os.path.join(ROOT, "data", "reference")
OUT = os.path.join(ROOT, "data", "processed")
MVP = os.path.join(OUT, "mvp")
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Coverage: every state / UT in India, tagged with an agro-economic zone for reporting.
MVP_STATES = {
    "Punjab": "North", "Haryana": "North", "Uttar Pradesh": "North", "Uttarakhand": "North",
    "Himachal Pradesh": "North", "Jammu And Kashmir": "North", "Ladakh": "North", "Delhi": "North",
    "Chandigarh": "North",
    "Madhya Pradesh": "Central", "Chhattisgarh": "Central",
    "Bihar": "East", "Jharkhand": "East", "Odisha": "East", "West Bengal": "East",
    "Assam": "North-East", "Arunachal Pradesh": "North-East", "Manipur": "North-East", "Meghalaya": "North-East",
    "Mizoram": "North-East", "Nagaland": "North-East", "Sikkim": "North-East", "Tripura": "North-East",
    "Rajasthan": "West", "Gujarat": "West", "Maharashtra": "West", "Goa": "West",
    "The Dadra And Nagar Haveli And Daman And Diu": "West",
    "Karnataka": "South", "Tamil Nadu": "South", "Andhra Pradesh": "South", "Telangana": "South",
    "Kerala": "South", "Puducherry": "South",
    "Andaman And Nicobar Islands": "Islands", "Lakshadweep": "Islands",
}
RECENT_YEARS = ["2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025"]
STATEWB_EXCLUDE = {"Punjab", "Uttar Pradesh", "Assam", "Meghalaya"}

# Canonical crop keys used across APY / UPAG / MSP / AgCensus / irrigation tables.
CROP_MAP = {
    "Rice": "rice", "Paddy": "rice", "Paddy - Common": "rice",
    "Wheat": "wheat", "Maize": "maize", "Jowar": "jowar", "Jowar - Hybrid": "jowar",
    "Bajra": "bajra", "Ragi": "ragi", "Barley": "barley", "Small Millets": "small_millets",
    "Tur": "tur", "Arhar/Tur": "tur", "Arhar(Tur)": "tur", "Arhar (Tur)": "tur",
    "Gram": "gram", "Urad": "urad", "Moong": "moong", "Moong(Green Gram)": "moong",
    "Lentil": "lentil", "Masoor": "lentil", "Masur (Lentil)": "lentil",
    "Other Pulses": "other_pulses",
    "Groundnut": "groundnut", "Groundnut In Shell": "groundnut",
    "Rapeseed & Mustard": "rapeseed_mustard", "Rapeseed &Mustard": "rapeseed_mustard",
    "Rapeseed/Mustard": "rapeseed_mustard", "Rapeseed and Mustard": "rapeseed_mustard",
    "Soybean": "soybean", "Soyabean": "soybean", "Soyabean - Yellow": "soybean",
    "Sunflower": "sunflower", "Sunflower Seed": "sunflower", "Sesamum": "sesamum",
    "Safflower": "safflower", "Nigerseed": "nigerseed", "Niger Seed": "nigerseed",
    "Castorseed": "castor", "Castor Seed": "castor", "Linseed": "linseed",
    "Cotton": "cotton", "Cotton(Lint)": "cotton", "Cotton - Medium Staple": "cotton",
    "Sugarcane": "sugarcane", "Jute": "jute",
}
CROP_GROUP = {
    **{c: "cereal" for c in ["rice", "wheat", "maize", "jowar", "bajra", "ragi", "barley", "small_millets"]},
    **{c: "pulse" for c in ["tur", "gram", "urad", "moong", "lentil", "other_pulses"]},
    **{c: "edible_oilseed" for c in ["groundnut", "rapeseed_mustard", "soybean", "sunflower",
                                     "sesamum", "safflower", "nigerseed", "linseed"]},
    "castor": "non_edible_oilseed", "cotton": "fibre", "jute": "fibre", "sugarcane": "sugar",
}

SHC_MIDPOINTS = {  # ICAR soil-test rating classes -> representative value
    "Nitrogen": {"Low": 180, "Medium": 420, "High": 650},          # kg/ha available N
    "Phosphorus": {"Low": 7, "Medium": 17.5, "High": 35},          # kg/ha available P2O5-P (Olsen)
    "Potassium": {"Low": 80, "Medium": 195, "High": 350},          # kg/ha available K
    "Organic Carbon": {"Low": 0.35, "Medium": 0.625, "High": 0.9},  # %
}


def save(df, name, sub=OUT):
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, name)
    df.to_csv(path, index=False)
    print(f"  wrote {os.path.relpath(path, ROOT)}  {df.shape}")
    return df


def int_code(s):
    return pd.to_numeric(s, errors="coerce").astype("Int64")


def fy_start(year):
    return int(str(year)[:4])


def norm_state(s):
    return s.str.strip().str.title().str.replace(" And ", " and ", regex=False)


# --------------------------------------------------------------------------- districts
def build_district_master():
    print("district master")
    frames = []
    for f in ["apy_upag.csv", "land_use_classification_district.csv", "irrigation_sources_district.csv",
              "apy_district.csv"]:
        d = pd.read_csv(os.path.join(RAW, f), usecols=["year", "state_name", "state_code",
                                                       "district_name", "district_code"], low_memory=False)
        d["src"] = f
        frames.append(d)
    d = pd.concat(frames).dropna(subset=["district_code"])
    d["district_code"] = int_code(d.district_code)
    d["state_code"] = int_code(d.state_code)
    d["y"] = d.year.map(fy_start)
    d = d.sort_values("y").drop_duplicates("district_code", keep="last")
    cw = pd.read_csv(os.path.join(REF, "district_split_crosswalk.csv"))
    d = d.merge(cw[["district_code", "parent_district_code"]], on="district_code", how="left")
    d["parent_district_code"] = d.parent_district_code.fillna(d.district_code).astype("Int64")
    d["region"] = d.state_name.map(MVP_STATES)
    d["is_mvp_state"] = d.region.notna()
    out = d[["state_code", "state_name", "district_code", "district_name", "parent_district_code",
             "region", "is_mvp_state"]].sort_values(["state_name", "district_name"])
    return save(out, "district_master.csv")


# --------------------------------------------------------------------------- crops
def build_crop_production():
    print("crop production (APY legacy 1997-2013 + UPAG 2013-2025)")
    up = pd.read_csv(os.path.join(RAW, "apy_upag.csv"))
    up = up.rename(columns={"area": "area_ha", "production": "production_t", "yield": "yield_kg_ha"})
    up["source"] = "UPAG"
    lg = pd.read_csv(os.path.join(RAW, "apy_district.csv"), low_memory=False)
    lg = lg.rename(columns={"area": "area_ha", "production": "production_t"})
    lg["yield_kg_ha"] = lg["yield"] * 1000
    lg["source"] = "DES-APY"
    # UPAG is the authoritative series from 2013-14; keep legacy only for earlier years.
    lg = lg[lg.year.map(fy_start) < 2013]
    cols = ["year", "state_code", "state_name", "district_code", "district_name", "season",
            "crop_name", "area_ha", "production_t", "yield_kg_ha", "source"]
    d = pd.concat([up[cols], lg[cols]])
    d = d[d.season.isin(["Kharif", "Rabi", "Summer", "Autumn", "Winter", "Whole Year"])]
    d["crop"] = d.crop_name.map(CROP_MAP)
    d = d.dropna(subset=["crop", "district_code"])
    d["crop_group"] = d.crop.map(CROP_GROUP)
    d["district_code"] = int_code(d.district_code)
    d["state_code"] = int_code(d.state_code)
    # Cotton production is reported in bales (170 kg lint) in legacy APY; UPAG reports tonnes.
    d.loc[(d.crop == "cotton") & (d.source == "DES-APY"), "production_t"] *= 0.17
    d["yield_kg_ha"] = np.where(d.area_ha > 0, d.production_t * 1000 / d.area_ha, np.nan)
    d.loc[d.area_ha <= 0, ["area_ha"]] = np.nan
    d = (d.groupby(["year", "state_code", "state_name", "district_code", "district_name", "season",
                    "crop", "crop_group", "source"], as_index=False)
         .agg(area_ha=("area_ha", "sum"), production_t=("production_t", "sum")))
    d["yield_kg_ha"] = (d.production_t * 1000 / d.area_ha).where(d.area_ha > 0).round(1)
    d["fy_start"] = d.year.map(fy_start)
    return save(d.sort_values(["state_name", "district_name", "fy_start", "crop", "season"]),
                "crop_production_district.csv")


def build_crop_stats(cp):
    """District x crop x season statistics over the last 5 years (optimizer inputs)."""
    print("crop stats (5-yr)")
    r = cp[cp.year.isin(RECENT_YEARS)]
    g = r.groupby(["state_name", "district_code", "district_name", "crop", "crop_group", "season"])
    s = g.agg(years_reported=("year", "nunique"), area_ha_mean=("area_ha", "mean"),
              production_t_mean=("production_t", "mean"), yield_kg_ha_mean=("yield_kg_ha", "mean"),
              yield_kg_ha_std=("yield_kg_ha", "std"), yield_kg_ha_max=("yield_kg_ha", "max")).reset_index()
    s["yield_cv"] = (s.yield_kg_ha_std / s.yield_kg_ha_mean).round(3)

    # 10-yr linear yield trend (kg/ha/yr) captures structural change.
    long = cp[cp.fy_start >= 2015].dropna(subset=["yield_kg_ha"])

    def slope(x):
        return np.polyfit(x.fy_start, x.yield_kg_ha, 1)[0] if x.fy_start.nunique() >= 4 else np.nan

    tr = (long.groupby(["district_code", "crop", "season"]).apply(slope)
          .rename("yield_trend_kg_ha_per_yr").reset_index())
    s = s.merge(tr, on=["district_code", "crop", "season"], how="left")
    return s.round(2)


# --------------------------------------------------------------------------- soil
def build_soil():
    print("soil health card (village -> district, chunked)")
    parts = []
    usecols = ["year", "state_name", "state_code", "district_name", "district_code",
               "nutrient_name", "nutrient_level", "value"]
    for ch in pd.read_csv(os.path.join(RAW, "soil_health_card_village.csv"), usecols=usecols,
                          chunksize=2_000_000):
        parts.append(ch.groupby(usecols[:-1], dropna=False)["value"].sum())
    s = pd.concat(parts).groupby(level=list(range(7))).sum().reset_index()
    s["district_code"] = int_code(s.district_code)
    long = s.rename(columns={"value": "samples"})
    save(long, "soil_health_district_long.csv")

    # Not every nutrient is tested every cycle (e.g. Punjab/Haryana skipped N in 2024-25), so the
    # district table pools all available SHC cycles; per-year detail stays in the long table.
    long = long.copy()
    long["year"] = "pooled_" + "_".join(sorted(long.year.dropna().unique()))
    long = long.groupby(["year", "state_name", "district_code", "district_name", "nutrient_name",
                         "nutrient_level"], as_index=False).samples.sum()
    tot = long.groupby(["year", "district_code", "nutrient_name"]).samples.transform("sum")
    long["share"] = long.samples / tot
    key = ["year", "state_name", "district_code", "district_name"]
    wide = long.pivot_table(index=key, columns=["nutrient_name", "nutrient_level"], values="share")
    out = pd.DataFrame(index=wide.index)
    for nut, mids in SHC_MIDPOINTS.items():
        tag = nut.lower().replace(" ", "_")
        for lvl in ["Low", "Medium", "High"]:
            out[f"{tag}_{lvl.lower()}_share"] = wide.get((nut, lvl))
        tested = sum(wide.get((nut, l), 0).notna() for l in mids) > 0
        est = sum(wide.get((nut, l), 0).fillna(0) * v for l, v in mids.items())
        out[f"{tag}_est"] = est.where(tested)
    out["ph_acidic_share"] = wide.get(("Soil Ph", "Acidic"))
    out["ph_neutral_share"] = wide.get(("Soil Ph", "Neutral"))
    out["ph_alkaline_share"] = wide.get(("Soil Ph", "Alkaline"))
    out["ec_saline_share"] = wide.get(("Electrical Conductivity", "Saline"))
    for micro in ["Sulphur", "Zinc", "Boron", "Iron", "Manganese", "Copper"]:
        out[f"{micro.lower()}_deficient_share"] = wide.get((micro, "Deficient"))
    n = long[long.nutrient_name == "Soil Ph"].groupby(key).samples.sum()
    out["n_samples"] = n
    out = out.reset_index().round(4)
    out.columns.name = None
    out = out.rename(columns={"nitrogen_est": "soil_n_kg_ha_est", "phosphorus_est": "soil_p_kg_ha_est",
                              "potassium_est": "soil_k_kg_ha_est", "organic_carbon_est": "soil_oc_pct_est"})
    return save(out, "soil_health_district.csv")


# --------------------------------------------------------------------------- groundwater
def build_groundwater(dm):
    print("groundwater wells -> district/year")
    cols = ["date", "state_name", "district_code", "station_name", "latitude", "longitude", "currentlevel", "source"]
    g = pd.concat([pd.read_csv(os.path.join(RAW, f), usecols=cols, low_memory=False)
                   for f in ["groundwater_cgwb_wells.csv", "groundwater_statewb_wells.csv"]])
    # State-board series for these states are not depth-below-ground (Punjab reports water-table
    # elevation, ~150 m amsl; UP has a different datum with extreme outliers) -> CGWB only there.
    bad = (g.source != "CGWB") & norm_state(g.state_name).isin(STATEWB_EXCLUDE)
    g = g[~bad]
    g["date"] = pd.to_datetime(g.date, errors="coerce")
    g = g.dropna(subset=["date", "currentlevel", "district_code"])
    g = g[(g.currentlevel >= 0) & (g.currentlevel <= 200)]  # depth below ground (m); drop artefacts
    g["district_code"] = int_code(g.district_code)
    m = g.date.dt.month
    g["window"] = np.select([m.isin([4, 5, 6]), m.isin([10, 11, 12])], ["pre_monsoon", "post_monsoon"], "other")
    g = g[g.window != "other"]
    # Water year: post-monsoon (Oct-Dec) of year Y pairs with pre-monsoon of Y+1 -> use calendar year.
    g["year"] = g.date.dt.year
    g["well_id"] = g.source + "|" + g.station_name.astype(str) + "|" + g.latitude.round(4).astype(str) + "|" + \
        g.longitude.round(4).astype(str)
    w = g.groupby(["district_code", "well_id", "year", "window"]).currentlevel.mean().reset_index()

    dy = (w.groupby(["district_code", "year", "window"])
          .agg(depth_m_median=("currentlevel", "median"), wells=("well_id", "nunique")).reset_index())
    dy = dy.pivot_table(index=["district_code", "year"], columns="window",
                        values=["depth_m_median", "wells"]).reset_index()
    dy.columns = ["_".join([c for c in col if c]).replace("depth_m_median_", "depth_m_")
                  for col in dy.columns]
    dy["seasonal_fluctuation_m"] = dy.depth_m_pre_monsoon - dy.depth_m_post_monsoon
    dy = dy.merge(dm[["district_code", "state_name", "district_name"]], on="district_code", how="left")
    save(dy.round(3), "groundwater_district_year.csv")

    # Long-term trend per well (pre-monsoon), aggregated to district. +ve = water table falling.
    pre = w[w.window == "pre_monsoon"]

    def slope(x):
        return np.polyfit(x.year, x.currentlevel, 1)[0] if x.year.nunique() >= 4 else np.nan

    ws = pre.groupby(["district_code", "well_id"]).apply(slope).rename("slope").dropna().reset_index()
    tr = ws.groupby("district_code").agg(
        gw_depth_trend_m_per_yr=("slope", "median"),
        gw_wells_falling_share=("slope", lambda s: (s > 0.1).mean()),
        gw_trend_wells=("slope", "size")).reset_index()
    last = (dy.dropna(subset=["depth_m_pre_monsoon"]).sort_values("year")
            .groupby("district_code").tail(3).groupby("district_code")
            .agg(gw_pre_monsoon_depth_m=("depth_m_pre_monsoon", "mean"),
                 gw_post_monsoon_depth_m=("depth_m_post_monsoon", "mean"),
                 gw_seasonal_fluctuation_m=("seasonal_fluctuation_m", "mean"),
                 gw_last_year=("year", "max")).reset_index())
    out = last.merge(tr, on="district_code", how="outer")
    return save(out.round(3), "groundwater_district_summary.csv")


# --------------------------------------------------------------------------- irrigation & land use
def build_irrigation():
    print("irrigation sources")
    d = pd.read_csv(os.path.join(RAW, "irrigation_sources_district.csv"))
    d["district_code"] = int_code(d.district_code)
    d["src"] = d.area_sub_classification.map({
        "Government": "canal", "Private": "canal", "Tank": "tank", "Tubewell": "tubewell",
        "Other Well": "other_well", "Other Source": "other"})
    p = d.pivot_table(index=["year", "state_name", "district_code", "district_name", "irrigated_area_type"],
                      columns="src", values="irrigated_area", aggfunc="sum").reset_index()
    p.columns.name = None
    srcs = ["canal", "tank", "tubewell", "other_well", "other"]
    p["total_ha"] = p[srcs].sum(axis=1, min_count=1)
    p["groundwater_share"] = (p.tubewell.fillna(0) + p.other_well.fillna(0)) / p.total_ha
    p["canal_share"] = p.canal / p.total_ha
    p["tank_share"] = p.tank / p.total_ha
    p["irrigated_area_type"] = p.irrigated_area_type.map({"Net Irrigated Area": "net", "Gross Irrigated Area": "gross"})
    p = p.rename(columns={s: f"{s}_ha" for s in srcs})
    p["fy_start"] = p.year.map(fy_start)
    return save(p.round(4), "irrigation_sources_district.csv")


def build_land_use():
    print("land use")
    d = pd.read_csv(os.path.join(RAW, "land_use_classification_district.csv"))
    d["district_code"] = int_code(d.district_code)
    d["metric"] = d.area_sub_classification.fillna(d.land_use_metrics)
    p = d.pivot_table(index=["year", "state_name", "district_code", "district_name"], columns="metric",
                      values="area", aggfunc="sum").reset_index()
    p.columns.name = None
    ren = {"Reporting Area": "reporting_area_ha", "Net Area Sown": "net_sown_area_ha",
           "Cropped Area": "gross_cropped_area_ha", "Area Sown More Than Once": "area_sown_more_than_once_ha",
           "Forests": "forest_ha", "Area under Non Agricultural Uses": "non_agri_use_ha",
           "Barren and Unculturable Land": "barren_ha", "Culturable waste Land": "culturable_waste_ha",
           "Current Fallow": "current_fallow_ha", "Fallow Lands other than current Fallows": "other_fallow_ha",
           "Permanent Pasture and other Grazing Land": "pasture_ha",
           "Land under Misc. Tree Crops and Groves not included in Net Area Sown": "tree_crops_ha"}
    p = p.rename(columns=ren)
    p["cropping_intensity_pct"] = 100 * p.gross_cropped_area_ha / p.net_sown_area_ha
    p["fy_start"] = p.year.map(fy_start)
    return save(p.round(2), "land_use_district.csv")


# --------------------------------------------------------------------------- agcensus
def build_agcensus():
    print("agricultural census (non-crop 2010-11 & 2015-16, crop 2010-11)")
    nc = pd.read_csv(os.path.join(RAW, "agcensus_noncrop_district.csv"), low_memory=False)
    nc["district_code"] = int_code(nc.district_code)
    num = nc.select_dtypes("number").columns.drop(["id", "state_code", "district_code"], errors="ignore")
    key = ["year", "state_name", "district_code", "district_name"]
    tot = nc.groupby(key)[list(num)].sum().reset_index()
    small = nc[nc.farm_size_category.isin(["Marginal", "Small"])].groupby(key)[["hol_no", "hol_ar"]].sum()
    tot = tot.merge(small.rename(columns={"hol_no": "ms_hol_no", "hol_ar": "ms_hol_ar"}).reset_index(), on=key)
    out = tot[key].copy()
    out["holdings_no"] = tot.hol_no
    out["holdings_area_ha"] = tot.hol_ar
    out["avg_holding_ha"] = tot.hol_ar / tot.hol_no
    out["marginal_small_holdings_share"] = tot.ms_hol_no / tot.hol_no
    out["marginal_small_area_share"] = tot.ms_hol_ar / tot.hol_ar
    out["net_irrigated_area_ha"] = tot.net_irr_ar
    for s in ["canal", "tank", "well", "tube", "othr"]:
        out[f"irr_{s}_area_ha"] = tot[f"{s}_ar"]
    out["tubewells_electric"] = tot.twl_elc_n
    out["tubewells_diesel"] = tot.twl_dsl_n
    out["tubewells_total"] = tot.twl_t
    out["wells_with_pumpset"] = tot.wl_ps_t
    out["gross_cropped_irrigated_ha"] = tot.gca_irr_ar
    out["gross_cropped_unirrigated_ha"] = tot.gca_unirr_ar
    out["gross_cropped_irrigated_share"] = tot.gca_irr_ar / tot.gca_tot
    save(out.round(4), "agcensus_holdings_district.csv")

    print("  agcensus crop (198 MB, chunked)")
    parts = []
    for ch in pd.read_csv(os.path.join(RAW, "agcensus_crop_district.csv"), chunksize=1_000_000,
                          usecols=["year", "state_name", "district_code", "district_name", "crop_name",
                                   "irr_ar", "unirr_ar", "total_ar"]):
        parts.append(ch.groupby(["year", "state_name", "district_code", "district_name", "crop_name"])
                     [["irr_ar", "unirr_ar", "total_ar"]].sum())
    c = pd.concat(parts).groupby(level=list(range(5))).sum().reset_index()
    c["district_code"] = int_code(c.district_code)
    c["crop"] = c.crop_name.map(CROP_MAP)
    c["irrigated_share"] = c.irr_ar / c.total_ar
    c = c.rename(columns={"irr_ar": "irrigated_ha", "unirr_ar": "unirrigated_ha", "total_ar": "total_ha"})
    return out, save(c.round(4), "agcensus_crop_irrigation_district.csv")


# --------------------------------------------------------------------------- msp
def build_msp():
    print("msp")
    m = pd.read_csv(os.path.join(RAW, "msp.csv"))
    m = m.rename(columns={"crop": "crop_variety", "min_support_price": "msp_inr_per_qtl"})
    m["crop"] = m.crop_variety.map(CROP_MAP)
    m["fy_start"] = m.year.map(fy_start)
    m = m[["year", "fy_start", "season", "crop", "crop_variety", "msp_inr_per_qtl"]]
    return save(m.sort_values(["crop_variety", "fy_start"]), "msp.csv")


# --------------------------------------------------------------------------- trade & food balance
TRADE_GROUPS = [  # (group, crop key or None, HS prefixes, conversion to primary-equivalent tonnes)
    ("pulses", "pulse", ["0713"], 1.0),
    ("edible_oils", "edible_oil", ["1507", "1508", "1509", "1510", "1511", "1512", "1513", "1514",
                                   "1515"], 1.0),
    ("oilseeds", "edible_oilseed", ["1201", "1202", "1204", "1205", "1206", "1207"], 1.0),
    ("rice", "rice", ["1006"], 1.0),
    ("wheat", "wheat", ["1001", "1101"], 1.0),
    ("maize", "maize", ["1005"], 1.0),
    ("coarse_grains_other", "coarse", ["1002", "1003", "1004", "1007", "1008"], 1.0),
    ("sugar", "sugar", ["1701"], 1.0),
    ("oilcakes_meals", "meal", ["2304", "2305", "2306"], 1.0),
    ("fertilisers", "fert", ["31"], 1.0),
]
PULSE_HS6 = {"071310": "peas", "071320": "gram", "071331": "urad_moong", "071332": "adzuki",
             "071333": "kidney_beans", "071339": "other_beans", "071340": "lentil", "071350": "broad_beans",
             "071360": "tur", "071390": "other_pulses"}

# Primary oilseed -> edible oil recovery (SEA/DFPD conventions; groundnut in pods).
OIL_RECOVERY = {"groundnut": 0.29, "rapeseed_mustard": 0.34, "soybean": 0.18, "sunflower": 0.35,
                "sesamum": 0.40, "safflower": 0.30, "nigerseed": 0.33, "linseed": 0.33}


def classify_hs(code):
    if code.startswith("151530"):  # castor oil - non edible
        return None
    best = None
    for grp, _, prefs, _ in TRADE_GROUPS:
        for p in prefs:
            if code.startswith(p) and (best is None or len(p) > best[1]):
                best = (grp, len(p))
    return best[0] if best else None


def build_trade():
    print("trade")
    frames = [pd.read_csv(os.path.join(RAW, f), dtype={"hs_code": str, "hs2": str})
              for f in ["trade_hs8_import.csv", "trade_hs8_export.csv"]]
    t = pd.concat(frames)
    t["fy_start"] = t.fiscal_year.map(fy_start)
    t["group"] = t.hs_code.map(classify_hs)
    t["quantity_t"] = np.where(t.unit == "KGS", t.quantity / 1000, np.nan)
    t.loc[t.unit == "THD", "quantity_t"] = np.nan
    t["pulse_type"] = t.hs_code.str[:6].map(PULSE_HS6)
    save(t, "trade_hs8_agri.csv")
    g = (t.dropna(subset=["group"]).groupby(["flow", "fiscal_year", "fy_start", "group"])
         .agg(quantity_t=("quantity_t", "sum"), value_usd_mn=("value_usd_mn", "sum")).reset_index())
    save(g.round(2), "trade_commodity_groups.csv")
    p = (t[t.group == "pulses"].groupby(["flow", "fiscal_year", "pulse_type"])
         .agg(quantity_t=("quantity_t", "sum"), value_usd_mn=("value_usd_mn", "sum")).reset_index())
    save(p.round(2), "trade_pulses_by_type.csv")
    return g


def build_food_balance(cp, tg):
    """National self-sufficiency ratio (FAO): SSR = P / (P + M - X); IDR = M / (P + M - X)."""
    print("national food balance / SSR")
    nat = cp[cp.fy_start >= 2013].groupby(["year", "fy_start", "crop", "crop_group"]).production_t.sum().reset_index()
    rows = []
    for (year, fy), d in nat.groupby(["year", "fy_start"]):
        prod = {
            "rice": d.loc[d.crop == "rice", "production_t"].sum(),
            "wheat": d.loc[d.crop == "wheat", "production_t"].sum(),
            "maize": d.loc[d.crop == "maize", "production_t"].sum(),
            "pulses": d.loc[d.crop_group == "pulse", "production_t"].sum(),
            "edible_oils": sum(d.loc[d.crop == c, "production_t"].sum() * r for c, r in OIL_RECOVERY.items()),
        }
        for grp, p in prod.items():
            m = tg[(tg.flow == "import") & (tg.fy_start == fy) & (tg.group == grp)]
            x = tg[(tg.flow == "export") & (tg.fy_start == fy) & (tg.group == grp)]
            if m.empty and x.empty:
                continue
            mi, xi = m.quantity_t.sum(), x.quantity_t.sum()
            supply = p + mi - xi
            rows.append({"year": year, "fy_start": fy, "commodity": grp, "domestic_production_t": p,
                         "imports_t": mi, "exports_t": xi, "apparent_consumption_t": supply,
                         "ssr_pct": 100 * p / supply, "import_dependency_pct": 100 * mi / supply,
                         "import_value_usd_mn": m.value_usd_mn.sum(), "export_value_usd_mn": x.value_usd_mn.sum()})
    fb = pd.DataFrame(rows).round(2)
    fb["note"] = np.where(fb.commodity == "edible_oils",
                          "production = primary oilseeds x oil recovery; excludes secondary oils "
                          "(cottonseed, rice bran, coconut, palm) so SSR is a lower bound", "")
    return save(fb, "national_food_balance.csv")


# --------------------------------------------------------------------------- MVP feature store
def latest(df, key="district_code", year_col="fy_start", require=None):
    d = df.dropna(subset=[require]) if require else df
    return d.sort_values(year_col).groupby(key).tail(1)


def build_mvp(dm, cp, stats, soil, gw, irr, lu, agc, agc_crop, msp):
    print("district feature store (all India)")
    base = dm[dm.is_mvp_state].copy()
    parent = base.set_index("district_code").parent_district_code

    def with_parent(df, cols, tag):
        """Attach columns by own code; fall back to pre-split parent district when missing."""
        df = df.set_index("district_code")[cols]
        own = base[["district_code"]].join(df, on="district_code")
        par = base[["parent_district_code"]].join(df, on="parent_district_code")
        miss = own[cols].isna().all(axis=1)
        own.loc[miss, cols] = par.loc[miss, cols].values
        own[f"{tag}_from_parent_district"] = miss & base.parent_district_code.ne(base.district_code)
        return own.drop(columns="district_code")

    f = base.copy()
    l = latest(lu, require="net_sown_area_ha")
    f = f.merge(l[["district_code", "year", "net_sown_area_ha", "gross_cropped_area_ha", "cropping_intensity_pct",
                   "current_fallow_ha", "other_fallow_ha", "culturable_waste_ha", "reporting_area_ha"]]
                .rename(columns={"year": "land_use_year"}), on="district_code", how="left")
    ni = latest(irr[irr.irrigated_area_type == "net"], require="total_ha")
    f = f.merge(ni[["district_code", "year", "total_ha", "groundwater_share", "canal_share", "tank_share"]]
                .rename(columns={"year": "irrigation_year", "total_ha": "net_irrigated_area_ha"}),
                on="district_code", how="left")
    f["irrigation_coverage_pct"] = 100 * f.net_irrigated_area_ha / f.net_sown_area_ha

    sl = soil.sort_values("year").groupby("district_code").tail(1)
    soil_cols = [c for c in sl.columns if c.endswith(("_share", "_est")) or c == "n_samples"]
    f = pd.concat([f.reset_index(drop=True),
                   with_parent(sl, soil_cols + ["year"], "soil").reset_index(drop=True)
                   .rename(columns={"year": "soil_card_year"})], axis=1)

    gw_cols = [c for c in gw.columns if c != "district_code"]
    f = pd.concat([f, with_parent(gw, gw_cols, "gw").reset_index(drop=True)], axis=1)

    # Prefer Ag Census 2015-16; fall back to 2010-11 where a district is absent from 2015-16.
    a = agc.sort_values("year").groupby("district_code").tail(1)
    a_cols = ["year", "avg_holding_ha", "marginal_small_holdings_share", "marginal_small_area_share",
              "gross_cropped_irrigated_share"]
    f = pd.concat([f, with_parent(a, a_cols, "agcensus").reset_index(drop=True)
                   .rename(columns={"year": "agcensus_year"})], axis=1)

    # Cropping pattern (5-yr mean area, all seasons).
    cs = stats.groupby(["district_code", "crop"]).area_ha_mean.sum().unstack()
    grp = stats.groupby(["district_code", "crop_group"]).area_ha_mean.sum().unstack()
    tot = cs.sum(axis=1)
    pat = pd.DataFrame({
        "crop_area_tracked_ha": tot,
        "rice_area_share": cs.get("rice") / tot, "wheat_area_share": cs.get("wheat") / tot,
        "pulse_area_share": grp.get("pulse") / tot, "edible_oilseed_area_share": grp.get("edible_oilseed") / tot,
        "sugarcane_area_share": cs.get("sugarcane") / tot, "cotton_area_share": cs.get("cotton") / tot,
        "dominant_crops": cs.apply(lambda r: "-".join(r.dropna().sort_values(ascending=False).index[:3]), axis=1),
    }).reset_index()
    f = f.merge(pat, on="district_code", how="left")
    ycols = {}
    for c in ["rice", "wheat", "tur", "gram", "urad", "moong", "lentil", "groundnut", "rapeseed_mustard",
              "soybean", "sunflower", "sesamum", "maize"]:
        y = stats[stats.crop == c].groupby("district_code").apply(
            lambda x: np.average(x.yield_kg_ha_mean, weights=x.area_ha_mean)
            if x.area_ha_mean.sum() > 0 and x.yield_kg_ha_mean.notna().all() else np.nan)
        ycols[f"{c}_yield_kg_ha_5yr"] = y
        ycols[f"{c}_area_ha_5yr"] = stats[stats.crop == c].groupby("district_code").area_ha_mean.sum()
    f = f.merge(pd.DataFrame(ycols).reset_index(), on="district_code", how="left")
    save(f.round(4), "mvp_district_features.csv", MVP)

    # District x crop x season optimizer table with MSP and irrigation share.
    s = stats[stats.state_name.isin(MVP_STATES)].copy()
    s["region"] = s.state_name.map(MVP_STATES)
    lm = msp.dropna(subset=["crop"]).sort_values("fy_start").groupby("crop").tail(1)
    lm = lm[~lm.crop_variety.str.contains("Grade|Black|Long|Maldandi", regex=True)]
    s = s.merge(lm[["crop", "msp_inr_per_qtl", "year"]].rename(columns={"year": "msp_year"}), on="crop", how="left")
    s["gross_value_at_msp_inr_ha"] = (s.yield_kg_ha_mean / 100 * s.msp_inr_per_qtl).round(0)
    ac = agc_crop.dropna(subset=["crop"]).groupby(["district_code", "crop"]).agg(
        irrigated_ha=("irrigated_ha", "sum"), total_ha=("total_ha", "sum"))
    ac["irrigated_share_agcensus_2010"] = ac.irrigated_ha / ac.total_ha
    ac = ac[["irrigated_share_agcensus_2010"]].reset_index()
    s = s.merge(base[["district_code", "parent_district_code"]], on="district_code", how="left")
    s = s.merge(ac, on=["district_code", "crop"], how="left")
    fill = s.merge(ac.rename(columns={"district_code": "parent_district_code",
                                      "irrigated_share_agcensus_2010": "p"}),
                   on=["parent_district_code", "crop"], how="left").p
    s["irrigated_share_agcensus_2010"] = s.irrigated_share_agcensus_2010.fillna(fill)
    save(s.drop(columns="parent_district_code").round(4), "mvp_district_crop_season.csv", MVP)
    return f


def main():
    dm = build_district_master()
    cp = build_crop_production()
    stats = save(build_crop_stats(cp), "crop_stats_district_5yr.csv")
    soil = build_soil()
    gw = build_groundwater(dm)
    irr = build_irrigation()
    lu = build_land_use()
    agc, agc_crop = build_agcensus()
    msp = build_msp()
    tg = build_trade()
    build_food_balance(cp, tg)
    build_mvp(dm, cp, stats, soil, gw, irr, lu, agc, agc_crop, msp)


if __name__ == "__main__":
    main()
