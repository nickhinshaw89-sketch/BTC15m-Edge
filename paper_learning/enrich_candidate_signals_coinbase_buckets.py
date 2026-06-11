import csv
from pathlib import Path
from datetime import datetime, timezone
from collections import deque

BASE = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest/paper_learning")
SIGNALS = BASE / "candidate_signals.csv"
TRADES = Path("/var/lib/coinbase-flow/coinbase_btc_all_trades.csv")
OUT = BASE / "candidate_signals_with_coinbase_buckets.csv"

ADD_FIELDS = [
    "cb_trades_60s","cb_notional_60s","cb_buy_usd_60s","cb_sell_usd_60s","cb_imbalance_60s","cb_return_60s_pct","cb_volatility_60s_pct","cb_large_10k_60s","cb_large_50k_60s",
    "cb_trades_300s","cb_notional_300s","cb_buy_usd_300s","cb_sell_usd_300s","cb_imbalance_300s","cb_return_300s_pct","cb_volatility_300s_pct","cb_large_10k_300s","cb_large_50k_300s",
    "cb_return_60s_bucket","cb_return_300s_bucket","cb_volume_60s_bucket","cb_volume_300s_bucket","cb_imbalance_60s_bucket","cb_volatility_300s_bucket","cb_large_trade_60s_bucket","cb_regime_id"
]

def dt(x):
    x=(x or "").strip().replace("Z","+00:00")
    if not x:
        return None
    d=datetime.fromisoformat(x)
    if d.tzinfo is None:
        d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)

def f(x):
    try: return float(x)
    except Exception: return 0.0

def side_buy(x):
    return str(x or "").upper() in ("BUY","B","TAKER_BUY")

def ret_bucket(x, wide=False):
    if x <= (-0.50 if wide else -0.20): return "strong_down"
    if x <= (-0.12 if wide else -0.05): return "down"
    if x <  (0.12 if wide else 0.05): return "flat"
    if x <  (0.50 if wide else 0.20): return "up"
    return "strong_up"

def vol_bucket(x):
    if x < 0.05: return "quiet"
    if x < 0.15: return "normal"
    if x < 0.35: return "volatile"
    return "extreme"

def volume_bucket(x, w):
    if w == 60:
        if x < 250000: return "low"
        if x < 1000000: return "normal"
        if x < 3000000: return "high"
        return "extreme"
    if x < 1000000: return "low"
    if x < 5000000: return "normal"
    if x < 15000000: return "high"
    return "extreme"

def imb_bucket(x):
    if x <= -0.35: return "sell_dominant"
    if x <= -0.10: return "sell_bias"
    if x < 0.10: return "balanced"
    if x < 0.35: return "buy_bias"
    return "buy_dominant"

def large_bucket(n):
    if n <= 0: return "none"
    if n <= 2: return "some"
    if n <= 6: return "active"
    return "extreme"

def stats(q):
    if not q:
        return dict(trades=0, notional=0, buy=0, sell=0, imbalance=0, ret=0, vol=0, large10=0, large50=0)
    prices=[r[1] for r in q]
    notionals=[r[2] for r in q]
    buys=sum(r[2] for r in q if r[3])
    sell=sum(r[2] for r in q if not r[3])
    total=buys+sell
    ret=((prices[-1]/prices[0])-1)*100 if prices[0] else 0
    vol=((max(prices)-min(prices))/prices[-1])*100 if prices[-1] else 0
    return dict(trades=len(q), notional=sum(notionals), buy=buys, sell=sell, imbalance=((buys-sell)/total if total else 0), ret=ret, vol=vol, large10=sum(1 for r in q if r[2]>=10000), large50=sum(1 for r in q if r[2]>=50000))

def regime(r60, r300):
    rb60=ret_bucket(r60["ret"], False); rb300=ret_bucket(r300["ret"], True)
    ib=imb_bucket(r60["imbalance"]); vb=volume_bucket(r60["notional"],60); volb=vol_bucket(r300["vol"]); lb=large_bucket(r60["large50"])
    if rb300 in ("up","strong_up") and ib in ("buy_bias","buy_dominant") and vb in ("high","extreme"): return "moonshot_up"
    if rb300 in ("down","strong_down") and ib in ("sell_bias","sell_dominant") and vb in ("high","extreme"): return "moonshot_down"
    if rb60=="strong_up" and ib=="buy_dominant": return "squeeze_up"
    if rb60=="strong_down" and ib=="sell_dominant": return "squeeze_down"
    if lb in ("active","extreme") and ib=="buy_dominant": return "buy_sweep"
    if lb in ("active","extreme") and ib=="sell_dominant": return "sell_sweep"
    if rb300=="flat" and volb in ("quiet","normal"): return "chop"
    if rb300 in ("up","strong_up"): return "trend_up"
    if rb300 in ("down","strong_down"): return "trend_down"
    return "mixed"

signals=[]
with SIGNALS.open(newline="") as fh:
    for r in csv.DictReader(fh):
        d=dt(r.get("ts_utc"))
        if d:
            r["_dt"]=d
            signals.append(r)
signals.sort(key=lambda r:r["_dt"])

q60=deque(); q300=deque(); out=[]; idx=0
with TRADES.open(newline="") as fh:
    tr=csv.DictReader(fh)
    for sig in signals:
        sdt=sig["_dt"]
        for row in tr:
            t=dt(row.get("received_at_utc"))
            if not t: continue
            if t > sdt:
                hold=(t, f(row.get("price")), f(row.get("notional_usd")), side_buy(row.get("inferred_taker_side")))
                break
            item=(t, f(row.get("price")), f(row.get("notional_usd")), side_buy(row.get("inferred_taker_side")))
            q60.append(item); q300.append(item)
        else:
            hold=None
        while q60 and (sdt-q60[0][0]).total_seconds() > 60: q60.popleft()
        while q300 and (sdt-q300[0][0]).total_seconds() > 300: q300.popleft()
        r60=stats(q60); r300=stats(q300)
        sig.pop("_dt", None)
        sig.update({
            "cb_trades_60s":r60["trades"], "cb_notional_60s":round(r60["notional"],2), "cb_buy_usd_60s":round(r60["buy"],2), "cb_sell_usd_60s":round(r60["sell"],2), "cb_imbalance_60s":round(r60["imbalance"],6), "cb_return_60s_pct":round(r60["ret"],6), "cb_volatility_60s_pct":round(r60["vol"],6), "cb_large_10k_60s":r60["large10"], "cb_large_50k_60s":r60["large50"],
            "cb_trades_300s":r300["trades"], "cb_notional_300s":round(r300["notional"],2), "cb_buy_usd_300s":round(r300["buy"],2), "cb_sell_usd_300s":round(r300["sell"],2), "cb_imbalance_300s":round(r300["imbalance"],6), "cb_return_300s_pct":round(r300["ret"],6), "cb_volatility_300s_pct":round(r300["vol"],6), "cb_large_10k_300s":r300["large10"], "cb_large_50k_300s":r300["large50"],
            "cb_return_60s_bucket":ret_bucket(r60["ret"],False), "cb_return_300s_bucket":ret_bucket(r300["ret"],True), "cb_volume_60s_bucket":volume_bucket(r60["notional"],60), "cb_volume_300s_bucket":volume_bucket(r300["notional"],300), "cb_imbalance_60s_bucket":imb_bucket(r60["imbalance"]), "cb_volatility_300s_bucket":vol_bucket(r300["vol"]), "cb_large_trade_60s_bucket":large_bucket(r60["large50"]), "cb_regime_id":regime(r60,r300)
        })
        out.append(sig)
        if hold:
            q60.append(hold); q300.append(hold)

fields=list(csv.DictReader(open(SIGNALS,newline="")).fieldnames)+ADD_FIELDS
with OUT.open("w", newline="") as fh:
    w=csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    for r in out:
        w.writerow({k:r.get(k,"") for k in fields})
print({"wrote":str(OUT),"rows":len(out)})
