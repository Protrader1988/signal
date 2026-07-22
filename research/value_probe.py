"""
Probe: is free fundamental data usable for a below-book-value + quality screen?
Pulls yfinance fundamentals for a value-leaning universe and reports coverage
and what a quality-filtered deep-value screen would surface RIGHT NOW.

Honesty: this is a live screen, NOT a backtested edge — free data has no clean
point-in-time fundamentals, so a value backtest would be look-ahead biased.
"""
import json, traceback, time
from datetime import datetime, timezone
import yfinance as yf

# Value-leaning universe: sectors that actually trade near/below book
# (banks, insurers, autos, energy, industrials, materials) + broad names.
UNIV = [
 "JPM","BAC","WFC","C","GS","MS","USB","PNC","TFC","KEY","CFG","RF","HBAN","FITB","ALLY","COF","SCHW","BK","STT",
 "MET","PRU","AIG","ALL","TRV","HIG","LNC","PFG","CINF",
 "F","GM","STLA","VWAGY","HMC","TM",
 "XOM","CVX","COP","OXY","VLO","PSX","MPC","HAL","SLB","DVN","APA","CVE","OVV",
 "CAT","DE","CMI","PCAR","EMR","ETN","DOW","LYB","NUE","STLD","CLF","X","AA","FCX","MOS","CF",
 "T","VZ","PARA","WBD","CMCSA","INTC","PYPL","WBA","CVS","F","KHC","BG","ADM","GM",
]
NOTABLE = ["SPCX","TSLA","NVDA","PLTR","RKLB","ASTS"]  # high-profile growth / recent IPOs to check
UNIV = sorted(set(UNIV+NOTABLE))

def num(x):
    try:
        f=float(x); return f if f==f else None
    except: return None

def main():
    rows=[]; got=0; miss=0
    for tk in UNIV:
        try:
            info=yf.Ticker(tk).info
            pb=num(info.get("priceToBook")); eps=num(info.get("trailingEps"))
            roe=num(info.get("returnOnEquity")); de=num(info.get("debtToEquity"))
            mc=num(info.get("marketCap")); pe=num(info.get("trailingPE"))
            price=num(info.get("currentPrice") or info.get("regularMarketPrice"))
            sector=info.get("sector")
            if pb is None and eps is None and mc is None:
                miss+=1; continue
            got+=1
            rows.append({"ticker":tk,"pb":pb,"eps":eps,"roe":roe,"de":de,"mc":mc,"pe":pe,"price":price,"sector":sector})
        except Exception as e:
            miss+=1
        time.sleep(0.15)
    # quality deep-value screen: below/near book, profitable, sane leverage, real size
    cands=[r for r in rows if r["pb"] and 0<r["pb"]<=1.3 and r["eps"] and r["eps"]>0
           and (r["mc"] or 0)>2e9 and (r["de"] is None or r["de"]<200)]
    cands.sort(key=lambda r:r["pb"])
    out={"generated":datetime.now(timezone.utc).isoformat(),
         "universe":len(UNIV),"data_ok":got,"data_missing":miss,
         "candidates_count":len(cands),
         "candidates":[{"ticker":c["ticker"],"pb":round(c["pb"],2),"pe":round(c["pe"],1) if c["pe"] else None,
                        "roe_pct":round(c["roe"]*100,1) if c["roe"] is not None else None,
                        "de":round(c["de"],0) if c["de"] is not None else None,
                        "price":c["price"],"sector":c["sector"]} for c in cands[:25]]}
    json.dump(out,open("research/output/value_probe.json","w"),indent=2)
    print(f"coverage: {got}/{len(UNIV)} ok, {miss} missing | candidates: {len(cands)}")
    for c in cands[:15]:
        print(f"  {c['ticker']:5} P/B {c['pb']:.2f}  ROE {c['roe'] and round(c['roe']*100,1)}%  D/E {c['de']}  {c['sector']}")
    notable={r["ticker"]:r for r in rows}
    print("NOTABLE (fundamentals check):")
    for tk in ["SPCX","TSLA","NVDA","PLTR","RKLB","ASTS"]:
        r=notable.get(tk)
        if r: print(f"  {tk:5} price {r['price']}  P/B {r['pb']}  P/E {r['pe']}  EPS {r['eps']}  sector {r['sector']}")
        else: print(f"  {tk:5} no data")
    out["notable"]=[{"ticker":tk,**{k:notable[tk][k] for k in ("price","pb","pe","eps","sector")}} for tk in ["SPCX","TSLA","NVDA","PLTR","RKLB","ASTS"] if tk in notable]
    json.dump(out,open("research/output/value_probe.json","w"),indent=2)

if __name__=="__main__":
    try: main()
    except Exception as e:
        open("research/output/VALUE_PROBE_ERROR.txt","w").write(traceback.format_exc()); print("FATAL",e)
