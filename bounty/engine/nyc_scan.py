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
import json, os, statistics, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date, timedelta

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/2.0)"}

ASSUMPTIONS = {
    "hard_cost_psf": 300, "soft_cost_pct": 0.25,
    "du_factor_sf": 680, "efficiency": 0.85,
    "rent_psf_mo": {"BX": 3.35, "BK": 4.10, "QN": 3.80},
    "vacancy": 0.05, "opex_ratio": 0.30,
    "ltc": 0.85, "rate": 0.0675, "amort_years": 30, "dev_fee_pct": 0.07,
    "land_blend": "60% real vacant-land sale comps ($/SF by borough, last 18mo) + 40% assessed x2",
    "solo_equity_cap": 1_000_000, "mid_equity_cap": 3_000_000,
    "note": "Screening estimates for outreach, not appraisals. Every number derived from public data + these published assumptions.",
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
    """Median $/SF of real vacant-land sales per borough, last ~18 months."""
    since = (date.today() - timedelta(days=548)).isoformat()
    where = (f"sale_date >= '{since}' and sale_price > 100000 "
             "and building_class_at_time_of_sale like 'V%' "
             "and land_square_feet > 1000 and borough in('2','3','4')")
    url = ("https://data.cityofnewyork.us/resource/usep-8jbt.json?"
           f"$select=borough,sale_price,land_square_feet,sale_date"
           f"&$where={urllib.parse.quote(where)}&$limit=5000")
    rows = get_json(url)
    psf = {}
    for b in ("2","3","4"):
        vals = [n(r["sale_price"])/n(r["land_square_feet"]) for r in rows
                if str(r.get("borough"))==b and n(r.get("land_square_feet"))>0]
        vals = [v for v in vals if 10 < v < 2000]   # sanity band
        psf[SALES_BORO[b]] = round(statistics.median(vals),0) if len(vals)>=5 else None
    print("land comps $/SF:", psf, f"({len(rows)} sales)")
    return psf, len(rows)

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
    hard = buildable * A["hard_cost_psf"]; soft = hard * A["soft_cost_pct"]
    tdc = land + hard + soft
    net_sf = buildable * A["efficiency"]
    gpr = net_sf * A["rent_psf_mo"][boro] * 12
    noi = gpr * (1 - A["vacancy"]) * (1 - A["opex_ratio"])
    loan = tdc * A["ltc"]; equity = tdc - loan
    r = A["rate"]/12; nper = A["amort_years"]*12
    ds = loan * (r*(1+r)**nper)/((1+r)**nper - 1) * 12
    dscr = noi/ds if ds > 0 else 0
    cf = noi - ds; coc = cf/equity if equity > 0 else 0
    # max land you could pay and still hit DSCR 1.20 (real negotiating number)
    noi_needed_ds = noi / 1.20
    max_loan = noi_needed_ds / ((r*(1+r)**nper)/((1+r)**nper-1)*12)
    max_tdc = max_loan / A["ltc"]
    max_land = max(0, max_tdc - hard - soft)
    bucket = "solo" if equity <= A["solo_equity_cap"] else ("mid" if equity <= A["mid_equity_cap"] else "jv")
    # score v2
    basis = land / comp_est if comp_est else 1.0            # <1 = cheaper than comps
    s_basis = max(0, min(1, (1.35 - basis)/0.7)) * 30
    s_use  = (1 - bfar/rfar) * 25
    s_dscr = max(0, min(1, (dscr - 1.0)/0.4)) * 20
    s_fit  = (15 if bucket=="solo" else (8 if bucket=="mid" else 3))
    s_city = 10 if city else 0
    score = round(s_basis + s_use + s_dscr + s_fit + s_city, 1)
    return {
        "bbl": p.get("bbl"), "address": (p.get("address") or "").title(),
        "borough": boro, "zip": p.get("zipcode"), "zone": p.get("zonedist1"),
        "lot_sf": int(lot), "built_far": round(bfar,2), "max_far": round(rfar,2),
        "buildable_sf": int(buildable), "est_units": units,
        "existing_units": int(n(p.get("unitsres"))), "year_built": int(n(p.get("yearbuilt"))) or None,
        "owner": (p.get("ownername") or "").title(), "city_owned": city,
        "assessed_land": int(assess_land), "land_comp_psf": comp_psf,
        "est_land_cost": int(land), "max_land_at_dscr120": int(max_land),
        "tdc": int(tdc), "loan": int(loan), "equity_needed": int(equity),
        "noi": int(noi), "dscr": round(dscr,2), "cash_flow": int(cf),
        "coc_pct": round(coc*100,1), "dev_fee": int(tdc*A["dev_fee_pct"]),
        "bucket": bucket, "score": score,
        "lat": n(p.get("latitude"), None), "lng": n(p.get("longitude"), None),
    }

def main():
    os.makedirs("bounty/data", exist_ok=True)
    today = date.today().isoformat()
    comps, n_sales = fetch_land_comps()
    raw = fetch_pluto()
    print(f"PLUTO {len(raw)} parcels fetched")
    deals = []
    for p in raw:
        if not acquirable(p): continue
        d = underwrite(p, comps)
        if d: deals.append(d)
    deals.sort(key=lambda d: d["score"], reverse=True)

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
                      "land_sales_used": n_sales},
           "land_comps_psf": comps,
           "assumptions": ASSUMPTIONS,
           "deals": top("solo") + top("mid") + top("jv",40) + [d for d in deals if d["city_owned"]][:40]}
    # dedupe deals list preserving order
    seen=set(); dd=[]
    for d in out["deals"]:
        if d["bbl"] in seen: continue
        seen.add(d["bbl"]); dd.append(d)
    out["deals"]=dd
    json.dump(out, open("bounty/data/nyc_deals.json","w"), indent=2)
    print(f"qualified {len(deals)} | solo {out['counts']['solo']} mid {out['counts']['mid']} jv {out['counts']['jv']} | city {out['counts']['city_owned']} | new {new_count} | emitted {len(dd)}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/NYC_ERROR.txt","w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
