"""BOUNTY letter list: off-market sites with the biggest zoning upside, one list across all regions,
with owner name and mailing address, ready for letters or door knocks.

Sources (all produced by the other engines from official data):
  Yonkers        hidden_potential.json  (verified Yonkers code)
  Westchester    westchester_all.json   (county zoning layer)
  Fairfield CT   ct_fairfield.json      (state assessor data, 8-30g status)
  North NJ       nj_north.json          (redevelopment areas, rail)
  NYC            nyc_rezone.json        (MapPLUTO) + owner mailing address from the latest ACRIS deed

Upside score (0-100) favours: more buildable units, vacant/parking/industrial land, transit,
rezoned industrial, redevelopment areas, 8-30g towns; it penalises owners that look institutional.
Outputs bounty/data/letters.json and bounty/data/letters.csv (mail-merge ready).
"""
import csv, json, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
LEGALS = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
MASTER = "https://data.cityofnewyork.us/resource/bnx9-e6tj.json"
PARTIES = "https://data.cityofnewyork.us/resource/636b-3b5g.json"
PER_REGION = int(os.environ.get("LETTERS_PER_REGION", 150))
NYC_MAILING_LOOKUPS = int(os.environ.get("LETTERS_NYC_LOOKUPS", 120))
INSTITUTION = re.compile(r"HOUSING|HDFC|AUTHORITY|DEVELOPMENT FUND|CHURCH|DIOCESE|TEMPLE|SCHOOL|UNIVERSITY|HOSPITAL|"
                         r"CITY OF|TOWN OF|VILLAGE OF|COUNTY|STATE OF|TRANSIT|RAILROAD|UTILITY|EDISON|LAND TRUST", re.I)


def load(n):
    try:
        return json.load(open(os.path.join(DATA, n)))
    except Exception:
        return {}


def get(url, params, timeout=40):
    u = url + "?" + urllib.parse.urlencode(params)
    for i in range(3):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout).read())
        except Exception:
            time.sleep(2 * (i + 1))
    return []


def acris_mailing(bbl):
    """Grantee name and mailing address on the most recent deed for a BBL (ACRIS open data)."""
    b = str(bbl)
    if len(b) != 10:
        return None
    boro, block, lot = b[0], str(int(b[1:6])), str(int(b[6:]))
    legs = get(LEGALS, {"$select": "document_id", "$where": f"borough='{boro}' AND block={block} AND lot={lot}", "$limit": 200})
    ids = [l["document_id"] for l in legs if l.get("document_id")]
    if not ids:
        return None
    ids_sql = ",".join(f"'{i}'" for i in ids[:150])
    deeds = get(MASTER, {"$select": "document_id,document_date,doc_type", "$where": f"document_id in ({ids_sql}) AND doc_type in ('DEED','DEEDO','DEED, LE')",
                         "$order": "document_date DESC", "$limit": 1})
    if not deeds:
        return None
    d = deeds[0]
    ps = get(PARTIES, {"$where": f"document_id='{d['document_id']}' AND party_type='2'", "$limit": 5})
    if not ps:
        return None
    p = ps[0]
    addr = ", ".join(x for x in (p.get("address_1"), p.get("address_2"), p.get("city"), p.get("state"), p.get("zip")) if x)
    return {"owner": p.get("name"), "mailing": addr, "deed_date": (d.get("document_date") or "")[:10]}


def score(units, tags, use, owner):
    s = min(units or 0, 120) / 120 * 50
    t = set(tags or [])
    s += 12 * ("TRANSIT" in t) + 10 * bool(t & {"REZONED INDUSTRIAL", "REZONE INDUSTRIAL"}) + 10 * ("REDEVELOPMENT AREA" in t)
    s += 6 * ("8-30g" in t) + 8 * bool(re.search(r"vacant|parking", use or "", re.I)) + 4 * ("SUBDIVIDE" in t)
    if INSTITUTION.search(owner or ""):
        s -= 25
    return max(0, min(100, round(s)))


def main():
    rows = []
    hp = load("hidden_potential.json")
    for r in hp.get("off_market") or []:
        tags = ["SUBDIVIDE"] if r.get("play") == "subdivide" else ["MULTIFAMILY ZONE"]
        rows.append(dict(region="Yonkers", address=r["address"], town="Yonkers", parcel=r.get("printkey"), zone=r.get("zone"),
                         use=str(r.get("class")), lot_sf=r.get("lot_sf"), upside=(f"{r.get('lots')} lots" if r.get("play") == "subdivide" else f"~{r.get('units')} units"),
                         units=r.get("units"), tags=tags, basis=r.get("rule") or "", owner=r.get("owner"), mailing=r.get("owner_mailing")))
    wa = load("westchester_all.json")
    for m in (wa.get("munis") or {}).values():
        for r in m.get("rows", []):
            rows.append(dict(region="Westchester", address=r["address"], town=r["muni"], parcel=r.get("printkey"), zone=r.get("zone"),
                             use=str(r.get("class")), lot_sf=r.get("lot_sf"), upside=r.get("est_units"), units=r.get("units"), tags=r.get("tags"),
                             basis=r.get("basis"), owner=r.get("owner"), mailing=r.get("owner_mailing")))
    ct = load("ct_fairfield.json")
    for t in (ct.get("towns") or {}).values():
        for r in t.get("rows", []):
            rows.append(dict(region="Fairfield County", address=r["address"], town=r["muni"], parcel=r.get("parcel"), zone=r.get("zone"),
                             use=r.get("use"), lot_sf=r.get("lot_sf"), upside=f"{r.get('acres')} acres", units=int((r.get("acres") or 0) * 20),
                             tags=r.get("tags"), basis=r.get("ct_8_30g"), owner=r.get("owner"), mailing=r.get("owner_mailing")))
    nj = load("nj_north.json")
    for r in nj.get("rows") or []:
        rows.append(dict(region="North NJ", address=r["address"], town=r["muni"], parcel=r.get("pin"), zone="see town zoning map",
                         use=r.get("use"), lot_sf=r.get("lot_sf"), upside=f"{r.get('acres')} acres", units=int((r.get("acres") or 0) * 40),
                         tags=r.get("tags"), basis=r.get("context"), owner=r.get("owner"), mailing=r.get("owner_mailing"),
                         zoning_map=r.get("zoning_map"), zoning_code=r.get("zoning_code")))
    nyc = load("nyc_rezone.json")
    for r in nyc.get("rows") or []:
        rows.append(dict(region="NYC " + r["area"], address=r["address"], town=r["area"], parcel=r.get("bbl"), zone=r.get("zone"),
                         use=r.get("use"), lot_sf=r.get("lot_sf"), upside=f"{r.get('units_lo')} to {r.get('units_hi')} units",
                         units=r.get("units_hi"), tags=r.get("tags"), basis=f"FAR {r.get('far')}", owner=r.get("owner"), mailing=""))
    for r in rows:
        r["score"] = score(r.get("units"), r.get("tags"), r.get("use"), r.get("owner"))
    # keep the best per region
    out = []
    for reg in sorted({r["region"] for r in rows}):
        rr = sorted((r for r in rows if r["region"] == reg), key=lambda r: -r["score"])
        seen, keep = set(), []
        for r in rr:
            k = (r["address"], r["town"])
            if k in seen or not r["address"]:
                continue
            seen.add(k)
            keep.append(r)
            if len(keep) >= PER_REGION:
                break
        out += keep
    # NYC mailing addresses from ACRIS for the top NYC targets
    nyc_rows = sorted([r for r in out if r["region"].startswith("NYC")], key=lambda r: -r["score"])[:NYC_MAILING_LOOKUPS]
    prev = {(r["address"], r["town"]): r for r in (load("letters.json").get("rows") or [])}
    for r in nyc_rows:
        p = prev.get((r["address"], r["town"]))
        if p and p.get("mailing"):
            r["mailing"], r["deed_owner"], r["deed_date"] = p["mailing"], p.get("deed_owner"), p.get("deed_date")
            continue
        m = acris_mailing(r["parcel"])
        if m:
            r["mailing"], r["deed_owner"], r["deed_date"] = m["mailing"], m["owner"], m["deed_date"]
    out.sort(key=lambda r: -r["score"])
    json.dump({"generated_utc": datetime.now(timezone.utc).isoformat(), "total": len(out),
               "by_region": {reg: sum(1 for r in out if r["region"] == reg) for reg in sorted({r["region"] for r in out})},
               "method": __doc__.strip(), "rows": out}, open(os.path.join(DATA, "letters.json"), "w"), indent=1)
    with open(os.path.join(DATA, "letters.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["score", "region", "town", "address", "parcel", "zone", "current_use", "upside", "tags", "owner", "mailing_address"])
        for r in out:
            w.writerow([r["score"], r["region"], r["town"], r["address"], r["parcel"], r["zone"], r["use"], r["upside"],
                        " ".join(r.get("tags") or []), r.get("deed_owner") or r.get("owner"), r.get("mailing")])
    print("letters:", len(out))


if __name__ == "__main__":
    main()
