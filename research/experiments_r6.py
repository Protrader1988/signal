"""
Signal — research round 6: the aggressive mix (target: 30-60% a year).

Question: does a concentrated mix of the two things that tested best -
stock momentum (top 10% of the list) and the Bitcoin trend rule (hold above the
150-day average) - reach 30-60% a year, and what does it cost in drawdown?

Portfolios (all long-only, no borrowing, rebalanced to target weights monthly):
  baseline_current     what the paper account runs now: 90% momentum top-20% with a
                       15% volatility target + 10% Bitcoin trend
  mix_80_20 / 70_30 / 60_40 / 50_50
                       X% concentrated momentum (top 10%, no volatility target)
                       + Y% Bitcoin trend
  momentum_top10_only  100% concentrated momentum
  btc_trend_only       100% Bitcoin trend
  spy_buy_hold         the S&P 500 fund
  equal_weight_universe  every stock on the list held equally (shows how much of
                       momentum's number is just the hindsight-picked list)

Same honesty rules as every round: real prices, signals lagged a day, costs charged
(10 bps stocks, 25 bps Bitcoin), chronological 65/35 in-sample / out-of-sample split.

QUALIFYING BAR (written before seeing results). A mix earns a tracked slot only if:
  (1) out-of-sample return is at least 30% a year, AND
  (2) its worst drawdown over the full history is no deeper than -50%, AND
  (3) it beat the S&P 500 fund in at least 60% of calendar years.

Known flattery, stated up front: the stock list was chosen with today's knowledge of
which companies survived and grew, and Bitcoin's early years will not repeat. Treat
every return here as an upper bound, not a forecast.
"""

import json, os, sys, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experiments_r3 import EQUITY_UNIVERSE, IS_FRAC, log, momentum_returns, vol_target_exposure, apply_cost_on_exposure
import experiments_r5 as R5

START_EQ = "2017-01-01"; PPY = 365
MIXES = {"mix_80_20": (0.8, 0.2), "mix_70_30": (0.7, 0.3), "mix_60_40": (0.6, 0.4), "mix_50_50": (0.5, 0.5)}

def fetch_eq(tickers, start):
    import yfinance as yf
    raw = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    px = px.dropna(how="all"); return px[px.columns[px.notna().mean() > 0.55]].ffill(limit=5)

def to_calendar(ret, idx):
    """Business-day return series -> calendar days (0 on days the stock market is closed)."""
    return ret.reindex(idx).fillna(0.0)

def blend(streams, weights, rebal_days=30, cost_bps=10.0):
    """Monthly rebalance to target weights; sleeves drift in between. Returns daily portfolio returns."""
    df = pd.concat(streams, axis=1).dropna(); w_t = np.array(weights, dtype=float)
    vals = w_t.copy(); out = []
    for i, row in enumerate(df.values):
        before = vals.sum(); vals = vals * (1 + row); r = vals.sum() / before - 1
        if (i + 1) % rebal_days == 0:
            tot = vals.sum(); turn = np.abs(vals / tot - w_t).sum()
            r -= turn * cost_bps / 1e4; vals = w_t * tot * (1 - turn * cost_bps / 1e4)
        out.append(r)
    return pd.Series(out, index=df.index)

def stats(ret):
    ret = ret.dropna()
    if len(ret) < 200: return {}
    eq = (1 + ret).cumprod(); yrs = len(eq) / PPY; vol = ret.std() * np.sqrt(PPY)
    dd = eq / eq.cummax() - 1
    under = (dd < 0).astype(int); longest = int((under.groupby((under == 0).cumsum()).cumsum()).max())
    roll12 = eq / eq.shift(365) - 1
    return {"cagr_pct": round(float(eq.iloc[-1] ** (1 / yrs) - 1) * 100, 1),
            "sharpe": round(float(ret.mean() * PPY / vol), 2) if vol > 0 else None,
            "vol_pct": round(float(vol) * 100, 1), "maxDD_pct": round(float(dd.min()) * 100, 1),
            "longest_underwater_days": longest,
            "worst_12m_pct": round(float(roll12.min()) * 100, 1) if roll12.notna().any() else None,
            "best_12m_pct": round(float(roll12.max()) * 100, 1) if roll12.notna().any() else None}

def yearly(ret):
    return {str(y): round(float(((1 + ret[ret.index.year == y]).prod() - 1) * 100), 1)
            for y in sorted(set(ret.index.year)) if (ret.index.year == y).sum() > 300}

def main():
    os.makedirs("research/output", exist_ok=True)
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "bar": "OOS return >= 30%/yr AND full-history max drawdown no deeper than -50% AND beat SPY in >= 60% of calendar years"}
    try:
        eq = fetch_eq(EQUITY_UNIVERSE, START_EQ)
        btc = R5.fetch(["BTC-USD"], "2014-09-01")["BTC-USD"].dropna()
        # sleeves
        mom10 = momentum_returns(eq, top_frac=0.10)
        mom20 = momentum_returns(eq, top_frac=0.20)
        mom20_vt = apply_cost_on_exposure(mom20, vol_target_exposure(mom20, 0.15, 252).reindex(mom20.index).fillna(0))
        ew = eq.pct_change().mean(axis=1).loc[mom10.index[0]:]
        spy = eq["SPY"].pct_change().loc[mom10.index[0]:] if "SPY" in eq.columns else None
        btc_net, _ = R5.run_rule(btc, R5.exposures(btc)["sma_150"])
        cal = pd.date_range(mom10.index[0], min(mom10.index[-1], btc_net.index[-1]), freq="D")
        S = {"mom10": to_calendar(mom10, cal), "mom20_vt": to_calendar(mom20_vt, cal),
             "btc": btc_net.reindex(cal).fillna(0.0), "ew": to_calendar(ew, cal)}
        if spy is not None: S["spy"] = to_calendar(spy, cal)
        P = {"baseline_current": blend([S["mom20_vt"], S["btc"]], (0.9, 0.1))}
        for k, (a, b) in MIXES.items(): P[k] = blend([S["mom10"], S["btc"]], (a, b))
        P["momentum_top10_only"] = S["mom10"]; P["btc_trend_only"] = S["btc"]
        if "spy" in S: P["spy_buy_hold"] = S["spy"]
        P["equal_weight_universe"] = S["ew"]
        cut = cal[int(len(cal) * IS_FRAC)]
        spy_y = yearly(S["spy"]) if "spy" in S else {}
        out = {}
        for k, r in P.items():
            y = yearly(r)
            beat = [yy for yy in y if yy in spy_y and y[yy] > spy_y[yy]]
            out[k] = {"full": stats(r), "in_sample": stats(r.loc[:cut]), "out_sample": stats(r.loc[cut:]), "yearly_pct": y,
                      "years_beating_spy": f"{len(beat)} of {len([yy for yy in y if yy in spy_y])}" if spy_y else None,
                      "beat_spy_share": round(len(beat) / max(1, len([yy for yy in y if yy in spy_y])), 2) if spy_y else None}
        for k in MIXES:
            v = out[k]
            try: v["qualifies"] = bool(v["out_sample"]["cagr_pct"] >= 30 and v["full"]["maxDD_pct"] >= -50 and (v["beat_spy_share"] or 0) >= 0.6)
            except Exception: v["qualifies"] = False
        res.update({"period": f"{cal[0].date()} to {cal[-1].date()}", "out_sample_from": str(cut.date()), "portfolios": out,
                    "qualifying": [k for k in MIXES if out[k].get("qualifies")],
                    "correlation_mom10_btc": round(float(S["mom10"].corr(S["btc"])), 2)})
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"; res["tb"] = traceback.format_exc()
    json.dump(res, open("research/output/experiments_r6.json", "w"), indent=2)

    L = ["# Research round 6 — the aggressive mix (target 30-60% a year)", "", f"_Generated {res['generated_utc']}_", "",
         f"Qualifying bar (set in advance): {res['bar']}.", ""]
    if "portfolios" in res:
        L += [f"Period {res['period']} · out-of-sample from {res['out_sample_from']} · momentum/Bitcoin daily correlation {res['correlation_mom10_btc']}", ""]
        for k, v in res["portfolios"].items():
            f, o = v["full"], v["out_sample"]
            q = "" if "qualifies" not in v else (" · **QUALIFIES**" if v["qualifies"] else " · does not qualify")
            L.append(f"- **{k}**: full {f.get('cagr_pct')}%/yr Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% "
                     f"worst-12m {f.get('worst_12m_pct')}% underwater {f.get('longest_underwater_days')}d | "
                     f"OOS {o.get('cagr_pct')}%/yr Sharpe {o.get('sharpe')} maxDD {o.get('maxDD_pct')}% | beat SPY {v.get('years_beating_spy')}{q}")
        L += ["", "Yearly returns (%):"]
        keys = [k for k in ("spy_buy_hold", "baseline_current", "mix_80_20", "mix_70_30", "mix_60_40", "mix_50_50") if k in res["portfolios"]]
        for y in res["portfolios"][keys[0]]["yearly_pct"]:
            L.append(f"- {y}: " + " · ".join(f"{k} {res['portfolios'][k]['yearly_pct'].get(y)}" for k in keys))
        L += ["", f"Qualifying mixes: {', '.join(res['qualifying']) or 'none'}"]
    else:
        L += ["## Error", res.get("error", "unknown")]
    open("research/output/EXPERIMENTS_R6.md", "w").write("\n".join(L))
    log("done")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        os.makedirs("research/output", exist_ok=True)
        open("research/output/EXPERIMENTS_R6_ERROR.txt", "w").write(traceback.format_exc()); log(f"FATAL {e}"); sys.exit(1)
