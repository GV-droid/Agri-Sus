#!/usr/bin/env python3
"""Phase-2 feature engineering: fills the MVP gaps and builds the app / BigQuery tables.

Inputs  : data/processed/* (from build_datasets.py), data/raw/{gwra_district, cost_of_cultivation,
          climate_hist_district_year, climate_cmip6_district_year, rainfall_daily_district,
          pmksy_micro_irrigation}.csv, data/reference/{crop_agronomy, policy_constants}.csv
Outputs : data/processed/app/*.csv  (loaded into BigQuery dataset agri_sus_mvp)

Run: .venv/bin/python scripts/build_features.py
"""
import os
import re
import warnings

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

warnings.filterwarnings("ignore")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw")
REF = os.path.join(ROOT, "data", "reference")
PROC = os.path.join(ROOT, "data", "processed")
APP = os.path.join(PROC, "app")

BASE_YEARS = range(2020, 2025)          # agri years used for the 2026 baseline (2020-21 .. 2024-25)
PLAN_YEARS = [2027, 2028, 2029, 2030]
SEASONS = ["Kharif", "Rabi"]


def save(df, name):
    os.makedirs(APP, exist_ok=True)
    p = os.path.join(APP, name + ".csv")
    df.to_csv(p, index=False)
    print(f"  wrote {os.path.relpath(p, ROOT)}  {df.shape}")
    return df


def norm(s):
    s = str(s).lower().replace("&", "and")
    s = re.sub(r"\b(district|dist)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def constants():
    c = pd.read_csv(os.path.join(REF, "policy_constants.csv"))
    return dict(zip(c.parameter, c.value.astype(float)))


# --------------------------------------------------------------------------- GWRA
GWRA_STATE = {"TAMILNADU": "Tamil Nadu"}
GWRA_ALIASES = {  # IN-GRES name -> LGD name (normalised) where fuzzy matching is unsafe
    "sbsnagar": "shahidbhagatsinghnagar",
    "muktsar": "srimuktsarsahib", "kanchipuram": "kancheepuram", "rangareddy": "rangareddy",
    "nilgiris": "thenilgiris", "tuticorin": "thoothukkudi", "kanpurdehat": "kanpurdehat",
    "komarambheemasifabad": "kumurambheemasifabad", "bhadradrikothagudem": "bhadradrikothagudem",
    "kadapa": "ysrkadapa", "spsrnellore": "spsrnellore", "nellore": "spsrnellore",
    "bengaluruurban": "bengaluruurban", "bangalore": "bengaluruurban", "mysore": "mysuru",
    "kalaburagi": "kalaburagi", "gulbarga": "kalaburagi", "belgaum": "belagavi",
    "jayashankarbhupalapally": "jayashankarbhupalapally", "yadadribhuvanagiri": "yadadribhuvanagiri",
    "mahabubnagar": "mahabubnagar", "sriramadasu": "", "kushinagar": "kushinagar",
    "sant ravidas nagar": "bhadohi", "santravidasnagarbhadohi": "bhadohi", "santravidasnagar": "bhadohi",
    "amethi": "amethi", "maunathbhanjan": "mau", "mewat": "nuh", "gurgaon": "gurugram",
    "konaseema": "drbrambedkarkonaseema", "lakhimpurkheri": "kheri", "shrawasti": "shravasti", "siddharthnagar": "siddharthnagar",
    "prayagraj": "prayagraj", "allahabad": "prayagraj", "faizabad": "ayodhya",
    # rest of India
    "nmandaman": "northandmiddleandaman", "karimganj": "sribhumi", "kaimur": "kaimurbhabua",
    "westchamparan": "pashchimchamparan", "eastchamparan": "purbichamparan", "kanker": "uttarbastarkanker",
    "dantewada": "dakshinbastardantewada", "balrampur": "balrampurramanujganj", "balodabazar": "balodabazarbhatapara",
    "goanorth": "northgoa", "goasouth": "southgoa", "sln": "solan", "kin": "kinnaur", "srm": "sirmaur", "kng": "kangra",
    "kul": "kullu", "mnd": "mandi", "shm": "shimla", "bls": "bilaspur", "chm": "chamba", "las": "lahaulandspiti",
    "hmp": "hamirpur", "leh": "lehladakh", "hoshangabad": "narmadapuram", "khandwa": "khandwaeastnimar",
    "khargone": "khargonewestnimar", "chsambhajinagar": "chhatrapatisambhajinagar", "aurangabad": "aurangabad",
    "balasore": "baleshwar", "deogarh": "debagada", "nayagarh": "nayagada", "cuttack": "kataka", "khurda": "khordha",
    "sundargarh": "sundaragada", "angul": "anugola", "bargarh": "baragada", "gbnagar": "gautambuddhanagar",
    "kochbihar": "coochbehar", "hugli": "hooghly", "haora": "howrah",
}


def map_names(src, dm, src_state_col, src_name_col):
    """Map (state, district name) -> LGD district_code using exact/alias/fuzzy matching within state."""
    out = []
    for st, grp in src.groupby(src_state_col):
        lgd = dm[dm.state_name == st]
        if lgd.empty:
            continue
        choices = {norm(n): c for n, c in zip(lgd.district_name, lgd.district_code)}
        for name in grp[src_name_col].unique():
            k = norm(name)
            code, how = choices.get(k), "exact"
            if code is None:
                k = GWRA_ALIASES.get(k, k)
                code, how = choices.get(k), "alias"
            if code is None and k:
                m = process.extractOne(k, list(choices), scorer=fuzz.ratio)
                if m and m[1] >= 80:
                    code, how = choices[m[0]], f"fuzzy{int(m[1])}"
            out.append({src_state_col: st, src_name_col: name, "district_code": code, "match": how})
    return pd.DataFrame(out)


def build_gwra(dm):
    g = pd.read_csv(os.path.join(RAW, "gwra_district.csv"))
    g["state_name"] = g.state_name.map(lambda s: GWRA_STATE.get(s, s.title().replace(" And ", " and ")))
    g["state_key"] = g.state_name.str.lower()
    m = map_names(g[["state_key", "district_name"]].drop_duplicates(),
                  dm.assign(state_key=dm.state_name.str.lower()).drop(columns="state_name")
                  .rename(columns={"state_key": "state_name"}).rename(columns={"state_name": "state_key"})
                  .rename(columns={"state_key": "state_name"}),
                  "state_key", "district_name")
    g = g.merge(m, on=["state_key", "district_name"], how="left").drop(columns="state_key")
    unmatched = g[g.district_code.isna() & (g.assessment_year == "2024-2025")]
    print(f"  GWRA: {g.district_code.notna().mean():.1%} rows matched to LGD; "
          f"{len(unmatched)} unmatched in 2024-25 (e.g. {unmatched.district_name.head(5).tolist()})")
    g = g.dropna(subset=["district_code"])
    g["district_code"] = g.district_code.astype(int)
    agg = {c: "sum" for c in g.columns if c.endswith("_ham") or c.startswith("blocks_") or c.endswith("_ha")}
    agg.update({"rainfall_mm": "mean", "category": "first", "district_name": "first", "state_name": "first",
                "stage_of_extraction_pct": "first", "location_uuid": "count"})
    g = g.groupby(["assessment_year", "district_code"], as_index=False).agg(agg)
    multi = g.location_uuid > 1  # several IN-GRES units merged into one LGD district -> recompute
    g.loc[multi, "stage_of_extraction_pct"] = 100 * g.extraction_total_ham / g.extractable_resource_ham
    g["stage_of_extraction_pct"] = g.stage_of_extraction_pct.round(1)
    g["category"] = pd.cut(g.stage_of_extraction_pct, [-1, 70, 90, 100, 1e9],
                           labels=["Safe", "Semi-Critical", "Critical", "Over-Exploited"]).astype(str)
    g = g.rename(columns={"location_uuid": "ingres_units"})
    return save(g, "gwra_district")


# --------------------------------------------------------------------------- Cost of cultivation
COC_CROP = {"Paddy": "rice", "Wheat": "wheat", "Maize": "maize", "Bajra": "bajra", "Jowar": "jowar",
            "Ragi": "ragi", "Tur (Arhar)": "tur", "Gram": "gram", "Urad": "urad", "Moong": "moong",
            "Masur": "lentil", "Lentil": "lentil", "Groundnut": "groundnut", "Soyabean": "soybean",
            "Rapeseed & Mustard (Toria/Taramira)": "rapeseed_mustard", "Sunflower": "sunflower",
            "Sesamum (Til)": "sesamum", "Cotton": "cotton", "Sugarcane": "sugarcane"}


def build_cost(k):
    c = pd.read_csv(os.path.join(RAW, "cost_of_cultivation.csv"))
    c["crop"] = c.crop_name.map(COC_CROP)
    c = c.dropna(subset=["crop"])
    c["fy_start"] = c.year.str[:4].astype(int)
    c["a2fl_inr_ha"] = c.cul_cost_a2fl.fillna(c.cul_cost_a2 + c.opr_cost_hmn_lab_family.fillna(0))
    c = c[c.fy_start >= c.fy_start.max() - 2]  # latest 3 CACP years (2019-20..2021-22)
    g = c.groupby(["state_name", "crop"], as_index=False).agg(
        a2fl_inr_ha=("a2fl_inr_ha", "mean"), c2_inr_ha=("cul_cost_c2", "mean"),
        cacp_yield_qtl_ha=("derived_yield", "mean"), byproduct_inr_ha=("by_product_value", "mean"),
        base_year=("fy_start", "max"))
    esc = (1 + k["cost_inflation_pct_per_year"] / 100) ** (2026 - g.base_year)
    for col in ["a2fl_inr_ha", "c2_inr_ha", "byproduct_inr_ha"]:
        g[col] = (g[col] * esc).round(0)
    g["cost_basis"] = "CACP " + g.base_year.astype(str) + " escalated to 2026-27"
    nat = g.groupby("crop", as_index=False)[["a2fl_inr_ha", "c2_inr_ha", "byproduct_inr_ha"]].median()
    nat["state_name"] = "_ALL_"
    nat["cost_basis"] = "all-India median"
    return save(pd.concat([g, nat], ignore_index=True), "cost_of_cultivation_state_crop")


# --------------------------------------------------------------------------- Climate
CLIM_VARS = ["kharif_rain_mm", "kharif_tmax_c", "kharif_max_dry_spell_days", "kharif_heavy_rain_days",
             "kharif_et0_mm", "rabi_rain_mm", "rabi_tmax_c", "rabi_tmin_c", "terminal_heat_days",
             "harvest_extreme_rain_days", "rabi_et0_mm"]


def imd_seasonal(dm):
    path = os.path.join(RAW, "rainfall_daily_district.csv")
    r = pd.read_csv(path, usecols=["date", "district_code", "actual"], parse_dates=["date"])
    r["m"], r["y"] = r.date.dt.month, r.date.dt.year
    kh = r[r.m.between(6, 9)].groupby(["district_code", "y"]).agg(imd_kharif_rain_mm=("actual", "sum"),
                                                                  n=("actual", "count"))
    kh = kh[kh.n >= 110].drop(columns="n").reset_index()
    r["ay"] = np.where(r.m >= 11, r.y, r.y - 1)
    rb = r[(r.m >= 11) | (r.m <= 3)].groupby(["district_code", "ay"]).agg(imd_rabi_rain_mm=("actual", "sum"),
                                                                          n=("actual", "count"))
    rb = rb[rb.n >= 140].drop(columns="n").reset_index().rename(columns={"ay": "y"})
    out = kh.merge(rb, on=["district_code", "y"], how="outer").rename(columns={"y": "agri_year"})
    return out


def build_climate(dm):
    h = pd.read_csv(os.path.join(RAW, "climate_hist_district_year.csv"))
    imd = imd_seasonal(dm)
    h = h.merge(imd, on=["district_code", "agri_year"], how="left")
    # child districts missing in IMD -> use parent district's IMD series
    par = dm.set_index("district_code").parent_district_code.to_dict()
    miss = h.imd_kharif_rain_mm.isna()
    if miss.any():
        alt = imd.rename(columns={"district_code": "parent"})
        hh = h[miss].assign(parent=h[miss].district_code.map(par)).drop(columns=["imd_kharif_rain_mm",
                                                                                "imd_rabi_rain_mm"])
        hh = hh.merge(alt, on=["parent", "agri_year"], how="left").drop(columns="parent")
        h = pd.concat([h[~miss], hh], ignore_index=True)
    h = save(h.sort_values(["district_code", "agri_year"]), "climate_history_district_year")

    clim = h[h.agri_year.between(2005, 2024)].groupby("district_code")[CLIM_VARS].mean()
    clim["kharif_dry_spell_prob"] = h[h.agri_year.between(2005, 2024)].groupby("district_code") \
        .kharif_max_dry_spell_days.apply(lambda s: (s >= 14).mean())
    clim = clim.add_prefix("clim_").reset_index()

    proj_path = os.path.join(RAW, "climate_cmip6_district_year.csv")
    rows = []
    if os.path.exists(proj_path):
        p = pd.read_csv(proj_path)
        base = p[p.period == "baseline"].groupby(["district_code", "model"])[CLIM_VARS].mean()
        fut = p[(p.period == "future") & p.agri_year.isin(PLAN_YEARS + [2026])].set_index(
            ["district_code", "model", "agri_year"])[CLIM_VARS]
        d = fut.join(base, rsuffix="_base")
        out = pd.DataFrame(index=d.index)
        for v in CLIM_VARS:
            if v.endswith("_mm"):
                out[v + "_ratio"] = d[v] / d[v + "_base"].replace(0, np.nan)
            else:
                out[v + "_delta"] = d[v] - d[v + "_base"]
        # climate signal = 2026-2030 window mean per model, then ensemble mean (single years are dominated by
        # internal variability); the same delta is applied to every plan year, shocks are layered by scenarios
        per_model = out.groupby(["district_code", "model"]).mean()
        ens = per_model.groupby("district_code").mean()
        ens["n_models"] = per_model.groupby("district_code").size()
        ens = ens.reset_index()
        rows = pd.concat([ens.assign(agri_year=y) for y in [2026] + PLAN_YEARS], ignore_index=True)
    else:
        print("  ! CMIP6 file missing: projections will use climatology only")
        rows = pd.DataFrame([(c, y) for c in clim.district_code for y in [2026] + PLAN_YEARS],
                            columns=["district_code", "agri_year"])
    # every district with observed climate gets a projection row; districts outside the CMIP6 subset keep a zero delta
    grid = pd.DataFrame([(c, y) for c in clim.district_code for y in [2026] + PLAN_YEARS],
                        columns=["district_code", "agri_year"])
    proj = grid.merge(rows, on=["district_code", "agri_year"], how="left").merge(clim, on="district_code", how="left")
    # delta method: observed 2005-24 climatology x CMIP6 change signal
    for v in CLIM_VARS:
        if v.endswith("_mm"):
            r = proj.get(v + "_ratio", pd.Series(1.0, index=proj.index)).clip(0.75, 1.3).fillna(1.0)
            proj["proj_" + v] = (proj["clim_" + v] * r).round(1)
        else:
            dlt = proj.get(v + "_delta", pd.Series(0.0, index=proj.index)).fillna(0.0)
            proj["proj_" + v] = (proj["clim_" + v] + dlt).round(2).clip(lower=0)
    proj["proj_kharif_dry_spell_prob"] = (proj.clim_kharif_dry_spell_prob +
                                          proj.get("kharif_max_dry_spell_days_delta", pd.Series(0.0, index=proj.index)).fillna(0) / 20
                                          ).clip(0, 1).round(3)
    proj["scenario"] = "SSP2-4.5 (NEX-GDDP-CMIP6 ensemble)"
    save(proj, "climate_projection_district_year")
    return h, clim, proj


# --------------------------------------------------------------------------- Micro-irrigation
def build_pmksy(dm):
    p = pd.read_csv(os.path.join(RAW, "pmksy_micro_irrigation.csv"))
    g = p.groupby("district_code", as_index=False).agg(
        micro_irrigation_drip_ha=("phy_achivement_drip", "sum"),
        micro_irrigation_sprinkler_ha=("phy_achivement_sprinkler", "sum"))
    g["micro_irrigation_ha_2015_24"] = g.micro_irrigation_drip_ha + g.micro_irrigation_sprinkler_ha
    return g


# --------------------------------------------------------------------------- Feature stores
def build_baseline_crops(cp, agro, dm):
    mvp = set(dm[dm.is_mvp_state].district_code)
    c = cp[cp.district_code.isin(mvp) & cp.fy_start.isin(BASE_YEARS) & cp.season.isin(SEASONS + ["Whole Year"])]
    c = c[c.crop.isin(agro.index)]
    c.loc[c.season == "Whole Year", "season"] = c.loc[c.season == "Whole Year", "crop"].map(
        lambda x: "Kharif" if x in ("cotton", "sugarcane", "tur") else None)
    c = c.dropna(subset=["season"])
    yrs = c.groupby(["district_code", "crop", "season"]).fy_start.nunique()
    g = c.groupby(["district_code", "crop", "season"]).agg(area_ha=("area_ha", "sum"),
                                                          production_t=("production_t", "sum"))
    g = (g.div(len(BASE_YEARS))).join(yrs.rename("years_reported")).reset_index()
    g["yield_kg_ha"] = (1000 * g.production_t / g.area_ha).where(g.area_ha > 0)
    return g[g.area_ha > 0]


def margin_inputs(feat, agro, cost, msp, k):
    latest = msp[msp.fy_start == msp.fy_start.max()].groupby("crop").msp_inr_per_qtl.max()
    feat["msp_inr_qtl"] = feat.crop.map(latest)
    st_cost = cost[cost.state_name != "_ALL_"].set_index(["state_name", "crop"])
    nat_cost = cost[cost.state_name == "_ALL_"].set_index("crop")
    keys = list(zip(feat.state_name, feat.crop))
    for col in ["a2fl_inr_ha", "byproduct_inr_ha"]:
        v = pd.Series([st_cost[col].get(x, np.nan) for x in keys], index=feat.index)
        feat[col] = v.fillna(feat.crop.map(nat_cost[col]))
    # DES reports rice as milled rice; MSP is for paddy -> convert with 0.67 out-turn ratio
    to_market_unit = feat.crop.map(lambda c: 1 / 0.67 if c == "rice" else 1.0)
    feat["gross_value_inr_ha"] = (feat.pred_yield_kg_ha / 100 * to_market_unit * feat.msp_inr_qtl
                                  + feat.byproduct_inr_ha.fillna(0)).round(0)
    feat["net_farmer_margin_inr_ha"] = (feat.gross_value_inr_ha - feat.a2fl_inr_ha).round(0)
    return feat


def build_feature_store(dm, cp, agro, gw, cost, clim_proj, msp, k):
    mvpd = pd.read_csv(os.path.join(PROC, "mvp", "mvp_district_features.csv"))
    base = build_baseline_crops(cp, agro, dm)
    save(base.merge(dm[["district_code", "district_name", "state_name"]], on="district_code"),
         "baseline_crop_area_district")

    # ---- district-level water & soil context
    gw24 = gw[gw.assessment_year == "2024-2025"].set_index("district_code")
    gw22 = gw[gw.assessment_year == "2022-2023"].set_index("district_code")
    par = dm.set_index("district_code").parent_district_code
    d = mvpd.set_index("district_code")
    ctx = pd.DataFrame(index=d.index)
    for col in ["state_name", "district_name", "region", "net_sown_area_ha", "gross_cropped_area_ha",
                "net_irrigated_area_ha", "groundwater_share", "canal_share", "irrigation_coverage_pct",
                "current_fallow_ha", "soil_n_kg_ha_est", "soil_p_kg_ha_est", "soil_k_kg_ha_est", "soil_oc_pct_est",
                "ph_acidic_share", "ph_alkaline_share", "sulphur_deficient_share", "zinc_deficient_share",
                "gw_pre_monsoon_depth_m", "gw_depth_trend_m_per_yr", "avg_holding_ha"]:
        ctx[col] = d[col]

    def gwcol(df, col):
        own = df[col].reindex(ctx.index)
        return own.fillna(pd.Series(df[col].reindex(par.reindex(ctx.index).values).values, index=ctx.index))
    for col in ["stage_of_extraction_pct", "annual_recharge_ham", "extractable_resource_ham",
                "extraction_total_ham", "extraction_irrigation_ham", "extraction_domestic_ham",
                "extraction_industry_ham", "category", "blocks_safe", "blocks_semi_critical",
                "blocks_critical", "blocks_over_exploited"]:
        ctx["cgwb_" + col] = gwcol(gw24, col)
    ctx["cgwb_stage_change_2022_24_pp"] = ctx.cgwb_stage_of_extraction_pct - gwcol(gw22, "stage_of_extraction_pct")
    ctx["cgwb_from_parent_district"] = gw24.stage_of_extraction_pct.reindex(ctx.index).isna()
    nsa = ctx.net_sown_area_ha.replace(0, np.nan)
    # groundwater available for irrigation at the sustainable cap, expressed as mm over net sown area
    cap = k["gw_recharge_cap_share"]
    ctx["safe_gw_irrigation_ham"] = (cap * ctx.cgwb_extractable_resource_ham - ctx.cgwb_extraction_domestic_ham
                                     - ctx.cgwb_extraction_industry_ham).clip(lower=0)
    ctx["safe_gw_allowance_mm_yr"] = (ctx.safe_gw_irrigation_ham / nsa * 1000).round(0)
    ctx["current_gw_irrigation_mm_yr"] = (ctx.cgwb_extraction_irrigation_ham / nsa * 1000).round(0)
    canal_ha = ctx.net_irrigated_area_ha * ctx.canal_share.fillna(0)
    ctx["canal_supply_mm_season"] = (canal_ha * k["canal_supply_mm_per_irrigated_ha"] / nsa).round(0)
    pm = build_pmksy(dm).set_index("district_code")
    ctx["micro_irrigation_ha_2015_24"] = pm.micro_irrigation_ha_2015_24.reindex(ctx.index).fillna(0)
    ctx["micro_irrigation_share"] = (ctx.micro_irrigation_ha_2015_24 / nsa).clip(upper=1).round(4)
    cl = clim_proj[clim_proj.agri_year == 2026].set_index("district_code")
    for v in ["clim_kharif_rain_mm", "clim_rabi_rain_mm", "clim_kharif_tmax_c", "clim_rabi_tmax_c",
              "clim_terminal_heat_days", "clim_kharif_dry_spell_prob", "clim_kharif_et0_mm", "clim_rabi_et0_mm"]:
        ctx[v] = cl[v].reindex(ctx.index)
    # gap-fill for districts outside the original MVP: state median (flagged), never for CGWB resource figures
    base_nsa = base.groupby(["district_code", "season"]).area_ha.sum().groupby(level=0).max()
    imputed = pd.Series("", index=ctx.index)
    miss = ctx.net_sown_area_ha.isna() & base_nsa.reindex(ctx.index).notna()
    ctx.loc[miss, "net_sown_area_ha"] = base_nsa.reindex(ctx.index)[miss]
    imputed[miss] += "net_sown_area_ha;"
    for col in ["irrigation_coverage_pct", "groundwater_share", "canal_share", "micro_irrigation_share",
                "canal_supply_mm_season", "soil_n_kg_ha_est", "soil_p_kg_ha_est", "soil_k_kg_ha_est", "soil_oc_pct_est",
                "zinc_deficient_share", "sulphur_deficient_share", "ph_acidic_share", "ph_alkaline_share",
                "avg_holding_ha", "current_fallow_ha"]:
        med = ctx.groupby("state_name")[col].transform("median").fillna(ctx[col].median())
        m = ctx[col].isna()
        ctx.loc[m, col] = med[m]
        imputed[m] += col + ";"
    ctx["imputed_fields"] = imputed.str.rstrip(";")
    ctx = ctx.reset_index()
    geo = pd.read_csv(os.path.join(PROC, "geo", "district_centroids.csv"))[["district_code", "lat", "lon", "area_km2"]]
    ctx = ctx.merge(geo, on="district_code", how="left")
    save(ctx, "district_context")

    # ---- candidate crops per district-season: grown in district, or grown in >=20% of the state's districts
    cand = base[["district_code", "crop", "season"]].copy()
    st = base.merge(ctx[["district_code", "state_name"]], on="district_code")
    share = st.groupby(["state_name", "crop", "season"]).district_code.nunique() / \
        ctx.groupby("state_name").district_code.nunique()
    share = share[share >= 0.2].reset_index()[["state_name", "crop", "season"]]
    cand = pd.concat([cand, ctx[["district_code", "state_name"]].merge(share, on="state_name")
                      .drop(columns="state_name")]).drop_duplicates()
    cand = cand[cand.crop.isin(agro[agro.optimizer_crop == 1].index) |
                cand.set_index(["district_code", "crop", "season"]).index.isin(
                    base.set_index(["district_code", "crop", "season"]).index)]
    cand = cand[[s in agro.loc[c, "seasons"].split("|") or c in ("cotton", "sugarcane")
                 for c, s in zip(cand.crop, cand.season)]]

    # baseline yield: district 5-yr mean, else state median, shrunk toward state for thin history
    ystate = st.groupby(["state_name", "crop", "season"]).yield_kg_ha.median().rename("state_yield")
    f = cand.merge(ctx[["district_code", "state_name"]], on="district_code") \
        .merge(base[["district_code", "crop", "season", "area_ha", "yield_kg_ha", "years_reported"]],
               on=["district_code", "crop", "season"], how="left") \
        .merge(ystate.reset_index(), on=["state_name", "crop", "season"], how="left")
    f["baseline_area_ha"] = f.area_ha.fillna(0)
    w = (f.years_reported.fillna(0) / 5).clip(0, 1)
    f["baseline_yield_kg_ha"] = (w * f.yield_kg_ha.fillna(0) + (1 - w) * f.state_yield).round(0)
    f["yield_source"] = np.where(f.years_reported.fillna(0) >= 3, "district history",
                                 np.where(f.years_reported.fillna(0) > 0, "district+state blend", "state median"))
    f = f.dropna(subset=["baseline_yield_kg_ha"]).drop(columns=["area_ha", "yield_kg_ha", "state_yield"])
    save(f, "district_crop_candidates")
    return ctx, f


def build_macro(agro, k):
    fb = pd.read_csv(os.path.join(PROC, "national_food_balance.csv"))
    tg = pd.read_csv(os.path.join(PROC, "trade_commodity_groups.csv"))
    last = fb.fy_start.max()
    b = fb[fb.fy_start == last].set_index("commodity")
    imp = tg[(tg.flow == "import") & (tg.fy_start == last)].set_index("group")
    rows = []
    for crop, a in agro.iterrows():
        cat = a.commodity_category
        commodity = {"Pulse": "pulses", "Edible oilseed": "edible_oils"}.get(cat, crop if crop in b.index else None)
        if commodity is None:
            continue
        r = b.loc[commodity]
        grp = commodity if commodity in imp.index else None
        landed = (imp.loc[grp, "value_usd_mn"] * 1e6 / imp.loc[grp, "quantity_t"]) if grp else np.nan
        rows.append({
            "crop_name": crop, "commodity": commodity, "commodity_category": cat,
            "oil_or_pulse_recovery_factor": a.product_recovery if cat == "Edible oilseed" else 1.0,
            "baseline_year": r.year, "baseline_domestic_prod_mt": round(r.domestic_production_t / 1e6, 3),
            "baseline_imports_mt": round(r.imports_t / 1e6, 3), "baseline_exports_mt": round(r.exports_t / 1e6, 3),
            "baseline_consumption_mt": round(r.apparent_consumption_t / 1e6, 3),
            "baseline_ssr_pct": r.ssr_pct, "baseline_import_value_usd_mn": r.import_value_usd_mn,
            "import_landed_price_usd_t": round(landed, 1) if pd.notna(landed) else None,
            "price_elasticity": a.price_elasticity,
        })
    return save(pd.DataFrame(rows), "national_macro_constraints")


def build_yield_training(cp, ctx, hist, agro):
    """district x crop x season x year yields joined with season-matched climate (for BQML / local model)."""
    c = cp[cp.district_code.isin(ctx.district_code) & cp.season.isin(SEASONS) & cp.crop.isin(agro.index)
           & (cp.fy_start >= 2001) & (cp.area_ha >= 100) & (cp.yield_kg_ha > 0)].copy()
    c = c.rename(columns={"fy_start": "agri_year"})
    h = hist.set_index(["district_code", "agri_year"])
    clim = h.groupby(level=0)[CLIM_VARS].mean()
    j = c.join(h[CLIM_VARS], on=["district_code", "agri_year"]).join(clim.add_prefix("clim_"), on="district_code")
    kh = j.season == "Kharif"
    j["season_rain_mm"] = np.where(kh, j.kharif_rain_mm, j.rabi_rain_mm)
    j["season_rain_anom_pct"] = 100 * (j.season_rain_mm / np.where(kh, j.clim_kharif_rain_mm,
                                                                     j.clim_rabi_rain_mm) - 1)
    j["season_tmax_anom_c"] = np.where(kh, j.kharif_tmax_c - j.clim_kharif_tmax_c, j.rabi_tmax_c - j.clim_rabi_tmax_c)
    j["dry_spell_days"] = np.where(kh, j.kharif_max_dry_spell_days, np.nan)
    j["heat_days_anom"] = np.where(kh, 0, j.terminal_heat_days - j.clim_terminal_heat_days)
    j["extreme_rain_days"] = np.where(kh, j.kharif_heavy_rain_days, j.harvest_extreme_rain_days)
    j = j.merge(ctx[["district_code", "soil_n_kg_ha_est", "soil_p_kg_ha_est", "soil_k_kg_ha_est", "soil_oc_pct_est",
                     "irrigation_coverage_pct", "groundwater_share", "lat", "lon"]], on="district_code", how="left")
    j = j.sort_values("agri_year")
    grp = j.groupby(["district_code", "crop", "season"]).yield_kg_ha
    j["trailing5_yield_kg_ha"] = grp.transform(lambda s: s.shift(1).rolling(5, min_periods=3).mean())
    j["yield_ratio"] = j.yield_kg_ha / j.trailing5_yield_kg_ha
    j = j.dropna(subset=["season_rain_mm", "trailing5_yield_kg_ha"])
    j = j[j.yield_ratio.between(0.2, 3)]
    cols = ["district_code", "state_name", "district_name", "crop", "season", "agri_year", "area_ha",
            "yield_kg_ha", "trailing5_yield_kg_ha", "yield_ratio", "season_rain_mm", "season_rain_anom_pct",
            "season_tmax_anom_c", "dry_spell_days", "heat_days_anom", "extreme_rain_days", "soil_n_kg_ha_est",
            "soil_p_kg_ha_est", "soil_k_kg_ha_est", "soil_oc_pct_est", "irrigation_coverage_pct",
            "groundwater_share", "lat", "lon"]
    return save(j[cols], "yield_training")


def main():
    k = constants()
    agro = pd.read_csv(os.path.join(REF, "crop_agronomy.csv")).set_index("crop")
    dm = pd.read_csv(os.path.join(PROC, "district_master.csv"))
    cp = pd.read_csv(os.path.join(PROC, "crop_production_district.csv"))
    msp = pd.read_csv(os.path.join(PROC, "msp.csv"))
    print("GWRA ..."); gw = build_gwra(dm)
    print("cost ..."); cost = build_cost(k)
    print("climate ..."); hist, clim, proj = build_climate(dm)
    print("features ..."); ctx, cand = build_feature_store(dm, cp, agro, gw, cost, proj, msp, k)
    print("macro ..."); build_macro(agro, k)
    print("yield training ..."); build_yield_training(cp, ctx, hist, agro)
    import shutil
    shutil.copy(os.path.join(REF, "crop_agronomy.csv"), os.path.join(APP, "crop_agronomy.csv"))
    shutil.copy(os.path.join(REF, "policy_constants.csv"), os.path.join(APP, "policy_constants.csv"))
    msp[msp.fy_start == msp.fy_start.max()].to_csv(os.path.join(APP, "msp_latest.csv"), index=False)


if __name__ == "__main__":
    main()
