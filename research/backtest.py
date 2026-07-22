"""
Signal — cross-sectional multi-factor backtest (honest v1).

Goal of this script: find out whether a legitimate, institutional-style
cross-sectional ranking approach has a real edge on free daily data — BEFORE
we build any product around it. Nothing here is cosmetic. Every number is
computed from real price data with no look-ahead, real transaction costs, and
an explicit in-sample vs out-of-sample split so we can see overfitting.

Two strategies, two horizons:
  A. POSITION  (1 week – 1 quarter): cross-sectional momentum + trend filter,
     monthly rebalance. The classic "winners keep winning" effect.
  B. SWING     (3 – 7 days): short-term mean-reversion (buy oversold names that
     are still in an uptrend), weekly rebalance.

Run separately for US equities/ETFs and for crypto (different calendars).

Honesty rules enforced in code:
  - Signals at date t use ONLY data up to and including t; positions are held
    over t+1..t+h. No same-bar look-ahead.
  - Transaction costs charged on turnover each rebalance.
  - Report IN-SAMPLE (first 65%) and OUT-OF-SAMPLE (last 35%) separately.
    An edge that only shows up in-sample is not an edge.
  - Benchmarks: SPY buy&hold and equal-weight-universe buy&hold.
"""

import json
import sys
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Universes
# ----------------------------------------------------------------------------
EQUITY_UNIVERSE = [
    # Mega/large-cap liquid names across sectors
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "LLY", "MRK", "PFE",
    "XOM", "CVX", "COP", "CAT", "DE", "HON", "GE", "BA", "LMT", "UPS",
    "WMT", "COST", "HD", "LOW", "MCD", "SBUX", "NKE", "PG", "KO", "PEP",
    "DIS", "CMCSA", "T", "VZ", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "TXN",
    # Sector / broad ETFs for breadth
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC",
    "SPY", "QQQ", "IWM", "DIA", "GLD",
]
CRYPTO_UNIVERSE = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "ADA-USD",
    "AVAX-USD", "DOGE-USD", "LINK-USD", "DOT-USD", "MATIC-USD", "LTC-USD",
]

BENCHMARK = "SPY"
START = "2017-01-01"
COST_BPS = 10.0          # per-side transaction cost in basis points
IS_FRAC = 0.65           # in-sample fraction (chronological)
TRADING_DAYS = 252


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_prices(tickers, start=START):
    """Download daily adjusted close. Returns a clean price DataFrame."""
    import yfinance as yf
    log(f"Downloading {len(tickers)} tickers since {start} ...")
    raw = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        px = raw["Close"].copy()
    else:  # single ticker edge case
        px = raw[["Close"]].copy()
        px.columns = tickers
    px = px.dropna(how="all")
    # Require a reasonable history; drop names with too much missing data
    good = px.columns[px.notna().mean() > 0.60]
    px = px[good].ffill(limit=5)
    log(f"Usable tickers: {len(px.columns)}  rows: {len(px)}  "
        f"range: {px.index.min().date()}..{px.index.max().date()}")
    return px


def perf_stats(equity, periods_per_year=TRADING_DAYS):
    """Compute honest performance stats from an equity curve (Series)."""
    equity = equity.dropna()
    if len(equity) < 5:
        return {}
    rets = equity.pct_change().dropna()
    total = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = len(equity) / periods_per_year
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0 if years > 0 else np.nan
    vol = rets.std() * np.sqrt(periods_per_year)
    sharpe = (rets.mean() * periods_per_year) / vol if vol > 0 else np.nan
    downside = rets[rets < 0].std() * np.sqrt(periods_per_year)
    sortino = (rets.mean() * periods_per_year) / downside if downside > 0 else np.nan
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    max_dd = dd.min()
    return {
        "total_return_pct": round(total * 100, 1),
        "cagr_pct": round(cagr * 100, 1),
        "vol_pct": round(vol * 100, 1),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 1),
        "pct_positive_days": round((rets > 0).mean() * 100, 1),
    }


def run_strategy(px, kind, rebal_days, top_frac=0.25, cost_bps=COST_BPS):
    """
    Generic cross-sectional backtest.

    kind == "position": score = blend of 12-1m and 6-1m momentum, long only
            names above their 200d SMA (trend filter). Hold top_frac.
    kind == "swing":    among names above 100d SMA with positive 3m momentum,
            score = short-term reversal (negative of 5d return). Buy the most
            oversold. Hold top_frac.

    Positions formed on rebalance date t from data <= t, held until next rebal.
    Returns a daily equity curve (Series).
    """
    rets = px.pct_change()
    sma200 = px.rolling(200).mean()
    sma100 = px.rolling(100).mean()
    mom_12_1 = px.shift(21) / px.shift(252) - 1.0     # 12m ago -> 1m ago
    mom_6_1 = px.shift(21) / px.shift(126) - 1.0      # 6m ago -> 1m ago
    mom_3m = px / px.shift(63) - 1.0
    ret_5d = px / px.shift(5) - 1.0

    dates = px.index
    rebal_idx = list(range(252, len(dates), rebal_days))  # warm-up 252 days
    if not rebal_idx:
        return pd.Series(dtype=float)

    weights = pd.DataFrame(0.0, index=dates, columns=px.columns)
    for i in rebal_idx:
        t = dates[i]
        if kind == "position":
            z1 = _zscore(mom_12_1.loc[t])
            z2 = _zscore(mom_6_1.loc[t])
            score = (z1 + z2) / 2.0
            eligible = px.loc[t] > sma200.loc[t]        # trend filter
        else:  # swing
            score = -_zscore(ret_5d.loc[t])             # oversold = high score
            eligible = (px.loc[t] > sma100.loc[t]) & (mom_3m.loc[t] > 0)
        score = score[eligible.reindex(score.index).fillna(False)]
        score = score.dropna()
        if len(score) < 3:
            continue
        n = max(1, int(round(len(score) * top_frac)))
        picks = score.sort_values(ascending=False).head(n).index
        w = pd.Series(0.0, index=px.columns)
        w[picks] = 1.0 / n
        # hold until next rebalance
        j = min(i + rebal_days, len(dates))
        weights.iloc[i:j] = w.values

    # daily portfolio return: yesterday's weights applied to today's return
    port_ret = (weights.shift(1) * rets).sum(axis=1)
    # transaction costs on turnover at each weight change
    turnover = (weights - weights.shift(1)).abs().sum(axis=1)
    port_ret = port_ret - turnover * (cost_bps / 1e4)
    port_ret = port_ret.loc[dates[rebal_idx[0]]:]
    equity = (1 + port_ret.fillna(0)).cumprod()
    return equity


def _zscore(s):
    s = s.astype(float)
    mu, sd = s.mean(), s.std()
    if not np.isfinite(sd) or sd == 0:
        return s * 0.0
    return (s - mu) / sd


def split_stats(equity, ppy=TRADING_DAYS):
    """In-sample vs out-of-sample split stats."""
    equity = equity.dropna()
    if len(equity) < 30:
        return {"error": "insufficient data"}
    cut = int(len(equity) * IS_FRAC)
    is_eq = equity.iloc[:cut] / equity.iloc[0]
    oos_eq = equity.iloc[cut:] / equity.iloc[cut]
    return {
        "full": perf_stats(equity / equity.iloc[0], ppy),
        "in_sample": perf_stats(is_eq, ppy),
        "out_of_sample": perf_stats(oos_eq, ppy),
        "split_date": str(equity.index[cut].date()),
    }


def benchmark_equity(px, tickers):
    """Equal-weight buy&hold of a set of tickers (rebalanced daily to EW)."""
    sub = px[[c for c in tickers if c in px.columns]].dropna(how="all")
    rets = sub.pct_change().mean(axis=1)
    return (1 + rets.fillna(0)).cumprod()


def run_market(name, universe, ppy):
    out = {"market": name}
    try:
        px = fetch_prices(universe)
        if px.shape[1] < 5:
            return {"market": name, "error": "not enough data"}
        # Strategies
        pos = run_strategy(px, "position", rebal_days=21)   # monthly
        swing = run_strategy(px, "swing", rebal_days=5)     # weekly
        out["position_strategy"] = split_stats(pos, ppy)
        out["swing_strategy"] = split_stats(swing, ppy)
        # Benchmarks aligned to the strategy window
        ew = benchmark_equity(px, list(px.columns))
        out["benchmark_equal_weight"] = perf_stats(ew / ew.iloc[0], ppy)
        if BENCHMARK in px.columns:
            spy = px[BENCHMARK].dropna()
            out["benchmark_SPY"] = perf_stats(spy / spy.iloc[0], ppy)
        out["universe_size"] = int(px.shape[1])
        out["history_start"] = str(px.index.min().date())
        out["history_end"] = str(px.index.max().date())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
    return out


def main():
    results = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "params": {
            "start": START, "cost_bps": COST_BPS, "in_sample_frac": IS_FRAC,
            "top_frac": 0.25,
        },
        "notes": [
            "No look-ahead: signals use data <= t, held t+1 onward.",
            "Costs charged on turnover. IS/OOS split is chronological.",
            "This is methodology validation, not a live trading recommendation.",
        ],
    }
    # Equities use a 252-day year; crypto trades ~365 days/yr.
    results["equities"] = run_market("US equities/ETFs", EQUITY_UNIVERSE, 252)
    results["crypto"] = run_market("Major crypto", CRYPTO_UNIVERSE, 365)

    with open("research/output/results.json", "w") as f:
        json.dump(results, f, indent=2)
    log("Wrote research/output/results.json")

    # Human-readable summary
    lines = ["# Backtest results", "",
             f"_Generated {results['generated_utc']}_", ""]
    for mkt_key in ("equities", "crypto"):
        m = results[mkt_key]
        lines.append(f"## {m.get('market', mkt_key)}")
        if "error" in m:
            lines.append(f"ERROR: {m['error']}")
            lines.append("")
            continue
        lines.append(f"Universe: {m.get('universe_size')} names · "
                     f"{m.get('history_start')} → {m.get('history_end')}")
        for strat in ("position_strategy", "swing_strategy"):
            s = m.get(strat, {})
            if "error" in s:
                lines.append(f"- {strat}: {s['error']}")
                continue
            full = s.get("full", {})
            oos = s.get("out_of_sample", {})
            lines.append(f"- **{strat}** (full): CAGR {full.get('cagr_pct')}%, "
                         f"Sharpe {full.get('sharpe')}, maxDD {full.get('max_drawdown_pct')}%")
            lines.append(f"    out-of-sample (since {s.get('split_date')}): "
                         f"CAGR {oos.get('cagr_pct')}%, Sharpe {oos.get('sharpe')}, "
                         f"maxDD {oos.get('max_drawdown_pct')}%")
        bh = m.get("benchmark_SPY") or m.get("benchmark_equal_weight", {})
        lines.append(f"- benchmark: CAGR {bh.get('cagr_pct')}%, Sharpe {bh.get('sharpe')}, "
                     f"maxDD {bh.get('max_drawdown_pct')}%")
        lines.append("")
    with open("research/output/SUMMARY.md", "w") as f:
        f.write("\n".join(lines))
    log("Wrote research/output/SUMMARY.md")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Always leave a trace so results can be read back over git
        with open("research/output/ERROR.txt", "w") as f:
            f.write(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        log(f"FATAL: {e}")
        sys.exit(1)
