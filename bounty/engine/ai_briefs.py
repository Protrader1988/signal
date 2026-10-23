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

MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash"]   # same list the signal digest uses; old lite/2.0 names 404
API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

PROMPT = """You are an acquisitions director at a sharp NYC development shop, briefing the principal.
Below is REAL output from our parcel screener: underutilized NYC lots underwritten across financing
paths (485-x tax exemption, City of Yes UAP, Section 8 overlay, ELLA/LIHTC stack). Land costs are
ESTIMATES from real sale comps; walk_away_land is the max land price where the deal still pencils.

Each deal carries an ACTION: "offer" (walk-away land >= est. land: send the letter, bid at or under comps,
never above max_offer), "negotiate" (walk-away 70-100% of comps: only with a motivated seller),
"program" (only the HPD subsidy stack works), "rfp" (city-owned: RFP / Fast Track pipeline, never a private sale).
walk_away_as_builder assumes the principal is the GC; walk_away_third_party_gc is the same site at a hired-GC cost.
faith_owned sites belong to a church or congregation: a nonprofit landowner can be the 51% qualified partner HPD's
Neighborhood Builders Fast Track requires, so the play there is a ground-lease JV, not a cash offer.

Using ONLY the data provided (never invent addresses, numbers, or programs):
- Write a tight morning brief: which 3-5 sites to send letters to THIS WEEK and why (2-3 sentences).
- For each target give: the play in one sentence, the offer frame (max_offer vs est. land, headroom),
  and the one thing to verify before the letter goes out (zoning, occupancy, institutional-owner approval,
  sewer, site condition). For rfp sites say who to call (Council member, community board) not "make an offer".
- Plain language, no em dashes. These are screening estimates pending diligence.

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
                raw=urllib.request.urlopen(req,timeout=150).read()
                return json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"], m
            except urllib.error.HTTPError as e:
                print(f"{m} attempt {attempt}: HTTP {e.code}"); last=e
                if e.code==429 and attempt==1: time.sleep(35); continue
                break
            except Exception as e:
                print(f"{m} attempt {attempt}: {e}"); last=e
                if attempt==1: time.sleep(10); continue
                break
    raise last or RuntimeError("no model")

def main():
    os.makedirs("bounty/data", exist_ok=True)
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key:
        json.dump({"available":False,"note":"Add GEMINI_API_KEY secret to enable AI deal briefs."},
                  open("bounty/data/briefs.json","w"),indent=2); return
    d=json.load(open("bounty/data/nyc_deals.json"))
    mk=[x for x in d["deals"] if x.get("action") in ("offer","negotiate")][:10]
    fa=[x for x in d["deals"] if x.get("faith_owned") and x.get("action") in ("offer","negotiate")][:3]
    pr=[x for x in d["deals"] if x.get("action")=="program"][:2]
    rf=[x for x in d["deals"] if x.get("action")=="rfp"][:2]
    mk=mk+[x for x in fa if x not in mk]
    slim=[]
    for x in mk+pr+rf:
        b=x.get("best") or {}
        slim.append({"address":x["address"],"borough":x["borough"],"zone":x["zone"],
                     "units":x["est_units"],"action":x.get("action"),"action_why":x.get("action_why"),
                     "owner_class":x.get("owner_class"),"faith_owned":x.get("faith_owned"),"owner_lots_on_board":x.get("owner_lot_count"),
                     "occupied":x.get("occupied"),"city_owned":x["city_owned"],
                     "est_land_cost":x["est_land_cost"],"max_offer":x.get("max_offer"),"headroom":x.get("headroom"),
                     "walk_away_as_builder":x["walk_away_land"],"walk_away_third_party_gc":x.get("walk_away_land_institutional"),
                     "best_path":x["best_path_label"],"equity":x["equity_needed"],
                     "yoc_pct":b.get("yoc_pct"),"coc_pct":b.get("coc_pct"),"dev_fee":b.get("dev_fee"),
                     "motivated":x.get("motivated"),"has_mailing":bool(x.get("owner_mailing")),"score":x["score"]})
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
