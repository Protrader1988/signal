"""
Signal — paper trading on Alpaca (PAPER ACCOUNT ONLY).

Runs right after the signal engine. It makes the Alpaca paper account mirror the
ledger's open POSITION book:

  BUY   every ticker the ledger holds open in the position book that the paper
        account does not hold (and has no pending order for).
  SELL  every ticker the paper account holds that the ledger no longer holds
        open (stopped out, or reached its time limit).

Orders are plain market orders, whole shares, good for the day. The engine runs
after the US close, so they queue and fill at the NEXT session's open - the
same entry the ledger's v2 rules assume.

Sizing: each new position gets  equity x position_exposure / number_of_open_names,
capped by the cash actually available. No margin, no shorting, no leverage.

Safety:
  - The endpoint is hard-coded to Alpaca's PAPER host. This script cannot reach a
    live account even if live keys are supplied; live keys are simply rejected.
  - Keys come only from the ALPACA_API_KEY / ALPACA_SECRET_KEY environment
    variables (GitHub Actions secrets). They are never written to disk or logs.
  - If keys are missing or rejected the script logs it and exits cleanly.

Output: site/data/paper.json  (account snapshot, positions, recent orders,
daily equity history). Real paper fills - not tracked signals.
"""

import json, os, sys, traceback, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://paper-api.alpaca.markets"          # PAPER ONLY. Do not change.
DATA_DIR = "site/data"
MAX_NEW_ORDERS_PER_RUN = 25
MIN_ORDER_DOLLARS = 50.0

def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)

def make_api(key, secret):
    assert "paper-api" in BASE, "refusing to run against a non-paper endpoint"
    def api(method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method,
              data=(json.dumps(body).encode() if body is not None else None),
              headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                       "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode() or "null"
                return r.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            try: detail = json.loads(e.read().decode() or "null")
            except Exception: detail = None
            return e.code, detail
    return api

def load(path, default):
    try: return json.load(open(path))
    except Exception: return default

def fnum(x, d=0.0):
    try: return float(x)
    except Exception: return d

def run(api, signals, ledger, state, today_utc):
    """Pure decision + execution. `api` is injectable so this can be tested offline."""
    out = {"updated_utc": today_utc, "mode": "paper", "broker": "Alpaca (paper account)",
           "started": state.get("started") or today_utc[:10], "errors": []}

    code, acct = api("GET", "/v2/account")
    if code != 200 or not isinstance(acct, dict):
        out["status"] = "keys_rejected" if code in (401, 403) else f"account_error_{code}"
        out["errors"].append("Alpaca did not accept the keys. Check that they are PAPER keys." if code in (401, 403)
                             else f"Could not read the account (HTTP {code}).")
        log(out["errors"][-1]); return out
    if acct.get("trading_blocked") or acct.get("account_blocked"):
        out["status"] = "account_blocked"; out["errors"].append("The paper account is blocked from trading."); return out

    equity = fnum(acct.get("equity")); cash = fnum(acct.get("cash"))
    _, positions = api("GET", "/v2/positions"); positions = positions if isinstance(positions, list) else []
    _, open_orders = api("GET", "/v2/orders?status=open&limit=200"); open_orders = open_orders if isinstance(open_orders, list) else []
    held = {p["symbol"]: p for p in positions}
    pending_buy = {o["symbol"] for o in open_orders if o.get("side") == "buy"}
    pending_sell = {o["symbol"] for o in open_orders if o.get("side") == "sell"}

    # target = the ledger's open position book (crypto and the retired swing book are never traded here)
    book = [s for s in ledger.get("open", []) if s.get("book") == "position" and "-USD" not in s.get("ticker", "")]
    target = {s["ticker"]: s for s in book}
    exposure = min(1.0, max(0.0, fnum(signals.get("position_suggested_exposure"), 0.0)))
    placed = []

    # 1) SELL what the ledger no longer holds
    for sym, p in held.items():
        if sym in target or sym in pending_sell: continue
        qty = p.get("qty")
        c, r = api("POST", "/v2/orders", {"symbol": sym, "qty": str(qty), "side": "sell", "type": "market", "time_in_force": "day"})
        ok = c in (200, 201)
        placed.append({"symbol": sym, "side": "sell", "qty": fnum(qty), "ok": ok, "reason": "closed in ledger",
                       "error": None if ok else (r or {}).get("message", f"HTTP {c}") if isinstance(r, dict) else f"HTTP {c}"})
        log(f"SELL {sym} x{qty} -> {'ok' if ok else 'FAILED'}")

    # 2) BUY what the ledger holds and the account does not
    to_buy = [t for t in target if t not in held and t not in pending_buy]
    n_names = max(1, len(target))
    per_name = equity * exposure / n_names
    # cash already spoken for by queued buys
    committed = sum(fnum(o.get("qty")) * fnum(target.get(o["symbol"], {}).get("last_price")) for o in open_orders if o.get("side") == "buy")
    budget = max(0.0, cash - committed) * 0.98
    for sym in to_buy[:MAX_NEW_ORDERS_PER_RUN]:
        px = fnum(target[sym].get("last_price")) or fnum(target[sym].get("entry_price"))
        dollars = min(per_name, budget)
        if px <= 0 or dollars < MIN_ORDER_DOLLARS:
            placed.append({"symbol": sym, "side": "buy", "qty": 0, "ok": False, "reason": "new in ledger", "error": "not enough cash for this position"})
            continue
        qty = int(dollars // px)
        if qty < 1:
            placed.append({"symbol": sym, "side": "buy", "qty": 0, "ok": False, "reason": "new in ledger",
                           "error": f"one share (${px:,.0f}) costs more than the ${dollars:,.0f} allotted"})
            continue
        c, r = api("POST", "/v2/orders", {"symbol": sym, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day"})
        ok = c in (200, 201)
        if ok: budget -= qty * px
        placed.append({"symbol": sym, "side": "buy", "qty": qty, "ok": ok, "reason": "new in ledger", "est_dollars": round(qty * px, 2),
                       "error": None if ok else ((r or {}).get("message", f"HTTP {c}") if isinstance(r, dict) else f"HTTP {c}")})
        log(f"BUY {sym} x{qty} (~${qty*px:,.0f}) -> {'ok' if ok else 'FAILED'}")

    # 3) snapshot for the site (no secrets, no account numbers)
    _, recent = api("GET", "/v2/orders?status=all&limit=50&direction=desc"); recent = recent if isinstance(recent, list) else []
    _, hist = api("GET", "/v2/account/portfolio/history?period=6M&timeframe=1D"); hist = hist if isinstance(hist, dict) else {}
    start_equity = state.get("start_equity") or equity
    out.update({
        "status": "ok", "start_equity": round(start_equity, 2),
        "account": {"equity": round(equity, 2), "cash": round(cash, 2),
                    "invested": round(sum(fnum(p.get("market_value")) for p in positions), 2),
                    "return_since_start_pct": round((equity / start_equity - 1) * 100, 2) if start_equity else 0.0},
        "sizing": {"exposure": exposure, "names": len(target), "dollars_per_name": round(per_name, 2)},
        "positions": [{"symbol": p["symbol"], "qty": fnum(p.get("qty")), "avg_entry": round(fnum(p.get("avg_entry_price")), 2),
                       "price": round(fnum(p.get("current_price")), 2), "value": round(fnum(p.get("market_value")), 2),
                       "pl": round(fnum(p.get("unrealized_pl")), 2), "pl_pct": round(fnum(p.get("unrealized_plpc")) * 100, 2)}
                      for p in sorted(positions, key=lambda p: -fnum(p.get("market_value")))],
        "orders_this_run": placed,
        "recent_orders": [{"symbol": o.get("symbol"), "side": o.get("side"), "qty": fnum(o.get("qty")), "status": o.get("status"),
                           "submitted": (o.get("submitted_at") or "")[:10], "filled_qty": fnum(o.get("filled_qty")),
                           "fill_price": round(fnum(o.get("filled_avg_price")), 2) if o.get("filled_avg_price") else None}
                          for o in recent[:30]],
        "equity_history": [{"date": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), "equity": round(fnum(e), 2)}
                           for t, e in zip(hist.get("timestamp") or [], hist.get("equity") or []) if e],
        "note": "Real fills in an Alpaca PAPER account (simulated money). The account mirrors the ledger's open position book; "
                "orders are placed after the close and fill at the next open.",
    })
    return out

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    key = os.environ.get("ALPACA_API_KEY", "").strip(); secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    now = datetime.now(timezone.utc).isoformat()
    state = load(f"{DATA_DIR}/paper.json", {})
    if not key or not secret:
        log("no Alpaca keys in the environment - paper trading skipped")
        json.dump({"updated_utc": now, "mode": "paper", "status": "no_keys", "started": state.get("started"),
                   "errors": ["The ALPACA_API_KEY / ALPACA_SECRET_KEY secrets are not available to this run."]},
                  open(f"{DATA_DIR}/paper.json", "w"), indent=2)
        return
    signals = load(f"{DATA_DIR}/signals.json", {}); ledger = load(f"{DATA_DIR}/ledger.json", {})
    if not ledger.get("open") and not ledger.get("closed"):
        log("ledger not available - nothing to mirror"); return
    out = run(make_api(key, secret), signals, ledger, state, now)
    if out.get("status") != "ok" and state.get("status") == "ok":
        # keep the last good snapshot visible, but record the failure
        state["last_error"] = {"at": now, "errors": out.get("errors")}; out = state
    json.dump(out, open(f"{DATA_DIR}/paper.json", "w"), indent=2)
    log(f"paper: {out.get('status')} · {len(out.get('orders_this_run', []))} orders this run")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        log(f"paper trading step failed: {type(e).__name__}: {e}")
        traceback.print_exc(); sys.exit(0)   # never fail the daily run
