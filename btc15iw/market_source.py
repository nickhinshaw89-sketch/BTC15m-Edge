import json
import os
from dataclasses import dataclass
from typing import Optional
from .timeutils import parse_utc

@dataclass
class MarketSpec:
    ticker: Optional[str]
    open_time_utc: object
    close_time_utc: object
    floor_strike: Optional[float]
    floor_strike_source: str

def _num(v):
    if v is None or str(v).strip() == "":
        return None
    return float(v)

def read_market_state_file(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    return data

def load_market_spec(cfg):
    data = read_market_state_file(cfg.active_market_state_file)
    ticker = cfg.active_market_ticker or data.get("market_ticker") or data.get("ticker")
    open_t = parse_utc(cfg.market_open_time_utc or data.get("open_time_utc") or data.get("open_time"))
    close_t = parse_utc(cfg.market_close_time_utc or data.get("close_time_utc") or data.get("close_time"))
    fs = cfg.floor_strike
    if fs is None:
        fs = _num(data.get("floor_strike"))
    source = cfg.floor_strike_source
    if data.get("floor_strike_source"):
        source = data.get("floor_strike_source")
    return MarketSpec(ticker=ticker, open_time_utc=open_t, close_time_utc=close_t, floor_strike=fs, floor_strike_source=source)
