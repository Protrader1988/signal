"""
BOUNTY — Zoning Radar (v1, Oct 2026). See the density before the market prices it.

Three layers, each labeled by how real it is:

  1. NYC REZONINGS (real, parcel-level). DCP's Zoning Application Portal open
     data: every map/text amendment with status, dates, community district, the
     applicant, and the tax lots inside the rezoning area (ZAP Project BBLs).
     The project brief is parsed for "R6 to R7A"-style district changes and
     scored as an FAR delta. nyc_scan.py joins this file to the deal board:
     a lot INSIDE a rezoning gets the proposed FAR underwritten; a lot on the
     SAME BLOCK as a private rezoning gets an "adjacent" flag.
       projects: https://data.cityofnewyork.us/resource/hgx4-8ukb.json
       bbls:     https://data.cityofnewyork.us/resource/2iga-a6mk.json
       detail:   https://zap.planning.nyc.gov/projects/<project_id>

  2. MUNICIPAL NEWS RADAR (signal, not record). Westchester / Putnam / Dutchess
     towns, the CT Gold Coast and Bergen/Hudson NJ have no standardized zoning
     feed, so this layer polls Google News RSS per municipality for density
     language (ADU, duplex, overlay, TOD, upzone, mixed-use, redevelopment
     plan, Fourth Round, 8-30g, housing growth plan) and scores each hit.
     It tells you where to read the agenda, not what the ordinance says.

  3. STATE LAW TRACKER (curated, dated, sourced). Statewide changes that move
     density in many towns at once. Edited by hand in STATE_TRACKER below;
     every entry carries a verified-on date and a source link.

Output: bounty/data/zoning_radar.json
"""
import json, os, re, time, urllib.request, urllib.parse, traceback, html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date, timedelta

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/3.0)"}
ZAP_PROJECTS = "https://data.cityofnewyork.us/resource/hgx4-8ukb.json"
ZAP_BBLS = "https://data.cityofnewyork.us/resource/2iga-a6mk.json"
SINCE_DAYS = 730          # rezonings filed or moved in the last 2 years
NEWS_DAYS = 120

# Max residential FAR by district (PLUTO residfar convention: height-factor max where it exceeds QH).
# MIH columns: FAR when an MIH area is mapped on the district (ZR 23-154 / City of Yes).
DISTRICT_FAR = {
    "R1":0.5,"R1-1":0.5,"R1-2":0.5,"R2":0.5,"R2A":0.5,"R2X":0.85,"R3-1":0.5,"R3-2":0.5,"R3A":0.5,"R3X":0.5,
    "R4":0.75,"R4-1":0.75,"R4A":0.75,"R4B":0.9,"R5":1.25,"R5A":1.1,"R5B":1.35,"R5D":2.0,
    "R6":2.43,"R6-1":2.43,"R6A":3.0,"R6B":2.0,"R7-1":3.44,"R7-2":3.44,"R7-3":5.0,"R7A":4.0,"R7B":3.0,"R7D":4.2,"R7X":5.0,
    "R8":6.02,"R8A":6.02,"R8B":4.0,"R8X":6.02,"R9":7.52,"R9A":7.52,"R9D":9.0,"R9X":9.0,"R10":10.0,"R10A":10.0,"R10X":10.0,
    # commercial districts -> residential equivalent
    "C1-6":3.44,"C1-6A":4.0,"C1-7":6.02,"C1-7A":6.02,"C1-8":7.52,"C1-8A":7.52,"C1-8X":9.0,"C1-9":10.0,"C1-9A":10.0,
    "C2-6":3.44,"C2-6A":4.0,"C2-7":6.02,"C2-7A":6.02,"C2-7X":6.02,"C2-8":7.52,"C2-8A":7.52,
    "C4-1":1.25,"C4-2":2.43,"C4-2A":3.0,"C4-3":2.43,"C4-3A":3.0,"C4-4":3.44,"C4-4A":4.0,"C4-4D":6.02,"C4-4L":4.0,
    "C4-5":3.44,"C4-5A":4.0,"C4-5D":6.02,"C4-5X":5.0,"C4-6":10.0,"C4-6A":10.0,"C4-7":10.0,
    "C5-1":10.0,"C5-2":10.0,"C5-3":10.0,"C6-1":3.44,"C6-2":6.02,"C6-2A":6.02,"C6-3":7.52,"C6-3A":7.52,"C6-3D":9.0,"C6-3X":9.0,
    "C6-4":10.0,"C6-4A":10.0,"C6-4X":10.0,"C6-5":10.0,"C6-6":10.0,"C6-7":10.0,"C6-8":10.0,"C6-9":10.0,"C6-12":12.0,
    "C8-1":0.0,"C8-2":0.0,"C8-3":0.0,"C8-4":0.0,"M1-1":0.0,"M1-2":0.0,"M1-3":0.0,"M1-4":0.0,"M1-5":0.0,"M1-6":0.0,
    "M2-1":0.0,"M2-2":0.0,"M3-1":0.0,
}
MIH_FAR = {"R6":3.6,"R6A":3.6,"R6B":2.2,"R7-1":4.6,"R7-2":4.6,"R7A":4.6,"R7B":3.0,"R7D":5.6,"R7X":6.0,
           "R8":7.2,"R8A":7.2,"R8X":7.2,"R9":8.0,"R9A":8.5,"R9D":10.0,"R9X":9.7,"R10":12.0,"R10A":12.0,"R10X":12.0,
           "C4-4D":7.2,"C4-5D":7.2,"C4-5X":6.0,"C4-6":12.0,"C6-2":7.2,"C6-3":8.0,"C6-4":12.0,"C6-12":12.0,
           "C1-6A":4.6,"C2-6A":4.6,"C1-7A":7.2,"C2-7A":7.2,"C2-8A":8.5,"C1-8A":8.5}

DIST_RE = re.compile(r"\b((?:R|C|M)\d{1,2}(?:-\d{1,2})?[A-Z]{0,2})(?:/(?:C\d-\d[A-Z]?))?(?:\([A-Z]+\))?", re.I)

def get(url, timeout=90, raw=False):
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=timeout).read()
    return data if raw else json.loads(data)

def parse_change(brief):
    """'rezone C8-2 to R9A/C2-4' -> (['C8-2'], ['R9A']) using the first 'X to Y' pair in the brief."""
    if not brief: return [], []
    b = brief.replace("–", "-")
    m = re.search(r"(?:from\s+)?([RCM]\d[\w/\-\(\)]*(?:\s*(?:,|and|&)\s*[RCM]\d[\w/\-\(\)]*)*)\s+(?:to|into)\s+((?:an?\s+)?[RCM]\d[\w/\-\(\)]*(?:\s*(?:,|and|&)\s*[RCM]\d[\w/\-\(\)]*)*)", b, re.I)
    if not m: return [], []
    def dists(s):
        out = []
        for d in DIST_RE.findall(s):
            d = d.upper()
            if d in DISTRICT_FAR and d not in out: out.append(d)
        return out
    return dists(m.group(1)), dists(m.group(2))

def far_of(dists, mih=False):
    best = 0.0
    for d in dists:
        f = MIH_FAR.get(d) if mih else None
        best = max(best, f if f is not None else DISTRICT_FAR.get(d, 0.0))
    return best

def stage_of(p):
    if p.get("completed_date"): return "completed", 5
    if p.get("approval_date"): return "approved", 4
    st = (p.get("public_status") or "").lower()
    if "review" in st or p.get("certified_referred"): return "in public review", 3
    if "noticed" in st: return "noticed", 2
    if "withdrawn" in st or "disapproved" in st: return "withdrawn", 0
    return "filed", 1

def fetch_zap():
    since = (date.today() - timedelta(days=SINCE_DAYS)).isoformat()
    where = (f"(app_filed_date > '{since}' OR current_milestone_date > '{since}') "
             "AND (actions like '%ZM%' OR actions like '%ZR%') "
             "AND borough in('Bronx','Brooklyn','Queens','Manhattan')")
    fields = ("project_id,project_name,project_brief,project_status,public_status,actions,ulurp_numbers,"
              "primary_applicant,applicant_type,borough,community_district,cc_district,current_milestone,"
              "current_milestone_date,app_filed_date,noticed_date,certified_referred,approval_date,completed_date,mih_flag")
    out, off = [], 0
    while off < 6000:
        url = (ZAP_PROJECTS + f"?$select={urllib.parse.quote(fields)}&$where={urllib.parse.quote(where)}"
               f"&$order={urllib.parse.quote('app_filed_date DESC')}&$limit=1000&$offset={off}")
        page = get(url); out += page
        if len(page) < 1000: break
        off += 1000
    return out

def fetch_bbls(project_ids):
    """project_id -> list of bbl strings."""
    out = {}
    ids = list(project_ids)
    for i in range(0, len(ids), 80):
        chunk = ids[i:i+80]
        inb = ",".join(f"'{x}'" for x in chunk)
        url = ZAP_BBLS + f"?$select=project_id,bbl&$where={urllib.parse.quote('project_id in(' + inb + ')')}&$limit=50000"
        try:
            for r in get(url):
                b = str(r.get("bbl") or "").split(".")[0]
                if b: out.setdefault(r["project_id"], []).append(b)
        except Exception as e:
            print(f"  zap bbls chunk failed: {e}")
    return out

def build_nyc():
    projects = fetch_zap()
    print(f"ZAP: {len(projects)} map/text amendments since {SINCE_DAYS}d")
    items = []
    for p in projects:
        brief = p.get("project_brief") or ""
        frm, to = parse_change(brief)
        mih = (p.get("mih_flag") in (True, "true", "True")) or ("mih" in brief.lower()) or ("mandatory inclusionary" in brief.lower())
        far_from, far_to = far_of(frm), far_of(to, mih=mih)
        kind = "text amendment"
        acts = (p.get("actions") or "").upper()
        if "ZM" in acts:
            if to and far_to > far_from: kind = "upzoning"
            elif to and far_to < far_from and far_from > 0: kind = "downzoning"
            elif any(d.startswith("C1") or d.startswith("C2") for d in DIST_RE.findall(brief.upper())) and not to: kind = "commercial overlay"
            else: kind = "map amendment"
        stage, rank = stage_of(p)
        if rank == 0: continue
        units = None
        mu = re.search(r"(\d{2,4})\s+(?:dwelling\s+)?units", brief, re.I)
        if mu: units = int(mu.group(1))
        items.append({
            "project_id": p.get("project_id"), "name": p.get("project_name"), "kind": kind,
            "borough": p.get("borough"), "cd": p.get("community_district"), "council": p.get("cc_district"),
            "applicant": p.get("primary_applicant"), "applicant_type": p.get("applicant_type"),
            "from": frm, "to": to, "far_from": far_from, "far_to": far_to,
            "far_delta_pct": (round((far_to/far_from - 1)*100) if far_from and far_to else None),
            "mih": mih, "units_proposed": units,
            "stage": stage, "stage_rank": rank, "milestone": p.get("current_milestone"),
            "milestone_date": (p.get("current_milestone_date") or "")[:10],
            "filed": (p.get("app_filed_date") or "")[:10], "certified": (p.get("certified_referred") or "")[:10],
            "approved": (p.get("approval_date") or "")[:10],
            "ulurp": p.get("ulurp_numbers"), "brief": brief[:600],
            "url": f"https://zap.planning.nyc.gov/projects/{p.get('project_id')}",
        })
    bbls = fetch_bbls([i["project_id"] for i in items if i["project_id"]])
    for i in items:
        i["bbls"] = bbls.get(i["project_id"], [])
        i["lots"] = len(i["bbls"])
        i["blocks"] = sorted({b[:6] for b in i["bbls"]})   # boro+block prefix (1+5 digits)
    # most useful first: upzonings, then by stage, then size
    items.sort(key=lambda i: ({"upzoning":3,"map amendment":2,"text amendment":1,"commercial overlay":1,"downzoning":0}[i["kind"]],
                              i["stage_rank"], i["lots"]), reverse=True)
    return items

# ---------------- municipal news radar ----------------
MUNIS = {
    "NY": ["Yonkers","Mount Vernon","New Rochelle","White Plains","Peekskill","Ossining","Port Chester","Tarrytown",
           "Sleepy Hollow","Mamaroneck","Harrison","Rye","Scarsdale","Greenburgh","Mount Kisco","Bedford","Yorktown",
           "Cortlandt","Somers","Croton-on-Hudson","Pleasantville","Dobbs Ferry","Hastings-on-Hudson","Irvington",
           "Carmel","Brewster","Putnam Valley","Philipstown","Cold Spring",
           "Beacon","Fishkill","Wappingers","Poughkeepsie","Hyde Park","Rhinebeck","Red Hook","Pawling"],
    "CT": ["Greenwich","Stamford","Darien","New Canaan","Westport","Norwalk","Ridgefield","Fairfield","Danbury","New Haven"],
    "NJ": ["Fort Lee","Edgewater","Englewood","Englewood Cliffs","Cliffside Park","Fairview","Palisades Park","Leonia",
           "Ridgefield NJ","Hackensack","Teaneck","Bogota","North Bergen","West New York","Union City","Weehawken",
           "Guttenberg","Hoboken","Jersey City","Tenafly","Bergenfield"],
    "NYC": ["City Planning Commission rezoning","ULURP rezoning Bronx","ULURP rezoning Brooklyn","ULURP rezoning Queens",
            "City of Yes housing","HPD RFP affordable housing site","Council land use rezoning Bronx","Council land use rezoning Brooklyn"],
}
TOPIC = '(rezoning OR zoning OR "accessory dwelling" OR ADU OR duplex OR overlay OR "transit-oriented" OR upzone OR "mixed-use" OR "redevelopment plan" OR "affordable housing plan" OR "fourth round" OR 8-30g OR "housing growth plan" OR "City of Yes")'
DENSITY_WORDS = {
    "upzon": 30, "rezon": 20, "accessory dwelling": 25, "adu": 15, "duplex": 20, "two-family": 15, "overlay": 20,
    "transit-oriented": 25, "tod ": 10, "mixed-use": 15, "multifamily": 15, "multi-family": 15, "density": 15,
    "redevelopment plan": 25, "area in need of redevelopment": 25, "pilot": 8, "fourth round": 25, "fair share": 15,
    "inclusionary": 15, "8-30g": 25, "housing growth plan": 20, "priority housing development zone": 30,
    "as-of-right": 20, "as of right": 20, "height": 8, "stories": 8, "units": 8, "approved": 10, "adopted": 15,
    "ordinance": 10, "moratorium": -20, "lawsuit": -10, "rejected": -15, "denied": -15,
}
# headline words that mean a same-named place somewhere else (or a listing, not a zoning story)
COLLISIONS = {"bedford": ("virginia","lynchburg","bedford county","bedford-stuy","bedford stuyvesant","bedford ave","bedford avenue","massachusetts","texas"),
              "greenwich": ("village","penthouse","london","greenwich st","greenwich street","greenwich ave"),
              "rye": ("whiskey","bread","rye brook ny","rye, nh"), "carmel": ("indiana","california","carmel-by-the-sea","carmel valley"),
              "beacon": ("beacon hill","beacon st","beacon street","lighthouse"), "red hook": ("brooklyn",), "hyde park": ("chicago","london","hyde park ave"),
              "fairview": ("fairview heights","oregon","tennessee","texas","north carolina","nc"), "ridgefield": ("washington","wa"),
              "harrison": ("new jersey","nj","arkansas","harrison county","harrison township"), "newburgh": ("newburgh heights",),
              "hastings-on-hudson": (), "mamaroneck": (), "peekskill": (), "yonkers": (), "union city": ("california","georgia","tennessee"),
              "fort lee": ("virginia","fort lee va"), "englewood": ("colorado","florida","chicago","ohio"), "hackensack": (), "teaneck": (),
              "new canaan": (), "darien": ("georgia","illinois"), "westport": ("massachusetts","washington"), "norwalk": ("california","ohio","iowa"),
              "stamford": ("stamford bridge","lincolnshire","england"), "danbury": ("texas","nh"), "fairfield": ("california","iowa","ohio","new jersey","fairfield county, ohio")}
NEG = ("obituar", "penthouse", "asking price", "lynchburg", "virginia beach", "sports", "football", "basketball", "arrest", "crash", "closes sale", "for sale", "listed for", "asks $",
       "sells for", "curbed", "raleigh", "charlotte", "mecklenburg", "ohio", "michigan", "texas", "florida", "california",
       "brooklyn park", "brooklyn center", "brooklyn village", "queens park", "bronx zoo")

def news_for(q, region, muni):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        raw = get(url, timeout=40, raw=True)
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  news {muni}: {e}"); return []
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_DAYS)
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            dt = datetime.strptime(pub[:25], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        if dt and dt < cutoff: continue
        t = title.lower()
        if any(n in t for n in NEG): continue
        mname = muni.lower().split(" nj")[0]
        if region != "NYC" and mname not in t: continue        # the town must be in the headline, not just the query
        if region != "NYC" and any(c in t for c in COLLISIONS.get(mname, ())): continue
        if region == "NYC" and not any(k in t for k in ("bronx","brooklyn","queens","manhattan","nyc","new york","city planning commission","hpd","ulurp","city of yes")): continue
        score = sum(w for k, w in DENSITY_WORDS.items() if k in t)
        if region != "NYC": score += 10
        if score < 20: continue
        out.append({"region": region, "muni": muni, "title": title, "link": link, "source": src,
                    "date": dt.date().isoformat() if dt else None, "score": score})
    return out

def build_news():
    items, seen = [], set()
    for region, munis in MUNIS.items():
        for m in munis:
            if region == "NYC":
                q = m
            else:
                q = f'"{m.replace(" NJ","")}" {TOPIC}'
                q += {"NJ": " NJ", "CT": " Connecticut", "NY": " NY"}[region]
            for it in news_for(q, region, m):
                k = it["title"].lower()[:80]
                if k in seen: continue
                seen.add(k); items.append(it)
            time.sleep(1.2)
    items.sort(key=lambda i: (i["score"], i["date"] or ""), reverse=True)
    print(f"news radar: {len(items)} scored items")
    return items

# ---------------- state law tracker (curated; verify dates at the source) ----------------
STATE_TRACKER = [
    {"state": "NYC", "name": "City of Yes for Housing Opportunity", "status": "in effect", "date": "2024-12-05",
     "what": "Universal Affordability Preference: ~20% more floor area in R6-R10 when the extra space is permanently affordable at a 60% AMI average; parking minimums removed or reduced by zone; ADUs and small-lot infill; town-center zoning (housing above stores) in low-density districts; office-to-residential conversions citywide for buildings built before 1991.",
     "play": "Every R6-R10 lot on the deal board already carries the UAP path. Low-density districts (R1-R5) now allow ADUs and 3-5 story 'town center' buildings on commercial streets: a separate screen worth adding.",
     "source": "https://www.nyc.gov/site/planning/plans/city-of-yes/city-of-yes-housing-opportunity.page", "verified": "2026-10-23"},
    {"state": "NYC", "name": "485-x (Affordable Neighborhoods for New Yorkers)", "status": "in effect", "date": "2024-04-20",
     "what": "Replaces 421-a. Option B (6-99 units): 20% affordable at 80% AMI avg, 35-year exemption, no construction wage rule. Option C (6-10 units): 10-year exemption, half stabilized. Start construction by June 15, 2034.",
     "play": "Underwritten on every NYC deal. The 6-10 unit Option C is the solo-builder path.",
     "source": "https://www.nyc.gov/site/hpd/services-and-information/tax-incentives-485-x.page", "verified": "2026-10-23"},
    {"state": "NY", "name": "Faith-Based Affordable Housing Act (S3397B / A3647B)", "status": "pending in committee", "date": "2026-01-07",
     "what": "Would let religious corporations build apartments as of right on their land: outside NYC up to 30 units/acre (50 if subsidized) and +20 ft height, with 20% at 80% AMI; in NYC, height and coverage of nearby taller districts with 25% at 60% AMI. 60-day local approval, no parking minimums, SEQRA EIS exemption with a Phase I.",
     "play": "If it passes, every faith-owned lot on the board (NYC filter: Faith-owned) and every church parcel in Westchester becomes a by-right site. Watch the bill; line up congregations now.",
     "source": "https://www.nysenate.gov/legislation/bills/2025/S3397/amendment/B", "verified": "2026-10-23"},
    {"state": "NY", "name": "Pro-Housing Communities certification", "status": "in effect", "date": "2023-07-18",
     "what": "Municipalities that certify (and commit to growing housing stock or adopting pro-housing zoning) get priority for state discretionary funds. Certified towns are the ones adopting ADU, mixed-use and TOD changes.",
     "play": "Cross-check any suburban deal's town against the certified list; a certified town is likelier to approve density and to have a zoning change in process.",
     "source": "https://www.ny.gov/programs/pro-housing-communities-program", "verified": "2026-10-23"},
    {"state": "CT", "name": "HB 8002 Housing Growth Act", "status": "signed", "date": "2025-11-26",
     "what": "As-of-right middle and multifamily housing in qualifying transit-oriented districts; municipalities encouraged to adopt Priority Housing Development Zones (as-of-right multifamily); parking requirements cut for projects of 16 or fewer units and capped above that; harder to petition against zoning amendments; mandatory municipal or regional Housing Growth Plans (Fairfield County towns by June 2029).",
     "play": "Stamford, Norwalk, Darien, Westport and Greenwich station areas are the first places to look for TOD-district sites. Track each town's Housing Growth Plan and PHDZ adoption on the news radar.",
     "source": "https://www.carmodylaw.com/housing-growth-bill-brings-substantial-land-use-reform-to-connecticut/", "verified": "2026-10-23"},
    {"state": "CT", "name": "CGS 8-30g Affordable Housing Appeals", "status": "in effect", "date": "1990-01-01",
     "what": "In towns under 10% affordable stock, a project with 30% deed-restricted units can appeal a zoning denial and the town must prove a public-health or safety reason to deny. Greenwich, Darien, New Canaan, Westport, Ridgefield are under 10%; Stamford and New Haven are exempt.",
     "play": "The CT board's lever. Sewer availability decides density far more than the statute.",
     "source": "https://portal.ct.gov/doh/doh/programs/affordable-housing-appeals-listing", "verified": "2026-10-23"},
    {"state": "NJ", "name": "Fourth Round affordable housing obligations (2025-2035)", "status": "in effect; ordinances were due 2026-03-15", "date": "2026-03-15",
     "what": "Every municipality had to adopt a Housing Element and Fair Share Plan by June 30, 2025 and the implementing zoning (inclusionary and overlay zones, redevelopment plans) by March 15, 2026. Towns that miss lose immunity from exclusionary-zoning (builder's remedy) litigation.",
     "play": "The adopted ordinances are new overlay zones with real density, often on commercial corridors. Pull each Bergen/Hudson town's HEFSP for the site list; towns out of compliance are builder's-remedy targets.",
     "source": "https://www.foxrothschild.com/publications/new-jersey-passes-affordable-housing-law", "verified": "2026-10-23"},
    {"state": "NJ", "name": "Local Redevelopment and Housing Law (40A:12A) + Long Term Tax Exemption (40A:20)", "status": "in effect", "date": "1992-01-01",
     "what": "A municipality can designate an Area in Need of Redevelopment, adopt a redevelopment plan with its own density, and convey sites directly to a designated redeveloper with a ~30-year PILOT.",
     "play": "The NJ board's lever. New redevelopment-plan adoptions show up on the news radar first.",
     "source": "https://www.nj.gov/dca/dlps/", "verified": "2026-10-23"},
]

def main():
    os.makedirs("bounty/data", exist_ok=True)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "nyc": [], "news": [], "state_tracker": STATE_TRACKER,
           "notes": {"nyc": "DCP ZAP open data, map (ZM) and text (ZR) amendments filed or moved in the last 24 months. FAR deltas parsed from the project brief; verify on the ZAP page.",
                     "news": "Google News RSS per municipality, scored on density language. A signal to read the agenda, not a record of an adopted ordinance.",
                     "state_tracker": "Curated statewide changes with verified-on dates. Edit STATE_TRACKER in zoning_radar.py."}}
    try:
        out["nyc"] = build_nyc()
    except Exception as e:
        print(f"ZAP layer failed: {e}"); traceback.print_exc()
    try:
        out["news"] = build_news()
    except Exception as e:
        print(f"news layer failed: {e}")
    out["counts"] = {"nyc_total": len(out["nyc"]),
                     "nyc_upzonings": sum(1 for i in out["nyc"] if i["kind"] == "upzoning"),
                     "nyc_in_review_or_approved": sum(1 for i in out["nyc"] if i["stage_rank"] >= 3),
                     "nyc_lots_covered": sum(i["lots"] for i in out["nyc"]),
                     "news_items": len(out["news"])}
    json.dump(out, open("bounty/data/zoning_radar.json", "w"), indent=1)
    print("zoning radar:", out["counts"])

if __name__ == "__main__":
    try: main()
    except Exception:
        os.makedirs("bounty/data", exist_ok=True)
        open("bounty/data/ZONING_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL"); raise SystemExit(1)
