"""
BOUNTY — on-market cross-check, v1 Oct 2026.

Listing sites (StreetEasy, LoopNet, Crexi, Zillow) have no free API and block scrapers.
Gemini's free tier includes Google Search grounding, so for the top actionable lots the
engine asks, per address: is it currently listed for sale, by whom, at what price, with the
source URL. Results are AI-plus-search derived and labeled as such on the page; a hit means
"go read the listing", a miss means "nothing found", never "not for sale".

Output: bounty/data/listings.json  {bbl_or_key: {listed, asking_price, broker, url, note, checked}}
Runs after the scans. Same GEMINI_API_KEY as the digest. Budget: ~12 grounded calls/night.
"""
import json, os, re, time, urllib.request, urllib.error, traceback
from datetime import date

MODELS = ["gemini-3.6-flash", "gemini-flash-latest"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
OUT = "bounty/data/listings.json"

PROMPT = """You are checking whether development sites are currently LISTED FOR SALE. Use Google Search for each address.
For each item return: listed (true/false/unknown), asking_price (number, USD, or null), broker (string or null),
url (the listing page if found, else null), note (<=120 chars: listing headline, days on market, or 'no listing found').
Only report a listing you actually found in search results for that exact address or lot; if you find a nearby but
different address, set listed=false and mention it in note. Do not guess prices.
Return ONLY a JSON array in the same order as the input, like:
[{"key":"...","listed":true,"asking_price":1250000,"broker":"...","url":"https://...","note":"..."}]
INPUT:
"""

def gemini(key, prompt):
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "tools": [{"google_search": {}}],
                       "generationConfig": {"temperature": 0.1}}).encode()
    last = None
    for m in MODELS:
        for attempt in (1, 2, 3):
            try:
                req = urllib.request.Request(API.format(m=m) + f"?key={key}", data=body, headers={"Content-Type": "application/json"})
                raw = urllib.request.urlopen(req, timeout=150).read()
                j = json.loads(raw)
                text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
                return text, m
            except urllib.error.HTTPError as e:
                try: body = e.read().decode()[:300]
                except Exception: body = ""
                print(f"{m} attempt {attempt}: HTTP {e.code} {body}"); last = e
                if e.code == 429 and attempt < 3: time.sleep(70 * attempt); continue
                break
            except Exception as e:
                print(f"{m} attempt {attempt}: {e}"); last = e
                if attempt == 1: time.sleep(10); continue
                break
    raise last or RuntimeError("no model")

def parse_json_array(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m: return []
    try: return json.loads(m.group(0))
    except Exception:
        try: return json.loads(m.group(0).replace("'", '"'))
        except Exception: return []

def targets():
    """Top actionable lots across the boards, with a stable key and a search-friendly address."""
    t = []
    def load(f):
        try: return json.load(open(f"bounty/data/{f}_deals.json")).get("deals", [])
        except Exception: return []
    boro = {"BX": "Bronx, NY", "BK": "Brooklyn, NY", "QN": "Queens, NY"}
    for d in [x for x in load("nyc") if x.get("action") in ("offer", "negotiate")][:70]:
        t.append({"key": d["bbl"], "address": f"{d['address']}, {boro.get(d['borough'], 'New York, NY')} {d.get('zip') or ''}".strip(), "board": "nyc"})
    for f, tk, st in (("nys", "muni", "NY"), ("ct", "town", "CT"), ("nj", "town", "NJ")):
        for d in [x for x in load(f) if x.get("action") in ("offer", "negotiate")][:15]:
            key = d.get("pams_pin") or f"{f}|{d.get(tk)}|{d.get('address')}"
            t.append({"key": key, "address": f"{d.get('address')}, {d.get(tk)}, {st}", "board": f})
    return t

def main():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    prev = {}
    if os.path.exists(OUT):
        try: prev = json.load(open(OUT))
        except Exception: prev = {}
    if not key:
        json.dump(prev or {"_note": "Add GEMINI_API_KEY to enable the on-market cross-check."}, open(OUT, "w"), indent=1); return
    today = date.today().isoformat()
    tg = targets()
    # re-check each key at most every 3 days to stay inside the free grounding budget
    due = [x for x in tg if (prev.get(x["key"]) or {}).get("checked", "0000") < (date.fromordinal(date.today().toordinal() - 3)).isoformat()]
    print(f"listings: {len(tg)} targets, {len(due)} due for a check")
    out = dict(prev); out.pop("_note", None)
    n_listed = 0
    for i in range(0, len(due), 8):
        batch = due[i:i+8]
        payload = PROMPT + json.dumps([{"key": b["key"], "address": b["address"]} for b in batch], indent=0)
        try:
            text, model = gemini(key, payload)
        except Exception as e:
            print(f"  batch {i//8} failed: {e}")
            out["_meta"] = {"generated": today, "targets": len(tg), "checked_today": 0, "listed_now": 0,
                            "error": f"{type(e).__name__}: {str(e)[:160]}",
                            "note": "On-market check did not run: Google Search grounding returned an error. On the free Gemini tier grounding has no quota; enable pay-as-you-go on the key (about $35 per 1,000 grounded prompts, ~12/day here) or leave the board off-market only."}
            json.dump(out, open(OUT, "w"), indent=1); return
        arr = parse_json_array(text)
        got = {str(a.get("key")): a for a in arr if isinstance(a, dict)}
        for b in batch:
            a = got.get(str(b["key"])) or {}
            listed = a.get("listed")
            rec = {"listed": listed if listed in (True, False) else "unknown",
                   "asking_price": a.get("asking_price") if isinstance(a.get("asking_price"), (int, float)) else None,
                   "broker": a.get("broker"), "url": a.get("url") if str(a.get("url") or "").startswith("http") else None,
                   "note": str(a.get("note") or "")[:160], "checked": today, "model": model, "board": b["board"], "address": b["address"]}
            if rec["listed"] is True: n_listed += 1
            out[b["key"]] = rec
        time.sleep(20)
    out["_meta"] = {"generated": today, "targets": len(tg), "checked_today": len(due), "listed_now": sum(1 for k, v in out.items() if k != "_meta" and v.get("listed") is True),
                    "note": "AI + Google Search derived. A hit means a listing was found in search results for that address; a miss means nothing was found, not that the lot is unavailable."}
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"listings: {out['_meta']}")

if __name__ == "__main__":
    try: main()
    except Exception:
        open("bounty/data/LISTINGS_ERROR.txt", "w").write(traceback.format_exc()); print("err"); raise SystemExit(1)
