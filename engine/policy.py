"""
Policy Desk — federal deals as an event study, not a headline feed.

THE QUESTION
Washington now takes equity, penny warrants, price floors, offtake and revenue
shares in listed companies. Everyone can see the announcements. The only thing
worth knowing is whether acting on one pays, and that is an empirical question
with a standard method, so this file uses the standard method.

THE METHOD (market model event study)
For every dated deal, a market model is fitted on the estimation window
t0-250..t0-21 by OLS of the stock's daily return on SPY's. Abnormal return is
AR_t = r_t - (alpha + beta*r_spy,t): what the stock did beyond what its own market
sensitivity already explained. The tables report BUY-AND-HOLD abnormal return over
each window — the compounded stock return minus the compounded expected return —
because that is what a holder actually experiences, and because summed daily ARs
can print below -100% over long windows, which cannot happen to a share. The event-
time chart still shows cumulative daily ARs, the standard way to read the path.
Windows:

    [0,0]    the announcement session itself — mostly gapped, mostly unbuyable
    [1,5]    the week after you could actually buy at the day-0 close
    [1,21]   the month after
    [1,63]   the quarter after
    [1,126]  two quarters after

Deals are grouped into cohorts because the interesting hypothesis is not "does
policy pay" but "which STRUCTURE pays": contracted cash economics (price floors,
offtake, multi-year procurement) versus a headline equity stake with no attached
revenue, versus a non-binding LOI, versus extraction, where the government takes
a cut and is a cost. Each cohort reports a median abnormal return, a bootstrap 95%
confidence interval and a sign-test p-value, and the site prints the sample size next to
every one of them, because with n in the teens most of these are anecdotes with
error bars and saying so is the whole point.

DETECTION
A curated list goes stale the day after it is written, so the registry is not the
only input. Every run searches EDGAR full text for the phrases these deals
actually produce in 8-K filings, resolves filer CIKs to tickers, and publishes
anything that is not already in the registry as an UNVERIFIED candidate with a
link to the filing. The desk therefore surfaces the next deal without waiting for
a human to notice it, while never silently promoting a machine guess into the
scored table.

EXPOSURE
Instead of a vibes list, each name carries its actual federal obligations from
USAspending — prime contract dollars, award count, most recent action date,
awarding agency — with the verification link. Name matching is imperfect and the
site says so.

FORWARD RECORD
site/data/policy_ledger.json accumulates every event from the moment this desk
first sees it, with the price at that moment. Pre-existing deals are written once
and flagged backfilled=true; they are excluded from the forward record, exactly
as the main shadow ledger treats hindsight. Over time this produces an honest
out-of-sample record for the policy desk itself.

Free sources only. Output: site/data/policy.json, site/data/policy_ledger.json
"""
import json
import math
import os
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import policy_sources as src

# ---------------------------------------------------------------------------
# REGISTRY
# Every entry is a dated, publicly announced event with a source link. Cohort is
# the structural claim being tested, not a rating.
#   contracted : contracted cash economics attached (price floor, offtake, procurement)
#   equity     : equity or warrants, no attached revenue commitment
#   loi        : announced, non-binding, terms can still move
#   extraction : government takes a share of the company's revenue
#   partner    : listed read-through to a deal with a private counterparty
# ---------------------------------------------------------------------------
REGISTRY = [
    {"ticker": "MP", "name": "MP Materials", "date": "2025-07-10", "agency": "DoD",
     "cohort": "contracted", "basis": 30.03, "recipient": "MP MATERIALS",
     "structure": "$400M convertible preferred at $30.03 plus warrants, ~15% as-converted. The economics that matter are contractual: a 10-year $110/kg NdPr price floor and 100% magnet offtake from the 10X facility.",
     "source_url": "https://mpmaterials.com/news/mp-materials-announces-transformational-public-private-partnership-with-the-department-of-defense-to-accelerate-u-s-rare-earth-magnet-independence/"},
    {"ticker": "NVDA", "name": "Nvidia", "date": "2025-08-11", "agency": "Commerce / BIS",
     "cohort": "extraction", "basis": None, "recipient": "NVIDIA",
     "structure": "15% of China H20 revenue paid to the government for export licences, later 25% on H200. Value flows out of the company, not in. Both chip names fell on the report.",
     "source_url": "https://www.cbsnews.com/news/nvidia-amd-chip-sales-china-15-percent-h20-mi308/"},
    {"ticker": "AMD", "name": "AMD", "date": "2025-08-11", "agency": "Commerce / BIS",
     "cohort": "extraction", "basis": None, "recipient": "ADVANCED MICRO DEVICES",
     "structure": "15% of China MI308 revenue to the government in exchange for export licences. Same structure as Nvidia: a levy presented as a partnership.",
     "source_url": "https://www.pbs.org/newshour/politics/under-new-unusual-agreement-u-s-will-get-a-15-cut-of-nvidia-and-amd-chip-sales-to-china"},
    {"ticker": "INTC", "name": "Intel", "date": "2025-08-22", "agency": "Commerce",
     "cohort": "equity", "basis": 20.47, "recipient": "INTEL",
     "structure": "9.9% of common (433M shares) at $20.47, converting $8.9B of unpaid CHIPS and Secure Enclave money. Additional 5% warrant only if Intel sells majority control of Foundry. No board seat, no stated exit.",
     "source_url": "https://www.intc.com/news-events/press-releases/detail/1748/intel-and-trump-administration-reach-historic-agreement-to"},
    {"ticker": "LAC", "name": "Lithium Americas", "date": "2025-09-30", "agency": "DOE",
     "cohort": "equity", "basis": 3.30, "recipient": "LITHIUM AMERICAS",
     "structure": "Penny warrants for 5% of the company plus 5% of the Thacker Pass JV, tied to restructuring the $2.23B DOE loan. No cash outlay by the government and no revenue commitment attached.",
     "source_url": "https://www.pbs.org/newshour/nation/u-s-government-taking-stake-in-company-operating-massive-lithium-mine-in-nevada"},
    {"ticker": "TMQ", "name": "Trilogy Metals", "date": "2025-10-06", "agency": "DoW / OSC",
     "cohort": "equity", "basis": 2.17, "recipient": "TRILOGY METALS",
     "structure": "$35.6M for 10% of common plus 7.5% in penny warrants, tied to the Ambler Road access decision in Alaska. Pre-production, so the stake is the whole story.",
     "source_url": "https://www.cato.org/blog/government-ownership-stakes-companies-becoming-routine-under-trump"},
    {"ticker": "CCJ", "name": "Cameco", "date": "2025-10-27", "agency": "DOE",
     "cohort": "partner", "basis": None, "recipient": "CAMECO",
     "structure": "Listed read-through to the $80B Westinghouse AP1000 partnership. The government's warrants sit in Westinghouse, which is private — holders own the partner, not the instrument.",
     "source_url": "https://www.cameco.com/media/news/united-states-government-brookfield-and-cameco-announce-transformational-partnership"},
    {"ticker": "LHX", "name": "L3Harris", "date": "2026-01-13", "agency": "DoD",
     "cohort": "contracted", "basis": None, "recipient": "L3HARRIS",
     "structure": "$1B convertible preferred in the Missile Solutions unit, auto-converting at its spin-off IPO, tied to multi-year solid rocket motor procurement. Conversion is subject to appropriations.",
     "source_url": "https://www.cato.org/blog/trump-administration-takes-equity-stake-defense-contractor"},
    {"ticker": "USAR", "name": "USA Rare Earth", "date": "2026-01-26", "agency": "Commerce",
     "cohort": "loi", "basis": None, "recipient": "USA RARE EARTH",
     "structure": "Letter of intent for access to up to $1.6B with an equity stake plus warrants. Non-binding: the terms in the headline are not yet the terms of a contract.",
     "source_url": "https://www.nist.gov/news-events/news/2026/01/department-commerces-chips-program-announces-letter-intent-usa-rare-earth"},
    {"ticker": "IBM", "name": "IBM", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "loi", "basis": None, "recipient": "INTERNATIONAL BUSINESS MACHINES",
     "structure": "$1B toward a 300mm quantum wafer foundry venture for a minority non-controlling stake. Reported at LOI stage.",
     "source_url": "https://www.datacenterdynamics.com/en/news/us-dept-of-commerce-awards-nine-quantum-computing-companies-2bn-in-exchange-for-non-controlling-equity-stakes/"},
    {"ticker": "RGTI", "name": "Rigetti Computing", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "equity", "basis": None, "recipient": "RIGETTI",
     "structure": "Up to $100M for a minority non-controlling stake, announced 21 May 2026 and definitive 8 Sep 2026. The definitive agreement moved the stock roughly a quarter as much as the announcement did.",
     "source_url": "https://247wallst.com/investing/2026/09/08/quantum-stocks-rally-as-commerce-department-takes-equity-stakes-rigetti-surges-6-d-wave-climbs-5/"},
    {"ticker": "QBTS", "name": "D-Wave Quantum", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "equity", "basis": None, "recipient": "D-WAVE",
     "structure": "$100M CHIPS award for a minority stake of roughly 1.9%, part of a nine-company, ~$2B quantum tranche.",
     "source_url": "https://www.cnbc.com/2026/05/21/quantum-stocks--us-taking-equity-stakes.html"},
    {"ticker": "GFS", "name": "GlobalFoundries", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "equity", "basis": None, "recipient": "GLOBALFOUNDRIES",
     "structure": "Two CHIPS awards totalling roughly $675M — quantum foundry and silicon photonics — for about 1.8% combined.",
     "source_url": "https://www.timesunion.com/business/article/commerce-finalizes-375-million-grant-22423918.php"},
    {"ticker": "AA", "name": "Alcoa", "date": "2026-08-31", "agency": "DoW",
     "cohort": "contracted", "basis": None, "recipient": "ALCOA",
     "structure": "~$174M of equity in the project vehicle for the Wagerup gallium refinery, with offtake attached. The stake is in the project, not the parent.",
     "source_url": "https://www.war.gov/News/Releases/Release/Article/4587183/department-of-war-announces-174-million-investment-to-secure-gallium-supply-cha/"},
    {"ticker": "ELMT", "name": "The Elmet Group", "date": "2026-09-14", "agency": "DoW",
     "cohort": "contracted", "basis": None, "recipient": "ELMET",
     "structure": "$450M committed — redeemable preferred plus warrants for up to 19.9% — funding tungsten and molybdenum capacity. Newest deal on the board.",
     "source_url": "https://www.globenewswire.com/news-release/2026/09/14/3360841/0/en/department-of-war-makes-landmark-450-million-committed-investment-in-the-elmet-group-to-secure-america-s-tungsten-supply-chain.html"},
]

# Names repeatedly floated as candidates. No deal exists for any of them; they are
# carried only so their real federal exposure can be measured rather than asserted.
RADAR = [
    {"ticker": "PPTA", "sector": "Critical minerals", "recipient": "PERPETUA",
     "thesis": "Antimony and gold at Stibnite. Antimony is on every stockpile list and has no US production."},
    {"ticker": "UUUU", "sector": "Critical minerals", "recipient": "ENERGY FUELS",
     "thesis": "Uranium plus a running rare-earth separation line — the profile the DoW has repeatedly funded."},
    {"ticker": "NB", "sector": "Critical minerals", "recipient": "NIOCORP",
     "thesis": "Niobium, scandium and titanium at Elk Creek, with an existing EXIM and DoD funding history."},
    {"ticker": "METC", "sector": "Critical minerals", "recipient": "RAMACO",
     "thesis": "Rare earth build-out attached to an existing coal cash flow."},
    {"ticker": "CRML", "sector": "Critical minerals", "recipient": "CRITICAL METALS",
     "thesis": "Tanbreez in Greenland. Administration interest has been reported; nothing is signed."},
    {"ticker": "BWXT", "sector": "Nuclear", "recipient": "BWX TECHNOLOGIES",
     "thesis": "Naval reactors and microreactors — a direct line into the SMR programmes."},
    {"ticker": "LEU", "sector": "Nuclear", "recipient": "CENTRUS",
     "thesis": "Domestic HALEU enrichment, the bottleneck in every advanced-reactor timeline."},
    {"ticker": "OKLO", "sector": "Nuclear", "recipient": "OKLO",
     "thesis": "Named in DOE fast-track lists. Pre-revenue, so entirely a policy-narrative position."},
    {"ticker": "KTOS", "sector": "Defense / drones", "recipient": "KRATOS",
     "thesis": "Drone tariffs and the Pentagon fly-off programmes point at it."},
    {"ticker": "AVAV", "sector": "Defense / drones", "recipient": "AEROVIRONMENT",
     "thesis": "Largest listed pure-play in small UAS as procurement dollars concentrate."},
    {"ticker": "HII", "sector": "Shipbuilding", "recipient": "HUNTINGTON INGALLS",
     "thesis": "Maritime Action Plan and the Navy overhaul. The buyback restrictions cut the other way."},
    {"ticker": "GD", "sector": "Shipbuilding", "recipient": "GENERAL DYNAMICS",
     "thesis": "Submarine industrial base funding, with the same capital-return constraint attached."},
]

COHORT_LABEL = {
    "contracted": "Contracted economics",
    "equity": "Equity stake only",
    "loi": "Non-binding LOI",
    "extraction": "Government takes a cut",
    "partner": "Partner read-through",
}

WINDOWS = [("day0", 0, 0), ("w1", 1, 5), ("m1", 1, 21), ("q1", 1, 63), ("h1", 1, 126)]
WINDOW_LABEL = {"day0": "Day 0", "w1": "+1w", "m1": "+1m", "q1": "+1q", "h1": "+6m"}
PATH_FROM, PATH_TO = -5, 63
MIN_EST_OBS = 60          # minimum estimation-window observations for a fitted beta
MIN_COHORT_N = 3          # below this, no cohort aggregate is published at all


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------
def load_closes(tickers, start="2024-01-01"):
    tickers = list(dict.fromkeys(tickers))
    out = {}
    try:
        df = yf.download(tickers, start=start, progress=False, auto_adjust=True, threads=True)
        close = df["Close"] if isinstance(df.columns, pd.MultiIndex) or "Close" in df else df
        for tk in tickers:
            try:
                s = close[tk].dropna() if hasattr(close, "columns") else close.dropna()
                if len(s) > 30:
                    out[tk] = s
            except Exception:
                continue
    except Exception:
        traceback.print_exc()
    return out


def rets(s):
    return s.pct_change().dropna()


# ---------------------------------------------------------------------------
# event study
# ---------------------------------------------------------------------------
def market_model(r_stock, r_mkt, end_idx):
    """OLS alpha/beta on the estimation window ending 21 sessions before the event.
    Falls back to a plain market adjustment (alpha 0, beta 1) when history is short —
    and says which one it used, because a fitted beta on 12 observations is worse
    than no beta at all."""
    lo = max(0, end_idx - 250)
    hi = max(0, end_idx - 21)
    x = r_mkt.iloc[lo:hi]
    y = r_stock.reindex(x.index).dropna()
    x = x.reindex(y.index)
    if len(y) >= MIN_EST_OBS:
        beta, alpha = np.polyfit(x.values, y.values, 1)
        resid = y.values - (alpha + beta * x.values)
        return float(alpha), float(beta), "market model", float(np.std(resid, ddof=2))
    return 0.0, 1.0, "market-adjusted (short history)", float(np.std(y.values)) if len(y) > 2 else None


def event_study(close, spy, date_str):
    """Returns (windows dict, path dict, meta) or None when the event is untestable."""
    if close is None or spy is None:
        return None
    r_s, r_m = rets(close), rets(spy)
    idx = r_s.index[r_s.index >= date_str]
    if not len(idx):
        return None                      # announced today, or after the last close
    t0 = r_s.index.get_loc(idx[0])
    alpha, beta, model, sigma = market_model(r_s, r_m, t0)
    aligned_m = r_m.reindex(r_s.index).fillna(0.0)
    ar = r_s.values - (alpha + beta * aligned_m.values)

    def bhar(a, b):
        """Buy-and-hold abnormal return: what a holder actually experienced, minus what
        the fitted market exposure would have returned over the same days. Summed daily
        ARs (the textbook CAR) drift from the holding experience over long windows and
        can print below -100%, which is not a thing that can happen to a share."""
        lo, hi = t0 + a, t0 + b
        if lo < 0 or hi >= len(ar) or hi < lo:
            return None
        r_actual = float(np.prod(1.0 + r_s.values[lo:hi + 1]) - 1.0)
        expected = alpha + beta * aligned_m.values[lo:hi + 1]
        r_expected = float(np.prod(1.0 + expected) - 1.0)
        return round((r_actual - r_expected) * 100, 1)

    windows = {k: bhar(a, b) for k, a, b in WINDOWS}
    path = {}
    run = 0.0
    for d in range(PATH_FROM, PATH_TO + 1):
        p = t0 + d
        if 0 <= p < len(ar):
            run += float(ar[p])
            path[d] = round(run * 100, 2)
    raw = None
    try:
        entry = float(close.iloc[close.index.get_loc(idx[0])])
        raw = round((float(close.iloc[-1]) / entry - 1) * 100, 1)
    except Exception:
        pass
    sessions_since = len(ar) - t0 - 1
    return windows, path, {"model": model, "beta": round(beta, 2), "alpha_bp": round(alpha * 1e4, 1),
                           "sessions_since": int(sessions_since), "raw_since_pct": raw,
                           "resid_vol_pct": round(sigma * 100, 2) if sigma else None}


# ---------------------------------------------------------------------------
# statistics — small samples, stated as such
# ---------------------------------------------------------------------------
def median_ci(values, iters=4000, seed=7):
    """Bootstrap 95% CI of the median. With n<10 this interval is wide on purpose."""
    v = np.array([x for x in values if x is not None], dtype=float)
    if len(v) < 3:
        return None, None, None
    rng = np.random.default_rng(seed)
    meds = np.median(rng.choice(v, size=(iters, len(v)), replace=True), axis=1)
    return (round(float(np.median(v)), 1),
            round(float(np.percentile(meds, 2.5)), 1),
            round(float(np.percentile(meds, 97.5)), 1))


def sign_test_p(values):
    """Two-sided sign test against a median of zero. Exact binomial, no scipy."""
    v = [x for x in values if x is not None and x != 0]
    n = len(v)
    if n < 3:
        return None
    k = sum(1 for x in v if x > 0)
    cdf = lambda m: sum(math.comb(n, i) for i in range(0, m + 1)) / (2 ** n)
    p = 2 * min(cdf(k), 1 - cdf(k - 1) if k > 0 else 1.0)
    return round(min(1.0, p), 3)


def cohort_stats(events):
    out = {}
    for cohort in sorted({e["cohort"] for e in events}):
        rows = [e for e in events if e["cohort"] == cohort]
        entry = {"cohort": cohort, "label": COHORT_LABEL.get(cohort, cohort), "n": len(rows),
                 "tickers": [r["ticker"] for r in rows], "windows": {}}
        for key, _, _ in WINDOWS:
            vals = [r["car"].get(key) for r in rows if r.get("car")]
            vals = [v for v in vals if v is not None]
            med, lo, hi = median_ci(vals)
            entry["windows"][key] = {"n": len(vals), "median": med, "lo": lo, "hi": hi,
                                     "p": sign_test_p(vals),
                                     "pos": sum(1 for v in vals if v > 0)}
        entry["reportable"] = len(rows) >= MIN_COHORT_N
        out[cohort] = entry
    return out


def cohort_paths(events):
    """Median abnormal-return path per cohort, in event time. This is the picture:
    where the move happens and whether it holds.

    Median, not mean, and deliberately: with six deals in a cohort one 700% microcap
    decides the average, and a chart that shows that outlier's path while claiming to
    describe the group is exactly the kind of thing this desk is supposed to avoid."""
    paths = {}
    for cohort in sorted({e["cohort"] for e in events}):
        rows = [e["path"] for e in events if e["cohort"] == cohort and e.get("path")]
        if len(rows) < MIN_COHORT_N:
            continue
        days, med, lo, hi, cover = [], [], [], [], []
        for d in range(PATH_FROM, PATH_TO + 1):
            vals = [p.get(str(d), p.get(d)) for p in rows]
            vals = [v for v in vals if v is not None]
            if len(vals) < MIN_COHORT_N:
                continue
            days.append(d)
            med.append(round(float(np.median(vals)), 2))
            lo.append(round(float(np.percentile(vals, 25)), 2))
            hi.append(round(float(np.percentile(vals, 75)), 2))
            cover.append(len(vals))
        if days:
            paths[cohort] = {"label": COHORT_LABEL.get(cohort, cohort), "days": days,
                             "median": med, "q25": lo, "q75": hi,
                             "n": max(cover), "n_min": min(cover)}
    return paths


# ---------------------------------------------------------------------------
# exposure, detection, ledger
# ---------------------------------------------------------------------------
def build_exposure(entries):
    out = []
    for e in entries:
        rec = e.get("recipient")
        spend = src.usaspending_awards(rec) if rec else None
        out.append({"ticker": e["ticker"], "name": e.get("name") or e["ticker"],
                    "sector": e.get("sector"), "thesis": e.get("thesis"),
                    "recipient": rec, "federal": spend})
    out.sort(key=lambda r: -((r["federal"] or {}).get("total") or 0))
    return out


def build_detection(cik_map, known_tickers, name_map):
    """Four independent detectors, deliberately. EDGAR is the best evidence and the
    least reliable to reach — the SEC returns 403 to most datacenter IPs — so the desk
    does not depend on it: DoW contract announcements and USAspending awards are filed
    whether or not anyone writes a press release, and news is the backstop. Everything
    here is machine-found and stays unverified until a human puts it in the registry."""
    items = []
    for f in src.edgar_fulltext():
        tk = cik_map.get(f.get("cik")) if f.get("cik") else None
        items.append({**f, "ticker": tk, "source": "SEC EDGAR", "quality": "filing"})
    for f in src.dow_contracts():
        items.append({**f, "ticker": None, "quality": "announcement"})
    for f in src.usaspending_recent():
        items.append({**f, "ticker": None, "quality": "award record"})
    for f in src.news_detect():
        items.append({**f, "ticker": None, "quality": "headline"})

    for it in items:
        if not it.get("ticker"):
            hay = (it.get("company") or "").upper()
            for rec, tk in name_map.items():
                if rec and rec in hay:
                    it["ticker"] = tk
                    break
        it["status"] = ("in registry" if it.get("ticker") in known_tickers and it.get("ticker")
                        else "unverified")
    seen, dedup = set(), []
    for it in sorted(items, key=lambda x: (x.get("date") or "", x.get("quality") == "filing"),
                     reverse=True):
        k = ((it.get("company") or "")[:60].upper(), it.get("date"), it.get("matched"))
        if k in seen:
            continue
        seen.add(k)
        dedup.append(it)
    return dedup[:24]


LEDGER_PATH = "site/data/policy_ledger.json"


def update_ledger(events, first_run_date):
    try:
        led = json.load(open(LEDGER_PATH))
    except Exception:
        led = {"created_utc": datetime.now(timezone.utc).isoformat(), "entries": []}
    by_key = {e["key"]: e for e in led.get("entries", [])}
    today = datetime.now(timezone.utc).date().isoformat()
    for ev in events:
        key = f"{ev['ticker']}:{ev['date']}"
        if key not in by_key:
            by_key[key] = {"key": key, "ticker": ev["ticker"], "event_date": ev["date"],
                           "cohort": ev["cohort"], "first_seen": today,
                           "price_at_first_seen": ev.get("price"),
                           # anything whose event predates the desk is hindsight, and is
                           # excluded from the forward record for exactly that reason
                           "backfilled": ev["date"] < first_run_date}
        row = by_key[key]
        if row.get("price_at_first_seen") and ev.get("price"):
            row["return_since_seen_pct"] = round((ev["price"] / row["price_at_first_seen"] - 1) * 100, 1)
        row["last_price"] = ev.get("price")
        row["last_update"] = today
    led["entries"] = sorted(by_key.values(), key=lambda r: r["event_date"], reverse=True)
    fwd = [e for e in led["entries"] if not e.get("backfilled")]
    led["forward"] = {
        "n": len(fwd),
        "since": min([e["first_seen"] for e in fwd], default=None),
        "median_return_pct": (round(float(np.median([e["return_since_seen_pct"] for e in fwd
                                                     if e.get("return_since_seen_pct") is not None])), 1)
                              if any(e.get("return_since_seen_pct") is not None for e in fwd) else None),
    }
    json.dump(led, open(LEDGER_PATH, "w"), indent=2)
    return led


# ---------------------------------------------------------------------------
def main():
    os.makedirs("site/data", exist_ok=True)
    first_run_date = "2026-09-14"      # the day this desk went live; older events are hindsight

    tickers = [d["ticker"] for d in REGISTRY] + [d["ticker"] for d in RADAR] + ["SPY"]
    closes = load_closes(tickers)
    spy = closes.get("SPY")

    events, untestable = [], []
    for d in REGISTRY:
        s = closes.get(d["ticker"])
        row = {k: d.get(k) for k in ("ticker", "name", "date", "agency", "cohort",
                                     "basis", "structure", "source_url")}
        row["price"] = round(float(s.iloc[-1]), 2) if s is not None and len(s) else None
        res = event_study(s, spy, d["date"]) if s is not None else None
        if not res:
            row.update({"car": {}, "path": {}, "meta": None, "testable": False})
            untestable.append(d["ticker"])
        else:
            w, path, meta = res
            row.update({"car": w, "path": {str(k): v for k, v in path.items()},
                        "meta": meta, "testable": True})
            if d["basis"] and row["price"]:
                row["vs_basis_pct"] = round((row["price"] / d["basis"] - 1) * 100, 1)
        events.append(row)
    events.sort(key=lambda r: r["date"], reverse=True)

    testable = [e for e in events if e.get("testable")]
    stats = cohort_stats(testable)
    paths = cohort_paths(testable)

    # headline arithmetic across every testable deal, all cohorts pooled
    pooled = {}
    for key, _, _ in WINDOWS:
        vals = [e["car"].get(key) for e in testable if e.get("car")]
        vals = [v for v in vals if v is not None]
        med, lo, hi = median_ci(vals)
        pooled[key] = {"n": len(vals), "median": med, "lo": lo, "hi": hi,
                       "p": sign_test_p(vals), "pos": sum(1 for v in vals if v > 0)}

    cik_map = src.company_tickers()
    known = {d["ticker"] for d in REGISTRY}
    name_map = {(d.get("recipient") or "").upper(): d["ticker"]
                for d in REGISTRY + RADAR if d.get("recipient")}
    detection = build_detection(cik_map, known, name_map)
    documents = (src.federal_register() + src.dow_releases())
    documents.sort(key=lambda d: d.get("date") or "", reverse=True)
    exposure = build_exposure(REGISTRY + RADAR)
    ledger = update_ledger(events, first_run_date)

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method": ("Market model event study. Alpha and beta are fitted by OLS on the 250 sessions "
                   "ending 21 before each announcement; abnormal return is the stock's return minus "
                   "what that fitted market sensitivity predicts. Window figures are buy-and-hold "
                   "abnormal returns — compounded, the way a holder experiences them — while the "
                   "event-time chart shows the cumulative daily path. Day 0 is the announcement session, which is mostly gapped and "
                   "therefore mostly unbuyable; every other window starts at the day-0 close, the "
                   "first price a retail account could actually pay. Deals with under 60 estimation "
                   "observations fall back to a plain market adjustment and are labelled."),
        "limits": ("Read the sample sizes before the medians. Fifteen events across five structures "
                   "is not a dataset, it is a set of anecdotes with error bars, and the bootstrap "
                   "intervals and sign-test p-values are printed so that is visible rather than "
                   "hidden. The registry is also curated, which means selection bias: deals that "
                   "made headlines are over-represented against quiet ones. Nothing here is a "
                   "backtest, none of it is advice, and the forward ledger below is the only part "
                   "that will ever be genuinely out of sample."),
        "windows": [{"key": k, "label": WINDOW_LABEL[k], "from": a, "to": b} for k, a, b in WINDOWS],
        "events": events,
        "untestable": untestable,
        "pooled": pooled,
        "cohorts": stats,
        "paths": paths,
        "exposure": exposure,
        "detection": detection,
        "documents": documents[:16],
        "ledger": {"forward": ledger.get("forward"), "entries": ledger.get("entries", [])[:12]},
        "health": src.HEALTH,
        "counts": {"events": len(events), "testable": len(testable), "radar": len(RADAR),
                   "detection": len(detection), "documents": len(documents)},
    }
    json.dump(out, open("site/data/policy.json", "w"), indent=2)
    m1 = pooled.get("m1", {})
    print(f"policy: {len(testable)}/{len(events)} testable · pooled +1m median "
          f"{m1.get('median')}% (n={m1.get('n')}, p={m1.get('p')}) · "
          f"{len(detection)} filings · health={ {k: v['ok'] for k, v in src.HEALTH.items()} }")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        open("site/data/POLICY_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL", e)
