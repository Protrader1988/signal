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
revenue, versus a non-binding LOI, versus the definitive agreement that follows it
months later, versus a sector directive carrying no money at all, versus a
restriction on capital returns, versus extraction, where the government takes a cut
and is a cost. Each cohort reports a median abnormal return, a bootstrap 95%
confidence interval and a sign-test p-value, and the site prints the sample size next to
every one of them, because with n in the teens most of these are anecdotes with
error bars and saying so is the whole point.

DETECTION
A curated list goes stale the day after it is written, so the registry is not the
only input. Every run reads three independent detectors: EDGAR full text for the
phrases these deals produce in 8-Ks (best evidence, and the SEC blocks most
datacenter IPs, so it cannot be relied on alone), every federal award over $50M
filed to USAspending, and policy headlines as a backstop. The DoW contracts feed
publishes one digest a day rather than one entry per deal, so it is carried as a
document to read rather than as a per-company detection. Recipient names are matched back to tracked tickers, and
anything outside the registry is published as an UNVERIFIED candidate with a link
to the underlying record. The desk therefore surfaces the next deal without
waiting for a human to notice it, while never promoting a machine guess into the
scored table on its own.

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
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import policy_sources as src

# ---------------------------------------------------------------------------
# REGISTRY
# Every entry is a dated, publicly announced event with a source link. Cohort is
# the structural claim being tested, not a rating.
#   contracted   : contracted cash economics attached (price floor, offtake, procurement)
#   equity       : equity or warrants, no attached revenue commitment
#   loi          : announced, non-binding, terms can still move
#   confirmation : the definitive agreement, months after the announcement
#   directive    : sector policy with no company-specific money
#   restriction  : the government constrains the company (capital returns)
#   extraction   : government takes a share of the company's revenue
#   partner      : listed read-through to a deal with a private counterparty
# ---------------------------------------------------------------------------
REGISTRY = [
    # ---- contracted economics: a price floor, offtake or procurement commitment ----
    {"ticker": "MP", "name": "MP Materials", "date": "2025-07-10", "agency": "DoD",
     "cohort": "contracted", "basis": 30.03, "recipient": "MP MATERIALS",
     "structure": "$400M convertible preferred at $30.03 plus warrants, ~15% as-converted. The economics that matter are contractual: a 10-year $110/kg NdPr price floor and 100% magnet offtake from the 10X facility.",
     "source_url": "https://mpmaterials.com/news/mp-materials-announces-transformational-public-private-partnership-with-the-department-of-defense-to-accelerate-u-s-rare-earth-magnet-independence/"},
    {"ticker": "LHX", "name": "L3Harris", "date": "2026-01-13", "agency": "DoD",
     "cohort": "contracted", "basis": None, "recipient": "L3HARRIS",
     "structure": "$1B convertible preferred in the Missile Solutions unit, auto-converting at its spin-off IPO, tied to multi-year solid rocket motor procurement. Conversion is subject to appropriations.",
     "source_url": "https://www.cato.org/blog/trump-administration-takes-equity-stake-defense-contractor"},
    {"ticker": "AA", "name": "Alcoa", "date": "2026-08-31", "agency": "DoW",
     "cohort": "contracted", "basis": None, "recipient": "ALCOA",
     "structure": "~$174M of equity in the project vehicle for the Wagerup gallium refinery, with offtake attached. The stake is in the project, not the parent.",
     "source_url": "https://www.war.gov/News/Releases/Release/Article/4587183/department-of-war-announces-174-million-investment-to-secure-gallium-supply-cha/"},
    {"ticker": "ELMT", "name": "The Elmet Group", "date": "2026-09-14", "agency": "DoW",
     "cohort": "contracted", "basis": None, "recipient": "ELMET",
     "structure": "$450M committed: redeemable preferred plus warrants for up to 19.9%, $200M drawn at closing, one DoW director and one observer. Separately a DLA stockpile IDIQ for tungsten with a $2B ceiling and $150M funded.",
     "source_url": "https://www.globenewswire.com/news-release/2026/09/14/3360841/0/en/department-of-war-makes-landmark-450-million-committed-investment-in-the-elmet-group-to-secure-america-s-tungsten-supply-chain.html"},

    # ---- equity or warrants, with no revenue commitment attached ----
    {"ticker": "INTC", "name": "Intel", "date": "2025-08-22", "agency": "Commerce",
     "cohort": "equity", "basis": 20.47, "recipient": "INTEL",
     "structure": "9.9% of common (433M shares) at $20.47, converting $8.9B of unpaid CHIPS and Secure Enclave money. Additional 5% warrant only if Intel sells majority control of Foundry. No board seat, no stated exit.",
     "source_url": "https://www.intc.com/news-events/press-releases/detail/1748/intel-and-trump-administration-reach-historic-agreement-to"},
    {"ticker": "LAC", "name": "Lithium Americas", "date": "2025-09-30", "agency": "DOE",
     "cohort": "equity", "basis": 3.30, "recipient": "LITHIUM AMERICAS",
     "structure": "Penny warrants for 5% of the company plus 5% of the Thacker Pass JV, tied to restructuring the $2.23B DOE loan. No cash outlay by the government and no revenue commitment attached.",
     "source_url": "https://www.pbs.org/newshour/nation/u-s-government-taking-stake-in-company-operating-massive-lithium-mine-in-nevada"},
    {"ticker": "AREC", "name": "American Resources", "date": "2025-11-03", "agency": "DoW / OSC",
     "cohort": "equity", "basis": None, "recipient": "AMERICAN RESOURCES",
     "structure": "$80M Office of Strategic Capital loan to subsidiary ReElement, matched by private capital, with the Department of War taking warrants. Part of the $1.4B Vulcan Elements magnet package; the warrants sit in the subsidiary, not the listed parent.",
     "source_url": "https://www.prnewswire.com/news-releases/vulcan-elements-forges-1-4-billion-partnership-with-the-united-states-government-and-reelement-technologies-to-expand-100-vertically-integrated-domestic-magnet-supply-chain-302602878.html"},
    {"ticker": "RGTI", "name": "Rigetti Computing", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "equity", "basis": None, "recipient": "RIGETTI",
     "structure": "Up to $100M for a minority non-controlling stake, reported at roughly 2.3% and struck at a discount. Part of the ~$2B nine-company quantum package.",
     "source_url": "https://www.cnbc.com/2026/05/21/quantum-stocks--us-taking-equity-stakes.html"},
    {"ticker": "QBTS", "name": "D-Wave Quantum", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "equity", "basis": None, "recipient": "D-WAVE",
     "structure": "$100M CHIPS award for a minority stake of roughly 1.9%, in the same quantum tranche.",
     "source_url": "https://www.cnbc.com/2026/05/21/quantum-stocks--us-taking-equity-stakes.html"},

    # ---- announced but non-binding: a letter of intent is not a contract ----
    {"ticker": "TMQ", "name": "Trilogy Metals", "date": "2025-10-06", "agency": "DoW / OSC",
     "cohort": "loi", "basis": 2.17, "recipient": "TRILOGY METALS",
     "structure": "Letter of intent for $35.6M — 10% of common plus 7.5% in penny warrants — tied to the Ambler Road access decision. It stayed an LOI for eleven months, with the closing deadline extended more than once.",
     "source_url": "https://trilogymetals.com/news-and-media/news/trilogy-metals-provides-an-update-on-the-strategic-equity-investment-by-the-u-s-department-of-war/"},
    {"ticker": "USAR", "name": "USA Rare Earth", "date": "2026-01-26", "agency": "Commerce",
     "cohort": "loi", "basis": None, "recipient": "USA RARE EARTH",
     "structure": "Letter of intent for access to up to $1.6B with an equity stake plus warrants. Non-binding: the terms in the headline are not yet the terms of a contract.",
     "source_url": "https://www.nist.gov/news-events/news/2026/01/department-commerces-chips-program-announces-letter-intent-usa-rare-earth"},
    {"ticker": "IBM", "name": "IBM", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "loi", "basis": None, "recipient": "INTERNATIONAL BUSINESS MACHINES",
     "structure": "$1B toward a 300mm quantum wafer foundry venture for a minority non-controlling stake, reported at letter-of-intent stage.",
     "source_url": "https://www.datacenterdynamics.com/en/news/us-dept-of-commerce-awards-nine-quantum-computing-companies-2bn-in-exchange-for-non-controlling-equity-stakes/"},
    {"ticker": "GFS", "name": "GlobalFoundries (quantum)", "date": "2026-05-21", "agency": "Commerce",
     "cohort": "loi", "basis": None, "recipient": "GLOBALFOUNDRIES",
     "structure": "Letter of intent for a $375M quantum manufacturing award, with Commerce taking strategic equity of about 1%.",
     "source_url": "https://gf.com/news-and-events/news/globalfoundries-launches-quantum-technology-solutions-to-scale-us-quantum-manufacturing/"},
    {"ticker": "GFS", "name": "GlobalFoundries (photonics)", "date": "2026-07-29", "agency": "Commerce",
     "cohort": "loi", "basis": None, "recipient": "GLOBALFOUNDRIES",
     "structure": "Second letter of intent, $300M for silicon photonics, with Commerce taking a further ~1%. The stock fell about 6.6% on the day — a federal cheque is not automatically good news.",
     "source_url": "https://investors.gf.com/news-releases/news-release-details/globalfoundries-signs-letter-intent-us-department-commerce-300"},

    # ---- confirmation: the definitive agreement, months after the announcement ----
    {"ticker": "RGTI", "name": "Rigetti (definitive)", "date": "2026-09-08", "agency": "Commerce",
     "cohort": "confirmation", "basis": None, "recipient": "RIGETTI",
     "structure": "The May announcement becomes a signed agreement. Tests directly whether confirmation is worth anything once the headline is old.",
     "source_url": "https://247wallst.com/investing/2026/09/08/quantum-stocks-rally-as-commerce-department-takes-equity-stakes-rigetti-surges-6-d-wave-climbs-5/"},
    {"ticker": "QBTS", "name": "D-Wave (definitive)", "date": "2026-09-08", "agency": "Commerce",
     "cohort": "confirmation", "basis": None, "recipient": "D-WAVE",
     "structure": "Same package finalised; the stock moved roughly a fifth as much as it did on the announcement.",
     "source_url": "https://247wallst.com/investing/2026/09/08/quantum-stocks-rally-as-commerce-department-takes-equity-stakes-rigetti-surges-6-d-wave-climbs-5/"},
    {"ticker": "TMQ", "name": "Trilogy (closed)", "date": "2026-09-11", "agency": "DoW / OSC",
     "cohort": "confirmation", "basis": None, "recipient": "TRILOGY METALS",
     "structure": "The eleven-month-old LOI actually closes: $35.6M funded, the government becomes a holder.",
     "source_url": "https://www.prnewswire.com/news-releases/trilogy-metals-closes-us35-6-million-strategic-equity-investment-by-the-us-department-of-war-302876686.html"},

    # ---- directive: sector policy with no company-specific money ----
    {"ticker": "IBM", "name": "IBM (quantum EOs)", "date": "2026-06-22", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "INTERNATIONAL BUSINESS MACHINES",
     "structure": "Two executive orders on quantum innovation and post-quantum cryptography. No money, no equity — pure policy signal.",
     "source_url": "https://www.whitehouse.gov/presidential-actions/2026/06/ushering-in-the-next-frontier-of-quantum-innovation/"},
    {"ticker": "QBTS", "name": "D-Wave (quantum EOs)", "date": "2026-06-22", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "D-WAVE",
     "structure": "Same executive orders, no company-specific commitment.",
     "source_url": "https://www.whitehouse.gov/presidential-actions/2026/06/ushering-in-the-next-frontier-of-quantum-innovation/"},
    {"ticker": "RGTI", "name": "Rigetti (quantum EOs)", "date": "2026-06-22", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "RIGETTI",
     "structure": "Same executive orders, no company-specific commitment.",
     "source_url": "https://www.whitehouse.gov/presidential-actions/2026/06/ushering-in-the-next-frontier-of-quantum-innovation/"},
    {"ticker": "IONQ", "name": "IonQ (quantum EOs)", "date": "2026-06-22", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "IONQ",
     "structure": "Rallied on the orders despite taking no federal equity in the May package.",
     "source_url": "https://www.lawfaremedia.org/article/white-house-releases-executive-orders-on-quantum-computing"},
    {"ticker": "QUBT", "name": "Quantum Computing Inc (quantum EOs)", "date": "2026-06-22", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "QUANTUM COMPUTING",
     "structure": "Also outside the equity package; moved on the policy signal alone.",
     "source_url": "https://www.lawfaremedia.org/article/white-house-releases-executive-orders-on-quantum-computing"},
    {"ticker": "KTOS", "name": "Kratos (drone tariffs)", "date": "2026-08-14", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "KRATOS",
     "structure": "Section 232 drone tariffs — 100% on large drones and critical components — signed the evening of 13 August, so day 0 is the first session that could trade it.",
     "source_url": "https://www.whitehouse.gov/fact-sheets/2026/08/fact-sheet-president-donald-j-trump-bolsters-national-security-and-strengthens-u-s-supply-chains-by-imposing-tariffs-on-drones-and-their-parts-and-components/"},
    {"ticker": "AVAV", "name": "AeroVironment (drone tariffs)", "date": "2026-08-14", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "AEROVIRONMENT",
     "structure": "Same proclamation; protection of domestic small-UAS production.",
     "source_url": "https://www.whitehouse.gov/fact-sheets/2026/08/fact-sheet-president-donald-j-trump-bolsters-national-security-and-strengthens-u-s-supply-chains-by-imposing-tariffs-on-drones-and-their-parts-and-components/"},
    {"ticker": "RCAT", "name": "Red Cat (drone tariffs)", "date": "2026-08-14", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "RED CAT",
     "structure": "Same proclamation; small-cap beneficiary.",
     "source_url": "https://finance.yahoo.com/economy/policy/articles/umac-rcat-onds-avav-ktos-012719145.html"},
    {"ticker": "ONDS", "name": "Ondas (drone tariffs)", "date": "2026-08-14", "agency": "White House",
     "cohort": "directive", "basis": None, "recipient": "ONDAS",
     "structure": "Same proclamation; small-cap beneficiary.",
     "source_url": "https://finance.yahoo.com/economy/policy/articles/umac-rcat-onds-avav-ktos-012719145.html"},

    # ---- restriction: the government constrains the company ----
    {"ticker": "HII", "name": "Huntington Ingalls (buyback EO)", "date": "2026-01-07", "agency": "White House",
     "cohort": "restriction", "basis": None, "recipient": "HUNTINGTON INGALLS",
     "structure": "EO 14372 lets the Secretary of War restrict buybacks, dividends and executive pay at contractors judged to be underperforming on critical programmes. HII halted buybacks.",
     "source_url": "https://www.whitehouse.gov/presidential-actions/2026/01/prioritizing-the-warfighter-in-defense-contracting/"},
    {"ticker": "GD", "name": "General Dynamics (buyback EO)", "date": "2026-01-07", "agency": "White House",
     "cohort": "restriction", "basis": None, "recipient": "GENERAL DYNAMICS",
     "structure": "Same order; capital returns become conditional on delivery performance.",
     "source_url": "https://www.cnbc.com/2026/01/07/trump-dividends-stock-buybacks-defense-companies.html"},
    {"ticker": "LMT", "name": "Lockheed Martin (buyback EO)", "date": "2026-01-07", "agency": "White House",
     "cohort": "restriction", "basis": None, "recipient": "LOCKHEED MARTIN",
     "structure": "Same order. No company is named in the text; the names came from the President's remarks that day.",
     "source_url": "https://www.cnbc.com/2026/01/07/trump-dividends-stock-buybacks-defense-companies.html"},
    {"ticker": "RTX", "name": "RTX (buyback EO)", "date": "2026-01-07", "agency": "White House",
     "cohort": "restriction", "basis": None, "recipient": "RTX",
     "structure": "Same order; defence primes sold off that session.",
     "source_url": "https://news.usni.org/2026/01/08/trump-executive-order-puts-pressure-on-defense-companies-seeks-to-halt-stock-buybacks"},
    {"ticker": "NOC", "name": "Northrop Grumman (buyback EO)", "date": "2026-01-07", "agency": "White House",
     "cohort": "restriction", "basis": None, "recipient": "NORTHROP GRUMMAN",
     "structure": "Same order.",
     "source_url": "https://news.usni.org/2026/01/08/trump-executive-order-puts-pressure-on-defense-companies-seeks-to-halt-stock-buybacks"},

    # ---- extraction: the government takes a share of revenue ----
    {"ticker": "NVDA", "name": "Nvidia", "date": "2025-08-11", "agency": "Commerce / BIS",
     "cohort": "extraction", "basis": None, "recipient": "NVIDIA",
     "structure": "15% of China H20 revenue paid to the government for export licences, later 25% on H200. Value flows out of the company, not in.",
     "source_url": "https://www.cbsnews.com/news/nvidia-amd-chip-sales-china-15-percent-h20-mi308/"},
    {"ticker": "AMD", "name": "AMD", "date": "2025-08-11", "agency": "Commerce / BIS",
     "cohort": "extraction", "basis": None, "recipient": "ADVANCED MICRO DEVICES",
     "structure": "15% of China MI308 revenue in exchange for export licences. Same structure as Nvidia: a levy presented as a partnership.",
     "source_url": "https://www.pbs.org/newshour/politics/under-new-unusual-agreement-u-s-will-get-a-15-cut-of-nvidia-and-amd-chip-sales-to-china"},

    # ---- partner: the listed read-through to a private counterparty ----
    {"ticker": "CCJ", "name": "Cameco", "date": "2025-10-27", "agency": "DOE",
     "cohort": "partner", "basis": None, "recipient": "CAMECO",
     "structure": "Read-through to the $80B Westinghouse AP1000 partnership. The government's warrants sit in Westinghouse, which is private — holders own the partner, not the instrument.",
     "source_url": "https://www.cameco.com/media/news/united-states-government-brookfield-and-cameco-announce-transformational-partnership"},
]

# What this registry does and does not cover, published on the page so the selection
# is auditable rather than implied.
COVERAGE = {
    "reviewed_through": "2026-09-19",
    "includes": ("Every federal equity stake, warrant, price floor, offtake, procurement "
                 "commitment, revenue share, sector directive and capital-return restriction "
                 "since July 2025 that attaches to a company listed on a US exchange."),
    "excludes": [
        "Deals whose counterparty is private (Westinghouse, Vulcan Elements, Korea Zinc, "
        "the July 2026 CHIPS tranche) — no ticker, no price, nothing to measure. Where a "
        "listed parent or partner exists it is carried instead, labelled as such.",
        "Foreign listings such as Syrah on the ASX, where the SPY market model does not apply.",
        "US Steel, delisted in June 2025 five days after the golden share was approved, so no "
        "post-event window exists.",
        "Private offtakes that read as federal but are not: Critical Metals' 15-year REalloys "
        "agreement has no government party, and is deliberately kept out.",
    ],
    "bias": ("Curation is the weak point. Deals that made headlines are easier to find than "
             "quiet ones, which biases the sample toward larger reactions. The detection feed "
             "below is the mechanism for closing that gap over time."),
}

# Names repeatedly floated as candidates. No federal transaction exists for any of
# them; they are carried only so their real federal exposure can be measured rather
# than asserted, and so the difference between "gets talked about" and "got a deal"
# stays visible.
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
     "thesis": "Tanbreez in Greenland. Its 15-year REalloys offtake reads federal in the headlines and is not — no government party, no price floor — which is why it stays here and out of the study."},
    {"ticker": "BWXT", "sector": "Nuclear", "recipient": "BWX TECHNOLOGIES",
     "thesis": "Naval reactors and microreactors — a direct line into the SMR programmes."},
    {"ticker": "LEU", "sector": "Nuclear", "recipient": "CENTRUS",
     "thesis": "Domestic HALEU enrichment, the bottleneck in every advanced-reactor timeline."},
    {"ticker": "OKLO", "sector": "Nuclear", "recipient": "OKLO",
     "thesis": "Named in DOE fast-track lists. Pre-revenue, so entirely a policy-narrative position."},
    {"ticker": "UMAC", "sector": "Defense / drones", "recipient": "UNUSUAL MACHINES",
     "thesis": "Drone-component onshoring; moved on the August tariffs without any federal transaction of its own."},
]

COHORT_LABEL = {
    "contracted": "Contracted economics",
    "equity": "Equity stake only",
    "loi": "Non-binding LOI",
    "confirmation": "Definitive agreement",
    "directive": "Sector directive, no money",
    "restriction": "Capital returns restricted",
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
    # one row per ticker: the registry holds several events for some names (GlobalFoundries
    # twice, Rigetti three times) and querying the same recipient repeatedly would be both
    # slower and misleading in the table
    seen, uniq = set(), []
    for e in entries:
        if e["ticker"] in seen:
            continue
        seen.add(e["ticker"])
        uniq.append(e)
    entries = uniq
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
    # A detection feed is about what is happening now; news RSS happily returns
    # six-month-old commentary, which would turn this card into an archive. Award
    # records are exempt: the query already constrains them to recent obligations,
    # while the date they carry is the contract's own start, often years back.
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=60)).isoformat()
    items = [i for i in items
             if i.get("quality") == "award record" or (i.get("date") or "9999") >= cutoff]

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
    documents = (src.federal_register() + src.dow_releases() + src.dow_contracts())
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
        "limits": (f"Read the sample sizes before the medians. {len(testable)} events across "
                   f"{len(stats)} structures, with {sum(1 for c in stats.values() if c['reportable'])} "
                   "of those structures large enough to report a median at all, is a small sample: "
                   "the bootstrap intervals and sign-test p-values are printed so that stays visible "
                   "rather than hidden, and a median whose interval straddles zero is not evidence. "
                   "Nothing here is a backtest, none of it is advice, and the forward ledger below "
                   "is the only part that will ever be genuinely out of sample."),
        "coverage": COVERAGE,
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
