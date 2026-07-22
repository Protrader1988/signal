"""
BOUNTY — AI deal briefs (Gemini free tier, same key as Signal).

Turns the top-ranked REAL deals into a plain-English morning brief: what to
chase first, why each site works, what the risks are, and the negotiating
frame (est. land cost vs walk-away). Interprets ONLY the engine's real numbers
— never invents facts, prices, or addresses. Honest about assumptions.

Output: bounty/data/briefs.json
"""
import json, os, time, urllib.request, urllib.error, traceback
from datetime import datetime, timezone

MODELS = ["gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

PROMPT = """You are an acquisitions director at a sharp NYC development shop, briefing the principal.
Below is REAL output from our parcel screener: underutilized NYC lots underwritten across financing
paths (485-x tax exemption, City of Yes UAP, Section 8 overlay, ELLA/LIHTC stack). Land costs are
ESTIMATES from real sale comps; walk_away_land is the max land price where the deal still pencils.

Using ONLY the data provided (never invent addresses, numbers, or programs):
- Write a tight morning brief: where the opportunity is concentrated and why (2-3 sentences).
- Pick the 3-5 most compelling targets and for each give: the play in one sentence, the negotiating
  frame (est land vs walk-away headroom), and the main risk to check (zoning verify, site condition,
  HPD disposition process for city-owned, subsidy competitiveness for program plays).
- Be honest: these are screening estimates pending diligence; city-owned sites go through HPD/EDC
  disposition (RFPs), not private purchase.

Return ONLY JSON: {"brief":"...", "targets":[{"address":"...","play":"...","negotiation":"...","risk":"..."}],
"process_note":"one sentence on next steps"}
DATA:
"""

def gemini(key, prompt):
    body = json.dumps({"contents":[{"parts":[{"text":prompt}]}],
                       "generationConfig":{"temperature":0.35,"responseMimeType":"application/json"}}).encode()
    last=None
    for m in MODELS:
        for attempt in (1,2):
            try:
                req=urllib.request.Request(API.format(m=m)+f"?key={key}",data=body,
                                           headers={"Content-Type":"application/json"})
                raw=urllib.request.urlopen(req,timeout=60).read()
                return json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"], m
            except urllib.error.HTTPError as e:
                print(f"{m} attempt {attempt}: HTTP {e.code}"); last=e
                if e.code==429 and attempt==1: time.sleep(35); continue
                break
            except Exception as e:
                print(f"{m}: {e}"); last=e; break
    raise last or RuntimeError("no model")

def main():
    os.makedirs("bounty/data", exist_ok=True)
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key:
        json.dump({"available":False,"note":"Add GEMINI_API_KEY secret to enable AI deal briefs."},
                  open("bounty/data/briefs.json","w"),indent=2); return
    d=json.load(open("bounty/data/nyc_deals.json"))
    mk=[x for x in d["deals"] if x.get("tier")=="market"][:12]
    pr=[x for x in d["deals"] if x.get("tier")=="program"][:4]
    slim=[]
    for x in mk+pr:
        b=x.get("best") or {}
        slim.append({"address":x["address"],"borough":x["borough"],"zone":x["zone"],
                     "units":x["est_units"],"tier":x["tier"],"city_owned":x["city_owned"],
                     "est_land_cost":x["est_land_cost"],"walk_away_land":x["walk_away_land"],
                     "best_path":x["best_path_label"],"equity":x["equity_needed"],
                     "coc_pct":b.get("coc_pct"),"dev_fee":b.get("dev_fee"),"score":x["score"]})
    payload=PROMPT+json.dumps({"land_comps_psf":d.get("land_comps_psf"),
                               "counts":d.get("counts"),"deals":slim},indent=1)
    try:
        txt,model=gemini(key,payload)
        parsed=json.loads(txt)
        json.dump({"available":True,"generated_utc":datetime.now(timezone.utc).isoformat(),
                   "model":model,"brief":parsed.get("brief"),"targets":parsed.get("targets",[]),
                   "process_note":parsed.get("process_note"),
                   "disclaimer":"AI interpretation of the engine's real screening output. Estimates pending diligence — not investment advice."},
                  open("bounty/data/briefs.json","w"),indent=2)
        print("briefs written:",model)
    except Exception as e:
        json.dump({"available":False,"note":f"AI briefs temporarily unavailable ({type(e).__name__})."},
                  open("bounty/data/briefs.json","w"),indent=2)
        open("bounty/data/BRIEFS_ERROR.txt","w").write(traceback.format_exc()); print("err",e)

if __name__=="__main__":
    try: main()
    except Exception:
        open("bounty/data/BRIEFS_ERROR.txt","w").write(traceback.format_exc()); raise SystemExit(1)
