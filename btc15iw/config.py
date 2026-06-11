import os
from dataclasses import dataclass
from typing import Optional

def load_env_file(path):
    if not path or not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def getenv_bool(name, default=False):
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def getenv_float(name, default=None):
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    return float(v)

def getenv_int(name, default=None):
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    return int(float(v))

@dataclass
class Config:
    app_dir: str
    state_dir: str
    strategy_id: str
    place_orders: bool
    contracts: int
    active_market_ticker: Optional[str]
    active_market_state_file: Optional[str]
    market_open_time_utc: Optional[str]
    market_close_time_utc: Optional[str]
    floor_strike: Optional[float]
    floor_strike_source: str
    kalshi_ws_url: str
    kalshi_api_base: str
    kalshi_api_key_id: Optional[str]
    kalshi_private_key_path: Optional[str]
    btc_ref_ws_url: str
    btc_ref_source: str
    btc_ref_symbol: str
    loop_sleep_sec: float
    health_every_sec: float
    market_state_every_sec: float
    btc_tick_log_min_interval_sec: float
    live_order_time_in_force: str
    live_order_post_only: bool

def load_config(config_path=None):
    if config_path:
        load_env_file(config_path)
    app_dir = os.getenv("APP_DIR", "/opt/kalshi-research/btc15_implied_winner_widest")
    state_dir = os.getenv("STATE_DIR", "/var/lib/kalshi-reversion/btc15_implied_winner_widest")
    return Config(
        app_dir=app_dir,
        state_dir=state_dir,
        strategy_id=os.getenv("STRATEGY_ID", "BTC15M_IMPLIED_WINNER_WIDEST_V1"),
        place_orders=getenv_bool("PLACE_ORDERS", False),
        contracts=getenv_int("CONTRACTS", 10),
        active_market_ticker=os.getenv("ACTIVE_MARKET_TICKER") or None,
        active_market_state_file=os.getenv("ACTIVE_MARKET_STATE_FILE") or None,
        market_open_time_utc=os.getenv("MARKET_OPEN_TIME_UTC") or None,
        market_close_time_utc=os.getenv("MARKET_CLOSE_TIME_UTC") or None,
        floor_strike=getenv_float("FLOOR_STRIKE", None),
        floor_strike_source=os.getenv("FLOOR_STRIKE_SOURCE", "config_or_btc_ref_at_open"),
        kalshi_ws_url=os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2"),
        kalshi_api_base=os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"),
        kalshi_api_key_id=os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_ACCESS_KEY") or None,
        kalshi_private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH") or None,
        btc_ref_ws_url=os.getenv("BTC_REF_WS_URL", "wss://stream.binance.com:9443/ws/btcusdt@trade"),
        btc_ref_source=os.getenv("BTC_REF_WS_SOURCE", "binance_trade_ws"),
        btc_ref_symbol=os.getenv("BTC_REF_SYMBOL", "BTCUSDT"),
        loop_sleep_sec=getenv_float("LOOP_SLEEP_SEC", 0.10),
        health_every_sec=getenv_float("HEALTH_EVERY_SEC", 15.0),
        market_state_every_sec=getenv_float("MARKET_STATE_EVERY_SEC", 1.0),
        btc_tick_log_min_interval_sec=getenv_float("BTC_TICK_LOG_MIN_INTERVAL_SEC", 2.0),
        live_order_time_in_force=os.getenv("LIVE_ORDER_TIME_IN_FORCE", "immediate_or_cancel"),
        live_order_post_only=getenv_bool("LIVE_ORDER_POST_ONLY", False),
    )
