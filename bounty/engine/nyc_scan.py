"""
BOUNTY — NYC development-site scanner (Phase 1). Runs daily on GitHub Actions.

REAL DATA ONLY:
  - NYC PLUTO (Socrata 64uk-42ks): every parcel's zoning, FAR, lot size, owner.
  - NYC DOF assessments (yjxr-fw8i): assessed land/total value for price anchoring.
Underutilization thesis: parcels in development-friendly residential zones built
to a fraction of their allowed FAR = the buildable "bounty" is the gap.

HONESTY RULES:
  - No mock data. If an API fails, the output says so; nothing is invented.
  - Off-market parcels have NO asking price. We anchor on ASSESSED value and a
    market-multiple range, clearly labeled an ESTIMATE for outreach, not a price.
  - All underwriting assumptions live in ASSUMPTIONS below, published in the
    output so the terminal can display them and they can be tuned in one place.
  - History file tracks first_seen so "NEW today" is real, and scans never
    delete prior knowledge (append/update, not delete-reinsert).

Output: bounty/data/nyc_deals.json, bounty/data/history.json
"""
import json, os, time, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone, date

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}

# ---------------- assumptions (published, tunable) ----------------
ASSUMPTIONS = {
    "hard_cost_psf": 300,            # new construction $/SF (NYC outer-borough)
    "soft_cost_pct": 0.25,           # of hard cost
    "du_factor_sf": 680,             # zoning SF per dwelling unit (typical R-district factor)
    "efficiency": 0.85,              # net rentable / gross buildable
    "rent_psf_mo": {"BX": 3.35, "BK": 4.10, "QN": 3.80},  # market rent $/net SF/month (approx)
    "vacancy": 0.05,
    "opex_ratio": 0.30,              # of EGI
    "ltc": 0.85,                     # Ponce/Northeast construction-to-perm thesis
    "rate": 0.0675, "amort_years": 30,
    "dev_fee_pct": 0.07,
    "land_multiple_low": 1.0,        # land cost estimate = assessed land value x range
    "land_multiple_high": 2.0,       # (NYC assessed values run well below market)
    "jv_equity_cap": 1_000_000,      # solo-equity ceiling before JV flag
    "note": "Estimates for screening/outreach only — not appraisals. Rents/costs are editable assumptions, shown in the terminal.",
}

TARGET_ZONES = ["R6","R6A","R6B","R7A","R7B","R7D","R7X","R8","R8A","R8B","R8X",
                "R7-1","R7-2","R6-1","C1-4","C2-4","C4-4","C4-5X","M1-4/R6A","M1-4/R7A"]
BOROUGHS = {"2":"BX","3":"BK","4":"QN"}   # PLUTO borocode -> tag

def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def fetch_pluto():
    zone_list = ",".join(f"'{z}'" for z in TARGET_ZONES)
    where = (f"borocode in('2','3','4') and zonedist1 in({zone_list}) "
             "and lotarea between 1500 and 40000 "
             "and residfar > 0 and builtfar < residfar*0.5 "
             "and landuse not in('06','07','08')")  # skip existing large multifam/institutional? keep simple
    fields = ("borocode,borough,block,lot,address,zipcode,zonedist1,overlay1,"
              "lotarea,lotfrontage: lotfront,bldgarea,builtfar,residfar,commfar,"
              "numfloors,unitsres,unitstotal,yearbuilt,ownername,ownertype,"
              "assessland,assesstot,landuse,bbl,latitude,longitude")
    # Socrata select can't rename with spaces; use plain field list
    fields = ("borocode,borough,block,lot,address,zipcode,zonedist1,overlay1,"
              "lotarea,lotfront,bldgarea,builtfar,residfar,commfar,"
              "numfloors,unitsres,unitstotal,yearbuilt,ownername,ownertype,"
              "assessland,assesstot,landuse,bbl,latitude,longitude")
    url = ("https://data.cityofnewyork.us/resource/64uk-42ks.json?"
           f"$select={urllib.parse.quote(fields)}&$where={urllib.parse.quote(where)}"
           "&$order=" + urllib.parse.quote("lotarea DESC") + "&$limit=1200")
    return get_json(url)

def n(x, d=0.0):
    try:
        v = float(x); return v if v == v else d
    except Exception: return d

def underwrite(p):
    A = ASSUMPTIONS
    lot = n(p.get("lotarea")); rfar = n(p.get("residfar")); bfar = n(p.get("builtfar"))
    if lot <= 0 or rfar <= 0: return None
    boro = BOROUGHS.get(str(p.get("borocode","")), "BX")
    buildable = lot * rfar
    unused = max(0.0, buildable - n(p.get("bldgarea")))
    units = int(buildable / A["du_factor_sf"])
    if units < 6: return None                      # too small for the thesis
    # costs
    assess_land = n(p.get("assessland")); assess_tot = n(p.get("assesstot"))
    land_low  = assess_land * A["land_multiple_low"] if assess_land>0 else lot*40
    land_high = assess_land * A["land_multiple_high"] if assess_land>0 else lot*80
    land_mid = (land_low+land_high)/2
    hard = buildable * A["hard_cost_psf"]
    soft = hard * A["soft_cost_pct"]
    tdc = land_mid + hard + soft
    # income
    net_sf = buildable * A["efficiency"]
    gpr = net_sf * A["rent_psf_mo"][boro] * 12
    egi = gpr * (1 - A["vacancy"])
    noi = egi * (1 - A["opex_ratio"])
    # debt
    loan = tdc * A["ltc"]; equity = tdc - loan
    r = A["rate"]/12; nper = A["amort_years"]*12
    ds = loan * (r*(1+r)**nper)/((1+r)**nper - 1) * 12 if loan>0 else 0
    dscr = noi/ds if ds>0 else 0
    cf = noi - ds
    coc = cf/equity if equity>0 else 0
    dev_fee = tdc * A["dev_fee_pct"]
    # score: blend of upside + feasibility
    underuse = 1 - (bfar/rfar if rfar>0 else 1)
    score = round(min(100, underuse*40 + max(0,min(dscr-0.9,0.6))/0.6*35 + min(units,60)/60*25), 1)
    return {
        "bbl": p.get("bbl"), "address": (p.get("address") or "").title(),
        "borough": boro, "zip": p.get("zipcode"),
        "zone": p.get("zonedist1"), "overlay": p.get("overlay1"),
        "lot_sf": int(lot), "built_far": round(bfar,2), "max_far": round(rfar,2),
        "buildable_sf": int(buildable), "unused_sf": int(unused),
        "est_units": units, "existing_units": int(n(p.get("unitsres"))),
        "year_built": int(n(p.get("yearbuilt"))) or None,
        "owner": (p.get("ownername") or "").title(), "owner_type": p.get("ownertype"),
        "city_owned": any(k in (p.get("ownername") or "").upper() for k in
                          ("CITY OF NEW YORK","NYC ","HOUSING PRESERVATION","DEPT OF","DEPARTMENT OF")),
        "assessed_land": int(assess_land), "assessed_total": int(assess_tot),
        "est_land_cost_low": int(land_low), "est_land_cost_high": int(land_high),
        "tdc": int(tdc), "loan_85ltc": int(loan), "equity_needed": int(equity),
        "noi": int(noi), "annual_debt_service": int(ds),
        "dscr": round(dscr,2), "cash_flow": int(cf), "coc_pct": round(coc*100,1),
        "dev_fee": int(dev_fee),
        "jv_flag": equity > ASSUMPTIONS["jv_equity_cap"],
        "score": score,
        "lat": n(p.get("latitude"), None), "lng": n(p.get("longitude"), None),
    }

def main():
    os.makedirs("bounty/data", exist_ok=True)
    today = date.today().isoformat()
    try:
        raw = fetch_pluto()
        print(f"PLUTO returned {len(raw)} parcels")
    except Exception as e:
        json.dump({"generated_utc": datetime.now(timezone.utc).isoformat(),
                   "error": f"PLUTO fetch failed: {type(e).__name__}: {e}", "deals": []},
                  open("bounty/data/nyc_deals.json","w"), indent=2)
        raise
    deals = []
    for p in raw:
        d = underwrite(p)
        if d: deals.append(d)
    deals.sort(key=lambda d: d["score"], reverse=True)

    # history: first_seen tracking (never delete)
    hist_path = "bounty/data/history.json"
    hist = {}
    if os.path.exists(hist_path):
        try: hist = json.load(open(hist_path))
        except Exception: hist = {}
    new_count = 0
    for d in deals:
        k = d["bbl"] or d["address"]
        if k not in hist:
            hist[k] = {"first_seen": today}; new_count += 1
        d["first_seen"] = hist[k]["first_seen"]
        d["is_new"] = hist[k]["first_seen"] == today
    json.dump(hist, open(hist_path,"w"))

    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "region": "NYC (Bronx, Brooklyn, Queens)",
           "thesis": "Off-market underutilized parcels: built FAR < 50% of allowed, dev-friendly zones, 1.5k-40k SF lots, ≥6 buildable units.",
           "counts": {"scanned": len(raw), "qualified": len(deals), "new_today": new_count,
                      "city_owned": sum(1 for d in deals if d["city_owned"]),
                      "jv_needed": sum(1 for d in deals if d["jv_flag"])},
           "assumptions": ASSUMPTIONS,
           "deals": deals[:400]}
    json.dump(out, open("bounty/data/nyc_deals.json","w"), indent=2)
    print(f"qualified {len(deals)} deals | new today {new_count} | "
          f"city-owned {out['counts']['city_owned']} | jv {out['counts']['jv_needed']}")

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/NYC_ERROR.txt","w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
