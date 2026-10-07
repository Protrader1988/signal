"""
Strategy definitions for the lab.

Each strategy is a pure function of a price panel and a date, returning target
weights for the NEXT session. No strategy may look at a price later than the date
it is given; every function slices the panel up to `i` inclusive and the lab
realises the return from `i` to `i+1`. That is the whole lookahead defence, and it
is why the strategies live in their own file with no access to the ledger.

The set is deliberately small and each entry has an economic reason to exist. The
point is not to search a space of parameter combinations — with enough variations
you will always find a winner, and it will be noise. Twelve ideas that someone can
defend in a sentence, evaluated honestly over years, beats ten thousand that nobody
can.

Two of them (policy_tilt, flow_rotation) read the policy desk's own output, which
is the part no one else can copy: federal obligation data per sector, and per-name
federal exposure, updated daily.
"""
import numpy as np
import pandas as pd

ETFS = {"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE",
        "SPY", "QQQ", "IWM", "MDY", "DIA", "GLD", "EFA", "EEM"}
SECTOR_ETF = {              # policy sector -> the listed proxy a retail account can hold
    "Shipbuilding": "XLI", "Munitions": "XLI", "Drones / UAS": "XLI",
    "Semiconductors": "XLK", "Quantum": "XLK",
    "Nuclear": "XLU", "Pharma onshoring": "XLV", "Critical minerals": "XLB",
}
N_HOLD = 10                 # names per book; wide enough to diversify, tight enough to matter


def _stocks(panel):
    return [c for c in panel.columns if c not in ETFS]


def _ret(panel, i, lb, skip=0):
    """Return over the lb sessions ending `skip` sessions before i."""
    a, b = i - lb - skip, i - skip
    if a < 0:
        return pd.Series(dtype=float)
    return panel.iloc[b] / panel.iloc[a] - 1.0


def _vol(panel, i, lb=63):
    if i - lb < 1:
        return pd.Series(dtype=float)
    r = panel.iloc[i - lb:i + 1].pct_change().dropna(how="all")
    return r.std() * np.sqrt(252)


def _above_ma(panel, i, lb=200):
    if i - lb < 0:
        return pd.Series(dtype=bool)
    ma = panel.iloc[i - lb:i + 1].mean()
    return panel.iloc[i] > ma


def _eq(names):
    names = [n for n in names if n]
    return {n: 1.0 / len(names) for n in names} if names else {}


def _top(series, n=N_HOLD, largest=True):
    s = series.dropna()
    if s.empty:
        return []
    s = s[np.isfinite(s)]
    return list((s.nlargest(n) if largest else s.nsmallest(n)).index)


# ---------------------------------------------------------------------------
# the strategies
# ---------------------------------------------------------------------------
def mom_12_1(panel, i, ctx):
    """Classic cross-sectional momentum: 12-month return skipping the last month,
    which is the version that survives in the literature because the skip avoids
    short-term reversal."""
    r = _ret(panel, i, 252, skip=21)[_stocks(panel)]
    return _eq(_top(r))


def mom_6_1(panel, i, ctx):
    """Faster momentum. Included to see whether the horizon matters or whether both
    are the same bet wearing different clothes."""
    r = _ret(panel, i, 126, skip=21)[_stocks(panel)]
    return _eq(_top(r))


def rev_5d(panel, i, ctx):
    """Short-term reversal, restricted to names in a long-term uptrend: buy the
    week's worst losers among stocks still above their 200-day average. Liquidity
    provision, and the strategy most likely to be eaten by trading costs."""
    st = _stocks(panel)
    up = _above_ma(panel, i)
    up = [c for c in st if bool(up.get(c, False))]
    if not up:
        return {}
    r = _ret(panel, i, 5)[up]
    return _eq(_top(r, largest=False))


def lowvol(panel, i, ctx):
    """Lowest realised volatility. The low-beta anomaly: boring stocks have
    historically paid more per unit of risk than they should."""
    v = _vol(panel, i)[_stocks(panel)]
    return _eq(_top(v, largest=False))


def trend_abs(panel, i, ctx):
    """Momentum, but only while the market itself is in an uptrend. Goes to cash
    when SPY is below its 200-day average — a crash filter, not a stock picker."""
    if not ctx.get("spy_above_200"):
        return {}
    return mom_12_1(panel, i, ctx)


def dual_mom(panel, i, ctx):
    """Relative momentum with an absolute filter applied name by name: a stock has
    to be beating its peers AND be up over the year to qualify."""
    r = _ret(panel, i, 252, skip=21)[_stocks(panel)]
    r = r[r > 0]
    return _eq(_top(r))


def breadth_gated(panel, i, ctx):
    """Momentum only while market breadth is expanding. The hypothesis the plain
    momentum book cannot test: that the edge is conditional on participation."""
    if not ctx.get("breadth_rising"):
        return {}
    return mom_12_1(panel, i, ctx)


def dip_in_uptrend(panel, i, ctx):
    """Buy the pullback: names above their 200-day average that are furthest below
    their own 20-day average. Trend plus a discount, which is the shape most
    discretionary traders think they are trading."""
    st = _stocks(panel)
    up = _above_ma(panel, i)
    up = [c for c in st if bool(up.get(c, False))]
    if not up or i < 20:
        return {}
    ma20 = panel.iloc[i - 20:i + 1][up].mean()
    gap = panel.iloc[i][up] / ma20 - 1.0
    return _eq(_top(gap, largest=False))


def near_high(panel, i, ctx):
    """Closest to the 52-week high. Tests whether proximity to the high carries
    information that the momentum ranking does not."""
    st = _stocks(panel)
    if i < 252:
        return {}
    hi = panel.iloc[i - 252:i + 1][st].max()
    prox = panel.iloc[i][st] / hi
    return _eq(_top(prox))


def invvol_mom(panel, i, ctx):
    """Momentum names sized inverse to their own volatility, so a quiet winner and
    a violent one contribute comparable risk."""
    names = list(mom_12_1(panel, i, ctx))
    if not names:
        return {}
    v = _vol(panel, i)[names].replace(0, np.nan).dropna()
    if v.empty:
        return _eq(names)
    w = (1.0 / v)
    w = w / w.sum()
    return {k: float(x) for k, x in w.items()}


def policy_tilt(panel, i, ctx):
    """Momentum, overweighting names the federal government is actually paying.

    The exposure figures come from the policy desk's USAspending pull, so this is
    the one book here that trades on a dataset nobody else is assembling daily. A
    name with real federal obligations gets a 1.5x tilt within the momentum book —
    it is a tilt, not a thesis, because the policy desk's own event study says the
    announcements themselves are noise after day 0."""
    base = mom_12_1(panel, i, ctx)
    if not base:
        return {}
    exposure = ctx.get("federal_exposure") or {}
    if not exposure:
        return base
    tilt = {k: v * (1.5 if exposure.get(k, 0) > 0 else 1.0) for k, v in base.items()}
    tot = sum(tilt.values())
    return {k: v / tot for k, v in tilt.items()} if tot else base


def flow_rotation(panel, i, ctx):
    """Hold the sector ETFs whose federal obligations are accelerating fastest.

    Also from the policy desk: year-on-year growth in actual federal spending per
    sector, mapped to the listed proxy a retail account can hold. The money is
    filed whether or not anyone announces it, which is the premise being tested."""
    flow = ctx.get("sector_flow") or {}
    if not flow:
        return {}
    scored = {}
    for sector, yoy in flow.items():
        etf = SECTOR_ETF.get(sector)
        if not etf or etf not in panel.columns or yoy is None:
            continue
        scored[etf] = max(scored.get(etf, -1e9), yoy)
    winners = [k for k, v in sorted(scored.items(), key=lambda kv: -kv[1])[:3] if v > 0]
    return _eq(winners)


def cash(panel, i, ctx):
    """Holds nothing. The null hypothesis, on the page, every day: any book that
    cannot beat this is not a strategy."""
    return {}


REGISTRY = [
    ("mom_12_1", "12-1 momentum", mom_12_1, "Cross-sectional momentum, 12 months skipping the last."),
    ("mom_6_1", "6-1 momentum", mom_6_1, "Same idea on a faster horizon."),
    ("rev_5d", "5-day reversal", rev_5d, "Weekly losers inside long-term uptrends."),
    ("lowvol", "Low volatility", lowvol, "The low-beta anomaly, equal weighted."),
    ("trend_abs", "Trend-filtered momentum", trend_abs, "Momentum, flat while SPY is below its 200-day."),
    ("dual_mom", "Dual momentum", dual_mom, "Relative strength plus an absolute filter per name."),
    ("breadth_gated", "Breadth-gated momentum", breadth_gated, "Momentum only while breadth is expanding."),
    ("dip_in_uptrend", "Dip in uptrend", dip_in_uptrend, "Pullbacks within names above their 200-day."),
    ("near_high", "Near 52-week high", near_high, "Proximity to the high as its own signal."),
    ("invvol_mom", "Risk-weighted momentum", invvol_mom, "Momentum names sized inverse to volatility."),
    ("policy_tilt", "Policy-tilted momentum", policy_tilt, "Momentum tilted toward names with real federal obligations."),
    ("flow_rotation", "Federal flow rotation", flow_rotation, "Sector ETFs where federal spending is accelerating."),
    ("cash", "Cash", cash, "Holds nothing. The null hypothesis."),
]
