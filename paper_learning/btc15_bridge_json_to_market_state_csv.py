import csv, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/kalshi-research/btc15_implied_winner_widest")
from btc15iw.quote_fallback import fallback_best_quote

BASE = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest")
SRC = BASE / "active_btc15_market_state.json"
OUT = BASE / "btc15_implied_winner_market_state.csv"

FIELDS = [
    "ts_utc","market_ticker","open_time_utc","close_time_utc",
    "floor_strike","floor_strike_source","btc_ref_price","sec_to_close",
    "yes_best_bid_c","yes_best_ask_c","no_best_bid_c","no_best_ask_c",
    "yes_bid_plus_no_bid_c","ws_seq","ws_lag_ms",
]

def parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z","+00:00"))

def seconds_to_close(close_time):
    try:
        return (parse_ts(close_time) - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return ""

def ensure_header():
    if not OUT.exists() or OUT.stat().st_size == 0:
        with OUT.open("w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()

def append_row(row):
    with OUT.open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerow({k: row.get(k, "") for k in FIELDS})

last_ts = None
ensure_header()

while True:
    try:
        d = json.load(SRC.open())
        ts = d.get("generated_at_utc") or datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
        if ts != last_ts:
            ticker = d.get("market_ticker") or ""
            q = fallback_best_quote(ticker) if ticker else {}
            row = {
                "ts_utc": ts,
                "market_ticker": ticker,
                "open_time_utc": d.get("open_time_utc", ""),
                "close_time_utc": d.get("close_time_utc", ""),
                "floor_strike": d.get("floor_strike", ""),
                "floor_strike_source": d.get("floor_strike_source", ""),
                "btc_ref_price": d.get("live_proxy_median", ""),
                "sec_to_close": seconds_to_close(d.get("close_time_utc", "")),
                "yes_best_bid_c": q.get("yes_best_bid_c", ""),
                "yes_best_ask_c": q.get("yes_best_ask_c", ""),
                "no_best_bid_c": q.get("no_best_bid_c", ""),
                "no_best_ask_c": q.get("no_best_ask_c", ""),
                "yes_bid_plus_no_bid_c": q.get("yes_bid_plus_no_bid_c", ""),
                "ws_seq": "",
                "ws_lag_ms": "",
            }
            append_row(row)
            last_ts = ts
    except Exception as e:
        print("adapter_error", repr(e), flush=True)
    time.sleep(2)
