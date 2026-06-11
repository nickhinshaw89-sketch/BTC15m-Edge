import csv, os, time, json
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .kalshi_auth import load_private_key
from .order_client import KalshiOrderClient

APP_STRATEGY_ID = "BTC15M_PAPER_SIGNAL_FOLLOWER_V1"

APPROVED = {
    "PAPER_ONLY_PROMOTED_NO_SP0_1_TTC10_15_ENTRY50_59_GAP25_50": {
        "side": "NO",
        "gap_min": 25.0,
        "gap_max": 50.0,
        "spread_max": 1.0,
        "cut_hours_utc": {3,4,6,9,11,12,14,15,17,20,23},
    },
}

def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def hour_utc(ts):
    try:
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc).hour
    except Exception:
        return None

def load_seen(path):
    p = Path(path)
    if not p.exists():
        return set()
    out = set()
    try:
        with p.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    out.add(line)
    except Exception:
        pass
    return out

def append_seen(path, key):
    with open(path, "a") as f:
        f.write(key + "\n")

def append_csv(path, row, fields):
    p = Path(path)
    exists = p.exists() and p.stat().st_size > 0
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})

def check_row(row):
    sid = row.get("strategy_id", "")
    rule = APPROVED.get(sid)
    if not rule:
        return False, "strategy_not_approved"

    side = row.get("side", "").upper()
    if side != rule["side"]:
        return False, "side_mismatch"

    gap = num(row.get("gap_usd"))
    spread = num(row.get("spread_c"))
    ask = num(row.get("entry_ask_c"))
    h = hour_utc(row.get("ts_utc"))

    if gap is None:
        return False, "missing_gap"
    if spread is None:
        return False, "missing_spread"
    if ask is None:
        return False, "missing_entry_ask"
    if h is None:
        return False, "missing_hour"

    if not (rule["gap_min"] <= gap < rule["gap_max"]):
        return False, "gap_rejected"
    if spread > rule["spread_max"]:
        return False, "spread_rejected"
    if h in rule["cut_hours_utc"]:
        return False, "hour_cut_rejected"

    return True, "approved"

def main():
    cfg = load_config()
    state_dir = Path(cfg.state_dir) / "paper_signal_follower"
    state_dir.mkdir(parents=True, exist_ok=True)

    signal_file = Path(cfg.state_dir) / "paper_learning" / "candidate_signals.csv"
    seen_file = state_dir / "seen_keys.txt"
    decision_log = state_dir / "decisions.csv"
    order_log = state_dir / "orders.csv"
    health_file = state_dir / "health.json"

    fields = ["ts_utc","follower_ts_utc","decision","reason","strategy_id","ticker","side","entry_ask_c","entry_bid_c","spread_c","gap_usd","hour_utc","dedup_key","place_orders"]
    order_fields = ["ts_utc","strategy_id","ticker","side","contracts","limit_price_c","http_status","order_id","client_order_id","raw"]

    private_key = None
    order_client = None
    if int(cfg.place_orders):
        private_key = load_private_key(cfg.kalshi_private_key_path)
        order_client = KalshiOrderClient(
            cfg.kalshi_api_base,
            cfg.kalshi_api_key_id,
            private_key,
            cfg.live_order_time_in_force,
            bool(cfg.live_order_post_only),
        )

    seen = load_seen(seen_file)

    # Safety: on every process start, absorb all existing rows so restarts
    # cannot trigger live orders from historical/backfilled paper signals.
    if signal_file.exists():
        try:
            for row in csv.DictReader(signal_file.open()):
                key = row.get("dedup_key") or (row.get("strategy_id","") + "|" + row.get("ticker","") + "|" + row.get("side",""))
                if key and key not in seen:
                    seen.add(key)
                    append_seen(seen_file, key)
        except Exception:
            pass

    while True:
        try:
            if signal_file.exists():
                rows = list(csv.DictReader(signal_file.open()))
                for row in rows:
                    key = row.get("dedup_key") or (row.get("strategy_id","") + "|" + row.get("ticker","") + "|" + row.get("side",""))
                    if key in seen:
                        continue

                    ok, reason = check_row(row)
                    h = hour_utc(row.get("ts_utc"))
                    out = dict(row)
                    out.update({
                        "follower_ts_utc": utcnow(),
                        "decision": "TRADE" if ok else "SKIP",
                        "reason": reason,
                        "hour_utc": h if h is not None else "",
                        "place_orders": int(cfg.place_orders),
                    })
                    append_csv(decision_log, out, fields)
                    seen.add(key)
                    append_seen(seen_file, key)

                    if ok and int(cfg.place_orders):
                        res = order_client.place_buy(row["ticker"], row["side"], int(cfg.contracts), float(row["entry_ask_c"]), APP_STRATEGY_ID)
                        append_csv(order_log, {
                            "ts_utc": utcnow(),
                            "strategy_id": APP_STRATEGY_ID,
                            "ticker": row["ticker"],
                            "side": row["side"],
                            "contracts": int(cfg.contracts),
                            "limit_price_c": row["entry_ask_c"],
                            "http_status": res.get("http_status"),
                            "order_id": res.get("order_id"),
                            "client_order_id": res.get("client_order_id"),
                            "raw": json.dumps(res, default=str),
                        }, order_fields)

            health_file.write_text(json.dumps({
                "ts_utc": utcnow(),
                "signal_file": str(signal_file),
                "seen_count": len(seen),
                "place_orders": int(cfg.place_orders),
                "status": "OK",
            }, indent=2))
        except Exception as e:
            health_file.write_text(json.dumps({
                "ts_utc": utcnow(),
                "status": "ERROR",
                "error": str(e),
            }, indent=2))

        time.sleep(1)

if __name__ == "__main__":
    main()
