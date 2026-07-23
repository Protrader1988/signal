"""
BOUNTY — NYC development-site scanner v2. Real data only, daily on Actions.

v2 upgrades over v1 (honest fixes from first live run):
  - LAND ANCHORING ON REAL COMPS: median $/SF of actual vacant-land sales
    (DOF Rolling Sales, class V*) per borough, blended with assessed value.
    v1's assessed-only anchor was absurd on vacant land ($3k for an acre).
  - ACQUIRABILITY FILTERS: drops parks, transit, utilities, port authority,
    NYCHA, schools, cemeteries — parcels you cannot actually buy. City-owned
    (HPD/DCAS-type) kept and TAGGED as disposition/RFP opportunities.
  - BUCKETS: solo (equity <= $1M), mid ($1-3M, creative financing), jv (>$3M).
    v1 sorted by lot size so only whales surfaced; your thesis needs solo deals.
  - SCORE v2: differentiates on land-basis-vs-comps (the real money-maker),
    underuse, DSCR, city-owned upside, and solo-size fit — not just size.

Outputs: bounty/data/nyc_deals.json, bounty/data/history.json
"""
import json, os, sys, statistics, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from underwrite import underwrite_all_paths, ASSUMPTIONS as UW
import acris_distress

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/2.0)"}

ASSUMPTIONS = {
    "hard_cost_psf": 300, "soft_cost_pct": 0.25,
    "du_factor_sf": 680, "efficiency": 0.85,
    "rent_psf_mo": {"BX": 3.35, "BK": 4.10, "QN": 3.80},
    "vacancy": 0.05, "opex_ratio": 0.30,
    "ltc": 0.85, "rate": 0.0675, "amort_years": 30, "dev_fee_pct": 0.07,
    "land_blend": "60% real vacant-land sale comps ($/SF by borough, last 18mo) + 40% assessed x2",
    "solo_equity_cap": 1_000_000, "mid_equity_cap": 3_000_000,
    "neighborhood_rent": "Market rents are borough baseline x a published ZIP factor (LIC 1.5x, Williamsburg 1.35x, East NY 0.8x, etc.); unlisted ZIPs = 1.0. Tunes which deals pencil to the actual submarket.",
    "acris_motivated": "Motivated-seller flag from real ACRIS records: owner tenure = years since the latest recorded deed (25y+ = long-hold/estate, low basis); free-and-clear = no mortgage recorded after that deed (no payoff, cleaner close). Motivated = 25y+ tenure OR (free-and-clear AND 15y+). Additive score bonus; nothing simulated. (ACRIS Real Property does not carry lis-pendens/tax-lien filings, so no 'distress lien' flag is claimed.)",
    "note": "Screening estimates for outreach, not appraisals. Every number derived from public data + these published assumptions.",
}

# Neighborhood market-rent factors vs borough baseline, keyed by ZIP.
# Rents vary 2x+ within a borough (LIC vs Jamaica; Williamsburg vs Brownsville);
# a flat borough rent overstates weak submarkets and understates strong ones,
# which changes which deals actually pencil. Editable, published assumption;
# any ZIP not listed defaults to 1.00 (borough baseline).
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
EXCLUDE_OWNER = ("PARKS","PARK ","TRANSIT","MTA","METROPOLITAN TRANS","CON ED","CONSOLIDATED EDISON",
                 "PORT AUTHORITY","HOUSING AUTHORITY","NYCHA","BOARD OF ED","DEPT OF ED","SCHOOL",
                 "CEMETERY","U S POSTAL","UNITED STATES","STATE OF NEW YORK","AMTRAK","LIRR",
                 "WATERFRONT","DEP ","ENVIRONMENTAL PROT")
CITY_KEYS = ("CITY OF NEW YORK","NYC ","HOUSING PRESERVATION","HPD","DCAS","ECONOMIC DEVELOPMENT")

def get_json(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def n(x, d=0.0):
    try:
        v = float(x); return v if v == v else d
    except Exception: return d

def fetch_land_comps():
    """Median $/SF of real vacant-land sales per borough, last ~18 months.
    Numeric columns in usep-8jbt are text -> filter numerics client-side."""
    since = (date.today() - timedelta(days=548)).isoformat()
    base = "https://data.cityofnewyork.us/resource/usep-8jbt.json"
    # discover the dataset's real field names from one raw row
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
    # remap to canonical names for the loop below
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
    while offset < 6000:
        url = ("https://data.cityofnewyork.us/resource/64uk-42ks.json?"
               f"$select={urllib.parse.quote(fields)}&$where={urllib.parse.quote(where)}"
               "&$order=bbl&$limit=2000&$offset=" + str(offset))
        page = get_json(url)
        out += page
        if len(page) < 2000: break
        offset += 2000
    return out

def acquirable(p):
    owner = (p.get("ownername") or "").upper()
    if not p.get("address"): return False
    if not any(ch.isdigit() for ch in p.get("address","")): return False
    city = any(k in owner for k in CITY_KEYS)
    if not city and any(k in owner for k in EXCLUDE_OWNER): return False
    return True

def underwrite(p, comps):
    A = ASSUMPTIONS
    lot = n(p.get("lotarea")); rfar = n(p.get("residfar")); bfar = n(p.get("builtfar"))
    if lot <= 0 or rfar <= 0: return None
    boro = BOROUGHS.get(str(p.get("borocode","")), "BX")
    buildable = lot * rfar
    units = int(buildable / A["du_factor_sf"])
    if units < 6 or units > 300: return None
    owner = (p.get("ownername") or "").upper()
    city = any(k in owner for k in CITY_KEYS)
    # land anchor: blend real comps with assessed
    comp_psf = comps.get(boro)
    assess_land = n(p.get("assessland"))
    comp_est = lot * comp_psf if comp_psf else None
    assess_est = assess_land * 2 if assess_land > 1000 else None
    if comp_est and assess_est: land = 0.6*comp_est + 0.4*assess_est
    elif comp_est: land = comp_est
    elif assess_est: land = assess_est
    else: return None
    # neighborhood rent tuning: borough baseline x ZIP factor
    zipc = str(p.get("zipcode") or "")[:5]
    rfac = NEIGHBORHOOD_RENT_FACTOR.get(zipc, 1.00)
    rent_market = round(UW["rent_mo"]["market"][boro] * rfac)
    # multi-path underwriting (the real institutional layer)
    uw = underwrite_all_paths(boro, lot, rfar, land, rent_market=rent_market)
    if not uw: return None
    best = next((pp for pp in uw["paths"] if pp["path"] == uw["best_path"]), None)
    # walk-away land price: solve land where best-ish path hits feasibility.
    # Approximate with the 485-x path NOI (most common closer).
    p485 = next((pp for pp in uw["paths"] if pp["path"].startswith("485x")), uw["paths"][0])
    k = (UW["conv_rate"]/12*(1+UW["conv_rate"]/12)**(UW["conv_amort"]*12)) / \
        ((1+UW["conv_rate"]/12)**(UW["conv_amort"]*12)-1) * 12
    max_loan = (p485["noi"]/UW["conv_min_dscr"])/k if p485["noi"] > 0 else 0
    hard = buildable*UW["hard_cost_psf"]; soft = hard*UW["soft_cost_pct"]
    # walk-away land: highest land price where 485-x path stays feasible
    # (loan can be < 85% LTC; binding constraint is equity <= 35% of TDC)
    walk_away = max(0, max_loan/0.65 - hard - soft)
    tier = uw["tier"]
    mkt_eq = uw.get("market_equity")
    if tier == "market" and mkt_eq is not None:
        bucket = "solo" if mkt_eq <= 1_000_000 else ("mid" if mkt_eq <= 3_000_000 else "jv")
        equity = mkt_eq
    elif tier == "program":
        bucket = "program"; equity = 0
    else:
        bucket = "none"; equity = p485["equity"]
    # score v3.1 — market-executable ranks above program-dependent above none
    basis = land / comp_est if comp_est else 1.0
    s_basis = max(0, min(1, (1.35 - basis)/0.7)) * 25
    s_use  = (1 - bfar/rfar) * 15
    if tier == "market" and best:
        prof = best.get("coc_pct") or 0
        s_path = 30
        s_prof = max(0, min(1, (prof - 4)/8)) * 12
    elif tier == "program":
        s_path = 14; s_prof = 0
    else:
        s_path = 0; s_prof = 0
    s_fit  = {"solo": 10, "mid": 6, "jv": 2, "program": 6, "none": 0}[bucket]
    s_city = 8 if city else 0
    score = round(s_basis + s_use + s_path + s_prof + s_fit + s_city, 1)
    if tier == "none": score = min(score, 39.9)
    elif tier == "program": score = min(score, 69.9)   # program plays cap below market plays
    return {
        "bbl": p.get("bbl"), "address": (p.get("address") or "").title(),
        "borough": boro, "zip": p.get("zipcode"), "zone": p.get("zonedist1"),
        "lot_sf": int(lot), "built_far": round(bfar,2), "max_far": round(rfar,2),
        "buildable_sf": int(buildable), "est_units": uw["units_base"],
        "existing_units": int(n(p.get("unitsres"))), "year_built": int(n(p.get("yearbuilt"))) or None,
        "owner": (p.get("ownername") or "").title(), "city_owned": city,
        "assessed_land": int(assess_land), "land_comp_psf": comp_psf,
        "est_land_cost": int(land), "walk_away_land": int(walk_away),
        "pencils": uw["any_feasible"], "tier": tier, "best_path": uw["best_path"],
        "best_path_label": uw["best_label"],
        "best": best, "paths": uw["paths"],
        "equity_needed": int(equity), "bucket": bucket, "score": score,
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
    deals = []
    for p in raw:
        if not acquirable(p): continue
        d = underwrite(p, comps)
        if d: deals.append(d)
    deals.sort(key=lambda d: d["score"], reverse=True)

    # ACRIS motivated-seller layer: real recorded liens/foreclosures + owner tenure.
    # Bounded to the top candidates (by score) to keep the join cheap; non-fatal.
    try:
        cand = deals[:320]
        acris_distress.annotate(cand, {"2":"2","3":"3","4":"4"})
        for d in cand:
            bump = (8 if (d.get("owner_tenure_years") or 0) >= 25 else 0) + (5 if d.get("free_and_clear") else 0)
            if bump:
                d["score"] = round(d["score"] + bump, 1)
                if d["tier"] == "none": d["score"] = min(d["score"], 49.9)
                elif d["tier"] == "program": d["score"] = min(d["score"], 74.9)
                else: d["score"] = min(d["score"], 100.0)
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

    def top(bucket, k=60):
        return [d for d in deals if d["bucket"]==bucket][:k]
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "region": "NYC — Bronx, Brooklyn, Queens",
           "thesis": "Off-market underutilized parcels in dev-friendly zones; land anchored to real vacant-land sale comps; underwritten to your 85% LTC thesis.",
           "counts": {"scanned": len(raw), "qualified": len(deals), "new_today": new_count,
                      "solo": sum(1 for d in deals if d["bucket"]=="solo"),
                      "mid": sum(1 for d in deals if d["bucket"]=="mid"),
                      "jv": sum(1 for d in deals if d["bucket"]=="jv"),
                      "city_owned": sum(1 for d in deals if d["city_owned"]),
                      "pencils": sum(1 for d in deals if d["pencils"]),
                      "motivated": sum(1 for d in deals if d.get("motivated")),
                      "free_and_clear": sum(1 for d in deals if d.get("free_and_clear")),
                      "land_sales_used": n_sales},
           "program_assumptions": UW,
           "land_comps_psf": comps,
           "assumptions": ASSUMPTIONS,
           "deals": top("solo") + top("mid") + top("jv",40) + top("program",60) + [d for d in deals if d["city_owned"]][:40]}
    # dedupe deals list preserving order
    seen=set(); dd=[]
    for d in out["deals"]:
        if d["bbl"] in seen: continue
        seen.add(d["bbl"]); dd.append(d)
    # strip internal-only join keys
    for d in dd:
        d.pop("_bc",None); d.pop("_blk",None); d.pop("_lot",None)
    out["deals"]=dd
    err="bounty/data/NYC_ERROR.txt"
    if os.path.exists(err): os.remove(err)
    json.dump(out, open("bounty/data/nyc_deals.json","w"), indent=2)
    print(f"qualified {len(deals)} | solo {out['counts']['solo']} mid {out['counts']['mid']} jv {out['counts']['jv']} | city {out['counts']['city_owned']} | new {new_count} | emitted {len(dd)}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/NYC_ERROR.txt","w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
