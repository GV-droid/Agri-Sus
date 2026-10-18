"""Scrape India's commodity-wise import/export statistics (TradeStat / EIDB, Dept. of Commerce).

Source: https://tradestat.commerce.gov.in/eidb/commodity_wise_export (and _import)
Pulls ALL commodities at 8-digit HS level for every available fiscal year, both in
quantity and US$ million, then keeps agri/food chapters (HS 01-24) plus fertilisers (31).

Output: data/raw/trade_hs8_{import,export}.csv
  columns: flow, fiscal_year, hs_code, hs2, commodity, unit, quantity, value_usd_mn
"""
import csv
import html
import http.cookiejar
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://tradestat.commerce.gov.in/eidb"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "raw")
KEEP_HS2 = {f"{i:02d}" for i in range(1, 25)} | {"31"}
REPORT_QTY, REPORT_USD = "3", "2"


def make_opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (AgriSus research scraper)")]
    return opener


def fetch(opener, url, data=None, timeout=600):
    for attempt in range(4):
        try:
            body = urllib.parse.urlencode(data).encode() if data else None
            with opener.open(url, body, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # gov portal is flaky; back off and retry
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def get_form(opener, page):
    s = fetch(opener, f"{BASE}/{page}")
    token = re.search(r'name="_token" value="([^"]+)"', s).group(1)
    years = sorted(set(re.findall(r'<option value="(\d{4})"', s)))
    # Import and export forms use slightly different field names; discover them.
    names = set(re.findall(r'name="([^"]+)"', s))
    pick = lambda pat: next(n for n in names if re.fullmatch(pat, n))
    fields = {
        "year": pick(r"Eidb_?YearCw[ei]"),
        "level": pick(r"Eidb_?ComLevelCw[ei]"),
        "report": pick(r"Eidb_?ReportCw[ei]"),
        "type": "comType" if "comType" in names else "commodityType",
    }
    return token, years, fields


def parse_table(s):
    tables = re.findall(r"<table.*?</table>", s, re.S)
    if not tables:
        return None, []
    # Some report variants omit closing </tr> tags, so split on row openings instead.
    rows = re.split(r"<tr[\s>]", tables[0])[1:]
    clean = lambda c: html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
    parsed = [[clean(c) for c in re.findall(r"<t[hd][\s>].*?</t[hd]>", r, re.S)] for r in rows]
    parsed = [p for p in parsed if p]
    return parsed[0], parsed[1:]


def num(x):
    try:
        return float(x.replace(",", "").strip())
    except ValueError:
        return None


def query(opener, page, year, report):
    """Return (fiscal_year_label, {hs: (desc, unit, value)}, share_sum_pct).

    Quantity report columns: S.No, HS, Commodity, Unit, <prev FY>, <FY>, %Growth
    US$ report columns:      S.No, HS, Commodity, <prev FY>, %Share, <FY>, %Share, %Growth
    """
    token, _, f = get_form(opener, page)
    data = {"_token": token, f["year"]: year, f["type"]: "all", f["level"]: "8", f["report"]: report}
    s = fetch(opener, f"{BASE}/{page}", data)
    header, rows = parse_table(s)
    if header is None:
        raise RuntimeError(f"no table for {page} {year} report={report}")
    is_usd = header[4].startswith("%Share")
    fy_col = 5
    out, share_sum = {}, 0.0
    for r in rows:
        if len(r) >= 7 and r[1].isdigit():
            unit = "" if is_usd else r[3]
            out[r[1]] = (r[2], unit, num(r[fy_col]))
            if is_usd:
                share_sum += num(r[6]) or 0.0
    return header[fy_col].replace(" ", ""), out, share_sum


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    opener = make_opener()
    flows = sys.argv[1:] or ["import", "export"]
    totals_path = os.path.join(OUT_DIR, "trade_year_totals.csv")
    totals = []
    if os.path.exists(totals_path):
        with open(totals_path) as tf:
            totals = [r for r in csv.DictReader(tf) if r["flow"] not in flows]
    for flow in flows:
        page = f"commodity_wise_{flow}"
        _, years, _ = get_form(opener, page)
        path = os.path.join(OUT_DIR, f"trade_hs8_{flow}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["flow", "fiscal_year", "hs_code", "hs2", "commodity", "unit",
                        "quantity", "value_usd_mn", "portal_dup_factor"])
            for y in years:
                fy, qty, _ = query(opener, page, y, REPORT_QTY)
                _, usd, share_sum = query(opener, page, y, REPORT_USD)
                # The portal occasionally double-counts a whole year (e.g. exports FY2023-24:
                # %Share sums to 200 and every line is exactly 2x). Detect and correct.
                factor = max(1, round(share_sum / 100.0))
                total_usd = sum(v[2] or 0 for v in usd.values())
                totals.append({"flow": flow, "fiscal_year": fy, "reported_total_usd_mn": round(total_usd, 2),
                               "share_sum_pct": round(share_sum, 2), "portal_dup_factor": factor,
                               "corrected_total_usd_mn": round(total_usd / factor, 2)})
                n = 0
                for code in sorted(set(qty) | set(usd)):
                    if code[:2] not in KEEP_HS2:
                        continue
                    desc, unit, q = qty.get(code, (usd[code][0], "", None))
                    v = usd[code][2] if code in usd else None
                    q = q / factor if q is not None else None
                    v = v / factor if v is not None else None
                    w.writerow([flow, fy, code, code[:2], desc, unit, q, v, factor])
                    n += 1
                print(f"{flow} {fy}: {n} agri HS8 lines (of {len(qty)}); total ${total_usd/factor/1000:.1f}B"
                      f" (dup factor {factor})", flush=True)
                f.flush()
    with open(totals_path, "w", newline="") as tf:
        w = csv.DictWriter(tf, fieldnames=list(totals[0]))
        w.writeheader()
        w.writerows(totals)


if __name__ == "__main__":
    main()
