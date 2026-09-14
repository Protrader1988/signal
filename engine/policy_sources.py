"""
Primary-source fetchers for the Policy Desk. Free, public, no keys.

Every fetcher returns plain data and records its own failure in HEALTH instead of
raising, because a desk that silently shows an empty table when a feed is down is
indistinguishable from a desk with nothing to report. The site renders HEALTH, so
a broken source is visible rather than cosmetic.

Sources
  SEC EDGAR full-text search  efts.sec.gov      8-K/8-K/A text search — this is where
                                                a federal deal first appears with terms
  SEC company_tickers.json    sec.gov           CIK -> ticker resolution
  Federal Register API        federalregister.gov  EOs, proclamations, agency notices
  DoW releases RSS            war.gov           daily defense announcements
  USAspending API             api.usaspending.gov  actual federal obligations by recipient
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# SEC requires automated clients to identify themselves with a contact address and
# rejects everything else with a 403 — including, in practice, most datacenter IPs.
# Set a SEC_CONTACT repository secret (an email) to improve the odds; the desk works
# without EDGAR because the other detectors below do not depend on it.
SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
UA = {"User-Agent": (f"SignalTerminal/1.0 ({SEC_CONTACT})" if SEC_CONTACT
                     else "SignalTerminal/1.0 (+https://github.com/protrader1988/signal)"),
      "Accept-Encoding": "gzip, deflate", "Accept": "application/json"}

HEALTH = {}


def _mark(name, ok, note=""):
    HEALTH[name] = {"ok": bool(ok), "note": note,
                    "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return raw


def get_json(url, headers=None, timeout=25, retries=2):
    for i in range(retries + 1):
        try:
            return json.loads(_get(url, headers, timeout))
        except Exception as e:
            if i == retries:
                raise
            time.sleep(1.5 * (i + 1))
    return None


def post_json(url, payload, timeout=30, retries=1):
    body = json.dumps(payload).encode()
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if i == retries:
                raise
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# SEC
# ---------------------------------------------------------------------------
def company_tickers():
    """{cik_int: TICKER}. Used to turn an EDGAR hit into something tradeable."""
    try:
        data = get_json("https://www.sec.gov/files/company_tickers.json")
        out = {}
        for row in (data or {}).values():
            try:
                out[int(row["cik_str"])] = row["ticker"].upper()
            except Exception:
                continue
        _mark("sec_tickers", bool(out), f"{len(out)} tickers")
        return out
    except Exception as e:
        _mark("sec_tickers", False, str(e)[:120])
        return {}


EDGAR_QUERIES = [
    # phrases that appear in the filings these deals produce, not generic policy words
    ('"Department of War" "warrant"', "DoW warrant"),
    ('"Office of Strategic Capital"', "OSC investment"),
    ('"CHIPS Incentives" "equity"', "CHIPS equity"),
    ('"United States government" "preferred stock" "purchase agreement"', "USG preferred"),
    ('"Defense Production Act" "purchase commitment"', "DPA commitment"),
    ('"offtake agreement" "Department of Defense"', "DoD offtake"),
]


def edgar_fulltext(days=45, forms="8-K", cap_per_query=10):
    """EDGAR full-text search. Real filings, with the terms of the deal in them.

    This is the detection layer: a federal transaction that matters to shareholders
    is an 8-K, and it is searchable within minutes of filing."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    until = datetime.now(timezone.utc).date().isoformat()
    hits, ok_any = [], False
    for q, label in EDGAR_QUERIES:
        url = ("https://efts.sec.gov/LATEST/search-index?q=" + urllib.parse.quote(q) +
               f"&forms={urllib.parse.quote(forms)}&dateRange=custom"
               f"&startdt={since}&enddt={until}")
        try:
            data = get_json(url, headers={"Referer": "https://www.sec.gov/edgar/search/"})
            ok_any = True
            for h in ((data or {}).get("hits", {}).get("hits", []) or [])[:cap_per_query]:
                src = h.get("_source", {}) or {}
                ciks = src.get("ciks") or []
                cik = None
                try:
                    cik = int(ciks[0])
                except Exception:
                    pass
                adsh = (h.get("_id", "") or "").split(":")[0].replace("-", "")
                link = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/" if cik and adsh
                        else "https://www.sec.gov/edgar/search/")
                hits.append({
                    "company": (src.get("display_names") or ["unknown"])[0],
                    "cik": cik,
                    "form": (src.get("root_forms") or [forms])[0],
                    "date": src.get("file_date"),
                    "link": link,
                    "matched": label,
                })
        except Exception as e:
            _mark("sec_edgar", False, str(e)[:120])
        time.sleep(0.4)
    if ok_any:
        _mark("sec_edgar", True, f"{len(hits)} filings across {len(EDGAR_QUERIES)} queries")
    return hits


# ---------------------------------------------------------------------------
# Federal Register
# ---------------------------------------------------------------------------
FR_TERMS = ["critical minerals", "semiconductor", "shipbuilding", "nuclear",
            "export control", "tariff"]


def federal_register(days=30, per_term=4):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    out, ok_any = [], False
    for term in FR_TERMS:
        url = ("https://www.federalregister.gov/api/v1/documents.json"
               f"?per_page={per_term}&order=newest"
               "&fields[]=title&fields[]=html_url&fields[]=publication_date"
               "&fields[]=type&fields[]=agencies"
               f"&conditions[term]={urllib.parse.quote(term)}"
               f"&conditions[publication_date][gte]={since}")
        try:
            data = get_json(url)
            ok_any = True
            for d in (data or {}).get("results", []):
                ag = ", ".join(a.get("name", "") for a in (d.get("agencies") or [])[:2])
                title = d.get("title", "")
                # a full-text hit on "nuclear" inside a Sunshine Act meeting notice is not
                # policy news; keep presidential documents, or documents whose own title
                # carries the term
                presidential = "presidential" in (d.get("type", "") or "").lower()
                if not presidential and term.lower() not in title.lower():
                    continue
                out.append({"title": title, "link": d.get("html_url", ""),
                            "date": d.get("publication_date"), "source": ag or "Federal Register",
                            "kind": d.get("type", ""), "matched": term})
        except Exception as e:
            _mark("federal_register", False, str(e)[:120])
        time.sleep(0.3)
    if ok_any:
        _mark("federal_register", True, f"{len(out)} documents")
    return out


# ---------------------------------------------------------------------------
# Department of War releases
# ---------------------------------------------------------------------------
def dow_releases(cap=10):
    import feedparser
    url = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=20"
    try:
        f = feedparser.parse(_get(url, timeout=25))
        items = []
        for e in f.entries[:cap]:
            t = getattr(e, "published_parsed", None)
            items.append({"title": getattr(e, "title", "").strip(),
                          "link": getattr(e, "link", ""),
                          "date": datetime(*t[:6]).date().isoformat() if t else None,
                          "source": "Dept. of War", "kind": "release", "matched": "defense"})
        _mark("dow_rss", bool(items), f"{len(items)} releases")
        return [i for i in items if i["title"]]
    except Exception as e:
        _mark("dow_rss", False, str(e)[:120])
        return []


# ---------------------------------------------------------------------------
# USAspending — real federal money, by recipient
# ---------------------------------------------------------------------------
DEAL_WORDS = ("equity", "warrant", "stake", "investment", "offtake", "price floor",
              "strategic capital", "critical mineral", "stockpile", "preferred stock",
              "public-private", "partnership")


def dow_contracts(cap=40):
    """Daily DoW contract announcements. These post around 5pm ET and are the first
    public record of a lot of this money — often the same day as the company's 8-K."""
    import feedparser
    url = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=40"
    try:
        f = feedparser.parse(_get(url, timeout=25))
        out = []
        for e in f.entries[:cap]:
            title = getattr(e, "title", "").strip()
            body = (getattr(e, "summary", "") or "")[:4000]
            hay = (title + " " + body).lower()
            hit = [w for w in DEAL_WORDS if w in hay]
            if not hit:
                continue
            t = getattr(e, "published_parsed", None)
            out.append({"company": title[:120], "link": getattr(e, "link", ""),
                        "date": datetime(*t[:6]).date().isoformat() if t else None,
                        "source": "DoW contracts", "matched": hit[0], "form": "contract"})
        _mark("dow_contracts", True, f"{len(out)} matching announcements")
        return out
    except Exception as e:
        _mark("dow_contracts", False, str(e)[:120])
        return []


def _first(row, *keys):
    for k in keys:
        v = row.get(k)
        if v:
            return v
    return None


AGENCY_HINTS = ("defense", "war", "energy", "commerce", "interior")


def usaspending_recent(days=45, min_amount=50_000_000, limit=80):
    """Large recent federal awards, any recipient. This is the detector that does not
    depend on anyone writing a press release: the money is filed whether or not it is
    announced, and a new nine-figure recipient in these agencies is worth a look."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    # No agency filter in the request: USAspending matches toptier agency names exactly
    # and the spellings shift ("Department of Defense" vs "Department of Defense (DOD)"),
    # which silently returns nothing. Ask broadly, filter the agency string here.
    payload = {
        "filters": {
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
            "award_type_codes": ["A", "B", "C", "D"],
            "award_amounts": [{"lower_bound": min_amount}],
        },
        "fields": ["Award Amount", "Recipient Name", "Awarding Agency", "Description",
                   "Start Date", "Last Modified Date", "generated_internal_id"],
        "sort": "Award Amount", "order": "desc", "limit": limit, "page": 1, "subawards": False,
    }
    try:
        data = post_json("https://api.usaspending.gov/api/v2/search/spending_by_award/", payload)
        rows = (data or {}).get("results", []) or []
        out = []
        for r in rows:
            name = (r.get("Recipient Name") or "").strip()
            agency = r.get("Awarding Agency") or ""
            if not name or not any(h in agency.lower() for h in AGENCY_HINTS):
                continue
            gid = r.get("generated_internal_id")
            out.append({
                "company": name,
                "amount": r.get("Award Amount"),
                "date": _first(r, "Start Date", "Last Modified Date", "Action Date"),
                "agency": agency,
                "desc": (r.get("Description") or "")[:160],
                "source": "USAspending",
                "matched": "award >= $%dM" % (min_amount // 1_000_000),
                "form": "award",
                "link": (f"https://www.usaspending.gov/award/{gid}" if gid else
                         "https://www.usaspending.gov/search?keywords=" + urllib.parse.quote(name)),
            })
        _mark("usaspending_recent", True,
              f"{len(out)} of {len(rows)} awards over ${min_amount/1e6:.0f}M in scope agencies")
        return out
    except Exception as e:
        _mark("usaspending_recent", False, str(e)[:120])
        return []


NEWS_DETECT = [
    ("equity stake", '"equity stake" ("Department of War" OR "Commerce Department") company'),
    ("federal investment", '("Office of Strategic Capital" OR "Defense Production Act") investment company'),
    ("offtake", '"offtake agreement" government critical minerals company'),
]


def news_detect(cap=5):
    """Backstop detector over free news RSS. Lowest evidentiary quality of the three,
    and labelled as such wherever it surfaces."""
    import feedparser
    out = []
    try:
        for label, q in NEWS_DETECT:
            url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) +
                   "&hl=en-US&gl=US&ceid=US:en")
            f = feedparser.parse(_get(url, timeout=20))
            for e in f.entries[:cap]:
                title = getattr(e, "title", "").strip()
                srcname = ""
                if getattr(e, "source", None) and getattr(e.source, "title", None):
                    srcname = e.source.title
                if not srcname and " - " in title:
                    title, srcname = title.rsplit(" - ", 1)
                t = getattr(e, "published_parsed", None)
                out.append({"company": title[:140], "link": getattr(e, "link", ""),
                            "date": datetime(*t[:6]).date().isoformat() if t else None,
                            "source": srcname or "news", "matched": label, "form": "headline"})
            time.sleep(0.3)
        _mark("news_detect", True, f"{len(out)} headlines")
    except Exception as e:
        _mark("news_detect", False, str(e)[:120])
    return out


def usaspending_awards(recipient, years=2, limit=100):
    """Prime contract + grant awards whose recipient name matches. Returns total
    obligations, count, most recent action date and a verification link.

    Caveat carried through to the site: this is a NAME match against the recipient
    field, so subsidiaries filed under other names are missed and same-name entities
    can be over-counted. It is an order-of-magnitude exposure read, not accounting."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=365 * years)
    payload = {
        "filters": {
            "recipient_search_text": [recipient],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
            "award_type_codes": ["A", "B", "C", "D"],
        },
        "fields": ["Award Amount", "Recipient Name", "Action Date", "Awarding Agency"],
        "sort": "Award Amount", "order": "desc", "limit": limit, "page": 1,
        "subawards": False,
    }
    try:
        data = post_json("https://api.usaspending.gov/api/v2/search/spending_by_award/", payload)
        rows = (data or {}).get("results", []) or []
        total = 0.0
        last = None
        agencies = {}
        for r in rows:
            amt = r.get("Award Amount") or 0
            try:
                total += float(amt)
            except Exception:
                pass
            d = _first(r, "Start Date", "Last Modified Date", "Action Date", "End Date")
            if d and (last is None or str(d) > str(last)):
                last = str(d)[:10]
            ag = r.get("Awarding Agency")
            if ag:
                agencies[ag] = agencies.get(ag, 0) + 1
        top_agency = max(agencies, key=agencies.get) if agencies else None

        # The award list is capped, so its sum understates any large recipient. The
        # category endpoint aggregates server-side; prefer it and fall back to the sum,
        # saying on the page which of the two the number came from.
        agg, basis = None, "sum of top %d awards" % limit
        try:
            cat = post_json("https://api.usaspending.gov/api/v2/search/spending_by_category/",
                            {"category": "recipient", "filters": payload["filters"],
                             "limit": 5, "page": 1})
            best = None
            for c in (cat or {}).get("results", []) or []:
                nm = (c.get("name") or "").upper()
                if recipient.upper().split()[0] in nm:
                    best = c
                    break
            if best and best.get("amount"):
                agg = float(best["amount"])
                basis = "aggregated by recipient"
        except Exception:
            pass

        _mark("usaspending", True, "queried")
        return {"total": round(agg if agg is not None else total, 0), "count": len(rows),
                "last_action": last, "top_agency": top_agency,
                "truncated": agg is None and len(rows) >= limit, "basis": basis,
                "link": "https://www.usaspending.gov/search?keywords=" + urllib.parse.quote(recipient)}
    except Exception as e:
        _mark("usaspending", False, str(e)[:120])
        return None
