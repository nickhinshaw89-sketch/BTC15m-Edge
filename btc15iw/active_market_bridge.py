#!/usr/bin/env python3
import argparse
import csv
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config

APPEND_CSV = "/var/lib/kalshi-reversion/crypto_universe/direct_15m_crypto_open_snapshots_append.csv"


def now_utc():
    return datetime.now(timezone.utc)


def parse_utc(value):
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt):
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else default
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


class BtcWsState:
    def __init__(self):
        self.lock = threading.Lock()
        self.price = None
        self.raw_symbol = ""
        self.exchange_ts_utc = None
        self.recv_ts_utc = None
        self.connected = False
        self.error = ""

    def update(self, price, symbol, exchange_ms):
        recv = now_utc()
        exch = recv
        if exchange_ms:
            try:
                exch = datetime.fromtimestamp(int(exchange_ms) / 1000.0, tz=timezone.utc)
            except Exception:
                exch = recv
        with self.lock:
            self.price = float(price)
            self.raw_symbol = symbol or ""
            self.exchange_ts_utc = exch
            self.recv_ts_utc = recv

    def snapshot(self):
        with self.lock:
            return {
                "price": self.price,
                "raw_symbol": self.raw_symbol,
                "exchange_ts_utc": self.exchange_ts_utc,
                "recv_ts_utc": self.recv_ts_utc,
                "connected": self.connected,
                "error": self.error,
            }


def start_btc_ws(url, state):
    def on_open(ws):
        with state.lock:
            state.connected = True
            state.error = ""

    def on_close(ws, code, msg):
        with state.lock:
            state.connected = False
            state.error = "closed:%s:%s" % (code, msg)

    def on_error(ws, err):
        with state.lock:
            state.error = str(err)

    def on_message(ws, message):
        try:
            d = json.loads(message)
            price = d.get("p") or d.get("price")
            symbol = d.get("s") or d.get("symbol") or ""
            exchange_ms = d.get("T") or d.get("E") or d.get("ts_ms")
            if price is not None:
                state.update(price, symbol, exchange_ms)
        except Exception as e:
            with state.lock:
                state.error = "parse_error:%s" % e

    def run():
        import websocket
        while True:
            try:
                ws = websocket.WebSocketApp(
                    url,
                    on_open=on_open,
                    on_close=on_close,
                    on_error=on_error,
                    on_message=on_message,
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                with state.lock:
                    state.connected = False
                    state.error = str(e)
            time.sleep(2)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/kalshi-research/btc15_implied_winner_widest/config.env")
    ap.add_argument("--csv", default=APPEND_CSV)
    ap.add_argument("--out", default="")
    ap.add_argument("--cache", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--once-ready", action="store_true")
    ap.add_argument("--allow-late-open-sec", type=float, default=2.0)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    out = args.out or os.path.join(cfg.state_dir, "active_btc15_market_state.json")
    cache_path = args.cache or os.path.join(cfg.state_dir, "btc15_floor_cache.json")
    status_path = args.status or os.path.join(cfg.state_dir, "btc15_floor_bridge_status.json")

    floors = load_json(cache_path, {})
    state = BtcWsState()
    start_btc_ws(cfg.btc_ref_ws_url, state)

    last_print = 0.0

    while True:
        row = latest_btc_row(args.csv)
        snap = state.snapshot()
        now = now_utc()

        status = {
            "ts_utc": iso_z(now),
            "ok": False,
            "reason": "",
            "btc_ws_connected": bool(snap["connected"]),
            "btc_ws_error": snap["error"],
            "btc_ref_price": snap["price"],
            "btc_ref_exchange_ts_utc": iso_z(snap["exchange_ts_utc"]),
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

        active_open_key = iso_z(open_dt)

        if active_open_key not in floors:
            if 0 <= (now - open_dt).total_seconds() <= args.allow_late_open_sec:
                target_open = open_dt
            else:
                target_open = close_dt
        else:
            target_open = open_dt

        target_key = iso_z(target_open)

        if target_key not in floors and snap["price"] is not None and snap["exchange_ts_utc"] is not None:
            if snap["exchange_ts_utc"] >= target_open:
                floors[target_key] = {
                    "floor_strike": snap["price"],
                    "floor_strike_source": "btc_ref_at_open_bridge",
                    "frozen_for_open_time_utc": target_key,
                    "btc_ref_exchange_ts_utc": iso_z(snap["exchange_ts_utc"]),
                    "btc_ref_receive_ts_utc": iso_z(snap["recv_ts_utc"]),
                    "raw_symbol": snap["raw_symbol"],
                }
                atomic_write_json(cache_path, floors)

        if active_open_key in floors:
            floor = floors[active_open_key]
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
                "floor_btc_ref_exchange_ts_utc": floor.get("btc_ref_exchange_ts_utc"),
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
            status["reason"] = "waiting_for_clean_floor_at_open"
            status["target_open_time_utc"] = target_key
            atomic_write_json(status_path, status)
            if time.monotonic() - last_print >= 15:
                last_print = time.monotonic()
                print(json.dumps({
                    "status": status["reason"],
                    "target_open_time_utc": target_key,
                    "btc_ref_price": snap["price"],
                    "btc_ref_exchange_ts_utc": iso_z(snap["exchange_ts_utc"]),
                    "active_ticker": row.get("ticker"),
                    "active_open_time": row.get("open_time"),
                    "active_close_time": row.get("close_time"),
                }, sort_keys=True), flush=True)

        time.sleep(0.5)


if __name__ == "__main__":
    raise SystemExit(main())
