"""
Probe free public parcel/sales data availability for the target geography:
NYC, Westchester/Putnam/Dutchess (NY), CT towns, northern NJ towns.
Runs on GitHub Actions (full internet). Writes research/output/re_probe.json + log.
"""
import json, urllib.request, urllib.parse, traceback
from datetime import datetime, timezone

UA={"User-Agent":"Mozilla/5.0 (compatible; ResearchProbe/1.0)","Accept":"application/json"}
def get(url, timeout=25):
    req=urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()

def try_json(name, url, note=""):
    try:
        raw=get(url)
        data=json.loads(raw)
        n=len(data) if isinstance(data,list) else (len(data.get("features",[])) if isinstance(data,dict) and "features" in data else 1)
        sample=None
        if isinstance(data,list) and data: sample=list(data[0].keys())[:12]
        elif isinstance(data,dict) and data.get("features"): sample=list(data["features"][0].get("attributes",data["features"][0].get("properties",{})).keys())[:12]
        elif isinstance(data,dict): sample=list(data.keys())[:12]
        print(f"OK   {name}: rows/keys={n} sample={sample}")
        return {"name":name,"ok":True,"note":note,"rows":n,"sample_fields":sample,"url":url[:140]}
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__} {str(e)[:120]}")
        return {"name":name,"ok":False,"note":note,"error":f"{type(e).__name__}: {str(e)[:160]}","url":url[:140]}

def main():
    R=[]
    Q=urllib.parse.quote
    # --- NYC core ---
    R.append(try_json("NYC PLUTO (Socrata 64uk-42ks)","https://data.cityofnewyork.us/resource/64uk-42ks.json?$limit=1","parcels+zoning+FAR"))
    R.append(try_json("NYC ACRIS master (bnx9-e6tj)","https://data.cityofnewyork.us/resource/bnx9-e6tj.json?$limit=1","deeds/mortgages"))
    R.append(try_json("NYC DOF rolling sales (usep-8jbt)","https://data.cityofnewyork.us/resource/usep-8jbt.json?$limit=1","recent sales"))
    R.append(try_json("NYC DOF assessments (yjxr-fw8i)","https://data.cityofnewyork.us/resource/yjxr-fw8i.json?$limit=1","assessed values"))
    R.append(try_json("NYC city-owned (caiw-33pf)","https://data.cityofnewyork.us/resource/caiw-33pf.json?$limit=1","IPIS city property"))
    # --- NYS (Westchester, Putnam, Dutchess) ---
    R.append(try_json("NYS tax parcels (data.ny.gov 7vem-aaz7) Westchester",
        "https://data.ny.gov/resource/7vem-aaz7.json?$limit=1&county_name=Westchester","NYS parcel/assessment"))
    R.append(try_json("NYS tax parcels Putnam",
        "https://data.ny.gov/resource/7vem-aaz7.json?$limit=1&county_name=Putnam","Putnam coverage"))
    R.append(try_json("NYS tax parcels Dutchess",
        "https://data.ny.gov/resource/7vem-aaz7.json?$limit=1&county_name=Dutchess","Dutchess coverage"))
    R.append(try_json("Westchester GIS parcels (ArcGIS)",
        "https://giswww.westchestergov.com/arcgis/rest/services?f=json","county GIS services"))
    # --- CT ---
    R.append(try_json("CT open data catalog search 'parcel'",
        "https://api.us.socrata.com/api/catalog/v1?domains=data.ct.gov&q=parcel&limit=5","find CT parcel datasets"))
    R.append(try_json("CT statewide parcels (ArcGIS CTmaps)",
        "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services?f=json","CT geodata services"))
    R.append(try_json("CT OPM parcels alt (cteco)",
        "https://cteco.uconn.edu/ctmaps/rest/services?f=json","CTECO services"))
    # --- NJ ---
    R.append(try_json("NJGIN ArcGIS services root",
        "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services?f=json","NJ hosted layers"))
    R.append(try_json("NJ parcels/MOD-IV composite query (Fort Lee test)",
        "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/NJ_Parcels_and_MOD4_Composite/FeatureServer/0/query?where=" + Q("MUN_NAME LIKE '%FORT LEE%'") + "&outFields=*&resultRecordCount=1&f=json","NJ parcels+assessments"))
    R.append(try_json("NJGIN open data catalog",
        "https://api.us.socrata.com/api/catalog/v1?domains=njgin.nj.gov&q=parcel&limit=3","catalog check"))
    # --- Zoning atlas (regional) ---
    R.append(try_json("National Zoning Atlas API check",
        "https://www.zoningatlas.org/api","zoning atlas availability"))

    out={"generated_utc":datetime.now(timezone.utc).isoformat(),"results":R,
         "ok_count":sum(1 for r in R if r["ok"]),"total":len(R)}
    json.dump(out,open("research/output/re_probe.json","w"),indent=2)
    print(f"\n{out['ok_count']}/{out['total']} endpoints OK")

if __name__=="__main__":
    try: main()
    except Exception:
        open("research/output/RE_PROBE_ERROR.txt","w").write(traceback.format_exc())
