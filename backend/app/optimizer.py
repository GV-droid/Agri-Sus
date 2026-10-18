"""Agri Sus national crop-allocation optimizer (Google OR-Tools linear program, COIN-OR CLP).

Decision variables  area[d,c,s,t]  for every district in India x candidate crops x {Kharif, Rabi} x plan years
                    (2027..2030 by default; 2 to 8 years from 2027), linked into one glide path:
                    area[t] = baseline + (t-2026)/H x (area[final] - baseline), so each district moves steadily
                    towards its final-year crop mix and never switches back and forth.
Calibration         PMP-style: farmers' revealed preference makes today's crop mix their own optimum. Expanding a
                    crop above its baseline is valued at most at the district-season mean margin (rho), shrinking a
                    crop costs at least rho - so the LP cannot "discover" profit that farmers are leaving on the
                    table, and every move away from the status quo must be paid for by a public benefit.
Hard constraints    season land (+ rice-fallow use by pulses/oilseeds; <= 3% may be left fallow), year-on-year
                    flexibility bounds, max annual land shift k%, CWAR > threshold blocks expansion, national crop
                    growth cap, farmer welfare floor per district-year (calibrated income + incentive >= floor) with
                    incentives limited by an annual budget.
Soft constraints    national cereal self-sufficiency floor, rice & wheat SSR, district groundwater draft caps
                    (ramped to target), legume-share ramp, final-year import-substitution target - penalised slacks so
                    every scenario is solvable and violations are reported instead of silently failing.
Objective (Rs cr)   farmer welfare + strategic premium on avoided imports + soil N credit - groundwater cost
                    - (MCPF-1) x incentives - transition costs - slacks
Decision traces     duals (shadow prices) of binding constraints and reduced costs of rejected crops.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from ortools.linear_solver import pywraplp

from .data import PLAN_YEARS, ClimateShock, _price_table, gw_calibration, plan_features, plan_years, store
from .trade import alternatives as trade_alternatives, resolve_trade_cut

CR = 1e7  # Rs per crore
LP_ENGINE = os.getenv("LP_ENGINE", "CLP")  # COIN-OR CLP via OR-Tools (4x faster than GLOP at national scale)
DEMAND_GROWTH = {"rice": 0.008, "wheat": 0.012, "maize": 0.035, "pulses": 0.02, "edible_oils": 0.025}
LEGUME_FAMILIES = {"Fabaceae"}
FALLOW_CROPS_FAMILIES = {"Fabaceae", "Brassicaceae", "Pedaliaceae", "Asteraceae"}
TRANSITION_COST_INR_HA = 1_500  # extension, seed & learning cost per ha switched
WATER_VALUE_INR_HAM = 25_000  # pumping energy + depletion externality per ha-m (~Rs 2.5/m3)
# slack penalties in Rs crore: per kt cereal shortfall, per ha-m over GW cap, per $bn import-target miss, per ha legume gap
PEN = {"cereal": 50.0, "gw": 0.2, "import": 20000.0, "legume": 2e-4}
MCPF = 1.25  # marginal cost of public funds: Rs 1 of incentive costs society Rs 1.25 (distortionary taxation)
# Behavioural realism (Day's recursive-programming "flexibility constraints" + PMP calibration), applied year-on-year
# along the glide path:
FLEX_UP = 0.20  # a district's area under a crop can grow by <= 20% of today's area a year ...
FLEX_NEW_SHARE = 0.01  # ... plus 1% of the season's cropped area (lets crops with little or no area enter slowly)
FLEX_DOWN = {"rice": 0.15, "wheat": 0.15}  # water-intensive staples can shrink <= 15% of last year's area a year
FLEX_DOWN_DEFAULT = 0.05  # other crops (millets, maize, soybean, pulses...) <= 5% a year - no millet collapse
NATIONAL_GROWTH = 0.05  # national area of any crop grows <= 5% of today's area a year (seeds, procurement, processing)
ADOPTION_PREMIUM = 0.15  # risk premium farmers require on expanded area: 15% of max(rho, Rs 10k/ha) per year
ADOPTION_PREMIUM_FLOOR_INR = 10_000

# ---- policy instruments (all drawn from one annual budget; the LP chooses the cheapest mix per district)
INSTRUMENTS = {
    "dbt": "Income-parity transfer (DBT per farmer, MPMV-style Rs/acre)",
    "seed_kits": "Seed & FPO kits (certified seed minikit, bio-inputs, FPO aggregation)",
    "price_support": "Assured price on diversified area (price-deficiency payment up to MSP)",
}
KIT_COST_INR_HA = 4_000  # certified seed + rhizobium/PSB + micronutrients + FPO aggregation support, per ha per season
KIT_YIELD_UPLIFT = 0.10  # quality seed + package of practices: +10% yield (NFSM cluster demonstrations report 20-40%)
PDP_ADMIN_SHARE = 0.03  # administrative cost of a price-deficiency payment (share of the payout)
PDP_RISK_RELIEF = 0.5  # an assured floor price removes half of the adoption risk premium on covered area
# ---- fiscal ledger (returns to the exchequer, Rs)
POWER_SUBSIDY_INR_M3 = 1.5  # ~0.3 kWh per m3 pumped x ~Rs 5/kWh unrecovered farm-power cost
UREA_SUBSIDY_INR_T = 32_000  # FY2024-25 urea subsidy ~Rs 1.19 lakh cr / ~37 Mt urea sold
UREA_N_SHARE = 0.46
# least-cost mode: Rs cr of objective per Rs cr of outlay (dominates every other use of money) and a near-hard
# import target (Rs cr per $mn short), i.e. "reach the target if at all possible, spending as little as possible"
MIN_BUDGET_COST = 50.0
PEN_IMPORT_MIN_BUDGET = 10_000.0
# MROPI = public return (forex + power + urea subsidy saved) per Rs of outlay. Calibrated on the baseline run: most
# pulse/oilseed districts return > 10x on small outlays (forex dominates); the costly water-driven rice shifts of the
# Punjab-Haryana belt return < 1x in cash terms (their payoff is the aquifer, which this ledger does not price).
MROPI_TIERS = ((10.0, "Tier 1"), (1.0, "Tier 2"), (0.0, "Tier 3"))


@dataclass
class SimParams:
    scenario: str = "baseline"
    max_shift_pct: float = 12.0
    cereal_floor_pct: float = 101.5
    gw_mode: str = "cap80"  # status_quo | cap80 (OE/critical <= 80% of recharge) | zero_depletion (all <= 70%)
    import_target_pct: float = 25.0  # % cut in 2030 pulses+edible-oil import bill vs status-quo 2030
    incentive_budget_cr: float = 6000.0  # Rs crore per year for income-parity transfers
    income_floor_pct: float = 100.0
    rice_export_retention_pct: float = 75.0  # keep this share of 2024-25 rice exports (20.2 Mt) feasible
    legume_ramp_pp: float = 1.5  # pp of net sown area per year
    fallow_use_pct: float = 20.0  # share of current fallows usable by rabi pulses/oilseeds by 2030
    price_realisation: float = 0.9  # non-procured crops realise this share of MSP
    crop_growth_cap_pct: float = 100 * NATIONAL_GROWTH  # seed/procurement/processing scale-up: national crop area growth %/yr
    import_price_mult: dict = field(default_factory=lambda: {"pulses": 1.0, "edible_oils": 1.0})
    shocks: list = field(default_factory=list)
    w_farmer: float = 1.0
    # strategic self-reliance premium as a share of the landed import value (FX exposure + world-price volatility);
    # the commodity value itself is already in the farmer margin, so 1.0 would double count it
    w_import: float = 0.25
    w_soil: float = 1.0
    w_water: float = 1.0
    states: list = field(default_factory=list)  # restrict reallocation to these states (others frozen)
    budget_mode: str = "fixed"  # fixed: spend <= incentive_budget_cr | min_budget: least outlay that meets the target
    instruments: list = field(default_factory=lambda: list(INSTRUMENTS))  # enabled policy instruments
    # bilateral supply shocks / decoupling goals: [{"country": "Indonesia", "product": "palm oil", "pct": 30}]
    trade_cuts: list = field(default_factory=list)
    horizon_years: int = len(PLAN_YEARS)  # plan length from 2027 (2-8); targets and caps apply to the final year


def _years_of(f: pd.DataFrame) -> list:
    """The plan years present in a feature frame (the run's horizon)."""
    return sorted(int(y) for y in pd.unique(f.agri_year))


def _demand(cons: float, key: str, year: int) -> float:
    return cons * (1 + DEMAND_GROWTH.get(key, 0.01)) ** (year - 2024)


def _macro(ds):
    m = ds["national_macro_constraints"]
    cereals = m[m.commodity_category == "Cereal"].set_index("crop_name")
    groups = m[m.commodity.isin(["pulses", "edible_oils"])].groupby("commodity").first()
    return cereals, groups


def _commodity_output(f: pd.DataFrame) -> pd.Series:
    """kt of tradeable commodity per ha (pulse grain, edible oil, cereal grain)."""
    rec = store()["crop_agronomy"].set_index("crop").product_recovery
    r = np.where(f.category == "Edible oilseed", rec.reindex(f.crop).values, 1.0)
    return f.yield_kg_ha * r / 1e6


def evaluate(f: pd.DataFrame, area: np.ndarray, p: SimParams, calib: pd.Series,
             kit_cov: np.ndarray | None = None) -> dict:
    """KPIs for an area allocation (used for both the status quo and the optimised plan). `kit_cov` = ha per row
    covered by seed & FPO kits, whose yield uplift adds commodity output."""
    ds = store()
    cereals, groups = _macro(ds)
    out_kt = _commodity_output(f) * area
    if kit_cov is not None:
        out_kt = out_kt + KIT_YIELD_UPLIFT * _commodity_output(f) * kit_cov
    f = f.assign(area=area, out_kt=out_kt)
    out = {}
    ctx = ds.context.set_index("district_code")
    years = _years_of(f)
    for y in years:
        g = f[f.agri_year == y]
        base = f[f.agri_year == years[0]]
        k = {}
        # national cereal balance (districts without crop data scaled by the modelled mean climate factor)
        prod_c = {}
        for c in cereals.index:
            gc, bc = g[g.crop == c], base[base.crop == c]
            mvp_base = (bc.baseline_area_ha * bc.baseline_yield_kg_ha).sum() / 1e9
            cf = np.average(gc.climate_factor, weights=gc.baseline_area_ha + 1e-9) if len(gc) else 1.0
            prod_c[c] = max(cereals.loc[c, "baseline_domestic_prod_mt"] - mvp_base, 0) * cf + gc.out_kt.sum() / 1e3
        cons_c = {c: _demand(cereals.loc[c, "baseline_consumption_mt"], c, y) for c in cereals.index}
        k["cereal_ssr_pct"] = 100 * sum(prod_c.values()) / sum(cons_c.values())
        k["rice_ssr_pct"] = 100 * prod_c["rice"] / cons_c["rice"]
        k["wheat_ssr_pct"] = 100 * prod_c["wheat"] / cons_c["wheat"]
        k["rice_exportable_surplus_mt"] = prod_c["rice"] - cons_c["rice"]
        bill = 0.0
        for grp, row in groups.iterrows():
            crops = ds["national_macro_constraints"].query("commodity == @grp").crop_name
            gg, bb = g[g.crop.isin(crops)], base[base.crop.isin(crops)]
            rec = ds["crop_agronomy"].set_index("crop").product_recovery
            mvp_base = (bb.baseline_area_ha * bb.baseline_yield_kg_ha * np.where(
                bb.category == "Edible oilseed", rec.reindex(bb.crop).values, 1)).sum() / 1e9
            cf = np.average(gg.climate_factor, weights=gg.baseline_area_ha + 1e-9)
            prod = max(row.baseline_domestic_prod_mt - mvp_base, 0) * cf + gg.out_kt.sum() / 1e3
            cons = _demand(row.baseline_consumption_mt, grp, y)
            imp = max(cons - prod + row.baseline_exports_mt, 0)
            price = row.import_landed_price_usd_t * p.import_price_mult.get(grp, 1.0)
            k[f"{grp}_ssr_pct"] = 100 * prod / cons
            k[f"{grp}_imports_mt"] = imp
            k[f"{grp}_import_bill_usd_bn"] = imp * price / 1e3
            bill += imp * price / 1e3
        k["import_bill_usd_bn"] = bill
        draft = (g.area * g.gw_draft_ham_per_ha * g.district_code.map(calib)).groupby(g.district_code).sum()
        k["gw_irrigation_draft_bcm"] = draft.sum() / 1e5  # 1 BCM = 1e5 ha-m
        cx = ctx.reindex(draft.index)
        other = cx.cgwb_extraction_total_ham - cx.cgwb_extraction_irrigation_ham
        stage = 100 * (other + draft) / cx.cgwb_extractable_resource_ham
        k["districts_over_exploited"] = int((stage > 100).sum())
        k["farmer_margin_cr"] = (g.area * g.net_margin_inr_ha).sum() / CR
        k["n_credit_kt"] = (g.area * g.n_credit_kg_ha).sum() / 1e6
        k["legume_area_mha"] = g.loc[g.family.isin(LEGUME_FAMILIES), "area"].sum() / 1e6
        k["rice_area_mha"] = g.loc[g.crop == "rice", "area"].sum() / 1e6
        k["district_stage"] = stage.round(1).to_dict()
        # absolute sustainability indicators (same yardstick for "do nothing" and the plan)
        w_draft = (g.baseline_area_ha.fillna(0) * g.gw_draft_ham_per_ha * g.district_code.map(calib)).groupby(
            g.district_code).sum().reindex(stage.index)
        ws = water_score(stage)
        ok = ws.notna() & (w_draft > 0)
        k["water_index"] = float(np.average(ws[ok], weights=w_draft[ok])) if ok.any() else 70.0
        k["legume_share_gca_pct"] = 100 * k["legume_area_mha"] * 1e6 / max(g.area.sum(), 1.0)
        ca = g.groupby(["district_code", "crop"]).area.sum()
        tot_d = ca.groupby(level=0).transform("sum")
        hhi = ((ca / tot_d.where(tot_d > 0)) ** 2).groupby(level=0).sum(min_count=1).dropna()
        div = ((1 - hhi) / 0.8 * 100).clip(0, 100)
        wts = g.groupby("district_code").baseline_area_ha.sum().reindex(div.index).fillna(0)
        k["diversity_index"] = float(np.average(div, weights=wts)) if wts.sum() > 0 else 0.0
        out[y] = k
    return out


# Agri Sustainability & Self-reliance Index: absolute 0-100 pillars, identical yardstick for every scenario,
# so "do nothing" and the plan are directly comparable (no relative re-basing, no saturation at the status quo).
ASSI_WEIGHTS = {"self_reliance": 0.30, "water": 0.30, "soil": 0.20, "diversity": 0.20}
SOIL_TARGET_LEGUME_SHARE_PCT = 33.3  # one legume in a three-crop rotation = full soil-health score


def water_score(stage):
    """0-100 from the CGWB stage of extraction: <=60% -> 100, 100% (over-exploited line) -> 71, >=200% -> 0."""
    if isinstance(stage, pd.Series):
        return (100 - (stage.astype(float) - 60) * 100 / 140).clip(0, 100)
    return None if not _finite(stage) else float(np.clip(100 - (float(stage) - 60) * 100 / 140, 0, 100))


def assi_pillars(k: dict) -> dict:
    """Pillar scores (0-100) of the ASSI for one year's national allocation."""
    raw = {
        "self_reliance": (min(k["pulses_ssr_pct"], 100) + min(k["edible_oils_ssr_pct"], 100)) / 2,
        "water": k["water_index"],
        "soil": 100 * k["legume_share_gca_pct"] / SOIL_TARGET_LEGUME_SHARE_PCT,
        "diversity": k["diversity_index"],
    }
    return {key: round(float(np.clip(v, 0, 100)), 1) for key, v in raw.items()}


def assi(k: dict) -> float:
    """ASSI 0-100 = 30% self-reliance (pulses & edible-oil SSR) + 30% groundwater health (draft-weighted CGWB
    stage) + 20% soil (legume share of cropped area vs 1-in-3 rotation) + 20% crop diversity (1 - HHI)."""
    p = assi_pillars(k)
    return round(float(sum(ASSI_WEIGHTS[key] * v for key, v in p.items())), 1)


# district sustainability score (0-100, absolute; farmer income is a guardrail, not a scored pillar)
DSS_WEIGHTS = {"water": 0.40, "soil": 0.30, "diversity": 0.30}


def district_score(stage, legume_share_gca, hhi) -> dict:
    ws = water_score(stage)
    water = 70.0 if ws is None else ws  # no CGWB assessment -> neutral, identical in both views
    soil = 0.0 if legume_share_gca is None or pd.isna(legume_share_gca) else float(
        np.clip(100 * legume_share_gca / SOIL_TARGET_LEGUME_SHARE_PCT, 0, 100))
    div = 0.0 if hhi is None or pd.isna(hhi) else float(np.clip((1 - hhi) / 0.8 * 100, 0, 100))
    p = {"water": water, "soil": soil, "diversity": div}
    return {"score": round(sum(DSS_WEIGHTS[k] * v for k, v in p.items()), 1), **{k: round(v, 1) for k, v in p.items()}}


def _hhi(a: pd.Series) -> float | None:
    t = a.sum()
    return float(((a / t) ** 2).sum()) if t > 0 else None


def stress_tests(years: list) -> dict:
    """Unplanned shocks placed relative to the plan (2nd year, 3rd year, last two years): 2028, 2029 and 2029-30 for
    the default 2027-30 plan."""
    y1, y2, ya, yb = years[min(1, len(years) - 1)], years[min(2, len(years) - 1)], years[-2], years[-1]
    return {
        f"el_nino_{y1}": (f"El Nino drought {y1} (-18% monsoon)", [ClimateShock(y1, kharif_rain_pct=-18, kharif_tmax_c=0.8)]),
        f"heatwave_{y2}": (f"Rabi heatwave {y2} (+2.2 C)", [ClimateShock(y2, rabi_tmax_c=2.2)]),
        f"monsoon_fail_{yb}": (f"Back-to-back weak monsoon {ya}-{str(yb)[2:]} (-12%)",
                               [ClimateShock(ya, kharif_rain_pct=-12), ClimateShock(yb, kharif_rain_pct=-12)]),
    }


STRESS_TESTS = stress_tests(PLAN_YEARS)


def robustness(f: pd.DataFrame, area: np.ndarray, base: np.ndarray, p: SimParams, shocks: list, calib,
               kit_cov: np.ndarray | None = None) -> list:
    """Re-score the *fixed* plan (no re-optimisation) under unplanned climate shocks: does it still beat the
    status quo exposed to the same shock?"""
    out = []
    years = _years_of(f)
    for key, (label, extra) in stress_tests(years).items():
        g = plan_features(shocks + extra, p.price_realisation, years=years).reset_index(drop=True)
        if len(g) != len(f):
            continue
        g["gw_k"] = f.gw_k.values
        pl, s0 = evaluate(g, area, p, calib, kit_cov), evaluate(g, base, p, calib)
        yrs = [{"year": y, "assi": assi(pl[y]), "assi_status_quo": assi(s0[y]),
                "cereal_ssr_pct": round(pl[y]["cereal_ssr_pct"], 2),
                "import_bill_usd_bn": round(pl[y]["import_bill_usd_bn"], 3)} for y in years]
        out.append({"id": key, "label": label, "years": yrs,
                    "min_assi": min(r["assi"] for r in yrs),
                    "plan_beats_status_quo": all(r["assi"] >= r["assi_status_quo"] for r in yrs)})
    return out


def _finite(v) -> bool:
    try:
        return v is not None and bool(np.isfinite(float(v)))
    except (TypeError, ValueError):
        return False


def _json_safe(x):
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    return x


def simulate(p: SimParams, incentive_extra: np.ndarray | None = None) -> dict:
    t0 = time.time()
    ds = store()
    ctx = ds.context.set_index("district_code")
    cst = ds.constants
    shocks = [s if isinstance(s, ClimateShock) else ClimateShock(**s) for s in p.shocks]
    PLAN_YEARS = plan_years(p.horizon_years)  # noqa: N806 - this run's horizon (2-8 years from 2027)
    f = plan_features(shocks, p.price_realisation, years=PLAN_YEARS).reset_index(drop=True)
    calib = gw_calibration(f)
    f["gw_k"] = f.district_code.map(calib).values
    f["out_kt_ha"] = _commodity_output(f)
    n = len(f)
    base = f.baseline_area_ha.fillna(0).values
    movable = (f.optimizer_crop.values == 1)
    if p.states:
        movable &= f.state_name.isin(p.states).values
    season_area = f.groupby(["district_code", "season", "agri_year"]).baseline_area_ha.transform("sum").values
    expand_ok = f.cwar.values <= cst["cwar_threshold"]
    # One coherent transition pathway (glide path): the LP chooses every district's 2030 crop mix and each plan year
    # moves an equal step towards it (25/50/75/100%), so no farmer is asked to switch and then switch back. Every
    # year of the path must still satisfy that year's climate, land, water, food-security and income constraints.
    # Year-on-year flexibility along the path: the first step may add at most FLEX_UP of today's area (+ a new-crop
    # allowance), the last (relatively largest) step may cut at most FLEX_DOWN of the previous year's area. A crop
    # that is water-infeasible (CWAR) in any plan year cannot expand above its baseline at all.
    nY = len(PLAN_YEARS)
    yi = f.agri_year.map({y: j + 1 for j, y in enumerate(PLAN_YEARS)}).values.astype(float)
    lam = yi / nY
    down = f.crop.map(FLEX_DOWN).fillna(FLEX_DOWN_DEFAULT).values.astype(float)
    cap_T = np.minimum(np.maximum(base * 2.5, base + 0.30 * season_area),
                       base * (1 + nY * FLEX_UP) + nY * FLEX_NEW_SHARE * season_area)
    lo_T = base * (1 - down) / (1 + (nY - 1) * down)
    cap = np.where(expand_ok, base + lam * (cap_T - base), base)
    cap = np.where(movable, np.maximum(cap, base), base)
    lo = np.where(movable, base - lam * (base - lo_T), base)

    solver = pywraplp.Solver.CreateSolver(LP_ENGINE) or pywraplp.Solver.CreateSolver("GLOP")
    inf = solver.infinity()
    A = [solver.NumVar(lo[i], max(cap[i], lo[i]), "") for i in range(n)]
    obj = solver.Objective()
    obj.SetMaximization()

    # objective: farmer margin + soil N credit - water (import bill handled via national balance vars)
    for i in range(n):
        coef = (p.w_farmer * f.net_margin_inr_ha.iat[i] + p.w_soil * f.n_value_inr_ha.iat[i]
                - p.w_water * WATER_VALUE_INR_HAM * f.gw_draft_ham_per_ha.iat[i] * f.gw_k.iat[i]) / CR
        obj.SetCoefficient(A[i], float(coef))

    # glide-path links: area[t] = baseline + lam_t x (area[2030] - baseline) for every movable district-crop-season
    chains = {}  # 2030 row -> its rows for every plan year (2027..2030)
    for rows in f.groupby(["district_code", "crop", "season"]).indices.values():
        rows = sorted(rows, key=lambda i: yi[i])
        iT = rows[-1]
        if not movable[iT]:
            continue
        chains[iT] = rows
        for i in rows[:-1]:
            rhs = float((1 - lam[i]) * base[i])
            cl = solver.Constraint(rhs, rhs)
            cl.SetCoefficient(A[i], 1)
            cl.SetCoefficient(A[iT], -float(lam[i]))

    # PMP-style calibration to revealed preference. rho = area-weighted mean margin of the movable crops in the
    # district-season-year. Area above baseline earns at most rho (h = m - rho > 0 is "hidden" cost: risk, market
    # access, missing procurement), area below baseline loses at least rho (g = rho - m > 0 is hidden value of the
    # incumbent crop). Net private value of any move is therefore <= 0: the status quo is the farmers' optimum.
    keys = ["district_code", "season", "agri_year"]
    bm = np.where(movable, base, 0.0)
    num = pd.Series(bm * f.net_margin_inr_ha.values).groupby([f[k] for k in keys]).transform("sum").values
    den = pd.Series(bm).groupby([f[k] for k in keys]).transform("sum").values
    rho = np.where(den > 0, num / np.where(den > 0, den, 1), f.net_margin_inr_ha.values)
    m_arr = f.net_margin_inr_ha.values.astype(float)
    premium = ADOPTION_PREMIUM * np.maximum(rho, ADOPTION_PREMIUM_FLOOR_INR) + TRANSITION_COST_INR_HA / len(PLAN_YEARS)
    h_arr, g_arr = np.maximum(m_arr - rho, 0.0) + premium, np.maximum(rho - m_arr, 0.0)
    U, Dn, folded = {}, {}, np.zeros(n, dtype=bool)
    for i in range(n):
        if not movable[i]:
            continue
        if cap[i] > base[i] + 1e-6:
            if base[i] <= 0:  # new crop for the district: u == A, fold the hidden cost into A's coefficient
                obj.SetCoefficient(A[i], obj.GetCoefficient(A[i]) - p.w_farmer * h_arr[i] / CR)
                folded[i] = True
            else:
                u = solver.NumVar(0, inf, "")
                cu = solver.Constraint(-inf, float(base[i]))  # A - u <= base  ->  u >= A - base
                cu.SetCoefficient(A[i], 1)
                cu.SetCoefficient(u, -1)
                obj.SetCoefficient(u, -p.w_farmer * h_arr[i] / CR)
                U[i] = u
        if g_arr[i] > 1 and base[i] > 0:
            dv = solver.NumVar(0, float(base[i]), "")
            cd = solver.Constraint(float(base[i]), inf)  # A + d >= base  ->  d >= base - A
            cd.SetCoefficient(A[i], 1)
            cd.SetCoefficient(dv, 1)
            obj.SetCoefficient(dv, -p.w_farmer * g_arr[i] / CR)
            Dn[i] = dv

    # land, abandonment, cumulative shift limit, fallow
    cons_land, cons_shift = {}, {}
    fallow_frac = {y: p.fallow_use_pct / 100 * (j + 1) / len(PLAN_YEARS) for j, y in enumerate(PLAN_YEARS)}
    for (d, s, y), rows in f.groupby(["district_code", "season", "agri_year"]).indices.items():
        tot = base[rows].sum()
        if tot <= 0:
            continue
        fu = None
        if s == "Rabi":
            fallow = float(ctx.at[d, "current_fallow_ha"]) if d in ctx.index else 0.0
            fu = solver.NumVar(0, max(fallow, 0) * fallow_frac[y], "")
            c_fu = solver.Constraint(-inf, 0)
            c_fu.SetCoefficient(fu, 1)
            for i in rows:
                if f.family.iat[i] in FALLOW_CROPS_FAMILIES and movable[i]:
                    c_fu.SetCoefficient(A[i], -1)
        c = solver.Constraint(0.97 * tot, tot)
        for i in rows:
            c.SetCoefficient(A[i], 1)
        if fu is not None:
            c.SetCoefficient(fu, -1)
        cons_land[(d, s, y)] = c
        # land moved into new crops (area above baseline, excluding fallows brought into use) <= k% a year
        cs = solver.Constraint(-inf, p.max_shift_pct / 100 * tot * (PLAN_YEARS.index(y) + 1))
        n_terms = 0
        for i in rows:
            if i in U:
                cs.SetCoefficient(U[i], 1)
                n_terms += 1
            elif folded[i]:
                cs.SetCoefficient(A[i], 1)
                n_terms += 1
        if fu is not None:
            cs.SetCoefficient(fu, -1)
        if n_terms:
            cons_shift[(d, s, y)] = cs

    # national balances
    cereals, groups = _macro(ds)
    base0 = f[f.agri_year == PLAN_YEARS[0]]
    cons_cereal, cons_rw, imp_vars, cons_import, bal_c = {}, {}, {}, None, {}
    rec = ds["crop_agronomy"].set_index("crop").product_recovery
    for y in PLAN_YEARS:
        g = f[f.agri_year == y]
        rest_c, cons_c = {}, {}
        for c in cereals.index:
            gc, bc = g[g.crop == c], base0[base0.crop == c]
            mvp_base = (bc.baseline_area_ha * bc.baseline_yield_kg_ha).sum() / 1e6  # kt
            cf = np.average(gc.climate_factor, weights=gc.baseline_area_ha + 1e-9) if len(gc) else 1.0
            rest_c[c] = max(cereals.loc[c, "baseline_domestic_prod_mt"] * 1e3 - mvp_base, 0) * cf
            cons_c[c] = _demand(cereals.loc[c, "baseline_consumption_mt"], c, y) * 1e3
        sl = solver.NumVar(0, inf, "")
        obj.SetCoefficient(sl, -PEN["cereal"])
        rhs = p.cereal_floor_pct / 100 * sum(cons_c.values()) - sum(rest_c.values())
        cc = solver.Constraint(rhs, inf)
        cc.SetCoefficient(sl, 1)
        for i in g.index[g.crop.isin(cereals.index)]:
            cc.SetCoefficient(A[i], float(f.out_kt_ha.iat[i]))
        cons_cereal[y] = cc
        rice_need = cons_c["rice"] + p.rice_export_retention_pct / 100 * cereals.loc["rice", "baseline_exports_mt"] * 1e3
        for c, need in (("rice", rice_need), ("wheat", 0.97 * cons_c["wheat"])):
            sl2 = solver.NumVar(0, inf, "")
            obj.SetCoefficient(sl2, -PEN["cereal"])
            c2 = solver.Constraint(need - rest_c[c], inf)
            c2.SetCoefficient(sl2, 1)
            for i in g.index[g.crop == c]:
                c2.SetCoefficient(A[i], float(f.out_kt_ha.iat[i]))
            cons_rw[(c, y)] = c2
        # pulses / edible oils: imports - surplus = demand - production + exports
        for grp, row in groups.iterrows():
            crops = ds["national_macro_constraints"].query("commodity == @grp").crop_name
            gg, bb = g[g.crop.isin(crops)], base0[base0.crop.isin(crops)]
            r_b = np.where(bb.category == "Edible oilseed", rec.reindex(bb.crop).values, 1)
            mvp_base = (bb.baseline_area_ha * bb.baseline_yield_kg_ha * r_b).sum() / 1e6
            cf = np.average(gg.climate_factor, weights=gg.baseline_area_ha + 1e-9)
            rest = max(row.baseline_domestic_prod_mt * 1e3 - mvp_base, 0) * cf
            cons = _demand(row.baseline_consumption_mt, grp, y) * 1e3
            imp, sur = solver.NumVar(0, inf, ""), solver.NumVar(0, inf, "")
            price_cr_per_kt = row.import_landed_price_usd_t * p.import_price_mult.get(grp, 1.0) * 1e3 * cst["usd_inr"] / CR
            obj.SetCoefficient(imp, -p.w_import * price_cr_per_kt)
            cb = solver.Constraint(cons - rest + row.baseline_exports_mt * 1e3, cons - rest + row.baseline_exports_mt * 1e3)
            cb.SetCoefficient(imp, 1)
            cb.SetCoefficient(sur, -1)
            for i in gg.index:
                cb.SetCoefficient(A[i], float(f.out_kt_ha.iat[i]))
            imp_vars[(grp, y)] = (imp, row.import_landed_price_usd_t * p.import_price_mult.get(grp, 1.0))
            bal_c[(grp, y)] = cb

    # policy instruments on diversified pulse/oilseed area (coverage follows the glide path: lam_t x 2030 coverage,
    # and never exceeds the area actually added: K <= A_2030 - baseline). Kits: Rs KIT_COST in kind + yield uplift
    # (more output -> fewer imports). Price support: tops the farm-gate price up to MSP and removes part of the
    # adoption risk premium. Both draw on the same annual budget as income-parity transfers.
    grp_of = {c: g for g, cs in ds["national_macro_constraints"].groupby("commodity").crop_name for c in cs
              if g in ("pulses", "edible_oils")}
    use_kit = "seed_kits" in p.instruments
    use_pdp = "price_support" in p.instruments and p.price_realisation < 0.999
    msp_row = _price_table(ds).reindex(f.crop).values.astype(float)
    yk = f.yield_kg_ha.values.astype(float)
    gap_arr = np.where(f.category.isin(["Pulse", "Edible oilseed"]).values,
                       np.maximum(np.nan_to_num(msp_row) - f.price_inr_qtl.values, 0) * yk / 100, 0.0)
    uplift_arr = KIT_YIELD_UPLIFT * yk * f.price_inr_qtl.values / 100
    relief_arr = PDP_RISK_RELIEF * ADOPTION_PREMIUM * np.maximum(rho, ADOPTION_PREMIUM_FLOOR_INR)
    KIT, PDP = {}, {}
    for iT, rows in chains.items():
        if f.category.iat[iT] not in ("Pulse", "Edible oilseed") or not (iT in U or folded[iT]):
            continue
        for kind, on in (("kit", use_kit), ("pdp", use_pdp and gap_arr[iT] > 1)):
            if not on:
                continue
            v = solver.NumVar(0, inf, "")
            c = solver.Constraint(-inf, 0.0 if folded[iT] else -float(base[iT]))  # v <= A_2030 - baseline
            c.SetCoefficient(v, 1)
            c.SetCoefficient(A[iT], -1)
            if kind == "kit":
                coef = sum(lam[i] * (p.w_farmer * (KIT_COST_INR_HA + uplift_arr[i]) - MCPF * KIT_COST_INR_HA) for i in rows)
                for i in rows:
                    grp = grp_of.get(f.crop.iat[i])
                    if grp and (grp, int(f.agri_year.iat[i])) in bal_c:
                        bal_c[(grp, int(f.agri_year.iat[i]))].SetCoefficient(
                            v, float(lam[i] * KIT_YIELD_UPLIFT * f.out_kt_ha.iat[i]))
                KIT[iT] = v
            else:
                coef = sum(lam[i] * (p.w_farmer * (gap_arr[i] + relief_arr[i]) - MCPF * gap_arr[i] * (1 + PDP_ADMIN_SHARE))
                           for i in rows)
                PDP[iT] = v
            obj.SetCoefficient(v, float(coef / CR))

    # national absorption limits: area of each crop grows by <= crop_growth_cap_pct of today's area a year (seed,
    # procurement, processing). Along the straight glide path the 2030 limit implies every earlier year's.
    cons_nat = {}
    for (c_, y), rows in f.groupby(["crop", "agri_year"]).indices.items():
        b = float(base[rows].sum())
        if y != PLAN_YEARS[-1] or b <= 0 or not movable[rows].any():
            continue
        cn = solver.Constraint(-inf, b * (1 + p.crop_growth_cap_pct / 100 * nY))
        for i in rows:
            cn.SetCoefficient(A[i], 1)
        cons_nat[(c_, y)] = cn

    # status-quo evaluation (same climate & prices) for targets and income floor
    sq = evaluate(f, base, p, calib)
    yT = PLAN_YEARS[-1]
    min_budget = p.budget_mode == "min_budget"
    pen_import_mn = PEN_IMPORT_MIN_BUDGET if min_budget else PEN["import"] / 1e3  # Rs cr per $mn short
    # import-substitution target ramped linearly to the 2030 goal (avoids a cliff-edge transition in the final year)
    cons_imp_y, sl_imp_y, target_y = {}, {}, {}
    for j, y in enumerate(PLAN_YEARS):
        target_bn = sq[y]["import_bill_usd_bn"] * (1 - p.import_target_pct / 100 * (j + 1) / len(PLAN_YEARS))
        target_y[y] = target_bn
        sl = solver.NumVar(0, inf, "")  # $mn short of the target
        obj.SetCoefficient(sl, -pen_import_mn)
        ct = solver.Constraint(-inf, target_bn * 1e3)  # in $mn
        ct.SetCoefficient(sl, -1)
        for grp in groups.index:
            v, price = imp_vars[(grp, y)]
            ct.SetCoefficient(v, price / 1e3)  # kt * $/t = $k -> /1e3 = $mn
        cons_imp_y[y], sl_imp_y[y] = ct, sl
    cons_import, sl_imp = cons_imp_y[yT], sl_imp_y[yT]

    # bilateral trade shocks: imports of the group must fall by the volume bought from that country (ramped to 2030),
    # i.e. domestic production replaces it. Soft (reported shortfall) so impossible asks stay solvable.
    trade = [resolve_trade_cut(c) for c in p.trade_cuts]
    vol_grp = {}
    for t_ in trade:
        vol_grp[t_["group"]] = vol_grp.get(t_["group"], 0.0) + t_["volume_kt"]
    cons_trade = {}
    for grp, vol in vol_grp.items():
        for j, y in enumerate(PLAN_YEARS):
            req = vol * (j + 1) / len(PLAN_YEARS)
            imp, price = imp_vars[(grp, y)]
            slt = solver.NumVar(0, inf, "")
            obj.SetCoefficient(slt, -pen_import_mn * price / 1e3)  # per kt short
            ct = solver.Constraint(-inf, sq[y][f"{grp}_imports_mt"] * 1e3 - req)
            ct.SetCoefficient(imp, 1)
            ct.SetCoefficient(slt, -1)
            cons_trade[(grp, y)] = (ct, slt, req)

    # groundwater caps per district-year (ramped)
    cons_gw = {}
    for d, rows_d in f.groupby("district_code").indices.items():
        if d not in ctx.index:
            continue
        draft_sq = (base[rows_d] * f.gw_draft_ham_per_ha.values[rows_d] * f.gw_k.values[rows_d])
        cat = str(ctx.at[d, "cgwb_category"])
        other = ctx.at[d, "cgwb_extraction_total_ham"] - ctx.at[d, "cgwb_extraction_irrigation_ham"]
        extractable = ctx.at[d, "cgwb_extractable_resource_ham"]
        for j, y in enumerate(PLAN_YEARS):
            rows = [i for i in rows_d if f.agri_year.iat[i] == y]
            sq_y = float(sum(base[i] * f.gw_draft_ham_per_ha.iat[i] * f.gw_k.iat[i] for i in rows))
            if sq_y <= 0:
                continue
            if p.gw_mode == "zero_depletion" and pd.notna(extractable):
                # every district back to CGWB 'Safe' (stage of extraction <= 70%) by 2030
                target = min(sq_y, max(0.70 * extractable - other, 0))
            elif p.gw_mode == "cap80" and cat in ("Over-Exploited", "Critical") and pd.notna(extractable):
                target = min(sq_y, max(cst["gw_recharge_cap_share"] * extractable - other, 0))
            else:
                target = sq_y
            capy = sq_y + (target - sq_y) * (j + 1) / len(PLAN_YEARS)
            slg = solver.NumVar(0, inf, "")
            obj.SetCoefficient(slg, -PEN["gw"])
            cg = solver.Constraint(-inf, capy)
            cg.SetCoefficient(slg, -1)
            for i in rows:
                cg.SetCoefficient(A[i], float(f.gw_draft_ham_per_ha.iat[i] * f.gw_k.iat[i]))
            cons_gw[(d, y)] = (cg, capy, sq_y, slg)
        _ = draft_sq

    # legume ramp (Fabaceae share of NSA) per district-year
    cons_leg = {}
    for (d, y), rows in f.groupby(["district_code", "agri_year"]).indices.items():
        nsa = float(ctx.at[d, "net_sown_area_ha"]) if d in ctx.index else 0
        legs = [i for i in rows if f.family.iat[i] in LEGUME_FAMILIES]
        if nsa <= 0 or not legs:
            continue
        j = PLAN_YEARS.index(y)
        need = base[legs].sum() + p.legume_ramp_pp / 100 * (j + 1) * nsa
        sll = solver.NumVar(0, inf, "")
        obj.SetCoefficient(sll, -PEN["legume"])
        if not any(movable[i] for i in legs):
            continue
        cl = solver.Constraint(need - sum(base[i] for i in legs if not movable[i]), inf)
        cl.SetCoefficient(sll, 1)
        for i in legs:
            if movable[i]:
                cl.SetCoefficient(A[i], 1)
        cons_leg[(d, y)] = (cl, sll)

    # farmer welfare floor with incentives & budget: calibrated income (margin - hidden costs) + incentive >= floor
    # in every district-year. Incentives are transfers: society pays MCPF per rupee, farmers value it at w_farmer.
    # Fixed mode: outlay <= budget each year. Least-cost mode: the budget B_y is a decision variable, minimised.
    cons_inc, budget_c, I, B = {}, {}, {}, {}
    for y in PLAN_YEARS:
        if min_budget:
            B[y] = solver.NumVar(0, inf, "")
            obj.SetCoefficient(B[y], -MIN_BUDGET_COST)
            budget_c[y] = solver.Constraint(-inf, 0.0)
            budget_c[y].SetCoefficient(B[y], -1)
        else:
            budget_c[y] = solver.Constraint(-inf, p.incentive_budget_cr)
    use_dbt = "dbt" in p.instruments
    for (d, y), rows in f.groupby(["district_code", "agri_year"]).indices.items():
        sq_m = float((base[rows] * f.net_margin_inr_ha.values[rows]).sum()) / CR
        inc = solver.NumVar(0, inf if use_dbt else 0.0, "")
        obj.SetCoefficient(inc, p.w_farmer - MCPF)
        budget_c[y].SetCoefficient(inc, 1)
        floor = p.income_floor_pct / 100 * sq_m if sq_m > 0 else sq_m * (2 - p.income_floor_pct / 100)
        ci = solver.Constraint(floor, inf)
        ci.SetCoefficient(inc, 1)
        for i in rows:
            ci.SetCoefficient(A[i], float((m_arr[i] - (h_arr[i] if folded[i] else 0.0)) / CR))
            if i in U:
                ci.SetCoefficient(U[i], float(-h_arr[i] / CR))
            if i in Dn:
                ci.SetCoefficient(Dn[i], float(-g_arr[i] / CR))
        cons_inc[(d, y)] = ci
        I[(d, y)] = inc

    # instruments: the in-kind / price benefit counts towards each district-year's income floor and its cost
    # towards that year's budget
    for iT, v in list(KIT.items()) + list(PDP.items()):
        is_kit = iT in KIT and KIT[iT] is v
        for i in chains[iT]:
            y, d = int(f.agri_year.iat[i]), int(f.district_code.iat[i])
            if is_kit:
                benefit, cost = KIT_COST_INR_HA + uplift_arr[i], KIT_COST_INR_HA
            else:
                benefit, cost = gap_arr[i] + relief_arr[i], gap_arr[i] * (1 + PDP_ADMIN_SHARE)
            cons_inc[(d, y)].SetCoefficient(v, float(lam[i] * benefit / CR))
            budget_c[y].SetCoefficient(v, float(lam[i] * cost / CR))

    status = solver.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError(f"solver status {status}")
    area = np.array([v.solution_value() for v in A])
    f["plan_area_ha"] = area
    hidden = np.zeros(n)
    exp_rows = np.array(sorted(set(U) | set(np.flatnonzero(folded))), dtype=int)
    if len(exp_rows):
        hidden[exp_rows] += h_arr[exp_rows] * np.maximum(area[exp_rows] - base[exp_rows], 0.0)
    con_rows = np.array(sorted(Dn), dtype=int)
    if len(con_rows):
        hidden[con_rows] += g_arr[con_rows] * np.maximum(base[con_rows] - area[con_rows], 0.0)
    f["hidden_cost_inr"] = hidden  # calibrated (unobserved) private cost of the move, Rs
    f["reduced_cost_cr_per_kha"] = [v.reduced_cost() * 1e3 for v in A]
    # shadow price of the district groundwater cap carried by each row (Rs/ha): when a cap binds or is breached, every
    # hectare that pumps groundwater is charged at the cap's dual, which is most of the LP reduced cost there
    gw_dual = {key: abs(v[0].dual_value()) for key, v in cons_gw.items()}
    f["gw_shadow_inr_ha"] = [gw_dual.get((f.district_code.iat[i], f.agri_year.iat[i]), 0.0) * f.gw_draft_ham_per_ha.iat[i]
                             * f.gw_k.iat[i] * CR for i in range(n)]
    gw_cap_state = {key: ("above cap" if v[3].solution_value() > 1 else "at cap" if gw_dual[key] > 1e-9
                          else "below cap" if v[1] < v[2] - 1e-6 else "no cap") for key, v in cons_gw.items()}
    # instrument coverage (ha) per row and its cost / farmer benefit (Rs)
    kit_cov, pdp_cov = np.zeros(n), np.zeros(n)
    for iT, v in KIT.items():
        val = max(v.solution_value(), 0.0)
        for i in chains[iT]:
            kit_cov[i] = lam[i] * val
    for iT, v in PDP.items():
        val = max(v.solution_value(), 0.0)
        for i in chains[iT]:
            pdp_cov[i] = lam[i] * val
    f["kit_ha"], f["price_support_ha"] = kit_cov, pdp_cov
    f["kit_cost_inr"] = kit_cov * KIT_COST_INR_HA
    f["price_support_cost_inr"] = pdp_cov * gap_arr * (1 + PDP_ADMIN_SHARE)
    f["instrument_benefit_inr"] = kit_cov * (KIT_COST_INR_HA + uplift_arr) + pdp_cov * (gap_arr + relief_arr)
    f["kit_extra_out_kt"] = KIT_YIELD_UPLIFT * f.out_kt_ha.values * kit_cov
    plan = evaluate(f, area, p, calib, kit_cov)
    usd_inr = cst["usd_inr"]
    landed = {grp: float(row.import_landed_price_usd_t * p.import_price_mult.get(grp, 1.0)) for grp, row in groups.iterrows()}
    f["import_value_saved_cr"] = [
        ((area[i] - base[i]) * f.out_kt_ha.iat[i] + f.kit_extra_out_kt.iat[i]) * 1e3 * landed[grp_of[f.crop.iat[i]]] * usd_inr / CR
        if f.crop.iat[i] in grp_of else 0.0 for i in range(n)]

    # ---- KPIs
    years = []
    by_dy = f.groupby(["district_code", "agri_year"]).indices
    ym = f.agri_year.values
    inc_dy = {key: v.solution_value() for key, v in I.items()}
    kit_c, pdp_c, ben = f.kit_cost_inr.values, f.price_support_cost_inr.values, f.instrument_benefit_inr.values
    kit_dy = {key: float(kit_c[rows].sum()) / CR for key, rows in by_dy.items()}
    pdp_dy = {key: float(pdp_c[rows].sum()) / CR for key, rows in by_dy.items()}
    ben_dy = {key: float(ben[rows].sum()) / CR for key, rows in by_dy.items()}
    for y in PLAN_YEARS:
        k, s0 = plan[y], sq[y]
        dbt_y = sum(v for (d, yy), v in inc_dy.items() if yy == y)
        kit_y = sum(v for (d, yy), v in kit_dy.items() if yy == y)
        pdp_y = sum(v for (d, yy), v in pdp_dy.items() if yy == y)
        ben_y = sum(v for (d, yy), v in ben_dy.items() if yy == y)
        incent = dbt_y + kit_y + pdp_y  # public outlay
        hid = float(hidden[ym == y].sum()) / CR
        welfare = k["farmer_margin_cr"] - hid
        # districts whose calibrated income + incentive falls below the floor (should be 0: it is a hard constraint)
        below = 0
        for (d, yy), rows in by_dy.items():
            if yy != y:
                continue
            sq_m = float((base[rows] * m_arr[rows]).sum()) / CR
            got = float((area[rows] * m_arr[rows]).sum() - hidden[rows].sum()) / CR + inc_dy[(d, yy)] + ben_dy[(d, yy)]
            if sq_m > 0 and got < p.income_floor_pct / 100 * sq_m - 0.01:
                below += 1
        imp_saving_bn = s0["import_bill_usd_bn"] - k["import_bill_usd_bn"]
        water_saved = s0["gw_irrigation_draft_bcm"] - k["gw_irrigation_draft_bcm"]
        urea_saved_kt = (k["n_credit_kt"] - s0["n_credit_kt"]) / UREA_N_SHARE
        forex_cr = imp_saving_bn * usd_inr * 100  # $bn -> Rs cr
        power_cr = water_saved * 1e9 * POWER_SUBSIDY_INR_M3 / CR
        urea_cr = urea_saved_kt * 1e3 * UREA_SUBSIDY_INR_T / CR
        years.append({
            "year": y, "assi": assi(k), "assi_status_quo": assi(s0),
            "assi_pillars": assi_pillars(k), "assi_pillars_status_quo": assi_pillars(s0),
            "water_index": round(k["water_index"], 1), "water_index_status_quo": round(s0["water_index"], 1),
            "legume_share_gca_pct": round(k["legume_share_gca_pct"], 2),
            "legume_share_gca_status_quo_pct": round(s0["legume_share_gca_pct"], 2),
            "diversity_index": round(k["diversity_index"], 1), "diversity_index_status_quo": round(s0["diversity_index"], 1),
            "farmer_income_vs_today_pct": round(100 * (welfare + dbt_y + ben_y) / s0["farmer_margin_cr"], 2),
            "farmer_welfare_change_cr": round(welfare - s0["farmer_margin_cr"], 0),
            "farmer_accounting_margin_change_cr": round(k["farmer_margin_cr"] - s0["farmer_margin_cr"], 0),
            "districts_below_income_floor": below,
            "guardrail_income_ok": below == 0,
            "guardrail_cereal_ok": bool(k["cereal_ssr_pct"] >= p.cereal_floor_pct - 0.05),
            "import_bill_usd_bn": round(k["import_bill_usd_bn"], 3),
            "import_bill_status_quo_usd_bn": round(s0["import_bill_usd_bn"], 3),
            "import_saving_usd_bn": round(imp_saving_bn, 3),
            "import_target_usd_bn": round(target_y[y], 3),
            "import_target_shortfall_usd_bn": round(max(0.0, k["import_bill_usd_bn"] - target_y[y]), 3),
            "pulses_ssr_pct": round(k["pulses_ssr_pct"], 2), "pulses_ssr_status_quo_pct": round(s0["pulses_ssr_pct"], 2),
            "edible_oils_ssr_pct": round(k["edible_oils_ssr_pct"], 2),
            "edible_oils_ssr_status_quo_pct": round(s0["edible_oils_ssr_pct"], 2),
            "pulses_imports_mt": round(k["pulses_imports_mt"], 3),
            "pulses_imports_status_quo_mt": round(s0["pulses_imports_mt"], 3),
            "edible_oils_imports_mt": round(k["edible_oils_imports_mt"], 3),
            "edible_oils_imports_status_quo_mt": round(s0["edible_oils_imports_mt"], 3),
            "cereal_ssr_pct": round(k["cereal_ssr_pct"], 2), "cereal_ssr_status_quo_pct": round(s0["cereal_ssr_pct"], 2),
            "rice_ssr_pct": round(k["rice_ssr_pct"], 2), "wheat_ssr_pct": round(k["wheat_ssr_pct"], 2),
            "rice_exportable_surplus_mt": round(k["rice_exportable_surplus_mt"], 2),
            "rice_exportable_surplus_status_quo_mt": round(s0["rice_exportable_surplus_mt"], 2),
            "gw_draft_bcm": round(k["gw_irrigation_draft_bcm"], 3),
            "gw_draft_status_quo_bcm": round(s0["gw_irrigation_draft_bcm"], 3),
            "water_saved_bcm": round(water_saved, 3),
            "districts_over_exploited": k["districts_over_exploited"],
            "districts_over_exploited_status_quo": s0["districts_over_exploited"],
            "farmer_margin_cr": round(k["farmer_margin_cr"], 0),
            "farmer_margin_status_quo_cr": round(s0["farmer_margin_cr"], 0),
            "incentive_cr": round(incent, 0),
            "budget_cr": round(B[y].solution_value(), 0) if min_budget else p.incentive_budget_cr,
            "outlay_by_instrument_cr": {"dbt": round(dbt_y, 1), "seed_kits": round(kit_y, 1),
                                        "price_support": round(pdp_y, 1)},
            "farmer_benefit_from_instruments_cr": round(dbt_y + ben_y, 0),
            "kit_area_mha": round(float(kit_cov[ym == y].sum()) / 1e6, 3),
            "price_support_area_mha": round(float(pdp_cov[ym == y].sum()) / 1e6, 3),
            "fiscal_ledger": {
                "outlay_cr": round(incent, 0),
                "forex_saved_cr": round(forex_cr, 0),
                "power_subsidy_saved_cr": round(power_cr, 0),
                "urea_subsidy_saved_cr": round(urea_cr, 0),
                "fiscal_return_cr": round(power_cr + urea_cr, 0),
                "fiscal_roi": round((power_cr + urea_cr) / incent, 2) if incent > 1 else None,
                "public_return_ratio": round((forex_cr + power_cr + urea_cr) / incent, 2) if incent > 1 else None,
            },
            "n_credit_kt": round(k["n_credit_kt"], 1), "n_credit_status_quo_kt": round(s0["n_credit_kt"], 1),
            "urea_saved_kt": round(urea_saved_kt, 1),
            "legume_area_mha": round(k["legume_area_mha"], 3), "rice_area_mha": round(k["rice_area_mha"], 3),
            "rice_area_status_quo_mha": round(s0["rice_area_mha"], 3),
            "area_shifted_mha": round(float(np.abs(area - base)[f.agri_year.values == y].sum() / 2e6), 3),
        })

    # ---- binding constraints (shadow prices)
    binding = []
    for y, c in cons_cereal.items():
        if abs(c.dual_value()) > 1e-9:
            binding.append({"constraint": f"National cereal SSR floor {p.cereal_floor_pct}% ({y})", "year": y,
                            "shadow_price": f"Rs {abs(c.dual_value()) * 1e3:,.2f} cr per Mt of cereal"})
    for (c_, y), c in cons_rw.items():
        if abs(c.dual_value()) > 1e-9:
            binding.append({"constraint": (f"Rice: domestic demand + {p.rice_export_retention_pct:.0f}% of exports ({y})" if c_ == "rice" else f"Wheat 97% self-sufficiency floor ({y})"), "year": y,
                            "shadow_price": f"Rs {abs(c.dual_value()) * 1e3:,.2f} cr per Mt"})
    for y, c in budget_c.items():
        if abs(c.dual_value()) > 1e-9 and not min_budget:
            binding.append({"constraint": f"Incentive budget Rs {p.incentive_budget_cr:,.0f} cr ({y})", "year": y,
                            "shadow_price": f"Rs {abs(c.dual_value()):.2f} of objective per extra Rs 1 of budget"})
    if abs(cons_import.dual_value()) > 1e-9:
        binding.append({"constraint": f"2030 import-substitution target {p.import_target_pct}%", "year": yT,
                        "shadow_price": f"Rs {abs(cons_import.dual_value()):.2f} cr per $mn"})
    gw_viol = [(d, y, v[3].solution_value()) for (d, y), v in cons_gw.items() if v[3].solution_value() > 1]
    n_shift_binding = sum(1 for c in cons_shift.values() if abs(c.dual_value()) > 1e-9)
    n_gw_binding = sum(1 for (c, *_rest) in cons_gw.values() if abs(c.dual_value()) > 1e-9)
    n_cwar_blocked = int(((f.cwar > cst["cwar_threshold"]) & movable & (f.agri_year == PLAN_YEARS[0])).sum())
    slack_import = sl_imp.solution_value() / 1e3  # $bn
    kT = years[-1]
    # bilateral trade shocks: required vs achieved import cut and the districts / crops that replace the volume
    trade_out = None
    if trade:
        groups_out = []
        for grp, vol in vol_grp.items():
            yrs = []
            for y in PLAN_YEARS:
                ct, slt, req = cons_trade[(grp, y)]
                cut = (sq[y][f"{grp}_imports_mt"] - plan[y][f"{grp}_imports_mt"]) * 1e3
                yrs.append({"year": y, "required_cut_kt": round(req, 1), "achieved_cut_kt": round(cut, 1),
                            "shortfall_kt": round(max(slt.solution_value(), 0.0), 1)})
            gT = f[(f.agri_year == yT) & f.crop.map(grp_of).eq(grp)]
            add_kt = (gT.plan_area_ha - gT.baseline_area_ha) * gT.out_kt_ha + gT.kit_extra_out_kt
            by_crop = add_kt.groupby(gT.crop).sum().sort_values(ascending=False)
            by_dist = add_kt.groupby(gT.district_code).sum().sort_values(ascending=False)
            groups_out.append({
                "group": grp, "volume_kt": round(vol, 1), "years": yrs, "met": yrs[-1]["shortfall_kt"] < 1,
                "substitute_crops": [{"crop": c, "extra_kt": round(float(v), 1)} for c, v in by_crop.items() if v > 0.05][:8],
                "substitute_districts": [{"district_code": int(d_), "district_name": str(ctx.at[d_, "district_name"]),
                                          "state_name": str(ctx.at[d_, "state_name"]), "extra_kt": round(float(v), 2)}
                                         for d_, v in by_dist.head(10).items() if v > 0.01],
            })
        cut_countries = {c["country"] for c in trade}
        trade_out = {"cuts": [dict(c, alternatives=trade_alternatives(c, cut_countries)) for c in trade],
                     "groups": groups_out}
    lever_status = _lever_status(p, years, cons_cereal, cons_rw, budget_c, cons_imp_y, slack_import, cons_shift,
                                 cons_nat, cons_leg, n_gw_binding, len(gw_viol), yT, trade_out)

    # ---- per-district results
    dist = []
    for d, g in f.groupby("district_code"):
        rec_d = {"district_code": int(d), "district_name": str(ctx.at[d, "district_name"]) if d in ctx.index else "",
                 "state_name": str(g.state_name.iat[0]), "years": {}}
        for y in PLAN_YEARS:
            gy = g[g.agri_year == y]
            st = plan[y]["district_stage"].get(d)
            st0 = sq[y]["district_stage"].get(d)
            leg = gy.loc[gy.family.isin(LEGUME_FAMILIES), "plan_area_ha"].sum()
            leg0 = gy.loc[gy.family.isin(LEGUME_FAMILIES), "baseline_area_ha"].sum()
            gca, gca0 = gy.plan_area_ha.sum(), gy.baseline_area_ha.sum()
            rice, rice0 = gy.loc[gy.crop == "rice", "plan_area_ha"].sum(), gy.loc[gy.crop == "rice", "baseline_area_ha"].sum()
            m_plan = float((gy.plan_area_ha * gy.net_margin_inr_ha).sum() / CR)
            m_sq = float((gy.baseline_area_ha * gy.net_margin_inr_ha).sum() / CR)
            hid_d = float(gy.hidden_cost_inr.sum() / CR)
            inc_d = I[(d, y)].solution_value() if (d, y) in I else 0.0
            kit_d, pdp_d, ben_d = kit_dy.get((d, y), 0.0), pdp_dy.get((d, y), 0.0), ben_dy.get((d, y), 0.0)
            outlay_d = inc_d + kit_d + pdp_d
            water_mcm = float(((gy.baseline_area_ha - gy.plan_area_ha) * gy.gw_draft_ham_per_ha * gy.gw_k).sum() / 100)
            imp_cr = float(gy.import_value_saved_cr.sum())
            power_d = water_mcm * 1e6 * POWER_SUBSIDY_INR_M3 / CR
            urea_d = float(((gy.plan_area_ha - gy.baseline_area_ha) * gy.n_credit_kg_ha).sum()) / UREA_N_SHARE / 1e3 \
                * UREA_SUBSIDY_INR_T / CR
            ret_d = imp_cr + power_d + urea_d
            mropi = ret_d / outlay_d if outlay_d >= 0.01 else None
            if mropi is None:
                tier = "Self-financing" if ret_d > 0.01 else "No outlay"
            else:
                tier = next(lbl for thr, lbl in MROPI_TIERS if mropi >= thr or thr == 0.0)
            ls = 100 * leg / gca if gca > 0 else None
            ls0 = 100 * leg0 / gca0 if gca0 > 0 else None
            sc = district_score(st, ls, _hhi(gy.groupby("crop").plan_area_ha.sum()))
            sc0 = district_score(st0, ls0, _hhi(gy.groupby("crop").baseline_area_ha.sum()))
            rec_d["years"][y] = {
                "gw_stage_pct": float(st) if _finite(st) else None,
                "gw_stage_status_quo_pct": float(st0) if _finite(st0) else None,
                "legume_share_pct": round(ls, 2) if ls is not None else None,
                "legume_share_status_quo_pct": round(ls0, 2) if ls0 is not None else None,
                "rice_share_pct": round(100 * rice / gca, 2) if gca > 0 else None,
                "rice_share_status_quo_pct": round(100 * rice0 / gca0, 2) if gca0 > 0 else None,
                "rice_area_change_pct": round(100 * (rice / rice0 - 1), 2) if rice0 > 0 else None,
                "margin_change_pct": round(100 * (m_plan / m_sq - 1), 2) if m_sq > 0 else None,
                "income_vs_today_pct": round(100 * (m_plan - hid_d + inc_d + ben_d) / m_sq, 2) if m_sq > 0 else None,
                "incentive_cr": round(outlay_d, 2),
                "outlay_cr": {"dbt": round(inc_d, 2), "seed_kits": round(kit_d, 2), "price_support": round(pdp_d, 2)},
                "kit_ha": round(float(gy.kit_ha.sum()), 0), "price_support_ha": round(float(gy.price_support_ha.sum()), 0),
                "returns_cr": {"forex": round(imp_cr, 2), "power_subsidy": round(power_d, 2), "urea_subsidy": round(urea_d, 2)},
                "mropi": round(mropi, 2) if mropi is not None else None, "mropi_tier": tier,
                "area_shifted_ha": round(float(np.abs(gy.plan_area_ha - gy.baseline_area_ha).sum() / 2), 0),
                "sustainability_score": sc["score"], "sustainability_score_status_quo": sc0["score"],
                "score_pillars": {k: v for k, v in sc.items() if k != "score"},
                "score_pillars_status_quo": {k: v for k, v in sc0.items() if k != "score"},
                "water_assessed": _finite(st0),
                "gw_cap": gw_cap_state.get((d, y)),
                "water_saved_mcm": round(water_mcm, 2),
                "rice_area_change_ha": round(float(rice - rice0), 0),
                "legume_area_change_ha": round(float(leg - leg0), 0),
            }
        dist.append(rec_d)

    years = [{k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in row.items()} for row in years]

    # ---- state roll-up (plan year 2030)
    states = {}
    for rec_d in dist:
        y = rec_d["years"][yT]
        s_ = states.setdefault(rec_d["state_name"], {"state_name": rec_d["state_name"], "districts": 0, "score": 0.0,
                                                    "score_status_quo": 0.0, "water_saved_mcm": 0.0,
                                                    "rice_area_change_ha": 0.0, "legume_area_change_ha": 0.0,
                                                    "incentive_cr": 0.0, "area_shifted_ha": 0.0, "returns_cr": 0.0,
                                                    "outlay_cr": {"dbt": 0.0, "seed_kits": 0.0, "price_support": 0.0}})
        s_["districts"] += 1
        s_["score"] += y["sustainability_score"]
        s_["score_status_quo"] += y["sustainability_score_status_quo"]
        for kk in ("water_saved_mcm", "rice_area_change_ha", "legume_area_change_ha", "incentive_cr", "area_shifted_ha"):
            s_[kk] += y[kk] or 0
        for kk, v in y["outlay_cr"].items():
            s_["outlay_cr"][kk] += v
        s_["returns_cr"] += sum(y["returns_cr"].values())
    state_rows = []
    for s_ in states.values():
        n_ = max(s_["districts"], 1)
        s_["score"] = round(s_["score"] / n_, 1)
        s_["score_status_quo"] = round(s_["score_status_quo"] / n_, 1)
        s_["score_gain"] = round(s_["score"] - s_["score_status_quo"], 1)
        for kk in ("water_saved_mcm", "rice_area_change_ha", "legume_area_change_ha", "incentive_cr", "area_shifted_ha",
                   "returns_cr"):
            s_[kk] = round(s_[kk], 1)
        s_["outlay_cr"] = {kk: round(v, 1) for kk, v in s_["outlay_cr"].items()}
        s_["mropi"] = round(s_["returns_cr"] / s_["incentive_cr"], 2) if s_["incentive_cr"] >= 0.5 else None
        state_rows.append(s_)
    state_rows.sort(key=lambda r: -r["score_gain"])
    stress = robustness(f, area, base, p, shocks, calib, kit_cov)
    tiers = {}
    for rec_d in dist:
        t_ = rec_d["years"][yT]
        e = tiers.setdefault(t_["mropi_tier"], {"tier": t_["mropi_tier"], "districts": 0, "outlay_cr": 0.0, "returns_cr": 0.0})
        e["districts"] += 1
        e["outlay_cr"] += t_["incentive_cr"]
        e["returns_cr"] += sum(t_["returns_cr"].values())
    tier_order = ["Tier 1", "Tier 2", "Tier 3", "Self-financing", "No outlay"]
    # whole-plan totals: what the policy costs and returns over every plan year, not just the final one
    led = [r["fiscal_ledger"] for r in years]
    t_out = float(sum(r["incentive_cr"] for r in years))
    t_fx, t_pw, t_ur = (float(sum(l_[k_] for l_ in led)) for k_ in
                        ("forex_saved_cr", "power_subsidy_saved_cr", "urea_subsidy_saved_cr"))
    totals = {
        "years": len(years), "first_year": years[0]["year"], "last_year": years[-1]["year"],
        "budget_cr": round(float(sum(r["budget_cr"] for r in years)), 0), "outlay_cr": round(t_out, 0),
        "by_instrument_cr": {k_: round(float(sum(r["outlay_by_instrument_cr"][k_] for r in years)), 0)
                             for k_ in INSTRUMENTS},
        "forex_saved_cr": round(t_fx, 0), "power_subsidy_saved_cr": round(t_pw, 0),
        "urea_subsidy_saved_cr": round(t_ur, 0), "returns_cr": round(t_fx + t_pw + t_ur, 0),
        "public_return_ratio": round((t_fx + t_pw + t_ur) / t_out, 2) if t_out > 1 else None,
        "fiscal_roi": round((t_pw + t_ur) / t_out, 2) if t_out > 1 else None,
    }
    policy_mix = {
        "mode": p.budget_mode,
        "instruments": [{"id": k_, "label": INSTRUMENTS[k_], "enabled": k_ in p.instruments,
                         "outlay_2030_cr": kT["outlay_by_instrument_cr"][k_]} for k_ in INSTRUMENTS],
        "budget_path_cr": [{"year": r["year"], "budget_cr": r["budget_cr"], "outlay_cr": r["incentive_cr"],
                            **{k_: r["outlay_by_instrument_cr"][k_] for k_ in INSTRUMENTS}} for r in years],
        "totals": totals,
        "min_budget_cr": kT["incentive_cr"] if min_budget else None,
        "target_met": slack_import < 0.005,
        "import_target_shortfall_usd_bn": round(slack_import, 3),
        "assumptions": {"kit_cost_inr_ha": KIT_COST_INR_HA, "kit_yield_uplift_pct": 100 * KIT_YIELD_UPLIFT,
                        "price_support_admin_pct": 100 * PDP_ADMIN_SHARE,
                        "price_support_risk_relief_pct": 100 * PDP_RISK_RELIEF, "mcpf": MCPF,
                        "power_subsidy_inr_m3": POWER_SUBSIDY_INR_M3, "urea_subsidy_inr_t": UREA_SUBSIDY_INR_T,
                        "usd_inr": usd_inr},
        "mropi_tiers": [dict(tiers[t_], outlay_cr=round(tiers[t_]["outlay_cr"], 1),
                             returns_cr=round(tiers[t_]["returns_cr"], 1)) for t_ in tier_order if t_ in tiers],
        "mropi_thresholds": {"Tier 1": f">= {MROPI_TIERS[0][0]:g}x", "Tier 2": f"{MROPI_TIERS[1][0]:g}-{MROPI_TIERS[0][0]:g}x",
                             "Tier 3": f"< {MROPI_TIERS[1][0]:g}x"},
    }
    return _json_safe({
        "params": {k: v for k, v in asdict(p).items() if k != "shocks"} | {
            "shocks": [asdict(s) for s in shocks]},
        "solver": {"engine": f"OR-Tools · {'COIN-OR CLP' if LP_ENGINE == 'CLP' else LP_ENGINE}", "status": "OPTIMAL" if status == 0 else "FEASIBLE",
                   "variables": solver.NumVariables(), "constraints": solver.NumConstraints(),
                   "objective_cr": round(solver.Objective().Value(), 1), "seconds": round(time.time() - t0, 2)},
        "plan_years": list(PLAN_YEARS),
        "kpis": years,
        "binding_constraints": binding,
        "lever_status": lever_status,
        "constraint_summary": {
            "district_shift_limits_binding": n_shift_binding, "district_gw_caps_binding": n_gw_binding,
            "district_gw_caps_violated": len(gw_viol), "crop_district_expansions_blocked_by_cwar": n_cwar_blocked,
            "import_target_shortfall_usd_bn": round(slack_import, 3),
        },
        "districts": dist,
        "states": state_rows,
        "robustness": stress,
        "policy_mix": policy_mix,
        "trade": trade_out,
        "assi_weights": ASSI_WEIGHTS,
        "district_score_weights": DSS_WEIGHTS,
        "_frame": f,
    })


def _lever_status(p, years, cons_cereal, cons_rw, budget_c, cons_imp_y, slack_import, cons_shift, cons_nat, cons_leg,
                  n_gw_binding, n_gw_viol, yT, trade_out=None) -> dict:
    """Is each policy lever limiting the optimal plan? Read from LP duals and slacks, so the UI can explain why moving
    a lever does (or does not) change the result."""
    tol = 1e-7
    k = {r["year"]: r for r in years}[yT]
    out = {}
    mix = k["outlay_by_instrument_cr"]
    if p.budget_mode == "min_budget":
        out["incentive_budget_cr"] = ("inactive", "Ignored in least-cost mode: the optimizer sets the budget itself")
        if p.import_target_pct <= 0:
            out["budget_mode"] = ("active", "Least-cost mode needs an import target above 0%")
        elif slack_import > 0.005:
            out["budget_mode"] = ("missed", f"Target unreachable within the guardrails: ${slack_import:.2f} bn short even "
                                            f"with Rs {k['incentive_cr']:,.0f} cr in {yT}")
        else:
            out["budget_mode"] = ("binding", f"Least cost to meet the target: Rs {k['incentive_cr']:,.0f} cr in {yT}")
    else:
        out["budget_mode"] = ("active", f"Fixed budget of Rs {p.incentive_budget_cr:,.0f} cr a year")
        if any(abs(c.dual_value()) > tol for c in budget_c.values()) or k["incentive_cr"] >= 0.995 * p.incentive_budget_cr:
            out["incentive_budget_cr"] = ("binding", f"Fully used (Rs {k['incentive_cr']:,.0f} cr in {yT}) - the main bottleneck: "
                                                     "more budget lets more farmers switch at income parity")
        else:
            out["incentive_budget_cr"] = ("slack", f"Only Rs {k['incentive_cr']:,.0f} cr of Rs {p.incentive_budget_cr:,.0f} cr needed")
    used = [f"{lbl} Rs {mix[key]:,.0f} cr" for key, lbl in (("dbt", "transfers"), ("seed_kits", "kits"),
                                                            ("price_support", "price support")) if key in p.instruments]
    out["instruments"] = ("active", f"{yT} mix: " + ", ".join(used) if used else "No instrument enabled: nobody can be "
                                                                                  "compensated, so little land moves")
    if trade_out:
        notes, state = [], "binding"
        for g in trade_out["groups"]:
            last, name = g["years"][-1], g["group"].replace("_", " ")
            if not g["met"]:
                state = "missed"
                notes.append(f"{name}: {last['shortfall_kt']:,.0f} kt short of the {last['required_cut_kt']:,.0f} kt to replace")
            elif last["achieved_cut_kt"] > last["required_cut_kt"] + 5:
                notes.append(f"{name}: covered anyway - the plan cuts {last['achieved_cut_kt']:,.0f} kt "
                             f"(needs {last['required_cut_kt']:,.0f} kt)")
            else:
                notes.append(f"{name}: {last['required_cut_kt']:,.0f} kt replaced by domestic output in {yT}")
        if state != "missed" and all("covered anyway" in s for s in notes):
            state = "slack"
        out["trade_cuts"] = (state, "; ".join(notes))
    n_sh = sum(1 for (d, s_, y), c in cons_shift.items() if abs(c.dual_value()) > tol)
    out["max_shift_pct"] = (("binding", f"Limits {n_sh} district-season-years") if n_sh else
                            ("slack", "Not limiting: farmer adoption limits and the budget bind first"))
    cer = any(abs(c.dual_value()) > tol for c in cons_cereal.values())
    out["cereal_floor_pct"] = (("binding", f"Binding: cereal self-sufficiency held at the {p.cereal_floor_pct}% floor") if cer else
                               ("slack", f"Not limiting: cereal self-sufficiency stays at {k['cereal_ssr_pct']:.1f}%"))
    rb = any(abs(c.dual_value()) > tol for (c_, y), c in cons_rw.items() if c_ == "rice")
    out["rice_export_retention_pct"] = (("binding", "Binding: paddy kept to protect rice exports") if rb else
                                        ("slack", f"Not limiting: rice surplus {k['rice_exportable_surplus_mt']:.1f} Mt covers it"))
    if p.import_target_pct <= 0:
        out["import_target_pct"] = ("slack", "No target set: imports cut only where it pays")
    elif slack_import > 1e-6:
        out["import_target_pct"] = ("missed", f"Not reached: ${slack_import:.2f} bn short in {yT}; seed/market capacity and "
                                              "the budget cap substitution, so a higher target changes nothing")
    elif any(abs(c.dual_value()) > tol for c in cons_imp_y.values()):
        out["import_target_pct"] = ("binding", f"Met exactly ({k['import_saving_usd_bn']:.2f} $bn saved); it drives the plan")
    else:
        out["import_target_pct"] = ("slack", "Met without effort: the plan beats the target anyway")
    nat = sorted({c_ for (c_, y), c in cons_nat.items() if y == yT and abs(c.dual_value()) > tol})
    out["crop_growth_cap_pct"] = (("binding", f"Binding for {len(nat)} crops in {yT} ({', '.join(nat[:5])})") if nat else
                                  ("slack", "Not limiting"))
    leg_short = sum(1 for (d, y), (c, sl) in cons_leg.items() if y == yT and sl.solution_value() > 1)
    leg_n = sum(1 for (d, y) in cons_leg if y == yT)
    out["legume_ramp_pp"] = (("missed", f"Ramp not reached in {leg_short} of {leg_n} districts (soft target)") if leg_short else
                             ("slack", "Met everywhere"))
    out["gw_mode"] = (("binding", f"Caps bind in {n_gw_binding} district-years; {n_gw_viol} district-years stay above the cap "
                                  "(budget-limited, reported honestly)") if n_gw_binding or n_gw_viol else
                      ("slack", "Not limiting"))
    out["price_realisation"] = ("active", "Sets farm-gate prices of non-procured pulses/oilseeds - always shapes the plan")
    return {key: {"state": st, "note": note} for key, (st, note) in out.items()}


def district_trace(result: dict, district_code: int) -> dict:
    """Explainable decision trace for one district: crop moves, why, and rejected alternatives."""
    f = result["_frame"]
    g = f[f.district_code == district_code]
    if g.empty:
        return {"error": f"district {district_code} has no modelled cropland"}
    cst = store().constants
    ctx = store().context.set_index("district_code").loc[district_code]
    years = _years_of(f)
    yT = years[-1]
    stage_now = float(ctx.cgwb_stage_of_extraction_pct) if pd.notna(ctx.cgwb_stage_of_extraction_pct) else 0.0
    gy = g[g.agri_year == yT].copy()
    gy["change_ha"] = gy.plan_area_ha - gy.baseline_area_ha
    moves = []
    for r in gy.sort_values("change_ha").itertuples():
        if abs(r.change_ha) < 50:
            continue
        reason = []
        if r.change_ha < 0:
            if r.cwar > cst["cwar_threshold"]:
                reason.append(f"CWAR {r.cwar:.2f} > {cst['cwar_threshold']} (irrigation need {r.irrigation_need_mm:.0f} mm)")
            if r.gw_draft_ham_per_ha > 0.2:
                reason.append(f"pumps ~{r.gw_draft_ham_per_ha * 1000:.0f} mm of groundwater per ha")
            if stage_now > 100:
                reason.append(f"aquifer over-exploited ({stage_now:.0f}% of recharge)")
            if not reason:
                reason.append("frees land for pulses/oilseeds needed nationally")
        else:
            if r.n_credit_kg_ha > 0:
                reason.append(f"fixes nitrogen: +{r.n_credit_kg_ha:.0f} kg/ha for the next crop")
            if r.category in ("Pulse", "Edible oilseed"):
                reason.append("replaces imports")
            if r.cwar <= cst["cwar_threshold"]:
                reason.append(f"water-feasible (CWAR {r.cwar:.2f})")
            if r.net_margin_inr_ha > 0:
                reason.append(f"net margin Rs {r.net_margin_inr_ha:,.0f}/ha at MSP")
        moves.append({"crop": r.crop, "season": r.season, "baseline_ha": round(r.baseline_area_ha),
                      "plan_2030_ha": round(r.plan_area_ha), "change_ha": round(r.change_ha),
                      "yield_kg_ha": round(r.yield_kg_ha), "net_margin_inr_ha": round(r.net_margin_inr_ha),
                      "irrigation_need_mm": r.irrigation_need_mm, "cwar": r.cwar, "reasons": reason})
    rejected = []
    cand = gy[(gy.plan_area_ha < 1) & (gy.optimizer_crop == 1)].copy()
    cand["_rc"] = cand.reduced_cost_cr_per_kha.abs()
    for r in cand.sort_values("_rc").itertuples():
        why = []
        if r.cwar > cst["cwar_threshold"]:
            why.append(f"CWAR {r.cwar:.2f} exceeds {cst['cwar_threshold']} - blocked")
        if r.net_margin_inr_ha < 0:
            why.append(f"loses money: net margin Rs {r.net_margin_inr_ha:,.0f}/ha")
        why.append(f"each hectare would cost the national plan Rs {abs(r.reduced_cost_cr_per_kha) * 1e4:,.0f} (LP reduced cost)")
        rejected.append({"crop": r.crop, "season": r.season, "yield_kg_ha": round(r.yield_kg_ha), "reasons": why})
    def mix(q: pd.DataFrame, col: str) -> dict:
        q = q[q[col] > 0.5].sort_values(col, ascending=False)
        return {"total_ha": round(float(q[col].sum())),
                "top_crops": [{"crop": r.crop, "area_ha": round(getattr(r, col))} for r in q.head(5).itertuples()]}

    timeline = [{"year": 0, "season": s, **mix(gy[gy.season == s], "baseline_area_ha")} for s in ("Kharif", "Rabi")]
    for y in years:
        gg = g[g.agri_year == y]
        for s in ("Kharif", "Rabi"):
            timeline.append({"year": y, "season": s, **mix(gg[gg.season == s], "plan_area_ha")})
    drec = next((x for x in result["districts"] if x["district_code"] == district_code), None)
    return {
        "district_code": district_code, "district_name": ctx.district_name, "state_name": ctx.state_name,
        "context": {"gw_category": ctx.cgwb_category, "gw_stage_pct": ctx.cgwb_stage_of_extraction_pct,
                    "irrigation_coverage_pct": round(ctx.irrigation_coverage_pct, 1),
                    "soil_n_kg_ha": round(ctx.soil_n_kg_ha_est, 1), "soil_oc_pct": round(ctx.soil_oc_pct_est, 3),
                    "net_sown_area_ha": ctx.net_sown_area_ha, "fallow_ha": ctx.current_fallow_ha},
        "final_year": yT, "plan_years": years,
        "moves_2030": moves, "rejected_alternatives": rejected[:8], "rotation_timeline": timeline,
        "yearly": drec["years"] if drec else {},
    }


# ---- next-crop audit
RICE_FALLOW_CROPS = {"lentil", "gram", "urad", "moong", "rapeseed_mustard", "sesamum"}  # ICAR/TRFA rice-fallow package
TERMINAL_HEAT_FLAG_DAYS = 2.0  # extra Feb-Mar days with Tmax >= 32 C at grain fill that warrant a warning
DRY_SEASON_FLAG_PCT = -10.0


def _alt_why(c: dict, gw_cap: str | None, span: str = "2027-30") -> str:
    tot, gw = c["reduced_cost_inr_ha"] or 0, c["groundwater_cap_cost_inr_ha"] or 0
    if gw_cap in ("at cap", "above cap") and gw > 0.5 * tot:
        return (f"Forcing 1 ha of {c['name']} would cost the plan Rs {tot:,.0f} over {span}, {100 * gw / max(tot, 1):.0f}% of it "
                f"because it pumps groundwater in a district {gw_cap} on its sustainable draft")
    return (f"Forcing 1 ha of {c['name']} would cost the plan Rs {tot:,.0f} over {span}: the crops chosen give more margin, "
            "nitrogen and import savings per hectare of this season's land")


def district_candidates(result: dict, district_code: int, year: int | None = None) -> dict:
    """Next-crop audit for one district and plan year: for each season, every candidate crop with its predicted yield,
    water feasibility, economics, fit after the preceding season's crop, and what the plan decided (and why)."""
    f = result["_frame"]
    years = _years_of(f)
    yT = years[-1]
    year = int(year or yT)
    if year not in years:
        raise ValueError(f"year must be one of {years}")
    g = f[f.district_code == district_code]
    if g.empty:
        return {"error": f"district {district_code} has no modelled cropland"}
    ds = store()
    thr = ds.constants["cwar_threshold"]
    ag = ds["crop_agronomy"].set_index("crop")
    ctx = ds.context.set_index("district_code").loc[district_code]
    cl = ds["climate_projection_district_year"]
    cr = cl[(cl.district_code == district_code) & (cl.agri_year == year)]
    if cr.empty:  # after the CMIP6 projections end: the district's projected mean, as in plan_features
        cd = cl[cl.district_code == district_code]
        cr = cd.head(1).assign(**cd.mean(numeric_only=True).to_dict()) if len(cd) else cd
    heat = float(cr.terminal_heat_days_delta.iloc[0]) if len(cr) else 0.0
    gy = g[g.agri_year == year]
    rc = g[g.agri_year == yT].set_index(["crop", "season"]).reduced_cost_cr_per_kha
    lam_of = {y: (j + 1) / len(years) for j, y in enumerate(years)}
    gws = (g.agri_year.map(lam_of) * g.gw_shadow_inr_ha).groupby([g.crop, g.season]).sum() \
        if "gw_shadow_inr_ha" in g else pd.Series(dtype=float)
    drec = next((x for x in result["districts"] if x["district_code"] == district_code), None)
    gw_cap = drec["years"][year].get("gw_cap") if drec else None
    name = lambda c: str(ag.at[c, "display_name"]) if c in ag.index else c  # noqa: E731

    def dominant(q: pd.DataFrame, col: str) -> dict | None:
        q = q[q[col] > 0.5]
        if q.empty:
            return None
        r = q.loc[q[col].idxmax()]
        return {"crop": r.crop, "name": name(r.crop), "area_ha": round(float(r[col])),
                "share_pct": round(100 * float(r[col]) / float(q[col].sum()), 1)}

    # agri year y = Kharif y + Rabi y/y+1: Rabi follows this year's Kharif; Kharif follows last year's Rabi
    preceding = {"Rabi": dict(season="Kharif", year=year, label=f"Kharif {year} (plan)",
                              crop=dominant(gy[gy.season == "Kharif"], "plan_area_ha"))}
    if year == years[0]:
        preceding["Kharif"] = dict(season="Rabi", year=year - 1, label="Rabi today",
                                   crop=dominant(gy[gy.season == "Rabi"], "baseline_area_ha"))
    else:
        preceding["Kharif"] = dict(season="Rabi", year=year - 1, label=f"Rabi {year - 1} (plan)",
                                   crop=dominant(g[(g.agri_year == year - 1) & (g.season == "Rabi")], "plan_area_ha"))

    def flags_for(r, pc: str | None) -> list:
        out = []
        fam = ag.at[r.crop, "family"] if r.crop in ag.index else ""
        if r.cwar > thr:
            out.append({"code": "water_blocked", "level": "block",
                        "text": f"Water-infeasible: needs {r.irrigation_need_mm:.0f} mm of irrigation (CWAR {r.cwar:.2f} > {thr}) - cannot expand"})
        if r.season == "Rabi" and r.crop in ag.index and int(ag.at[r.crop, "heat_sensitive_rabi"]) and heat >= TERMINAL_HEAT_FLAG_DAYS:
            out.append({"code": "terminal_heat", "level": "warn",
                        "text": f"Terminal heat: +{heat:.1f} days >= 32 C in Feb-Mar at grain fill by {year} (CMIP6 SSP2-4.5)"})
        if pd.notna(r.season_rain_anom_pct) and r.season_rain_anom_pct <= DRY_SEASON_FLAG_PCT and r.crop in ag.index \
                and float(ag.at[r.crop, "drought_tolerance"]) < 0.5:
            out.append({"code": "drier_season", "level": "warn",
                        "text": f"{r.season} rain {r.season_rain_anom_pct:+.0f}% vs normal; low drought tolerance"})
        if r.net_margin_inr_ha < 0:
            out.append({"code": "loss_making", "level": "warn",
                        "text": f"Loses Rs {abs(r.net_margin_inr_ha):,.0f}/ha at expected prices"})
        if pc and pc in ag.index:
            pfam = ag.at[pc, "family"]
            ncred = float(ag.at[pc, "n_credit_next_crop_kg_ha"])
            if pc == r.crop:
                out.append({"code": "same_crop", "level": "warn",
                            "text": f"{name(r.crop)} after {name(pc)}: pest and disease carry-over, no rotation benefit"})
            elif pfam == "Fabaceae" and fam == "Fabaceae":
                out.append({"code": "legume_after_legume", "level": "warn",
                            "text": f"Legume after {name(pc)}: soil-borne wilt/root-rot risk; no extra N benefit"})
            elif pfam == "Poaceae" and fam == "Poaceae":
                out.append({"code": "cereal_after_cereal", "level": "info",
                            "text": f"Cereal after {name(pc)}: nitrogen-hungry, no rotation benefit"})
            if ncred > 0 and fam != "Fabaceae":
                out.append({"code": "n_credit", "level": "good",
                            "text": f"+{ncred:.0f} kg N/ha left by preceding {name(pc)} (~{ncred / UREA_N_SHARE:.0f} kg urea/ha saved)"})
            if r.season == "Rabi" and pc == "rice":
                if r.crop in RICE_FALLOW_CROPS:
                    out.append({"code": "rice_fallow", "level": "good",
                                "text": "Fits rice fallows: short-duration crop on residual soil moisture"})
                elif r.crop == "wheat":
                    out.append({"code": "late_sowing", "level": "warn",
                                "text": "Wheat after a ~125-day paddy is sown late, which raises terminal-heat exposure"})
        return out

    seasons = []
    for s in ("Kharif", "Rabi"):
        q = gy[gy.season == s]
        if q.empty or q.baseline_area_ha.sum() <= 0:
            continue
        tot_plan = float(q.plan_area_ha.sum()) or 1.0
        pre = preceding[s]
        pc = pre["crop"]["crop"] if pre["crop"] else None
        cands = []
        for r in q.itertuples():
            base_ha, plan_ha = float(r.baseline_area_ha), float(r.plan_area_ha)
            ch = plan_ha - base_ha
            tol = max(50.0, 0.02 * base_ha)
            fl = flags_for(r, pc)
            if base_ha < 1 and plan_ha < 1:
                decision = "Blocked (water)" if r.cwar > thr else ("Fixed" if not r.optimizer_crop else "Not chosen")
            elif ch > tol:
                decision = "Expand"
            elif ch < -tol:
                decision = "Reduce"
            else:
                decision = "Keep"
            rc_inr = rc.get((r.crop, s), np.nan)
            lp_cost = round(abs(float(rc_inr)) * 1e4) if decision in ("Not chosen", "Blocked (water)") and pd.notna(rc_inr) else None
            gw_part = min(round(float(gws.get((r.crop, s), 0.0))), lp_cost) if lp_cost is not None else None
            cands.append({
                "crop": r.crop, "name": name(r.crop), "category": r.category, "family": r.family,
                "decision": decision, "baseline_ha": round(base_ha), "plan_ha": round(plan_ha), "change_ha": round(ch),
                "plan_share_pct": round(100 * plan_ha / tot_plan, 1),
                "baseline_yield_kg_ha": round(float(r.baseline_yield_kg_ha)) if pd.notna(r.baseline_yield_kg_ha) else None,
                "predicted_yield_kg_ha": round(float(r.yield_kg_ha)), "climate_factor": round(float(r.climate_factor), 3),
                "yield_source": r.yield_source, "irrigation_need_mm": round(float(r.irrigation_need_mm)),
                "cwar": round(float(r.cwar), 2), "water_ok": bool(r.cwar <= thr),
                "price_inr_qtl": round(float(r.price_inr_qtl)), "net_margin_inr_ha": round(float(r.net_margin_inr_ha)),
                "n_credit_kg_ha": round(float(r.n_credit_kg_ha), 1),
                "kit_ha": round(float(getattr(r, "kit_ha", 0.0))), "price_support_ha": round(float(getattr(r, "price_support_ha", 0.0))),
                "reduced_cost_inr_ha": lp_cost, "groundwater_cap_cost_inr_ha": gw_part,
                "flags": fl,
            })
        order = {"Expand": 0, "Keep": 1, "Reduce": 2, "Not chosen": 3, "Fixed": 4, "Blocked (water)": 5}
        cands.sort(key=lambda c: (order[c["decision"]], -c["plan_ha"], c["reduced_cost_inr_ha"] or 0))
        grown = [c for c in cands if c["plan_ha"] > 0]
        expanding = sorted([c for c in cands if c["decision"] == "Expand"], key=lambda c: -c["change_ha"])
        lead = max(grown, key=lambda c: c["plan_ha"]) if grown else None
        if expanding:
            rec = expanding[0]
            why = f"the plan adds {rec['change_ha']:,} ha of {rec['name']} here"
        elif lead:
            rec = lead
            why = f"no switch pays off here under the current levers; {rec['name']} stays the main crop"
        else:
            rec, why = None, "no modelled crop"
        alt = [c for c in cands if c["decision"] == "Not chosen" and c["reduced_cost_inr_ha"] is not None]
        best_alt = min(alt, key=lambda c: c["reduced_cost_inr_ha"]) if alt else None
        seasons.append({
            "season": s, "preceding": pre, "plan_main_crop": dominant(q, "plan_area_ha"),
            "today_main_crop": dominant(q, "baseline_area_ha"),
            "recommended": ({"crop": rec["crop"], "name": rec["name"], "why": why,
                             "positives": [x["text"] for x in rec["flags"] if x["level"] == "good"],
                             "cautions": [x["text"] for x in rec["flags"] if x["level"] in ("warn", "block")]} if rec else None),
            "closest_alternative": ({"crop": best_alt["crop"], "name": best_alt["name"],
                                     "cost_to_plan_inr_ha": best_alt["reduced_cost_inr_ha"],
                                     "groundwater_cap_cost_inr_ha": best_alt["groundwater_cap_cost_inr_ha"],
                                     "why": _alt_why(best_alt, gw_cap, f"{years[0]}-{str(yT)[2:]}")} if best_alt else None),
            "candidates": cands,
        })
    return {
        "district_code": district_code, "district_name": ctx.district_name, "state_name": ctx.state_name, "year": year,
        "groundwater_cap": gw_cap,
        "climate": {"terminal_heat_days_delta": round(heat, 1),
                    "kharif_rain_ratio": round(float(cr.kharif_rain_mm_ratio.iloc[0]), 3) if len(cr) else None,
                    "rabi_rain_ratio": round(float(cr.rabi_rain_mm_ratio.iloc[0]), 3) if len(cr) else None,
                    "rabi_tmax_delta_c": round(float(cr.rabi_tmax_c_delta.iloc[0]), 2) if len(cr) else None,
                    "scenario": str(cr.scenario.iloc[0]) if len(cr) and "scenario" in cr else None},
        "seasons": seasons,
        "method": ("Predicted yield = district baseline x climate factor from the yield model (CMIP6 anomalies for the "
                   "year). Water feasibility = CWAR (irrigation need / safe groundwater + canal supply). Decisions come from "
                   "the LP plan; 'cost to plan' is the LP reduced cost of forcing one hectare of a crop it did not choose. "
                   "Rotation checks use the dominant crop of the preceding season."),
    }
