"""
BOUNTY — Northern NJ scanner (Bergen/Hudson Gold Coast + GWB corridor). Daily.

Data: NJGIN Parcels_Composite (ArcGIS) — MOD-IV assessments joined to parcels.
REAL data: OWNER_NAME, PROP_CLASS, LAND_VAL, IMPRVT_VAL, NET_VALUE, CALC_ACRE.

THE NJ EDGE: the workhorse here isn't a zoning override — it's the
LONG-TERM TAX EXEMPTION PILOT (N.J.S.A. 40A:20). Once a site sits in a municipal
"Area in Need of Redevelopment," the town can convey it to a designated
redeveloper (no public bid) with a 30-year PILOT (~10-15% of revenue instead of
full taxes). So this engine prioritizes VACANT (class 1) and
COMMERCIAL/INDUSTRIAL (4A/4B) parcels — the classes that become redevelopment
sites — and flags each as a PILOT/redevelopment candidate to verify against the
town's redevelopment map.

HONESTY TIER: SCREEN. No standardized zoning in the parcel data; unit counts are
a published density assumption to verify per town. NJ assessed values run at
town-specific ratios (not market) — labeled as such. Owner names = outreach list.

Output: bounty/data/nj_deals.json (+ appends to history.json)
"""
import json, os, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
LAYER = ("https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/"
         "Parcels_Composite_NJ_WM/FeatureServer/0/query")

# Bergen + Hudson waterfront / GWB-corridor targets.
TOWNS = [
    ("FORT LEE", "BERGEN", 40), ("EDGEWATER", "BERGEN", 45), ("ENGLEWOOD", "BERGEN", 25),
    ("ENGLEWOOD CLIFFS", "BERGEN", 20), ("CLIFFSIDE PARK", "BERGEN", 45), ("FAIRVIEW", "BERGEN", 35),
    ("PALISADES PARK", "BERGEN", 35), ("LEONIA", "BERGEN", 20), ("RIDGEFIELD", "BERGEN", 25),
    ("HACKENSACK", "BERGEN", 30), ("TEANECK", "BERGEN", 20), ("BOGOTA", "BERGEN", 20),
    ("NORTH BERGEN", "HUDSON", 40), ("WEST NEW YORK", "HUDSON", 55), ("UNION CITY", "HUDSON", 55),
    ("WEEHAWKEN", "HUDSON", 45), ("GUTTENBERG", "HUDSON", 55),
]
CLASSES = ("1", "4A", "4B")   # vacant, commercial, industrial (dev/redev sites)
ASSUMPTIONS = {
    "density_note": "Units assume a per-town multifamily density (waterfront high-rise towns higher). VERIFY zoning/redevelopment plan — NJ waterfront zoning ranges from low-rise to high-rise by block.",
    "min_acres": 0.15, "max_acres": 20.0, "min_assessed_per_acre": 150000,
    "value_note": "NJ NET_VALUE is ASSESSED value at each town's equalization ratio — NOT market. Useful as a relative within-town signal; multiply by the town ratio for market. Verify.",
    "note": "Screen tier on real NJ MOD-IV parcel data. Redevelopment/PILOT flag is strategic — verify against the municipal redevelopment map.",
}
EXCLUDE_OWNERS = ("BORO OF","BOROUGH OF","TOWNSHIP OF","CITY OF","TOWN OF","COUNTY OF","STATE OF",
                  "NJ ","N J ","N.J.","UNITED STATES","USA","U S ","BOARD OF ED","SCHOOL","HOUSING AUTH",
                  "NJ TRANSIT","TRANSIT","PORT AUTH","PSE&G","PSEG","PUBLIC SERVICE","UTILITY","WATER",
                  "CEMETERY","CHURCH","DIOCESE","TEMPLE","CONGREGATION","HOSPITAL","REDEVELOPMENT AGENCY")

def get_json(url, timeout=90):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())

def n(x, d=0.0):
    try:
        v = float(str(x).replace(",", "").replace("$", "")); return v if v == v else d
    except Exception: return d

FIELDS = "PAMS_PIN,MUN_NAME,COUNTY,PROP_CLASS,PROP_LOC,OWNER_NAME,ST_ADDRESS,LAND_VAL,IMPRVT_VAL,NET_VALUE,CALC_ACRE,LAND_DESC,BLDG_DESC"

def fetch_town(town, county):
    cls = ",".join(f"'{c}'" for c in CLASSES)
    where = f"COUNTY='{county}' AND MUN_NAME LIKE '%{town}%' AND PROP_CLASS IN ({cls})"
    out, offset = [], 0
    while offset < 20000:
        params = {"where": where, "outFields": FIELDS, "returnGeometry": "false",
                  "f": "json", "resultRecordCount": 2000, "resultOffset": offset}
        try:
            d = get_json(LAYER + "?" + urllib.parse.urlencode(params))
        except Exception as e:
            print(f"  {town} @{offset} failed: {e}"); break
        feats = d.get("features", [])
        out += [f.get("attributes", {}) for f in feats]
        if not d.get("exceededTransferLimit") or not feats: break
        offset += len(feats)
    return out

def main():
    os.makedirs("bounty/data", exist_ok=True)
    today = date.today().isoformat()
    A = ASSUMPTIONS
    deals = []
    town_counts = {}
    for town, county, dens in TOWNS:
        rows = fetch_town(town, county)
        town_counts[town] = len(rows)
        print(f"{town} ({county}): {len(rows)} parcels")
        for r in rows:
            # confirm town (LIKE can over-match, e.g., RIDGEFIELD vs RIDGEFIELD PARK)
            mun = str(r.get("MUN_NAME", "") or "").upper()
            if town not in mun: continue
            acres = n(r.get("CALC_ACRE"))
            if not (A["min_acres"] <= acres <= A["max_acres"]): continue
            pclass = str(r.get("PROP_CLASS", "") or "").strip()
            land_val = n(r.get("LAND_VAL")); imp_val = n(r.get("IMPRVT_VAL"))
            net = n(r.get("NET_VALUE")) or (land_val + imp_val)
            if net <= 0: continue
            imp_ratio = imp_val/net if net > 0 else 0
            vacant = pclass == "1"
            # 4A/4B redevelopment candidate if land-heavy (underimproved)
            redev = pclass in ("4A", "4B")
            if not vacant and not (redev and imp_ratio <= 0.55): continue
            per_acre = net/acres if acres > 0 else 0
            if per_acre < A["min_assessed_per_acre"]: continue
            owner = str(r.get("OWNER_NAME", "") or "").title()
            if any(k in owner.upper() for k in EXCLUDE_OWNERS): continue
            est_units = int(acres * dens)
            if est_units < 6: continue
            per_unit = net/est_units if est_units else 9e9
            addr = str(r.get("PROP_LOC", "") or r.get("ST_ADDRESS", "") or "").title()
            play = "vacant land" if vacant else ("commercial redevelopment" if pclass == "4A" else "industrial redevelopment")
            # score: vacant + redevelopment-class sites, cheap assessed basis, size, county-agnostic
            s_type = 30 if vacant else 22
            s_cost = max(0, min(1, (250000 - per_unit)/230000)) * 26
            s_size = max(0, min(1, est_units/60)) * 18
            s_redev = 12 if redev else 0
            score = round(s_type + s_cost + s_size + s_redev, 1)
            deals.append({
                "region": "NJ", "town": town.title(), "county": county.title(),
                "address": addr or "(unaddressed parcel)",
                "prop_class": pclass, "land_desc": r.get("LAND_DESC"), "bldg_desc": r.get("BLDG_DESC"),
                "play_type": play, "vacant": vacant, "redevelopment_candidate": redev,
                "acres": round(acres, 2), "assumed_density_uac": dens,
                "improvement_ratio": round(imp_ratio, 2),
                "est_units": est_units,
                "assessed_net": int(net), "assessed_per_acre": int(per_acre),
                "assessed_per_est_unit": int(per_unit),
                "owner": owner, "pams_pin": r.get("PAMS_PIN"),
                "score": score, "tier": "screen",
                "pilot_note": "Verify against the municipal redevelopment map — an 'Area in Need of Redevelopment' designation unlocks a 40A:20 PILOT and direct conveyance.",
                "verify": "Assessed value ≠ market (apply town ratio); confirm zoning/redevelopment status.",
            })
    # dedupe by pin/address, sort
    seen = set(); dd = []
    for d in sorted(deals, key=lambda d: d["score"], reverse=True):
        k = d.get("pams_pin") or (d["town"], d["address"])
        if k in seen: continue
        seen.add(k); dd.append(d)
    deals = dd
    # history
    hp = "bounty/data/history.json"; hist = {}
    if os.path.exists(hp):
        try: hist = json.load(open(hp))
        except Exception: hist = {}
    new_count = 0
    for d in deals:
        k = f"NJ|{d['town']}|{d['address']}"
        if k not in hist: hist[k] = {"first_seen": today}; new_count += 1
        d["first_seen"] = hist[k]["first_seen"]; d["is_new"] = hist[k]["first_seen"] == today
    json.dump(hist, open(hp, "w"))
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "region": "Northern NJ — Bergen/Hudson Gold Coast + GWB corridor",
           "tier_note": "SCREEN tier: real NJ MOD-IV parcel data. Density is a published assumption; assessed values are not market. The redevelopment/PILOT flag is the strategic edge.",
           "counts": {"by_town": town_counts, "qualified": len(deals), "new_today": new_count,
                      "vacant": sum(1 for d in deals if d["vacant"]),
                      "redevelopment": sum(1 for d in deals if d["redevelopment_candidate"])},
           "assumptions": ASSUMPTIONS,
           "deals": deals[:250]}
    err = "bounty/data/NJ_ERROR.txt"
    if os.path.exists(err): os.remove(err)
    json.dump(out, open("bounty/data/nj_deals.json", "w"), indent=2)
    print(f"qualified {len(deals)} | vacant {out['counts']['vacant']} | redev {out['counts']['redevelopment']} | new {new_count}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/NJ_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
