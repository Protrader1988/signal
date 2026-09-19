"""
Name → ticker resolution for the policy desk, without the SEC.

The SEC's company_tickers.json is the natural way to turn "Elmet Group" in a press
release into ELMT, and the SEC refuses datacenter IPs, so this is a curated map of
the universe this desk actually reads about: critical minerals, semiconductors and
quantum, nuclear, defense and drones, shipbuilding, pharma onshoring, energy.

It is deliberately narrow. A name that is not here does not silently disappear — it
surfaces as an unresolved candidate with its source link, which is the honest
outcome for a company the desk has never seen before.
"""
import re

NAME_TO_TICKER = {
    # critical minerals and materials
    "MP MATERIALS": "MP", "USA RARE EARTH": "USAR", "LITHIUM AMERICAS": "LAC",
    "TRILOGY METALS": "TMQ", "PERPETUA RESOURCES": "PPTA", "NIOCORP": "NB",
    "ENERGY FUELS": "UUUU", "RAMACO RESOURCES": "METC", "CRITICAL METALS": "CRML",
    "ALCOA": "AA", "CENTURY ALUMINUM": "CENX", "FREEPORT-MCMORAN": "FCX",
    "ALBEMARLE": "ALB", "PIEDMONT LITHIUM": "PDN", "SIGMA LITHIUM": "SGML",
    "CLEVELAND-CLIFFS": "CLF", "NUCOR": "NUE", "STEEL DYNAMICS": "STLD",
    "AMERICAN RESOURCES": "AREC", "REELEMENT": "AREC", "ELMET": "ELMT",
    "IVANHOE ELECTRIC": "IE", "TALON METALS": "TLOFF", "COMSTOCK": "LODE",
    "STANDARD LITHIUM": "SLI", "URANIUM ENERGY": "UEC", "UR-ENERGY": "URG",

    # semiconductors, quantum, photonics
    "INTEL": "INTC", "NVIDIA": "NVDA", "ADVANCED MICRO DEVICES": "AMD",
    "MICRON": "MU", "GLOBALFOUNDRIES": "GFS", "TEXAS INSTRUMENTS": "TXN",
    "ANALOG DEVICES": "ADI", "MICROCHIP": "MCHP", "ONSEMI": "ON", "WOLFSPEED": "WOLF",
    "APPLIED MATERIALS": "AMAT", "LAM RESEARCH": "LRCX", "KLA": "KLAC",
    "MARVELL": "MRVL", "BROADCOM": "AVGO", "QUALCOMM": "QCOM", "COHERENT": "COHR",
    "LUMENTUM": "LITE", "CORNING": "GLW", "TAIWAN SEMICONDUCTOR": "TSM",
    "INTERNATIONAL BUSINESS MACHINES": "IBM", "IBM": "IBM", "IONQ": "IONQ",
    "RIGETTI": "RGTI", "D-WAVE": "QBTS", "QUANTUM COMPUTING INC": "QUBT",
    "QUANTINUUM": "QNT", "HONEYWELL": "HON",

    # nuclear and power
    "CAMECO": "CCJ", "CENTRUS": "LEU", "OKLO": "OKLO", "NUSCALE": "SMR",
    "BWX TECHNOLOGIES": "BWXT", "LIGHTBRIDGE": "LTBR", "CONSTELLATION ENERGY": "CEG",
    "VISTRA": "VST", "GE VERNOVA": "GEV", "NEXTERA": "NEE", "TALEN ENERGY": "TLN",

    # defense, drones, space
    "LOCKHEED MARTIN": "LMT", "RTX": "RTX", "RAYTHEON": "RTX",
    "NORTHROP GRUMMAN": "NOC", "GENERAL DYNAMICS": "GD", "HUNTINGTON INGALLS": "HII",
    "L3HARRIS": "LHX", "BOEING": "BA", "KRATOS": "KTOS", "AEROVIRONMENT": "AVAV",
    "RED CAT": "RCAT", "ONDAS": "ONDS", "UNUSUAL MACHINES": "UMAC",
    "PALANTIR": "PLTR", "LEIDOS": "LDOS", "BOOZ ALLEN": "BAH", "CACI": "CACI",
    "SAIC": "SAIC", "PARSONS": "PSN", "ROCKET LAB": "RKLB", "AST SPACEMOBILE": "ASTS",
    "INTUITIVE MACHINES": "LUNR", "REDWIRE": "RDW", "TEXTRON": "TXT",
    "CURTISS-WRIGHT": "CW", "HOWMET": "HWM", "TRANSDIGM": "TDG",

    # pharma onshoring
    "ELI LILLY": "LLY", "PFIZER": "PFE", "MERCK": "MRK", "ABBVIE": "ABBV",
    "JOHNSON & JOHNSON": "JNJ", "AMGEN": "AMGN", "NOVO NORDISK": "NVO",
    "MODERNA": "MRNA", "THERMO FISHER": "TMO", "BRISTOL-MYERS": "BMY",

    # energy, industrial, autos
    "EXXON": "XOM", "CHEVRON": "CVX", "CONOCOPHILLIPS": "COP", "CHENIERE": "LNG",
    "VENTURE GLOBAL": "VG", "NEXTDECADE": "NEXT", "PLUG POWER": "PLUG",
    "BLOOM ENERGY": "BE", "FIRST SOLAR": "FSLR", "CATERPILLAR": "CAT",
    "DEERE": "DE", "FLUOR": "FLR", "JACOBS": "J", "AECOM": "ACM",
    "FORD": "F", "GENERAL MOTORS": "GM", "TESLA": "TSLA", "STELLANTIS": "STLA",
}

# Longest names first so "GENERAL DYNAMICS" wins over a bare "GENERAL".
_ORDERED = sorted(NAME_TO_TICKER.items(), key=lambda kv: -len(kv[0]))

# Something that looks like a company but is not in the map — worth surfacing as
# unresolved rather than dropping.
_COMPANY_RE = re.compile(
    r"\b([A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){0,3}\s+"
    r"(?:Inc|Incorporated|Corp|Corporation|Company|Technologies|Industries|Materials|"
    r"Metals|Systems|Holdings|Group|Energy|Resources|Mining|LLC|Ltd|PLC)\b)")

_STOPWORDS = ("United States", "Department", "White House", "American People",
              "Executive Order", "Secretary", "Federal", "National Defense")


def resolve(text):
    """Tickers named in a piece of text, longest match first."""
    up = (text or "").upper()
    found = []
    for name, tk in _ORDERED:
        if name in up and tk not in found:
            found.append(tk)
    return found


def candidate_companies(text, limit=3):
    """Company-shaped names the map does not know. These are leads, not resolutions."""
    out = []
    for m in _COMPANY_RE.finditer(text or ""):
        name = m.group(1).strip()
        if any(sw.lower() in name.lower() for sw in _STOPWORDS):
            continue
        if resolve(name):
            continue
        if name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out
