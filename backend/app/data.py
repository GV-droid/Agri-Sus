"""Data access layer + 2027-2030 planning feature table.

DataStore reads the processed tables from local CSVs (default, offline) or BigQuery (DATA_BACKEND=bigquery),
so the identical code path runs on a laptop and on Cloud Run.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import settings
from .yield_model import YieldModel

PLAN_YEARS = [2027, 2028, 2029, 2030]
TABLES = ["district_context", "district_crop_candidates", "baseline_crop_area_district", "national_macro_constraints",
          "climate_projection_district_year", "cost_of_cultivation_state_crop", "msp_latest", "crop_agronomy",
          "policy_constants"]
# features degrade gracefully if absent: country-wise imports (India Data Portal), BigQuery ML challenger outputs
OPTIONAL_TABLES = ["trade_bilateral_dependencies", "yield_climate_factor_bqml", "yield_model_comparison"]
BQML_TABLES = {"yield_climate_factor_bqml", "yield_model_comparison"}  # created in BigQuery only (bigquery/02)
SEASON_DAYS = {"Kharif": 122, "Rabi": 151}
SUGARCANE_FRP_INR_QTL = 355.0  # FRP 2025-26 (CCEA)
N_VALUE_INR_KG = 120.0  # economic cost of urea-N incl. subsidy (~Rs 2,600/45 kg bag economic cost / 0.46 N)
# Agronomic ceilings for a *district average* yield (kg/ha; rice = milled rice, cotton = lint as reported by DES).
# A few APY rows carry unit errors (e.g. groundnut 132 t/ha, Kerala moong 5 t/ha); values above 1.6x the ceiling
# are treated as missing and imputed from the state (else national) median, the rest are clipped to the ceiling.
YIELD_CEILING_KG_HA = {
    "rice": 5200, "wheat": 5300, "maize": 9500, "bajra": 3500, "jowar": 3500, "ragi": 3500, "tur": 2200,
    "gram": 2600, "moong": 1500, "urad": 1600, "lentil": 1800, "groundnut": 4200, "soybean": 2600,
    "rapeseed_mustard": 2500, "sunflower": 2700, "sesamum": 1300, "cotton": 950, "sugarcane": 130000,
}


def clean_yields(cand: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Repair implausible district baseline yields (unit errors / zero yields) before they reach the LP."""
    cand = cand.copy()
    y = cand.baseline_yield_kg_ha.astype(float)
    ceiling = cand.crop.map(YIELD_CEILING_KG_HA).astype(float)
    bad = (y <= 0) | y.isna() | (ceiling.notna() & (y > 1.6 * ceiling))
    ok = y.where(~bad)
    fill = ok.groupby([cand.state_name, cand.crop]).transform("median").fillna(ok.groupby(cand.crop).transform("median"))
    y = ok.fillna(fill)
    floor = 0.15 * ok.groupby(cand.crop).transform("median")
    y = y.clip(lower=floor.fillna(0), upper=ceiling.fillna(np.inf))
    n_fixed = int((bad | (cand.baseline_yield_kg_ha.astype(float) - y).abs().gt(1)).sum())
    cand["baseline_yield_kg_ha"] = y.round(1)
    return cand, n_fixed


class DataStore:
    def __init__(self) -> None:
        self._t: dict[str, pd.DataFrame] = {}
        self.backend = settings.data_backend
        self.bq_errors: dict[str, str] = {}  # table -> BigQuery error type, for tables served from the snapshot
        self.climate_source = "NASA POWER + NEX-GDDP-CMIP6 (bundled snapshot)"
        for name in TABLES:
            self._t[name] = self._load(name)
        for name in OPTIONAL_TABLES:
            if name in BQML_TABLES and settings.yield_model == "prior":
                continue
            if name in BQML_TABLES and not (self.backend == "bigquery" and settings.project) and \
                    not os.path.exists(os.path.join(settings.data_dir, f"{name}.csv")):
                continue  # local snapshot: no BigQuery ML outputs
            try:
                self._t[name] = self._load(name)
            except Exception as e:  # pragma: no cover
                print(f"[data] optional table {name} unavailable: {e}")
        self._t["district_crop_candidates"], self.yield_rows_repaired = clean_yields(self._t["district_crop_candidates"])
        self.constants = {r.parameter: float(r.value) for r in self._t["policy_constants"].itertuples()}
        self.yield_model = YieldModel.load()
        self.yield_engine, self.bqml_factor = self._yield_engine()
        with open(settings.geo_path) as f:
            self.geojson = json.load(f)

    def _yield_engine(self) -> tuple[dict, pd.Series | None]:
        """Champion/challenger: BigQuery ML boosted-tree climate factors replace the agronomic prior only if the
        challenger beat the prior's BQML twin on 2020-24 hold-out WMAPE (or YIELD_MODEL=bqml forces it)."""
        mode = settings.yield_model
        info = {"active": "agronomic_prior", "mode": mode, "holdout_wmape_pct": {}}
        if not self.has("yield_climate_factor_bqml"):
            info["reason"] = "YIELD_MODEL=prior" if mode == "prior" else "BigQuery ML factors not available"
            if mode != "prior" and "yield_climate_factor_bqml" in self.bq_errors:
                info["reason"] += f" (BigQuery: {self.bq_errors['yield_climate_factor_bqml']})"
            return info, None
        cmp_ = self._t.get("yield_model_comparison")
        w = {}
        if cmp_ is not None:
            w = {str(r.model): float(r.holdout_wmape_pct) for r in cmp_.itertuples() if pd.notna(r.holdout_wmape_pct)}
        info["holdout_wmape_pct"] = w
        wins = w.get("bqml_boosted_tree", np.inf) < w.get("bqml_linear_prior_twin", np.inf)
        if mode != "bqml" and not wins:
            info["reason"] = "prior kept: the boosted tree did not beat it on 2020-24 hold-out WMAPE"
            return info, None
        t = self._t["yield_climate_factor_bqml"].dropna(subset=["climate_factor"])
        t = t.astype({"district_code": "int64", "agri_year": "int64", "climate_factor": "float64"})
        s = t.set_index(["district_code", "crop", "season", "agri_year"]).climate_factor
        s = s[~s.index.duplicated()]
        info.update(active="bqml_boosted_tree", rows=int(len(s)),
                    reason="forced by YIELD_MODEL=bqml" if mode == "bqml" and not wins else
                    "BigQuery ML boosted tree beat the prior on 2020-24 hold-out WMAPE")
        return info, s

    def _load(self, name: str) -> pd.DataFrame:
        if self.backend == "bigquery" and settings.project:
            try:
                from google.cloud import bigquery
                client = bigquery.Client(project=settings.project)
                # the Earth Engine pipeline writes an alternative climate table; CLIMATE_TABLE selects it
                src = settings.climate_table if name == "climate_projection_district_year" else name
                # numpy dtypes (not pandas nullable Int64/boolean) so BigQuery and the CSV snapshot behave identically
                df = client.query(f"SELECT * FROM `{settings.project}.{settings.bq_dataset}.{src}`").to_dataframe(
                    int_dtype=None, bool_dtype=None)
                if src != name:
                    self.climate_source = f"Google Earth Engine: CHIRPS, ERA5-Land, NEX-GDDP-CMIP6 (BigQuery {src})"
                return df
            except Exception as e:  # pragma: no cover - fall back to bundled snapshot
                self.bq_errors[name] = type(e).__name__
                print(f"[data] BigQuery load failed for {name}: {e}; using local CSV")
        return pd.read_csv(os.path.join(settings.data_dir, f"{name}.csv"))

    @property
    def bq_ok(self) -> bool:
        """True only if every core table was actually read from BigQuery."""
        return self.backend == "bigquery" and bool(settings.project) and not any(t in self.bq_errors for t in TABLES)

    @property
    def data_source(self) -> str:
        if not (self.backend == "bigquery" and settings.project):
            return "local snapshot"
        if self.bq_ok:
            return "BigQuery"
        failed = sorted({e for t, e in self.bq_errors.items() if t in TABLES})
        return f"local snapshot (BigQuery read failed: {', '.join(failed)})"

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self._t[name]

    def has(self, name: str) -> bool:
        return name in self._t

    @property
    def context(self) -> pd.DataFrame:
        return self._t["district_context"]


@lru_cache(maxsize=1)
def store() -> DataStore:
    return DataStore()


@dataclass
class ClimateShock:
    """A scenario perturbation layered on top of the CMIP6 SSP2-4.5 projection."""
    year: int
    kharif_rain_pct: float = 0.0
    kharif_tmax_c: float = 0.0
    rabi_tmax_c: float = 0.0
    states: list = field(default_factory=list)  # empty = all states


def _price_table(ds: DataStore) -> pd.Series:
    msp = ds["msp_latest"].dropna(subset=["crop"]).groupby("crop").msp_inr_per_qtl.first()
    msp["sugarcane"] = SUGARCANE_FRP_INR_QTL
    return msp


def plan_features(shocks: list | None = None, price_realisation: float = 0.9,
                  procurement_crops: tuple = ("rice", "wheat")) -> pd.DataFrame:
    """Per district x crop x season x plan-year: predicted yield, net margin, irrigation need, CWAR, N credit."""
    ds = store()
    shocks = shocks or []
    cand = ds["district_crop_candidates"].copy()
    ctx = ds.context.set_index("district_code")
    agr = ds["crop_agronomy"].set_index("crop")
    proj = ds["climate_projection_district_year"].set_index(["district_code", "agri_year"])
    c = ds.constants

    rows = []
    for y in PLAN_YEARS:
        f = cand.copy()
        f["agri_year"] = y
        rows.append(f)
    f = pd.concat(rows, ignore_index=True)
    p = proj.reindex(pd.MultiIndex.from_arrays([f.district_code, f.agri_year])).reset_index(drop=True)
    num = p.select_dtypes("number").columns
    if p[num].isna().any().any():  # newly carved districts without a centroid series -> state mean
        p[num] = p[num].fillna(p[num].groupby(f.state_name.values).transform("mean")).fillna(p[num].mean())
    x = ctx.reindex(f.district_code).reset_index(drop=True)
    kh = (f.season == "Kharif").values

    clim_rain = np.where(kh, p.clim_kharif_rain_mm, p.clim_rabi_rain_mm)
    rain = np.where(kh, p.proj_kharif_rain_mm, p.proj_rabi_rain_mm).astype(float)
    tx = np.where(kh, p.kharif_tmax_c_delta, p.rabi_tmax_c_delta).astype(float)
    heat = np.where(kh, 0.0, p.terminal_heat_days_delta).astype(float)
    dry = np.where(kh, p.proj_kharif_max_dry_spell_days, 0.0).astype(float)
    unshocked = (rain, tx, heat, dry)
    for s in shocks:
        m = (f.agri_year.values == s.year) & (f.state_name.isin(s.states).values if s.states else True)
        rain = np.where(m & kh, rain * (1 + s.kharif_rain_pct / 100), rain)
        dry = np.where(m & kh & (s.kharif_rain_pct <= -10), dry + 0.5 * abs(s.kharif_rain_pct), dry)
        tx = np.where(m & kh, tx + s.kharif_tmax_c, tx)
        tx = np.where(m & ~kh, tx + s.rabi_tmax_c, tx)
        heat = np.where(m & ~kh, heat + 6.0 * s.rabi_tmax_c, heat)  # ~6 extra >=32C days per +1C (NASA POWER 2001-25)
    def climate_rows(rain, tx, heat, dry):
        anom = (rain / np.where(clim_rain > 0, clim_rain, np.nan) - 1) * 100
        return anom, pd.DataFrame({
            "crop": f.crop, "season": f.season, "state_name": f.state_name, "agri_year": f.agri_year,
            "baseline_yield_kg_ha": f.baseline_yield_kg_ha,
            "irrigation_coverage_pct": x.irrigation_coverage_pct.values,
            "season_rain_anom_pct": np.nan_to_num(anom), "season_tmax_anom_c": tx, "dry_spell_days": dry,
            "heat_days_anom": heat,
            "extreme_rain_days": np.where(kh, p.proj_kharif_heavy_rain_days, p.proj_harvest_extreme_rain_days),
        })

    rain_anom, clim_rows = climate_rows(rain, tx, heat, dry)
    prior = ds.yield_model.climate_factor(clim_rows)
    f["climate_factor"] = prior
    f["yield_kg_ha"] = ds.yield_model.predict(clim_rows)
    if ds.bqml_factor is not None:
        # BigQuery ML challenger supplies the projected-climate factor; what-if shocks act through the prior's
        # marginal response on top of it; crops/districts the challenger does not cover keep the prior
        learned = ds.bqml_factor.reindex(pd.MultiIndex.from_arrays(
            [f.district_code, f.crop, f.season, f.agri_year])).to_numpy(dtype=float)
        shock = prior / ds.yield_model.climate_factor(climate_rows(*unshocked)[1]) if shocks else 1.0
        cf = np.where(np.isnan(learned), prior, np.clip(learned * shock, 0.4, 1.3))
        f["yield_kg_ha"] = f.yield_kg_ha / prior * cf
        f["climate_factor"] = cf
    f["season_rain_mm"] = rain
    f["season_rain_anom_pct"] = np.round(np.nan_to_num(rain_anom), 1)
    f["season_tmax_anom_c"] = np.round(tx, 2)

    # --- water: FAO-56 style ETc minus effective rain, PDMC savings on micro-irrigated share
    a = agr.reindex(f.crop)
    et0 = np.where(kh, p.proj_kharif_et0_mm, p.proj_rabi_et0_mm).astype(float)
    sd = np.where(kh, SEASON_DAYS["Kharif"], SEASON_DAYS["Rabi"])
    span = np.clip(a.duration_days.values / sd, 0.5, 1.2)
    etc = a.kc_avg.values * et0 * span + a.extra_water_mm.values
    eff_rain = c["effective_rain_factor"] * rain * np.minimum(span, 1.0)
    pdmc = 1 - c["pdmc_water_saving_pct"] / 100 * x.micro_irrigation_share.fillna(0).values
    f["etc_mm"] = np.round(etc, 1)
    f["irrigation_need_mm"] = np.round(np.maximum(etc - eff_rain, 0) * pdmc, 1)
    supply = x.canal_supply_mm_season.fillna(0).values + x.safe_gw_allowance_mm_yr.fillna(0).values / 2
    f["cwar"] = np.round(f.irrigation_need_mm / np.maximum(supply, 25), 3)

    # --- economics (Rs/ha); DES rice yields are milled rice -> paddy for MSP
    price = _price_table(ds).reindex(f.crop).values
    realise = np.where(f.crop.isin(procurement_crops), 1.0, price_realisation)
    # DES rice = milled rice -> paddy (out-turn 0.67); DES cotton = lint -> kapas for MSP (ginning out-turn ~0.34)
    paddy = np.select([f.crop == "rice", f.crop == "cotton"], [1 / agr.loc["rice", "product_recovery"], 1 / 0.34], 1.0)
    cost = ds["cost_of_cultivation_state_crop"]
    st = cost[cost.state_name != "_ALL_"].set_index(["state_name", "crop"])
    nat = cost[cost.state_name == "_ALL_"].set_index("crop")
    key = pd.MultiIndex.from_arrays([f.state_name, f.crop])
    a2fl = st.a2fl_inr_ha.reindex(key).values
    a2fl = np.where(np.isnan(a2fl), nat.a2fl_inr_ha.reindex(f.crop).values, a2fl)
    byp = st.byproduct_inr_ha.reindex(key).values
    byp = np.where(np.isnan(byp), nat.byproduct_inr_ha.reindex(f.crop).values, byp)
    f["price_inr_qtl"] = price * realise
    f["revenue_inr_ha"] = f.yield_kg_ha * paddy / 100 * f.price_inr_qtl + np.nan_to_num(byp)
    f["cost_a2fl_inr_ha"] = np.nan_to_num(a2fl, nan=np.nanmedian(a2fl))
    f["net_margin_inr_ha"] = np.round(f.revenue_inr_ha - f.cost_a2fl_inr_ha, 0)
    f["n_credit_kg_ha"] = a.n_credit_next_crop_kg_ha.values
    f["n_value_inr_ha"] = f.n_credit_kg_ha * N_VALUE_INR_KG
    f["family"] = a.family.values
    f["category"] = a.commodity_category.values
    f["optimizer_crop"] = a.optimizer_crop.values.astype(int)
    # irrigated share & GW share drive groundwater draft; calibrated so baseline matches CGWB irrigation draft
    f["irrigated_share"] = (x.irrigation_coverage_pct.fillna(0).values / 100).clip(0, 1)
    f["gw_share"] = x.groundwater_share.fillna(0).values
    f["gw_draft_ham_per_ha"] = f.irrigation_need_mm * f.irrigated_share * f.gw_share / 1000
    return f


def gw_calibration(feat: pd.DataFrame) -> pd.Series:
    """district factor so modelled baseline GW irrigation draft == CGWB GWRA 2024-25 irrigation extraction."""
    ctx = store().context.set_index("district_code")
    f0 = feat[feat.agri_year == PLAN_YEARS[0]]
    model = (f0.baseline_area_ha * f0.gw_draft_ham_per_ha).groupby(f0.district_code).sum()
    obs = ctx.cgwb_extraction_irrigation_ham.reindex(model.index)
    k = (obs / model.replace(0, np.nan)).clip(0.3, 3.0).fillna(1.0)
    return k
