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

MAX_SIGNAL_AGE_SEC = 30.0

def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def utcnow_dt():
    return datetime.now(timezone.utc)

def parse_utc(ts):
    try:
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None

def num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def hour_utc(ts):
    parsed = parse_utc(ts)
    return parsed.hour if parsed is not None else None

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

def evaluate_row(row, now=None):
    diagnostics = {
        "source_sec_to_close": "",
        "signal_age_sec": "",
        "effective_ttc_sec": "",
    }

    sid = row.get("strategy_id", "")
    rule = APPROVED.get(sid)
    if not rule:
        return False, "strategy_not_approved", diagnostics

    side = row.get("side", "").upper()
    if side != rule["side"]:
        return False, "side_mismatch", diagnostics

    gap = num(row.get("gap_usd"))
    spread = num(row.get("spread_c"))
    ask = num(row.get("entry_ask_c"))
    source_ttc = num(row.get("sec_to_close"))
    signal_ts = parse_utc(row.get("ts_utc"))
    now = now or utcnow_dt()
    h = signal_ts.hour if signal_ts is not None else None

    if source_ttc is not None:
        diagnostics["source_sec_to_close"] = source_ttc

    if gap is None:
        return False, "missing_gap", diagnostics
    if spread is None:
        return False, "missing_spread", diagnostics
    if ask is None:
        return False, "missing_entry_ask", diagnostics
    if h is None:
        return False, "missing_hour", diagnostics
    if source_ttc is None:
        return False, "missing_ttc", diagnostics

    signal_age_sec = (now - signal_ts).total_seconds()
    effective_ttc_sec = source_ttc - signal_age_sec
    diagnostics["signal_age_sec"] = signal_age_sec
    diagnostics["effective_ttc_sec"] = effective_ttc_sec

    if signal_age_sec < 0 or signal_age_sec > MAX_SIGNAL_AGE_SEC:
        return False, "stale_signal_rejected", diagnostics
    if not (600 <= effective_ttc_sec <= 900):
        return False, "effective_ttc_rejected", diagnostics
    if not (50 <= ask <= 59):
        return False, "entry_rejected", diagnostics
    if not (0 <= spread <= rule["spread_max"]):
        return False, "spread_rejected", diagnostics
    if not (rule["gap_min"] <= gap < rule["gap_max"]):
        return False, "gap_rejected", diagnostics
    if h in rule["cut_hours_utc"]:
        return False, "hour_cut_rejected", diagnostics

    return True, "approved", diagnostics

def check_row(row):
    ok, reason, _diagnostics = evaluate_row(row)
    return ok, reason

def main():
    cfg = load_config()
    state_dir = Path(cfg.state_dir) / "paper_signal_follower"
    state_dir.mkdir(parents=True, exist_ok=True)

    signal_file = Path(cfg.state_dir) / "paper_learning" / "candidate_signals.csv"
    seen_file = state_dir / "seen_keys.txt"
    decision_log = state_dir / "decisions.csv"
    order_log = state_dir / "orders.csv"
    health_file = state_dir / "health.json"

    fields = ["ts_utc","follower_ts_utc","decision","reason","strategy_id","ticker","side","entry_ask_c","entry_bid_c","spread_c","gap_usd","hour_utc","source_sec_to_close","signal_age_sec","effective_ttc_sec","dedup_key","place_orders"]
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

                    evaluation_time = utcnow_dt()
                    ok, reason, diagnostics = evaluate_row(row, now=evaluation_time)
                    h = hour_utc(row.get("ts_utc"))
                    out = dict(row)
                    out.update({
                        "follower_ts_utc": utcnow(),
                        "decision": "TRADE" if ok else "SKIP",
                        "reason": reason,
                        "hour_utc": h if h is not None else "",
                        "source_sec_to_close": diagnostics["source_sec_to_close"],
                        "signal_age_sec": diagnostics["signal_age_sec"],
                        "effective_ttc_sec": diagnostics["effective_ttc_sec"],
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
