"""
Signal — research round 4: can the engine be made to win bigger and lose smaller?

Same discipline as rounds 1-3: real prices, no look-ahead, 10 bps costs,
chronological in-sample / out-of-sample split. Nothing here is tuned to the
out-of-sample period.

  EXP A  Do stop-losses help the position book?
         Trade-level test of the live rules: every monthly momentum pick, held
         ~21 trading days, with no stop vs an ATR stop vs a trailing stop.
         Question: do stops make losses smaller WITHOUT giving up the edge?
         (Stops often cut the winners that momentum depends on. Measure it.)

  EXP B  Multi-asset trend following (time-series momentum).
         Stocks, bonds, gold, commodities, real estate, dollar ETFs. Hold each
         asset only while its own trend is up; size by inverse volatility.
         Long/flat and long/short. History from 2007 so it includes 2008.
         Question: is this a second return stream that pays when stocks fall?

  EXP C  Better stock selection on the SAME universe:
         momentum (validated base) vs momentum with the most volatile third
         removed vs smooth-path momentum ("frog in the pan") vs low-volatility.
         Question: which cuts the single-stock blowups (INTC/AMD-style)?

  EXP D  Combine the two engines (equity momentum + multi-asset trend).
         Question: does diversification raise return per unit of risk?

  EXP F  The conditions score ("when to go heavy, when to tread carefully").
         The live engine's 5-check market-conditions score sets exposure
         (100/75/50/25/0%). Applied to the momentum book with a one-day lag.
         Question: does sizing by conditions beat constant size, or is it just
         a slower way to be in cash? Compared with constant 100%, with the
         plain 15% vol target, and with a constant size equal to the score's
         own average exposure (so less risk alone does not count as a win).

  EXP E  The "win big" dial. Take the best combination and scale it to
         10/15/20/25% annual volatility, allowing up to 2x leverage with a
         5%/yr financing charge. Shows the honest trade: every step up in
         return comes with a deeper worst-case loss.
"""

import json, sys, os, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experiments_r3 import (EQUITY_UNIVERSE, COST_BPS, IS_FRAC, log, stats, iso, z,
                            momentum_returns, vol_target_exposure, apply_cost_on_exposure)

START_EQ = "2017-01-01"
START_TREND = "2006-01-01"
TREND_UNIVERSE = ["SPY","QQQ","IWM","EFA","EEM",          # equities
                  "TLT","IEF","LQD","HYG","TIP",          # bonds / credit
                  "GLD","SLV","DBC","USO",                # metals / commodities
                  "VNQ","UUP"]                            # real estate, US dollar
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
from signal_engine import conditions_frame, CONTEXT
FINANCING = 0.05   # annual cost charged on any exposure above 1.0

def fetch(tickers, start):
    import yfinance as yf
    log(f"downloading {len(tickers)} from {start}...")
    raw = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    px = px.dropna(how="all"); px = px[px.columns[px.notna().mean() > 0.55]].ffill(limit=5)
    log(f"  usable {len(px.columns)} rows {len(px)}"); return px

# ----------------------------------------------------------------- EXP A
def exp_stops(px, rebal=21, top_frac=0.20, hold=21):
    """Trade-level: each monthly pick entered at the NEXT close, held `hold` days."""
    dates = px.index; sma200 = px.rolling(200).mean()
    atr = px.diff().abs().rolling(14).mean()
    cost = 2 * COST_BPS / 100.0
    rules = {"no_stop": None, "atr2_stop": ("atr", 2.0), "atr3_stop": ("atr", 3.0),
             "trail10_stop": ("trail", 0.10), "trail15_stop": ("trail", 0.15)}
    trades = {k: [] for k in rules}
    cut = dates[int(len(dates) * IS_FRAC)]
    for i in range(252, len(dates) - hold - 2, rebal):
        t = dates[i]
        m12 = px.shift(21).loc[t] / px.shift(252).loc[t] - 1
        m6 = px.shift(21).loc[t] / px.shift(126).loc[t] - 1
        score = ((z(m12) + z(m6)) / 2)[px.loc[t] > sma200.loc[t]].dropna()
        if len(score) < 3: continue
        picks = score.sort_values(ascending=False).head(max(1, int(round(len(score) * top_frac)))).index
        for tk in picks:
            path = px[tk].iloc[i + 1:i + 2 + hold].dropna()
            if len(path) < 3: continue
            entry = float(path.iloc[0]); a = float(atr[tk].iloc[i])
            for name, rule in rules.items():
                exit_px = float(path.iloc[-1]); peak = entry
                if rule is not None:
                    for p in path.iloc[1:]:
                        p = float(p); peak = max(peak, p)
                        lvl = entry - rule[1] * a if rule[0] == "atr" else peak * (1 - rule[1])
                        if np.isfinite(lvl) and p <= lvl: exit_px = p; break
                trades[name].append(((exit_px / entry - 1) * 100 - cost, t >= cut))
    def summ(rows):
        r = np.array([x for x in rows])
        if len(r) < 20: return {"n": int(len(r))}
        w = r[r > 0]; l = r[r <= 0]
        return {"n": int(len(r)), "win_rate_pct": round(100 * len(w) / len(r), 1),
                "avg_trade_pct": round(float(r.mean()), 2),
                "avg_win_pct": round(float(w.mean()), 2) if len(w) else 0.0,
                "avg_loss_pct": round(float(l.mean()), 2) if len(l) else 0.0,
                "worst_pct": round(float(r.min()), 1),
                "win_loss_ratio": round(float(w.mean() / -l.mean()), 2) if len(w) and len(l) and l.mean() < 0 else None,
                "t_stat": round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2)}
    return {k: {"full": summ([x for x, _ in v]), "out_sample": summ([x for x, o in v if o])} for k, v in trades.items()}

# ----------------------------------------------------------------- EXP B
def trend_returns(px, mode="long_flat", blend=False, rebal=21, vol_lb=60):
    """Time-series momentum. Signal per asset from its own past return; inverse-vol sized;
    gross exposure capped at 1.0. Weights set at rebalance, applied from the next day."""
    rets = px.pct_change(); dates = px.index
    vol = rets.rolling(vol_lb).std() * np.sqrt(252)
    W = pd.DataFrame(0.0, index=dates, columns=px.columns)
    idx = list(range(260, len(dates), rebal))
    for i in idx:
        t = dates[i]
        if blend:
            sig = sum(np.sign(px.loc[t] / px.shift(n).loc[t] - 1) for n in (21, 63, 252)) / 3.0
        else:
            sig = np.sign(px.loc[t] / px.shift(252).loc[t] - 1)
        if mode == "long_flat": sig = sig.clip(lower=0)
        iv = (1.0 / vol.loc[t]).replace([np.inf, -np.inf], np.nan)
        w = (sig * iv).dropna()
        base = iv.reindex(w.index).sum()          # normalise by ALL assets' inverse-vol: flat assets = cash
        w = w / base if base and np.isfinite(base) else w * 0
        if w.abs().sum() > 1.0: w = w / w.abs().sum()
        row = pd.Series(0.0, index=px.columns); row[w.index] = w.values
        W.iloc[i:min(i + rebal, len(dates))] = row.values
    pr = (W.shift(1) * rets).sum(axis=1) - (W - W.shift(1)).abs().sum(axis=1) * (COST_BPS / 1e4)
    return pr.loc[dates[idx[0]]:]

def yearly(ret, years):
    out = {}
    for y in years:
        r = ret[ret.index.year == y]
        if len(r) > 100: out[str(y)] = round(float(((1 + r).prod() - 1) * 100), 1)
    return out

# ----------------------------------------------------------------- EXP C
def selection_returns(px, kind, rebal=21, top_frac=0.20):
    rets = px.pct_change(); sma200 = px.rolling(200).mean(); dates = px.index
    vol = rets.rolling(126).std()
    pos_frac = (rets > 0).rolling(252).mean(); neg_frac = (rets < 0).rolling(252).mean()
    W = pd.DataFrame(0.0, index=dates, columns=px.columns)
    idx = list(range(252, len(dates), rebal))
    for i in idx:
        t = dates[i]; up = px.loc[t] > sma200.loc[t]
        m12 = px.shift(21).loc[t] / px.shift(252).loc[t] - 1
        m6 = px.shift(21).loc[t] / px.shift(126).loc[t] - 1
        mom = ((z(m12) + z(m6)) / 2)[up].dropna()
        if len(mom) < 6: continue
        n = max(1, int(round(len(mom) * top_frac)))
        if kind == "momentum":
            picks = mom.sort_values(ascending=False).head(n).index
        elif kind == "momentum_ex_high_vol":
            v = vol.loc[t].reindex(mom.index).dropna()
            keep = v[v <= v.quantile(2 / 3)].index
            picks = mom.reindex(keep).dropna().sort_values(ascending=False).head(n).index
        elif kind == "smooth_momentum":
            # frog-in-the-pan: among the top-40% momentum names, keep the smoothest paths
            pool = mom.sort_values(ascending=False).head(max(n, int(round(len(mom) * 0.40)))).index
            smooth = (pos_frac.loc[t] - neg_frac.loc[t]).reindex(pool).dropna()
            picks = smooth.sort_values(ascending=False).head(n).index
        elif kind == "low_vol":
            v = vol.loc[t][up].dropna()
            picks = v.sort_values().head(n).index
        else:
            raise ValueError(kind)
        if len(picks) == 0: continue
        w = pd.Series(0.0, index=px.columns); w[picks] = 1.0 / len(picks)
        W.iloc[i:min(i + rebal, len(dates))] = w.values
    pr = (W.shift(1) * rets).sum(axis=1) - (W - W.shift(1)).abs().sum(axis=1) * (COST_BPS / 1e4)
    return pr.loc[dates[idx[0]]:]

# ----------------------------------------------------------------- EXP E
def scale_to_vol(ret, target, cap=2.0, lookback=60):
    rv = ret.rolling(lookback).std() * np.sqrt(252)
    exp = (target / rv).clip(upper=cap).shift(1).fillna(0.0)
    fin = (exp - 1.0).clip(lower=0) * (FINANCING / 252)
    return ret * exp - exp.diff().abs().fillna(0.0) * (COST_BPS / 1e4) - fin

def main():
    os.makedirs("research/output", exist_ok=True)
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "params": {"cost_bps": COST_BPS, "in_sample_frac": IS_FRAC, "financing_annual": FINANCING}}
    eq = tr = None
    try: eq = fetch(EQUITY_UNIVERSE, START_EQ)
    except Exception as e: res["equity_fetch_error"] = f"{type(e).__name__}: {e}"
    try: tr = fetch(TREND_UNIVERSE, START_TREND)
    except Exception as e: res["trend_fetch_error"] = f"{type(e).__name__}: {e}"

    def guard(key, fn):
        try: res[key] = fn()
        except Exception as e:
            res[key + "_error"] = f"{type(e).__name__}: {e}"; res[key + "_tb"] = traceback.format_exc()

    if eq is not None:
        guard("expA_stops", lambda: exp_stops(eq))
        def c():
            out = {}
            for k in ("momentum", "momentum_ex_high_vol", "smooth_momentum", "low_vol"):
                out[k] = iso(selection_returns(eq, k), 252)
            ew = eq.pct_change().mean(axis=1).loc[eq.index[252]:]
            out["equal_weight_same_universe"] = iso(ew, 252)
            return out
        guard("expC_selection", c)

    if eq is not None:
        def f():
            try: ctx = fetch(CONTEXT, START_EQ)
            except Exception: ctx = None
            mom = momentum_returns(eq)
            cf = conditions_frame(eq, ctx)
            exp = cf["exposure"].reindex(mom.index).shift(1).fillna(0.0)
            avg = float(exp.mean())
            out = {"constant_100pct": iso(mom, 252),
                   "conditions_score_sizing": iso(apply_cost_on_exposure(mom, exp), 252),
                   "constant_at_same_average_size": iso(mom * avg, 252),
                   "vol_target_15pct": iso(apply_cost_on_exposure(mom, vol_target_exposure(mom, 0.15, 252).reindex(mom.index).fillna(0)), 252),
                   "average_exposure_pct": round(avg * 100, 1),
                   "days_by_exposure_pct": {str(int(k * 100)): int(v) for k, v in exp.value_counts().sort_index().items()},
                   "credit_check_available": bool(ctx is not None and "credit" in cf.columns)}
            # forward 21-day SPY return by score: does the score separate good periods from bad?
            if "SPY" in eq.columns:
                fwd = eq["SPY"].shift(-21) / eq["SPY"] - 1
                g = pd.concat([cf["score"], fwd.rename("fwd")], axis=1).dropna().groupby("score")["fwd"]
                out["spy_next_month_by_score"] = {str(int(k)): {"avg_pct": round(float(v.mean() * 100), 2), "days": int(len(v))} for k, v in g}
            return out
        guard("expF_conditions", f)

    streams = {}
    if tr is not None:
        def b():
            out = {}
            for name, kw in (("trend_long_flat_12m", dict(mode="long_flat")),
                             ("trend_long_flat_blend", dict(mode="long_flat", blend=True)),
                             ("trend_long_short_blend", dict(mode="long_short", blend=True))):
                r = trend_returns(tr, **kw); streams[name] = r
                out[name] = iso(r, 252); out[name]["crisis_years_pct"] = yearly(r, (2008, 2018, 2020, 2022))
            if "SPY" in tr.columns:
                spy = tr["SPY"].pct_change().loc[streams["trend_long_flat_12m"].index[0]:]
                streams["spy"] = spy
                out["spy_buy_hold"] = iso(spy, 252); out["spy_buy_hold"]["crisis_years_pct"] = yearly(spy, (2008, 2018, 2020, 2022))
                eqw = tr.pct_change().mean(axis=1).loc[spy.index[0]:]
                out["equal_weight_all_assets"] = iso(eqw, 252)
            return out
        guard("expB_trend", b)

    if eq is not None and streams:
        def d():
            mom = momentum_returns(eq)
            mom_vt = apply_cost_on_exposure(mom, vol_target_exposure(mom, 0.15, 252).reindex(mom.index).fillna(0))
            best_name = "trend_long_flat_blend"; trend = streams[best_name]
            df = pd.concat([mom_vt.rename("mom"), trend.rename("trend")], axis=1).dropna()
            combo = 0.5 * df["mom"] + 0.5 * df["trend"]; streams["combo"] = combo
            out = {"equity_momentum_voltarget15": iso(df["mom"], 252),
                   "trend_same_period": iso(df["trend"], 252),
                   "combo_50_50": iso(combo, 252),
                   "correlation_daily": round(float(df["mom"].corr(df["trend"])), 2),
                   "trend_variant_used": best_name}
            if "spy" in streams: out["spy_same_period"] = iso(streams["spy"].reindex(df.index).fillna(0), 252)
            return out
        guard("expD_combination", d)
        if "combo" in streams:
            guard("expE_risk_dial", lambda: {f"vol_target_{int(v*100)}pct": iso(scale_to_vol(streams["combo"], v), 252)
                                             for v in (0.10, 0.15, 0.20, 0.25)})

    json.dump(res, open("research/output/experiments_r4.json", "w"), indent=2)

    def line(k, v):
        f = v.get("full", {}); o = v.get("out_sample", {})
        s = (f"- **{k}**: full CAGR {f.get('cagr_pct')}% Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% | "
             f"OOS CAGR {o.get('cagr_pct')}% Sharpe {o.get('sharpe')} maxDD {o.get('maxDD_pct')}%")
        if "crisis_years_pct" in v: s += f" | years {v['crisis_years_pct']}"
        return s
    L = ["# Research round 4 — win bigger, lose smaller?", "", f"_Generated {res['generated_utc']}_", "",
         "## EXP A Stops on the position book (per trade, after 20 bps round trip)"]
    for k, v in res.get("expA_stops", {}).items():
        f = v.get("full", {}); o = v.get("out_sample", {})
        L.append(f"- **{k}**: n {f.get('n')} · win {f.get('win_rate_pct')}% · avg trade {f.get('avg_trade_pct')}% · "
                 f"avg win {f.get('avg_win_pct')}% · avg loss {f.get('avg_loss_pct')}% · worst {f.get('worst_pct')}% · "
                 f"win/loss {f.get('win_loss_ratio')} · t {f.get('t_stat')} | OOS avg trade {o.get('avg_trade_pct')}% "
                 f"avg loss {o.get('avg_loss_pct')}% (n {o.get('n')})")
    L += ["", "## EXP B Multi-asset trend following (2007+)"]
    L += [line(k, v) for k, v in res.get("expB_trend", {}).items()]
    L += ["", "## EXP C Stock selection variants (same universe)"]
    L += [line(k, v) for k, v in res.get("expC_selection", {}).items()]
    L += ["", "## EXP D Equity momentum + trend, combined"]
    for k, v in res.get("expD_combination", {}).items():
        L.append(line(k, v) if isinstance(v, dict) else f"- {k}: {v}")
    L += ["", "## EXP F Conditions-score sizing on the momentum book"]
    for k, v in res.get("expF_conditions", {}).items():
        L.append(line(k, v) if isinstance(v, dict) and "full" in v else f"- {k}: {v}")
    L += ["", "## EXP E Risk dial on the combination (up to 2x, 5%/yr financing)"]
    L += [line(k, v) for k, v in res.get("expE_risk_dial", {}).items()]
    errs = [k for k in res if k.endswith("_error")]
    if errs: L += ["", "## Errors"] + [f"- {k}: {res[k]}" for k in errs]
    open("research/output/EXPERIMENTS_R4.md", "w").write("\n".join(L))
    log("done")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        os.makedirs("research/output", exist_ok=True)
        open("research/output/EXPERIMENTS_R4_ERROR.txt", "w").write(traceback.format_exc()); log(f"FATAL {e}"); sys.exit(1)
