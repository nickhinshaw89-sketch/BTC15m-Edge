#!/usr/bin/env python3
import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .proxy_source import floor_sample_at_or_after, latest_proxy, parse_utc, iso_z

APPEND_CSV = "/var/lib/kalshi-reversion/crypto_universe/direct_15m_crypto_open_snapshots_append.csv"


def now_utc():
    return datetime.now(timezone.utc)


def atomic_write_json(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)


def load_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else default
    except Exception:
        return default


def latest_btc_row(csv_path):
    rows = []
    p = Path(csv_path)
    if not p.exists():
        return None
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("series_ticker") == "KXBTC15M" or row.get("asset") == "BTC":
                if row.get("ticker") and row.get("open_time") and row.get("close_time"):
                    rows.append(row)
    if not rows:
        return None
    rows.sort(key=lambda r: parse_utc(r.get("capture_ts")) or datetime.min.replace(tzinfo=timezone.utc))
    return rows[-1]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/kalshi-research/btc15_implied_winner_widest/config.env")
    ap.add_argument("--csv", default=APPEND_CSV)
    ap.add_argument("--out", default="")
    ap.add_argument("--cache", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--once-ready", action="store_true")
    ap.add_argument("--max-floor-sample-lag-sec", type=float, default=10.0)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    out = args.out or os.path.join(cfg.state_dir, "active_btc15_market_state.json")
    cache_path = args.cache or os.path.join(cfg.state_dir, "btc15_floor_cache.json")
    status_path = args.status or os.path.join(cfg.state_dir, "btc15_floor_bridge_status.json")

    floors = load_json(cache_path, {})
    last_print = 0.0

    while True:
        row = latest_btc_row(args.csv)
        live = latest_proxy(max_age_sec=10.0)
        now = now_utc()

        status = {
            "ts_utc": iso_z(now),
            "ok": False,
            "reason": "",
            "proxy_latest": live or {},
            "active_csv": args.csv,
            "active_row": row or {},
            "out": out,
            "cache": cache_path,
        }

        if not row:
            status["reason"] = "missing_btc_row"
            atomic_write_json(status_path, status)
            time.sleep(1)
            continue

        open_dt = parse_utc(row.get("open_time"))
        close_dt = parse_utc(row.get("close_time"))

        if not open_dt or not close_dt:
            status["reason"] = "missing_open_or_close_time"
            atomic_write_json(status_path, status)
            time.sleep(1)
            continue

        open_key = iso_z(open_dt)

        if open_key not in floors:
            sample = floor_sample_at_or_after(open_dt, max_lag_sec=args.max_floor_sample_lag_sec)
            if sample:
                floors[open_key] = {
                    "floor_strike": sample["price"],
                    "floor_strike_source": sample["source"],
                    "frozen_for_open_time_utc": open_key,
                    "proxy_sample_ts_utc": sample["ts_utc"],
                    "proxy_sample_lag_sec": sample["lag_sec"],
                    "venue_count": sample["venue_count"],
                    "max_venue_spread": sample["max_venue_spread"],
                }
                atomic_write_json(cache_path, floors)

        if open_key in floors:
            floor = floors[open_key]
            payload = {
                "generated_at_utc": iso_z(now),
                "market_ticker": row.get("ticker"),
                "event_ticker": row.get("event_ticker"),
                "series_ticker": row.get("series_ticker"),
                "asset": row.get("asset"),
                "open_time_utc": iso_z(open_dt),
                "close_time_utc": iso_z(close_dt),
                "floor_strike": floor.get("floor_strike"),
                "floor_strike_source": floor.get("floor_strike_source"),
                "floor_proxy_sample_ts_utc": floor.get("proxy_sample_ts_utc"),
                "floor_proxy_sample_lag_sec": floor.get("proxy_sample_lag_sec"),
                "live_proxy_median": live.get("price") if live else None,
                "live_proxy_ts_utc": live.get("ts_utc") if live else "",
                "source_csv": args.csv,
                "source_capture_ts": row.get("capture_ts"),
            }
            atomic_write_json(out, payload)
            status["ok"] = True
            status["reason"] = "ready"
            status["market_state"] = payload
            atomic_write_json(status_path, status)
            print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
            if args.once_ready:
                return 0
        else:
            status["reason"] = "waiting_for_proxy_floor_sample_at_open"
            status["target_open_time_utc"] = open_key
            atomic_write_json(status_path, status)
            if time.monotonic() - last_print >= 15:
                last_print = time.monotonic()
                print(json.dumps({
                    "status": status["reason"],
                    "target_open_time_utc": open_key,
                    "active_ticker": row.get("ticker"),
                    "active_open_time": row.get("open_time"),
                    "active_close_time": row.get("close_time"),
                    "proxy_latest": live or {},
                }, sort_keys=True), flush=True)

        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
