"""
BOUNTY — Connecticut scanner (Gold Coast + Stamford/New Haven). Daily on Actions.

Data: CT 2025 Parcel & CAMA data (data.ct.gov rny9-6ak2), Socrata. REAL data.

THE CT EDGE — CGS 8-30g: in any town where <10% of housing is affordable, a
30%-affordable set-aside project can OVERRIDE local zoning denial (burden of
proof flips to the town). That's not a subsidy — it's the entitlement lever
that makes otherwise-unzonable multifamily approvable in wealthy towns. This
engine flags every parcel's 8-30g status so you see where the override applies.

HONESTY TIER: SCREEN. CT parcel data has no standardized zoning/FAR. Unit counts
are published density ASSUMPTIONS (higher where 8-30g override is available),
to be verified against town zoning / an 8-30g affordability plan. Owner names
are from the roll — your outreach list.

Output: bounty/data/ct_deals.json (+ appends to history.json)
"""
import json, os, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
BASE = "https://data.ct.gov/resource/rny9-6ak2.json"

# 8-30g status by town — % affordable approx from DOH Affordable Housing Appeals
# Listing (verify current list + moratoria before relying on it).
TOWNS = {
    "GREENWICH":  {"pct": 5.3,  "applies": True},
    "WESTPORT":   {"pct": 3.5,  "applies": True},
    "RIDGEFIELD": {"pct": 3.4,  "applies": True},
    "NEW CANAAN": {"pct": 3.0,  "applies": True},
    "DARIEN":     {"pct": 3.4,  "applies": True, "note": "has pursued moratoria before — verify current status"},
    "STAMFORD":   {"pct": 13.5, "applies": False, "reason": "≥10% affordable — 8-30g exempt"},
    "NEW HAVEN":  {"pct": 31.0, "applies": False, "reason": "≥10% affordable — 8-30g exempt"},
}
ASSUMPTIONS = {
    "density_8_30g_uac": 16, "density_base_uac": 8,
    "density_note": "Units assume ~16/acre WHERE 8-30g override is available (set-aside can exceed base zoning), else ~8/acre. Verify with an 8-30g affordability plan / town zoning.",
    "min_acres": 0.35, "max_acres": 15.0, "min_value_per_acre": 40000,
    "value_floor_note": "CT Gold Coast land is expensive; parcels under ~$40k/acre are almost certainly unbuildable (wetlands, ledge, no sewer).",
    "note": "Screen tier on real CT CAMA data. The 8-30g flag is the strategic signal — not a substitute for a zoning/affordability-plan review.",
}
EXCLUDE_OWNERS = ("TOWN OF","CITY OF","STATE OF","UNITED STATES","USA","U S ","COUNTY OF",
                  "BOARD OF ED","SCHOOL","FIRE DISTRICT","WATER","HOUSING AUTHORITY","METRO",
                  "CEMETERY","CHURCH","DIOCESE","CONGREGATION","LAND TRUST","CONSERVAN","PRESERVE",
                  "AUDUBON","NATURE","YMCA","UTILITY","EVERSOURCE","UNITED ILLUM")
# Undevelopable land uses — excluded regardless of 8-30g (you can't build on a wetland).
EXCLUDE_USE = ("WETLAND","MARSH","FLOOD","CONSERV","LEDGE","UNDEVELOP","NOT BUILD","OPEN SPACE",
               "RIGHT OF WAY","ROW","RIGHT-OF-WAY","SLIVER","GORE","WATER","POND","STREAM","UTILITY",
               "EASEMENT","PARK","CEMETERY","GOLF","BEACH","TIDAL","WOODLAND ASSESS")

def get_json(url, timeout=90):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())

def n(x, d=0.0):
    try:
        v = float(str(x).replace(",", "").replace("$", "")); return v if v == v else d
    except Exception: return d

def discover():
    row = get_json(BASE + "?$limit=1")[0]
    keys = list(row.keys())
    print("CT CAMA fields:", keys)
    def find(*prefs):
        for p in prefs:
            for k in keys:
                if p in k: return k
        return None
    F = {
        "town": find("property_city", "municipal", "town_name") or find("town_id"),
        "use_code": find("state_use") or find("use_code"),
        "use_desc": find("state_use_description", "use_desc", "property_use"),
        "zone": find("zone") ,
        "zone_desc": find("zone_desc", "zone_description"),
        "acres": find("land_acres", "acre", "acreage"),
        "land_val": find("appraised_land", "assessed_land"),
        "total_val": find("appraised_total", "assessed_total"),
        "owner": find("owner"),
        "addr": find("location", "property_address", "situs"),
        "mail": find("mailing_address", "mailing"),
        "mail_city": find("mailing_city"),
        "mail_state": find("mailing_state"),
        "mail_zip": find("mailing_zip"),
        "sale_price": find("sale_price"),
        "sale_date": find("sale_date"),
    }
    # ensure zone != zone_description collision
    if F["zone"] and F["zone_desc"] and F["zone"] == F["zone_desc"]:
        F["zone"] = "zone"
    print("field map:", {k: v for k, v in F.items() if v})
    return F, keys

def fetch_town(F, town):
    if not F["town"]:
        raise RuntimeError("no town field")
    where = f"upper({F['town']})='{town}'"
    sel = [v for v in F.values() if v]
    out, offset = [], 0
    while offset < 60000:
        url = (BASE + f"?$select={urllib.parse.quote(','.join(dict.fromkeys(sel)))}"
               f"&$where={urllib.parse.quote(where)}&$limit=10000&$offset={offset}")
        try:
            page = get_json(url)
        except Exception as e:
            print(f"  {town} page@{offset} failed: {e}"); break
        out += page
        if len(page) < 10000: break
        offset += 10000
    return out

def main():
    os.makedirs("bounty/data", exist_ok=True)
    today = date.today().isoformat()
    A = ASSUMPTIONS
    F, keys = discover()
    deals = []
    town_counts = {}
    for town, meta in TOWNS.items():
        try:
            rows = fetch_town(F, town)
        except Exception as e:
            print(f"{town} failed: {e}"); rows = []
        town_counts[town] = len(rows)
        print(f"{town}: {len(rows)} parcels")
        applies = meta["applies"]
        uac = A["density_8_30g_uac"] if applies else A["density_base_uac"]
        for r in rows:
            acres = n(r.get(F["acres"])) if F["acres"] else 0
            if not (A["min_acres"] <= acres <= A["max_acres"]): continue
            desc = str(r.get(F["use_desc"], "") or "").upper() if F["use_desc"] else ""
            zdesc = str(r.get(F["zone_desc"], "") or "").upper() if F["zone_desc"] else ""
            if any(k in desc for k in EXCLUDE_USE): continue   # wetlands/undevelopable — hard no
            land_val = n(r.get(F["land_val"])) if F["land_val"] else 0
            total_val = n(r.get(F["total_val"])) if F["total_val"] else 0
            val = total_val if total_val > 0 else land_val
            if val <= 0: continue
            imp_ratio = (total_val - land_val)/total_val if total_val > 0 else 0
            vacant = ("VAC" in desc) or (imp_ratio < 0.1 and land_val > 0)
            developable = vacant or (imp_ratio <= 0.30 and land_val > 0)
            if not developable: continue
            per_acre = val/acres if acres > 0 else 0
            if per_acre < A["min_value_per_acre"]: continue
            owner = str(r.get(F["owner"], "") or "").title() if F["owner"] else ""
            if any(k in owner.upper() for k in EXCLUDE_OWNERS): continue
            mail = " ".join(str(r.get(F[k], "") or "") for k in ("mail","mail_city","mail_state","mail_zip") if F.get(k)).strip().title()
            est_units = int(acres * uac)
            if est_units < 6: continue
            per_unit = val/est_units if est_units else 9e9
            addr = str(r.get(F["addr"], "") or "").title() if F["addr"] else ""
            play_type = "vacant land" if vacant else "teardown / assemblage (occupied)"
            zone_label = (r.get(F["zone_desc"]) if F["zone_desc"] and r.get(F["zone_desc"]) else
                          (r.get(F["zone"]) if F["zone"] else None))
            # score: 8-30g availability dominates, then vacant land, basis, size
            s_830 = 40 if applies else 8
            s_cost = max(0, min(1, (400000 - per_unit)/380000)) * 22
            s_vac = 20 if vacant else 4
            s_size = max(0, min(1, est_units/40)) * 12
            score = round(s_830 + s_cost + s_vac + s_size, 1)
            deals.append({
                "region": "CT", "town": town.title(),
                "address": addr or "(unaddressed parcel)",
                "use_desc": r.get(F["use_desc"]) if F["use_desc"] else None,
                "play_type": play_type,
                "zone": zone_label,
                "zone_desc": r.get(F["zone_desc"]) if F["zone_desc"] else None,
                "last_sale_price": int(n(r.get(F["sale_price"]))) if F["sale_price"] and n(r.get(F["sale_price"]))>0 else None,
                "last_sale_date": (str(r.get(F["sale_date"]))[:10] if F["sale_date"] and r.get(F["sale_date"]) else None),
                "acres": round(acres, 2), "vacant": vacant,
                "improvement_ratio": round(imp_ratio, 2),
                "eight_30g_applies": applies,
                "town_affordable_pct": meta["pct"],
                "eight_30g_note": meta.get("reason") or meta.get("note") or
                    "Under 10% affordable — a 30%-set-aside project can override local zoning denial.",
                "assumed_density_uac": uac,
                "est_units": est_units,
                "assessed_total": int(total_val), "est_value": int(val),
                "value_per_acre": int(per_acre), "value_per_est_unit": int(per_unit),
                "owner": owner, "owner_mailing": mail, "score": score, "tier": "screen",
                "verify": "Confirm zoning + file an 8-30g affordability plan; density is an assumption.",
            })
    # dedupe by town+address (CT roll has split-parcel duplicates)
    seen = set(); dd = []
    for d in sorted(deals, key=lambda d: d["score"], reverse=True):
        k = (d["town"], d["address"])
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
        k = f"CT|{d['town']}|{d['address']}"
        if k not in hist: hist[k] = {"first_seen": today}; new_count += 1
        d["first_seen"] = hist[k]["first_seen"]; d["is_new"] = hist[k]["first_seen"] == today
    json.dump(hist, open(hp, "w"))
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "region": "Connecticut — Gold Coast + Stamford/New Haven",
           "tier_note": "SCREEN tier: real CT CAMA parcel data. Density is a published assumption; the 8-30g flag is the strategic edge, not a zoning determination.",
           "eight_30g_rule": "A CT town is 8-30g-exempt only if ≥10% of its housing is affordable or it holds a moratorium. Under that, a 30%-affordable set-aside can override local zoning denial.",
           "towns": {t: {"affordable_pct": m["pct"], "8_30g_applies": m["applies"]} for t, m in TOWNS.items()},
           "counts": {"by_town": town_counts, "qualified": len(deals), "new_today": new_count,
                      "in_8_30g_towns": sum(1 for d in deals if d["eight_30g_applies"]),
                      "vacant": sum(1 for d in deals if d["vacant"])},
           "assumptions": ASSUMPTIONS,
           "deals": deals[:250]}
    err = "bounty/data/CT_ERROR.txt"
    if os.path.exists(err): os.remove(err)
    json.dump(out, open("bounty/data/ct_deals.json", "w"), indent=2)
    print(f"qualified {len(deals)} | 8-30g towns {out['counts']['in_8_30g_towns']} | vacant {out['counts']['vacant']} | new {new_count}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/CT_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
