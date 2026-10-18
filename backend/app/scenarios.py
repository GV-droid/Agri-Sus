"""Crisis scenarios (stress tests layered on the CMIP6 SSP2-4.5 baseline)."""
from __future__ import annotations

from copy import deepcopy

NORTH = ["Punjab", "Haryana", "Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Uttarakhand", "Himachal Pradesh",
         "Bihar", "Delhi", "Chandigarh"]
SOUTH = ["Karnataka", "Tamil Nadu", "Andhra Pradesh", "Telangana", "Kerala", "Puducherry"]

SCENARIOS = {
    "baseline": {
        "label": "Baseline (CMIP6 SSP2-4.5)",
        "description": "NEX-GDDP-CMIP6 SSP2-4.5 2026-30 climate signal (MRI-ESM2-0, EC-Earth3), 2026-27 MSPs, "
                       "2024-25 trade balance with demand growth.",
        "params": {},
    },
    "el_nino_2028": {
        "label": "2028 El Nino drought",
        "description": "Strong El Nino in 2028: Kharif monsoon -18% nationally (-25% in the South), +0.8 C Kharif Tmax.",
        "params": {"shocks": [{"year": 2028, "kharif_rain_pct": -18, "kharif_tmax_c": 0.8, "states": []},
                              {"year": 2028, "kharif_rain_pct": -8.5, "states": SOUTH}]},  # -18% x -8.5% = -25%
    },
    "palm_oil_shock": {
        "label": "Palm-oil price shock",
        "description": "Indonesian export levy + biodiesel mandate: landed edible-oil price +60%, pulses +10% (2027-30).",
        "params": {"import_price_mult": {"edible_oils": 1.6, "pulses": 1.1}},
    },
    "zero_gw_depletion": {
        "label": "Zero groundwater depletion mandate",
        "description": "Every district must reach CGWB 'Safe' (stage of extraction <= 70%) by 2030; ramped linearly.",
        "params": {"gw_mode": "zero_depletion"},
    },
    "indonesia_palm_halt": {
        "label": "Indonesia halts palm-oil exports",
        "description": "A 2022-style export ban held for 2027-30: India must replace the 3.9 Mt of palm oil it bought from "
                       "Indonesia in FY2024-25 (49% of its palm-oil imports; DGCI&S data via India Data Portal).",
        "params": {"trade_cuts": [{"country": "Indonesia", "product": "palm oil", "pct": 100}]},
    },
    "canada_lentil_halt": {
        "label": "Canada lentil supply cut",
        "description": "Lentil shipments from Canada stop (a risk flagged in the 2023 diplomatic row): replace the 626 kt "
                       "bought in FY2024-25 (51% of India's lentil imports).",
        "params": {"trade_cuts": [{"country": "Canada", "product": "lentil", "pct": 100}]},
    },
    "northern_heatwave": {
        "label": "Wheat-belt Rabi heatwave (2029)",
        "description": "Feb-Mar 2029 terminal heat: +2.2 C Rabi Tmax across the north-western and central wheat belt (cf. March 2022).",
        "params": {"shocks": [{"year": 2029, "rabi_tmax_c": 2.2, "states": NORTH}]},
    },
}


_RANGES = {
    "max_shift_pct": (0.0, 40.0), "cereal_floor_pct": (80.0, 130.0), "import_target_pct": (0.0, 80.0),
    "incentive_budget_cr": (0.0, 50000.0), "income_floor_pct": (80.0, 120.0), "rice_export_retention_pct": (0.0, 100.0),
    "legume_ramp_pp": (0.0, 5.0), "fallow_use_pct": (0.0, 60.0), "price_realisation": (0.5, 1.2),
    "crop_growth_cap_pct": (1.0, 20.0),
    "w_farmer": (0.0, 5.0), "w_import": (0.0, 5.0), "w_soil": (0.0, 5.0), "w_water": (0.0, 5.0),
}
_GW_MODES = {"status_quo", "cap80", "zero_depletion"}
_PLAN_YEARS = {2027, 2028, 2029, 2030}
BUDGET_MODES = {"fixed": "Fixed budget - best plan within the annual budget",
                "min_budget": "Least cost - smallest budget that meets the import target"}
INSTRUMENT_IDS = ("dbt", "seed_kits", "price_support")
MAX_TRADE_CUTS = 3


def _num(v, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("override values must be numeric")
    if x != x:
        raise ValueError("override values must be numeric")
    return min(max(x, lo), hi)


def _states(v) -> list:
    """State names, matched case-insensitively against the modelled states (free text never reaches the LP, the
    cache key, the BigQuery traces or the co-pilot's context)."""
    if not isinstance(v, (list, tuple)) or len(v) > 40:
        raise ValueError("states must be a list of up to 40 state names")
    from .data import store
    known = {s.lower(): s for s in store().context.state_name.unique()}
    out = []
    for x in v:
        s = known.get(str(x).strip().lower())
        if s is None:
            from difflib import get_close_matches
            near = get_close_matches(str(x).strip().title(), list(known.values()), n=3)
            raise ValueError(f"unknown state '{str(x)[:40]}'" + (f" (did you mean {', '.join(near)}?)" if near else ""))
        if s not in out:
            out.append(s)
    return sorted(out)


def _trade_cuts(v) -> list:
    """[{country, product, pct}] -> resolved against the bilateral trade table (raises ValueError with suggestions)."""
    from .trade import resolve_trade_cut
    if not isinstance(v, (list, tuple)) or len(v) > MAX_TRADE_CUTS:
        raise ValueError(f"trade_cuts must be a list of up to {MAX_TRADE_CUTS} items")
    out, seen = [], set()
    for x in v:
        if not isinstance(x, dict):
            raise ValueError("each trade cut needs country, product and pct")
        pct = round(_num(x.get("pct", 0), 0, 100), 1)
        if pct <= 0:
            continue
        r = resolve_trade_cut({"country": str(x.get("country", ""))[:60], "product": str(x.get("product", ""))[:60],
                               "pct": pct})
        key = (r["country"], r["product"])
        if key in seen:
            raise ValueError(f"duplicate trade cut for {r['product']} from {r['country']}")
        seen.add(key)
        out.append({"country": r["country"], "product": r["product"], "pct": pct})
    return out


def sanitize_overrides(overrides: dict | None) -> dict:
    """Whitelist + clamp user/agent supplied levers so a request cannot build an arbitrary or oversized LP."""
    out: dict = {}
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        if k in _RANGES:
            out[k] = _num(v, *_RANGES[k])
        elif k == "gw_mode":
            if v not in _GW_MODES:
                raise ValueError("gw_mode must be one of " + ", ".join(sorted(_GW_MODES)))
            out[k] = v
        elif k == "states":
            out[k] = _states(v)
        elif k == "budget_mode":
            if v not in BUDGET_MODES:
                raise ValueError("budget_mode must be one of " + ", ".join(BUDGET_MODES))
            out[k] = v
        elif k == "instruments":
            if not isinstance(v, (list, tuple)) or any(x not in INSTRUMENT_IDS for x in v):
                raise ValueError("instruments must be a list drawn from " + ", ".join(INSTRUMENT_IDS))
            out[k] = [x for x in INSTRUMENT_IDS if x in v]  # canonical order -> one cache key per set
        elif k == "trade_cuts":
            out[k] = _trade_cuts(v)
        elif k == "import_price_mult":
            if not isinstance(v, dict):
                raise ValueError("import_price_mult must be an object")
            out[k] = {g: _num(x, 0.3, 5.0) for g, x in v.items() if g in ("pulses", "edible_oils")}
        elif k == "shocks":
            if not isinstance(v, (list, tuple)) or len(v) > 8:
                raise ValueError("shocks must be a list of up to 8 items")
            sh = []
            for x in v:
                if not isinstance(x, dict) or int(_num(x.get("year", 0), 0, 9999)) not in _PLAN_YEARS:
                    raise ValueError("each shock needs a year in 2027-2030")
                sh.append({"year": int(x["year"]), "kharif_rain_pct": _num(x.get("kharif_rain_pct", 0), -60, 60),
                           "kharif_tmax_c": _num(x.get("kharif_tmax_c", 0), -3, 5),
                           "rabi_tmax_c": _num(x.get("rabi_tmax_c", 0), -3, 5), "states": _states(x.get("states", []))})
            out[k] = sh
        # unknown keys are dropped silently
    return out


def scenario_params(name: str, overrides: dict | None = None) -> dict:
    if name not in SCENARIOS:  # never solve (and cache) the baseline under an arbitrary label
        raise ValueError(f"unknown scenario '{str(name)[:40]}'; use one of: {', '.join(SCENARIOS)}")
    base = deepcopy(SCENARIOS[name]["params"])
    for k, v in sanitize_overrides(overrides).items():
        if v is None:
            continue
        if k == "shocks":
            base["shocks"] = base.get("shocks", []) + list(v)
        elif k == "import_price_mult":
            m = base.get("import_price_mult", {"pulses": 1.0, "edible_oils": 1.0})
            m.update(v)
            base[k] = m
        else:
            base[k] = v
    if base.get("trade_cuts") and not (overrides or {}).get("trade_cuts"):
        # preset shocks in the same canonical form the UI sends back, so both hit the same cached solve
        base["trade_cuts"] = _trade_cuts(base["trade_cuts"])
    base["scenario"] = name
    return base
