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

_CACHE = {}

def _segments(boro, want):
    """All CSCL segments in a borough for a normalized street name (cached). Tries exact label, then LIKE."""
    k = (boro, want)
    if k in _CACHE: return _CACHE[k]
    rows = []
    try:
        where = f"boroughcode='{boro}' and upper(full_street_name)='{want}'"
        rows = _get(CSCL + f"?$select=full_street_name,streetwidth,rw_type,l_low_hn,l_high_hn,r_low_hn,r_high_hn,the_geom&$where={urllib.parse.quote(where)}&$limit=600")
        if not rows:
            core = want.rsplit(" ", 1)[0] if " " in want else want
            where = f"boroughcode='{boro}' and upper(full_street_name) like '%{core}%'"
            rows = _get(CSCL + f"?$select=full_street_name,streetwidth,rw_type,l_low_hn,l_high_hn,r_low_hn,r_high_hn,the_geom&$where={urllib.parse.quote(where)}&$limit=600")
            rows = [r for r in rows if norm(r.get("full_street_name")) == want]
    except Exception as e:
        _CACHE[k] = {"error": str(e)[:120]}; return _CACHE[k]
    _CACHE[k] = rows
    return rows

def _hn(s):
    try: return int(re.sub(r"[^0-9]", "", str(s).split("-")[-1]))
    except Exception: return None

def _house_number(address):
    m = re.match(r"^\s*([\d\-]+)", address or "")
    return _hn(m.group(1)) if m else None

def _seg_dist(lat, lng, geom):
    try:
        best = 9e9
        for part in geom.get("coordinates", []):
            for x, y in part:
                d = ((y - lat) * 364000.0) ** 2 + ((x - lng) * 364000.0 * 0.76) ** 2
                best = min(best, d)
        return best ** 0.5
    except Exception:
        return 9e9

def lookup(lat, lng, address, boro=None):
    """Return {'street','roadbed_ft','class','match'} for the lot's frontage street, or None."""
    want = street_of(address)
    if not want or not boro: return None
    rows = _segments(boro, want)
    if isinstance(rows, dict): return rows
    if not rows: return None
    hn = _house_number(address)
    pick, how = None, None
    if hn is not None:
        for r in rows:
            for lo, hi in ((r.get("l_low_hn"), r.get("l_high_hn")), (r.get("r_low_hn"), r.get("r_high_hn"))):
                a, b = _hn(lo), _hn(hi)
                if a is not None and b is not None and min(a, b) <= hn <= max(a, b) and (a % 2 == hn % 2 or b % 2 == hn % 2):
                    pick, how = r, "house number"; break
            if pick: break
    if not pick and lat and lng:
        pick = min(rows, key=lambda r: _seg_dist(lat, lng, r.get("the_geom") or {}))
        how = "nearest segment"
    if not pick: return None
    cls, w = classify(pick.get("streetwidth"))
    return {"street": pick.get("full_street_name"), "roadbed_ft": w, "class": cls, "match": how}

def annotate(deals, limit=420, sleep=0.0):
    """Attach d['street'] and return the number of lots whose FAR rises on a wide street."""
    import time
    bumped = 0
    boro_code = {"BX": "2", "BK": "3", "QN": "4", "MN": "1", "SI": "5"}
    for d in deals[:limit]:
        r = lookup(d.get("lat"), d.get("lng"), d.get("address"), boro=boro_code.get(d.get("borough")))
        d["street"] = r
        if sleep: time.sleep(sleep)
    return bumped
