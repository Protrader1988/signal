"""
Policy Desk — the state-capitalism trade, tracked honestly (runs daily on Actions).

WHAT THIS IS
Since 2025 the federal government has stopped being only a regulator and become a
shareholder: direct equity, penny warrants, price floors, offtake contracts, golden
shares and export revenue shares across semis, critical minerals, quantum, nuclear
and defense. Those announcements move stocks violently on day one.

WHAT THIS IS NOT
A signal. There is no validated edge here and this file never claims one. The
positions table exists to answer one question with real prices: if you had bought
the day the deal was announced — at the price a retail account could actually get,
not the government's basis — would you have beaten simply owning SPY over the same
window? The engine computes that for every tracked deal, including the losers.
The answer is usually humbling, and that is the point.

Three things are tracked:
  1. POSITIONS — deals that actually happened. Curated by hand (a registry of
     verifiable, dated announcements); all prices and returns computed live.
  2. RADAR     — names repeatedly named as next candidates. Speculation, labeled
     as speculation. Ranked by fresh headline intensity, never by "conviction".
  3. TAPE      — the announcement channels themselves: Federal Register (EOs,
     Section 232 proclamations), DoW contracts/releases, and policy news RSS.
     These are where the next deal shows up before it is a stock move.

Free sources only: yfinance, federalregister.gov JSON API, war.gov RSS, Google News RSS.

Output: site/data/policy.json
"""
import json, os, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

import feedparser
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (compatible; SignalTerminal/1.0)"}

# ---------------------------------------------------------------------------
# REGISTRY — every entry is a dated, publicly announced event. Add new deals here.
#   tier: stake      = confirmed federal equity / warrants
#         loi        = announced, not yet definitive
#         contract   = economics without equity (price floor, offtake, golden share)
#         extraction = government takes a cut FROM the company (negative for holders)
#         partner    = public read-through to a deal with a private company
#   basis: the government's own entry price where disclosed, else None.
# ---------------------------------------------------------------------------
REGISTRY = [
    {"ticker": "INTC", "name": "Intel", "date": "2025-08-22", "agency": "Commerce",
     "tier": "stake", "basis": 20.47,
     "structure": "9.9% of common (433M sh) at $20.47, converting $8.9B of unpaid CHIPS and Secure Enclave money. Extra 5% warrant only if Intel sells majority control of Foundry. No board seat, no exit plan."},
    {"ticker": "MP", "name": "MP Materials", "date": "2025-07-10", "agency": "DoD",
     "tier": "stake", "basis": 30.03,
     "structure": "$400M convertible preferred at $30.03 plus warrants, ~15% as-converted. The real asset is contractual: a 10-year $110/kg NdPr price floor and 100% magnet offtake from the 10X facility."},
    {"ticker": "LAC", "name": "Lithium Americas", "date": "2025-09-30", "agency": "DOE",
     "tier": "stake", "basis": 3.30,
     "structure": "Penny warrants for 5% of the company plus 5% of the Thacker Pass JV, tied to restructuring the $2.23B DOE loan. No cash outlay by the government."},
    {"ticker": "TMQ", "name": "Trilogy Metals", "date": "2025-10-06", "agency": "DoW / OSC",
     "tier": "stake", "basis": 2.17,
     "structure": "$35.6M for 10% of common plus 7.5% in penny warrants, tied to the Ambler Road access decision in Alaska."},
    {"ticker": "LHX", "name": "L3Harris", "date": "2026-01-13", "agency": "DoD",
     "tier": "stake", "basis": None,
     "structure": "$1B convertible preferred in the Missile Solutions unit, auto-converting at its spin-off IPO. First Pentagon equity position in a major prime. Conversion is subject to appropriations."},
    {"ticker": "USAR", "name": "USA Rare Earth", "date": "2026-01-26", "agency": "Commerce",
     "tier": "loi", "basis": None,
     "structure": "Letter of intent for access to up to $1.6B, with an equity stake plus warrants. LOI, not a definitive agreement — terms can still move."},
    {"ticker": "RGTI", "name": "Rigetti Computing", "date": "2026-05-21", "agency": "Commerce",
     "tier": "stake", "basis": None,
     "structure": "Up to $100M for a minority non-controlling stake. Announced 21 May 2026, definitive 8 Sep 2026 — the confirmation moved the stock a quarter as much as the announcement did."},
    {"ticker": "QBTS", "name": "D-Wave Quantum", "date": "2026-05-21", "agency": "Commerce",
     "tier": "stake", "basis": None,
     "structure": "$100M CHIPS award for a minority stake (~1.9%). Part of a nine-company, ~$2B quantum tranche."},
    {"ticker": "IBM", "name": "IBM", "date": "2026-05-21", "agency": "Commerce",
     "tier": "loi", "basis": None,
     "structure": "$1B toward a 300mm quantum wafer foundry venture for a minority non-controlling stake. Reported at LOI stage."},
    {"ticker": "GFS", "name": "GlobalFoundries", "date": "2026-05-21", "agency": "Commerce",
     "tier": "stake", "basis": None,
     "structure": "Two CHIPS awards totalling ~$675M (quantum foundry plus silicon photonics) for roughly 1.8% combined."},
    {"ticker": "AA", "name": "Alcoa", "date": "2026-08-31", "agency": "DoW",
     "tier": "stake", "basis": None,
     "structure": "~$174M of equity in the project vehicle for the Wagerup gallium refinery, plus offtake. Stake is in the project, not the parent."},
    {"ticker": "ELMT", "name": "The Elmet Group", "date": "2026-09-14", "agency": "DoW",
     "tier": "stake", "basis": None,
     "structure": "$450M committed: redeemable preferred plus warrants for up to 19.9%, funding tungsten and molybdenum capacity. Newest deal on the board."},
    {"ticker": "CCJ", "name": "Cameco", "date": "2025-10-27", "agency": "DOE",
     "tier": "partner", "basis": None,
     "structure": "Public read-through to the $80B Westinghouse AP1000 partnership. The government's warrants are in Westinghouse (private), not in Cameco — you own the partner, not the stake."},
    {"ticker": "NVDA", "name": "Nvidia", "date": "2025-08-11", "agency": "Commerce / BIS",
     "tier": "extraction", "basis": None,
     "structure": "15% of China H20 revenue paid to the government for export licences, later 25% on H200. The government is taking value out, not putting it in. Both names fell on the news."},
    {"ticker": "AMD", "name": "AMD", "date": "2025-08-11", "agency": "Commerce / BIS",
     "tier": "extraction", "basis": None,
     "structure": "15% of China MI308 revenue to the government in exchange for export licences. Same structure as Nvidia: a levy dressed as a partnership."},
]

# ---------------------------------------------------------------------------
# RADAR — repeatedly named as candidates. NO deal exists for these. Speculation.
# ---------------------------------------------------------------------------
RADAR = [
    {"ticker": "PPTA", "sector": "Critical minerals", "thesis": "Antimony and gold at Stibnite; antimony is on every stockpile list and has no US production."},
    {"ticker": "UUUU", "sector": "Critical minerals", "thesis": "Uranium plus a rare-earth separation line already running — the exact profile the DoW has been funding."},
    {"ticker": "NB",   "sector": "Critical minerals", "thesis": "Niobium, scandium, titanium at Elk Creek; has an EXIM and DoD funding history."},
    {"ticker": "METC", "sector": "Critical minerals", "thesis": "Rare earth and critical minerals build-out attached to an existing coal cash flow."},
    {"ticker": "CRML", "sector": "Critical minerals", "thesis": "Tanbreez in Greenland — reported administration interest, nothing signed."},
    {"ticker": "BWXT", "sector": "Nuclear",           "thesis": "Naval reactors and microreactors; direct beneficiary of the SMR fast-track programmes."},
    {"ticker": "LEU",  "sector": "Nuclear",           "thesis": "Domestic HALEU enrichment — the bottleneck in every advanced-reactor timeline."},
    {"ticker": "OKLO", "sector": "Nuclear",           "thesis": "Named in DOE fast-track lists; pre-revenue, so entirely a policy-narrative position."},
    {"ticker": "KTOS", "sector": "Defense / drones",  "thesis": "Drone tariffs and the Pentagon fly-off programmes point straight at it."},
    {"ticker": "AVAV", "sector": "Defense / drones",  "thesis": "Largest listed pure-play in small UAS as procurement dollars concentrate."},
    {"ticker": "HII",  "sector": "Shipbuilding",      "thesis": "Maritime Action Plan and the Navy shipbuilding overhaul. Note the buyback restrictions cut the other way."},
    {"ticker": "GD",   "sector": "Shipbuilding",      "thesis": "Submarine industrial base funding, with the same capital-return constraint attached."},
]

# Announcement channels — these are where a deal is visible before it is a price.
CHANNELS = [
    {"name": "Federal Register", "url": "https://www.federalregister.gov/",
     "note": "Executive orders and Section 232 proclamations. The legal instrument, usually days after the announcement."},
    {"name": "DoW contracts", "url": "https://www.war.gov/News/Contracts/",
     "note": "Posted ~5pm ET weekdays. Minerals and defense deals show up here the same day as the company 8-K."},
    {"name": "White House actions", "url": "https://www.whitehouse.gov/presidential-actions/",
     "note": "Fact sheets and EOs, typically ahead of the Federal Register."},
    {"name": "SEC EDGAR full-text", "url": "https://www.sec.gov/edgar/search/",
     "note": "The company 8-K is often simultaneous with the agency release — and it carries the actual terms."},
    {"name": "Commerce / CHIPS", "url": "https://www.nist.gov/chips",
     "note": "Several equity conditions were posted here quietly with no press event."},
    {"name": "CFR investment tracker", "url": "https://www.cfr.org/articles/washingtons-growing-portfolio-tracking-u-s-government-investments",
     "note": "The most complete public accounting. No government source aggregates these."},
]

POLICY_QUERIES = [
    ("Equity stakes", "government equity stake company Commerce Department"),
    ("Defense", "Department of War investment critical minerals"),
    ("Minerals", "critical minerals stockpile contract award"),
    ("Orders", "executive order manufacturing tariffs onshoring"),
]


def num(x):
    try:
        f = float(x)
        return f if f == f else None
    except Exception:
        return None


def pct(a, b):
    """percent change from b to a"""
    if not a or not b:
        return None
    return round((a / b - 1) * 100, 1)


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------
def load_prices(tickers, start):
    """Daily closes for every ticker. Returns {ticker: pandas Series} — missing
    tickers are simply absent, never faked."""
    out = {}
    try:
        df = yf.download(list(dict.fromkeys(tickers)), start=start, progress=False,
                         auto_adjust=True, threads=True)
        close = df["Close"] if "Close" in df else df
        for tk in dict.fromkeys(tickers):
            try:
                s = close[tk].dropna() if hasattr(close, "columns") else close.dropna()
                if len(s):
                    out[tk] = s
            except Exception:
                pass
    except Exception:
        traceback.print_exc()
    return out


def at_or_after(series, date_str):
    """(date, close) for the first session on or after date_str."""
    try:
        s = series[series.index >= date_str]
        if len(s):
            return str(s.index[0].date()), float(s.iloc[0])
    except Exception:
        pass
    return None, None


def before(series, date_str):
    try:
        s = series[series.index < date_str]
        if len(s):
            return float(s.iloc[-1])
    except Exception:
        pass
    return None


def build_positions(prices):
    rows = []
    spy = prices.get("SPY")
    for d in REGISTRY:
        tk = d["ticker"]
        s = prices.get(tk)
        row = {k: d[k] for k in ("ticker", "name", "date", "agency", "tier", "structure")}
        row["basis"] = d["basis"]
        if s is None or not len(s):
            row.update({"price": None, "entry": None, "day1_pct": None, "since_pct": None,
                        "spy_pct": None, "excess_pct": None, "off_high_pct": None,
                        "vs_basis_pct": None, "days": None})
            rows.append(row)
            continue
        adate, entry = at_or_after(s, d["date"])
        prior = before(s, d["date"])
        last = float(s.iloc[-1])
        # 52-week high from the trailing year of sessions
        hi = float(s.iloc[-252:].max()) if len(s) >= 20 else float(s.max())
        _, spy_entry = at_or_after(spy, d["date"]) if spy is not None else (None, None)
        spy_last = float(spy.iloc[-1]) if spy is not None and len(spy) else None
        since = pct(last, entry)
        spy_ret = pct(spy_last, spy_entry)
        row.update({
            "price": round(last, 2),
            "entry": round(entry, 2) if entry else None,
            "entry_date": adate,
            "day1_pct": pct(entry, prior),                       # the announcement-day move
            "since_pct": since,                                  # buyable return since then
            "spy_pct": spy_ret,                                  # SPY over the same window
            "excess_pct": round(since - spy_ret, 1) if since is not None and spy_ret is not None else None,
            "off_high_pct": pct(last, hi),
            "vs_basis_pct": pct(last, d["basis"]) if d["basis"] else None,
            "days": (datetime.now(timezone.utc).date() - datetime.strptime(d["date"], "%Y-%m-%d").date()).days,
        })
        rows.append(row)
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def scorecard(rows):
    """The honest arithmetic. Excludes 'extraction' deals, where the government is
    taking rather than giving — including them would flatter the record with the
    AI capex cycle."""
    live = [r for r in rows if r["tier"] in ("stake", "loi", "contract") and r["excess_pct"] is not None]
    if not live:
        return {"n": 0}
    ex = sorted(r["excess_pct"] for r in live)
    sn = sorted(r["since_pct"] for r in live if r["since_pct"] is not None)
    d1 = sorted(r["day1_pct"] for r in live if r["day1_pct"] is not None)
    mid = lambda a: round(a[len(a) // 2], 1) if a else None
    beat = [r for r in live if r["excess_pct"] > 0]
    under = [r for r in live if r["since_pct"] is not None and r["since_pct"] < 0]
    return {
        "n": len(live),
        "median_excess_pct": mid(ex),
        "median_since_pct": mid(sn),
        "median_day1_pct": mid(d1),
        "beat_spy": len(beat),
        "beat_spy_pct": round(len(beat) / len(live) * 100),
        "below_entry": len(under),
        "best": max(live, key=lambda r: r["excess_pct"])["ticker"],
        "worst": min(live, key=lambda r: r["excess_pct"])["ticker"],
    }


def build_radar(prices, heat):
    out = []
    for d in RADAR:
        s = prices.get(d["ticker"])
        price = round(float(s.iloc[-1]), 2) if s is not None and len(s) else None
        chg = pct(float(s.iloc[-1]), float(s.iloc[-21])) if s is not None and len(s) > 21 else None
        out.append({**d, "price": price, "chg_20d_pct": chg,
                    "heat": heat.get(d["ticker"], 0)})
    out.sort(key=lambda r: (-(r["heat"] or 0), -(r["chg_20d_pct"] or -999)))
    return out


# ---------------------------------------------------------------------------
# tape — real documents and headlines, nothing generated
# ---------------------------------------------------------------------------
def gnews(query):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en"
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()
        return feedparser.parse(raw)
    except Exception:
        return feedparser.parse("")


def iso(entry):
    t = getattr(entry, "published_parsed", None)
    return datetime(*t[:6], tzinfo=timezone.utc).isoformat() if t else None


def federal_register(days=21):
    """Free JSON API. Presidential documents only — EOs, proclamations, memoranda."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    url = ("https://www.federalregister.gov/api/v1/documents.json"
           "?per_page=12&order=newest"
           "&fields[]=title&fields[]=html_url&fields[]=publication_date&fields[]=type"
           "&conditions[type][]=PRESDOCU"
           f"&conditions[publication_date][gte]={since}")
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()
        docs = json.loads(raw).get("results", [])
        return [{"title": d.get("title", ""), "link": d.get("html_url", ""),
                 "source": "Federal Register", "published": d.get("publication_date"),
                 "tag": "Order"} for d in docs if d.get("title")]
    except Exception:
        return []


def dow_releases(cap=8):
    feed_url = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=15"
    try:
        raw = urllib.request.urlopen(urllib.request.Request(feed_url, headers=UA), timeout=20).read()
        f = feedparser.parse(raw)
    except Exception:
        return []
    return [{"title": getattr(e, "title", "").strip(), "link": getattr(e, "link", ""),
             "source": "Dept. of War", "published": iso(e), "tag": "Defense"}
            for e in f.entries[:cap] if getattr(e, "title", "").strip()]


def build_tape():
    items = federal_register() + dow_releases()
    for tag, q in POLICY_QUERIES:
        f = gnews(q)
        for e in f.entries[:4]:
            title = getattr(e, "title", "").strip()
            src = ""
            if getattr(e, "source", None) and getattr(e.source, "title", None):
                src = e.source.title
            if not src and " - " in title:
                title, src = title.rsplit(" - ", 1)
            if title:
                items.append({"title": title, "link": getattr(e, "link", ""),
                              "source": src or "news", "published": iso(e), "tag": tag})
        time.sleep(0.3)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["published"] or "", reverse=True):
        k = it["title"].lower()[:80]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out[:22]


def heat_from_tape(tape):
    """How often a radar or registry name is actually being written about in a
    policy context. An attention count, not a forecast."""
    blob = " ".join((t["title"] or "") for t in tape).upper()
    names = {d["ticker"]: d.get("name", "") for d in REGISTRY}
    names.update({d["ticker"]: "" for d in RADAR})
    heat = {}
    for tk, nm in names.items():
        c = blob.count(tk)
        if nm:
            c += blob.count(nm.upper())
        heat[tk] = c
    return heat


def main():
    os.makedirs("site/data", exist_ok=True)
    tickers = [d["ticker"] for d in REGISTRY] + [d["ticker"] for d in RADAR] + ["SPY"]
    prices = load_prices(tickers, start="2025-06-01")
    positions = build_positions(prices)
    tape = build_tape()
    heat = heat_from_tape(tape)
    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "label": "Federal deals tracked against real prices. Not a signal, not a backtest, not advice.",
        "method": ("Every return is measured from the close of the announcement session — the first price a "
                   "retail account could actually pay — not from the government's own basis, which is usually "
                   "far lower and is not available to you. Each name is compared with SPY over the identical "
                   "window, so the column that matters is excess return, not the headline gain. Deals where "
                   "the government takes revenue rather than investing (Nvidia, AMD) are shown but excluded "
                   "from the scorecard."),
        "disclaimer": ("Policy events are not a validated edge. The move is concentrated in the first session "
                       "and frequently pre-market, which means the headline is usually unbuyable, and several "
                       "of these names have round-tripped the entire announcement move. Treat this as a "
                       "research desk for reading policy risk, not as a buy list."),
        "positions": positions,
        "scorecard": scorecard(positions),
        "radar": build_radar(prices, heat),
        "tape": tape,
        "channels": CHANNELS,
        "counts": {"positions": len(positions), "radar": len(RADAR), "tape": len(tape)},
    }
    json.dump(out, open("site/data/policy.json", "w"), indent=2)
    sc = out["scorecard"]
    print(f"policy: {len(positions)} deals, {len(tape)} tape items, "
          f"{sc.get('beat_spy', 0)}/{sc.get('n', 0)} beating SPY since announcement")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        open("site/data/POLICY_ERROR.txt", "w").write(traceback.format_exc())
        print("FATAL", e)
