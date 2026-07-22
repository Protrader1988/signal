"""
Emit the flagship strategy's equity curve vs benchmarks for the terminal chart.
Real historical prices; flagship = momentum top-20% monthly + 15% vol target.
Writes site/data/curve.json (downsampled, indexed to 100).
"""
import json, sys, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

START="2018-01-01"; COST_BPS=10.0; VT=0.15
UNIV=[
 "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","NFLX","ADBE","CRM","ORCL","INTC","QCOM","TXN","CSCO","IBM","MU","PYPL",
 "JPM","BAC","WFC","GS","MS","C","V","MA","AXP","BLK","SCHW","COF","UNH","JNJ","LLY","MRK","PFE","ABBV","BMY","AMGN","GILD","CVS","MDT","TMO",
 "XOM","CVX","COP","SLB","OXY","EOG","PSX","VLO","CAT","DE","HON","GE","BA","LMT","RTX","UPS","FDX","MMM","EMR",
 "WMT","COST","HD","LOW","TGT","MCD","SBUX","NKE","PG","KO","PEP","CL","MO","WBA","DIS","CMCSA","T","VZ","PARA","WBD","F","GM","UBER","ABNB",
 "XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLC","XLRE","SPY","QQQ","IWM","MDY","DIA","GLD","EFA","EEM"]

def z(s):
    s=s.astype(float); sd=s.std()
    return s*0.0 if (not np.isfinite(sd) or sd==0) else (s-s.mean())/sd

def main():
    import yfinance as yf
    raw=yf.download(UNIV,start=START,progress=False,auto_adjust=True)
    px=raw["Close"] if isinstance(raw.columns,pd.MultiIndex) else raw[["Close"]]
    px=px.dropna(how="all"); px=px[px.columns[px.notna().mean()>0.55]].ffill(limit=5)
    rets=px.pct_change(); sma200=px.rolling(200).mean(); dates=px.index
    idx=list(range(252,len(dates),21)); W=pd.DataFrame(0.0,index=dates,columns=px.columns)
    for i in idx:
        t=dates[i]
        m12=px.shift(21).loc[t]/px.shift(252).loc[t]-1; m6=px.shift(21).loc[t]/px.shift(126).loc[t]-1
        score=((z(m12)+z(m6))/2)[px.loc[t]>sma200.loc[t]].dropna()
        if len(score)<3: continue
        n=max(1,int(round(len(score)*0.20))); picks=score.sort_values(ascending=False).head(n).index
        w=pd.Series(0.0,index=px.columns); w[picks]=1.0/n; j=min(i+21,len(dates)); W.iloc[i:j]=w.values
    pr=(W.shift(1)*rets).sum(axis=1)-(W-W.shift(1)).abs().sum(axis=1)*(COST_BPS/1e4)
    pr=pr.loc[dates[idx[0]]:]
    rv=pr.rolling(30).std()*np.sqrt(252); exp=(VT/rv).clip(upper=1.0).shift(1).fillna(0)
    strat=pr*exp - exp.diff().abs().fillna(0)*(COST_BPS/1e4)
    eq_strat=(1+strat.fillna(0)).cumprod()
    spy=px["SPY"].reindex(eq_strat.index).ffill(); eq_spy=spy/spy.iloc[0]
    ew=(1+rets.mean(axis=1).reindex(eq_strat.index).fillna(0)).cumprod()
    # downsample weekly
    ds=eq_strat.index[::5]
    out={"start":str(eq_strat.index[0].date()),"end":str(eq_strat.index[-1].date()),
         "dates":[str(d.date()) for d in ds],
         "strategy":[round(float(eq_strat.loc[d]/eq_strat.iloc[0]*100),2) for d in ds],
         "spy":[round(float(eq_spy.loc[d]*100),2) for d in ds],
         "equal_weight":[round(float(ew.loc[d]/ew.iloc[0]*100),2) for d in ds],
         "label":"Flagship: momentum top-20% monthly + 15% vol target. Real prices, hypothetical trades, indexed to 100."}
    json.dump(out,open("site/data/curve.json","w"))
    print("wrote curve.json points:",len(ds))

if __name__=="__main__":
    try: main()
    except Exception as e:
        open("site/data/CURVE_ERROR.txt","w").write(traceback.format_exc()); print("FATAL",e); sys.exit(1)
