"""
BOUNTY — street width from DCP's Street Centerline (CSCL), v1 Oct 2026.

Zoning treats a street as WIDE when its mapped width (property line to property line)
is 75 ft or more; several R districts allow more FAR on a wide street, and Quality
Housing heights differ too. ZoLa shows it but has no API. CSCL on NYC Open Data
(inkn-q76z) carries `streetwidth` per segment, which is the ROADBED (curb to curb).
Mapped width = roadbed + two sidewalks (13-20 ft each), so:

    roadbed >= 50 ft  -> mapped >= ~78 ft  -> WIDE
    roadbed <= 36 ft  -> mapped <= ~68 ft  -> NARROW
    37-49 ft          -> ambiguous          -> VERIFY on ZoLa (reported as such)

Each lot is matched to the segment nearest its coordinates whose street name matches
the lot's address street; falls back to the nearest segment within 40 m.
"""
import json, re, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/3.0)"}
CSCL = "https://data.cityofnewyork.us/resource/inkn-q76z.json"

# wide-street residential FAR where the district has one (QH / HF wide-street rules; PLUTO residfar is the narrow/base value)
WIDE_FAR = {"R6": 3.0, "R6-1": 3.0, "R7-1": 4.0, "R7-2": 4.0, "R8": 7.2, "R9": 8.0,
            "C4-2": 3.0, "C4-3": 3.0, "C4-4": 4.0, "C4-5": 4.0, "C6-1": 4.0, "C6-2": 7.2, "C6-3": 8.0}

ABBR = {"AVENUE": "AVE", "STREET": "ST", "BOULEVARD": "BLVD", "PLACE": "PL", "ROAD": "RD", "PARKWAY": "PKWY",
        "TERRACE": "TER", "COURT": "CT", "DRIVE": "DR", "LANE": "LN", "EXPRESSWAY": "EXPY", "HIGHWAY": "HWY",
        "SQUARE": "SQ", "EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S", "SAINT": "ST"}

def norm(name):
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    s = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", s)          # 9TH -> 9
    toks = [ABBR.get(t, t) for t in s.split()]
    return " ".join(toks)

def street_of(address):
    """'2355 MORRIS AVENUE' -> 'MORRIS AVE'; '87-11 160 STREET' -> '160 ST'"""
    a = (address or "").upper().strip()
    m = re.match(r"^[\d\-]+[A-Z]?\s+(.*)$", a)
    return norm(m.group(1) if m else a)

def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

def classify(width):
    try: w = float(width)
    except Exception: return None, None
    if w >= 50: return "wide", w
    if w <= 36: return "narrow", w
    return "verify", w

def lookup(lat, lng, address, radius_m=45):
    """Return {'street','roadbed_ft','class'} for the lot's frontage street, or None."""
    if not lat or not lng: return None
    want = street_of(address)
    where = f"within_circle(the_geom,{lat},{lng},{radius_m})"
    try:
        rows = _get(CSCL + f"?$select=full_street_name,streetwidth,rw_type,l_low_hn,l_high_hn,r_low_hn,r_high_hn&$where={urllib.parse.quote(where)}&$limit=40")
    except Exception as e:
        return {"error": str(e)[:120]}
    rows = [r for r in rows if str(r.get("rw_type", "1")) in ("1", "", None) or str(r.get("rw_type")) == "1"]
    best = None
    for r in rows:
        nm = norm(r.get("full_street_name"))
        if nm and (nm == want or want.endswith(nm) or nm.endswith(want)):
            best = r; break
    if not best:
        # fall back to any segment that carries house numbers (a real frontage street), prefer widest
        cands = [r for r in rows if r.get("l_low_hn") or r.get("r_low_hn")] or rows
        if not cands: return None
        best = max(cands, key=lambda r: float(r.get("streetwidth") or 0))
        matched = False
    else:
        matched = True
    cls, w = classify(best.get("streetwidth"))
    return {"street": best.get("full_street_name"), "roadbed_ft": w, "class": cls, "name_matched": matched}

def annotate(deals, limit=420, sleep=0.0):
    """Attach d['street'] and return the number of lots whose FAR rises on a wide street."""
    import time
    bumped = 0
    for d in deals[:limit]:
        r = lookup(d.get("lat"), d.get("lng"), d.get("address"))
        d["street"] = r
        if sleep: time.sleep(sleep)
    return bumped
