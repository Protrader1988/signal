"""
AI market digest — honest directional read of the REAL headlines (daily).

Uses Google Gemini's free API tier to interpret the real news in news.json into
a Wall-Street-style read: an overall market lean with an explicit confidence
level, the bull case, the bear case, and per-theme takes. It leans into a view
where the headlines support one, but NEVER fakes certainty and NEVER invents
facts beyond the provided headlines.

Requires a free GEMINI_API_KEY (Google AI Studio) set as a GitHub secret.
If the key is absent, writes a placeholder so the UI shows a friendly setup note
instead of erroring. This is interpretation, not a guarantee or a forecast.

Output: site/data/digest.json
"""
import json, os, time, urllib.request, urllib.error, traceback
from datetime import datetime, timezone

# Try current free-tier model names in order; retry once on 429.
MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
API_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

PROMPT = """You are a seasoned, honest markets analyst briefing a personal trader.
Below are REAL news headlines pulled today from public feeds.

Using ONLY these headlines (do NOT invent facts, numbers, prices, or events not present):
- Give an overall market read with a directional lean and an HONEST confidence level.
- Lean into a directional view where the headlines genuinely support one; if they're mixed or thin, say neutral and say so.
- ALWAYS include both a bull case and a bear case.
- NEVER guarantee outcomes. Confidence reflects real uncertainty. This is interpretation, not a forecast.
- Keep it sharp and plain-English, like a desk analyst.

Return ONLY JSON in exactly this shape:
{"market":{"lean":"bullish|leaning bullish|neutral|leaning bearish|bearish","confidence":"low|medium|high","summary":"2-3 sentences on the overall picture","bull":"the case for upside","bear":"the case for downside","watch":"what to watch next"},
"takes":[{"topic":"a theme or ticker","lean":"bullish|leaning bullish|neutral|leaning bearish|bearish","confidence":"low|medium|high","note":"1-2 sentence honest read"}]}
Give 2 to 4 takes on the most important themes/tickers in the headlines.

HEADLINES:
"""

def gemini(key, prompt):
    body = json.dumps({"contents":[{"parts":[{"text":prompt}]}],
                       "generationConfig":{"temperature":0.4,"responseMimeType":"application/json"}}).encode()
    last = None
    for m in MODELS:
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(API_TMPL.format(m=m) + f"?key={key}",
                                             data=body, headers={"Content-Type":"application/json"})
                raw = urllib.request.urlopen(req, timeout=60).read()
                resp = json.loads(raw)
                txt = resp["candidates"][0]["content"]["parts"][0]["text"]
                print(f"model ok: {m}")
                return txt, m
            except urllib.error.HTTPError as e:
                detail = ""
                try: detail = e.read().decode()[:400]
                except Exception: pass
                print(f"model {m} attempt {attempt}: HTTP {e.code} {detail[:160]}")
                last = RuntimeError(f"{m}: HTTP {e.code} {detail[:200]}")
                if e.code == 429 and attempt == 1:
                    time.sleep(35); continue   # brief backoff then retry once
                break                          # other errors: next model
            except Exception as e:
                print(f"model {m} attempt {attempt}: {type(e).__name__} {e}")
                last = e; break
    raise last or RuntimeError("no model succeeded")

def main():
    os.makedirs("site/data", exist_ok=True)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        json.dump({"available": False,
                   "note": "AI digest is off. Add a free GEMINI_API_KEY (Google AI Studio) as a repository secret to turn on an honest daily market read of the real headlines."},
                  open("site/data/digest.json","w"), indent=2)
        print("no GEMINI_API_KEY — wrote placeholder"); return
    try:
        news = json.load(open("site/data/news.json"))
        heads = "\n".join(f"- [{it.get('tag','')}] {it['title']} ({it.get('source','')})" for it in news.get("items", [])[:26])
    except Exception:
        heads = ""
    if not heads:
        json.dump({"available": False, "note": "No headlines available to interpret yet."}, open("site/data/digest.json","w"), indent=2)
        print("no headlines"); return
    try:
        txt, used_model = gemini(key, PROMPT + heads)
        parsed = json.loads(txt)
        out = {"available": True, "generated_utc": datetime.now(timezone.utc).isoformat(), "model": used_model,
               "market": parsed.get("market", {}), "takes": parsed.get("takes", []),
               "disclaimer": "AI interpretation of real public headlines. Not a forecast, not investment advice — markets can move against any read. Confidence levels reflect genuine uncertainty."}
        json.dump(out, open("site/data/digest.json","w"), indent=2)
        print("digest written:", out["market"].get("lean"), out["market"].get("confidence"))
    except Exception as e:
        json.dump({"available": False, "note": f"AI digest temporarily unavailable ({type(e).__name__}). The real headlines below are unaffected."},
                  open("site/data/digest.json","w"), indent=2)
        open("site/data/DIGEST_ERROR.txt","w").write(traceback.format_exc()); print("digest err", e)

if __name__ == "__main__":
    try: main()
    except Exception as e:
        open("site/data/DIGEST_ERROR.txt","w").write(traceback.format_exc()); print("FATAL", e)
# trigger: c48a590
