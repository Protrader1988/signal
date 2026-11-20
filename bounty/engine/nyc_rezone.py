"""NYC upzoned-and-underbuilt scan (BOUNTY).

Finds private lots in the Bronx, Astoria/LIC (QN CD1), Williamsburg/Greenpoint
(BK CD1) and DUMBO/Downtown Brooklyn (BK CD2) where the zoning allows apartments
at medium or high density but the lot is vacant, parking, industrial, a
utility/transport use or a small commercial building using under 30% of the
residential floor area allowed.

Tags:
  REZONED INDUSTRIAL  an industrial use on a lot now mapped residential, or a
                      lot in a mixed M/R (MX) district
  UAP                 residential FAR of 2.4 or more (R6-equivalent or denser),
                      where City of Yes' Universal Affordability Preference adds
                      floor area for permanently affordable units
  TRANSIT             within half a mile of a subway station

Source: NYC MapPLUTO (DCP) and MTA subway stations (data.ny.gov). Units are a
range: 900 gross sf per apartment (low) to the 680 sf dwelling unit factor
(high). Check DOB NOW for an active new-building permit before outreach:
PLUTO lags construction.
"""
import json, math, os, re, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timezone

PLUTO = "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/MAPPLUTO/FeatureServer/0/query"
STATIONS = "https://data.ny.gov/resource/39hk-dx4f.json?$limit=1000"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "nyc_rezone.json")
UA = {"User-Agent": "bounty-terminal/1.0"}
AREAS = {"BX": "Bronx", 401: "Astoria / LIC", 301: "Williamsburg / Greenpoint", 302: "DUMBO / Downtown Brooklyn"}
USES = {"05": "commercial", "06": "industrial", "07": "transport/utility", "10": "parking", "11": "vacant"}
MAX_ROWS = 800
WHERE = ("(Borough='BX' OR CD IN (401,301,302)) AND LandUse IN ('05','06','07','10','11') "
         "AND ResidFAR>=2.4 AND LotArea>=2500 AND BldgArea < 0.3*LotArea*ResidFAR "
         "AND (OwnerType IS NULL OR OwnerType='P')")
FIELDS = "Borough,CD,BBL,Address,ZoneDist1,Overlay1,SPDist1,LandUse,LotArea,BldgArea,ResidFAR,OwnerName,Latitude,Longitude"
FLAG_OWNER = re.compile(r"HOUSING|HDFC|DEVELOPMENT FUND|DEV FUND|PHASE", re.I)


def get(url, tries=3, timeout=60):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
        except Exception as e:
            print("  retry", i + 1, e)
            time.sleep(3)
    raise RuntimeError("unreachable: " + url[:80])


def miles(a, b, c, d):
    r = 3958.8
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main():
    rows, off = [], 0
    while True:
        q = urllib.parse.urlencode({"where": WHERE, "outFields": FIELDS, "returnGeometry": "false", "f": "json",
                                    "resultOffset": off, "resultRecordCount": 2000, "orderByFields": "BBL"})
        d = get(PLUTO + "?" + q)
        if d.get("error"):
            raise RuntimeError(d["error"])
        rows += [f["attributes"] for f in d.get("features", [])]
        if not d.get("exceededTransferLimit"):
            break
        off += 2000
    try:
        st = [(float(s["gtfs_latitude"]), float(s["gtfs_longitude"]), s.get("stop_name", ""))
              for s in get(STATIONS) if s.get("gtfs_latitude")]
    except Exception:
        traceback.print_exc()
        st = []
    out = []
    for a in rows:
        fa = round(a["LotArea"] * a["ResidFAR"])
        lo, hi = fa // 900, fa // 680
        if lo < 10:
            continue
        z = a["ZoneDist1"] or ""
        tags = []
        if (z.startswith("M") and "/" in z) or (a["LandUse"] == "06" and z[:1] in "RC"):
            tags.append("REZONED INDUSTRIAL")
        if a["ResidFAR"] >= 2.4:
            tags.append("UAP")
        near = None
        if st and a.get("Latitude"):
            near = min(((miles(a["Latitude"], a["Longitude"], s[0], s[1]), s[2]) for s in st), key=lambda t: t[0])
            if near[0] <= 0.5:
                tags.append("TRANSIT")
        area = AREAS["BX"] if a["Borough"] == "BX" else AREAS.get(a["CD"], str(a["CD"]))
        own = a.get("OwnerName") or ""
        out.append({
            "area": area, "muni": area, "est_units": f"{lo}-{hi}", "bbl": str(int(a["BBL"])) if a.get("BBL") else "", "address": (a["Address"] or "").strip(),
            "zone": z + ("/" + a["Overlay1"] if a.get("Overlay1") else ""), "special": a.get("SPDist1") or "",
            "use": USES.get(a["LandUse"], a["LandUse"]), "lot_sf": round(a["LotArea"]), "bldg_sf": round(a["BldgArea"] or 0),
            "far": a["ResidFAR"], "floor_area": fa, "units_lo": lo, "units_hi": hi, "units": hi,
            "tags": tags, "station": near[1] if near and near[0] <= 0.5 else "",
            "station_mi": round(near[0], 2) if near else None, "owner": own,
            "check": "Owner name suggests a project may already be underway" if FLAG_OWNER.search(own) else "",
        })
    rank = lambda r: (("REZONED INDUSTRIAL" in r["tags"]) + ("TRANSIT" in r["tags"]) + (r["use"] in ("vacant", "parking")), r["units_hi"])
    out.sort(key=rank, reverse=True)
    counts = {}
    for r in out:
        c = counts.setdefault(r["area"], {"lots": 0, "rezoned_industrial": 0, "transit": 0})
        c["lots"] += 1
        c["rezoned_industrial"] += "REZONED INDUSTRIAL" in r["tags"]
        c["transit"] += "TRANSIT" in r["tags"]
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(), "method": __doc__.strip(),
           "counts": counts, "total": len(out), "rows": out[:MAX_ROWS]}
    json.dump(res, open(OUT, "w"), indent=1)
    print("nyc rezone:", len(out), counts)


if __name__ == "__main__":
    main()
