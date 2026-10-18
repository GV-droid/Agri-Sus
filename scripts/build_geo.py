#!/usr/bin/env python3
"""Decode the LGD-keyed district TopoJSON into GeoJSON + centroids.

Source: yashveeeeeeer/india-geodata docs/maps/data/districts.topo.json (LGD district codes,
CC0). Outputs:
  data/processed/geo/districts_india.geojson (all districts, properties: district_code, name, state)
  data/processed/geo/district_centroids.csv  (all districts: district_code, lat, lon, area_km2)
Stdlib only.
"""
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/raw/geo/districts.topo.json"
OUT = ROOT / "data/processed/geo"


def decode_arcs(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append([round(x * sx + tx, 5), round(y * sy + ty, 5)])
        arcs.append(pts)
    return arcs


def ring(arcs, idxs):
    pts = []
    for i in idxs:
        a = arcs[i] if i >= 0 else arcs[~i][::-1]
        pts.extend(a if not pts else a[1:])
    return pts


def geometry(arcs, g):
    if g["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(arcs, r) for r in g["arcs"]]}
    if g["type"] == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[ring(arcs, r) for r in p] for p in g["arcs"]]}
    return None


def ring_area_centroid(r):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(r, r[1:]):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    a /= 2
    if a == 0:
        return 0, r[0][0], r[0][1]
    return a, cx / (6 * a), cy / (6 * a)


def centroid(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    A = X = Y = 0.0
    for p in polys:
        for k, r in enumerate(p):
            a, cx, cy = ring_area_centroid(r)
            a = abs(a) * (1 if k == 0 else -1)
            A += a
            X += cx * a
            Y += cy * a
    lat = Y / A
    km2 = A * (111.32 ** 2) * math.cos(math.radians(lat))
    return round(lat, 4), round(X / A, 4), round(km2, 1)


def main():
    topo = json.loads(SRC.read_text())
    arcs = decode_arcs(topo)
    OUT.mkdir(parents=True, exist_ok=True)
    feats, cents = [], []
    for g in topo["objects"]["districts"]["geometries"]:
        geom = geometry(arcs, g)
        if not geom:
            continue
        p = g["properties"]
        lat, lon, km2 = centroid(geom)
        cents.append({"district_code": p["lgd"], "district_name": p["name"], "state_name": p["state"],
                      "state_code": p["state_lgd"], "lat": lat, "lon": lon, "area_km2": km2})
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"district_code": p["lgd"], "district_name": p["name"],
                                     "state_name": p["state"]}})
    (OUT / "districts_india.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                          separators=(",", ":")))
    with (OUT / "district_centroids.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cents[0]))
        w.writeheader()
        w.writerows(cents)
    print(f"{len(feats)} district features, {len(cents)} centroids")


if __name__ == "__main__":
    main()
