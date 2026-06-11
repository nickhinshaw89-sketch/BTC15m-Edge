#!/usr/bin/env python3
import json, os, time, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATE="/var/lib/kalshi-reversion/btc15_implied_winner_widest/active_btc15_market_state.json"
CACHE="/var/lib/kalshi-reversion/btc15_implied_winner_widest/coinbase_floor_cache.json"
ET=ZoneInfo("America/New_York")
MONS=["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]

def read_json(p):
    try: return json.load(open(p))
    except Exception: return {}

def write_json(p, obj):
    tmp=p+".tmp"
    with open(tmp,"w") as f:
        json.dump(obj,f,indent=2,sort_keys=True)
        f.write("\n")
    os.replace(tmp,p)

def coinbase_price():
    req=urllib.request.Request(
        "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
        headers={"User-Agent":"kalshi-btc15-coinbase-bridge"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        j=json.loads(r.read().decode())
    return float(j["price"]), datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def current_market():
    now_utc=datetime.now(timezone.utc)
    now_et=now_utc.astimezone(ET)
    minute=((now_et.minute//15)+1)*15
    close_et=now_et.replace(second=0,microsecond=0)
    if minute >= 60:
        close_et=close_et.replace(minute=0)+timedelta(hours=1)
    else:
        close_et=close_et.replace(minute=minute)
    open_et=close_et-timedelta(minutes=15)
    yy=close_et.strftime("%y")
    mon=MONS[close_et.month-1]
    dd=close_et.strftime("%d")
    hhmm=close_et.strftime("%H%M")
    suffix=close_et.strftime("%M")
    event=f"KXBTC15M-{yy}{mon}{dd}{hhmm}"
    ticker=f"{event}-{suffix}"
    return now_utc, open_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc), event, ticker

def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    cache=read_json(CACHE)
    while True:
        try:
            now_utc, open_utc, close_utc, event, ticker=current_market()
            px, px_ts=coinbase_price()
            if ticker not in cache:
                cache[ticker]={"floor_strike":px,"floor_proxy_sample_ts_utc":px_ts}
                write_json(CACHE, cache)
            floor=cache[ticker]["floor_strike"]
            floor_ts=cache[ticker]["floor_proxy_sample_ts_utc"]
            out={
                "asset":"BTC",
                "series_ticker":"KXBTC15M",
                "event_ticker":event,
                "market_ticker":ticker,
                "open_time_utc":open_utc.isoformat().replace("+00:00","Z"),
                "close_time_utc":close_utc.isoformat().replace("+00:00","Z"),
                "floor_strike":floor,
                "floor_strike_source":"coinbase_btc_usd_at_market_open",
                "floor_proxy_sample_ts_utc":floor_ts,
                "floor_proxy_sample_lag_sec":None,
                "live_proxy_median":px,
                "live_proxy_ts_utc":px_ts,
                "generated_at_utc":now_utc.isoformat().replace("+00:00","Z"),
                "source_csv":"live_et_active_bridge_coinbase_only"
            }
            write_json(STATE,out)
            print(json.dumps(out,indent=2,sort_keys=True), flush=True)
        except Exception as e:
            print("bridge_error:"+repr(e), flush=True)
        time.sleep(1)

if __name__=="__main__":
    main()
