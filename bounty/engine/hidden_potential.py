"""
BOUNTY: Hidden Potential scanner (Westchester County GIS). Runs free on GitHub Actions, no AI calls.

What it finds
  1. SUBDIVIDE  - lots in one-family / two-family zones big enough to split into two or more
                  conforming lots (lot area AND lot width checked against the zone minimums).
  2. UPZONED    - houses, small multifamily and vacant lots sitting in apartment zones
                  (A, BA, M, MG) whose floor area / density rules allow far more units
                  than what is there now (teardown or add-on plays).
  3. ON MARKET  - any of the above that is currently listed for sale (joined from
                  bounty/data/onmarket_watch.json, which a weekly listings check refreshes).

Data
  Parcels : Westchester County MunicipalTaxParcels_Query MapServer (layer per municipality)
  Zoning  : Westchester County DataHub zoning layer 207 (county compilation; verify on the
            municipal map before offering)
  Rules   : the municipality's own dimensional schedule, transcribed below with its source.
            A municipality is only scanned once its rules are verified from the official code.

Outputs bounty/data/hidden_potential.json
"""
import json, math, os, sys, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "hidden_potential.json")
WATCH = os.path.join(ROOT, "data", "onmarket_watch.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
GIS = "https://giswww.westchestergov.com/ArcGIS/rest/services"
ZONING = GIS + "/DataHub_EnvironmentandPlanning/MapServer/207/query"
PARCELS = GIS + "/MunicipalTaxParcels_Query/MapServer/{layer}/query"

# Residential and vacant property classes worth looking at (NYS RPS codes)
CLASSES = ["210", "215", "220", "230", "280", "311", "312", "314", "330", "331", "340", "411", "482", "483", "484", "620", "621"]

# ---- Verified municipal rules -------------------------------------------------------------
# Yonkers: Chapter 43, Table 43-3 Schedule of Dimensional Regulations for Residential Uses
# (amended through Ord. No. 4-2025), read from the official attachment on 2026-11-19.
#   one/two-family: min lot area, min lot width
#   multifamily: FAR, lot area per unit (None = floor area governs), min lot area / width
# Parking: Table 43-4, apartments 1 per unit + 0.33 per bedroom.
MUNIS = {
    "YON": {
        "name": "Yonkers", "layer": 483,
        "source": "Yonkers Code Ch. 43, Table 43-3 (thru Ord. 4-2025); parking Table 43-4",
        "subdivide": {  # zone: (min lot area sf, min lot width ft)
            "S-200": (20000, 200), "S-100": (10000, 100), "S-75": (7500, 75),
            "S-60": (6000, 60), "S-50": (5000, 50), "T": (5000, 50),
        },
        "multifamily": {  # zone: far, lot area per unit, min lot sf, min width ft, height note
            "A":  {"far": 3.00, "per_unit": None, "min_lot": 5000,  "min_w": 50,  "height": "1.5 x width of fronting street"},
            "BA": {"far": 3.00, "per_unit": None, "min_lot": 5000,  "min_w": 50,  "height": "1.5 x width of fronting street"},
            "M":  {"far": 1.25, "per_unit": 800,  "min_lot": 5000,  "min_w": 50,  "height": "65 ft"},
            "MG": {"far": 1.20, "per_unit": 1000, "min_lot": 10000, "min_w": 100, "height": "3 stories / 35 ft"},
        },
        "coverage_mf": 0.40,
        "parking_per_unit": 1.4,   # 1 + 0.33 x ~1.2 bedrooms average
    },
}
GSF_PER_UNIT = 900          # gross building area per apartment incl. halls, stairs, walls
SF_PER_STALL = 330          # surface stall incl. drive aisle
MAX_OFF_MARKET = 400        # keep the board file small


def post(url, data, timeout=120, tries=3):
    body = urllib.parse.urlencode(data).encode()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except Exception as e:  # network hiccup: back off and retry
            last = e
            time.sleep(3 * (i + 1))
    raise last


def in_poly(pt, rings):
    x, y = pt
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
    return inside


def dims_ft(ring):
    """Oriented bounding box of a lat/lng ring, in feet: (short side, long side)."""
    lat0 = ring[0][1]
    kx, ky = math.cos(math.radians(lat0)) * 364000, 364000
    pts = [(p[0] * kx, p[1] * ky) for p in ring]
    best = None
    for a in range(0, 180, 2):
        t = math.radians(a)
        c, s = math.cos(t), math.sin(t)
        us = [x * c + y * s for x, y in pts]
        vs = [-x * s + y * c for x, y in pts]
        w, h = max(us) - min(us), max(vs) - min(vs)
        if best is None or w * h < best[0]:
            best = (w * h, w, h)
    return round(min(best[1], best[2])), round(max(best[1], best[2]))


def centroid(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def load_watch():
    try:
        w = json.load(open(WATCH))
        return {e["printkey"]: e for e in w.get("listings", []) if e.get("printkey")}
    except Exception:
        return {}


def analyze(rule, zone, a, ring):
    sf = round(a.get("SHAPE.AREA") or 0)
    short, long_ = dims_ft(ring)
    base = {
        "printkey": a.get("PRINTKEY"), "address": (a.get("PROPADDRESS") or "").strip(),
        "class": a.get("PROPERTYCLASS"), "zone": zone, "lot_sf": sf, "dims_ft": f"{short}x{long_}",
        "owner": (a.get("OWNERNAME") or "").strip(),
        "owner_mailing": ", ".join(x for x in [(a.get("OWNERADDRESS") or "").strip(), (a.get("OWNERCITY") or "").strip(),
                                                 (a.get("OWNERSTATE") or "").strip(), (a.get("OWNERZIP") or "").strip()] if x),
        "lat": round(centroid(ring)[1], 6), "lng": round(centroid(ring)[0], 6),
    }
    if zone in rule["subdivide"]:
        min_a, min_w = rule["subdivide"][zone]
        by_area = sf // min_a
        if short >= 2 * min_w:
            fit, by_w = "frontage likely works", short // min_w
        elif long_ >= 2 * min_w:
            fit, by_w = "check which side fronts the street", long_ // min_w
        else:
            return None  # deep narrow lot: would need a flag lot, skip
        lots = int(min(by_area, by_w))
        if lots < 2:
            return None
        base.update(play="subdivide", lots=lots, new_lots=lots - 1, fit=fit,
                    rule=f"{zone}: min {min_a:,} sf and {min_w} ft wide per lot",
                    units=lots)
        return base
    mf = rule["multifamily"].get(zone)
    if not mf or sf < mf["min_lot"] or long_ < mf["min_w"]:
        return None
    zfa = round(sf * mf["far"])
    u_far = zfa // GSF_PER_UNIT
    u_den = sf // mf["per_unit"] if mf["per_unit"] else u_far
    units = int(min(u_far, u_den))
    if units < 6:
        return None
    stalls = math.ceil(units * rule["parking_per_unit"])
    open_land = sf * (1 - rule["coverage_mf"])
    parking = "fits on site (surface)" if stalls * SF_PER_STALL <= open_land else "needs structured parking or a parking variance"
    base.update(play="multifamily", units=units, zfa=zfa, far=mf["far"],
                rule=f"{zone}: FAR {mf['far']}" + (f", {mf['per_unit']} sf of lot per unit" if mf["per_unit"] else ", no per-unit cap") +
                     f", {int(rule['coverage_mf']*100)}% coverage, height {mf['height']}",
                parking_spaces=stalls, parking=parking)
    return base


def scan(code, rule, watch):
    zones = list(rule["subdivide"]) + list(rule["multifamily"])
    zq = post(ZONING, {"where": f"MUN='{code}' AND ZONING IN ({','.join(repr(z) for z in zones)})",
                       "outFields": "ZONING", "returnGeometry": "true", "outSR": "4326", "f": "json"})
    polys = zq.get("features", [])
    print(f"{rule['name']}: {len(polys)} zone polygons")
    seen, rows = set(), []
    cls = "(" + ",".join(f"'{c}'" for c in CLASSES) + ")"
    for z in polys:
        zone = z["attributes"]["ZONING"]
        if zone in rule["subdivide"]:
            min_sf = 2 * rule["subdivide"][zone][0]
        else:
            min_sf = rule["multifamily"][zone]["min_lot"]
        geom = json.dumps({"rings": z["geometry"]["rings"], "spatialReference": {"wkid": 4326}})
        try:
            pr = post(PARCELS.format(layer=rule["layer"]), {
                "geometry": geom, "geometryType": "esriGeometryPolygon", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "where": f'"SHAPE.AREA">={min_sf} AND PROPERTYCLASS IN {cls}',
                "outFields": "PRINTKEY,PROPADDRESS,PROPERTYCLASS,SHAPE.AREA,OWNERNAME,OWNERADDRESS,OWNERCITY,OWNERSTATE,OWNERZIP",
                "returnGeometry": "true", "outSR": "4326", "f": "json"})
        except Exception as e:
            print("  parcel query failed for a", zone, "polygon:", e)
            continue
        for f in pr.get("features", []):
            a = f["attributes"]
            pk = a.get("PRINTKEY")
            if not pk or pk in seen or not f.get("geometry"):
                continue
            ring = f["geometry"]["rings"][0]
            if not in_poly(centroid(ring), z["geometry"]["rings"]):
                continue  # parcel mostly in a neighbouring zone
            seen.add(pk)
            r = analyze(rule, zone, a, ring)
            if not r:
                continue
            r["muni"] = rule["name"]
            w = watch.get(pk)
            if w:
                r["listing"] = {k: w.get(k) for k in ("price", "status", "url", "checked", "broker", "note")}
                if w.get("price") and r["units"]:
                    r["price_per_unit"] = round(w["price"] / r["units"])
            rows.append(r)
    return rows


def main():
    watch = load_watch()
    allrows, rules_out = [], {}
    for code, rule in MUNIS.items():
        rules_out[rule["name"]] = {"source": rule["source"], "subdivide": rule["subdivide"],
                                   "multifamily": rule["multifamily"]}
        try:
            allrows += scan(code, rule, watch)
        except Exception:
            traceback.print_exc()
    on = [r for r in allrows if r.get("listing")]
    on.sort(key=lambda r: (r.get("price_per_unit") or 9e9))
    off = [r for r in allrows if not r.get("listing")]
    off.sort(key=lambda r: (-(r["units"]), -r["lot_sf"]))
    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "region": "Westchester (municipalities with verified zoning rules)",
        "method": "County parcels x county zoning layer, checked against the municipality's own dimensional schedule. "
                  "Units: apartments at 900 gross sf each, capped by the zone's lot-area-per-unit rule; "
                  "subdivision: lot count limited by BOTH area and width. Survey, slopes, rock, sewer and "
                  "the municipal zoning map must be confirmed before an offer.",
        "rules": rules_out,
        "counts": {
            "candidates": len(allrows),
            "subdivide": sum(1 for r in allrows if r["play"] == "subdivide"),
            "multifamily": sum(1 for r in allrows if r["play"] == "multifamily"),
            "on_market": len(on),
        },
        "on_market": on,
        "off_market": off[:MAX_OFF_MARKET],
    }
    json.dump(out, open(OUT, "w"), indent=1)
    print("hidden potential:", out["counts"])


if __name__ == "__main__":
    main()
