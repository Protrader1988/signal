"""
Market news — REAL headlines only (runs daily on Actions).

Pulls real articles from free news RSS (Google News) for the general market and
for the tickers currently in your signals/watchlist. Outputs real titles,
sources, and links — nothing is AI-generated or fabricated. (The old app's
fake-news generator is deliberately NOT reproduced.)

This is situational awareness, not a predictive edge — markets price news fast.

Output: site/data/news.json
"""
import json, urllib.parse, urllib.request, traceback, time
from datetime import datetime, timezone
import feedparser

GENERAL = [
    ("Markets", "stock market today"),
    ("Fed / rates", "Federal Reserve interest rates"),
    ("Economy", "US economy inflation jobs"),
]
UA = {"User-Agent": "Mozilla/5.0 (compatible; SignalTerminal/1.0)"}

def gnews(query):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=20).read()
        return feedparser.parse(raw)
    except Exception:
        return feedparser.parse("")

def clean_source(entry):
    src = ""
    if getattr(entry, "source", None) and getattr(entry.source, "title", None):
        src = entry.source.title
    return src

def iso(entry):
    t = getattr(entry, "published_parsed", None)
    if t:
        return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
    return None

def collect(feed, tag, cap):
    out = []
    for e in feed.entries[:cap]:
        title = getattr(e, "title", "").strip()
        # Google News titles are "Headline - Source"; split source out
        src = clean_source(e)
        if not src and " - " in title:
            title, src = title.rsplit(" - ", 1)
        out.append({"title": title, "source": src or "news", "link": getattr(e, "link", ""),
                    "published": iso(e), "tag": tag})
    return out

def load_tickers():
    try:
        s = json.load(open("site/data/signals.json"))
        tk = [x["ticker"] for x in s.get("position", [])[:8]] + [x["ticker"] for x in s.get("swing", [])[:6]]
        return list(dict.fromkeys(tk))
    except Exception:
        return ["AAPL", "NVDA", "MSFT"]

def main():
    import os; os.makedirs("site/data", exist_ok=True)
    items = []
    for tag, q in GENERAL:
        items += collect(gnews(q), tag, 5)
        time.sleep(0.3)
    for tk in load_tickers():
        items += collect(gnews(f"{tk} stock"), tk, 2)
        time.sleep(0.25)
    # dedupe by title, keep newest first
    seen = set(); dedup = []
    for it in sorted(items, key=lambda x: x["published"] or "", reverse=True):
        k = it["title"].lower()[:80]
        if k in seen or not it["title"]:
            continue
        seen.add(k); dedup.append(it)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "label": "Real headlines from public news feeds — situational awareness, not a predictive signal.",
           "count": len(dedup), "items": dedup[:28]}
    json.dump(out, open("site/data/news.json", "w"), indent=2)
    print(f"news: {len(dedup)} unique headlines")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        open("site/data/NEWS_ERROR.txt", "w").write(traceback.format_exc()); print("FATAL", e)
