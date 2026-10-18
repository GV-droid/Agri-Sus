#!/usr/bin/env python3
"""Fetch district climate history (NASA POWER) and CMIP6 projections (Open-Meteo Climate API).

History : NASA POWER daily (MERRA-2 based, 0.5 deg) 2001-2025 at every district centroid in India.
Future  : NASA NEX-GDDP-CMIP6 (bias-corrected daily, SSP2-4.5) via NCCS THREDDS subset service for
          MRI-ESM2-0, IITM-ESM, EC-Earth3; baseline 2005-2014 vs projection 2026-2031 (0.5-deg sampling).

Both are reduced to agronomic indicators per agricultural year (Jun Y .. May Y+1):
  kharif (Jun-Sep) rain/Tmax/ET0, longest Kharif dry spell (<2.5 mm/day), heavy-rain days (>=64.5 mm),
  rabi (Nov-Mar) rain/Tmax/Tmin/ET0, terminal heat days (Feb-Mar Tmax >= 32 C),
  harvest-window (Oct-Nov) extreme-rain days.

Outputs:
  data/raw/climate_hist_district_year.csv
  data/raw/climate_cmip6_district_year.csv
Raw API responses are cached in data/raw/climate_cache/ so the script can be resumed.
Usage: .venv/bin/python scripts/fetch_climate.py [hist] [cmip6]
"""
import csv
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/raw/climate_cache"
CENTROIDS = ROOT / "data/processed/geo/district_centroids.csv"


def get_json(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agri-sus/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=180))
        except Exception as e:  # noqa: BLE001
            wait = 20 * (i + 1) if "429" in str(e) else 4 * (i + 1)
            print(f"  retry {i+1} ({e}); sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(url)


def cached(name, url):
    p = CACHE / name
    if p.exists():
        return json.loads(p.read_text())
    d = get_json(url)
    p.write_text(json.dumps(d))
    return d


def ra_mm(lat, doy):
    """Extraterrestrial radiation (mm/day evaporation equivalent), FAO-56 eq. 21."""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1, min(1, -math.tan(phi) * math.tan(dec))))
    ra = 24 * 60 / math.pi * 0.082 * dr * (ws * math.sin(phi) * math.sin(dec)
                                           + math.cos(phi) * math.cos(dec) * math.sin(ws))
    return 0.408 * ra


def hargreaves(tmax, tmin, lat, d):
    if tmax is None or tmin is None:
        return None
    tmean = (tmax + tmin) / 2
    return max(0.0, 0.0023 * ra_mm(lat, d.timetuple().tm_yday) * (tmean + 17.8) * math.sqrt(max(tmax - tmin, 0)))


def longest_run(vals, cond):
    best = cur = 0
    for v in vals:
        cur = cur + 1 if (v is not None and cond(v)) else 0
        best = max(best, cur)
    return best


def indicators(days, lat):
    """days: dict date -> (tmax, tmin, prcp). Returns {agri_year: {...}}."""
    years = sorted({d.year for d in days})
    out = {}
    for y in years:
        def rng(a, b):
            return [(d, days[d]) for d in sorted(days) if a <= d <= b]
        kh = rng(date(y, 6, 1), date(y, 9, 30))
        rb = rng(date(y, 11, 1), date(y + 1, 3, 31))
        fm = rng(date(y + 1, 2, 1), date(y + 1, 3, 31))
        on = rng(date(y, 10, 1), date(y, 11, 30))
        if len(kh) < 110 or len(rb) < 140:
            continue

        def mean(xs):
            xs = [x for x in xs if x is not None]
            return round(sum(xs) / len(xs), 2) if xs else None

        def tot(xs):
            xs = [x for x in xs if x is not None]
            return round(sum(xs), 1) if xs else None
        rec = {
            "agri_year": y,
            "kharif_rain_mm": tot(v[2] for _, v in kh),
            "kharif_tmax_c": mean(v[0] for _, v in kh),
            "kharif_tmin_c": mean(v[1] for _, v in kh),
            "kharif_max_dry_spell_days": longest_run([v[2] for _, v in kh], lambda p: p < 2.5),
            "kharif_heavy_rain_days": sum(1 for _, v in kh if v[2] is not None and v[2] >= 64.5),
            "rabi_rain_mm": tot(v[2] for _, v in rb),
            "rabi_tmax_c": mean(v[0] for _, v in rb),
            "rabi_tmin_c": mean(v[1] for _, v in rb),
            "terminal_heat_days": sum(1 for _, v in fm if v[0] is not None and v[0] >= 32),
            "harvest_extreme_rain_days": sum(1 for _, v in on if v[2] is not None and v[2] >= 64.5),
        }
        if all(v[1] is not None for _, v in kh[:5]):
            rec["kharif_et0_mm"] = tot(hargreaves(v[0], v[1], lat, d) for d, v in kh)
            rec["rabi_et0_mm"] = tot(hargreaves(v[0], v[1], lat, d) for d, v in rb)
        out[y] = rec
    return out


def load_centroids():
    with CENTROIDS.open() as f:
        return list(csv.DictReader(f))


def write(path, rows):
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)", file=sys.stderr)


def fetch_hist(cents):
    def one(c):
        lat, lon = float(c["lat"]), float(c["lon"])
        url = ("https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,T2M_MIN,PRECTOTCORR"
               f"&community=AG&longitude={lon}&latitude={lat}&start=20010101&end=20251231&format=JSON")
        d = cached(f"power_{c['district_code']}.json", url)
        p = d["properties"]["parameter"]

        def val(x):
            return None if x is None or x <= -998 else x
        days = {}
        for k, tmax in p["T2M_MAX"].items():
            dt = date(int(k[:4]), int(k[4:6]), int(k[6:]))
            days[dt] = (val(tmax), val(p["T2M_MIN"].get(k)), val(p["PRECTOTCORR"].get(k)))
        return [{"district_code": c["district_code"], **r} for r in indicators(days, lat).values()]

    rows = []
    with ThreadPoolExecutor(4) as ex:
        for i, r in enumerate(ex.map(one, cents)):
            rows += r
            if i % 25 == 0:
                print(f"  power {i}/{len(cents)}", file=sys.stderr)
    write(ROOT / "data/raw/climate_hist_district_year.csv", rows)


NEX = "https://ds.nccs.nasa.gov/thredds"
NEX_MODELS = ["MRI-ESM2-0", "IITM-ESM", "EC-Earth3"]
NEX_VARS = ["tasmax", "tasmin", "pr"]
BBOX = dict(north=37.5, south=6, west=68, east=97.5)  # all of India


def nex_files(model, scenario, var):
    import re as _re
    url = f"{NEX}/catalog/AMES/NEX/GDDP-CMIP6/{model}/{scenario}/r1i1p1f1/{var}/catalog.xml"
    req = urllib.request.Request(url, headers={"User-Agent": "agri-sus/1.0"})
    xml = urllib.request.urlopen(req, timeout=120).read().decode()
    files = {}
    for name in _re.findall(r'name="([^"]+\.nc)"', xml):
        m = _re.search(r"_(\d{4})(_v[\d.]+)?\.nc$", name)
        if m:
            y = int(m.group(1))
            if y not in files or "_v" in name:  # prefer latest version
                files[y] = name
    return files


def nex_get(model, scenario, var, year, name):
    p = CACHE / f"nexIN_{model}_{var}_{year}.nc"
    if p.exists() and p.stat().st_size > 100000:
        return p
    url = (f"{NEX}/ncss/grid/AMES/NEX/GDDP-CMIP6/{model}/{scenario}/r1i1p1f1/{var}/{name}?var={var}"
           f"&north={BBOX['north']}&south={BBOX['south']}&west={BBOX['west']}&east={BBOX['east']}"
           "&horizStride=2&temporal=all&accept=netcdf3")
    for i in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agri-sus/1.0"})
            p.write_bytes(urllib.request.urlopen(req, timeout=600).read())
            return p
        except Exception as e:  # noqa: BLE001
            print(f"  retry {model} {var} {year}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise RuntimeError(url)


def fetch_cmip6(cents):
    """NASA NEX-GDDP-CMIP6 (bias-corrected, 0.25 deg; sampled at 0.5 deg), SSP2-4.5.
    Baseline: historical 2005-2014; projection: ssp245 2026-2031 (agri years 2026-2030)."""
    import netCDF4
    import numpy as np
    jobs = []
    for m in NEX_MODELS:
        for v in NEX_VARS:
            for scen, years in [("historical", range(2005, 2015)), ("ssp245", range(2026, 2032))]:
                try:
                    files = nex_files(m, scen, v)
                except Exception as e:  # noqa: BLE001
                    print(f"  skip {m} {scen} {v}: {e}", file=sys.stderr)
                    continue
                jobs += [(m, scen, v, y, files[y]) for y in years if y in files]
    print(f"  {len(jobs)} NEX-GDDP files to fetch", file=sys.stderr)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda j: nex_get(*j), jobs))
    rows = []
    for m in NEX_MODELS:
        series = {}  # (var) -> {date: array}
        grid = None
        for v in NEX_VARS:
            for f in sorted(CACHE.glob(f"nexIN_{m}_{v}_*.nc")):
                d = netCDF4.Dataset(f)
                if grid is None:
                    grid = (np.array(d["lat"][:]), np.array(d["lon"][:]))
                t = netCDF4.num2date(d["time"][:], d["time"].units, getattr(d["time"], "calendar", "standard"))
                arr = np.array(d[v][:], dtype="float32")
                for i, tt in enumerate(t):
                    try:
                        dt = date(tt.year, tt.month, tt.day)
                    except ValueError:
                        continue
                    series.setdefault(v, {})[dt] = arr[i]
                d.close()
        if grid is None or len(series) < 3:
            continue
        lats, lons = grid
        for c in cents:
            iy = int(np.abs(lats - float(c["lat"])).argmin())
            ix = int(np.abs(lons - float(c["lon"])).argmin())
            days = {}
            for dt, a in series["tasmax"].items():
                tn = series["tasmin"].get(dt)
                pr = series["pr"].get(dt)
                days[dt] = (float(a[iy, ix]) - 273.15,
                            None if tn is None else float(tn[iy, ix]) - 273.15,
                            None if pr is None else float(pr[iy, ix]) * 86400)
            for r in indicators(days, float(c["lat"])).values():
                period = "baseline" if r["agri_year"] <= 2014 else "future"
                if period == "baseline" and r["agri_year"] > 2013:
                    continue
                rows.append({"district_code": c["district_code"], "model": m, "period": period, **r})
        print(f"  {m}: done", file=sys.stderr)
    write(ROOT / "data/raw/climate_cmip6_district_year.csv", rows)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    what = sys.argv[1:] or ["hist", "cmip6"]
    cents = load_centroids()
    if "hist" in what:
        fetch_hist(cents)
    if "cmip6" in what:
        fetch_cmip6(cents)


if __name__ == "__main__":
    main()
