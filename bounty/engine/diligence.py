"""
BOUNTY — per-lot diligence layer (NYC). v1, Oct 2026.

Runs the pre-offer checks a buyer would otherwise do by hand, from public data,
for every actionable lot. Each check returns flags with a severity:
  red    = usually a deal-breaker or a hard cost (dead-narrow lot, landmark, active lien sale,
           remediation site on the lot, FAR cannot fit under the height limit)
  amber  = price it in / verify (narrow lot, violations with balances, flood zone,
           E-designation, prior auto or gas use, special district, open mortgage)
  green  = checked, nothing found

What is NOT automatable from open data (and is said so on the page):
  street width (wide vs narrow street decides R-district FAR; check ZoLa),
  court judgments, LLC members, a Phase I, a survey, a real massing study.

Datasets (all NYC Open Data / NYS Open Data, no key):
  DOB Violations            3h2n-5cm9   boro / block / lot (text, zero-padded)
  DOB ECB Violations        6bgk-3dad   boro / block / lot, ecb_violation_status, balance_due
  DOF Tax Lien Sale Lists   9rz4-mjek   borough / block / lot, cycle (90-day list)
  NYS DEC Remediation Sites c6ci-rzpg   lat / lng, site class, program
PLUTO fields (fetched by nyc_scan): overlay1/2, spdist1-3, ltdheight, histdist, landmark,
  edesignum, firm07_flag, pfirm15_flag, lotfront, lotdepth, irrlotcode, bldgclass, landuse.
"""
import json, math, os, urllib.request, urllib.parse
from datetime import date

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/3.0)"}
DOBV = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
ECB = "https://data.cityofnewyork.us/resource/6bgk-3dad.json"
LIEN = "https://data.cityofnewyork.us/resource/9rz4-mjek.json"
DEC = "https://data.ny.gov/resource/c6ci-rzpg.json"

# Quality Housing envelope by district: (max building height ft, lot coverage interior lot).
# Conservative pre-City-of-Yes QH heights; COY added ~10-20 ft in several districts, so a "does not fit"
# here is a real red flag, a "fits" is not a guarantee. Height-factor districts (R6, R7-1/2, R8) can
# go taller under the HF rules, so they get a generous allowance.
ENVELOPE = {
    "R6":(115,0.65),"R6-1":(115,0.65),"R6A":(85,0.65),"R6B":(55,0.60),"R7-1":(125,0.65),"R7-2":(125,0.65),"R7-3":(185,0.70),
    "R7A":(95,0.65),"R7B":(75,0.65),"R7D":(110,0.65),"R7X":(135,0.70),"R8":(155,0.70),"R8A":(135,0.70),"R8B":(85,0.70),
    "R8X":(165,0.70),"R9":(185,0.70),"R9A":(165,0.70),"R9X":(185,0.70),"R10":(235,0.70),"R10A":(215,0.70),
    "C1-4":(0,0),"C2-4":(0,0),"C4-4":(125,0.65),"C4-5X":(135,0.70),
}
AUTO_CLASSES = ("G",)          # PLUTO building class G* = garages, gas stations, car washes
AUTO_LANDUSE = ("10",)         # parking facilities

def _get(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def _chunks(seq, n):
    for i in range(0, len(seq), n): yield seq[i:i+n]

def _pad(block, lot):
    return str(int(block)).zfill(5), str(int(lot)).zfill(5)

def _flag(sev, code, text): return {"sev": sev, "code": code, "text": text}

def static_checks(d, p):
    """Checks that need only the PLUTO row (p) and the underwriting (d)."""
    f = []
    # --- geometry ---
    front = float(p.get("lotfront") or 0); depth = float(p.get("lotdepth") or 0)
    if 0 < front < 20: f.append(_flag("red", "narrow", f"frontage {front:.0f} ft: too narrow to build a code-compliant floor plate"))
    elif 0 < front < 25: f.append(_flag("amber", "narrow", f"frontage {front:.0f} ft: tight plate, single-stair / elevator placement will eat efficiency"))
    elif front >= 25: f.append(_flag("green", "frontage", f"frontage {front:.0f} ft × depth {depth:.0f} ft"))
    if str(p.get("irrlotcode", "")).lower() in ("true", "y", "1"): f.append(_flag("amber", "irregular", "irregular lot shape: get a survey before pricing the plate"))
    # --- zoning layers ---
    ov = [x for x in (p.get("overlay1"), p.get("overlay2")) if x]
    if ov: f.append(_flag("amber", "overlay", f"commercial overlay {'/'.join(ov)}: ground-floor retail allowed; check parking and use rules"))
    sp = [x for x in (p.get("spdist1"), p.get("spdist2"), p.get("spdist3")) if x]
    if sp: f.append(_flag("amber", "special_district", f"special district {'/'.join(sp)}: its own bulk, use and design rules override the base district"))
    if p.get("ltdheight"): f.append(_flag("red", "limited_height", f"limited height district {p.get('ltdheight')}: FAR may not be reachable"))
    if p.get("histdist"): f.append(_flag("red", "historic", f"historic district ({p.get('histdist')}): LPC approval for any new building, 12+ months"))
    if p.get("landmark"): f.append(_flag("red", "landmark", f"landmarked ({p.get('landmark')})"))
    if p.get("edesignum"): f.append(_flag("amber", "e_designation", f"E-designation {p.get('edesignum')}: hazmat / noise / air testing and remediation before a permit"))
    if str(p.get("firm07_flag") or "").strip() in ("1", "true", "True") or str(p.get("pfirm15_flag") or "").strip() in ("1", "true", "True"):
        f.append(_flag("amber", "flood", "FEMA flood zone (FIRM/PFIRM): elevated first floor, no residential below DFE, flood insurance"))
    # --- environmental history ---
    bc = str(p.get("bldgclass") or ""); lu = str(p.get("landuse") or "")
    if bc.startswith(AUTO_CLASSES) or lu in AUTO_LANDUSE:
        f.append(_flag("amber", "auto_use", f"prior auto / parking use (class {bc or lu}): Phase I and likely Phase II for petroleum"))
    # --- envelope: can the FAR physically fit? ---
    zone = str(p.get("zonedist1") or "")
    env = ENVELOPE.get(zone)
    lot = float(p.get("lotarea") or 0); far = float(d.get("max_far") or 0)
    if env and env[0] and lot:
        hgt, cov = env
        floors = max(1, int((hgt - 10) / 10))          # 10 ft floor-to-floor after a taller ground floor
        max_zfa = lot * cov * floors
        need = lot * far
        if need > max_zfa * 1.02:
            f.append(_flag("red", "envelope", f"FAR {far} needs {need:,.0f} SF but {floors} floors × {int(cov*100)}% coverage caps ~{max_zfa:,.0f} SF: usable FAR ≈ {max_zfa/lot:.2f}"))
            d["far_fits"] = round(max_zfa / lot, 2)
        else:
            f.append(_flag("green", "envelope", f"FAR fits: ~{floors} floors under {hgt} ft, {int(cov*100)}% coverage"))
            d["far_fits"] = far
    # --- street width (not in open data) ---
    f.append(_flag("manual", "street_width", "street width not in open data: confirm wide vs narrow street on ZoLa (changes R-district FAR)"))
    return f

def fetch_dob(bkl_by_boro):
    """bkl_by_boro: {boro_code: {(block5,lot5), ...}} -> {(boro,block5,lot5): {'dob_open':n,'dob_total':n,'types':[...]}}"""
    out = {}
    for boro, keys in bkl_by_boro.items():
        blocks = sorted({b for b, _ in keys})
        for chunk in _chunks(blocks, 60):
            inb = ",".join(f"'{b}'" for b in chunk)
            where = f"boro='{boro}' and block in({inb})"
            try:
                rows = _get(DOBV + f"?$select=boro,block,lot,violation_type,violation_category,issue_date,disposition_date&$where={urllib.parse.quote(where)}&$limit=50000")
            except Exception as e:
                print(f"  DOB violations chunk failed: {e}"); continue
            for r in rows:
                bl = (str(r.get("block") or "").zfill(5), str(r.get("lot") or "").zfill(5))
                if bl not in keys: continue
                k = (boro,) + bl
                o = out.setdefault(k, {"dob_open": 0, "dob_total": 0, "types": set()})
                o["dob_total"] += 1
                cat = str(r.get("violation_category") or "").upper()
                if "ACTIVE" in cat or ("DISMISSED" not in cat and not r.get("disposition_date")):
                    o["dob_open"] += 1
                    t = str(r.get("violation_type") or "")[:40]
                    if t: o["types"].add(t)
    return out

def fetch_ecb(bkl_by_boro):
    out = {}
    for boro, keys in bkl_by_boro.items():
        blocks = sorted({b for b, _ in keys})
        for chunk in _chunks(blocks, 60):
            inb = ",".join(f"'{b}'" for b in chunk)
            where = f"boro='{boro}' and block in({inb})"
            try:
                rows = _get(ECB + f"?$select=boro,block,lot,ecb_violation_status,balance_due,violation_type,issue_date,severity&$where={urllib.parse.quote(where)}&$limit=50000")
            except Exception as e:
                print(f"  ECB chunk failed: {e}"); continue
            for r in rows:
                bl = (str(r.get("block") or "").zfill(5), str(r.get("lot") or "").zfill(5))
                if bl not in keys: continue
                k = (boro,) + bl
                o = out.setdefault(k, {"ecb_active": 0, "ecb_balance": 0.0, "ecb_total": 0})
                o["ecb_total"] += 1
                if str(r.get("ecb_violation_status") or "").upper().startswith("ACTIVE"):
                    o["ecb_active"] += 1
                    try: o["ecb_balance"] += float(r.get("balance_due") or 0)
                    except Exception: pass
    return out

def fetch_lien_sale(bkl_by_boro):
    """Lots on the current/last DOF lien sale list (90-day list); keyed like the others."""
    out = {}
    names = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"}
    for boro, keys in bkl_by_boro.items():
        blocks = sorted({str(int(b)) for b, _ in keys})
        for chunk in _chunks(blocks, 80):
            inb = ",".join(chunk)
            where = f"borough='{boro}' and block in({inb})"
            try:
                rows = _get(LIEN + f"?$select=borough,block,lot,cycle,water_debt_only,tax_class_code&$where={urllib.parse.quote(where)}&$order=" + urllib.parse.quote("cycle DESC") + "&$limit=50000")
            except Exception as e:
                print(f"  lien sale chunk failed: {e}"); continue
            for r in rows:
                try: bl = (str(int(r.get("block"))).zfill(5), str(int(r.get("lot"))).zfill(5))
                except Exception: continue
                if bl not in keys: continue
                k = (boro,) + bl
                cur = out.get(k)
                if not cur or str(r.get("cycle") or "") > str(cur.get("cycle") or ""):
                    out[k] = {"cycle": r.get("cycle"), "water_only": str(r.get("water_debt_only") or "").upper().startswith("Y")}
    return out

def fetch_dec_sites():
    """NYS DEC remediation sites in the Bronx, Kings, Queens with coordinates."""
    try:
        rows = _get(DEC + "?$select=program_facility_name,siteclass,program_type,address1,locality,county,latitude,longitude&$where=" +
                    urllib.parse.quote("county in('Bronx','Kings','Queens')") + "&$limit=50000")
    except Exception as e:
        print(f"  DEC remediation fetch failed: {e}"); return []
    out = []
    for r in rows:
        try: out.append((float(r["latitude"]), float(r["longitude"]), r))
        except Exception: pass
    return out

def _dist_ft(lat1, lng1, lat2, lng2):
    dy = (lat2 - lat1) * 364000.0
    dx = (lng2 - lng1) * 364000.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)

def annotate(deals, pluto_by_bbl):
    """Attach d['diligence'] = {'flags':[...], 'red':n, 'amber':n, 'score':0-100} to each deal."""
    bkl_by_boro = {}
    key_of = {}
    for d in deals:
        p = pluto_by_bbl.get(d["bbl"])
        if not p: continue
        try:
            b5, l5 = _pad(p.get("block"), p.get("lot"))
        except Exception: continue
        boro = str(p.get("borocode") or "")
        bkl_by_boro.setdefault(boro, set()).add((b5, l5))
        key_of[d["bbl"]] = (boro, b5, l5)
    dob = fetch_dob(bkl_by_boro) if bkl_by_boro else {}
    ecb = fetch_ecb(bkl_by_boro) if bkl_by_boro else {}
    lien = fetch_lien_sale(bkl_by_boro) if bkl_by_boro else {}
    dec = fetch_dec_sites()
    print(f"  diligence: DOB {len(dob)} lots w/ violations · ECB {len(ecb)} · lien sale {len(lien)} · DEC sites {len(dec)}")
    for d in deals:
        p = pluto_by_bbl.get(d["bbl"])
        if not p: continue
        flags = static_checks(d, p)
        k = key_of.get(d["bbl"])
        v = dob.get(k) if k else None
        if v and v["dob_open"]:
            flags.append(_flag("amber", "dob", f"{v['dob_open']} open DOB violation(s) of {v['dob_total']} ({', '.join(sorted(v['types'])[:3])}): owner has a problem you can solve"))
            d["dob_open"] = v["dob_open"]
        else:
            flags.append(_flag("green", "dob", "no open DOB violations"))
        e = ecb.get(k) if k else None
        if e and e["ecb_active"]:
            flags.append(_flag("amber", "ecb", f"{e['ecb_active']} active ECB/OATH violation(s), ${e['ecb_balance']:,.0f} due: clears at closing from the price"))
            d["ecb_balance_due"] = int(e["ecb_balance"])
        else:
            flags.append(_flag("green", "ecb", "no active ECB balances"))
        l = lien.get(k) if k else None
        if l:
            flags.append(_flag("red" if not l["water_only"] else "amber", "lien_sale", f"on the DOF lien sale list ({l['cycle']}{', water debt only' if l['water_only'] else ''}): the most motivated seller there is, and a lien to clear"))
            d["lien_sale"] = l["cycle"]; d["motivated"] = True
        # ACRIS (set earlier by acris_distress): open mortgage
        if d.get("owner_tenure_years") is not None:
            if d.get("free_and_clear"): flags.append(_flag("green", "mortgage", "no mortgage recorded after the current deed"))
            else: flags.append(_flag("amber", "mortgage", "mortgage recorded after the deed: payoff at closing, lender consent for any JV"))
        if d.get("owner_on_deed") and d.get("owner") and d["owner_on_deed"].upper()[:12] != d["owner"].upper()[:12]:
            flags.append(_flag("amber", "name_mismatch", f"PLUTO owner '{d['owner']}' vs deed grantee '{d['owner_on_deed']}': confirm who holds title"))
        # DEC remediation sites within 150 ft
        if dec and d.get("lat") and d.get("lng"):
            near = [(round(_dist_ft(d["lat"], d["lng"], la, lo)), r) for la, lo, r in dec if abs(la - d["lat"]) < 0.002 and abs(lo - d["lng"]) < 0.002]
            near = sorted([(x, r) for x, r in near if x <= 150], key=lambda t: t[0])
            if near:
                x, r = near[0]
                sev = "red" if x <= 40 else "amber"
                flags.append(_flag(sev, "remediation", f"NYS DEC remediation site {x} ft away: {r.get('program_facility_name','')} ({r.get('program_type','')}, class {r.get('siteclass','')})"))
            else:
                flags.append(_flag("green", "remediation", "no DEC remediation site within 150 ft"))
        red = sum(1 for x in flags if x["sev"] == "red"); amber = sum(1 for x in flags if x["sev"] == "amber")
        d["diligence"] = {"flags": flags, "red": red, "amber": amber, "manual": [x["text"] for x in flags if x["sev"] == "manual"],
                          "checked": date.today().isoformat()}
        if any(x["code"] in ("narrow", "landmark", "historic") and x["sev"] == "red" for x in flags) and d.get("action") in ("offer", "negotiate"):
            d["action_before_diligence"] = d["action"]; d["action"] = "pass"
            d["action_why"] = "diligence: " + "; ".join(x["text"] for x in flags if x["sev"] == "red")[:200]
