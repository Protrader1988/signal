"""
BOUNTY — ACRIS distress / motivated-seller signal (NYC only).

Real data, no key. Joins two public ACRIS datasets:
  - Real Property Legals  (8h5j-fqxa): document_id <-> borough/block/lot
  - Real Property Master  (bnx9-e6tj): document_id -> doc_type, dates, amount
  - Document Control Codes (7isb-wh4c): doc_type -> human description
    (used to DISCOVER which doc-type codes mean distress — no code guessing)

Two honest signals per parcel:
  1. distress: a foreclosure filing / lis pendens / tax or mechanic's lien was
     recorded against the lot in the last ~4 years (a genuine motivated-seller
     tell — the owner is under legal/financial pressure).
  2. tenure: years since the most recent DEED was recorded. Long tenure (25y+)
     flags estate / long-hold owners who are statistically likelier to sell.

This module is ADDITIVE and NON-FATAL: if ACRIS is slow or unreachable, the
scan proceeds with no flags rather than failing. Every flag is a real recorded
document — nothing is inferred or simulated.
"""
import json, os, urllib.request, urllib.parse
from datetime import date

DEBUG = {"stages": {}}

UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/2.0)"}
LEGALS = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
MASTER = "https://data.cityofnewyork.us/resource/bnx9-e6tj.json"
CODES  = "https://data.cityofnewyork.us/resource/7isb-wh4c.json"

DISTRESS_KW = ("LIS PENDENS", "FORECLOS", "TAX LIEN", "FEDERAL LIEN", "STATE LIEN",
               "MECHANIC", "LIEN -", "NOTICE OF LIEN", "SEIZURE", "MARSHAL",
               "SHERIFF", "DEED IN LIEU", "REFEREE")
DEED_KW = ("DEED",)
DISTRESS_LOOKBACK_DAYS = 365 * 4


def _get(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _discover_codes():
    """Return (distress_codes:set, deed_codes:set) discovered from descriptions."""
    rows = _get(CODES + "?$limit=1000")
    if not rows:
        return set(), set()
    keys = list(rows[0].keys())
    def find(*pref):
        for p in pref:
            for k in keys:
                if k.startswith(p):
                    return k
        return None
    f_code = find("doc__type", "doc_type")
    f_desc = find("doc__type_description", "doc_type_description", "description")
    if not f_code or not f_desc:
        return set(), set()
    distress, deed = set(), set()
    for r in rows:
        desc = str(r.get(f_desc, "")).upper()
        code = str(r.get(f_code, "")).strip()
        if not code:
            continue
        if any(kw in desc for kw in DISTRESS_KW):
            distress.add(code)
        if any(kw in desc for kw in DEED_KW) and "DEED" in desc:
            deed.add(code)
    return distress, deed


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _legals_types():
    """Probe one legals row to learn whether borough/block/lot are numeric or
    text columns (Socrata returns JSON numbers for numeric columns, strings for
    text). Quoting an IN() clause wrong makes the whole query fail silently."""
    try:
        row = _get(LEGALS + "?$limit=1")
        r = row[0] if row else {}
        DEBUG["stages"]["legals_sample"] = {k: [type(v).__name__, v] for k, v in r.items()}
        return {c: isinstance(r.get(c), (int, float)) for c in ("borough", "block", "lot")}
    except Exception as e:
        DEBUG["stages"]["legals_probe_error"] = str(e)
        return {"borough": True, "block": True, "lot": True}


def _lit(v, numeric):
    """Render a value for a Socrata IN()/= clause per column type."""
    if numeric:
        try:
            return str(int(str(v)))
        except Exception:
            return "0"
    return "'" + str(v).replace("'", "''") + "'"


def _fetch_legals_for_boro(borough_code, blocks, types):
    """document_id + block + lot for the given borough & block set."""
    rows = []
    b_list = sorted({str(int(b)) for b in blocks if str(b).strip().isdigit()})
    blit = _lit(borough_code, types.get("borough"))
    for chunk in _chunks(b_list, 100):
        inb = ",".join(_lit(b, types.get("block")) for b in chunk)
        where = f"borough={blit} and block in({inb})"
        try:
            page = _get(LEGALS + f"?$select=document_id,borough,block,lot"
                        f"&$where={urllib.parse.quote(where)}&$limit=50000")
            rows += page
        except Exception as e:
            print(f"  ACRIS legals boro {borough_code} chunk failed: {e}")
            DEBUG["stages"].setdefault("legals_errors", []).append(str(e)[:200])
    return rows


def _fetch_master(doc_ids):
    """doc_type + recorded date + amount for the given document_ids."""
    out = {}
    ids = [d for d in doc_ids if d]
    for chunk in _chunks(ids, 120):
        inb = ",".join(f"'{d}'" for d in chunk)
        where = f"document_id in({inb})"
        try:
            page = _get(MASTER + f"?$select=document_id,doc_type,recorded_datetime,document_date,document_amt"
                        f"&$where={urllib.parse.quote(where)}&$limit=50000")
            for r in page:
                out[r.get("document_id")] = r
        except Exception as e:
            print(f"  ACRIS master chunk failed: {e}")
    return out


def annotate(deals, boro_map):
    """
    deals: list of dicts each with 'bbl' and PLUTO 'borocode','block','lot'
           (we read them off the raw parcel — caller must attach _bc/_blk/_lot).
    boro_map: PLUTO borocode -> ACRIS borough code (same 1..5 numbering).
    Returns dict bbl -> flag dict; also mutates deals in place with keys:
       distress(bool), distress_type, distress_year, owner_tenure_years, motivated(bool)
    """
    today = date.today()
    cutoff = (today.toordinal() - DISTRESS_LOOKBACK_DAYS)
    types = _legals_types()
    DEBUG["stages"]["legals_types"] = types
    try:
        distress_codes, deed_codes = _discover_codes()
        DEBUG["stages"]["codes"] = {"distress": sorted(distress_codes), "deed": sorted(deed_codes)}
        print(f"  ACRIS codes: {len(distress_codes)} distress, {len(deed_codes)} deed")
    except Exception as e:
        print(f"  ACRIS code discovery failed ({e}); skipping distress layer")
        DEBUG["stages"]["codes_error"] = str(e)
        _write_debug()
        return {}
    if not distress_codes and not deed_codes:
        _write_debug()
        return {}

    # group target (block,lot) by borough
    by_boro = {}
    for d in deals:
        bc = str(d.get("_bc") or "")
        by_boro.setdefault(bc, []).append(d)

    # (boro,block,lot) -> list of document_ids
    parcel_docs = {}
    doc_ids = set()
    legals_total = 0
    for bc, ds in by_boro.items():
        acris_boro = boro_map.get(bc, bc)
        blocks = {str(d.get("_blk")) for d in ds}
        legals = _fetch_legals_for_boro(acris_boro, blocks, types)
        legals_total += len(legals)
        want = {(str(int(str(d.get("_blk")))) if str(d.get("_blk")).isdigit() else str(d.get("_blk")),
                 str(int(str(d.get("_lot")))) if str(d.get("_lot")).isdigit() else str(d.get("_lot")))
                for d in ds}
        for r in legals:
            try:
                blk = str(int(r.get("block")))
                lot = str(int(r.get("lot")))
            except Exception:
                continue
            if (blk, lot) in want:
                key = (acris_boro, blk, lot)
                did = r.get("document_id")
                if did:
                    parcel_docs.setdefault(key, []).append(did)
                    doc_ids.add(did)
    DEBUG["stages"]["legals_rows"] = legals_total
    DEBUG["stages"]["parcels_matched"] = len(parcel_docs)
    DEBUG["stages"]["doc_ids"] = len(doc_ids)
    if not doc_ids:
        print(f"  ACRIS: no matching recorded documents (legals_rows={legals_total})")
        _write_debug()
        return {}
    master = _fetch_master(list(doc_ids))
    DEBUG["stages"]["master_rows"] = len(master)

    def ord_of(s):
        s = str(s or "")[:10]
        try:
            return date.fromisoformat(s).toordinal()
        except Exception:
            return 0

    flags = {}
    for d in deals:
        bc = str(d.get("_bc") or "")
        acris_boro = boro_map.get(bc, bc)
        try:
            blk = str(int(str(d.get("_blk"))))
            lot = str(int(str(d.get("_lot"))))
        except Exception:
            continue
        docs = parcel_docs.get((acris_boro, blk, lot), [])
        worst_distress = None
        worst_ord = 0
        latest_deed_ord = 0
        for did in docs:
            m = master.get(did)
            if not m:
                continue
            dt = str(m.get("doc_type", "")).strip()
            rec = m.get("recorded_datetime") or m.get("document_date")
            o = ord_of(rec)
            if dt in distress_codes and o >= cutoff and o > worst_ord:
                worst_ord = o
                worst_distress = dt
            if dt in deed_codes and o > latest_deed_ord:
                latest_deed_ord = o
        distress = worst_distress is not None
        tenure = None
        if latest_deed_ord:
            tenure = round((today.toordinal() - latest_deed_ord) / 365.25, 1)
        motivated = distress or (tenure is not None and tenure >= 25)
        d["distress"] = distress
        d["distress_type"] = worst_distress
        d["distress_year"] = date.fromordinal(worst_ord).year if worst_ord else None
        d["owner_tenure_years"] = tenure
        d["motivated"] = motivated
        if distress or motivated:
            flags[d.get("bbl")] = {"distress": distress, "distress_type": worst_distress,
                                   "tenure_years": tenure, "motivated": motivated}
    DEBUG["stages"]["distressed"] = sum(1 for d in deals if d.get("distress"))
    DEBUG["stages"]["motivated"] = sum(1 for d in deals if d.get("motivated"))
    DEBUG["stages"]["with_tenure"] = sum(1 for d in deals if d.get("owner_tenure_years"))
    _write_debug()
    print(f"  ACRIS: {sum(1 for d in deals if d.get('distress'))} distressed, "
          f"{sum(1 for d in deals if d.get('motivated'))} motivated (distress or 25y+ hold)")
    return flags


def _write_debug():
    try:
        os.makedirs("bounty/data", exist_ok=True)
        json.dump(DEBUG, open("bounty/data/ACRIS_DEBUG.json", "w"), indent=2, default=str)
    except Exception:
        pass
