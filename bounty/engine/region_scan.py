"""BOUNTY regional zoning scan: all of Westchester, Fairfield County CT, North New Jersey.

One engine, three regions, each built on the best official data that exists:

  WESTCHESTER  County tax parcels x the county's compiled municipal zoning layer (every
               municipality's districts with name, min lot and max FAR). Yonkers has its own
               verified engine (hidden_potential.py) and is skipped here.
  FAIRFIELD    CT statewide CAMA + parcel layer (every parcel carries its town zone code),
               the town's 8-30g status (CT DOH Affordable Housing Appeals List + moratoria),
               and distance to rail.
  NORTH NJ     NJ statewide parcels + MOD-IV, Areas in Need of Redevelopment (NJ DCA/OPA),
               passenger rail stations, and each municipality's zoning map/ordinance link
               (NJ DCA Zoning Information Directory).

Every row says which rule it rests on and what must be confirmed. Westchester rows whose
district has no FAR or density in the county layer say "density per code" instead of
guessing a unit count. The scan rotates through municipalities across daily runs so a slow
server never stalls the job; results merge into the existing files.
"""
import json, math, os, re, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; BountyEngine/1.0)"}
T0 = time.time()
DEADLINE_S = int(os.environ.get("REGION_DEADLINE_S", 20 * 60))
GSF = 900  # gross sf per apartment

# ---------------------------------------------------------------- shared helpers
def req(url, data=None, timeout=60, tries=3):
    last = None
    for i in range(tries):
        try:
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, data=body, headers=UA), timeout=timeout).read())
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def page(url, params, max_rows=60000):
    rows, off = [], 0
    while len(rows) < max_rows:
        p = dict(params, resultOffset=off, resultRecordCount=2000, f="json")
        d = req(url, p)
        if d.get("error"):
            raise RuntimeError(d["error"])
        feats = d.get("features", [])
        rows += feats
        if not d.get("exceededTransferLimit") and len(feats) < 2000:
            break
        off += len(feats)
    return rows


def miles(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 7917.6 * math.asin(math.sqrt(h))


def centroid(ring):
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    return (sum(ys) / len(ys), sum(xs) / len(xs))  # lat, lon


def time_left():
    return DEADLINE_S - (time.time() - T0)


def load(name):
    p = os.path.join(DATA, name)
    try:
        return json.load(open(p))
    except Exception:
        return {}


def save(name, obj):
    json.dump(obj, open(os.path.join(DATA, name), "w"), indent=1)


RAIL = []


def load_rail():
    """Rail, light-rail and subway stops in the NY/NJ/CT region (US DOT National Transit Map)."""
    u = "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_National_Transit_Map_Stops/FeatureServer/0/query"
    try:
        feats = page(u, {"where": "stop_type_text LIKE '%Rail%' OR stop_type_text LIKE '%Subway%'",
                         "geometry": "-75.0,40.4,-72.8,41.7", "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                         "spatialRel": "esriSpatialRelIntersects", "outFields": "stop_name,stop_lat,stop_lon",
                         "returnGeometry": "false"}, 20000)
        seen = set()
        for f in feats:
            a = f["attributes"]
            k = (round(a["stop_lat"], 3), round(a["stop_lon"], 3))
            if k in seen or a["stop_lat"] is None:
                continue
            seen.add(k)
            RAIL.append((a["stop_lat"], a["stop_lon"], (a["stop_name"] or "").title()))
        print("rail stops:", len(RAIL))
    except Exception:
        traceback.print_exc()


def nearest_rail(lat, lon):
    if not RAIL or lat is None:
        return None, None
    best = min(RAIL, key=lambda s: (s[0] - lat) ** 2 + ((s[1] - lon) * 0.76) ** 2)
    return best[2], round(miles(lat, lon, best[0], best[1]), 2)


PUBLIC = re.compile(r"\b(CITY OF|TOWN OF|VILLAGE OF|COUNTY OF|STATE OF|BOARD OF ED|SCHOOL DIST|HOUSING AUTH|UNITED STATES|"
                    r"METRO-NORTH|MTA\b|NJ TRANSIT|PORT AUTHORITY|OWNERS CORP|CONDOMINIUM|CO-?OP\b|CEMETERY|CHURCH|"
                    r"DIOCESE|SYNAGOGUE|TEMPLE|PARISH|STATE OF NEW JERSEY|STATE OF CONNECTICUT)", re.I)

# ---------------------------------------------------------------- WESTCHESTER
WGIS = "https://giswww.westchestergov.com/ArcGIS/rest/services"
WZ = WGIS + "/DataHub_EnvironmentandPlanning/MapServer/207/query"
WP = WGIS + "/MunicipalTaxParcels_Query/MapServer"
WNAMES = {"BED": "Bedford", "BXV": "Bronxville", "BUC": "Buchanan", "CRO": "Croton-on-Hudson", "CTD": "Cortlandt",
          "ECH": "Eastchester", "GRB": "Greenburgh", "MTP": "Mount Pleasant", "IRV": "Irvington", "MMT": "Mamaroneck Town",
          "MMV": "Mamaroneck Village", "NOC": "North Castle", "NSM": "North Salem", "WHP": "White Plains", "DBF": "Dobbs Ferry",
          "ARD": "Ardsley", "BRM": "Briarcliff Manor", "MTV": "Mount Vernon", "ELM": "Elmsford", "HAR": "Harrison",
          "HAS": "Hastings-on-Hudson", "TTN": "Tarrytown", "MTK": "Mount Kisco", "LEW": "Lewisboro", "NRO": "New Rochelle",
          "RYC": "Rye City", "OST": "Ossining Town", "OSV": "Ossining Village", "TUC": "Tuckahoe", "PKS": "Peekskill",
          "SLH": "Sleepy Hollow", "NWC": "New Castle", "LAR": "Larchmont", "PEL": "Pelham", "PDR": "Pound Ridge",
          "SOM": "Somers", "RYB": "Rye Brook", "PLV": "Pleasantville", "SCD": "Scarsdale", "YTN": "Yorktown",
          "PMR": "Pelham Manor", "PCH": "Port Chester"}
# name used in the county parcel layer list when it differs from WNAMES
WLAYER_ALIAS = {"MMT": "Mamaroneck Town", "MMV": "Mamaroneck Village", "OST": "Ossining Town", "OSV": "Ossining Village",
                "RYC": "Rye City", "CRO": "Croton", "HAS": "Hastings", "NWC": "New Castle", "PMR": "Pelham Manor",
                "PEL": "Pelham Village", "CTD": "Cortlandt", "MTP": "Mount Pleasant"}
RES_CLASSES = ("210", "215", "220", "230", "280", "311", "312", "314", "322", "323", "330", "331", "340",
               "421", "422", "425", "426", "431", "432", "433", "438", "439", "440", "441", "442", "443", "444",
               "449", "450", "452", "453", "454", "455", "464", "465", "482", "483", "484", "485", "486",
               "710", "712", "714", "715", "720")


def wclass(name, code):
    n = (name or "").upper() + " "
    c = (code or "").upper()
    if re.search(r"OVERLAY|WIRELESS|VIEW PRESERV|PARK|RECREAT|OPEN SPACE|CEMETER|HOSPITAL|RAILROAD|COLLEGE|GOLF|"
                 r"CONSERVATION|INSTITUTION|PUBLIC|CHURCH|SCHOOL|UNZONED|HUDSON RIVER", n):
        return "OTHER"
    if re.search(r"ONE.?FAMILY|SINGLE.?FAMILY|1.?FAMILY|ESTATE|RURAL", n):
        return "SF"
    if re.search(r"MULTI|APART|GARDEN|HIGH.?RISE|TOWNHOUSE|TOWN HOUSE|ATTACHED|MULTIPLE|DIVERSIFIED HOUSING|"
                 r"SENIOR|PLANNED RESIDEN|MIXED DENSITY", n):
        return "MF"
    if re.search(r"DOWNTOWN|CENTRAL|VILLAGE CENTER|CBD|MIXED|TRANSIT|TRAIN STATION|STATION PLAZA|WATERFRONT|CORE|"
                 r"REDEVELOP|URBAN RENEWAL|MVW|HAMLET CORE|TRANSITION", n):
        return "MIX"
    if re.search(r"TWO.?FAMILY|ONE AND TWO|THREE.?FAMILY", n):
        return "2F"
    if re.search(r"INDUST|MANUF|WAREHOUSE|RESEARCH", n) or re.match(r"^(M|LI|I|GI|PI)[-\s]?\d?", c):
        return "IND"
    if re.search(r"RESIDEN|^R[-\s]?\d", n + " " + c):
        return "SF"
    if re.search(r"COMMERC|BUSINESS|RETAIL|OFFICE|SHOPPING|HIGHWAY|SERVICE|NEIGHBORHOOD", n):
        return "COM"
    return "OTHER"


def wdensity(mun, code, name, far, min_lot):
    """(units_per_sf or None, far or None, basis) from the district's own name or the county FAR field."""
    c = (code or "").upper().replace(" ", "")
    if far and far > 0:
        return None, float(far), f"FAR {round(far, 2)} (county layer)"
    m = re.match(r"^M-?(\d+)$", c)
    if mun == "GRB" and m:  # Greenburgh M-14, M-25...: dwelling units per acre
        return int(m.group(1)) / 43560, None, f"{m.group(1)} units/acre (district name)"
    m = re.match(r"^RM-?(\d+)$", c)
    if mun == "MTK" and m:
        return int(m.group(1)) / 43560, None, f"{m.group(1)} units/acre (district name)"
    m = re.match(r"^M(\d{3,4})$", c)
    if mun == "ECH" and m:  # Eastchester M350/M700/M1500: lot area per unit
        return 1 / int(m.group(1)), None, f"{m.group(1)} sf lot area per unit (district name)"
    m = re.match(r"^RMF-?(\d+(\.\d+)?)$", c)
    if mun == "NRO" and m:  # New Rochelle RMF-0.7 etc.: FAR
        return None, float(m.group(1)), f"FAR {m.group(1)} (district name)"
    return None, None, "density per municipal code"


def in_poly(lat, lon, rings):
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]; xj, yj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
    return inside


def scan_westchester(state):
    """Per municipality: one id query + chunked parcel fetches, zones assigned locally (point in polygon)."""
    zones = page(WZ, {"where": "MUN IS NOT NULL", "outFields": "MUN,ZONING,ZONE_NAME,MIN_LOT,MAX_FAR,YEARUPDATED",
                      "returnGeometry": "true", "outSR": "4326", "maxAllowableOffset": "0.00005"}, 5000)
    layers = req(WP + "?f=json").get("layers", [])

    def layer_for(mun):
        want = (WLAYER_ALIAS.get(mun) or WNAMES[mun]).lower()
        for l in layers:
            if l["name"].lower().startswith(want) and "parcel" in l["name"].lower():
                return l["id"]
        for l in layers:
            if want.split()[0] in l["name"].lower() and "parcel" in l["name"].lower():
                return l["id"]
        return None

    out = load("westchester_all.json") or {}
    munis = out.get("munis", {})
    order = sorted([m for m in WNAMES if m != "YON"],
                   key=lambda m: (munis.get(m, {}).get("complete", False), munis.get(m, {}).get("scanned", "")))
    for mun in order:
        if time_left() < 180:
            break
        lid = layer_for(mun)
        mz = []
        for z in zones:
            a = z["attributes"]
            if a["MUN"] != mun or not z.get("geometry"):
                continue
            cls = wclass(a["ZONE_NAME"], a["ZONING"])
            if cls not in ("MF", "MIX", "IND", "SF", "2F"):
                continue
            rings = z["geometry"]["rings"]
            xs = [p[0] for r in rings for p in r]; ys = [p[1] for r in rings for p in r]
            mz.append((a, cls, rings, (min(xs), min(ys), max(xs), max(ys))))
        print(f"Westchester {WNAMES[mun]}: {len(mz)} relevant zone polygons, parcel layer {lid}")
        if lid is None or not mz:
            munis[mun] = {"name": WNAMES[mun], "scanned": datetime.now(timezone.utc).isoformat(), "rows": [], "count": 0,
                          "complete": True}
            continue
        url = f"{WP}/{lid}/query"
        where = f'"SHAPE.AREA">=5000 AND PROPERTYCLASS IN ({",".join(repr(c) for c in RES_CLASSES)})'
        try:
            ids = req(url, {"where": where, "returnIdsOnly": "true", "f": "json"}, timeout=90).get("objectIds") or []
        except Exception as e:
            print("  id query failed:", e)
            continue
        rows, seen, done, fails = [], set(), 0, 0
        for i in range(0, len(ids), 250):
            if time_left() < 90 or fails >= 4:
                break
            try:
                pr = req(url, {"objectIds": ",".join(map(str, ids[i:i + 250])),
                               "outFields": "*",  # some municipal layers have no owner fields; a named list fails there
                               "returnGeometry": "true", "outSR": "4326", "maxAllowableOffset": "0.0001", "f": "json"},
                         timeout=60, tries=2)
                if pr.get("error"):
                    raise RuntimeError(pr["error"].get("message"))
                fails = 0
            except Exception as e:
                fails += 1
                print("  chunk failed:", e)
                continue
            done += 250
            for f in pr.get("features", []):
                p = f["attributes"]
                pk = p.get("PRINTKEY")
                if not pk or pk in seen or not f.get("geometry") or not f["geometry"].get("rings"):
                    continue
                seen.add(pk)
                own = p.get("OWNERNAME") or ""
                if PUBLIC.search(own):
                    continue
                lat, lon = centroid(f["geometry"]["rings"][0])
                hit = None
                for a, cls, rings, bb in mz:
                    if bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3] and in_poly(lat, lon, rings):
                        hit = (a, cls)
                        break
                if not hit:
                    continue
                a, cls = hit
                area = p.get("SHAPE.AREA") or 0
                min_lot = a.get("MIN_LOT") or 0
                st, mi = nearest_rail(lat, lon)
                row = {"muni": WNAMES[mun], "printkey": pk, "address": (p.get("PROPADDRESS") or "").strip(),
                       "class": p.get("PROPERTYCLASS"), "lot_sf": round(area), "zone": a["ZONING"],
                       "zone_name": a["ZONE_NAME"], "zone_type": cls, "zone_year": a.get("YEARUPDATED"),
                       "owner": own, "owner_mailing": ", ".join(x for x in (p.get("OWNERADDRESS"), p.get("OWNERCITY"),
                                                                            p.get("OWNERSTATE"), p.get("OWNERZIP")) if x),
                       "station": st if mi is not None and mi <= 0.5 else "", "station_mi": mi, "tags": []}
                if mi is not None and mi <= 0.5:
                    row["tags"].append("TRANSIT")
                if cls in ("SF", "2F"):
                    if not min_lot or min_lot > 40000 or str(p.get("PROPERTYCLASS")) not in ("210", "215", "220", "311", "312", "314"):
                        continue
                    lots = int(area // min_lot)
                    if lots < 3 and not (lots >= 2 and str(p.get("PROPERTYCLASS")).startswith("31")):
                        continue  # a house lot needs room for 2 new lots; vacant land needs 2 lots
                    row.update(play="subdivide", lots=lots, units=lots, est_units=f"{lots} lots",
                               basis=f"min lot {int(min_lot):,} sf (county layer); street frontage not checked")
                    row["tags"].append("SUBDIVIDE")
                elif cls == "IND":
                    if not (mi is not None and mi <= 0.75) or area < 20000:
                        continue
                    row.update(play="industrial near rail", units=None, est_units="rezone",
                               basis="industrial district within 3/4 mile of rail: TOD rezoning or PUD candidate")
                    row["tags"].append("REZONE INDUSTRIAL")
                else:
                    if area < 10000:
                        continue
                    upsf, far, basis = wdensity(mun, a["ZONING"], a["ZONE_NAME"], a.get("MAX_FAR"), min_lot)
                    units = int(area * far / GSF) if far else (int(area * upsf) if upsf else None)
                    if units is not None and units < 6:
                        continue
                    row.update(play="apartments", units=units, est_units=(f"~{units}" if units else "per code"), basis=basis)
                    row["tags"].append("MULTIFAMILY ZONE" if cls == "MF" else "MIXED-USE / DOWNTOWN")
                rows.append(row)
        complete = done >= len(ids)
        rows.sort(key=lambda r: (("TRANSIT" in r["tags"]) + ("REZONE INDUSTRIAL" in r["tags"]) + (r.get("play") == "apartments"),
                                 r.get("units") or 0, r["lot_sf"]), reverse=True)
        munis[mun] = {"name": WNAMES[mun], "scanned": datetime.now(timezone.utc).isoformat(), "rows": rows[:120],
                      "count": len(rows), "complete": complete, "parcels_checked": min(done, len(ids))}
        print(f"  {len(ids)} parcels, {len(rows)} candidates, complete={complete}")
    allrows = [r for m in munis.values() for r in m["rows"]]
    save("westchester_all.json", {"generated_utc": datetime.now(timezone.utc).isoformat(), "munis": munis,
                                  "total": len(allrows),
                                  "method": "County parcels x Westchester County compiled municipal zoning layer (districts, min lot, max FAR). "
                                            "Yonkers is scanned separately with its verified code. Unit counts use the district's FAR or "
                                            "density from the county layer or district name at 900 gsf per unit; otherwise 'per code'. "
                                            "Subdivision counts are by area only. The county layer is a compilation dated by municipality; "
                                            "confirm on the municipal zoning map."})

# ---------------------------------------------------------------- FAIRFIELD COUNTY, CT
CT = "https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/Connecticut_CAMA_and_Parcel_Layer_2025/FeatureServer/0/query"
FAIRFIELD = ["Bethel", "Bridgeport", "Brookfield", "Danbury", "Darien", "Easton", "Fairfield", "Greenwich", "Monroe",
             "New Canaan", "New Fairfield", "Newtown", "Norwalk", "Redding", "Ridgefield", "Shelton", "Sherman", "Stamford",
             "Stratford", "Trumbull", "Weston", "Westport", "Wilton"]
# CT DOH Affordable Housing Appeals List: towns at 10%+ affordable are exempt from 8-30g appeals;
# OLR 2025-R-0143 (Aug 2025): active moratoria include Brookfield, Fairfield (to Apr 2029), New Canaan.
CT_EXEMPT = {"Bridgeport": "exempt (10%+ affordable)", "Danbury": "exempt (10%+ affordable)",
             "Norwalk": "exempt (10%+ affordable)", "Stamford": "exempt (10%+ affordable)",
             "Brookfield": "moratorium", "Fairfield": "moratorium to Apr 2029", "New Canaan": "moratorium"}
CT_USE_KEEP = re.compile(r"VACANT|COMMERC|INDUST|WAREHOUSE|PARKING|GARAGE|AUTO|SERVICE STA|RETAIL|STORE|OFFICE|"
                         r"MIXED|MULTI|APART|THREE FAM|TWO FAM|SINGLE FAM|RES LAND|DEVELOP", re.I)
CT_MF_ZONE = re.compile(r"(MF|RM|R-?MF|DD|CDD|TOD|MU|MX|CBD|DT|DV|VB|VC|CC|C-?C|RA|GB|NB|DB|BD|AH|HOD|PDD|PRD|SD)", re.I)


def scan_fairfield():
    out = load("ct_fairfield.json") or {}
    towns = out.get("towns", {})
    order = sorted(FAIRFIELD, key=lambda t: towns.get(t, {}).get("scanned", ""))  # oldest first if time runs short
    for town in order:
        if time_left() < 120:
            break
        try:
            feats = page(CT, {"where": f"Town_Name='{town}' AND Land_Acres>=0.25",
                              "outFields": "Town_Name,Location,Parcel_ID,Owner,Mailing_Address,Mailing_City,Mailing_State,"
                                           "Mailing_Zip,Assessed_Total,Assessed_Land,Assessed_Building,Land_Acres,Zone,"
                                           "State_Use_Description,AYB,Living_Area,Sale_Price,Sale_Date",
                              "returnGeometry": "false", "returnCentroid": "true", "outSR": "4326"}, 40000)
        except Exception:
            traceback.print_exc()
            continue
        rows = []
        status = CT_EXEMPT.get(town, "8-30g applies (under 10% affordable)")
        for f in feats:
            a = f["attributes"]
            use = a.get("State_Use_Description") or ""
            own = a.get("Owner") or ""
            zone0 = (a.get("Zone") or "").upper()
            if not use:  # some towns submit only the use code: fall back to code / zone
                code = str(a.get("State_Use") or "")
                if re.match(r"^(3|4)\d\d", code) or re.match(r"^(I|IP|LI|M|GI|IG)\b|^(I|M)-", zone0):
                    use = "Commercial/industrial (use code %s)" % code
            if not CT_USE_KEEP.search(use) or PUBLIC.search(own):
                continue
            acres = a.get("Land_Acres") or 0
            bldg = a.get("Assessed_Building") or 0
            land = a.get("Assessed_Land") or 0
            single = re.search(r"SINGLE FAM|ONE FAM|RES LAND", use, re.I)
            if single and acres < 1.0:
                continue  # house lots under an acre are not the play here
            if acres > 25:
                continue
            c = f.get("centroid") or {}
            st, mi = nearest_rail(c.get("y"), c.get("x"))
            underused = (bldg <= land * 0.6) or re.search(r"VACANT|PARKING|RES LAND", use, re.I)
            if not underused:
                continue
            tags = []
            if mi is not None and mi <= 0.5:
                tags.append("TRANSIT")
            if "applies" in status:
                tags.append("8-30g")
            if re.search(r"INDUST|WAREHOUSE", use, re.I):
                tags.append("REZONE INDUSTRIAL")
            zone = (a.get("Zone") or "").strip()
            if zone and CT_MF_ZONE.search(zone):
                tags.append("ZONE MAY ALLOW MF")
            rows.append({"muni": town, "parcel": a.get("Parcel_ID"), "address": (a.get("Location") or "").strip(),
                         "use": use, "zone": zone, "acres": round(acres, 2), "lot_sf": round(acres * 43560),
                         "assessed_land": land, "assessed_bldg": bldg, "owner": own,
                         "owner_mailing": ", ".join(str(x) for x in (a.get("Mailing_Address"), a.get("Mailing_City"),
                                                                     a.get("Mailing_State"), a.get("Mailing_Zip")) if x),
                         "station": st if mi is not None and mi <= 0.5 else "", "station_mi": mi,
                         "ct_8_30g": status, "tags": tags})
        score = lambda r: (("TRANSIT" in r["tags"]) * 2 + ("8-30g" in r["tags"]) + ("REZONE INDUSTRIAL" in r["tags"])
                           + ("ZONE MAY ALLOW MF" in r["tags"]), r["acres"])
        rows.sort(key=score, reverse=True)
        towns[town] = {"scanned": datetime.now(timezone.utc).isoformat(), "rows": rows[:150], "count": len(rows),
                       "status_8_30g": status}
        print(f"Fairfield {town}: {len(feats)} parcels >= 1/4 acre, {len(rows)} candidates")
    save("ct_fairfield.json", {"generated_utc": datetime.now(timezone.utc).isoformat(), "towns": towns,
                               "total": sum(len(t["rows"]) for t in towns.values()),
                               "method": "CT 2025 CAMA + parcel layer (town zone code on every parcel). Kept: vacant, parking, "
                                         "commercial, industrial and multifamily parcels of 1/4 acre or more whose building is "
                                         "assessed at no more than 60% of the land, plus house lots of an acre or more. "
                                         "8-30g status from the CT DOH Affordable Housing Appeals List and OLR 2025-R-0143. "
                                         "Zone codes are the assessor's; confirm the district rules on the town zoning map."})

# ---------------------------------------------------------------- NORTH NEW JERSEY
NJP = "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/Parcels_Composite_NJ_WM/FeatureServer/0/query"
ANIR = "https://services.arcgis.com/Aur8tCo478N3VovT/arcgis/rest/services/NJ_Areas_in_Need_of_Redevelopment/FeatureServer/0/query"
NJRAIL = "https://services.arcgis.com/Aur8tCo478N3VovT/arcgis/rest/services/NJ_Passenger_Rail_Stations/FeatureServer/0/query"
NJZ = "https://services.arcgis.com/Aur8tCo478N3VovT/arcgis/rest/services/Municipal_Zoning/FeatureServer/0/query"
NJ_COUNTIES = ["BERGEN", "HUDSON", "ESSEX", "PASSAIC", "UNION", "MORRIS"]
NJ_CLASS = {"1": "vacant", "4A": "commercial", "4B": "industrial", "4C": "apartments", "2": "residential 1-4 family"}


def scan_nj():
    out = load("nj_north.json") or {}
    links = {}
    try:
        for f in page(NJZ, {"where": "1=1", "outFields": "Municipali,County_1,Map,Ordinance", "returnGeometry": "false"}, 1000):
            a = f["attributes"]
            links[(a.get("Municipali") or "").upper()] = {"map": a.get("Map"), "ordinance": a.get("Ordinance")}
    except Exception:
        traceback.print_exc()
    work, rows_by = {}, {}
    # 1) Areas in Need of Redevelopment in the six counties
    tasks = []
    try:
        an = page(ANIR, {"where": "COUNTY IN (" + ",".join(f"'{c.title()}'" for c in NJ_COUNTIES) + ")" +
                         " OR UPPER(COUNTY) IN (" + ",".join(f"'{c}'" for c in NJ_COUNTIES) + ")",
                         "outFields": "RID,MUN_LABEL,COUNTY,RES_DATE,APPROVAL,CONDEMN", "returnGeometry": "true", "outSR": "4326"}, 5000)
        for f in an:
            tasks.append(("anir", str(f["attributes"].get("RID") or f["attributes"].get("OBJECTID")), f))
    except Exception:
        traceback.print_exc()
    # 2) Half-mile around each passenger rail station in the six counties
    try:
        rs = page(NJRAIL, {"where": "UPPER(COUNTY) IN (" + ",".join(f"'{c}'" for c in NJ_COUNTIES) + ")",
                           "outFields": "STATION,RAIL_LINE,MUNI,COUNTY", "returnGeometry": "true", "outSR": "4326"}, 2000)
        for f in rs:
            tasks.append(("rail", "rail:" + (f["attributes"].get("STATION") or "") + ":" + (f["attributes"].get("RAIL_LINE") or ""), f))
    except Exception:
        traceback.print_exc()
    print(f"NJ: {len(tasks)} redevelopment areas and stations to scan")
    for kind, key, f in tasks:
        if time_left() < 90:
            break
        a = f["attributes"]
        if kind == "anir":
            geom = json.dumps({"rings": f["geometry"]["rings"], "spatialReference": {"wkid": 4326}})
            gq = {"geometry": geom, "geometryType": "esriGeometryPolygon"}
            yr = ""
            for fld in ("APPROVAL", "RES_DATE"):
                v = a.get(fld)
                if isinstance(v, (int, float)) and v > 0:
                    yr = " " + datetime.fromtimestamp(v / 1000, timezone.utc).strftime("%Y"); break
            label = f"Redevelopment area ({a.get('MUN_LABEL')}, designated{yr}{'; condemnation authorized' if str(a.get('CONDEMN') or '').upper().startswith('Y') else ''})"
        else:
            g = f["geometry"]
            gq = {"geometry": f"{g['x']},{g['y']}", "geometryType": "esriGeometryPoint", "distance": 805, "units": "esriSRUnit_Meter"}
            label = f"{a.get('STATION')} station ({a.get('RAIL_LINE')})"
        try:
            ps = page(NJP, dict(gq, inSR="4326", spatialRel="esriSpatialRelIntersects",
                                where="PROP_CLASS IN ('1','4A','4B') AND CALC_ACRE>=0.15",
                                outFields="PAMS_PIN,MUN_NAME,COUNTY,PROP_LOC,PROP_CLASS,CALC_ACRE,OWNER_NAME,ST_ADDRESS,CITY_STATE,"
                                          "ZIP_CODE,LAND_VAL,IMPRVT_VAL,BLDG_DESC,YR_CONSTR,SALE_PRICE,DEED_DATE",
                                returnGeometry="false", returnCentroid="true", outSR="4326"), 6000)
        except Exception as e:
            print("  NJ query failed:", key, e)
            continue
        rows = []
        for p in ps:
            b = p["attributes"]
            own = b.get("OWNER_NAME") or ""
            if PUBLIC.search(own):
                continue
            if (b.get("CALC_ACRE") or 0) > 150:
                continue  # bad acreage in the composite
            land, imp = b.get("LAND_VAL") or 0, b.get("IMPRVT_VAL") or 0
            if b.get("PROP_CLASS") != "1" and imp > land * 0.75:
                continue  # building worth more than the dirt: not a redevelopment play
            c = p.get("centroid") or {}
            st, mi = nearest_rail(c.get("y"), c.get("x"))
            tags = ["REDEVELOPMENT AREA"] if kind == "anir" else []
            if mi is not None and mi <= 0.5:
                tags.append("TRANSIT")
            if b.get("PROP_CLASS") == "4B":
                tags.append("REZONE INDUSTRIAL")
            mun = (b.get("MUN_NAME") or "").upper()
            lk = links.get(mun) or {}
            rows.append({"muni": (b.get("MUN_NAME") or "").title(), "county": (b.get("COUNTY") or "").title(),
                         "pin": b.get("PAMS_PIN"), "address": b.get("PROP_LOC") or "", "use": NJ_CLASS.get(b.get("PROP_CLASS"), b.get("PROP_CLASS")),
                         "acres": round(b.get("CALC_ACRE") or 0, 2), "lot_sf": round((b.get("CALC_ACRE") or 0) * 43560),
                         "land_val": land, "impr_val": imp, "owner": own,
                         "owner_mailing": ", ".join(x for x in (b.get("ST_ADDRESS"), b.get("CITY_STATE"), b.get("ZIP_CODE")) if x),
                         "context": label, "station": st if mi is not None and mi <= 0.5 else "", "station_mi": mi,
                         "zoning_map": lk.get("map"), "zoning_code": lk.get("ordinance"), "tags": tags})
        rows_by[key] = rows
        work[key] = datetime.now(timezone.utc).isoformat()
    merged = {}
    for rs in rows_by.values():
        for r in rs:
            k = r["pin"]
            if k in merged:
                merged[k]["tags"] = sorted(set(merged[k]["tags"]) | set(r["tags"]))
            else:
                merged[k] = dict(r)
    allrows = sorted(merged.values(), key=lambda r: (("REDEVELOPMENT AREA" in r["tags"]) + ("TRANSIT" in r["tags"]) +
                                                     ("REZONE INDUSTRIAL" in r["tags"]), r["acres"]), reverse=True)
    if len(work) < len(tasks) * 0.8 and out.get("rows"):
        print(f"NJ: only {len(work)} of {len(tasks)} areas scanned; keeping the previous file")
        return
    save("nj_north.json", {"generated_utc": datetime.now(timezone.utc).isoformat(), "areas_scanned": len(work),
                           "total": len(allrows), "rows": allrows[:2000],
                           "method": "NJ statewide parcels + MOD-IV. Kept: vacant, commercial and industrial lots of 0.15 acre or more "
                                     "(buildings worth under 75% of the land) inside a designated Area in Need of Redevelopment or "
                                     "within half a mile of a passenger rail station, in Bergen, Hudson, Essex, Passaic, Union and "
                                     "Morris counties. NJ has no statewide zoning map: each row links the municipality's zoning map "
                                     "and ordinance from the NJ DCA directory. Redevelopment plans can set density above base zoning."})


def main():
    global DEADLINE_S
    load_rail()
    env = os.environ.get("REGIONS", "").strip()
    if env:
        which = [r.strip() for r in env.split(",") if r.strip()]
    else:  # weekly cadence keeps runs short: Mon NJ, Tue Fairfield, Wed-Fri Westchester (rotating towns)
        which = {0: ["nj"], 1: ["fairfield"]}.get(datetime.now(timezone.utc).weekday(), ["westchester"])
    print("regions:", which)
    weight = {"westchester": 0.5, "fairfield": 0.15, "nj": 0.35}
    total = DEADLINE_S
    tw = sum(weight.get(r, 0.3) for r in which)
    for i, r in enumerate(which):
        DEADLINE_S = (time.time() - T0) + total * weight.get(r, 0.3) / tw  # weighted slice of the run
        try:
            {"westchester": scan_westchester, "fairfield": scan_fairfield, "nj": scan_nj}[r](*([{}] if r == "westchester" else []))
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
