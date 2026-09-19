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
  White House                 whitehouse.gov    presidential actions, fact sheets,
                                                statements — the first public record of
                                                a directive, ahead of everything else
  DoW releases RSS            war.gov           daily defense announcements
  USAspending API             api.usaspending.gov  actual federal obligations by recipient
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SEC_CONTACT = os.environ.get("SEC_CONTACT", "").strip()
# The SEC's access policy asks for "Company Name contact@domain"; anything else is
# refused outright, and datacenter ranges are refused on top of that. Set a
# SEC_CONTACT secret to an email to use it; otherwise a GitHub noreply address, which
# is a real deliverable contact for this repository.
SEC_UA = f"Signal Terminal {SEC_CONTACT or 'protrader1988@users.noreply.github.com'}"
UA = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate", "Accept": "application/json"}
SEC_HEADERS = {"User-Agent": SEC_UA, "Accept": "application/json",
               "Accept-Encoding": "gzip, deflate", "Accept-Language": "en-US,en;q=0.9",
               "Host": "www.sec.gov", "Connection": "keep-alive"}

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
        data = get_json("https://www.sec.gov/files/company_tickers.json",
                        headers=SEC_HEADERS, retries=3)
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
            data = get_json(url, retries=2, headers={**SEC_HEADERS, "Host": "efts.sec.gov",
                                                     "Referer": "https://www.sec.gov/edgar/search/",
                                                     "Origin": "https://www.sec.gov"})
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
            _mark("sec_edgar", False,
                  f"{str(e)[:90]} — detection continues on the award and news feeds")
        time.sleep(0.8)
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
    per_term_kept = 2
    for term in FR_TERMS:
        kept = 0
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
                if kept >= per_term_kept:
                    continue
                kept += 1
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
# The White House — where a directive is public before it is anything else
# ---------------------------------------------------------------------------
WH_SECTIONS = ["https://www.whitehouse.gov/news/",
               "https://www.whitehouse.gov/fact-sheets/",
               "https://www.whitehouse.gov/presidential-actions/",
               "https://www.whitehouse.gov/briefings-statements/"]

# Matching a single word like "invest" or "partnership" catches every fact sheet the
# White House publishes — those words are in the boilerplate of a saltwater angling
# announcement. A post earns its place here on evidence: an industry this desk tracks,
# an instrument that moves value, a dollar figure, or a company that resolves to a
# ticker. One generic word is not evidence.
WH_INDUSTRY = ("critical mineral", "rare earth", "semiconductor", "chips act", "chip",
               "shipbuilding", "shipyard", "nuclear", "reactor", "uranium", "drone",
               "unmanned", "quantum", "defense industrial", "munitions", "steel",
               "aluminum", "gallium", "tungsten", "lithium", "graphite", "magnet",
               "pharmaceutical", "onshoring", "reshoring", "supply chain")
WH_INSTRUMENT = ("equity stake", "equity investment", "warrants for", "warrants to purchase",
                 "penny warrant", "warrants exercisable", "offtake", "price floor",
                 "stockpile", "section 232", "tariff", "export control", "procurement award",
                 "public-private partnership", "loan guarantee", "defense production act",
                 "strategic capital", "joint venture", "most favored nation",
                 "most-favored-nation", "drug pricing", "price cap", "buy american")
WH_MONEY = re.compile(r"\$\s?\d[\d,.]*\s?(?:billion|million|trillion)?", re.I)

_WH_LINK = re.compile(
    r'href="(https://www\.whitehouse\.gov/(?:fact-sheets|presidential-actions|'
    r'briefings-statements|articles|remarks)/(\d{4})/(\d{2})/([a-z0-9\-]+)/)"',
    re.I)
_WH_DATE = re.compile(r'datetime="(\d{4}-\d{2}-\d{2})')
_WH_DATE_TEXT = re.compile(
    r'\b(January|February|March|April|May|June|July|August|September|October|'
    r'November|December)\s+(\d{1,2}),\s+(20\d\d)\b')
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}


def _slug_title(slug):
    t = slug.replace("-", " ").strip()
    return (t[:1].upper() + t[1:]) if t else slug


def _wh_article_date(url, cache={}):
    """Day-precision date, read from the article itself. The listing pages only carry
    year and month in the URL, and 'this happened sometime in September' is not a
    detection feed."""
    if url in cache:
        return cache[url]
    day = None
    try:
        html = _get(url, timeout=25).decode("utf-8", "ignore")
        m = _WH_DATE.search(html) or None
        if m:
            day = m.group(1)
        else:
            m2 = _WH_DATE_TEXT.search(html)
            if m2:
                day = f"{m2.group(3)}-{_MONTHS[m2.group(1)]:02d}-{int(m2.group(2)):02d}"
    except Exception:
        pass
    cache[url] = day
    return day


def _strip_html(html):
    html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#8217;", "'")
                .replace("&#8220;", '"').replace("&#8221;", '"').replace("&#039;", "'"))
    return re.sub(r"\s+", " ", html).strip()


def _wh_article(url, cache={}):
    """(date, text) for one White House post. The listing pages carry only year and
    month, and the companies are in the body rather than the headline, so the post
    itself is where both actually live."""
    if url in cache:
        return cache[url]
    date, text = None, ""
    try:
        html = _get(url, timeout=25).decode("utf-8", "ignore")
        m = _WH_DATE.search(html)
        if m:
            date = m.group(1)
        else:
            m2 = _WH_DATE_TEXT.search(html)
            if m2:
                date = f"{m2.group(3)}-{_MONTHS[m2.group(1)]:02d}-{int(m2.group(2)):02d}"
        text = _strip_html(html)[:6000]
    except Exception:
        pass
    cache[url] = (date, text)
    return date, text


def wh_evidence(text):
    """What in this post makes it market-relevant, and how strongly."""
    import tickers
    low = (text or "").lower()
    industry = [w for w in WH_INDUSTRY if w in low][:3]
    instrument = [w for w in WH_INSTRUMENT if w in low][:3]
    money = [m.strip() for m in WH_MONEY.findall(text or "")][:2] if text else []
    money = [m for m in WH_MONEY.finditer(text or "")][:2]
    money = [m.group(0).strip() for m in money]
    names = tickers.resolve(text)
    # Money alone is not evidence: every fact sheet cites a dollar figure somewhere.
    # A post has to name an industry this desk tracks, an instrument that moves value,
    # or a company that resolves to a ticker — "Signs Framework Agreement With Lockheed
    # Martin" is a federal deal with a listed counterparty and no industry word in it.
    qualifies = bool(industry or instrument or names)
    score = ((2 if industry else 0) + (2 if instrument else 0) +
             (1 if money else 0) + (2 if names else 0)) if qualifies else 0
    return {"industry": industry, "instrument": instrument, "money": money,
            "tickers": names[:4], "score": score}


def whitehouse(days=30, cap=30, fetch_bodies=24, min_score=3):
    """Presidential actions, fact sheets, briefings and articles, kept only when the
    post itself carries evidence: an industry this desk tracks, an instrument that
    moves value, a dollar figure, or a company that resolves to a ticker. For a sector
    directive this is the first public record — ahead of the Federal Register, which
    publishes the legal text days later.

    Bodies are fetched rather than headlines matched, because the headline of a fact
    sheet rarely names the company the money is going to."""
    seen, links = set(), []
    ok_any = False
    for page in WH_SECTIONS:
        try:
            html = _get(page, timeout=30).decode("utf-8", "ignore")
            ok_any = True
        except Exception as e:
            _mark("whitehouse", False, str(e)[:110])
            continue
        for m in _WH_LINK.finditer(html):
            url, yr, mo, slug = m.group(1), m.group(2), m.group(3), m.group(4)
            if url in seen:
                continue
            seen.add(url)
            links.append({"link": url, "month": f"{yr}-{mo}", "slug": slug,
                          "section": page.rstrip("/").rsplit("/", 1)[-1],
                          "title": _slug_title(slug)})
        time.sleep(0.4)

    links.sort(key=lambda x: x["month"], reverse=True)
    links = links[:cap]
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    out, bodies, scanned = [], 0, 0
    for it in links:
        date, text = (None, "")
        if bodies < fetch_bodies:
            date, text = _wh_article(it["link"])
            bodies += 1
            time.sleep(0.25)
        ev = wh_evidence((it["title"] + " ") + (text or ""))
        scanned += 1
        if ev["score"] < min_score:
            continue
        precision = "day"
        if not date:
            date, precision = it["month"] + "-01", "month"
        if date < cutoff:
            continue
        reason = " · ".join(filter(None, [
            ", ".join(ev["industry"]), ", ".join(ev["instrument"]), ", ".join(ev["money"])]))
        out.append({"title": it["title"], "company": it["title"], "link": it["link"],
                    "date": date, "date_precision": precision, "source": "White House",
                    "kind": it["section"].replace("-", " "),
                    "matched": reason or "policy", "evidence": ev,
                    "desc": (text or "")[:1200], "form": "white house"})
    if ok_any:
        _mark("whitehouse", True,
              f"{len(out)} of {scanned} posts cleared the evidence bar ({bodies} bodies read)")
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


def dow_contracts(cap=40, as_documents=True):
    """Daily DoW contract announcements. These post around 5pm ET and are the first
    public record of a lot of this money — but the feed publishes one digest per day
    with the detail behind the link, so keyword-matching the entry text finds nothing.
    Returned as documents to read rather than as per-company detections; USAspending
    is the detector that actually resolves to a recipient."""
    import feedparser
    url = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=40"
    try:
        f = feedparser.parse(_get(url, timeout=55))
        out = []
        for e in f.entries[:cap]:
            title = getattr(e, "title", "").strip()
            body = (getattr(e, "summary", "") or "")[:4000]
            hay = (title + " " + body).lower()
            hit = [w for w in DEAL_WORDS if w in hay]
            t = getattr(e, "published_parsed", None)
            out.append({"title": title[:140], "company": title[:140],
                        "link": getattr(e, "link", ""),
                        "date": datetime(*t[:6]).date().isoformat() if t else None,
                        "source": "DoW contracts", "kind": "contracts digest",
                        "matched": hit[0] if hit else "daily contracts",
                        "form": "contract"})
        _mark("dow_contracts", True, f"{len(out)} daily digests")
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


def _any_date(row):
    """USAspending returns different date fields per award type and drops the ones it
    does not recognise from the request, so scan for whatever date came back rather
    than betting on one spelling — but only fields describing when something HAPPENED.
    A contract's End Date is a performance deadline and can sit in 2100, which is how
    "last action" first came back reading like a prophecy."""
    best = None
    for k, v in (row or {}).items():
        kl = k.lower()
        if "date" not in kl or not v:
            continue
        if "end" in kl or "expir" in kl or "completion" in kl:
            continue
        sv = str(v)[:10]
        if len(sv) == 10 and sv <= datetime.now(timezone.utc).date().isoformat() \
                and (best is None or sv > best):
            best = sv
    return best


AGENCY_HINTS = ("defense", "war", "energy", "commerce", "interior")

# Management-and-operating contractors for national labs and cleanup sites take the
# largest awards in the system by an order of magnitude. They are consortia that run
# government facilities, not companies anyone can buy, and left in they crowd out
# every actual deal.
OPERATOR_PATTERNS = ("NATIONAL SECURITY", "MISSION COMPLETION", "SCIENCE ASSOCIATES",
                     "ENGINEERING SOLUTIONS", "BATTELLE", "ALLIANCE FOR", "FEDERAL PETROLEUM",
                     "LABORATOR", "UNIVERSIT", "RESEARCH CORPORATION", "UT-BATTELLE",
                     "NATIONAL TECHNOLOGY", "FEDERAL SERVICES", "SITE SERVICES",
                     "ENVIRONMENTAL MANAGEMENT", "LEGACY MANAGEMENT")


def is_facility_operator(name):
    n = (name or "").upper()
    return any(pat in n for pat in OPERATOR_PATTERNS)


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
        dropped_ops = 0
        for r in rows:
            name = (r.get("Recipient Name") or "").strip()
            agency = r.get("Awarding Agency") or ""
            if not name or not any(h in agency.lower() for h in AGENCY_HINTS):
                continue
            if is_facility_operator(name):
                dropped_ops += 1
                continue
            gid = r.get("generated_internal_id")
            out.append({
                "company": name,
                "amount": r.get("Award Amount"),
                "date": _any_date(r),
                "agency": agency,
                "desc": (r.get("Description") or "")[:160],
                "source": "USAspending",
                "matched": "award >= $%dM" % (min_amount // 1_000_000),
                "form": "award",
                "link": (f"https://www.usaspending.gov/award/{gid}" if gid else
                         "https://www.usaspending.gov/search?keywords=" + urllib.parse.quote(name)),
            })
        out.sort(key=lambda r: r.get("date") or "", reverse=True)
        _mark("usaspending_recent", True,
              f"{len(out)} of {len(rows)} awards over ${min_amount/1e6:.0f}M kept "
              f"({dropped_ops} facility operators dropped)")
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


def usaspending_awards(recipient, years=2, limit=100, max_pages=3):
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
        "fields": ["Award Amount", "Recipient Name", "Action Date", "Awarding Agency",
                   "Start Date", "End Date", "Last Modified Date"],
        "sort": "Award Amount", "order": "desc", "limit": limit, "page": 1,
        "subawards": False,
    }
    try:
        rows, page, exhausted = [], 1, False
        while page <= max_pages:               # 3 x 100 awards; enough for every name here
            payload["page"] = page
            data = post_json("https://api.usaspending.gov/api/v2/search/spending_by_award/", payload)
            batch = (data or {}).get("results", []) or []
            rows += batch
            meta = (data or {}).get("page_metadata") or {}
            if len(batch) < limit or not meta.get("hasNext", len(batch) == limit):
                exhausted = True
                break
            page += 1
        total = 0.0
        last = None
        agencies = {}
        for r in rows:
            amt = r.get("Award Amount") or 0
            try:
                total += float(amt)
            except Exception:
                pass
            d = _any_date(r)
            if d and (last is None or d > last):
                last = d
            ag = r.get("Awarding Agency")
            if ag:
                agencies[ag] = agencies.get(ag, 0) + 1
        top_agency = max(agencies, key=agencies.get) if agencies else None

        # The award list is capped, so its sum understates any large recipient. The
        # category endpoint aggregates server-side; prefer it and fall back to the sum,
        # saying on the page which of the two the number came from.
        agg, basis = None, ("sum of all %d matched awards" % len(rows) if exhausted
                            else "floor: first %d awards, more exist" % len(rows))
        try:
            cat = None
            for url, body in (
                ("https://api.usaspending.gov/api/v2/search/spending_by_category/",
                 {"category": "recipient", "filters": payload["filters"], "limit": 5, "page": 1}),
                ("https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/",
                 {"filters": payload["filters"], "limit": 5, "page": 1}),
            ):
                try:
                    cat = post_json(url, body, retries=0)
                    if cat:
                        break
                except Exception:
                    continue
            best = None
            for c in (cat or {}).get("results", []) or []:
                nm = (c.get("name") or "").upper()
                if recipient.upper().split()[0] in nm:
                    best = c
                    break
            if best and best.get("amount"):
                agg = float(best["amount"])
                basis = "aggregated by recipient"
            _mark("usaspending_agg", bool(agg is not None),
                  ("matched " + (best or {}).get("name", "")[:40]) if agg is not None
                  else "no recipient match in aggregate response")
        except Exception as e:
            _mark("usaspending_agg", False, str(e)[:110])

        keys = ",".join(sorted((rows[0] or {}).keys()))[:70] if rows else "no rows"
        _mark("usaspending", True, f"queried [{keys}]")
        return {"total": round(agg if agg is not None else total, 0), "count": len(rows),
                "last_action": last, "top_agency": top_agency,
                "truncated": agg is None and not exhausted, "basis": basis, "pages": page,
                "link": "https://www.usaspending.gov/search?keywords=" + urllib.parse.quote(recipient)}
    except Exception as e:
        _mark("usaspending", False, str(e)[:120])
        return None
