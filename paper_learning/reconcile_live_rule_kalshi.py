import csv, json, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "/opt/kalshi-research/btc15_implied_winner_widest")

from btc15iw.config import load_config
from btc15iw.kalshi_auth import load_private_key, http_get_json

ORDERS = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest/btc15_implied_winner_orders.csv")
OUT = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest/paper_learning/live_rule_with_kalshi.csv")
SUMMARY = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest/paper_learning/live_rule_daily_summary.csv")

FIELDS = [
    "ts_utc","day","strategy_id","ticker","side","contracts",
    "limit_price_c","entry_ask_c","fill_price_c","fee_c",
    "kalshi_status","kalshi_api_status","winner_side",
    "outcome","gross_c","net_c","order_id","order_status"
]

def fnum(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def norm_side(x):
    s = str(x or "").strip().upper()
    if s in ("YES","Y","1","TRUE"): return "YES"
    if s in ("NO","N","0","FALSE"): return "NO"
    return ""

def market_winner(m):
    vals = [
        m.get("result"), m.get("settlement_value"), m.get("winning_outcome"),
        m.get("winner"), m.get("outcome"), m.get("settlement_result")
    ]
    for v in vals:
        n = norm_side(v)
        if n:
            return n
    return ""

def order_fill(row):
    side = norm_side(row.get("side"))
    price = fnum(row.get("limit_price_c") or row.get("entry_ask_c"))
    fee_c = 0.0
    try:
        raw = json.loads(row.get("raw_json") or "{}")
        o = raw.get("order") or {}
        side = norm_side(o.get("outcome_side") or o.get("side") or side)
        if side == "YES":
            price = fnum(o.get("yes_price_dollars")) * 100.0 or price
        elif side == "NO":
            price = fnum(o.get("no_price_dollars")) * 100.0 or price
        fee_c = fnum(o.get("taker_fees_dollars")) * 100.0
    except Exception:
        pass
    return side, round(price, 4), round(fee_c, 4)

cfg = load_config("/opt/kalshi-research/btc15_implied_winner_widest/config.env")
pk = load_private_key(cfg.kalshi_private_key_path)

rows = []
cache = {}

with ORDERS.open(newline="") as f:
    for r in csv.DictReader(f):
        if r.get("order_status") != "ACCEPTED":
            continue
        ticker = r.get("market_ticker") or r.get("ticker") or ""
        if not ticker:
            continue

        if ticker not in cache:
            st, body = http_get_json(cfg.kalshi_api_base, cfg.kalshi_api_key_id, pk, f"/markets/{ticker}")
            m = body.get("market", {}) if isinstance(body, dict) else {}
            cache[ticker] = (st, m)

        st, m = cache[ticker]
        kstatus = m.get("status", "")
        winner = market_winner(m)

        side, fill_price_c, fee_c = order_fill(r)
        contracts = fnum(r.get("contracts"), 1.0)

        if winner:
            win = side == winner
            gross_each = (100.0 - fill_price_c) if win else -fill_price_c
            gross_c = gross_each * contracts
            net_c = gross_c - fee_c
            outcome = "WIN" if win else "LOSS"
        else:
            gross_c = net_c = 0.0
            outcome = "UNSETTLED"

        rows.append({
            "ts_utc": r.get("ts_utc",""),
            "day": (r.get("ts_utc","")[:10]),
            "strategy_id": "LIVE_10S_5M_40_59_S5_G25",
            "ticker": ticker,
            "side": side,
            "contracts": r.get("contracts",""),
            "limit_price_c": r.get("limit_price_c",""),
            "entry_ask_c": r.get("entry_ask_c",""),
            "fill_price_c": fill_price_c,
            "fee_c": fee_c,
            "kalshi_status": kstatus,
            "kalshi_api_status": st,
            "winner_side": winner,
            "outcome": outcome,
            "gross_c": round(gross_c, 4),
            "net_c": round(net_c, 4),
            "order_id": r.get("order_id",""),
            "order_status": r.get("order_status",""),
        })

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)

d = defaultdict(lambda: [0,0,0,0.0])
for r in rows:
    if r["outcome"] == "UNSETTLED":
        continue
    x = d[(r["day"], r["strategy_id"])]
    x[0] += 1
    x[1] += 1 if r["outcome"] == "WIN" else 0
    x[2] += 1 if r["outcome"] == "LOSS" else 0
    x[3] += fnum(r["net_c"])

with SUMMARY.open("w", newline="") as f:
    fields = ["day","strategy_id","trades","wins","losses","win_rate","net_c","avg_net_c"]
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    for (day, sid), (n, wins, losses, net) in sorted(d.items()):
        w.writerow({
            "day": day, "strategy_id": sid, "trades": n,
            "wins": wins, "losses": losses,
            "win_rate": round(wins/n, 4) if n else "",
            "net_c": round(net, 4),
            "avg_net_c": round(net/n, 4) if n else "",
        })

print("=== live detail ===")
with OUT.open() as f:
    print(f.read())

print("=== live summary ===")
with SUMMARY.open() as f:
    print(f.read())
