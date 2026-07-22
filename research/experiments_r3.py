"""
Signal — research round 3: drawdown control (the final research round).

The validated equity momentum engine has a real edge but a -33% drawdown, and
crypto buy&hold has a brutal -85%. Drawdown is what makes people abandon a
working strategy at the worst moment, so before building we test two standard
institutional risk overlays — honestly, with the same no-look-ahead / IS-OOS
discipline:

  EXP A  Equity momentum + risk overlays:
         base (validated top-20% monthly momentum, trend-filtered)
         + MARKET REGIME filter (in cash when SPY < its 200d SMA)
         + VOLATILITY TARGETING (scale exposure to a target vol, cap 1.0)
         + both.
         Question: can we cut the -33% drawdown while keeping the Sharpe?

  EXP B  Crypto risk overlays on the basket:
         buy&hold vs vol-targeted vs (trend-timed + vol-targeted).
         Question: can we make crypto's -85% survivable?

No leverage anywhere (exposure capped at 1.0) — long-only, cash is the brake.
"""

import json, sys, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

COST_BPS=10.0; IS_FRAC=0.65; START="2017-01-01"
EQUITY_UNIVERSE=[
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","NFLX","ADBE","CRM","ORCL","INTC","QCOM","TXN","CSCO","IBM","MU","PYPL",
    "JPM","BAC","WFC","GS","MS","C","V","MA","AXP","BLK","SCHW","COF",
    "UNH","JNJ","LLY","MRK","PFE","ABBV","BMY","AMGN","GILD","CVS","MDT","TMO",
    "XOM","CVX","COP","SLB","OXY","EOG","PSX","VLO",
    "CAT","DE","HON","GE","BA","LMT","RTX","UPS","FDX","MMM","EMR",
    "WMT","COST","HD","LOW","TGT","MCD","SBUX","NKE","PG","KO","PEP","CL","MO","WBA",
    "DIS","CMCSA","T","VZ","PARA","WBD","F","GM","UBER","ABNB",
    "XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLC","XLRE",
    "SPY","QQQ","IWM","MDY","DIA","GLD","EFA","EEM",
]
CRYPTO_UNIVERSE=["BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD","ADA-USD","AVAX-USD","DOGE-USD","LINK-USD","DOT-USD","LTC-USD"]

def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)

def fetch(t):
    import yfinance as yf
    log(f"downloading {len(t)}...")
    raw=yf.download(t,start=START,progress=False,auto_adjust=True)
    px=raw["Close"] if isinstance(raw.columns,pd.MultiIndex) else raw[["Close"]]
    px=px.dropna(how="all"); px=px[px.columns[px.notna().mean()>0.55]].ffill(limit=5)
    log(f"  usable {len(px.columns)} rows {len(px)}"); return px

def stats(eq,ppy):
    eq=eq.dropna()
    if len(eq)<30: return {}
    r=eq.pct_change().dropna(); yrs=len(eq)/ppy
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1 if yrs>0 else np.nan
    vol=r.std()*np.sqrt(ppy); sh=(r.mean()*ppy)/vol if vol>0 else np.nan
    dd=(eq/eq.cummax()-1).min()
    return {"cagr_pct":round(cagr*100,1),"sharpe":round(sh,2),"vol_pct":round(vol*100,1),"maxDD_pct":round(dd*100,1)}

def iso(ret, ppy):
    """ret: daily return Series -> IS/OOS stats on the equity curve."""
    ret=ret.dropna()
    if len(ret)<60: return {"error":"insufficient"}
    eq=(1+ret).cumprod(); cut=int(len(eq)*IS_FRAC)
    return {"full":stats(eq/eq.iloc[0],ppy),
            "in_sample":stats(eq.iloc[:cut]/eq.iloc[0],ppy),
            "out_sample":stats(eq.iloc[cut:]/eq.iloc[cut],ppy),
            "split":str(eq.index[cut].date())}

def z(s):
    s=s.astype(float); sd=s.std()
    return s*0.0 if (not np.isfinite(sd) or sd==0) else (s-s.mean())/sd

def momentum_returns(px, rebal=21, top_frac=0.20):
    """Validated equity engine -> daily portfolio return series (pre-overlay)."""
    rets=px.pct_change(); sma200=px.rolling(200).mean(); dates=px.index
    idx=list(range(252,len(dates),rebal))
    W=pd.DataFrame(0.0,index=dates,columns=px.columns)
    for i in idx:
        t=dates[i]
        m12=px.shift(21).loc[t]/px.shift(252).loc[t]-1
        m6 =px.shift(21).loc[t]/px.shift(126).loc[t]-1
        score=((z(m12)+z(m6))/2)[px.loc[t]>sma200.loc[t]].dropna()
        if len(score)<3: continue
        n=max(1,int(round(len(score)*top_frac)))
        picks=score.sort_values(ascending=False).head(n).index
        w=pd.Series(0.0,index=px.columns); w[picks]=1.0/n
        j=min(i+rebal,len(dates)); W.iloc[i:j]=w.values
    pr=(W.shift(1)*rets).sum(axis=1)
    to=(W-W.shift(1)).abs().sum(axis=1)
    pr=(pr-to*(COST_BPS/1e4)).loc[dates[idx[0]]:]
    return pr

def vol_target_exposure(ret, target_annual, ppy, lookback=30):
    """Exposure = min(1, target/realized_vol), using vol up to yesterday."""
    rv=ret.rolling(lookback).std()*np.sqrt(ppy)
    exp=(target_annual/rv).clip(upper=1.0)
    return exp.shift(1).fillna(0.0)  # lag: only past info

def apply_cost_on_exposure(base_ret, exposure):
    """Apply an exposure series to a return series, charging cost on exposure changes."""
    er=base_ret*exposure
    turn=exposure.diff().abs().fillna(0.0)
    return er - turn*(COST_BPS/1e4)

def expA(px):
    ppy=252
    base=momentum_returns(px)
    out={"base": iso(base,ppy)}
    # regime: SPY above its 200d SMA
    if "SPY" in px.columns:
        spy=px["SPY"]; regime=(spy>spy.rolling(200).mean()).astype(float)
        regime=regime.reindex(base.index).shift(1).fillna(0.0)
        out["plus_regime"]=iso(apply_cost_on_exposure(base,regime),ppy)
    else:
        regime=None
    # vol target 15%
    vt=vol_target_exposure(base,0.15,ppy)
    out["plus_voltarget15"]=iso(apply_cost_on_exposure(base,vt.reindex(base.index).fillna(0)),ppy)
    # both
    if regime is not None:
        both=(regime*vt.reindex(base.index).fillna(0)).clip(upper=1.0)
        out["plus_both"]=iso(apply_cost_on_exposure(base,both),ppy)
    return out

def expB(px):
    ppy=365
    rets=px.pct_change(); basket=rets.mean(axis=1).loc[px.index[252]:]
    out={"buy_hold": iso(basket,ppy)}
    # vol target 40% (crypto is high-vol; still aggressive but bounded)
    vt=vol_target_exposure(basket,0.40,ppy,lookback=30)
    out["voltarget40"]=iso(apply_cost_on_exposure(basket,vt.reindex(basket.index).fillna(0)),ppy)
    # trend timing + vol target
    sma200=px.rolling(200).mean(); mom=px/px.shift(200)-1
    on=((px>sma200)&(mom>0)).astype(float)
    w=on.div(on.sum(axis=1).replace(0,np.nan),axis=0).fillna(0.0)
    timed=(w.shift(1)*rets).sum(axis=1) - (w-w.shift(1)).abs().sum(axis=1)*(COST_BPS/1e4)
    timed=timed.loc[px.index[252]:]
    vt2=vol_target_exposure(timed,0.40,ppy,lookback=30)
    out["trend_plus_voltarget40"]=iso(apply_cost_on_exposure(timed,vt2.reindex(timed.index).fillna(0)),ppy)
    return out

def main():
    res={"generated_utc":datetime.now(timezone.utc).isoformat(),
         "params":{"cost_bps":COST_BPS,"in_sample_frac":IS_FRAC}}
    try:
        res["expA_equity_risk_overlays"]=expA(fetch(EQUITY_UNIVERSE))
    except Exception as e:
        res["expA_error"]=f"{type(e).__name__}: {e}"; res["expA_tb"]=traceback.format_exc()
    try:
        res["expB_crypto_risk_overlays"]=expB(fetch(CRYPTO_UNIVERSE))
    except Exception as e:
        res["expB_error"]=f"{type(e).__name__}: {e}"; res["expB_tb"]=traceback.format_exc()
    json.dump(res,open("research/output/experiments_r3.json","w"),indent=2)

    L=["# Research round 3 — drawdown control","",f"_Generated {res['generated_utc']}_","",
       "## EXP A Equity momentum + risk overlays (goal: cut -33% maxDD, keep Sharpe)"]
    for k,v in res.get("expA_equity_risk_overlays",{}).items():
        f=v.get("full",{}); o=v.get("out_sample",{})
        L.append(f"- **{k}**: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
                 f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')} maxDD {o.get('maxDD_pct')}%")
    L+=["","## EXP B Crypto + risk overlays (goal: make -85% survivable)"]
    for k,v in res.get("expB_crypto_risk_overlays",{}).items():
        f=v.get("full",{}); o=v.get("out_sample",{})
        L.append(f"- **{k}**: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
                 f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')} maxDD {o.get('maxDD_pct')}%")
    open("research/output/EXPERIMENTS_R3.md","w").write("\n".join(L))
    log("done")

if __name__=="__main__":
    try: main()
    except Exception as e:
        open("research/output/EXPERIMENTS_R3_ERROR.txt","w").write(traceback.format_exc()); log(f"FATAL {e}"); sys.exit(1)
