import json
import threading
import time
from datetime import datetime, timezone

from .orderbook import Orderbook
from .timeutils import ms_to_iso_z

class BTCRefState:
    def __init__(self):
        self.price = None
        self.raw_symbol = ""
        self.exchange_ts_utc = ""
        self.receive_lag_ms = None
        self.connected = False
        self.last_msg_monotonic = None
        self.last_error = None
        self.lock = threading.Lock()

    def update(self, price, raw_symbol="", exchange_ts_ms=None):
        now_ms = int(time.time() * 1000)
        with self.lock:
            self.price = float(price)
            self.raw_symbol = raw_symbol
            self.exchange_ts_utc = ms_to_iso_z(exchange_ts_ms)
            self.receive_lag_ms = (now_ms - int(exchange_ts_ms)) if exchange_ts_ms else None
            self.last_msg_monotonic = time.monotonic()

    def age_sec(self):
        with self.lock:
            if self.last_msg_monotonic is None:
                return None
            return time.monotonic() - self.last_msg_monotonic

class BTCRefWS(threading.Thread):
    def __init__(self, url, state, logger, source="binance_trade_ws", min_log_interval_sec=2.0):
        super().__init__(daemon=True)
        self.url = url
        self.state = state
        self.logger = logger
        self.source = source
        self.min_log_interval_sec = min_log_interval_sec
        self.stop_flag = threading.Event()
        self._last_logged = 0.0

    def run(self):
        if self.source == "btc_index_proxy_median" or str(self.url).startswith("file://"):
            self._run_proxy_file()
            return

        try:
            import websocket
        except Exception as e:
            self.state.last_error = f"missing_websocket_client:{e}"
            return

        while not self.stop_flag.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self.url,
                    on_open=lambda w: self._on_open(w),
                    on_message=lambda w, m: self._on_message(w, m),
                    on_error=lambda w, e: self._on_error(e),
                    on_close=lambda w, code, msg: self._on_close(code, msg),
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                self.state.last_error = str(e)
            self.state.connected = False
            time.sleep(2)

    def _run_proxy_file(self):
        from .proxy_source import latest_proxy

        self.state.connected = True
        while not self.stop_flag.is_set():
            snap = latest_proxy(max_age_sec=10.0)
            if snap:
                with self.state.lock:
                    self.state.price = float(snap["price"])
                    self.state.raw_symbol = "BTCUSD_PROXY_MEDIAN"
                    self.state.exchange_ts_utc = snap["ts_utc"]
                    self.state.receive_lag_ms = None
                    self.state.last_msg_monotonic = time.monotonic()
                    self.state.connected = True
                    self.state.last_error = None

                now = time.monotonic()
                if now - self._last_logged >= self.min_log_interval_sec:
                    self._last_logged = now
                    self.logger.write("btc_ref_ticks", {
                        "source": self.source,
                        "btc_ref_price": float(snap["price"]),
                        "raw_symbol": "BTCUSD_PROXY_MEDIAN",
                        "exchange_ts_utc": snap["ts_utc"],
                        "receive_lag_ms": "",
                    })
            else:
                self.state.last_error = "btc_index_proxy_missing_or_stale"

            time.sleep(0.5)

    def _on_open(self, ws):
        self.state.connected = True

    def _on_close(self, code, msg):
        self.state.connected = False
        self.state.last_error = f"btc_ws_closed:{code}:{msg}"

    def _on_error(self, err):
        self.state.last_error = f"btc_ws_error:{err}"

    def _on_message(self, ws, message):
        try:
            d = json.loads(message)
            price = d.get("p") or d.get("price")
            symbol = d.get("s") or d.get("symbol") or ""
            ts_ms = d.get("T") or d.get("E") or d.get("ts_ms")
            if price is None:
                return
            self.state.update(price, symbol, ts_ms)
            now = time.monotonic()
            if now - self._last_logged >= self.min_log_interval_sec:
                self._last_logged = now
                self.logger.write("btc_ref_ticks", {
                    "source": self.source,
                    "btc_ref_price": float(price),
                    "raw_symbol": symbol,
                    "exchange_ts_utc": ms_to_iso_z(ts_ms),
                    "receive_lag_ms": self.state.receive_lag_ms,
                })
        except Exception as e:
            self.state.last_error = f"btc_parse_error:{e}"

class KalshiWSState:
    def __init__(self, ticker):
        self.ticker = ticker
        self.book = Orderbook(ticker)
        self.connected = False
        self.sid = None
        self.last_error = None
        self.lock = threading.Lock()

    def age_sec(self):
        with self.lock:
            return self.book.age_sec()

    def best(self):
        with self.lock:
            return self.book.best()

    def ws_lag_ms(self):
        with self.lock:
            return self.book.ws_lag_ms()

class KalshiOrderbookWS(threading.Thread):
    def __init__(self, url, ticker, headers, state):
        super().__init__(daemon=True)
        self.url = url
        self.ticker = ticker
        self.headers = headers
        self.state = state
        self.stop_flag = threading.Event()

    def run(self):
        try:
            import websocket
        except Exception as e:
            self.state.last_error = f"missing_websocket_client:{e}"
            return

        if isinstance(self.headers, dict):
            header_list = [f"{k}: {v}" for k, v in self.headers.items()]
        else:
            header_list = self.headers  # Already a list
        while not self.stop_flag.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self.url,
                    header=header_list,
                    on_open=lambda w: self._on_open(w),
                    on_message=lambda w, m: self._on_message(w, m),
                    on_error=lambda w, e: self._on_error(e),
                    on_close=lambda w, code, msg: self._on_close(code, msg),
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                self.state.last_error = str(e)
            self.state.connected = False
            time.sleep(2)

    def _on_open(self, ws):
        self.state.connected = True
        sub = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": [self.ticker],
            },
        }
        ws.send(json.dumps(sub))

    def _on_close(self, code, msg):
        self.state.connected = False
        self.state.last_error = f"kalshi_ws_closed:{code}:{msg}"

    def _on_error(self, err):
        self.state.last_error = f"kalshi_ws_error:{err}"

    def _on_message(self, ws, message):
        try:
            d = json.loads(message)
            typ = d.get("type")
            msg = d.get("msg") or {}
            seq = d.get("seq")
            if typ == "subscribed":
                self.state.sid = msg.get("sid") or d.get("sid")
                return
            if typ == "orderbook_snapshot":
                with self.state.lock:
                    self.state.book.apply_snapshot_msg(msg, seq=seq)
                return
            if typ == "orderbook_delta":
                with self.state.lock:
                    self.state.book.apply_delta_msg(msg, seq=seq)
                return
            if typ == "error":
                self.state.last_error = f"kalshi_ws_msg_error:{msg}"
        except Exception as e:
            self.state.last_error = f"kalshi_parse_error:{e}"
