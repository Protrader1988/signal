"""
Signal — research round 5: is there a Bitcoin strategy worth paper trading?

Earlier rounds found no stock-picking-style edge in crypto: ranking coins against
each other failed, and simply holding the basket beat everything on raw return,
at the price of -85% drawdowns. This round asks a narrower question about
Bitcoin alone, on its full daily history (2014 onward):

    Can a simple, pre-specified trend rule keep most of Bitcoin's return while
    avoiding most of its crashes?

Rules tested (long or cash only; no shorting, no leverage):
  buy_hold            hold Bitcoin throughout (the benchmark to beat)
  sma_N               hold while price is above its N-day average (N = 100, 150, 200)
  cross_50_200        hold while the 50-day average is above the 200-day average
  mom_blend           exposure = share of the 30/90/180-day returns that are positive
  breakout_50_20      buy on a 50-day high, sell on a 20-day low
  each of the above also with a 40% volatility target (smaller position when swings are large)

Honesty rules, same as every round:
  - signals use only prices up to yesterday; trades happen the next day
  - 25 bps charged on every change in position (crypto costs more to trade than stocks)
  - chronological split: first 65% in-sample, last 35% out-of-sample
  - several lookbacks are shown side by side so one lucky setting cannot be cherry-picked

PASS BAR (written before seeing results). A rule earns a paper-trading slot only if,
OUT-OF-SAMPLE, it has BOTH:
  (1) a higher Sharpe ratio than buy-and-hold, and
  (2) a worst drawdown no deeper than 60% of buy-and-hold's,
AND the same rule also beats buy-and-hold's Sharpe over the FULL history.
If nothing passes, the honest conclusion is "hold or stay out", not "trade it".
"""

import json, os, sys, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

COST_BPS = 25.0; IS_FRAC = 0.65; START = "2014-09-01"; PPY = 365
ASSETS = ["BTC-USD", "ETH-USD"]

def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)

def fetch(tickers, start):
    import yfinance as yf
    raw = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    return px.dropna(how="all").ffill(limit=3)

def stats(ret):
    ret = ret.dropna()
    if len(ret) < 200: return {}
    eq = (1 + ret).cumprod(); yrs = len(eq) / PPY
    vol = ret.std() * np.sqrt(PPY)
    return {"cagr_pct": round(float((eq.iloc[-1]) ** (1 / yrs) - 1) * 100, 1),
            "sharpe": round(float(ret.mean() * PPY / vol), 2) if vol > 0 else None,
            "vol_pct": round(float(vol) * 100, 1),
            "maxDD_pct": round(float((eq / eq.cummax() - 1).min()) * 100, 1)}

def exposures(p):
    """Each rule -> exposure series in [0,1] decided at day t's close."""
    e = {}
    e["buy_hold"] = pd.Series(1.0, index=p.index)
    for n in (100, 150, 200):
        sma = p.rolling(n).mean(); e[f"sma_{n}"] = (p > sma).astype(float).where(sma.notna())
    s50, s200 = p.rolling(50).mean(), p.rolling(200).mean()
    e["cross_50_200"] = (s50 > s200).astype(float).where(s200.notna())
    e["mom_blend"] = (sum((p / p.shift(n) - 1 > 0).astype(float) for n in (30, 90, 180)) / 3.0).where(p.shift(180).notna())
    hi, lo = p.rolling(50).max(), p.rolling(20).min()
    state = pd.Series(np.nan, index=p.index); on = 0.0
    for i in range(len(p)):
        if np.isfinite(hi.iloc[i]):
            if p.iloc[i] >= hi.iloc[i]: on = 1.0
            elif p.iloc[i] <= lo.iloc[i]: on = 0.0
            state.iloc[i] = on
    e["breakout_50_20"] = state
    return e

def run_rule(p, exp, vol_target=None):
    r = p.pct_change()
    if vol_target:
        rv = r.rolling(30).std() * np.sqrt(PPY)
        exp = (exp * (vol_target / rv).clip(upper=1.0))
    pos = exp.shift(1)                                   # trade the day after the signal
    net = pos * r - pos.diff().abs().fillna(0.0) * (COST_BPS / 1e4)
    return net, pos

def evaluate(p, name):
    p = p.dropna(); out = {}
    start = p.index[200] if len(p) > 400 else p.index[0]
    cut = p.index[int(len(p) * IS_FRAC)]
    for rule, exp in exposures(p).items():
        for vt in (None, 0.40):
            key = rule + ("_vt40" if vt else "")
            net, pos = run_rule(p, exp, vt)
            net, pos = net.loc[start:], pos.loc[start:]
            yearly = {str(y): round(float(((1 + net[net.index.year == y]).prod() - 1) * 100), 1)
                      for y in sorted(set(net.index.year)) if (net.index.year == y).sum() > 300}
            out[key] = {"full": stats(net), "in_sample": stats(net.loc[:cut]), "out_sample": stats(net.loc[cut:]),
                        "time_in_market_pct": round(float((pos.fillna(0) > 0).mean()) * 100, 1),
                        "avg_exposure_pct": round(float(pos.fillna(0).mean()) * 100, 1),
                        "position_changes_per_year": round(float((pos.diff().abs() > 0.05).sum() / (len(pos) / PPY)), 1),
                        "yearly_pct": yearly}
    bh = out["buy_hold"]
    for key, v in out.items():
        if key == "buy_hold": v["passes"] = None; continue
        try:
            v["passes"] = bool(v["out_sample"]["sharpe"] > bh["out_sample"]["sharpe"]
                               and abs(v["out_sample"]["maxDD_pct"]) <= 0.60 * abs(bh["out_sample"]["maxDD_pct"])
                               and v["full"]["sharpe"] > bh["full"]["sharpe"])
        except Exception: v["passes"] = False
    return {"asset": name, "first_day": str(start.date()), "last_day": str(p.index[-1].date()),
            "out_sample_from": str(cut.date()), "rules": out,
            "passing_rules": [k for k, v in out.items() if v.get("passes")]}

def main():
    os.makedirs("research/output", exist_ok=True)
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "params": {"cost_bps": COST_BPS, "in_sample_frac": IS_FRAC, "vol_target": 0.40},
           "pass_bar": "OOS Sharpe above buy-and-hold AND OOS max drawdown no deeper than 60% of buy-and-hold's AND full-history Sharpe above buy-and-hold"}
    try:
        px = fetch(ASSETS, START)
        for a in ASSETS:
            if a in px.columns:
                try: res[a] = evaluate(px[a], a)
                except Exception as e: res[a + "_error"] = f"{type(e).__name__}: {e}"; res[a + "_tb"] = traceback.format_exc()
    except Exception as e:
        res["fetch_error"] = f"{type(e).__name__}: {e}"
    json.dump(res, open("research/output/experiments_r5.json", "w"), indent=2)

    L = ["# Research round 5 — a Bitcoin strategy worth paper trading?", "", f"_Generated {res['generated_utc']}_", "",
         f"Pass bar (set in advance): {res['pass_bar']}.", ""]
    for a in ASSETS:
        d = res.get(a)
        if not d: continue
        L += [f"## {a}  ({d['first_day']} to {d['last_day']}; out-of-sample from {d['out_sample_from']})", ""]
        for k, v in d["rules"].items():
            f, o = v["full"], v["out_sample"]
            flag = "" if v["passes"] is None else (" · **PASS**" if v["passes"] else " · fail")
            L.append(f"- **{k}**: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
                     f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')} maxDD {o.get('maxDD_pct')}% | "
                     f"in market {v['time_in_market_pct']}% · {v['position_changes_per_year']} changes/yr{flag}")
        L += ["", f"Passing rules: {', '.join(d['passing_rules']) or 'none'}", "",
              "Yearly returns (%), buy-and-hold vs sma_200 vs mom_blend:"]
        for y in d["rules"]["buy_hold"]["yearly_pct"]:
            L.append(f"- {y}: hold {d['rules']['buy_hold']['yearly_pct'].get(y)} · sma_200 {d['rules']['sma_200']['yearly_pct'].get(y)} · "
                     f"mom_blend {d['rules']['mom_blend']['yearly_pct'].get(y)}")
        L.append("")
    errs = [k for k in res if k.endswith("_error")]
    if errs: L += ["## Errors"] + [f"- {k}: {res[k]}" for k in errs]
    open("research/output/EXPERIMENTS_R5.md", "w").write("\n".join(L))
    log("done")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        os.makedirs("research/output", exist_ok=True)
        open("research/output/EXPERIMENTS_R5_ERROR.txt", "w").write(traceback.format_exc()); log(f"FATAL {e}"); sys.exit(1)
