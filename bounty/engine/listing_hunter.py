"""BOUNTY listing hunter: finds NEW development listings every day.

LoopNet, Crexi, Zillow and the MLS portals have no free API and block scrapers, so an AI
model with live web search does the reading: one search prompt per target area per day,
asking only for listings it can cite with a URL. Every hit is appended to
bounty/data/feasibility_sites.json, so the next memo run gives it the full
land-use / test-fit review automatically, and it shows on the board as "found by search".

Engines (first that works is used):
  Gemini with Google Search grounding   (GEMINI_API_KEY; grounding is billed per prompt on paid keys)
  Claude with the web search tool        (ANTHROPIC_API_KEY, optional)

Budget: one prompt per area per day (10 areas) and up to 15 results each.
"""
import json, os, re, time, urllib.error, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SITES = os.path.join(DATA, "feasibility_sites.json")
SEEN = os.path.join(DATA, "hunter_seen.json")
GEM_MODELS = ["gemini-3.6-flash", "gemini-flash-latest"]
GEM_API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
CLAUDE_MODEL = os.environ.get("HUNTER_CLAUDE_MODEL", "claude-haiku-5-5")

AREAS = [
    ("Bronx, NY", "NYC", "BX"), ("Astoria and Long Island City, Queens, NY", "NYC", "QN"),
    ("Williamsburg and Greenpoint, Brooklyn, NY", "NYC", "BK"), ("DUMBO, Vinegar Hill and Downtown Brooklyn, NY", "NYC", "BK"),
    ("Yonkers, NY", "Yonkers", None), ("Mount Vernon, New Rochelle and Port Chester, NY", "Westchester", None),
    ("White Plains, Peekskill, Ossining, Tarrytown and Sleepy Hollow, NY", "Westchester", None),
    ("Bridgeport, Stratford, Norwalk and Stamford, CT", "CT", None),
    ("Danbury, Bethel, Shelton, Fairfield, Westport and Greenwich, CT", "CT", None),
    ("Jersey City, Hoboken, Newark, Paterson and Hackensack, NJ", "NJ", None),
]

PROMPT = """Search the web now for properties currently FOR SALE in {area} that a developer would buy to build
multifamily housing: development land, vacant lots, teardown or underbuilt buildings on land zoned for more units,
industrial or commercial sites zoned or rezoned for residential, and sites with approved plans or approvals.
Look at LoopNet, Crexi, Zillow, Realtor.com, Redfin, StreetEasy, Showcase, CommercialCafe, CityFeet, PropertyShark,
OneKey or SmartMLS public pages, and brokerage sites. Prefer listings posted or updated in the last 60 days.
Asking price up to $4,000,000 or unpriced.
Report ONLY listings you actually saw in search results, each with the URL where you saw it. Never guess an address,
price, size or approval. Use null for anything the page did not state.
Return ONLY a JSON array (max 15 items) of objects:
{{"address":"street address","town":"city","state":"NY|NJ|CT","price":number or null,"lot_sf":number or null,
"zoning":"as stated or null","approved_units":number or null,"buildable_sf":number or null,
"headline":"listing title, <= 90 chars","url":"https://...","source":"site name","updated":"date shown or null"}}"""


def http_json(url, body, headers, timeout=180):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def ask_gemini(key, prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.1}}
    last = None
    for m in GEM_MODELS:
        try:
            j = http_json(GEM_API.format(m=m) + f"?key={key}", body, {"Content-Type": "application/json"})
            return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"]), "gemini:" + m
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")[:400]
            print(f"  gemini {m}: HTTP {e.code} {err}")
            last = RuntimeError(f"gemini HTTP {e.code}: {err[:160]}")
        except Exception as e:
            print(f"  gemini {m}: {e}")
            last = e
    raise last


def ask_claude(key, prompt):
    body = {"model": CLAUDE_MODEL, "max_tokens": 4000,
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            "messages": [{"role": "user", "content": prompt}]}
    try:
        j = http_json("https://api.anthropic.com/v1/messages", body,
                      {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="ignore")[:400]
        print(f"  claude: HTTP {e.code} {err}")
        raise RuntimeError(f"claude HTTP {e.code}: {err[:160]}")
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text"), "claude:" + CLAUDE_MODEL


def parse(text):
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return [x for x in arr if isinstance(x, dict)]
    except Exception:
        return []


def norm(a, town):
    s = f"{a} {town}".upper()
    s = re.sub(r"\bSTREET\b", "ST", s); s = re.sub(r"\bAVENUE\b", "AVE", s); s = re.sub(r"\bROAD\b", "RD", s)
    s = re.sub(r"\bBOULEVARD\b", "BLVD", s); s = re.sub(r"[^A-Z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    gkey, ckey = os.environ.get("GEMINI_API_KEY", ""), os.environ.get("ANTHROPIC_API_KEY", "")
    sites = json.load(open(SITES))
    seen = json.load(open(SEEN)) if os.path.exists(SEEN) else {}
    known = {norm(s["address"], s.get("muni", "")) for s in sites["sites"]} | set(seen)
    status = {"generated_utc": datetime.now(timezone.utc).isoformat(), "areas": {}, "engine": None, "errors": []}
    added = 0
    for area, juris, boro in AREAS:
        text, engine = None, None
        for name, k, fn in (("gemini", gkey, ask_gemini), ("claude", ckey, ask_claude)):
            if not k:
                continue
            try:
                text, engine = fn(k, PROMPT.format(area=area))
                break
            except Exception as e:
                status["errors"].append(f"{area}: {e}")
        if text is None:
            status["areas"][area] = "no engine available"
            continue
        status["engine"] = engine
        hits = [h for h in parse(text) if h.get("address") and str(h.get("url", "")).startswith("http")]
        new = 0
        for h in hits:
            key = norm(h["address"], h.get("town", ""))
            if key in known:
                continue
            known.add(key)
            seen[key] = {"first_seen": datetime.now(timezone.utc).date().isoformat(), "url": h["url"]}
            site = {"id": "hunt-" + re.sub(r"[^a-z0-9]+", "-", key.lower())[:60], "address": h["address"], "muni": h.get("town"),
                    "state": h.get("state"), "jurisdiction": juris if juris in ("NYC", "Yonkers") else juris,
                    "ask": h.get("price"), "status": f"Listed ({h.get('source') or 'web'}{', updated ' + h['updated'] if h.get('updated') else ''}); found by search, verify",
                    "listing_url": h["url"], "headline": h.get("headline"), "listing_lot_sf": h.get("lot_sf"),
                    "listing_zoning": h.get("zoning"), "listing_buildable_sf": h.get("buildable_sf"),
                    "found_by": engine, "issues": ["Listing details come from the listing page; confirm lot, zoning and status with the broker"]}
            if boro:
                site["borough"] = boro
            if h.get("approved_units"):
                site["approval"] = {"status": "claimed by listing", "body": "unverified", "date": None, "units": h["approved_units"],
                                    "detail": "The listing claims approvals; get the resolution before relying on it.", "sources": [h["url"]]}
            sites["sites"].append(site)
            new += 1
        added += new
        status["areas"][area] = f"{len(hits)} found, {new} new"
        print(area, status["areas"][area])
        time.sleep(3)
    json.dump(sites, open(SITES, "w"), indent=1)
    json.dump(seen, open(SEEN, "w"), indent=1)
    status["added"] = added
    json.dump(status, open(os.path.join(DATA, "hunter_status.json"), "w"), indent=1)
    print("hunter added", added)


if __name__ == "__main__":
    main()
