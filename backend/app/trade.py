"""Bilateral trade dependencies (India Data Portal / DGCI&S country-wise imports) and trade-shock translation."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .data import store

GROUP_ALIASES = {"pulses": "pulses", "all pulses": "pulses", "pulse": "pulses", "dal": "pulses",
                 "edible oils": "edible_oils", "edible_oils": "edible_oils", "all edible oils": "edible_oils",
                 "edible oil": "edible_oils", "oils": "edible_oils", "vegetable oils": "edible_oils"}
SYNONYMS = {"arhar": "tur", "pigeon pea": "tur", "pigeonpea": "tur", "toor": "tur", "chana": "chickpea",
            "gram": "chickpea", "bengal gram": "chickpea", "masur": "lentil", "masoor": "lentil", "yellow peas": "peas",
            "pea": "peas", "urad": "urad", "moong": "moong", "mung": "moong", "palm": "palm oil", "cpo": "palm oil",
            "crude palm oil": "palm oil", "palmolein": "palm oil", "soy": "soybean oil", "soya": "soybean oil",
            "soyabean": "soybean oil", "soybean": "soybean oil", "sunflower": "sunflower", "mustard": "mustard",
            "rapeseed": "rapeseed", "canola": "rapeseed", "groundnut": "groundnut oil", "coconut": "coconut"}
# domestic crops that can replace each imported product (for the hectares-needed estimate)
SUBSTITUTES = {"palm oil": ["rapeseed_mustard", "soybean", "groundnut"], "soybean oil": ["soybean", "rapeseed_mustard"],
               "sunflower / safflower oil": ["sunflower", "rapeseed_mustard"], "lentil (masur)": ["lentil"],
               "tur (pigeon pea)": ["tur"], "chickpea (gram)": ["gram"], "urad / moong": ["urad", "moong"],
               "peas": ["gram", "lentil"], "rapeseed / mustard oil": ["rapeseed_mustard"],
               "groundnut oil": ["groundnut"], "coconut / palm-kernel oil": ["groundnut", "rapeseed_mustard"],
               "other vegetable oils": ["sesamum", "rapeseed_mustard"]}


def table() -> pd.DataFrame:
    try:
        return store()["trade_bilateral_dependencies"]
    except KeyError:
        return pd.DataFrame()


def latest_fy(t: pd.DataFrame | None = None) -> str | None:
    t = table() if t is None else t
    if t.empty:
        return None
    full = t[t.fy_months_covered >= 12]
    return str((full if len(full) else t).fiscal_year.max())


def _match_country(cur: pd.DataFrame, country: str) -> pd.DataFrame:
    c = country.strip().lower()
    m = cur[(cur.country.str.lower() == c) | (cur.alpha3.str.lower() == c)]
    if m.empty:
        m = cur[cur.country.str.lower().str.startswith(c)]
    if m.empty and len(c) >= 4:
        m = cur[cur.country.str.lower().str.contains(re.escape(c))]
    if m.empty and c in ("usa", "us", "america", "united states"):
        m = cur[cur.alpha3 == "USA"]
    if m.empty and c in ("russia",):
        m = cur[cur.alpha3 == "RUS"]
    if m.empty and c in ("tanzania",):
        m = cur[cur.alpha3 == "TZA"]
    return m


def match_product(cur: pd.DataFrame, product: str) -> tuple[str, str] | None:
    """-> ('group', 'pulses'|'edible_oils') or ('product', label)."""
    p = product.strip().lower().replace("_", " ")
    if p in GROUP_ALIASES:
        return "group", GROUP_ALIASES[p]
    p = SYNONYMS.get(p, p)
    labels = sorted(cur["product"].dropna().unique())
    for rule in (lambda l: l.lower() == p, lambda l: l.lower().startswith(p),
                 lambda l: p in re.split(r"[ /()-]+", l.lower()), lambda l: p in l.lower()):
        hits = [l for l in labels if rule(l)]
        if hits:
            return "product", hits[0]
    return None


def resolve_trade_cut(cut: dict) -> dict:
    """{'country', 'product', 'pct'} -> the import volume (kt) India must replace, from the latest complete fiscal year."""
    t = table()
    if t.empty:
        raise ValueError("bilateral trade table not loaded (data/processed/app/trade_bilateral_dependencies.csv)")
    fy = latest_fy(t)
    cur = t[t.fiscal_year == fy]
    country, product = str(cut.get("country", "")), str(cut.get("product", ""))
    pct = float(np.clip(float(cut.get("pct", 0) or 0), 0, 100))
    mp = match_product(cur, product)
    if mp is None:
        raise ValueError(f"unknown product '{product}'. Try one of: pulses, edible oils, "
                         + ", ".join(sorted(cur["product"].unique())))
    sel = cur[cur.commodity_group == mp[1]] if mp[0] == "group" else cur[cur["product"] == mp[1]]
    rows = _match_country(sel, country)
    if rows.empty:
        top = sel.groupby("country").qty_t.sum().sort_values(ascending=False).head(5)
        raise ValueError(f"India imported no {mp[1].replace('_', ' ')} from '{country}' in FY {fy}. Top suppliers: "
                         + ", ".join(f"{c} ({q / 1e3:,.0f} kt)" for c, q in top.items()))
    qty_kt = float(rows.qty_t.sum()) / 1e3
    usd_mn = float(rows.value_usd_mn.sum())
    total_kt = float(sel.qty_t.sum()) / 1e3
    grp = mp[1] if mp[0] == "group" else str(rows.commodity_group.iat[0])
    return {"country": str(rows.country.iat[0]), "alpha3": str(rows.alpha3.iat[0]),
            "product": mp[1].replace("_", " ") if mp[0] == "group" else mp[1], "group": grp, "fiscal_year": fy,
            "pct": pct, "base_qty_kt": round(qty_kt, 1), "base_value_usd_mn": round(usd_mn, 1),
            "share_of_product_pct": round(100 * qty_kt / total_kt, 1) if total_kt > 0 else None,
            "volume_kt": round(qty_kt * pct / 100, 1), "value_usd_mn": round(usd_mn * pct / 100, 1)}


def suppliers(product: str | None = None, top: int = 8) -> dict:
    """Supplier concentration per product for the latest complete fiscal year (+ 5-year volume history)."""
    t = table()
    if t.empty:
        return {"fiscal_year": None, "products": []}
    fy = latest_fy(t)
    cur = t[t.fiscal_year == fy]
    out = []
    for (grp, prod), g in cur.groupby(["commodity_group", "product"]):
        if product and prod != product:
            continue
        tot = float(g.qty_t.sum())
        if tot < 1000:  # < 1 kt a year: not a meaningful dependency
            continue
        g = g.sort_values("qty_t", ascending=False)
        shares = g.qty_t / tot
        hist = t[t["product"] == prod].groupby("fiscal_year").qty_t.sum()
        out.append({"product": prod, "group": grp, "total_kt": round(tot / 1e3, 1),
                    "total_usd_mn": round(float(g.value_usd_mn.sum()), 1),
                    "hhi": round(float((shares ** 2).sum()), 3),
                    "suppliers": [{"country": r.country, "alpha3": r.alpha3, "qty_kt": round(r.qty_t / 1e3, 1),
                                   "value_usd_mn": round(r.value_usd_mn, 1), "share_pct": round(100 * r.qty_t / tot, 1)}
                                  for r in g.head(top).itertuples()],
                    "history_kt": [{"fiscal_year": fy_, "qty_kt": round(float(q) / 1e3, 1)}
                                   for fy_, q in hist.items() if t[t.fiscal_year == fy_].fy_months_covered.max() >= 12][-6:]})
    out.sort(key=lambda r: -r["total_usd_mn"])
    return {"fiscal_year": fy, "source": "India Data Portal - Import Trade Statistics (DGCI&S, country x HS8, monthly)",
            "products": out}


def decompose(country: str, product: str, pct: float) -> dict:
    """Quick (no-LP) translation of a bilateral cut into domestic production and land needed."""
    cut = resolve_trade_cut({"country": country, "product": product, "pct": pct})
    ds = store()
    cand = ds["district_crop_candidates"]
    agr = ds["crop_agronomy"].set_index("crop")
    subs = SUBSTITUTES.get(cut["product"], ["rapeseed_mustard", "soybean"] if cut["group"] == "edible_oils" else ["gram", "lentil", "tur"])
    opts = []
    for c in subs:
        g = cand[cand.crop == c]
        if g.empty or c not in agr.index:
            continue
        y = float(np.average(g.baseline_yield_kg_ha, weights=g.baseline_area_ha + 1e-9))
        rec = float(agr.at[c, "product_recovery"]) if cut["group"] == "edible_oils" else 1.0
        t_ha = y * rec / 1e3
        opts.append({"crop": c, "display_name": str(agr.at[c, "display_name"]), "national_yield_kg_ha": round(y),
                     "product_t_per_ha": round(t_ha, 3),
                     "hectares_needed_mha": round(cut["volume_kt"] * 1e3 / max(t_ha, 1e-6) / 1e6, 2)})
    return {**cut, "substitutes": opts,
            "note": "Hectares if one crop alone replaced the volume at today's national average yield; the optimizer "
                    "spreads it across water-feasible districts and crops (run the plan with this trade shock)."}


def alternatives(cut: dict, exclude: set | None = None, top: int = 4) -> list:
    """Other countries India already buys the same product (or group) from - where a shortfall could be sourced."""
    t = table()
    if t.empty:
        return []
    cur = t[t.fiscal_year == cut.get("fiscal_year", latest_fy(t))]
    sel = cur[cur["product"] == cut["product"]]
    if sel.empty:
        sel = cur[cur.commodity_group == cut["group"]]
    tot = float(sel.qty_t.sum())
    by = sel.groupby("country").qty_t.sum().sort_values(ascending=False)
    skip = (exclude or set()) | {cut["country"]}
    return [{"country": c, "qty_kt": round(float(q) / 1e3, 1), "share_pct": round(100 * float(q) / tot, 1)}
            for c, q in by.items() if c not in skip and q > 0][:top]


_COUNTRY_ALIASES = {"usa": "USA", "u.s.": "USA", "america": "USA", "united states": "USA", "us": "USA", "russia": "RUS",
                    "uae": "ARE", "emirates": "ARE", "tanzania": "TZA", "uk": "GBR", "britain": "GBR",
                    "netherlands": "NLD", "holland": "NLD", "burma": "MMR"}
_GENERIC = {"united", "republic", "kingdom", "islands", "island", "south", "north", "democratic", "people", "states",
            "federation", "the", "and", "of", "arab", "saint", "new", "guinea", "east", "west", "central"}
_PRODUCT_WORDS = [(r"\bpalm", "palm oil"), (r"\bsoy", "soybean oil"), (r"\bsunflower", "sunflower"),
                  (r"\b(mustard|rapeseed|canola)", "rapeseed"), (r"\b(lentils?|masu?o?r)\b", "lentil"),
                  (r"\b(chickpeas?|chana|gram)\b", "chickpea"), (r"\b(tur|toor|arhar|pigeon ?peas?)\b", "tur"),
                  (r"\b(urad|moong|mung)\b", "urad"), (r"\b(yellow )?peas?\b", "peas"),
                  (r"\b(pulses?|dal|dals)\b", "pulses"), (r"\b(edible|vegetable|cooking) oils?\b", "edible oils")]
_STOP_WORDS = re.compile(r"\b(halt(s|ed)?|stop(s|ped)?|ban(s|ned)?|embargo(es|ed)?|cuts? off|end(s|ed)?|block(s|ed)?|"
                         r"sanction(s|ed)?|suspend(s|ed)?|zero)\b")


def parse_cut(q: str) -> dict | None:
    """Free text ('cut palm oil imports from Indonesia by 30%', 'Canada stops lentils') -> {country, product, pct}."""
    t = table()
    if t.empty:
        return None
    q = q.lower()
    cur = t[t.fiscal_year == latest_fy(t)]
    country = None
    for a, iso in _COUNTRY_ALIASES.items():
        if re.search(r"(?<![\w.])" + re.escape(a) + r"(?![\w])", q):
            hit = cur[cur.alpha3 == iso]
            if len(hit):
                country = str(hit.country.iat[0])
                break
    if country is None:
        names = cur.groupby("country").qty_t.sum().sort_values(ascending=False).index
        for c in names:
            words = {c.lower()} | {w for w in re.split(r"[ ,()'-]+", c.lower()) if len(w) >= 5 and w not in _GENERIC}
            if any(re.search(r"\b" + re.escape(w) + r"\b", q) for w in words):
                country = str(c)
                break
    product = next((p for pat, p in _PRODUCT_WORDS if re.search(pat, q)), None)
    if not country or not product:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|per ?cent|pct)", q)
    if m:
        pct = float(m.group(1))
    elif _STOP_WORDS.search(q):
        pct = 100.0
    elif re.search(r"\b(cut|reduce|replace|lose|losing|without|shock|disrupt)", q):
        pct = 100.0
    else:
        return None
    return {"country": country, "product": product, "pct": max(0.0, min(pct, 100.0))}
