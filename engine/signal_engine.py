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

Ledger rules v2 (stricter, closer to what a real account would get):
  - ENTRY at the NEXT trading day's open after the signal (a signal is computed
    after the close, so the signal-day close is not a price anyone could trade).
  - STOPS ENFORCED, sized by evidence (research round 4, EXP A):
    a tight 2-ATR stop cut the average position trade from +1.77% to +0.64%
    by cutting off winners, so it was removed. The position book now uses a
    WIDE 15% TRAILING stop (15% below the highest close since entry), which
    kept most of the return (+1.53%) and cut the worst trade from -34% to -21%.
    A stop is booked at the real close that breached it, never at the stop price.
  - SWING BOOK RETIRED (Oct 2026): +0.05% per signal before costs in live
    tracking. No new swing signals; open swing trades run off under their rules.
  - MARKET CONDITIONS are a READOUT ONLY. Sizing by the conditions score did
    not beat a constant smaller position (EXP F), so it no longer sets size.
  - COSTS: 10 bps charged on entry and again on exit.
  - BENCHMARK: every trade records SPY's return over the same window and the
    excess. Beating zero is not the test; beating SPY is.
  Trades opened under the old rules keep their original numbers and are tagged
  rules="v1"; the scorecard reports v2 trades separately under "strict".
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
COST_BPS_PER_SIDE = 10      # charged on entry and on exit of every ledger trade
BENCH = "SPY"
RULES = "v2"                # v2 = next-open entry, stops enforced, costs, SPY-relative
TRAIL_STOP_POSITION = 0.15  # position book: exit on a close 15% below the highest close since entry
SWING_ENABLED = False       # swing book retired Oct 2026 (flat before costs, negative after, in live tracking)

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

# Context tickers: used ONLY to read market conditions, never traded or ranked.
CONTEXT=["HYG","IEF"]
ETFS={"XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLC","XLRE","SPY","QQQ","IWM","MDY","DIA","GLD","EFA","EEM"}
# conditions score -> share of the account the playbook may put to work
EXPOSURE_BY_SCORE={5:1.00,4:0.75,3:0.50,2:0.25,1:0.0,0:0.0}
STANCE_BY_SCORE={5:"GO HEAVY",4:"NORMAL SIZE",3:"TREAD CAREFULLY",2:"DEFENSIVE",1:"STAND ASIDE",0:"STAND ASIDE"}

def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)

def fetch(tickers, with_open=False):
    import yfinance as yf
    raw = yf.download(tickers, start=START, progress=False, auto_adjust=True)
    px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    px = px.dropna(how="all")
    px = px[px.columns[px.notna().mean() > 0.55]].ffill(limit=5)
    if not with_open: return px
    try:
        op = raw["Open"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Open"]]
        op = op.reindex(index=px.index, columns=px.columns)
    except Exception:
        op = px*np.nan
    return px, op

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

def conditions_frame(px, ctx=None):
    """Daily market-conditions checklist, computed from prices only (no forecasts).

    Five yes/no checks, each using data available at that day's close:
      trend    SPY above its 200-day average AND that average is rising
      calm     SPY 20-day volatility no more than 1.25x its own 1-year median
      breadth  at least 55% of the individual stocks above their own 200-day average
      credit   high-yield bonds (HYG) outperforming Treasuries (IEF) vs their 50-day average
      room     SPY no more than 12% above its 200-day average (not stretched)
    A check with no data is left out and the score is scaled to the checks available.
    Returns a DataFrame: one bool column per check, plus score (0-5) and exposure (0-1).
    A failed TREND check caps exposure at 25% no matter what else passes.
    """
    out=pd.DataFrame(index=px.index)
    if "SPY" in px.columns:
        spy=px["SPY"]; sma=spy.rolling(200).mean()
        out["trend"]=(spy>sma)&(sma>sma.shift(21))
        rv=spy.pct_change().rolling(20).std()
        out["calm"]=rv<=1.25*rv.rolling(252).median()
        out["room"]=spy<=1.12*sma
        for c in ("trend","calm","room"): out[c]=out[c].where(sma.notna())
    stocks=[c for c in px.columns if c not in ETFS and c not in CONTEXT]
    if len(stocks)>=10:
        s200=px[stocks].rolling(200).mean()
        above=(px[stocks]>s200).where(s200.notna())
        out["breadth"]=(above.mean(axis=1)>=0.55).where(above.notna().sum(axis=1)>=10)
    if ctx is not None and all(c in ctx.columns for c in ("HYG","IEF")):
        ratio=(ctx["HYG"]/ctx["IEF"]).reindex(px.index).ffill(limit=5); r50=ratio.rolling(50).mean()
        out["credit"]=(ratio>r50).where(r50.notna())
    checks=[c for c in ("trend","calm","breadth","credit","room") if c in out.columns]
    if not checks:
        out["score"]=np.nan; out["exposure"]=np.nan; return out
    passed=out[checks].astype(float).sum(axis=1,min_count=1); avail=out[checks].notna().sum(axis=1)
    score=(passed/avail.replace(0,np.nan)*5).round()
    exp=score.map(EXPOSURE_BY_SCORE)
    if "trend" in out.columns:
        exp=exp.where(out["trend"].astype(float)!=0.0, np.minimum(exp,0.25))
    out["score"]=score; out["exposure"]=exp
    return out

CHECK_TEXT={
    "trend":("Trend","S&P 500 above a rising 200-day average","S&P 500 below its 200-day average, or the average is falling"),
    "calm":("Volatility","Daily swings are normal","Daily swings are well above normal"),
    "breadth":("Breadth","Most stocks are in uptrends","Fewer than 55% of stocks are in uptrends"),
    "credit":("Credit","High-yield bonds are holding up","High-yield bonds are weakening against Treasuries"),
    "room":("Stretch","Index is not overextended","Index is more than 12% above its 200-day average"),
}

def build_conditions(px, ctx=None):
    """Today's conditions + a one-line directive for the playbook."""
    f=conditions_frame(px, ctx)
    if f.empty or not np.isfinite(f["score"].iloc[-1]):
        return {"available":False,"sizing":False,"directive":"Market conditions could not be read today."}
    row=f.iloc[-1]; score=int(row["score"])
    checks=[]
    for k,(name,good,bad) in CHECK_TEXT.items():
        if k in f.columns and pd.notna(row[k]):
            ok=bool(row[k]); checks.append({"key":k,"name":name,"pass":ok,"text":good if ok else bad})
    n_pass=sum(c["pass"] for c in checks)
    same=(f["score"]==row["score"]).iloc[::-1]
    days=int(same.cumprod().sum())
    return {"available":True,"as_of":str(f.index[-1].date()),"score":score,"checks_passed":n_pass,
            "checks_total":len(checks),"sizing":False,
            "directive":f"{n_pass} of {len(checks)} market conditions are favorable.",
            "checks":checks,"days_at_score":days,
            "note":"Readout only: it does not set position size. In testing (2017 to now), sizing by this score "
                   "did no better than holding a constant smaller position, and low scores were not followed "
                   "by worse months. Size comes from the volatility target."}

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
            "suggested_stop":round(p*(1-TRAIL_STOP_POSITION),2),
            "stop_rule":f"{int(TRAIL_STOP_POSITION*100)}% trailing, from the highest close since entry",
            "horizon":"~1 month (position)",
        })

    # SWING book (short-term momentum continuation)
    elig = (price>sma100) & (m3>0)
    sw_score = z(st10)[elig].dropna().sort_values(ascending=False).head(N_SWING if SWING_ENABLED else 0)
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

def _px_on(df, tk, date):
    """Price for ticker on an exact date string, or None."""
    try:
        v=df.at[pd.Timestamp(date), tk]
        return float(v) if np.isfinite(v) else None
    except Exception:
        return None

def _bench_ret(eq_px, eq_op, entry_date, exit_date, from_open):
    """SPY % return over the trade's window (open->close for v2, close->close for v1)."""
    if BENCH not in eq_px.columns: return None
    p0=_px_on(eq_op if from_open else eq_px, BENCH, entry_date)
    if p0 is None: p0=_px_on(eq_px, BENCH, entry_date)
    p1=_px_on(eq_px, BENCH, exit_date)
    if p0 is None or p1 is None or p0<=0: return None
    return round((p1/p0-1)*100,2)

def update_ledger(ledger, signals, eq_px, cr_px, eq_op=None):
    """Add new signals; update/close open ones with real forward returns.

    v2 rules: next-open entry, enforced stops (booked at the real close),
    10 bps per side, and every trade measured against SPY over the same window.
    """
    if eq_op is None: eq_op = eq_px*np.nan
    open_sig = ledger.get("open", [])
    closed = ledger.get("closed", [])
    today = signals["as_of_equity"]
    cost_rt = 2*COST_BPS_PER_SIDE/100.0          # round-trip cost in % points
    dates = [str(d.date()) for d in eq_px.index]

    def series(tk):
        if tk in eq_px.columns: return eq_px[tk], True
        if tk in cr_px.columns: return cr_px[tk], False
        return None, False

    def close_out(s, exit_date, exit_px, reason):
        gross=(exit_px/s["entry_price"]-1)*100
        s["last_price"]=round(exit_px,2)
        s["return_pct"]=round(gross-(cost_rt if s.get("rules")==RULES else 0.0),2)
        s["days_held"]=(datetime.fromisoformat(exit_date).date()-datetime.fromisoformat(s["entry_date"]).date()).days
        s["closed_date"]=exit_date; s["exit_reason"]=reason
        s["outcome"]="win" if s["return_pct"]>0 else "loss"
        b=_bench_ret(eq_px, eq_op, s["entry_date"], exit_date, s.get("rules")==RULES)
        if b is not None:
            s["spy_return_pct"]=b; s["excess_pct"]=round(s["return_pct"]-b,2)
        s.pop("pending",None)
        closed.append(s)

    # 0) legacy (v1) open trades never had their stop stored. Rebuild the stop that
    #    was published with the signal (entry - k*ATR as of the entry date) and
    #    enforce it from this run forward - history is not rewritten.
    atr_all = eq_px.diff().abs().rolling(14).mean()
    for s in open_sig:
        if "rules" in s or "stop" in s: continue
        s["rules"]="v1"
        a=_px_on(atr_all, s["ticker"], s["entry_date"])
        if a is not None:
            s["stop"]=round(s["entry_price"]-(2.0 if s["book"]=="position" else 1.5)*a,2)
            s["last_checked"]=today

    # 1) update open signals
    still_open=[]
    for s in open_sig:
        ser, is_eq = series(s["ticker"])
        if ser is None: still_open.append(s); continue

        # 1a) v2 trades waiting for their real entry: fill at the first open after the signal
        if s.get("pending"):
            sig_date=s.get("signal_date", s["entry_date"])
            nxt=[d for d in dates if d>sig_date] if is_eq else []
            filled=False
            for d in nxt:
                o=_px_on(eq_op, s["ticker"], d)
                if o is None: o=_px_on(eq_px, s["ticker"], d)   # no open published: fall back to that day's close
                if o is not None and o>0:
                    s["entry_date"]=d; s["entry_price"]=round(o,2); s.pop("pending"); filled=True
                    break
            if not filled:
                still_open.append(s); continue

        # 1b) walk every close since the last check: first close <= stop exits there
        horizon_cal = 32 if s["book"]=="position" else 9  # ~ trading horizon in calendar days
        d0=datetime.fromisoformat(s["entry_date"]).date()
        start=s.get("last_checked", s["entry_date"])
        stop=s.get("stop")
        trailing = s["book"]=="position"
        if trailing and "peak" not in s:
            # highest close from entry up to the last day already checked (no hindsight beyond it)
            hist=ser.loc[pd.Timestamp(s["entry_date"]):pd.Timestamp(start)].dropna()
            s["peak"]=round(float(max([s["entry_price"]]+list(hist.values))),2)
            s["stop_rule"]="trail15"
        done=False
        for d in [x for x in dates if x>=s["entry_date"] and x>=start]:
            p=_px_on(ser.to_frame(s["ticker"]), s["ticker"], d)
            if p is None: continue
            held=(datetime.fromisoformat(d).date()-d0).days
            if trailing:
                s["peak"]=round(max(s["peak"],p),2)
                stop=round(s["peak"]*(1-TRAIL_STOP_POSITION),2); s["stop"]=stop
            if stop is not None and p<=stop:
                close_out(s, d, p, "stop"); done=True; break
            if held>=horizon_cal:
                close_out(s, d, p, "time"); done=True; break
            s["last_price"]=round(p,2)
            s["return_pct"]=round((p/s["entry_price"]-1)*100,2)
            s["days_held"]=held
        if not done:
            s["last_checked"]=today
            still_open.append(s)

    # 2) add today's new signals (dedupe by ticker+book). They are PENDING until
    #    the next session's open prints; entry_price shown meanwhile is the signal close.
    existing={(s["ticker"],s["book"]) for s in still_open}
    def add(book, items):
        for it in items:
            key=(it["ticker"],book)
            if key in existing: continue
            still_open.append({"ticker":it["ticker"],"book":book,"entry_date":today,
                               "signal_date":today,"signal_price":it["entry_ref"],
                               "entry_price":it["entry_ref"],"last_price":it["entry_ref"],
                               "stop":it.get("suggested_stop"),
                               "stop_rule":"trail15" if book=="position" else "atr1.5",
                               "return_pct":0.0,"days_held":0,"pending":True,"rules":RULES})
    add("position", signals["position"])
    add("swing", signals["swing"])

    # 2b) one-time backfill: tag legacy trades v1 and attach their SPY comparison
    for s in closed:
        s.setdefault("rules","v1"); s.setdefault("exit_reason","time")
        if "spy_return_pct" not in s and "closed_date" in s:
            b=_bench_ret(eq_px, eq_op, s["entry_date"], s["closed_date"], False)
            if b is not None:
                s["spy_return_pct"]=b; s["excess_pct"]=round(s["return_pct"]-b,2)
    for s in still_open: s.setdefault("rules","v1")

    # 3) scorecard from closed signals
    def score(book, rules=None):
        c=[s for s in closed if s["book"]==book and "return_pct" in s and (rules is None or s.get("rules")==rules)]
        if not c: return {"n":0}
        rets=[s["return_pct"] for s in c]
        wins=[r for r in rets if r>0]; losses=[r for r in rets if r<=0]
        ex=[s["excess_pct"] for s in c if "excess_pct" in s]
        out={"n":len(c),"win_rate_pct":round(100*len(wins)/len(c),1),
             "avg_return_pct":round(float(np.mean(rets)),2),
             "avg_win_pct":round(float(np.mean(wins)),2) if wins else 0.0,
             "avg_loss_pct":round(float(np.mean(losses)),2) if losses else 0.0,
             "worst_loss_pct":round(float(min(rets)),2),
             "stopped_out":sum(1 for s in c if s.get("exit_reason")=="stop")}
        if wins and losses and np.mean(losses)<0:
            out["win_loss_ratio"]=round(float(np.mean(wins)/-np.mean(losses)),2)
        if ex:
            out["avg_spy_return_pct"]=round(float(np.mean([s["spy_return_pct"] for s in c if "spy_return_pct" in s])),2)
            out["avg_excess_vs_spy_pct"]=round(float(np.mean(ex)),2)
            out["beat_spy_pct"]=round(100*sum(1 for e in ex if e>0)/len(ex),1)
        return out
    ledger["open"]=still_open; ledger["closed"]=closed
    ledger.setdefault("rules_v2_since", today)
    ledger["scorecard"]={"position":score("position"),"swing":score("swing"),
                         "strict":{"position":score("position",RULES),"swing":score("swing",RULES),
                                   "since":ledger["rules_v2_since"],
                                   "rules":"next-open entry · stops enforced at the real close · 10 bps per side · vs SPY"},
                         "live_since":ledger.get("live_since",today),"updated":today}
    return ledger

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log("fetching equities..."); eq,eq_op=fetch(EQUITY_UNIVERSE+CONTEXT, with_open=True)
    ctx=eq[[c for c in CONTEXT if c in eq.columns]]
    eq=eq.drop(columns=[c for c in CONTEXT if c in eq.columns]); eq_op=eq_op.reindex(columns=eq.columns)
    log("fetching crypto...");  cr=fetch(CRYPTO_UNIVERSE)
    position, swing, pos_exp, sw_exp, as_of = build_equity_signals(eq)
    crypto = build_crypto_sleeve(cr)
    try: conditions = build_conditions(eq, ctx)
    except Exception as e:
        log(f"conditions failed: {e}")
        conditions = {"available":False,"sizing":False,"directive":"Market conditions could not be read today."}

    signals={
        "as_of_equity":as_of,"as_of_crypto":crypto["as_of"],
        "conditions":conditions,
        "position":position,"swing":swing,
        "position_suggested_exposure":pos_exp,"swing_suggested_exposure":sw_exp if SWING_ENABLED else 0.0,
        "swing_retired":(not SWING_ENABLED),
        "vol_target_annual_pct":int(VOL_TARGET*100),
        "crypto":crypto,
        "disclaimer":"Educational signals from a validated momentum model. Not investment advice. "
                     "Execute in your own brokerage at your discretion.",
    }
    json.dump(signals, open(f"{DATA_DIR}/signals.json","w"), indent=2)
    log(f"wrote signals.json ({len(position)} position, {len(swing)} swing)")

    ledger=load_json(f"{DATA_DIR}/ledger.json", {"open":[],"closed":[],"live_since":as_of})
    if "live_since" not in ledger: ledger["live_since"]=as_of
    ledger=update_ledger(ledger, signals, eq, cr, eq_op)
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
