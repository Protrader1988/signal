"""
Reading layer for the Policy Desk — turns documents into structured deal records.

WHY THIS EXISTS
Keyword scoring answers "does this post mention tungsten". It cannot answer the
question that matters: who is the counterparty, what instrument did the government
actually take, how much, and is this a signed deal or a press release about a
future intention. That requires reading the document, so this module has a model
read it and return structured fields with the sentence it took them from.

WHAT IT WILL AND WILL NOT DO
Every record carries the quote it was extracted from and a confidence. Nothing it
produces enters the scored event study: extraction output is a PROPOSAL queue, and
a human moves an entry into the registry. The prompt forbids inference — if the
document does not state an amount, the amount is null rather than a guess — and the
output is validated field by field here, so a malformed or over-confident answer is
dropped rather than published.

Needs the free GEMINI_API_KEY the repo already uses for the market digest. Without
it the desk falls back to keyword detection and says so in source health.

Output: consumed by policy.py, published as policy.json["pipeline"]
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-3.6-pro", "gemini-2.5-flash"]
API_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

INSTRUMENTS = ["equity", "warrant", "convertible preferred", "price floor", "offtake",
               "procurement", "loan", "loan guarantee", "grant", "tariff", "export control",
               "revenue share", "stockpile", "golden share", "none"]
STAGES = ["rumored", "announced", "letter of intent", "definitive", "closed", "none"]

PROMPT = """You are a research analyst building a register of US federal government
transactions with companies. You are reading primary documents: White House posts,
Department of War releases, Federal Register documents, federal award records and
news headlines.

For EACH document, decide whether it describes the federal government taking a
financial position in, or a commercial commitment to, one or more specific companies
or a specific industry. Then extract only what the document actually says.

HARD RULES
- Never infer. If the document does not state an amount, use null. If it names no
  company, use an empty company list.
- Never use knowledge from outside the document. No filling in from memory.
- "quote" must be a verbatim span copied from the document, under 200 characters,
  that supports the extraction. If you cannot quote it, do not extract it.
- A generic policy statement with no money and no named industry is NOT a deal:
  return relevant=false for it.
- Distinguish carefully: an intention or a letter of intent is not a definitive
  agreement, and a closing is not a new deal.
- confidence: "high" only when the document states the counterparty, the instrument
  and the amount. "medium" when one is missing. "low" otherwise.

Return ONLY JSON in exactly this shape:
{"records":[{"id":"<the id given with the document>","relevant":true|false,
"companies":["exact names as written"],"sector":"short sector label or null",
"instrument":"one of: %s","amount_usd":<number or null>,
"stage":"one of: %s","agency":"agency named or null",
"summary":"one plain sentence on what the government is doing",
"quote":"verbatim span under 200 chars","confidence":"high|medium|low"}]}

Return one record per document, in the same order, using the ids given.

DOCUMENTS:
""" % ("|".join(INSTRUMENTS), "|".join(STAGES))


def _call(key, prompt, timeout=90):
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.1,
                                            "responseMimeType": "application/json"}}).encode()
    last = None
    for m in MODELS:
        try:
            req = urllib.request.Request(API_TMPL.format(m=m) + f"?key={key}", data=body,
                                         headers={"Content-Type": "application/json"})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            txt = json.loads(raw)["candidates"][0]["content"]["parts"][0]["text"]
            return txt, m
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            last = RuntimeError(f"{m}: HTTP {e.code} {detail[:120]}")
        except Exception as e:
            last = e
    raise last or RuntimeError("no model answered")


def _clean(rec, by_id, tickers, stats=None):
    """Validate one record. Anything that fails a check is dropped, not repaired —
    a half-parsed deal record is worse than no record. stats records WHY, because
    "no deals today" and "every record failed validation" look identical on the page
    and mean opposite things."""
    bump = (lambda k: stats.__setitem__(k, stats.get(k, 0) + 1)) if stats is not None else (lambda k: None)
    doc = by_id.get(str(rec.get("id")))
    if not doc:
        bump("unknown_id")
        return None
    if not rec.get("relevant"):
        bump("not_a_deal")
        return None
    quote = (rec.get("quote") or "").strip()
    text = doc.get("text") or ""
    # the quote has to actually be in the document, allowing for whitespace differences
    norm = lambda s: re.sub(r"\s+", " ", s or "").lower()
    if not quote or (norm(quote)[:120] not in norm(text) and norm(quote)[:60] not in norm(text)):
        bump("quote_not_in_document")
        return None
    instrument = (rec.get("instrument") or "none").lower()
    stage = (rec.get("stage") or "none").lower()
    if instrument not in INSTRUMENTS or stage not in STAGES:
        bump("bad_enum")
        return None
    if instrument == "none" and stage == "none":
        bump("no_instrument_or_stage")
        return None
    amount = rec.get("amount_usd")
    try:
        amount = float(amount) if amount is not None else None
    except Exception:
        amount = None
    companies = [c.strip() for c in (rec.get("companies") or []) if isinstance(c, str) and c.strip()][:5]
    resolved = []
    for c in companies:
        resolved += [t for t in tickers.resolve(c) if t not in resolved]
    if not resolved:
        resolved = tickers.resolve(" ".join(companies) + " " + (rec.get("summary") or ""))
    return {
        "id": str(rec.get("id")), "companies": companies, "tickers": resolved[:4],
        "sector": (rec.get("sector") or None), "instrument": instrument, "stage": stage,
        "amount_usd": amount, "agency": rec.get("agency") or doc.get("source"),
        "summary": (rec.get("summary") or "")[:300], "quote": quote[:220],
        "confidence": (rec.get("confidence") or "low").lower(),
        "date": doc.get("date"), "link": doc.get("link"), "source": doc.get("source"),
        "title": doc.get("title"),
    }


def extract(documents, tickers, cap=14, chars=2600):
    """documents: [{id, title, text, date, link, source}] → structured records.

    Returns (records, health) and never raises: the desk degrades to keyword
    detection rather than failing the run."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return [], {"ok": False, "note": "no GEMINI_API_KEY — keyword detection only"}
    docs = [d for d in documents if (d.get("text") or d.get("title"))][:cap]
    if not docs:
        return [], {"ok": True, "note": "nothing to read"}

    by_id, blocks = {}, []
    for i, d in enumerate(docs):
        did = str(i + 1)
        text = (d.get("text") or d.get("title") or "")[:chars]
        by_id[did] = {**d, "text": text}
        blocks.append(f"--- DOCUMENT {did} ({d.get('source', '')}, {d.get('date', '')}) ---\n"
                      f"TITLE: {d.get('title', '')}\n{text}")
    try:
        txt, model = _call(key, PROMPT + "\n\n".join(blocks))
    except Exception as e:
        return [], {"ok": False, "note": f"extraction failed: {str(e)[:110]}"}
    try:
        payload = json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return [], {"ok": False, "note": "model returned unparseable output"}
        try:
            payload = json.loads(m.group(0))
        except Exception:
            return [], {"ok": False, "note": "model returned unparseable output"}

    out, stats = [], {}
    for rec in (payload.get("records") or []):
        cleaned = _clean(rec, by_id, tickers, stats)
        if cleaned:
            out.append(cleaned)
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: (order.get(r["confidence"], 3), r.get("date") or ""), reverse=False)
    reasons = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(stats.items()))
    return out, {"ok": True, "model": model, "stats": stats,
                 "note": (f"{len(out)} records from {len(docs)} documents"
                          + (f" — {reasons}" if reasons else "")),
                 "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
