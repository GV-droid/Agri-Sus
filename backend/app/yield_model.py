"""Climate-aware crop yield model.

Target   : yield_ratio = yield(t) / trailing-5-year mean yield (removes district/crop level & slow trend)
Terms    : rainfall deficit/surplus/excess x rainfed share, crop-specific Tmax sensitivity, wheat terminal heat,
           Kharif dry spell >= 14 d x rainfed, extreme-rain days (literature priors, one calibrated shrinkage).
Cloud    : the same terms are trained as a BigQuery ML LINEAR_REG model (bigquery/02_bqml_yield_model.sql).

Usage: python -m app.yield_model train      (from backend/)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd

from .config import settings

NUM = ["season_rain_anom_pct", "season_tmax_anom_c", "dry_spell_days", "heat_days_anom", "extreme_rain_days",
       "irrigation_coverage_pct"]
EXCLUDE = {"sugarcane", "cotton"}  # not reallocated; units/yield dynamics differ
MODEL_PATH = os.path.join(settings.model_dir, "yield_model.joblib")
METRICS_PATH = os.path.join(settings.model_dir, "yield_model_metrics.json")

# Literature priors for the log-yield response (each term documented in docs/MODEL_CARD.md):
#  rainfed yield elasticity to monsoon deficit ~0.35 (Birthal et al. 2014; Auffhammer et al. 2012)
#  +1 C seasonal Tmax: wheat -5%, rice -3%, others -3% (Lobell et al. 2012; Asseng et al. 2015; Zhao et al. 2017)
#  wheat terminal heat: -0.5% per extra Feb-Mar day >= 32 C (ICAR-IIWBR); Kharif dry spell >= 14 d: -5% rainfed
#  waterlogging: -10% per +100% excess rain above +50% on rainfed area; extreme rain day: -1%
TERMS = {
    "rain_deficit_x_rainfed": 0.35, "rain_surplus_x_rainfed": 0.05, "rain_excess_x_rainfed": -0.10,
    "tmax_anom_c_x_sensitivity": 1.0, "wheat_terminal_heat_days": -0.005, "kharif_dry_spell14_x_rainfed": -0.05,
    "extreme_rain_days": -0.01,
}
TMAX_SENS = {"wheat": -0.05, "rice": -0.03}


def engineer(d: pd.DataFrame) -> pd.DataFrame:
    """Agro-climatic response terms (identical SQL in bigquery/02_bqml_yield_model.sql)."""
    rainfed = 1 - (d["irrigation_coverage_pct"].astype(float).fillna(50) / 100).clip(0, 1)
    kharif = (d["season"] == "Kharif").astype(float)
    ra = d["season_rain_anom_pct"].astype(float).fillna(0).clip(-80, 150) / 100
    tx = d["season_tmax_anom_c"].astype(float).fillna(0)
    sens = d["crop"].map(TMAX_SENS).fillna(-0.03)
    return pd.DataFrame({
        "rain_deficit_x_rainfed": np.minimum(ra, 0) * rainfed,
        "rain_surplus_x_rainfed": np.clip(ra, 0, 0.3) * rainfed,
        "rain_excess_x_rainfed": np.maximum(ra - 0.5, 0) * rainfed,
        "tmax_anom_c_x_sensitivity": tx * sens,
        "wheat_terminal_heat_days": d["heat_days_anom"].astype(float).fillna(0) * (d["crop"] == "wheat"),
        "kharif_dry_spell14_x_rainfed": (d["dry_spell_days"].astype(float).fillna(0) >= 14) * rainfed * kharif,
        "extreme_rain_days": d["extreme_rain_days"].astype(float).fillna(0).clip(0, 10),
    }, index=d.index)


def prior_index(d: pd.DataFrame) -> np.ndarray:
    e = engineer(d)
    return sum(e[k].values * v for k, v in TERMS.items())


def _ape(y, p, w):
    return float(np.average(np.abs(p / y - 1), weights=w) * 100)


def _fit_lambda(x, y, w):
    y = y - np.average(y, weights=w)
    return float(np.clip(np.sum(w * x * y) / np.sum(w * x * x), 0.25, 1.5))


def train() -> dict:
    """Calibrate one shrinkage factor (lambda) on the literature-prior response using 2001-2019 district data.
    Free-form learners (HistGradientBoosting, unconstrained Huber regression) were tested first; both lost to
    persistence on the 2020-24 hold-out because district-level year-to-year yield noise dominates the climate
    signal, so the deployed model keeps physically-signed priors and only learns their overall strength."""
    df = pd.read_csv(os.path.join(settings.data_dir, "yield_training.csv"))
    df = df[~df.crop.isin(EXCLUDE)].dropna(subset=["yield_ratio"])
    t = np.log(df.yield_ratio).values
    x = prior_index(df)
    w = np.sqrt(df.area_ha.values)
    tr, te = (df.agri_year <= 2019).values, (df.agri_year >= 2020).values
    lam = _fit_lambda(x[tr], t[tr], w[tr])
    y, base, aw = df.yield_kg_ha.values, df.trailing5_yield_kg_ha.values, df.area_ha.values
    shock = te & (((df.season == "Kharif") & (df.season_rain_anom_pct < -25)) | (df.season_tmax_anom_c > 1.0)).values
    metrics = {
        "model": "Literature-prior agro-climatic response, lambda-calibrated on 2001-2019 district yields",
        "holdout_years": "2020-21..2024-25", "train_rows": int(tr.sum()), "test_rows": int(te.sum()),
        "lambda_train": round(lam, 3),
        "wmape_pct_model": round(_ape(y[te], base[te] * np.exp(lam * x[te]), aw[te]), 2),
        "wmape_pct_persistence": round(_ape(y[te], base[te], aw[te]), 2),
        "climate_shock_rows": int(shock.sum()),
        "wmape_pct_model_climate_shocks": round(_ape(y[shock], base[shock] * np.exp(lam * x[shock]), aw[shock]), 2),
        "wmape_pct_persistence_climate_shocks": round(_ape(y[shock], base[shock], aw[shock]), 2),
        "rejected_alternatives": ["HistGradientBoostingRegressor (hold-out WMAPE worse than persistence)",
                                  "Unconstrained Huber regression (sign-unstable coefficients)"],
        "note": "District-level year-to-year yields are dominated by non-climate noise; the model is used for "
                "scenario stress-testing (direction and magnitude of climate shocks), not point forecasting.",
    }
    lam_all = _fit_lambda(x, t, w)
    metrics["lambda_final"] = round(lam_all, 3)
    metrics["effective_coefficients"] = {k: round(v * lam_all, 4) for k, v in TERMS.items()}
    os.makedirs(settings.model_dir, exist_ok=True)
    joblib.dump({"lambda": lam_all, "trend": _trends(df)}, MODEL_PATH)
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)
    return metrics


def _trends(df: pd.DataFrame) -> dict:
    """State x crop x season annual yield growth (log-linear 2005-2024), capped to [-1%, +2%]/yr."""
    out = {}
    d = df[df.agri_year >= 2005]
    for (st, c, s), g in d.groupby(["state_name", "crop", "season"]):
        a = (g.yield_kg_ha * g.area_ha).groupby(g.agri_year).sum() / g.area_ha.groupby(g.agri_year).sum()
        if len(a) >= 8:
            slope = np.polyfit(a.index.values, np.log(a.values), 1)[0]
            out[f"{st}|{c}|{s}"] = float(np.clip(slope, -0.01, 0.02))
    return out


@dataclass
class YieldModel:
    lam: float
    trend: dict

    @classmethod
    def load(cls) -> "YieldModel":
        if not os.path.exists(MODEL_PATH):
            train()
        b = joblib.load(MODEL_PATH)
        return cls(b["lambda"], b["trend"])

    def climate_factor(self, rows: pd.DataFrame) -> np.ndarray:
        return np.clip(np.exp(self.lam * prior_index(rows)), 0.4, 1.3)

    def explain(self, row: dict) -> dict:
        e = engineer(pd.DataFrame([row])).iloc[0]
        return {k: round(float(np.exp(self.lam * TERMS[k] * e[k]) - 1) * 100, 2) for k in TERMS if e[k] != 0}

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        """yield = baseline(2020-24 mean, centred on 2022) x trend^(t-2022) x climate factor."""
        g = np.array([self.trend.get(f"{s}|{c}|{se}", 0.005)
                      for s, c, se in zip(rows.state_name, rows.crop, rows.season)])
        return rows.baseline_yield_kg_ha.values * np.exp(g * (rows.agri_year.values - 2022)) * self.climate_factor(rows)


def metrics() -> dict:
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH) as f:
            return json.load(f)
    return {}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        print(json.dumps(train(), indent=2))
