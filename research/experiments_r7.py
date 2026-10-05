"""
Signal — research round 7: a thematic stock list.

Question: if the stock sleeve is restricted to six themes - space, defense, deep-sea
and critical-minerals mining, energy (nuclear, power, grid), AI and chip makers, and
magnets / rare earths - does momentum inside that list do better than momentum on the
current broad list?

What is tested:
  theme_momentum_top10 / top20   monthly momentum on the thematic list
  theme_equal_weight             every thematic stock held equally (no skill, just the themes)
  broad_momentum_top10           the same rule on the current broad list (round 6's stock sleeve)
  theme_mix_70_30                70% thematic momentum top 10% + 30% Bitcoin trend
  spy_buy_hold
  plus each theme held equally, to show which themes carried the result

READ THIS BEFORE THE NUMBERS. These themes were chosen in late 2026 because they are
the ones that have already gone up. Any backtest of "today's hot themes" is flattered
by that choice, and "theme_equal_weight" measures the size of the flattery: whatever
it earned above the S&P 500 came from knowing in advance which themes would run, not
from the strategy. Many of these companies also listed only recently, so the early
years rest on a handful of older defense and chip names.

BAR (set in advance) for moving the tracked aggressive mix onto the thematic list:
  thematic momentum top 10% must beat broad momentum top 10% on out-of-sample Sharpe
  AND its full-history worst drawdown must be no more than 10 points deeper.
"""

import json, os, sys, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experiments_r3 import EQUITY_UNIVERSE, IS_FRAC, COST_BPS, log, z
import experiments_r5 as R5
import experiments_r6 as R6

START = "2017-01-01"
THEMES = {
    "space":   ["RKLB", "ASTS", "PL", "LUNR", "RDW", "BKSY", "SPIR", "IRDM", "FLY", "VOYG", "KRMN", "VSAT"],
    "defense": ["LMT", "RTX", "NOC", "GD", "LHX", "HII", "KTOS", "AVAV", "PLTR", "LDOS", "CW", "TDG", "HWM", "MRCY", "DRS", "RCAT", "BBAI"],
    "mining":  ["TMC", "OMEX", "FCX", "ALB", "SQM", "CCJ", "UEC", "LEU"],
    "energy":  ["CEG", "VST", "TLN", "OKLO", "SMR", "NNE", "BWXT", "GEV", "ETN", "PWR", "VRT", "BE", "FSLR", "XOM", "CVX", "NEE"],
    "ai_chips": ["NVDA", "AMD", "AVGO", "TSM", "ASML", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ARM", "INTC", "QCOM", "TXN",
                 "SMCI", "ANET", "CRWV", "ALAB", "CRDO", "COHR", "DELL", "MSFT", "GOOGL", "META", "AMZN", "ORCL"],
    "magnets": ["MP", "USAR", "UUUU", "NB", "CRML", "REMX"],
}
ALL = sorted({t for v in THEMES.values() for t in v})

def fetch(tickers):
    """No coverage filter: recent listings are kept and simply start late."""
    import yfinance as yf
    raw = yf.download(tickers + ["SPY"], start=START, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    px = px.dropna(how="all").dropna(axis=1, how="all")
    return px.ffill(limit=5)

def momentum(px, top_frac, rebal=21, min_names=8):
    """Monthly momentum, tolerant of stocks that start mid-history (they join once they have a year of data)."""
    rets = px.pct_change(fill_method=None); sma200 = px.rolling(200).mean(); dates = px.index
    W = pd.DataFrame(0.0, index=dates, columns=px.columns); picks_log = {}
    idx = list(range(252, len(dates), rebal))
    for i in idx:
        t = dates[i]
        m12 = px.shift(21).loc[t] / px.shift(252).loc[t] - 1
        m6 = px.shift(21).loc[t] / px.shift(126).loc[t] - 1
        ok = m12.notna() & m6.notna() & sma200.loc[t].notna()
        score = ((z(m12[ok]) + z(m6[ok])) / 2)[px.loc[t][ok] > sma200.loc[t][ok]].dropna()
        if ok.sum() < min_names or len(score) < 2: continue
        n = max(2, int(round(ok.sum() * top_frac)))
        picks = score.sort_values(ascending=False).head(n).index
        w = pd.Series(0.0, index=px.columns); w[picks] = 1.0 / len(picks)
        W.iloc[i:min(i + rebal, len(dates))] = w.values; picks_log[str(t.date())] = list(picks)
    pr = (W.shift(1) * rets.fillna(0.0)).sum(axis=1) - (W - W.shift(1)).abs().sum(axis=1) * (COST_BPS / 1e4)
    return pr.loc[dates[idx[0]]:], picks_log

def main():
    os.makedirs("research/output", exist_ok=True)
    res = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "bar": "thematic momentum top 10% must beat broad momentum top 10% on OOS Sharpe AND full-history max drawdown no more than 10 points deeper"}
    try:
        tpx = fetch(ALL); spy_px = tpx["SPY"]; tpx = tpx[[c for c in tpx.columns if c in ALL]]
        bpx = R6.fetch_eq(EQUITY_UNIVERSE, START)
        loaded = {t: str(tpx[t].first_valid_index().date()) for t in tpx.columns}
        res["tickers_loaded"] = loaded; res["tickers_missing"] = [t for t in ALL if t not in tpx.columns]
        res["names_with_a_year_of_data_by_year"] = {str(y): int((tpx.loc[:f"{y}-01-01"].notna().sum() >= 252).sum()) for y in range(2018, 2027)}
        btc = R5.fetch(["BTC-USD"], "2014-09-01")["BTC-USD"].dropna()
        btc_net, _ = R5.run_rule(btc, R5.exposures(btc)["sma_150"])

        t10, picks10 = momentum(tpx, 0.10); t20, _ = momentum(tpx, 0.20)
        b10, _ = momentum(bpx, 0.10)
        tew = tpx.pct_change(fill_method=None).mean(axis=1).loc[t10.index[0]:]
        spy = spy_px.pct_change().loc[t10.index[0]:]
        cal = pd.date_range(t10.index[0], min(t10.index[-1], btc_net.index[-1]), freq="D")
        C = lambda r: R6.to_calendar(r, cal)
        P = {"theme_momentum_top10": C(t10), "theme_momentum_top20": C(t20), "theme_equal_weight": C(tew),
             "broad_momentum_top10": C(b10), "spy_buy_hold": C(spy),
             "theme_mix_70_30": R6.blend([C(t10), btc_net.reindex(cal).fillna(0.0)], (0.7, 0.3)),
             "broad_mix_70_30": R6.blend([C(b10), btc_net.reindex(cal).fillna(0.0)], (0.7, 0.3))}
        cut = cal[int(len(cal) * IS_FRAC)]
        out = {k: {"full": R6.stats(r), "out_sample": R6.stats(r.loc[cut:]), "yearly_pct": R6.yearly(r)} for k, r in P.items()}
        th = {}
        for name, tks in THEMES.items():
            cols = [t for t in tks if t in tpx.columns]
            r = C(tpx[cols].pct_change(fill_method=None).mean(axis=1).loc[t10.index[0]:])
            th[name] = {"names": len(cols), "full": R6.stats(r), "out_sample": R6.stats(r.loc[cut:]), "yearly_pct": R6.yearly(r)}
        a, b = out["theme_momentum_top10"], out["broad_momentum_top10"]
        try: passes = bool(a["out_sample"]["sharpe"] > b["out_sample"]["sharpe"] and a["full"]["maxDD_pct"] >= b["full"]["maxDD_pct"] - 10)
        except Exception: passes = False
        last = sorted(picks10)[-1] if picks10 else None
        res.update({"period": f"{cal[0].date()} to {cal[-1].date()}", "out_sample_from": str(cut.date()), "portfolios": out, "themes": th,
                    "passes_bar": passes, "latest_picks": {"date": last, "tickers": picks10.get(last)} if last else None,
                    "pick_counts": pd.Series([t for v in picks10.values() for t in v]).value_counts().head(15).to_dict()})
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"; res["tb"] = traceback.format_exc()
    json.dump(res, open("research/output/experiments_r7.json", "w"), indent=2)

    L = ["# Research round 7 — a thematic stock list", "", f"_Generated {res['generated_utc']}_", "", f"Bar (set in advance): {res['bar']}.", ""]
    if "portfolios" in res:
        L += [f"Period {res['period']} · out-of-sample from {res['out_sample_from']}",
              f"Loaded {len(res['tickers_loaded'])} of {len(ALL)} tickers · missing: {', '.join(res['tickers_missing']) or 'none'}",
              f"Thematic names with a year of history, by year: {res['names_with_a_year_of_data_by_year']}", ""]
        for k, v in res["portfolios"].items():
            f, o = v["full"], v["out_sample"]
            L.append(f"- **{k}**: full {f.get('cagr_pct')}%/yr Sharpe {f.get('sharpe')} maxDD {f.get('maxDD_pct')}% worst-12m {f.get('worst_12m_pct')}% | "
                     f"OOS {o.get('cagr_pct')}%/yr Sharpe {o.get('sharpe')} maxDD {o.get('maxDD_pct')}%")
        L += ["", "Each theme held equally:"]
        for k, v in res["themes"].items():
            f, o = v["full"], v["out_sample"]
            L.append(f"- **{k}** ({v['names']} names): full {f.get('cagr_pct')}%/yr maxDD {f.get('maxDD_pct')}% | OOS {o.get('cagr_pct')}%/yr maxDD {o.get('maxDD_pct')}%")
        L += ["", "Yearly returns (%):"]
        keys = ["spy_buy_hold", "theme_equal_weight", "theme_momentum_top10", "broad_momentum_top10", "theme_mix_70_30"]
        for y in res["portfolios"]["spy_buy_hold"]["yearly_pct"]:
            L.append(f"- {y}: " + " · ".join(f"{k} {res['portfolios'][k]['yearly_pct'].get(y)}" for k in keys))
        L += ["", f"Passes the bar: {res['passes_bar']}", f"Latest thematic picks: {res['latest_picks']}",
              f"Most-picked names: {res['pick_counts']}"]
    else:
        L += ["## Error", res.get("error", "unknown"), "", "```", res.get("tb", ""), "```"]
    open("research/output/EXPERIMENTS_R7.md", "w").write("\n".join(L))
    log("done")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        os.makedirs("research/output", exist_ok=True)
        open("research/output/EXPERIMENTS_R7_ERROR.txt", "w").write(traceback.format_exc()); log(f"FATAL {e}"); sys.exit(1)
