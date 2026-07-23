"""
BOUNTY — NY suburbs scanner (Westchester · Putnam · Dutchess). Daily on Actions.

Data: NYS statewide tax parcel/assessment roll (data.ny.gov 7vem-aaz7). REAL data.

HONESTY TIER: unlike NYC (parcel-level zoning -> FAR math), suburban zoning is
not in any free standardized dataset. This is therefore a SCREEN, clearly
labeled: it finds development-shaped parcels (vacant developable land and
underimproved multifamily-class properties), anchors on assessed/market values,
and estimates capacity ONLY under a published density assumption that must be
verified against town zoning before any underwriting is trusted.

Outputs: bounty/data/nys_deals.json (+ appends to history.json)
"""
import json, os, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
BASE = "https://data.ny.gov/resource/7vem-aaz7.json"
COUNTIES = ["Westchester", "Putnam", "Dutchess"]

ASSUMPTIONS = {
    "density_units_per_acre": 20,
    "density_note": "Est. units assume ~20/acre multifamily density IF zoning allows or can be obtained — VERIFY at town hall; this is a screen, not an entitlement analysis.",
    "min_acres": 0.4, "max_acres": 12.0,
    "min_value_per_acre": 30000,
    "value_floor_note": "Parcels under ~$30k/acre market value are near-certainly unbuildable (wetlands, slopes, no utilities, conservation) — excluded to keep the screen honest.",
    "target_classes_vacant": ["311","312","314","330","331","340","341"],
    "target_classes_underused": ["411","410","400"],
    "underuse_improvement_ratio_max": 0.35,
    "note": "Suburban tier: parcel + assessment screen on real NYS data. No zoning-grade underwriting is claimed.",
}
# Government/institutional owners you cannot buy from on the open market.
# (NYC owns large WATERSHED tracts in Putnam/Dutchess — never developable.)
EXCLUDE_OWNERS = ("CITY OF NEW YORK","NEW YORK CITY","NYC ","STATE OF NEW YORK","PEOPLE OF THE STATE",
                  "COUNTY OF","CITY OF","TOWN OF","VILLAGE OF","UNITED STATES","USA","U S ",
                  "SCHOOL DISTRICT","BOARD OF ED","FIRE DISTRICT","HOUSING AUTHORITY","MTA",
                  "METRO-NORTH","METRO NORTH","CENTRAL HUDSON","CON ED","CONSOLIDATED EDISON",
                  "NYSEG","WATER DISTRICT","CEMETERY","CHURCH OF","DIOCESE","PARKS")

def get_json(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def n(x, d=0.0):
    try:
        v = float(str(x).replace(",","")); return v if v == v else d
    except Exception: return d

def discover():
    row = get_json(BASE + "?$limit=1")[0]
    keys = list(row.keys())
    print("NYS parcel fields:", keys)
    def find(*prefs):
        for p in prefs:
            for k in keys:
                if k.startswith(p): return k
        return None
    F = {
        "county": find("county_name"),
        "muni": find("municipality_name"),
        "class": find("property_class"),
        "class_desc": find("property_class_description"),
        "addr_no": find("parcel_address_number"),
        "addr_st": find("parcel_address_street"),
        "acres": find("acreage","acres","parcel_acre"),
        "front": find("front","frontage"),
        "depth": find("depth"),
        "land_av": find("land_assessed","assessed_land","land_av","av_land"),
        "total_av": find("total_assessed","assessed_total","total_av","av_total"),
        "market": find("full_market_value","market_value"),
        "owner": find("primary_owner","owner_first","owner_name","owner"),
        "owner2": find("primary_owner_last","owner_last"),
        "mail_addr": find("mailing_address_number","mailing_address","mail_address"),
        "mail_st": find("mailing_address_street"),
        "mail_city": find("mailing_address_city"),
        "mail_state": find("mailing_address_state"),
        "mail_zip": find("mailing_address_zip"),
        "roll_year": find("roll_year"),
        "school": find("school_district_name"),
        "lat": find("latitude"),
        "lng": find("longitude"),
    }
    print("field map:", {k: v for k, v in F.items() if v})
    yr = row.get(F["roll_year"]) if F["roll_year"] else None
    return F, keys, yr

def fetch_county(F, county, classes, roll_year):
    cls = ",".join(f"'{c}'" for c in classes)
    where = f"{F['county']}='{county}' and {F['class']} in({cls})"
    if roll_year and F["roll_year"]:
        where += f" and {F['roll_year']}='{roll_year}'"
    sel_fields = [v for v in F.values() if v]
    out, offset = [], 0
    while offset < 40000:
        url = (BASE + f"?$select={urllib.parse.quote(','.join(dict.fromkeys(sel_fields)))}"
               f"&$where={urllib.parse.quote(where)}&$limit=8000&$offset={offset}")
        page = get_json(url)
        out += page
        if len(page) < 8000: break
        offset += 8000
    return out

def main():
    os.makedirs("bounty/data", exist_ok=True)
    today = date.today().isoformat()
    A = ASSUMPTIONS
    F, keys, roll_year = discover()
    # try latest roll year; if the sample row's year is stale that's fine — same vintage across rows
    all_classes = A["target_classes_vacant"] + A["target_classes_underused"]
    deals = []
    county_counts = {}
    for county in COUNTIES:
        try:
            rows = fetch_county(F, county, all_classes, roll_year)
        except Exception as e:
            print(f"{county} fetch failed: {e}"); rows = []
        county_counts[county] = len(rows)
        print(f"{county}: {len(rows)} candidate parcels (roll {roll_year})")
        for r in rows:
            acres = n(r.get(F["acres"])) if F["acres"] else 0
            if acres <= 0 and F["front"] and F["depth"]:
                acres = (n(r.get(F["front"])) * n(r.get(F["depth"]))) / 43560.0
            if not (A["min_acres"] <= acres <= A["max_acres"]): continue
            cls = str(r.get(F["class"], ""))
            vacant = cls in A["target_classes_vacant"]
            land_av = n(r.get(F["land_av"])) if F["land_av"] else 0
            tot_av = n(r.get(F["total_av"])) if F["total_av"] else 0
            market = n(r.get(F["market"])) if F["market"] else 0
            if tot_av <= 0 and market <= 0: continue
            imp_ratio = (tot_av - land_av)/tot_av if tot_av > 0 else 0
            if not vacant and imp_ratio > A["underuse_improvement_ratio_max"]: continue
            est_units = int(acres * A["density_units_per_acre"])
            if est_units < 8: continue
            val = market if market > 0 else tot_av * 1.0
            per_acre = val/acres if acres > 0 else 0
            if per_acre < A["min_value_per_acre"]: continue   # unbuildable-land floor
            addr = " ".join(str(r.get(F[k], "") or "") for k in ("addr_no","addr_st")).strip().title()
            owner = str(r.get(F["owner"], "") or "").title() if F["owner"] else ""
            if F["owner2"] and r.get(F["owner2"]):
                owner = (owner + " " + str(r.get(F["owner2"])).title()).strip()
            if any(k in owner.upper() for k in EXCLUDE_OWNERS): continue
            # score: vacant preferred, sane land basis per est-unit, size, priority county
            per_unit = val/est_units if est_units else 9e9
            s_type = 26 if vacant else 16
            s_cost = max(0, min(1, (150000 - per_unit)/130000)) * 34
            s_size = max(0, min(1, est_units/40)) * 18
            s_val  = 10 if val < 2_000_000 else (6 if val < 3_000_000 else 2)
            s_cty  = {"Westchester": 12, "Putnam": 6, "Dutchess": 6}.get(county, 0)
            deals.append({
                "region": county, "muni": str(r.get(F["muni"], "") or "").title(),
                "address": addr or "(unaddressed parcel)",
                "class": cls, "class_desc": r.get(F["class_desc"]),
                "acres": round(acres, 2), "vacant": vacant,
                "improvement_ratio": round(imp_ratio, 2),
                "est_units_at_assumed_density": est_units,
                "assessed_total": int(tot_av), "est_market_value": int(val),
                "value_per_acre": int(per_acre), "value_per_est_unit": int(per_unit),
                "owner": owner,
                "owner_mailing": " ".join(str(r.get(F[k], "") or "") for k in ("mail_addr","mail_st","mail_city","mail_state","mail_zip") if F.get(k)).strip().title(),
                "school_district": r.get(F["school"]),
                "lat": (n(r.get(F["lat"]), None) if F.get("lat") else None),
                "lng": (n(r.get(F["lng"]), None) if F.get("lng") else None),
                "score": round(s_type + s_cost + s_size + s_val + s_cty, 1),
                "tier": "screen",
                "verify": "Confirm zoning/density with the municipality before underwriting.",
            })
    deals.sort(key=lambda d: d["score"], reverse=True)
    # history / new flags (shared history file, keyed by county+address)
    hp = "bounty/data/history.json"
    hist = {}
    if os.path.exists(hp):
        try: hist = json.load(open(hp))
        except Exception: hist = {}
    new_count = 0
    for d in deals:
        k = f"NYS|{d['region']}|{d['address']}|{d['muni']}"
        if k not in hist: hist[k] = {"first_seen": today}; new_count += 1
        d["first_seen"] = hist[k]["first_seen"]; d["is_new"] = hist[k]["first_seen"] == today
    json.dump(hist, open(hp, "w"))
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "region": "NY Suburbs — Westchester, Putnam, Dutchess",
           "tier_note": "SCREEN tier: real NYS parcel/assessment data; density and value figures are published assumptions pending town-hall zoning verification. Not underwritten like NYC.",
           "roll_year": roll_year,
           "counts": {"by_county": county_counts, "qualified": len(deals), "new_today": new_count,
                      "vacant": sum(1 for d in deals if d["vacant"])},
           "assumptions": ASSUMPTIONS,
           "deals": deals[:250]}
    err = "bounty/data/NYS_ERROR.txt"
    if os.path.exists(err): os.remove(err)
    json.dump(out, open("bounty/data/nys_deals.json", "w"), indent=2)
    print(f"qualified {len(deals)} | new {new_count} | emitted {min(len(deals),250)}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/NYS_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
