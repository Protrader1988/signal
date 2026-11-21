"""BOUNTY listing hunter: finds NEW development listings every day.

LoopNet, Crexi, Zillow and the MLS portals have no free API and block scrapers, so an AI
model with live web search does the reading: one search prompt per target area per day,
asking only for listings it can cite with a URL. Every hit is appended to
bounty/data/feasibility_sites.json, so the next memo run gives it the full
land-use / test-fit review automatically, and it shows on the board as "found by search".

Free inputs (always read first):
  Google Sheet "BOUNTY Listings", published as CSV (URLs in bounty/data/hunter_config.json):
    tab "muse"   - clean rows added by the owner's Muse agent from its daily listing search
    tab "alerts" - raw rows from saved-search alert emails (Gmail -> Apps Script, bounty/tools/gmail_to_sheet.gs)
Paid search engines (only if HUNTER_SEARCH=1, first that works is used):
  Gemini with Google Search grounding   (GEMINI_API_KEY; grounding is billed per prompt on paid keys)
  Claude with the web search tool        (ANTHROPIC_API_KEY, optional)

Budget: one prompt per area per day (10 areas) and up to 15 results each.
"""
import csv, io, json, os, re, time, urllib.error, urllib.request
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


ADDR = re.compile(r"\b(\d{1,5}(?:-\d{1,5})?\s+[A-Z0-9][A-Za-z0-9.' ]{2,40}?\s(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Pl|Place|Ln|Lane|Dr|Drive|Ter|Terrace|Ct|Court|Pkwy|Parkway|Way|Hwy|Highway|Tpke|Turnpike))\b\.?,?\s*([A-Z][A-Za-z .]{2,30})?,?\s*(NY|NJ|CT)?", re.I)
PRICE = re.compile(r"\$\s?([0-9][0-9,]{4,})(?:\.\d+)?\s*(K|M)?", re.I)
JURIS = [(r"bronx", "NYC", "BX"), (r"astoria|long island city|\blic\b|queens", "NYC", "QN"),
         (r"brooklyn|williamsburg|greenpoint|dumbo", "NYC", "BK"), (r"yonkers", "Yonkers", None)]


def fetch_csv(url):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "BountyEngine/1.0"}), timeout=60).read().decode("utf-8", "ignore")
        return list(csv.DictReader(io.StringIO(raw)))
    except Exception as e:
        print("  sheet fetch failed:", e)
        return []


def juris_for(town, state):
    t = (town or "").lower()
    for pat, j, b in JURIS:
        if re.search(pat, t):
            return j, b
    return {"CT": "CT", "NJ": "NJ"}.get((state or "").upper(), "Westchester" if (state or "").upper() == "NY" else "Other"), None


def num(v):
    try:
        v = str(v).replace(",", "").replace("$", "").strip()
        return float(v) if v else None
    except Exception:
        return None


def sheet_hits():
    cfg = json.load(open(os.path.join(DATA, "hunter_config.json"))) if os.path.exists(os.path.join(DATA, "hunter_config.json")) else {}
    hits = []
    inbox = os.path.join(ROOT, "inbox", "listings.csv")
    rows_local = list(csv.DictReader(open(inbox, encoding="utf-8"))) if os.path.exists(inbox) else []
    for r in rows_local + (fetch_csv(cfg["muse_csv"]) if cfg.get("muse_csv") else []):
        if True:
            if r.get("address") and str(r.get("url", "")).startswith("http"):
                hits.append({"address": r["address"].strip(), "town": (r.get("town") or "").strip(), "state": (r.get("state") or "").strip().upper(),
                             "price": num(r.get("price")), "lot_sf": num(r.get("lot_sf")), "zoning": r.get("zoning") or None,
                             "approved_units": num(r.get("approved_units")), "url": r["url"].strip(),
                             "source": (r.get("source") or "Muse").strip(), "updated": r.get("date"), "headline": r.get("notes"), "via": "Muse"})
    if cfg.get("alerts_csv"):
        for r in fetch_csv(cfg["alerts_csv"]):
            text, url = r.get("text") or "", r.get("url") or ""
            m = ADDR.search(text)
            if not m or not url.startswith("http"):
                continue
            p = PRICE.search(text)
            price = None
            if p:
                price = float(p.group(1).replace(",", "")) * ({"K": 1e3, "M": 1e6}.get((p.group(2) or "").upper(), 1))
            src = re.search(r"(loopnet|crexi|zillow|streeteasy|realtor|redfin|onekey|showcase|commercialcafe|cityfeet|propertyshark|trulia|homes)", url, re.I)
            hits.append({"address": m.group(1).strip(), "town": (m.group(2) or "").strip(), "state": (m.group(3) or "").upper(),
                         "price": price, "url": url, "source": src.group(1).title() if src else "alert email",
                         "updated": r.get("received"), "headline": (r.get("subject") or "")[:90], "via": "saved-search alert"})
    return hits, bool(cfg.get("muse_csv") or cfg.get("alerts_csv") or rows_local)


def main():
    gkey, ckey = os.environ.get("GEMINI_API_KEY", ""), os.environ.get("ANTHROPIC_API_KEY", "")
    sites = json.load(open(SITES))
    seen = json.load(open(SEEN)) if os.path.exists(SEEN) else {}
    known = {norm(s["address"], s.get("muni", "")) for s in sites["sites"]} | set(seen)
    status = {"generated_utc": datetime.now(timezone.utc).isoformat(), "areas": {}, "engine": None, "errors": []}
    added = 0

    def add(h, engine, juris=None, boro=None):
        key = norm(h["address"], h.get("town", ""))
        if key in known:
            return 0
        known.add(key)
        if juris is None:
            juris, boro = juris_for(h.get("town"), h.get("state"))
        seen[key] = {"first_seen": datetime.now(timezone.utc).date().isoformat(), "url": h["url"]}
        site = {"id": "hunt-" + re.sub(r"[^a-z0-9]+", "-", key.lower())[:60], "address": h["address"], "muni": h.get("town"),
                "state": h.get("state"), "jurisdiction": juris, "ask": h.get("price"),
                "status": f"Listed ({h.get('source') or 'web'}{', ' + str(h['updated'])[:10] if h.get('updated') else ''}); via {h.get('via') or engine}, verify",
                "listing_url": h["url"], "headline": h.get("headline"), "listing_lot_sf": h.get("lot_sf"),
                "listing_zoning": h.get("zoning"), "listing_buildable_sf": h.get("buildable_sf"), "found_by": h.get("via") or engine,
                "issues": ["Listing details come from the listing page; confirm lot, zoning and status with the broker"]}
        if boro:
            site["borough"] = boro
        if h.get("approved_units"):
            site["approval"] = {"status": "claimed by listing", "body": "unverified", "date": None, "units": int(h["approved_units"]),
                                "detail": "The listing claims approvals; get the resolution before relying on it.", "sources": [h["url"]]}
        sites["sites"].append(site)
        return 1

    sh, has_cfg = sheet_hits()
    n_sheet = sum(add(h, "sheet") for h in sh)
    status["sheet"] = {"configured": has_cfg, "rows": len(sh), "new": n_sheet}
    added += n_sheet
    status["engine"] = "BOUNTY inbox (Muse + alert emails)" if has_cfg else None
    print("sheet:", status["sheet"])
    search_on = os.environ.get("HUNTER_SEARCH", "0") == "1"
    for area, juris, boro in (AREAS if search_on else []):
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
        new = sum(add(h, engine, juris, boro) for h in hits)
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
