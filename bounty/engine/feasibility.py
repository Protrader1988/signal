"""BOUNTY feasibility memos: land-use attorney + architect review for every listed site
and the strongest off-market targets.

Two layers, kept separate on purpose:
  1. FACTS AND MATH (deterministic): parcel data from the official GIS (NYC MapPLUTO,
     Westchester County parcels), street frontage measured edge by edge, the zoning
     district's floor area (NYC ZR 23-22 standard and UAP), verified municipal rules
     (Yonkers Table 43-3 / 43-4: FAR, lot area per unit, coverage, parking), approvals
     with their public sources (bounty/data/feasibility_sites.json).
  2. MEMO (Gemini, free tier, no web access): writes the attorney/architect memo from
     those facts only. It may not add approvals, dates, numbers or programs that are not
     in the facts; anything missing is listed as a diligence item.

Output: bounty/data/feasibility.json
"""
import hashlib, json, math, os, re, sys, time, traceback, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
PLUTO = "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/MAPPLUTO/FeatureServer/0/query"
MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
GSF, DUF = 900, 680
MAX_MEMOS = int(os.environ.get("FEAS_MAX_MEMOS", 40))

# NYC Zoning Resolution 23-22 (City of Yes, amended 12/5/2024): (standard FAR, qualifying affordable / UAP FAR)
NYC_FAR = {
    "R6": (2.20, 3.90), "R6A": (3.00, 3.90), "R6-1": (3.00, 3.90), "R7B": (3.00, 3.90), "R6B": (2.00, 2.40),
    "R6D": (2.50, 3.00), "R6-2": (2.50, 3.00), "R7A": (4.00, 5.01), "R7-1": (3.44, 5.01), "R7-2": (3.44, 5.01),
    "R7D": (4.66, 5.60), "R7X": (5.00, 6.00), "R7-3": (5.00, 6.00), "R8A": (6.02, 7.20), "R8X": (6.02, 7.20),
    "R8": (6.02, 7.20), "R8B": (4.00, 4.80), "R9A": (7.52, 9.02), "R9": (7.52, 9.02), "R9D": (9.00, 10.80),
    "R9X": (9.00, 10.80), "R9-1": (9.00, 10.80), "R10A": (10.00, 12.00), "R10X": (10.00, 12.00), "R10": (10.00, 12.00),
    "R5": (1.50, 2.00), "R5A": (1.50, 2.00), "R5B": (1.50, 2.00), "R5D": (2.00, 2.00), "R4": (1.00, 1.50),
    "R4A": (1.00, 1.50), "R4B": (1.00, 1.50), "R4-1": (1.00, 1.50),
}


def req(url, data=None, timeout=60, tries=3):
    last = None
    for i in range(tries):
        try:
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, data=body, headers=UA), timeout=timeout).read())
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def load(name, default=None):
    try:
        return json.load(open(os.path.join(DATA, name)))
    except Exception:
        return default


def units(fa):
    return (int(fa // GSF), int(fa // DUF)) if fa else (0, 0)


def nyc_facts(site):
    addr = site["address"].upper().replace(" ST", " STREET").replace(" AVE", " AVENUE").replace(" PL", " PLACE")
    q = {"where": f"Borough='{site.get('borough', 'BX')}' AND Address LIKE '{addr}%'",
         "outFields": "Address,BBL,ZoneDist1,ZoneDist2,Overlay1,SPDist1,LotArea,LotFront,LotDepth,BldgArea,UnitsRes,"
                      "NumFloors,YearBuilt,OwnerName,LandUse,LotType,ResidFAR,HistDist,Landmark,Latitude,Longitude",
         "returnGeometry": "false", "f": "json"}
    if site.get("bbl"):
        q["where"] = f"BBL={site['bbl']}"
    feats = req(PLUTO, q).get("features") or []
    if not feats:
        return {"error": "lot not found in MapPLUTO"}
    a = feats[0]["attributes"]
    z = (a.get("ZoneDist1") or "").upper()
    std, uap = NYC_FAR.get(z, (a.get("ResidFAR") or 0, None))
    lot = a.get("LotArea") or 0
    f = {"source": "NYC MapPLUTO (DCP)", "bbl": str(int(a["BBL"])) if a.get("BBL") else "", "zoning": z,
         "commercial_overlay": a.get("Overlay1"), "special_district": a.get("SPDist1"), "historic_district": a.get("HistDist"),
         "landmark": a.get("Landmark"), "lot_sf": lot, "frontage_ft": a.get("LotFront"), "depth_ft": a.get("LotDepth"),
         "corner_lot": str(a.get("LotType")) == "3", "existing_bldg_sf": a.get("BldgArea"), "existing_units": a.get("UnitsRes"),
         "year_built": a.get("YearBuilt"), "owner": a.get("OwnerName"),
         "far_rule": f"ZR 23-22: {z} standard FAR {std}" + (f", qualifying affordable (UAP) FAR {uap}" if uap else ""),
         "standard_floor_area": round(lot * std) if std else None, "uap_floor_area": round(lot * uap) if uap else None}
    f["units_standard"] = units(f["standard_floor_area"])
    f["units_uap"] = units(f["uap_floor_area"]) if uap else None
    if z in ("R7-1", "R7-2"):
        f["note_far"] = "ZR 23-22 lists a 4.00 standard FAR for R7-1/R7-2 lots meeting its footnote conditions (location relative to a wide street); confirm with an architect."
    if site.get("listing_lot_sf") and abs(site["listing_lot_sf"] - lot) > 0.1 * lot:
        f["lot_discrepancy"] = f"listing says {site['listing_lot_sf']:,} sf; tax lot is {lot:,} sf"
    return f


def yonkers_facts(site):
    from hidden_potential import MUNIS, frontage_ft, post, PARCELS
    rule = MUNIS["YON"]
    d = post(PARCELS.format(layer=rule["layer"]), {"where": f"PRINTKEY='{site['printkey']}'", "outFields": "*",
                                                    "returnGeometry": "false", "f": "json"})
    feats = d.get("features") or []
    if not feats:
        return {"error": "parcel not found"}
    a = feats[0]["attributes"]
    lot = round(a.get("SHAPE.AREA") or 0)
    zone = site.get("zone")
    fr = frontage_ft(rule["layer"], site["printkey"])
    f = {"source": "Westchester County tax parcels; " + rule["source"], "printkey": site["printkey"], "zoning": zone,
         "lot_sf": lot, "street_frontage_ft": fr, "property_class": a.get("PROPERTYCLASS"), "owner": a.get("OWNERNAME")}
    if zone in rule["subdivide"]:
        min_a, min_w = rule["subdivide"][zone]
        f["rule"] = f"{zone}: {min_a:,} sf and {min_w} ft of width at the street line per lot; every lot must front a mapped street"
        f["lots_by_area"] = lot // min_a
        f["lots_by_frontage"] = (fr // min_w) if fr is not None else None
        f["lots_allowed"] = min(f["lots_by_area"], f["lots_by_frontage"]) if fr is not None else None
    elif zone in rule["multifamily"]:
        mf = rule["multifamily"][zone]
        fa = round(lot * mf["far"])
        u_far = fa // GSF
        u_den = lot // mf["per_unit"] if mf["per_unit"] else u_far
        cov_fa = None
        f.update(rule=f"{zone}: FAR {mf['far']}" + (f", {mf['per_unit']} sf of lot per unit" if mf["per_unit"] else ", no per-unit cap")
                 + f", height {mf['height']}, multifamily lot coverage {int(rule['coverage_mf'] * 100)}%",
                 floor_area=fa, units_paper=int(min(u_far, u_den)),
                 parking_rule="1 space per unit + 0.33 per bedroom (Table 43-4)",
                 parking_spaces_at_paper_units=math.ceil(min(u_far, u_den) * rule["parking_per_unit"]),
                 surface_parking_capacity=int(lot * (1 - rule["coverage_mf"]) * 0.8 // 330))
    return f


def gemini(key, prompt):
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}).encode()
    last = None
    for m in MODELS:
        for attempt in (1, 2):
            try:
                r = urllib.request.Request(API.format(m=m) + f"?key={key}", data=body, headers={"Content-Type": "application/json"})
                raw = urllib.request.urlopen(r, timeout=150).read()
                return json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"], m
            except urllib.error.HTTPError as e:
                print(f"{m}: HTTP {e.code}")
                last = e
                if e.code == 429 and attempt == 1:
                    time.sleep(40)
                    continue
                break
            except Exception as e:
                print(f"{m}: {e}")
                last = e
                break
    raise last or RuntimeError("no model")


MEMO_PROMPT = """You are a New York / Connecticut land-use attorney working with a registered architect.
Write a development feasibility memo for the site below for a developer-buyer.
Use ONLY the FACTS. Do not add approvals, dates, unit counts, FARs, programs, rents or prices that are not in FACTS.
If something a buyer needs is not in FACTS, put it in "diligence". Plain language, no em dashes, short sentences.
Return ONLY JSON with these keys:
 "verdict": one sentence: what can realistically be built and whether it is worth pursuing at the ask,
 "as_of_right": what the zoning allows without discretionary approval, with the math from FACTS,
 "approval_path": as-of-right / site plan / special permit / variance / 8-30g / existing approval, and what that means for time and risk,
 "legal_issues": list of 2-5 issues (approval validity, conditions, affordability covenants, frontage, title, tenants, appeals),
 "test_fit": architect's notes: building envelope, likely unit count range, parking, what limits density first,
 "value": land cost per buildable unit at the ask using FACTS, and how that compares across the site's scenarios,
 "diligence": list of the 3-6 specific documents or checks to get before an offer,
 "letter_angle": one sentence on how to approach the owner if the site is off market (empty if listed).
FACTS:
"""


def offmarket_targets(n):
    """Strongest off-market sites from the scanners, for memos (letters list is built separately)."""
    out = []
    nyc = load("nyc_rezone.json", {}) or {}
    for r in (nyc.get("rows") or [])[:n]:
        if "REZONED INDUSTRIAL" in r.get("tags", []) and "TRANSIT" in r.get("tags", []) and 20 <= r.get("units_hi", 0) <= 120:
            out.append({"id": "nyc-" + r["bbl"], "address": r["address"], "muni": r["area"], "state": "NY", "jurisdiction": "NYC",
                        "borough": {"Bronx": "BX"}.get(r["area"], "BK" if "Brooklyn" in r["area"] or "Williamsburg" in r["area"] else "QN"),
                        "bbl": r["bbl"], "status": "Off market", "ask": None, "issues": [r.get("check")] if r.get("check") else []})
    return out[: n // 2]


def main():
    key = os.environ.get("GEMINI_API_KEY", "")
    cur = load("feasibility_sites.json", {"sites": []})
    sites = cur.get("sites", [])
    # Yonkers listings from the watchlist
    watch = load("onmarket_watch.json", {}) or {}
    hp = load("hidden_potential.json", {}) or {}
    zones = {r["printkey"]: r["zone"] for r in (hp.get("on_market") or []) + (hp.get("off_market") or []) if r.get("printkey")}
    for w in watch.get("listings", []):
        sites.append({"id": "yon-" + w["printkey"], "address": w["address"].split(",")[0], "muni": "Yonkers", "state": "NY",
                      "jurisdiction": "Yonkers", "printkey": w["printkey"], "zone": zones.get(w["printkey"]),
                      "ask": w.get("price"), "status": w.get("status"), "issues": [w["note"]] if w.get("note") else []})
    sites += offmarket_targets(int(os.environ.get("FEAS_OFFMARKET", 12)))
    prev = {m["id"]: m for m in (load("feasibility.json", {}) or {}).get("memos", [])}
    memos, n_ai = [], 0
    for s in sites:
        facts = {k: v for k, v in s.items() if k not in ("id",)}
        try:
            if s["jurisdiction"] == "NYC":
                facts["parcel"] = nyc_facts(s)
            elif s["jurisdiction"] == "Yonkers" and s.get("printkey") and s.get("zone"):
                facts["parcel"] = yonkers_facts(s)
        except Exception as e:
            facts["parcel"] = {"error": str(e)[:120]}
        ask = s.get("ask")
        p = facts.get("parcel") or {}
        scen = {}
        if p.get("units_standard"):
            scen["standard"] = p["units_standard"]
        if p.get("units_uap"):
            scen["with UAP affordable bonus"] = p["units_uap"]
        if p.get("units_paper"):
            scen["zoning maximum on paper"] = (p["units_paper"], p["units_paper"])
        if p.get("lots_allowed") is not None:
            scen["lots allowed"] = (p["lots_allowed"], p["lots_allowed"])
        if (s.get("approval") or {}).get("units"):
            scen["approved"] = (s["approval"]["units"], s["approval"]["units"])
        facts["scenarios_units"] = {k: f"{lo} to {hi}" if lo != hi else str(lo) for k, (lo, hi) in scen.items()}
        if ask:
            facts["ask_per_unit"] = {k: (f"${round(ask / hi):,} to ${round(ask / lo):,}" if lo and lo != hi else (f"${round(ask / hi):,}" if hi else "n/a"))
                                     for k, (lo, hi) in scen.items()}
        fp = json.dumps(facts, sort_keys=True, default=str)
        old = prev.get(s["id"])
        fh = hashlib.sha1(fp.encode()).hexdigest()[:12]
        memo = old.get("memo") if old and old.get("facts_hash") == fh else None
        if memo is None and key and n_ai < MAX_MEMOS:
            try:
                txt, model = gemini(key, MEMO_PROMPT + fp)
                memo = json.loads(txt)
                memo["_model"] = model
                n_ai += 1
                time.sleep(4)
            except Exception as e:
                print("memo failed", s["id"], e)
        memos.append({"id": s["id"], "address": s["address"], "muni": s.get("muni"), "state": s.get("state"),
                      "status": s.get("status"), "ask": ask, "facts": facts, "facts_hash": fh,
                      "memo": memo, "checked_utc": datetime.now(timezone.utc).isoformat()})
        print(s["id"], "ok" if memo else "facts only")
    json.dump({"generated_utc": datetime.now(timezone.utc).isoformat(), "memos": memos,
               "method": __doc__.strip()}, open(os.path.join(DATA, "feasibility.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
