#!/usr/bin/env python3
import argparse
import json
import os
import sys

from .config import load_config
from .market_source import load_market_spec
from .rule_kernel import candidate_key_for_trade, STRATEGY_ID

def check_import(name):
    try:
        __import__(name)
        return True, ""
    except Exception as e:
        return False, str(e)

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/kalshi-research/btc15_implied_winner_widest/config.env")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    market = load_market_spec(cfg)
    ws_ok, ws_err = check_import("websocket")
    crypto_ok, crypto_err = check_import("cryptography")

    result = {
        "strategy_id": STRATEGY_ID,
        "candidate_key": candidate_key_for_trade(),
        "config_path": args.config,
        "state_dir": cfg.state_dir,
        "place_orders": cfg.place_orders,
        "contracts": cfg.contracts,
        "active_market_ticker": market.ticker,
        "market_close_time_utc": str(market.close_time_utc) if market.close_time_utc else None,
        "floor_strike": market.floor_strike,
        "floor_strike_source": market.floor_strike_source,
        "websocket_client_import_ok": ws_ok,
        "websocket_client_error": ws_err,
        "cryptography_import_ok": crypto_ok,
        "cryptography_error": crypto_err,
        "kalshi_api_key_present": bool(cfg.kalshi_api_key_id),
        "kalshi_private_key_path_present": bool(cfg.kalshi_private_key_path),
        "kalshi_private_key_file_exists": bool(cfg.kalshi_private_key_path and os.path.exists(cfg.kalshi_private_key_path)),
        "uses_rest_for_quote_data": False,
        "uses_rest_for_btc_price": False,
        "uses_rest_for_market_scan": False,
    }

    blockers = []
    if not ws_ok:
        blockers.append("missing_python_websocket_client")
    if not crypto_ok:
        blockers.append("missing_python_cryptography")
    if not market.ticker:
        blockers.append("missing_active_market_ticker")
    if not market.close_time_utc:
        blockers.append("missing_close_time")
    if market.floor_strike is None:
        blockers.append("missing_floor_strike")
    if not result["kalshi_api_key_present"]:
        blockers.append("missing_kalshi_api_key")
    if not result["kalshi_private_key_path_present"] or not result["kalshi_private_key_file_exists"]:
        blockers.append("missing_kalshi_private_key_file")

    result["blockers"] = blockers
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if blockers else 0

if __name__ == "__main__":
    raise SystemExit(main())
