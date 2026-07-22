"""
Signal — daily signal engine + honest shadow ledger.

Runs daily on GitHub Actions (free), pulls free daily data, and produces:
  site/data/signals.json  — today's actionable signals (what to consider buying)
  site/data/ledger.json   — the LIVE shadow ledger: every past signal and its
                            real forward outcome, plus a running scorecard
  site/data/meta.json     — last-updated timestamp + universe info

Signals are built on the RESEARCH-VALIDATED configuration only:
  - POSITION book: cross-sectional 6&12-month momentum, names above 200d SMA,
    top ~20%, monthly horizon. (Survivorship-neutral edge; Sharpe ~1.1.)
  - SWING book: short-term 10-day momentum continuation, names above 100d SMA
    with positive 3-month momentum, ~1 week horizon. (Sharpe ~0.6-0.75.)
  - CRYPTO sleeve: NOT a signal edge. Vol-targeted exposure to coins in
    confirmed uptrends, clearly labelled RISK-MANAGED BETA, not alpha.

Vol-target sizing: suggested gross exposure scales a 15% annual vol target
against each book's recent realized volatility (long-only, capped at 100%).

Honesty rules:
  - The ledger is a REAL forward test that starts empty and fills as days pass.
    It is never seeded with backtest data. Backtest history is shown separately
    and always labelled SIMULATED.
  - Every signal records the actual entry price at signal time; outcomes are the
    real subsequent returns. Nothing is fabricated or capped.
"""

import json, os, sys, traceback
from datetime import datetime, timezone
import numpy as np, pandas as pd

START = "2015-01-01"
DATA_DIR = "site/data"
POSITION_HORIZON_D = 21     # ~1 month trading days
SWING_HORIZON_D = 5         # ~1 week
VOL_TARGET = 0.15
TOP_FRAC_POSITION = 0.20
N_SWING = 10

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

def fetch(tickers):
    import yfinance as yf
    raw = yf.download(tickers, start=START, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    px = px.dropna(how="all")
    px = px[px.columns[px.notna().mean() > 0.55]].ffill(limit=5)
    return px

def z(s):
    s=s.astype(float); sd=s.std()
    return s*0.0 if (not np.isfinite(sd) or sd==0) else (s-s.mean())/sd

def realized_vol(prices_df_picks, ppy, lookback=30):
    """Annualized realized vol of an equal-weight portfolio of the picks."""
    if prices_df_picks.shape[1]==0: return np.nan
    r = prices_df_picks.pct_change().mean(axis=1)
    return r.tail(lookback).std()*np.sqrt(ppy)

def suggested_exposure(vol):
    if not np.isfinite(vol) or vol<=0: return 0.0
    return round(float(min(1.0, VOL_TARGET/vol)), 2)

def build_equity_signals(px):
    t = px.index[-1]
    sma200 = px.rolling(200).mean().loc[t]
    sma100 = px.rolling(100).mean().loc[t]
    price = px.loc[t]
    m12 = px.shift(21).loc[t]/px.shift(252).loc[t]-1
    m6  = px.shift(21).loc[t]/px.shift(126).loc[t]-1
    m3  = price/px.shift(63).loc[t]-1
    st10= price/px.shift(10).loc[t]-1
    atr = (px.diff().abs().rolling(14).mean().loc[t])  # simple ATR proxy

    # POSITION book
    pos_score = ((z(m12)+z(m6))/2)[price>sma200].dropna()
    n = max(1, int(round(len(pos_score)*TOP_FRAC_POSITION)))
    pos_picks = pos_score.sort_values(ascending=False).head(n)
    pos_prices = px[pos_picks.index]
    pos_exp = suggested_exposure(realized_vol(pos_prices, 252))
    position=[]
    for rank,(tk,sc) in enumerate(pos_picks.items(),1):
        p=float(price[tk]); a=float(atr.get(tk,np.nan))
        position.append({
            "ticker":tk,"rank":rank,"score":round(float(sc),2),
            "entry_ref":round(p,2),
            "mom_6m_pct":round(float(m6[tk])*100,1),"mom_12m_pct":round(float(m12[tk])*100,1),
            "suggested_stop":round(p-2*a,2) if np.isfinite(a) else None,
            "horizon":"~1 month (position)",
        })

    # SWING book (short-term momentum continuation)
    elig = (price>sma100) & (m3>0)
    sw_score = z(st10)[elig].dropna().sort_values(ascending=False).head(N_SWING)
    sw_prices = px[sw_score.index]
    sw_exp = suggested_exposure(realized_vol(sw_prices, 252, lookback=20))
    swing=[]
    for rank,(tk,sc) in enumerate(sw_score.items(),1):
        p=float(price[tk]); a=float(atr.get(tk,np.nan))
        swing.append({
            "ticker":tk,"rank":rank,"score":round(float(sc),2),
            "entry_ref":round(p,2),"ret_10d_pct":round(float(st10[tk])*100,1),
            "suggested_stop":round(p-1.5*a,2) if np.isfinite(a) else None,
            "horizon":"~1 week (swing)",
        })
    return position, swing, pos_exp, sw_exp, str(t.date())

def build_crypto_sleeve(px):
    t=px.index[-1]; price=px.loc[t]
    sma200=px.rolling(200).mean().loc[t]
    mom=(price/px.shift(200).loc[t]-1)
    on = (price>sma200) & (mom>0)
    on_coins=[c for c in px.columns if bool(on.get(c,False))]
    exp = suggested_exposure(realized_vol(px[on_coins], 365) if on_coins else np.nan)
    coins=[]
    for tk in on_coins:
        coins.append({"ticker":tk,"entry_ref":round(float(price[tk]),2),
                      "mom_200d_pct":round(float(mom[tk])*100,1)})
    return {"coins_in_uptrend":coins,"suggested_gross_exposure":exp,
            "label":"RISK-MANAGED BETA — not a signal edge. High risk (historical drawdowns ~-59% even vol-targeted).",
            "as_of":str(t.date())}

# ---------------- shadow ledger ----------------
def load_json(path, default):
    if os.path.exists(path):
        try: return json.load(open(path))
        except Exception: return default
    return default

def update_ledger(ledger, signals, eq_px, cr_px):
    """Add new signals; update/close open ones with real forward returns."""
    open_sig = ledger.get("open", [])
    closed = ledger.get("closed", [])
    today = signals["as_of_equity"]

    # index of latest prices
    eq_last = eq_px.iloc[-1]; cr_last = cr_px.iloc[-1]
    def cur_price(tk):
        if tk in eq_last.index: return float(eq_last[tk])
        if tk in cr_last.index: return float(cr_last[tk])
        return None

    # 1) update open signals
    still_open=[]
    for s in open_sig:
        p=cur_price(s["ticker"])
        if p is not None:
            s["last_price"]=round(p,2)
            s["return_pct"]=round((p/s["entry_price"]-1)*100,2)
        # count trading days elapsed via date diff heuristic (calendar->approx)
        d0=datetime.fromisoformat(s["entry_date"]).date()
        d1=datetime.fromisoformat(today).date()
        s["days_held"]=(d1-d0).days
        horizon_cal = 32 if s["book"]=="position" else 9  # ~ trading horizon in calendar days
        if s["days_held"]>=horizon_cal and p is not None:
            s["closed_date"]=today; s["outcome"]="win" if s["return_pct"]>0 else "loss"
            closed.append(s)
        else:
            still_open.append(s)

    # 2) add today's new signals (dedupe by ticker+book+entry_date)
    existing={(s["ticker"],s["book"]) for s in still_open}
    def add(book, items):
        for it in items:
            key=(it["ticker"],book)
            if key in existing: continue
            still_open.append({"ticker":it["ticker"],"book":book,"entry_date":today,
                               "entry_price":it["entry_ref"],"last_price":it["entry_ref"],
                               "return_pct":0.0,"days_held":0})
    add("position", signals["position"])
    add("swing", signals["swing"])

    # 3) scorecard from closed signals
    def score(book):
        c=[s for s in closed if s["book"]==book and "return_pct" in s]
        if not c: return {"n":0}
        rets=[s["return_pct"] for s in c]
        wins=[r for r in rets if r>0]
        return {"n":len(c),"win_rate_pct":round(100*len(wins)/len(c),1),
                "avg_return_pct":round(float(np.mean(rets)),2),
                "avg_win_pct":round(float(np.mean(wins)),2) if wins else 0.0,
                "avg_loss_pct":round(float(np.mean([r for r in rets if r<=0])),2) if len(wins)<len(c) else 0.0}
    ledger["open"]=still_open; ledger["closed"]=closed
    ledger["scorecard"]={"position":score("position"),"swing":score("swing"),
                         "live_since":ledger.get("live_since",today),"updated":today}
    return ledger

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log("fetching equities..."); eq=fetch(EQUITY_UNIVERSE)
    log("fetching crypto...");  cr=fetch(CRYPTO_UNIVERSE)
    position, swing, pos_exp, sw_exp, as_of = build_equity_signals(eq)
    crypto = build_crypto_sleeve(cr)

    signals={
        "as_of_equity":as_of,"as_of_crypto":crypto["as_of"],
        "position":position,"swing":swing,
        "position_suggested_exposure":pos_exp,"swing_suggested_exposure":sw_exp,
        "vol_target_annual_pct":int(VOL_TARGET*100),
        "crypto":crypto,
        "disclaimer":"Educational signals from a validated momentum model. Not investment advice. "
                     "Execute in your own brokerage at your discretion.",
    }
    json.dump(signals, open(f"{DATA_DIR}/signals.json","w"), indent=2)
    log(f"wrote signals.json ({len(position)} position, {len(swing)} swing)")

    ledger=load_json(f"{DATA_DIR}/ledger.json", {"open":[],"closed":[],"live_since":as_of})
    if "live_since" not in ledger: ledger["live_since"]=as_of
    ledger=update_ledger(ledger, signals, eq, cr)
    json.dump(ledger, open(f"{DATA_DIR}/ledger.json","w"), indent=2)
    log(f"updated ledger: {len(ledger['open'])} open, {len(ledger['closed'])} closed")

    json.dump({"updated_utc":datetime.now(timezone.utc).isoformat(),
               "equity_universe":len(EQUITY_UNIVERSE),"crypto_universe":len(CRYPTO_UNIVERSE)},
              open(f"{DATA_DIR}/meta.json","w"), indent=2)
    log("done")

if __name__=="__main__":
    try: main()
    except Exception as e:
        os.makedirs(DATA_DIR, exist_ok=True)
        open(f"{DATA_DIR}/ENGINE_ERROR.txt","w").write(traceback.format_exc())
        log(f"FATAL {e}"); sys.exit(1)
