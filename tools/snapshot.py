#!/usr/bin/env python3
"""Daily history logger for the TradeTracker mock portfolio.

Appends (or replaces) one entry per trading day in history.json, using
end-of-day closes from Yahoo Finance.

Usage:
  snapshot.py --history history.json --positions positions.json --cash 129.86 \
      [--trades trades.json] [--scores scores.json] [--notes "text"] [--date YYYY-MM-DD]

positions.json: [{"ticker":"NVDA","shares":3.662,"basis":193.93,"book":"peace"}, ...]
  (end-of-day holdings, i.e. AFTER today's trades)
trades.json:    [{"action":"SELL","ticker":"NVDA","shares":3.662,"price":233.95,"reason":"..."}, ...]
scores.json:    {"NVDA":2,"AMD":3,...}

Or simply:  snapshot.py --history history.json --state state.json

Backfill mode (positions held constant over a date range):
  snapshot.py --history history.json --positions positions.json --cash 0 \
      --backfill-from 2026-07-02 --backfill-to 2026-10-01
"""
import argparse, json, os, sys, urllib.request, datetime as dt

BENCH = {"spx": "^GSPC", "ndx": "^IXIC"}


def closes(ticker, start, end):
    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp()) - 86400 * 7
    p2 = int(dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc).timestamp()) + 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?period1={p1}&period2={p2}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
    tz = r["meta"].get("gmtoffset", -14400)
    out = {}
    for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c is not None:
            d = dt.datetime.fromtimestamp(t + tz, dt.timezone.utc).date().isoformat()
            out[d] = round(c, 4)
    return out


def load(path, default):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def build_entry(date, positions, cash, px, prev_px, prev_entry, start_cash, trades, scores, notes, bench):
    rows, invested, cost = [], 0.0, 0.0
    for p in positions:
        c = px[p["ticker"]]
        val, cb = p["shares"] * c, p["shares"] * p["basis"]
        invested += val
        cost += cb
        pc = prev_px.get(p["ticker"])
        rows.append({
            "ticker": p["ticker"], "book": p["book"], "shares": round(p["shares"], 4),
            "basis": round(p["basis"], 2), "close": round(c, 2), "value": round(val, 2),
            "pnl": round(val - cb, 2), "pnl_pct": round((c / p["basis"] - 1) * 100, 2),
            "day_pct": round((c / pc - 1) * 100, 2) if pc else None,
            "score": scores.get(p["ticker"]),
        })
    total = invested + cash
    realized_today = 0.0
    for t in trades:
        t["amount"] = round(t["shares"] * t["price"], 2)
        if t["action"].upper().startswith("SELL") and "basis" in t:
            t["realized"] = round((t["price"] - t["basis"]) * t["shares"], 2)
            realized_today += t["realized"]
    prev_total = prev_entry["total"] if prev_entry else None
    peak = max(prev_entry.get("peak", start_cash) if prev_entry else start_cash, total)
    peace = sum(r["value"] for r in rows if r["book"] == "peace")
    e = {
        "date": date,
        "total": round(total, 2), "cash": round(cash, 2), "invested": round(invested, 2),
        "cost_basis": round(cost, 2), "unrealized": round(invested - cost, 2),
        "realized_today": round(realized_today, 2),
        "realized_cum": round((prev_entry.get("realized_cum", 0) if prev_entry else 0) + realized_today, 2),
        "day_change": round(total - prev_total, 2) if prev_total else None,
        "day_pct": round((total / prev_total - 1) * 100, 3) if prev_total else None,
        "cum_return_pct": round((total / start_cash - 1) * 100, 3),
        "peak": round(peak, 2), "drawdown_pct": round((total / peak - 1) * 100, 3),
        "peace_pct": round(peace / invested * 100, 1) if invested else 0,
        "spx": bench.get("spx"), "ndx": bench.get("ndx"),
        "positions": rows, "trades": trades, "notes": notes or "",
    }
    return e


def upsert(hist, entry):
    hist["days"] = [d for d in hist["days"] if d["date"] != entry["date"]]
    hist["days"].append(entry)
    hist["days"].sort(key=lambda d: d["date"])
    hist["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--state", help="state.json with positions/cash/today's trades (replaces --positions/--cash/--trades/--scores)")
    ap.add_argument("--positions")
    ap.add_argument("--cash", type=float)
    ap.add_argument("--trades")
    ap.add_argument("--scores")
    ap.add_argument("--notes", default="")
    ap.add_argument("--date")
    ap.add_argument("--backfill-from")
    ap.add_argument("--backfill-to")
    a = ap.parse_args()

    hist = load(a.history, {"start_cash": 10000, "start_date": None, "days": []})
    if a.state:
        # state.json: {"positions":[...], "cash":n, "day":{"date":..,"trades":[..],"scores":{..},"notes":".."}}
        st = load(a.state, {})
        positions, a.cash = st["positions"], st["cash"]
        day = st.get("day", {})
        same = day.get("date") == (a.date or dt.date.today().isoformat())
        trades = day.get("trades", []) if same else []
        scores = day.get("scores", {}) if same else {}
        a.notes = a.notes or (day.get("notes", "") if same else "")
    else:
        positions = load(a.positions, [])
        trades = load(a.trades, [])
        scores = load(a.scores, {})
    tickers = sorted({p["ticker"] for p in positions} | {t["ticker"] for t in trades})

    today = a.date or dt.date.today().isoformat()
    start = a.backfill_from or today
    end = a.backfill_to or today
    series = {t: closes(t, start, end) for t in tickers}
    bench = {k: closes(v, start, end) for k, v in BENCH.items()}

    if a.backfill_from:
        dates = sorted(d for d in series[positions[0]["ticker"]] if start <= d <= end)
    else:
        if today not in series[positions[0]["ticker"]]:
            sys.exit(f"No close for {today} yet (market closed / holiday / not settled).")
        dates = [today]

    for d in dates:
        prev = [x for x in hist["days"] if x["date"] < d]
        prev_entry = prev[-1] if prev else None
        px = {t: s[d] for t, s in series.items() if d in s}
        prev_px = {}
        for t, s in series.items():
            earlier = [k for k in s if k < d]
            if earlier:
                prev_px[t] = s[max(earlier)]
        e = build_entry(d, positions, a.cash, px, prev_px, prev_entry, hist["start_cash"],
                        [] if a.backfill_from else trades, {} if a.backfill_from else scores,
                        "backfilled from historical closes" if a.backfill_from else a.notes,
                        {k: (round(v[d], 2) if d in v else None) for k, v in bench.items()})
        upsert(hist, e)
    hist["start_date"] = hist["start_date"] or hist["days"][0]["date"]
    with open(a.history, "w") as f:
        json.dump(hist, f, indent=1)
    last = hist["days"][-1]
    print(f"{len(dates)} day(s) written. Last {last['date']}: total ${last['total']:,} "
          f"day {last['day_change']} cum {last['cum_return_pct']}%")


if __name__ == "__main__":
    import urllib.parse
    main()
