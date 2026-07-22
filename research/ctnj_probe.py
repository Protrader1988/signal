"""Discover working CT + NJ parcel service endpoints/layers for the next engines."""
import json, urllib.request, urllib.parse, traceback

UA={"User-Agent":"Mozilla/5.0 (compatible; ResearchProbe/1.0)"}
def get(url,timeout=30):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=timeout).read())

OUT=[]
def log(*a):
    line=" ".join(str(x) for x in a); print(line); OUT.append(line)

def list_services(root,label):
    try:
        d=get(root+"?f=json")
        svcs=[s.get("name","") for s in d.get("services",[])]
        folders=d.get("folders",[])
        log(f"[{label}] {len(svcs)} services, folders={folders[:12]}")
        hits=[s for s in svcs if any(k in s.lower() for k in ("parcel","cama","assess","property","tax"))]
        log(f"[{label}] parcel-ish: {hits[:12]}")
        return folders,hits
    except Exception as e:
        log(f"[{label}] FAIL {type(e).__name__} {str(e)[:100]}"); return [],[]

def probe_layer(url,label):
    try:
        d=get(url+"?f=json")
        lyrs=[(l.get("id"),l.get("name")) for l in d.get("layers",[])][:10]
        log(f"[{label}] layers: {lyrs}")
        if lyrs:
            l0=get(f"{url}/{lyrs[0][0]}?f=json")
            fields=[f.get("name") for f in l0.get("fields",[])][:25]
            log(f"[{label}] layer{lyrs[0][0]} fields: {fields}")
        return True
    except Exception as e:
        log(f"[{label}] FAIL {str(e)[:100]}"); return False

def main():
    # CT candidates
    for root,lab in [
        ("https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services","CT services1"),
        ("https://cteco.uconn.edu/ctmaps/rest/services","CTECO"),
    ]:
        folders,hits=list_services(root,lab)
        for h in hits[:3]:
            probe_layer(f"{root}/{h}/FeatureServer" if "services1" in root else f"{root}/{h}/MapServer", f"{lab}:{h}")
        for f in folders[:6]:
            try:
                d=get(f"{root}/{f}?f=json")
                svcs=[s.get("name","") for s in d.get("services",[])]
                ph=[s for s in svcs if any(k in s.lower() for k in ("parcel","cama","property"))]
                if ph: log(f"[{lab}/{f}] parcel-ish: {ph[:8]}")
                for h in ph[:2]:
                    probe_layer(f"{root}/{h}/MapServer", f"{lab}:{h}")
            except Exception as e:
                log(f"[{lab}/{f}] {str(e)[:60]}")
    # CT socrata catalog
    try:
        d=get("https://api.us.socrata.com/api/catalog/v1?domains=data.ct.gov&q=parcel&limit=8")
        for r in d.get("results",[]):
            res=r.get("resource",{})
            log(f"[data.ct.gov] {res.get('id')} · {res.get('name')} · type={res.get('type')}")
    except Exception as e: log(f"[data.ct.gov] {e}")
    # NJ candidates
    folders,hits=list_services("https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services","NJGIN")
    for h in hits[:6]:
        probe_layer(f"https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/{h}/FeatureServer", f"NJ:{h}")
    open("research/output/ctnj_probe.txt","w").write("\n".join(OUT))

if __name__=="__main__":
    try: main()
    except Exception:
        open("research/output/CTNJ_ERROR.txt","w").write(traceback.format_exc())
        open("research/output/ctnj_probe.txt","w").write("\n".join(OUT))
