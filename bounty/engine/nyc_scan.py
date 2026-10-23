"""
BOUNTY — NYC development-site scanner v3. Real data only, daily on Actions.

v3 (Oct 2026) — built around one question per parcel: WHAT IS THE NEXT ACTION?
  - OWNER CLASSES. Private owners -> outreach. HPD / DCAS / EDC -> RFP pipeline
    (Neighborhood Builders Fast Track, HPD RFPs, LIFT sites). Parks, DOT, DEP,
    DOE/SCA, NYCHA, Sanitation, NYPD, FDNY, Transit, Cultural Affairs, HRA, DHS,
    libraries, courts, state and federal -> EXCLUDED. v2 scored a DOE parking lot
    as the #1 "market" deal in the Bronx; nobody can buy that lot.
  - ACTION LABELS from the v4 underwriter: offer / negotiate / program / rfp /
    pass, with a MAX OFFER (walk-away land price at the target yield on cost)
    and HEADROOM (walk-away / estimated land). Only offer + negotiate are
    "actionable"; they lead the board. Pass deals are dropped from the feed.
  - OCCUPIED LOTS. A lot with 3+ existing residential units is a tenanted
    teardown (relocation + 485-x replacement-unit requirement); demoted, and
    flagged "occupied" so it is never mistaken for a vacant site.
  - OWNER MAILING ADDRESS from the ACRIS grantee record on the current deed, so
    the outreach CSV is a mailing list, not a list of addresses.
  - LAND ANCHOR unchanged: median $/SF of real vacant-land sales (DOF Rolling
    Sales, class V*) per borough, blended with assessed value.
  - SCORE v4 ranks on headroom, yield on cost, solo-size fit, motivated seller,
    and Section 8 fit (Bronx/Queens VPS parity). City-owned sites are scored on
    their own board, not mixed in.

Outputs: bounty/data/nyc_deals.json, bounty/data/history.json
"""
import json, os, sys, statistics, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from underwrite import underwrite_all_paths, action_for, ASSUMPTIONS as UW
import acris_distress

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/3.0)"}

ASSUMPTIONS = {
    "land_blend": "60% real vacant-land sale comps ($/SF by borough, last 18mo) + 40% assessed x2",
    "solo_equity_cap": 1_000_000, "mid_equity_cap": 3_000_000,
    "actionable": "offer = walk-away >= est. land (bid at/under comps); negotiate = walk-away 70-100% of est. land (needs a motivated seller); program = only the HPD subsidy stack works; rfp = city-owned disposition; pass = dropped.",
    "occupied_rule": "Lots with 3+ existing residential units are tenanted teardowns (relocation, 485-x replacement units) — demoted and flagged; 10+ units excluded.",
    "owner_classes": "private -> outreach; institutional (church, yeshiva, hospital, university, HDFC) -> outreach, but the sale needs a board vote plus Attorney General or court approval under N-PCL 511 (6-12 months); HPD/DCAS/EDC -> RFP pipeline; other agencies (Parks, DOT, DEP, DOE, NYCHA, Sanitation, NYPD, FDNY, Transit, Cultural Affairs, libraries, courts, state, federal) -> excluded as not acquirable.",
    "neighborhood_rent": "Market rents are borough baseline x a published ZIP factor (LIC 1.5x, Williamsburg 1.35x, East NY 0.8x, etc.); unlisted ZIPs = 1.0.",
    "acris_motivated": "Motivated-seller flag from real ACRIS records: owner tenure = years since the latest recorded deed (25y+ = long-hold/estate, low basis); free-and-clear = no mortgage recorded after that deed. Motivated = 25y+ tenure OR (free-and-clear AND 15y+). Owner mailing = grantee address on the current deed.",
    "note": "Screening estimates for outreach, not appraisals. Every number derived from public data + these published assumptions.",
}

# Neighborhood market-rent factors vs borough baseline, keyed by ZIP.
NEIGHBORHOOD_RENT_FACTOR = {
    # --- Brooklyn strong ---
    "11201":1.40,"11205":1.20,"11215":1.30,"11217":1.32,"11231":1.35,"11211":1.35,
    "11249":1.38,"11222":1.30,"11238":1.22,"11216":1.15,"11221":1.12,"11233":1.05,
    "11206":1.10,"11237":1.12,"11213":1.08,"11225":1.10,"11226":1.02,"11218":1.10,
    # --- Brooklyn weak ---
    "11207":0.82,"11208":0.80,"11239":0.78,"11212":0.78,"11236":0.85,"11224":0.82,
    "11223":0.90,"11229":0.92,"11214":0.92,"11235":0.90,
    # --- Bronx strong ---
    "10463":1.20,"10471":1.25,"10454":1.12,"10455":1.08,"10451":1.05,"10474":0.88,
    # --- Bronx weak / baseline ---
    "10456":0.92,"10457":0.92,"10459":0.90,"10460":0.92,"10453":0.95,"10458":0.98,
    "10466":0.90,"10467":0.95,"10468":0.98,"10469":1.00,"10473":0.90,
    # --- Queens strong ---
    "11101":1.50,"11109":1.55,"11102":1.28,"11103":1.25,"11106":1.28,"11104":1.22,
    "11105":1.18,"11375":1.20,"11377":1.05,"11385":1.10,"11354":1.02,"11355":0.98,
    # --- Queens weak ---
    "11433":0.85,"11434":0.85,"11435":0.88,"11436":0.85,"11691":0.80,"11692":0.82,
    "11412":0.88,"11413":0.88,"11420":0.90,"11421":0.95,
}

TARGET_ZONES = ["R6","R6A","R6B","R7A","R7B","R7D","R7X","R8","R8A","R8B","R8X",
                "R7-1","R7-2","C1-4","C2-4","C4-4","C4-5X"]
BOROUGHS = {"2":"BX","3":"BK","4":"QN"}
SALES_BORO = {"2":"BX","3":"BK","4":"QN"}

# Government / institutional owners that will not sell on the open market and do
# not dispose through HPD/EDC. Anything matching is dropped.
EXCLUDE_OWNER = ("PARKS","PARK ","TRANSIT","MTA","METROPOLITAN TRANS","CON ED","CONSOLIDATED EDISON",
                 "PORT AUTHORITY","HOUSING AUTHORITY","NYCHA","BOARD OF ED","DEPT OF ED","DEPARTMENT OF ED",
                 "EDUCATION","SCHOOL CONSTRUCTION","SCHOOL","CEMETERY","U S POSTAL","UNITED STATES",
                 "STATE OF NEW YORK","NEW YORK STATE","DORMITORY AUTH","AMTRAK","LIRR","WATERFRONT",
                 "DEP ","ENVIRONMENTAL PROT","TRANSPORTATION","DEPT OF TRANS","SANITATION","POLICE",
                 "FIRE DEPT","FIRE DEPARTMENT","CULTURAL AFFAIRS","HUMAN RESOURCES","HOMELESS",
                 "HEALTH","HOSPITALS","LIBRARY","CORRECTION","COURT","SMALL BUSINESS SERVICES",
                 "CITY UNIVERSITY","CUNY","SUNY")
# Private institutions that DO sell sites, but through a slower process (NY Not-for-Profit
# Corporation Law 510-511: board vote + Attorney General / court approval, 6-12 months).
INSTITUTIONAL_OWNER = ("CHURCH"," CH ","CH/","CHTLC","CATHOLIC","DIOCESE","ARCHDIOCESE","CONGREGATION","CONG ",
                       "SYNAGOGUE","TEMPLE","MOSQUE","MASJID","PARISH","RECTORY","CONVENT","MINISTR","BAPTIST",
                       "METHODIST","LUTHERAN","PRESBYTERIAN","EPISCOPAL","PENTECOSTAL","YESHIVA","BNOS","BAIS ",
                       "BETH ","B'NAI","BNAI","TALMUD","TORAH","HEBREW","ISLAMIC","CHRISTIAN","GOSPEL","TABERNACLE",
                       "ASSEMBLY OF GOD","SEVENTH DAY","7 DAY","ADVENTIST","SALVATION ARMY","HOSPITAL","MEDICAL CENTER",
                       "UNIVERSITY","COLLEGE","ACADEMY","FOUNDATION","HOUSING DEVELOPMENT FUND","HDFC","HOUSING DEV")
# City owners that DO dispose land for housing (RFP / Fast Track / LIFT pipeline).
RFP_OWNER = ("HOUSING PRESERVATION","HPD","CITYWIDE ADMIN","DCAS","ECONOMIC DEVELOPMENT","EDC",
             "CITY OF NEW YORK","CITY OF NY")
CITY_KEYS = ("CITY OF NEW YORK","NYC ","NEW YORK CITY")

def get_json(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def n(x, d=0.0):
    try:
        v = float(x); return v if v == v else d
    except Exception: return d

def fetch_land_comps():
    """Median $/SF of real vacant-land sales per borough, last ~18 months."""
    since = (date.today() - timedelta(days=548)).isoformat()
    base = "https://data.cityofnewyork.us/resource/usep-8jbt.json"
    probe = get_json(base + "?$limit=1")
    keys = list(probe[0].keys()) if probe else []
    print("rolling-sales fields:", keys)
    def find(*prefixes):
        for pref in prefixes:
            for k in keys:
                if k.startswith(pref): return k
        return None
    f_class = find("building_class_at_time")
    f_price = find("sale_price", "saleprice")
    f_land  = find("land_square", "land_sq", "landsquare")
    f_date  = find("sale_date", "saledate")
    f_boro  = find("borough", "boro")
    if not all([f_class, f_price, f_boro]):
        raise RuntimeError(f"could not map fields from {keys}")
    rows = []
    try:
        w = f"{f_class} like 'V%'"
        rows = get_json(base + f"?$where={urllib.parse.quote(w)}&$limit=20000")
    except Exception as e:
        print(f"comps class-filter failed: {e}")
    rows = [{"borough": r.get(f_boro), "sale_price": r.get(f_price),
             "land_square_feet": r.get(f_land), "sale_date": r.get(f_date)} for r in rows]
    def num(x):
        try: return float(str(x).replace(",","").replace("$",""))
        except Exception: return 0.0
    psf = {}
    used = 0
    for b in ("2","3","4"):
        vals = []
        for r in rows:
            if str(r.get("borough")).strip() != b: continue
            sd = str(r.get("sale_date",""))[:10]
            if sd and sd < since: continue
            price, sf = num(r.get("sale_price")), num(r.get("land_square_feet"))
            if price > 100000 and sf > 1000:
                v = price/sf
                if 10 < v < 2000: vals.append(v); used += 1
        psf[SALES_BORO[b]] = round(statistics.median(vals),0) if len(vals) >= 5 else None
    print("land comps $/SF:", psf, f"({used} usable sales of {len(rows)} fetched)")
    return psf, used

def fetch_pluto():
    zone_list = ",".join(f"'{z}'" for z in TARGET_ZONES)
    where = (f"borocode in('2','3','4') and zonedist1 in({zone_list}) "
             "and lotarea between 1500 and 40000 "
             "and residfar > 0 and builtfar < residfar*0.5 "
             "and ownertype not in('O') "
             "and landuse not in('06','07','08','09')")
    fields = ("borocode,borough,block,lot,address,zipcode,zonedist1,overlay1,"
              "lotarea,lotfront,bldgarea,builtfar,residfar,"
              "numfloors,unitsres,unitstotal,yearbuilt,ownername,ownertype,"
              "assessland,assesstot,landuse,bbl,latitude,longitude")
    out, offset = [], 0
    while offset < 10000:
        url = ("https://data.cityofnewyork.us/resource/64uk-42ks.json?"
               f"$select={urllib.parse.quote(fields)}&$where={urllib.parse.quote(where)}"
               "&$order=bbl&$limit=2000&$offset=" + str(offset))
        page = get_json(url)
        out += page
        if len(page) < 2000: break
        offset += 2000
    return out

def owner_class(p):
    """'private' | 'rfp' (HPD/DCAS/EDC) | 'excluded' (agency that does not dispose)."""
    owner = (p.get("ownername") or "").upper()
    otype = (p.get("ownertype") or "").upper()
    if any(k in owner for k in RFP_OWNER):
        return "rfp"
    if any(k in owner for k in EXCLUDE_OWNER):
        return "excluded"
    if otype == "C" or any(k in owner for k in CITY_KEYS):
        return "rfp"            # city-owned, no named agency: DCAS inventory -> disposition pipeline
    if otype in ("M", "O"):
        return "excluded"       # mixed city/private or other public authority (state, federal, PA)
    if any(k in owner for k in INSTITUTIONAL_OWNER):
        return "institutional"  # sells, but via N-PCL 511 (board + AG/court approval) — slower, real
    return "private"            # P, X (tax-exempt private not name-matched) or blank

def acquirable(p):
    if not p.get("address"): return False
    if not any(ch.isdigit() for ch in p.get("address","")): return False
    return owner_class(p) != "excluded"

def underwrite(p, comps):
    A = ASSUMPTIONS
    lot = n(p.get("lotarea")); rfar = n(p.get("residfar")); bfar = n(p.get("builtfar"))
    if lot <= 0 or rfar <= 0: return None
    boro = BOROUGHS.get(str(p.get("borocode","")), "BX")
    buildable = lot * rfar
    units = int(buildable / UW["du_factor_sf"])
    if units < 6 or units > 300: return None
    existing = int(n(p.get("unitsres")))
    if existing >= 10: return None            # tenanted apartment building, not a site
    occupied = existing >= 3
    oclass = owner_class(p)
    city = oclass == "rfp"
    # land anchor: blend real comps with assessed
    comp_psf = comps.get(boro)
    assess_land = n(p.get("assessland"))
    comp_est = lot * comp_psf if comp_psf else None
    assess_est = assess_land * 2 if assess_land > 1000 else None
    if comp_est and assess_est: land = 0.6*comp_est + 0.4*assess_est
    elif comp_est: land = comp_est
    elif assess_est: land = assess_est
    else: return None
    zipc = str(p.get("zipcode") or "")[:5]
    rfac = NEIGHBORHOOD_RENT_FACTOR.get(zipc, 1.00)
    rent_market = round(UW["rent_mo"]["market"][boro] * rfac)
    uw = underwrite_all_paths(boro, lot, rfar, land, rent_market=rent_market)
    if not uw: return None
    best = next((pp for pp in uw["paths"] if pp["path"] == uw["best_path"]), None)
    walk = uw["walk_away_land"]
    act = action_for(land, walk, city_owned=city, rfp_eligible=city,
                     program_ok=(uw["tier"] == "program"))
    tier = uw["tier"]
    if act["action"] in ("offer", "negotiate"): tier = "market"
    elif act["action"] == "program": tier = "program"
    elif act["action"] == "rfp": tier = "city"
    else: tier = "none"
    mkt_eq = uw.get("market_equity") or 0
    bucket = "solo" if mkt_eq <= A["solo_equity_cap"] else ("mid" if mkt_eq <= A["mid_equity_cap"] else "jv")
    if tier == "program": bucket = "program"
    if tier == "city": bucket = "city"
    # score v4 — what makes a site actionable, in order of weight
    head = act.get("headroom") or 0
    bm = next((pp for pp in uw["paths"] if pp["path"] == uw["best_market_path"]), None) or {}
    s_head = max(0, min(1, (head - 0.7)/0.8)) * 35          # 0.7x -> 0, 1.5x -> 35
    s_yoc  = max(0, min(1, ((bm.get("yoc_pct") or 0) - 6.0)/3.0)) * 20   # 6% -> 0, 9% -> 20
    s_fit  = {"solo": 15, "mid": 9, "jv": 3, "program": 5, "city": 8}[bucket]
    s_use  = (1 - bfar/rfar) * 10
    s_s8   = 6 if (bm.get("path","").endswith("_s8") and boro in ("BX","QN")) else 0
    score = round(s_head + s_yoc + s_fit + s_use + s_s8, 1)
    if occupied: score = round(score * 0.6, 1)
    if oclass == "institutional": score = round(score * 0.85, 1)
    if tier == "none": score = min(score, 29.9)
    elif tier == "program": score = min(score, 59.9)
    elif tier == "city": score = min(score, 79.9)
    return {
        "bbl": p.get("bbl"), "address": (p.get("address") or "").title(),
        "borough": boro, "zip": p.get("zipcode"), "zone": p.get("zonedist1"),
        "lot_sf": int(lot), "built_far": round(bfar,2), "max_far": round(rfar,2),
        "buildable_sf": int(buildable), "est_units": uw["units_base"],
        "existing_units": existing, "occupied": occupied,
        "year_built": int(n(p.get("yearbuilt"))) or None,
        "owner": (p.get("ownername") or "").title(), "owner_class": oclass, "city_owned": city,
        "owner_mailing": "", "owner_on_deed": "",
        "assessed_land": int(assess_land), "land_comp_psf": comp_psf,
        "est_land_cost": int(land),
        "walk_away_land": int(walk), "walk_away_land_institutional": int(uw["walk_away_land_institutional"]),
        "action": act["action"], "max_offer": act["max_offer"], "headroom": act["headroom"], "action_why": act["why"],
        "pencils": act["action"] in ("offer", "negotiate", "program", "rfp"),
        "tier": tier, "best_path": uw["best_path"], "best_path_label": uw["best_label"],
        "best_market_path": uw["best_market_path"],
        "best": best, "paths": uw["paths"],
        "equity_needed": int(mkt_eq), "bucket": bucket, "score": score,
        "rent_market_used": rent_market, "neighborhood_factor": rfac,
        "distress": False, "distress_type": None, "distress_year": None,
        "owner_tenure_years": None, "free_and_clear": False, "motivated": False,
        "lat": n(p.get("latitude"), None), "lng": n(p.get("longitude"), None),
        "_bc": str(p.get("borocode","")), "_blk": p.get("block"), "_lot": p.get("lot"),
    }

def main():
    os.makedirs("bounty/data", exist_ok=True)
    today = date.today().isoformat()
    try:
        comps, n_sales = fetch_land_comps()
    except Exception as e:
        print(f"comps failed entirely ({e}); degrading to assessed-only anchor (labeled)")
        comps, n_sales = {"BX": None, "BK": None, "QN": None}, 0
    raw = fetch_pluto()
    print(f"PLUTO {len(raw)} parcels fetched")
    deals, excluded = [], 0
    for p in raw:
        if not acquirable(p): excluded += 1; continue
        d = underwrite(p, comps)
        if d: deals.append(d)
    deals.sort(key=lambda d: d["score"], reverse=True)
    print(f"excluded {excluded} non-acquirable owners; underwrote {len(deals)}")

    # ACRIS layer: motivated-seller flags + owner mailing address, private owners first.
    try:
        cand = [d for d in deals if d["owner_class"] in ("private", "institutional") and d["action"] != "pass"][:400]
        acris_distress.annotate(cand, {"2":"2","3":"3","4":"4"})
        for d in cand:
            bump = (8 if (d.get("owner_tenure_years") or 0) >= 25 else 0) + (5 if d.get("free_and_clear") else 0)
            if bump and d["tier"] == "market":
                d["score"] = round(min(100.0, d["score"] + bump), 1)
        deals.sort(key=lambda d: d["score"], reverse=True)
    except Exception as e:
        print(f"ACRIS layer skipped: {e}")

    hist_path = "bounty/data/history.json"
    hist = {}
    if os.path.exists(hist_path):
        try: hist = json.load(open(hist_path))
        except Exception: hist = {}
    new_count = 0
    for d in deals:
        k = d["bbl"] or d["address"]
        if k not in hist: hist[k] = {"first_seen": today}; new_count += 1
        d["first_seen"] = hist[k]["first_seen"]; d["is_new"] = hist[k]["first_seen"] == today
    json.dump(hist, open(hist_path,"w"))

    private = [d for d in deals if d["owner_class"] in ("private", "institutional")]
    actionable = [d for d in private if d["action"] in ("offer", "negotiate")]
    program = [d for d in private if d["action"] == "program"]
    rfp = [d for d in deals if d["action"] == "rfp"]
    emitted = actionable[:220] + program[:40] + rfp[:80]
    seen=set(); dd=[]
    for d in emitted:
        if d["bbl"] in seen: continue
        seen.add(d["bbl"]); dd.append(d)
    for d in dd:
        d.pop("_bc",None); d.pop("_blk",None); d.pop("_lot",None)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "region": "NYC — Bronx, Brooklyn, Queens",
           "thesis": "Private underbuilt lots where the walk-away land price (at target yield on cost, owner-builder basis) covers the comp-based land estimate. Those are the sites to make offers on. City-owned HPD/DCAS/EDC lots are a separate RFP pipeline.",
           "counts": {"scanned": len(raw), "excluded_not_acquirable": excluded, "qualified": len(deals),
                      "new_today": new_count,
                      "actionable": len(actionable),
                      "offer": sum(1 for d in private if d["action"]=="offer"),
                      "negotiate": sum(1 for d in private if d["action"]=="negotiate"),
                      "program": len(program), "pass": sum(1 for d in private if d["action"]=="pass"),
                      "solo": sum(1 for d in actionable if d["bucket"]=="solo"),
                      "mid": sum(1 for d in actionable if d["bucket"]=="mid"),
                      "jv": sum(1 for d in actionable if d["bucket"]=="jv"),
                      "city_owned": len(rfp), "pencils": len(actionable) + len(program) + len(rfp),
                      "motivated": sum(1 for d in actionable if d.get("motivated")),
                      "free_and_clear": sum(1 for d in actionable if d.get("free_and_clear")),
                      "with_mailing": sum(1 for d in actionable if d.get("owner_mailing")),
                      "land_sales_used": n_sales},
           "program_assumptions": UW,
           "land_comps_psf": comps,
           "assumptions": ASSUMPTIONS,
           "deals": dd}
    err="bounty/data/NYC_ERROR.txt"
    if os.path.exists(err): os.remove(err)
    json.dump(out, open("bounty/data/nyc_deals.json","w"), indent=2)
    c = out["counts"]
    print(f"qualified {len(deals)} | actionable {c['actionable']} (offer {c['offer']} / negotiate {c['negotiate']}) | program {c['program']} | pass {c['pass']} | rfp {c['city_owned']} | mailing {c['with_mailing']} | new {new_count} | emitted {len(dd)}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/NYC_ERROR.txt","w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
