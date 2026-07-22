"""
Signal — research round 2: harden the edge, rework what failed.

Three honest experiments, all with no look-ahead, real costs, IS/OOS split:

  EXP 1  Equity momentum — is it survivorship-proof and parameter-stable?
         Broader universe (incl. known laggards). Benchmark vs EQUAL-WEIGHT of
         the SAME universe: since strategy and benchmark share the identical
         (survivorship-biased) name list, any outperformance is NOT explained
         by survivorship. Plus a parameter sweep to show the edge is stable,
         not cherry-picked.

  EXP 2  Short-term (3-7 day) book — reversal failed. Test alternatives:
         short-term momentum (continuation), 52w-high breakout proximity, and
         the reversal baseline, all trend-filtered, weekly hold.

  EXP 3  Crypto — cross-sectional selection failed. Test TIME-SERIES trend
         timing instead: hold each coin only when it's above its 200d SMA and
         its long-lookback return is positive; otherwise sit in cash. Compare
         to buy&hold basket and to BTC/ETH-only timing.
"""

import json, sys, traceback
from datetime import datetime, timezone
import numpy as np
import pandas as pd

COST_BPS = 10.0
IS_FRAC = 0.65
START = "2017-01-01"

# Broader, sector-spread universe that deliberately includes names that have
# LAGGED or fallen (INTC, PYPL, DIS, WBA, F, T, VZ, PARA, MMM, NKE, PFE, ...),
# so the test isn't just "today's winners".
EQUITY_UNIVERSE = [
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
CRYPTO_UNIVERSE = ["BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD","ADA-USD",
                   "AVAX-USD","DOGE-USD","LINK-USD","DOT-USD","LTC-USD"]


def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)

def fetch(tickers, ppy_hint="stock"):
    import yfinance as yf
    log(f"Downloading {len(tickers)} tickers...")
    raw = yf.download(tickers, start=START, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    px = px.dropna(how="all")
    good = px.columns[px.notna().mean() > 0.55]
    px = px[good].ffill(limit=5)
    log(f"  usable {len(px.columns)} rows {len(px)} {px.index.min().date()}..{px.index.max().date()}")
    return px

def stats(equity, ppy):
    equity = equity.dropna()
    if len(equity) < 30: return {}
    r = equity.pct_change().dropna()
    yrs = len(equity)/ppy
    cagr = (equity.iloc[-1]/equity.iloc[0])**(1/yrs)-1 if yrs>0 else np.nan
    vol = r.std()*np.sqrt(ppy)
    sharpe = (r.mean()*ppy)/vol if vol>0 else np.nan
    dd = (equity/equity.cummax()-1).min()
    return {"cagr_pct":round(cagr*100,1),"sharpe":round(sharpe,2),
            "vol_pct":round(vol*100,1),"maxDD_pct":round(dd*100,1)}

def iso(equity, ppy):
    equity = equity.dropna()
    if len(equity) < 60: return {"error":"insufficient"}
    cut = int(len(equity)*IS_FRAC)
    return {"full":stats(equity/equity.iloc[0],ppy),
            "in_sample":stats(equity.iloc[:cut]/equity.iloc[0],ppy),
            "out_sample":stats(equity.iloc[cut:]/equity.iloc[cut],ppy),
            "split":str(equity.index[cut].date())}

def zscore(s):
    s=s.astype(float); sd=s.std()
    return s*0.0 if (not np.isfinite(sd) or sd==0) else (s-s.mean())/sd

def cross_sectional(px, score_fn, eligible_fn, rebal, top_frac, ppy):
    """Long-only cross-sectional backtest. score_fn/eligible_fn take (px,t)."""
    rets = px.pct_change()
    dates = px.index
    idx = list(range(252, len(dates), rebal))
    if not idx: return pd.Series(dtype=float)
    W = pd.DataFrame(0.0, index=dates, columns=px.columns)
    for i in idx:
        t = dates[i]
        score = score_fn(px, t)
        elig = eligible_fn(px, t).reindex(score.index).fillna(False)
        score = score[elig].dropna()
        if len(score) < 3: continue
        n = max(1, int(round(len(score)*top_frac)))
        picks = score.sort_values(ascending=False).head(n).index
        w = pd.Series(0.0, index=px.columns); w[picks] = 1.0/n
        j = min(i+rebal, len(dates)); W.iloc[i:j] = w.values
    pr = (W.shift(1)*rets).sum(axis=1)
    to = (W-W.shift(1)).abs().sum(axis=1)
    pr = (pr - to*(COST_BPS/1e4)).loc[dates[idx[0]]:]
    return (1+pr.fillna(0)).cumprod()

def ew_benchmark(px, start_at=None):
    r = px.pct_change().mean(axis=1)
    if start_at is not None: r = r.loc[start_at:]
    return (1+r.fillna(0)).cumprod()

# ---- Experiment 1: equity momentum robustness -------------------------------
def exp1(px):
    ppy=252
    sma200 = px.rolling(200).mean()
    def score(px,t):
        m12 = px.shift(21).loc[t]/px.shift(252).loc[t]-1
        m6  = px.shift(21).loc[t]/px.shift(126).loc[t]-1
        return (zscore(m12)+zscore(m6))/2
    def elig(px,t): return px.loc[t] > sma200.loc[t]
    out={"universe_size":int(px.shape[1]), "sweep":{}}
    base=None
    for tf in (0.10,0.20,0.30):
        eq = cross_sectional(px, score, elig, 21, tf, ppy)
        out["sweep"][f"top_{int(tf*100)}pct"] = iso(eq, ppy)
        if abs(tf-0.20)<1e-9: base=eq
    # survivorship-neutral benchmark: EW of the SAME universe over same window
    if base is not None:
        ew = ew_benchmark(px, start_at=base.index[0])
        out["equal_weight_same_universe"] = iso(ew, ppy)
    return out

# ---- Experiment 2: short-term book variants ---------------------------------
def exp2(px):
    ppy=252
    sma100 = px.rolling(100).mean()
    hi252  = px.rolling(252).max()
    def elig(px,t):
        m3 = px.loc[t]/px.shift(63).loc[t]-1
        return (px.loc[t]>sma100.loc[t]) & (m3>0)
    variants={
        "short_mom_10d": lambda px,t: zscore(px.loc[t]/px.shift(10).loc[t]-1),      # continuation
        "breakout_52w":  lambda px,t: zscore(px.loc[t]/hi252.loc[t]),               # near highs
        "reversal_5d":   lambda px,t: -zscore(px.loc[t]/px.shift(5).loc[t]-1),      # baseline (failed)
    }
    out={}
    for name,sc in variants.items():
        eq = cross_sectional(px, sc, elig, 5, 0.20, ppy)
        out[name]=iso(eq,ppy)
    return out

# ---- Experiment 3: crypto trend timing --------------------------------------
def exp3(px):
    ppy=365
    sma200 = px.rolling(200).mean()
    mom = px/px.shift(200)-1
    rets = px.pct_change()
    # time-series: each coin ON when above 200d SMA and 200d momentum>0
    on = (px>sma200) & (mom>0)
    # equal weight across coins that are ON that day; else that sleeve is cash
    w = on.astype(float)
    w = w.div(w.sum(axis=1).replace(0,np.nan), axis=0).fillna(0.0)
    pr = (w.shift(1)*rets).sum(axis=1)
    to = (w-w.shift(1)).abs().sum(axis=1)
    pr = pr - to*(COST_BPS/1e4)
    eq_timed = (1+pr.loc[px.index[252]:].fillna(0)).cumprod()
    # BTC/ETH only timing
    core = [c for c in ("BTC-USD","ETH-USD") if c in px.columns]
    wc = on[core].astype(float); wc = wc.div(wc.sum(axis=1).replace(0,np.nan),axis=0).fillna(0.0)
    prc = (wc.shift(1)*rets[core]).sum(axis=1); toc=(wc-wc.shift(1)).abs().sum(axis=1)
    prc = prc - toc*(COST_BPS/1e4)
    eq_core = (1+prc.loc[px.index[252]:].fillna(0)).cumprod()
    bh = ew_benchmark(px, start_at=px.index[252])
    return {"trend_timed_all": iso(eq_timed,ppy),
            "trend_timed_BTC_ETH": iso(eq_core,ppy),
            "buy_hold_basket": iso(bh,ppy)}

def main():
    res={"generated_utc":datetime.now(timezone.utc).isoformat(),
         "params":{"cost_bps":COST_BPS,"in_sample_frac":IS_FRAC,"start":START}}
    try:
        eq_px = fetch(EQUITY_UNIVERSE)
        res["exp1_equity_momentum_robustness"]=exp1(eq_px)
        res["exp2_shortterm_variants"]=exp2(eq_px)
    except Exception as e:
        res["equity_error"]=f"{type(e).__name__}: {e}"; res["equity_tb"]=traceback.format_exc()
    try:
        c_px = fetch(CRYPTO_UNIVERSE)
        res["exp3_crypto_timing"]=exp3(c_px)
    except Exception as e:
        res["crypto_error"]=f"{type(e).__name__}: {e}"; res["crypto_tb"]=traceback.format_exc()

    json.dump(res, open("research/output/experiments.json","w"), indent=2)
    log("wrote experiments.json")

    L=["# Research round 2 — hardening", "", f"_Generated {res['generated_utc']}_",""]
    e1=res.get("exp1_equity_momentum_robustness",{})
    L.append("## EXP1 Equity momentum — robustness (survivorship-neutral)")
    L.append(f"Universe: {e1.get('universe_size')} names")
    for k,v in e1.get("sweep",{}).items():
        f=v.get("full",{}); o=v.get("out_sample",{})
        L.append(f"- {k}: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
                 f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')}")
    ew=e1.get("equal_weight_same_universe",{})
    L.append(f"- EQUAL-WEIGHT same universe (survivorship-neutral benchmark): "
             f"full CAGR {ew.get('full',{}).get('cagr_pct')}% Sharpe {ew.get('full',{}).get('sharpe')} | "
             f"OOS Sharpe {ew.get('out_sample',{}).get('sharpe')}")
    L.append("")
    L.append("## EXP2 Short-term book variants (weekly)")
    for k,v in res.get("exp2_shortterm_variants",{}).items():
        f=v.get("full",{}); o=v.get("out_sample",{})
        L.append(f"- {k}: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
                 f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')}")
    L.append("")
    L.append("## EXP3 Crypto trend timing")
    for k,v in res.get("exp3_crypto_timing",{}).items():
        f=v.get("full",{}); o=v.get("out_sample",{})
        L.append(f"- {k}: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
                 f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')}")
    open("research/output/EXPERIMENTS.md","w").write("\n".join(L))
    log("wrote EXPERIMENTS.md")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        open("research/output/EXPERIMENTS_ERROR.txt","w").write(traceback.format_exc())
        log(f"FATAL {e}"); sys.exit(1)
