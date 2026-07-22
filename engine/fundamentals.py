"""
Value / Opportunistic screen + Watchlist (runs daily on Actions).

VALUE SCREEN: below/near book value AND profitable AND sane leverage AND real
size. This is an HONEST LIVE SCREEN, not a backtested signal — free data has no
clean point-in-time fundamentals, so a value backtest would be look-ahead
biased. Presented as a due-diligence starting list, never as a validated edge.

WATCHLIST: high-profile / recently-IPO'd names (e.g. SPCX) tracked with a
status so they flow into momentum signals once they have enough price history.

Outputs: site/data/value.json, site/data/watchlist.json
"""
import json, time, traceback
from datetime import datetime, timezone
import yfinance as yf

VALUE_UNIV = sorted(set([
 "JPM","BAC","WFC","C","GS","MS","USB","PNC","TFC","KEY","CFG","RF","HBAN","FITB","ALLY","COF","SCHW","BK","STT",
 "MET","PRU","AIG","ALL","TRV","HIG","LNC","PFG","CINF",
 "F","GM","STLA","HMC","TM",
 "XOM","CVX","COP","OXY","VLO","PSX","MPC","HAL","SLB","DVN","APA","CVE","OVV",
 "CAT","DE","CMI","PCAR","EMR","ETN","DOW","LYB","NUE","STLD","CLF","X","AA","FCX","MOS","CF",
 "T","VZ","CMCSA","INTC","PYPL","CVS","KHC","BG","ADM",
]))
WATCHLIST = ["SPCX","RKLB","ASTS","PLTR","TSLA","NVDA","CRWV"]
MOM_HISTORY_NEEDED = 252

def num(x):
    try: f=float(x); return f if f==f else None
    except: return None

def run_value():
    rows=[]
    for tk in VALUE_UNIV:
        try:
            info=yf.Ticker(tk).info
            r={"ticker":tk,"pb":num(info.get("priceToBook")),"eps":num(info.get("trailingEps")),
               "roe":num(info.get("returnOnEquity")),"de":num(info.get("debtToEquity")),
               "mc":num(info.get("marketCap")),"pe":num(info.get("trailingPE")),
               "price":num(info.get("currentPrice") or info.get("regularMarketPrice")),
               "sector":info.get("sector")}
            rows.append(r)
        except Exception: pass
        time.sleep(0.12)
    cands=[r for r in rows if r["pb"] and 0<r["pb"]<=1.3 and r["eps"] and r["eps"]>0
           and (r["mc"] or 0)>2e9 and (r["de"] is None or r["de"]<200)
           and (r["roe"] is None or r["roe"]>=0.04)]
    cands.sort(key=lambda r:(r["roe"] or 0.02)/r["pb"], reverse=True)  # quality-value
    out={"generated_utc":datetime.now(timezone.utc).isoformat(),
         "label":"LIVE SCREEN — not a backtested signal. Below/near book value, profitable, sane debt. A starting list for your own due diligence.",
         "count":len(cands),
         "candidates":[{"ticker":c["ticker"],"pb":round(c["pb"],2),
                        "pe":round(c["pe"],1) if c["pe"] else None,
                        "roe_pct":round(c["roe"]*100,1) if c["roe"] is not None else None,
                        "price":c["price"],"sector":c["sector"]} for c in cands[:15]]}
    json.dump(out,open("site/data/value.json","w"),indent=2)
    print(f"value screen: {len(cands)} candidates from {len(rows)} fetched")

def run_watchlist():
    names=[]
    for tk in WATCHLIST:
        try:
            t=yf.Ticker(tk)
            hist=t.history(period="max")
            days=len(hist)
            price=float(hist["Close"].iloc[-1]) if days else None
            chg=None
            if days>=2:
                chg=round((hist["Close"].iloc[-1]/hist["Close"].iloc[-2]-1)*100,2)
            info={}
            try: info=t.info
            except Exception: pass
            pb=num(info.get("priceToBook")); eps=num(info.get("trailingEps"))
            if days>=MOM_HISTORY_NEEDED:
                status="Eligible — has enough history for momentum signals"
            else:
                status=f"Building history — {days}/{MOM_HISTORY_NEEDED} trading days (~{max(0,(MOM_HISTORY_NEEDED-days)//21)} mo to signals)"
            names.append({"ticker":tk,"price":round(price,2) if price else None,"chg_pct":chg,
                          "pb":round(pb,1) if pb else None,"eps":eps,"days":days,"status":status})
        except Exception:
            names.append({"ticker":tk,"status":"data unavailable"})
        time.sleep(0.12)
    out={"generated_utc":datetime.now(timezone.utc).isoformat(),
         "label":"On the radar — high-profile and recently-listed names. Recent IPOs need ~1 year of history before the momentum model can signal them.",
         "names":names}
    json.dump(out,open("site/data/watchlist.json","w"),indent=2)
    print(f"watchlist: {len(names)} names")

def main():
    import os; os.makedirs("site/data",exist_ok=True)
    try: run_value()
    except Exception as e: open("site/data/VALUE_ERROR.txt","w").write(traceback.format_exc()); print("value err",e)
    try: run_watchlist()
    except Exception as e: open("site/data/WATCH_ERROR.txt","w").write(traceback.format_exc()); print("watch err",e)

if __name__=="__main__": main()
