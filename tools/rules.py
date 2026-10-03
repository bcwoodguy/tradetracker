#!/usr/bin/env python3
"""Mechanical rule check for the TradeTracker mock portfolio.

Reads state.json (current holdings/cash/vault) and history.json (daily closes),
pulls the latest price for each holding, and prints which hard rules fire.
The bot runs this BEFORE making discretionary decisions.

Usage: python3 tools/rules.py [--state state.json] [--history history.json] [--json]
"""
import argparse, json, urllib.request, urllib.parse

STOP_LOSS = -8.0          # % below basis -> sell all
TAKE_PROFIT = 20.0        # % above basis -> trim 50%
MAX_POSITION = 1500.0     # $ cap per position
MAX_IDLE_CASH = 300.0
LOSER_TRAIL_PTS = 3.0     # trailing S&P over 10 sessions by >= this many points
LOSER_STREAK = 10         # consecutive closes below basis
LOSER_RED_DAYS = 4        # ... or down on >= this many of the last 5 sessions
VAULT_STEP = 2500.0       # withdraw this much each milestone
VAULT_TARGET = 10000.0    # stop once the original stake is fully out


def quote(t):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?range=1mo&interval=1d"
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
    r = r["chart"]["result"][0]
    closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
    return r["meta"].get("regularMarketPrice") or closes[-1], closes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--history", default="history.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    st = json.load(open(a.state))
    days = json.load(open(a.history))["days"]

    _, spx = quote("^GSPC")
    spx10 = (spx[-1] / spx[-11] - 1) * 100 if len(spx) > 10 else 0.0

    out, invested = [], 0.0
    for p in st["positions"]:
        price, closes = quote(p["ticker"])
        val = p["shares"] * price
        invested += val
        pnl = (price / p["basis"] - 1) * 100
        # streak of history closes below basis for this ticker (current basis)
        streak = 0
        for d in reversed(days):
            row = next((x for x in d["positions"] if x["ticker"] == p["ticker"]), None)
            if not row or row["close"] >= p["basis"]:
                break
            streak += 1
        r10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0.0
        red5 = sum(1 for i in range(-5, 0) if len(closes) > 5 and closes[i] < closes[i - 1])
        flags = []
        if pnl <= STOP_LOSS:
            flags.append("STOP_LOSS: sell all")
        # next trim only after another +20% from the last trim price (position["last_trim_price"])
        tp_line = max(p["basis"], p.get("last_trim_price", 0)) * (1 + TAKE_PROFIT / 100)
        if price >= tp_line:
            flags.append("TAKE_PROFIT: trim 50%, then set last_trim_price=%.2f in state.json" % price)
        if val > MAX_POSITION:
            flags.append(f"OVER_CAP: trim ${val - MAX_POSITION:,.0f}")
        if pnl < 0 and (spx10 - r10) >= LOSER_TRAIL_PTS and (streak >= LOSER_STREAK or red5 >= LOSER_RED_DAYS):
            flags.append("CONSISTENT_LOSER: sell all, reinvest in proven winner or new catalyst pick")
        out.append(dict(ticker=p["ticker"], price=round(price, 2), value=round(val, 2), pnl_pct=round(pnl, 2),
                        below_basis_streak=streak, ret10=round(r10, 2), spx10=round(spx10, 2), red_last5=red5, flags=flags))

    cash, vault = st.get("cash", 0.0), st.get("vault", 0.0)
    total = invested + cash + vault
    nxt = st.get("vault_next", 12500.0)
    acct = dict(invested=round(invested, 2), cash=round(cash, 2), vault=round(vault, 2), total=round(total, 2),
                vault_next=nxt, flags=[])
    if vault < VAULT_TARGET and total >= nxt:
        acct["flags"].append(f"VAULT_DUE: withdraw ${VAULT_STEP:,.0f} to vault (raise cash by trimming biggest winners), then set vault_next={nxt + VAULT_STEP:,.0f}")
    if cash > MAX_IDLE_CASH:
        acct["flags"].append(f"IDLE_CASH: deploy ${cash:,.2f} (after any vault withdrawal)")

    if a.json:
        print(json.dumps(dict(positions=out, account=acct), indent=1))
        return
    print(f"S&P 500 10-session: {spx10:+.2f}%")
    for r in out:
        print(f"{r['ticker']:6} ${r['price']:>9,.2f}  val ${r['value']:>8,.2f}  P&L {r['pnl_pct']:+6.2f}%  "
              f"below-basis {r['below_basis_streak']:>2}d  10d {r['ret10']:+5.1f}%  red {r['red_last5']}/5  "
              + ("  << " + " | ".join(r["flags"]) if r["flags"] else ""))
    print(f"ACCOUNT total ${total:,.2f} (invested ${invested:,.2f} + cash ${cash:,.2f} + vault ${vault:,.2f}); next vault milestone ${nxt:,.0f}")
    for f in acct["flags"]:
        print("  << " + f)


if __name__ == "__main__":
    main()
