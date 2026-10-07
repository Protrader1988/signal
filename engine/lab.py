"""
Strategy lab — twelve books, run live, allocated by what is actually working.

THE PROBLEM THIS SOLVES
A system that tries strategies until one looks good is a machine for finding
noise. Run enough variants against the same history and a winner always appears;
it then dies with real money. The defence is not cleverness, it is bookkeeping:

  1. A SMALL, FIXED hypothesis space. Twelve books, each defensible in a sentence,
     declared in lab_strategies.py. Adding a book is a deliberate act that raises
     the significance bar for every other book, and the page says by how much.
  2. EVERY BOOK IS LIVE FROM BIRTH. Positions are formed from data up to today's
     close and the return is realised from the NEXT session. A backtest is shown
     for context and labelled hindsight; it never promotes a book and it is never
     mixed into the live record.
  3. COSTS ARE CHARGED. Turnover times a spread assumption, every day. This is
     what kills the reversal book if it is going to die.
  4. ALLOCATION FOLLOWS LIVE RESULTS, not backtests: exponential weights over
     realised daily returns with a 60-day half-life, capped so no single book can
     take the account.
  5. KILL RULES ARE WRITTEN DOWN FIRST and enforced here, not by judgement after
     a bad month.

HOW LONG BEFORE ANY OF IT MEANS ANYTHING
t = Sharpe x sqrt(years). Twelve books is twelve tests, so the bar is roughly
z = 2.9 rather than 2.0, and a book with a true Sharpe of 1.0 needs about EIGHT
YEARS of live data to prove it. The page prints that number next to every book so
the record can never be mistaken for a verdict. The lab's first honest output is a
countdown, not a recommendation.

Output: site/data/lab.json
"""
import json
import math
import os
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import lab_strategies as strat
from signal_engine import EQUITY_UNIVERSE

BENCH = "SPY"
COST_BPS = 5.0          # one-way, per unit of turnover; charged every rebalance
HALF_LIFE = 60          # trading days, for the allocator's memory
MAX_WEIGHT = 0.30       # no book may hold more than this share of the account
WARMUP_DAYS = 20        # below this a book is "warming up" and gets no verdict
KILL_MIN_DAYS = 60      # no book is retired before it has had a fair run
KILL_TSTAT = -1.5       # ...and then only if it is losing with conviction
KILL_MAX_DD = 0.25      # or if it has drawn down this far, whichever comes first
HINDSIGHT_DAYS = 504    # ~2 years of context, labelled as hindsight, never promoted
STATE = "site/data/lab.json"


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
def load_panel(tickers, period="4y"):
    df = yf.download(sorted(set(tickers)), period=period, progress=False,
                     auto_adjust=True, threads=True)
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df
    close = close.dropna(axis=1, thresh=int(len(close) * 0.8)).ffill().dropna(how="all")
    return close


def context_for(panel, i, policy):
    """Market regime as of session i, plus the policy desk's own data. Everything
    here is computed from data at or before i."""
    ctx = {}
    spy = panel[BENCH] if BENCH in panel else None
    if spy is not None and i >= 200:
        ctx["spy_above_200"] = bool(spy.iloc[i] > spy.iloc[i - 200:i + 1].mean())
    else:
        ctx["spy_above_200"] = True
    stocks = [c for c in panel.columns if c not in strat.ETFS]
    if i >= 205:
        sub = panel[stocks]
        ma = sub.iloc[i - 200:i + 1].mean()
        breadth_now = float((sub.iloc[i] > ma).mean())
        ma5 = sub.iloc[i - 205:i - 4].mean()
        breadth_prev = float((sub.iloc[i - 5] > ma5).mean())
        ctx["breadth"] = round(breadth_now, 3)
        ctx["breadth_rising"] = breadth_now >= breadth_prev
    else:
        ctx["breadth"], ctx["breadth_rising"] = None, True
    if spy is not None and i >= 21:
        r = spy.iloc[i - 21:i + 1].pct_change().dropna()
        ctx["vol_regime"] = round(float(r.std() * np.sqrt(252) * 100), 1)
    ctx["federal_exposure"] = policy.get("federal_exposure", {})
    ctx["sector_flow"] = policy.get("sector_flow", {})
    return ctx


def read_policy():
    """The lab's two proprietary inputs, read from the policy desk's own output."""
    out = {"federal_exposure": {}, "sector_flow": {}}
    try:
        p = json.load(open("site/data/policy.json"))
        for e in p.get("exposure", []):
            fed = e.get("federal") or {}
            if e.get("ticker") and (fed.get("total") or 0) > 0:
                out["federal_exposure"][e["ticker"]] = float(fed["total"])
        for f in p.get("flow", []):
            if f.get("sector") and f.get("yoy_pct") is not None:
                out["sector_flow"][f["sector"]] = float(f["yoy_pct"])
    except Exception as e:
        log(f"policy inputs unavailable ({type(e).__name__}); the two policy books will sit flat")
    return out


def turnover(prev, new):
    keys = set(prev) | set(new)
    return sum(abs(new.get(k, 0.0) - prev.get(k, 0.0)) for k in keys)


def spearman(a, b):
    """Rank correlation without scipy."""
    if len(a) < 4:
        return None
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    sa, sb = ra.std(), rb.std()
    if not sa or not sb:
        return None
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


def measure(panel, i, weights, stock_cols):
    """Three readings of the same day, in ascending order of statistical power.

    ret     what a holder of this book actually made. The number that pays for
            dinner, and the slowest one to prove anything with, because most of its
            variance is the market moving.
    spread  the same book measured against the equal-weight universe. Removing the
            common factor cuts the daily noise by roughly three, which makes a real
            edge show up in a few hundred days instead of a few thousand.
    ic      rank correlation between what the book wanted to own and what actually
            went up, across the whole universe. One number per day, but built from
            ~90 comparisons, so it reads the SIGNAL rather than the portfolio and is
            the fastest honest read available."""
    if i + 1 >= len(panel):
        return 0.0, None, None
    nxt = (panel.iloc[i + 1] / panel.iloc[i] - 1.0)
    ret = float(sum(w * float(nxt.get(tk, 0.0)) for tk, w in weights.items()))
    cols = [c for c in stock_cols if c in nxt.index and nxt[c] == nxt[c]]
    if not cols:
        return ret, None, None
    uni = float(nxt[cols].mean())
    spread = ret - uni if weights else None
    ic = None
    if weights:
        want = [float(weights.get(c, 0.0)) for c in cols]
        if max(want) > 0:
            ic = spearman(want, [float(nxt[c]) for c in cols])
    return ret, spread, ic


def book_return(panel, i, weights):
    """Return from session i to i+1 for a set of target weights set at i."""
    if i + 1 >= len(panel) or not weights:
        return 0.0
    r = 0.0
    for tk, w in weights.items():
        if tk not in panel.columns:
            continue
        p0, p1 = panel[tk].iloc[i], panel[tk].iloc[i + 1]
        if p0 and p0 == p0 and p1 == p1:
            r += w * (p1 / p0 - 1.0)
    return float(r)


# ---------------------------------------------------------------------------
# statistics — the part that keeps this honest
# ---------------------------------------------------------------------------
def stats(returns, n_tests=1):
    """Statistics, or an honest refusal to produce them.

    A Sharpe ratio computed from seven days annualises a week of luck into a
    headline number — the first run of this lab printed 13.75 for a book that had
    simply not lost yet. Nothing annualised is published below the warm-up, and
    what is published instead is the number of days still required."""
    r = np.array([x for x in returns if x is not None], dtype=float)
    out = {"days": len(r)}
    if len(r) < 2:
        return out
    if len(r) < WARMUP_DAYS:
        eq = float(np.cumprod(1 + r)[-1])
        out["cum_pct"] = round((eq - 1) * 100, 2)
        out["needs_days"] = WARMUP_DAYS - len(r)
        out["too_early"] = True
        return out
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    out["cum_pct"] = round(float(eq[-1] - 1) * 100, 2)
    out["ann_pct"] = round((float(eq[-1]) ** (252 / len(r)) - 1) * 100, 2)
    sd = float(np.std(r, ddof=1))
    out["vol_pct"] = round(sd * np.sqrt(252) * 100, 2)
    out["sharpe"] = round(float(np.mean(r) / sd * np.sqrt(252)), 2) if sd > 0 else None
    out["max_dd_pct"] = round(float((eq / peak - 1).min()) * 100, 2)
    out["hit_pct"] = round(float((r > 0).mean()) * 100, 1)
    if out["sharpe"]:
        out["t_stat"] = round(out["sharpe"] * math.sqrt(len(r) / 252), 2)
        # how long until this Sharpe would clear a bar that accounts for the number
        # of books being compared at once
        z = 2.94 if n_tests > 1 else 1.96
        out["years_to_prove"] = round((z / abs(out["sharpe"])) ** 2, 1) if out["sharpe"] else None
        out["proven"] = bool(abs(out["t_stat"]) >= z and out["sharpe"] > 0)
    return out


def signal_stats(series, label, n_tests=13):
    """Statistics for a mean-zero-under-the-null series (spread, or IC), plus the
    number of trading days still required at the rate actually observed. This is
    the number that answers 'how long before I know', and it is computed from the
    data rather than asserted."""
    r = np.array([x for x in series if x is not None], dtype=float)
    out = {"days": len(r), "kind": label}
    if len(r) < 5:
        return out
    mu, sd = float(np.mean(r)), float(np.std(r, ddof=1))
    out["mean"] = round(mu, 5)
    out["t_stat"] = round(mu / (sd / math.sqrt(len(r))), 2) if sd > 0 else None
    out["hit_pct"] = round(float((r > 0).mean()) * 100, 1)
    z = 2.94 if n_tests > 1 else 1.96
    if len(r) < WARMUP_DAYS:
        # "2 days to proof" off a week of data is an extrapolation from noise; the
        # projection is withheld until there is enough sample to make one
        out["too_early"] = True
        return out
    if sd > 0 and mu != 0:
        need = int(round((z * sd / abs(mu)) ** 2))
        out["days_for_proof"] = need
        out["days_remaining"] = max(0, need - len(r))
        out["would_prove"] = mu > 0
        # at the rate observed so far this book would need longer than anyone will
        # wait; saying "beyond a decade" is more use than printing 297,883
        out["beyond_horizon"] = need > 2520
    return out


def allocator(live_returns, active, mature):
    """Exponential weights over live daily returns. A book earns capital by making
    money in the record, not in a backtest, and the memory decays so a book that
    stops working loses its allocation without anyone deciding to intervene."""
    if not active:
        return {}, "no active books"
    if not mature:
        # chasing seven days of returns is how a lab talks itself into noise; until
        # every book has a real sample the allocation is simply equal
        w = {k: 1.0 / len(active) for k in active}
        return _exact(w), f"equal weight until every book has {WARMUP_DAYS} live days"
    decay = 0.5 ** (1.0 / HALF_LIFE)
    scores = {}
    for key in active:
        r = [float(x) for x in live_returns.get(key, [])]
        if len(r) < 2:
            scores[key] = 0.0
            continue
        # decayed MEAN over decayed VOLATILITY, not decayed sum: scoring on raw
        # return hands the account to whichever book is most volatile and happens
        # to be up, which is how a lab ends up long a lottery ticket
        wts = np.array([decay ** i for i in range(len(r))])[::-1]
        wts /= wts.sum()
        arr = np.array(r)
        mu = float(np.sum(wts * arr))
        var = float(np.sum(wts * (arr - mu) ** 2))
        sd = math.sqrt(var) if var > 0 else 0.0
        scores[key] = mu / sd if sd > 0 else 0.0
    if not scores:
        return {}, "no scores"
    eta = 8.0                           # learning rate on the decayed Sharpe
    mx = max(scores.values())
    raw = {k: math.exp(eta * (v - mx)) for k, v in scores.items()}
    tot = sum(raw.values()) or 1.0
    w = {k: v / tot for k, v in raw.items()}
    # cap, then redistribute to the books still under it, until nothing breaches
    if MAX_WEIGHT * len(w) < 1.0:          # the cap must be satisfiable at all
        return _exact({k: 1.0 / len(w) for k in w}), "cap too tight for the number of books"
    for _ in range(50):
        over = {k: v for k, v in w.items() if v > MAX_WEIGHT + 1e-12}
        if not over:
            break
        spare = sum(v - MAX_WEIGHT for v in over.values())
        under = {k: v for k, v in w.items() if v <= MAX_WEIGHT + 1e-12}
        us = sum(under.values())
        if us <= 0:
            w = {k: 1.0 / len(w) for k in w}
            break
        w = {k: (MAX_WEIGHT if k in over else v + spare * v / us) for k, v in w.items()}
    return _exact(w), f"exponential weights on live returns, {HALF_LIFE}-day half-life"


def _exact(w):
    """Round for display, then put the rounding residual into the largest book that
    has room under the cap, so the published weights sum to exactly 1 without any
    book quietly ending up over its limit."""
    if not w:
        return {}
    out = {k: round(v, 4) for k, v in w.items()}
    resid = round(1.0 - sum(out.values()), 6)
    room = sorted((k for k in out if out[k] + resid <= MAX_WEIGHT + 1e-9),
                  key=lambda k: -out[k])
    target = room[0] if room else max(out, key=out.get)
    out[target] = round(out[target] + resid, 4)
    return out


def kill_check(st, status):
    if status == "retired":
        return "retired", None
    if st.get("days", 0) < KILL_MIN_DAYS:
        return status, None
    if st.get("max_dd_pct") is not None and st["max_dd_pct"] <= -KILL_MAX_DD * 100:
        return "retired", f"drawdown {st['max_dd_pct']}% breached the {int(KILL_MAX_DD*100)}% limit"
    if st.get("t_stat") is not None and st["t_stat"] <= KILL_TSTAT:
        return "retired", f"losing with conviction (t={st['t_stat']} over {st['days']} live days)"
    return status, None


# ---------------------------------------------------------------------------
def main():
    os.makedirs("site/data", exist_ok=True)
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {"started": None, "books": {}, "allocator": {"returns": [], "weights": {}}}

    policy = read_policy()
    panel = load_panel(EQUITY_UNIVERSE + [BENCH])
    if len(panel) < 260:
        raise RuntimeError(f"not enough history: {len(panel)} sessions")
    dates = [str(d.date()) for d in panel.index]
    last_i = len(panel) - 1
    today = dates[last_i]
    n_books = len(strat.REGISTRY)
    log(f"panel {panel.shape[1]} tickers x {len(panel)} sessions, through {today}")

    books = state.setdefault("books", {})
    state.setdefault("started", today)
    stock_cols = [c for c in panel.columns if c not in strat.ETFS]

    # ---- 1. mark yesterday's positions to market -------------------------
    for key, label, fn, why in strat.REGISTRY:
        b = books.setdefault(key, {"label": label, "why": why, "status": "warming up",
                                   "positions": {}, "as_of": None, "live": [],
                                   "retired_reason": None})
        b["label"], b["why"] = label, why          # keep text in sync with the code
        prev_date, prev_pos = b.get("as_of"), b.get("positions") or {}
        if prev_date and prev_date in dates and prev_date != today:
            i_prev = dates.index(prev_date)
            gross, spread, ic = measure(panel, i_prev, prev_pos, stock_cols)
            # costs are charged when the book CHANGES, which is the next step below
            entry = {"date": dates[i_prev + 1], "ret": round(gross, 6)}
            if spread is not None:
                entry["spread"] = round(spread, 6)
            if ic is not None:
                entry["ic"] = round(ic, 4)
            b.setdefault("live", []).append(entry)

    # ---- 2. form today's positions, charge the turnover ------------------
    ctx = context_for(panel, last_i, policy)
    for key, label, fn, why in strat.REGISTRY:
        b = books[key]
        if b.get("status") == "retired":
            b["positions"], b["as_of"] = {}, today
            continue
        try:
            w = fn(panel, last_i, ctx) or {}
        except Exception as e:
            log(f"  {key}: strategy raised {type(e).__name__}: {e}")
            w = b.get("positions") or {}
        tot = sum(w.values())
        if tot > 1.0001:                            # never leveraged
            w = {k: v / tot for k, v in w.items()}
        to = turnover(b.get("positions") or {}, w)
        cost = to * COST_BPS / 1e4
        if b.get("live"):
            b["live"][-1]["ret"] = round(b["live"][-1]["ret"] - cost, 6)
            b["live"][-1]["cost"] = round(cost, 6)
        b["positions"], b["as_of"], b["turnover"] = {k: round(v, 4) for k, v in w.items()}, today, round(to, 3)

    # ---- 3. score, enforce the kill rules --------------------------------
    live_returns = {k: [x["ret"] for x in (books[k].get("live") or [])] for k in books}
    for key in books:
        b = books[key]
        hist = books[key].get("live") or []
        b["stats"] = stats(live_returns.get(key, []), n_tests=n_books)
        b["signal"] = signal_stats([x.get("spread") for x in hist], "spread vs universe", n_books)
        b["ic"] = signal_stats([x.get("ic") for x in hist], "rank IC", n_books)
        days = b["stats"].get("days", 0)
        status = b.get("status", "warming up")
        if status != "retired":
            status = "live" if days >= WARMUP_DAYS else "warming up"
        status, reason = kill_check(b["stats"], status)
        if reason and not b.get("retired_reason"):
            b["retired_reason"] = reason
            b["retired_on"] = today
            log(f"  RETIRED {key}: {reason}")
        b["status"] = status

    # ---- 4. allocate across what is still alive --------------------------
    active = [k for k in books if books[k]["status"] != "retired" and k != "cash"]
    mature = bool(active) and all(len(live_returns.get(k, [])) >= WARMUP_DAYS for k in active)
    weights, alloc_mode = allocator(live_returns, active, mature)
    state["allocator"]["weights"] = weights
    n = len(live_returns.get(next(iter(books)), []))
    alloc_curve, equal_curve = [], []
    prev_w = {}
    for d in range(n):
        day = None
        step_alloc, step_eq = 0.0, 0.0
        for k in active:
            r = live_returns.get(k, [])
            if d < len(r):
                day = (books[k]["live"][d]["date"]) if d < len(books[k]["live"]) else day
                step_alloc += weights.get(k, 0.0) * r[d]
                step_eq += r[d] / max(1, len(active))
        alloc_curve.append({"date": day, "ret": round(step_alloc, 6)})
        equal_curve.append({"date": day, "ret": round(step_eq, 6)})
    state["allocator"]["returns"] = alloc_curve

    spy_live = []
    start_date = (books[next(iter(books))].get("live") or [{}])
    if start_date and start_date[0].get("date") in dates:
        s0 = dates.index(start_date[0]["date"])
        spy = panel[BENCH]
        for j in range(s0, last_i + 1):
            spy_live.append(float(spy.iloc[j] / spy.iloc[j - 1] - 1.0))

    # ---- 5. hindsight, clearly labelled, never promoted -------------------
    hind = {}
    start = max(260, len(panel) - HINDSIGHT_DAYS)
    for key, label, fn, why in strat.REGISTRY:
        rets, prev = [], {}
        for i in range(start, last_i):
            c = context_for(panel, i, policy)
            try:
                w = fn(panel, i, c) or {}
            except Exception:
                w = prev
            tot = sum(w.values())
            if tot > 1.0001:
                w = {k: v / tot for k, v in w.items()}
            rets.append(book_return(panel, i, w) - turnover(prev, w) * COST_BPS / 1e4)
            prev = w
        hind[key] = stats(rets, n_tests=n_books)

    # ---- 6. publish ------------------------------------------------------
    rows = []
    for key, label, fn, why in strat.REGISTRY:
        b = books[key]
        rows.append({"key": key, "label": label, "why": why, "status": b["status"],
                     "positions": b.get("positions") or {}, "turnover": b.get("turnover"),
                     "weight": weights.get(key, 0.0), "live": b.get("stats") or {},
                     "hindsight": hind.get(key) or {},
                     "signal": b.get("signal") or {}, "ic": b.get("ic") or {},
                     "retired_reason": b.get("retired_reason"),
                     "curve": [x["ret"] for x in (b.get("live") or [])][-250:]})
    rows.sort(key=lambda r: (-(r["weight"] or 0), -(r["live"].get("sharpe") or -9)))

    blended = {}
    for r in rows:
        if r["status"] == "retired" or not r["weight"]:
            continue
        for tkr, w in (r["positions"] or {}).items():
            blended[tkr] = blended.get(tkr, 0.0) + w * r["weight"]
    blended = {k: round(v, 4) for k, v in sorted(blended.items(), key=lambda kv: -kv[1]) if v >= 0.002}

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "blended": blended,
        "blended_invested_pct": round(sum(blended.values()) * 100, 1),
        "as_of": today, "started": state["started"],
        "n_books": n_books,
        "live_days": max((r["live"].get("days", 0) for r in rows), default=0),
        "rules": {"cost_bps": COST_BPS, "half_life": HALF_LIFE, "max_weight": MAX_WEIGHT,
                  "warmup_days": WARMUP_DAYS, "kill_min_days": KILL_MIN_DAYS,
                  "kill_tstat": KILL_TSTAT, "kill_max_dd_pct": KILL_MAX_DD * 100},
        "method": ("Each book forms positions from data up to today's close; the return is "
                   f"realised from the next session and charged {COST_BPS:.0f}bp of one-way "
                   "turnover. Capital is allocated by exponential weights over LIVE returns "
                   f"with a {HALF_LIFE}-day half-life, capped at {int(MAX_WEIGHT*100)}% per book. "
                   "The backtest column is hindsight: it is shown for context, it never "
                   "promotes a book, and it is not mixed into the live record."),
        "honesty": (f"With {n_books} books running, the significance bar is z=2.94 rather than "
                    "1.96, and a book with a true Sharpe of 1.0 needs about 8.6 years of live "
                    "data to clear it. Every row prints the years its own Sharpe would need. "
                    "Until a row says proven, it is a candidate, not an edge."),
        "learning": ("Three readings per book, in ascending order of statistical power. The "
                     "RETURN is what a holder made and the slowest thing to prove, because most "
                     "of its variance is simply the market moving. The SPREAD measures the same "
                     "book against the equal-weight universe, which removes the common factor "
                     "and cuts the daily noise by roughly three, so the same edge proves itself "
                     "about an order of magnitude sooner. The IC is the rank correlation between "
                     "what the book wanted to own and what actually rose, across every name in "
                     "the universe every day — one number built from ninety comparisons, which "
                     "reads the signal rather than the portfolio and is the fastest honest read "
                     "available. Each carries the number of trading days still required at the "
                     "rate observed so far, computed rather than asserted. Trading more often "
                     "does not speed this up on its own: fifty long-only positions correlated at "
                     "0.3 are worth about three independent bets, not fifty."),
        "books": rows,
        "allocator": {"weights": weights, "mode": alloc_mode, "mature": mature,
                      "live": stats([x["ret"] for x in alloc_curve], n_tests=n_books),
                      "equal_weight": stats([x["ret"] for x in equal_curve], n_tests=n_books),
                      "benchmark": stats(spy_live, n_tests=1),
                      "curve": [x["ret"] for x in alloc_curve][-250:]},
        "context": {k: ctx.get(k) for k in ("spy_above_200", "breadth", "breadth_rising", "vol_regime")},
        "policy_inputs": {"exposure_names": len(policy["federal_exposure"]),
                          "flow_sectors": len(policy["sector_flow"])},
    }
    json.dump(out, open("site/data/lab_public.json", "w"), indent=2)
    json.dump(state, open(STATE, "w"), indent=2)
    live = out["live_days"]
    log(f"lab: {n_books} books, {live} live days, "
        f"allocator sharpe {out['allocator']['live'].get('sharpe')} vs "
        f"equal-weight {out['allocator']['equal_weight'].get('sharpe')} vs "
        f"SPY {out['allocator']['benchmark'].get('sharpe')}")
    for r in rows[:5]:
        log(f"  {r['key']:16} w={r['weight']:.2f} live_sharpe={r['live'].get('sharpe')} "
            f"hindsight_sharpe={r['hindsight'].get('sharpe')} status={r['status']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        open("site/data/LAB_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL", e)
