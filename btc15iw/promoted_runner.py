#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from datetime import timezone

from .config import load_config
from .csvlog import CsvLogger
from .kalshi_auth import load_private_key, websocket_headers
from .market_source import load_market_spec
from .order_client import KalshiOrderClient
from .promotion_gate import evaluate_promoted_gate
from .quote_fallback import fallback_best_quote
from .rule_kernel import (
    STRATEGY_ID,
    construct_direct_and_reverse_candidates,
    evaluate_selected_direct_candidate,
)
from .state import StateStore
from .timeutils import utc_now, iso_z
from .ws_clients import BTCRefState, BTCRefWS, KalshiWSState, KalshiOrderbookWS


def reject_row(reason, cfg, market=None, extra=None):
    out = {
        "decision": "REJECT",
        "reason": reason,
        "strategy_id": cfg.strategy_id,
        "market_ticker": market.ticker if market else "",
        "place_orders": int(cfg.place_orders),
    }
    if extra:
        out.update(extra)
    return out


def acquire_lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f.write(str(os.getpid()))
    f.flush()
    return f

PROMOTED_RULES_PATH = Path("/var/lib/kalshi-learning/reports/promoted_rules.csv")
ACTIVE_EDGE_KEYS = {
    "side=NO|spread_bucket=spread_0_1|entry_bucket=entry_50_59|gap_bucket=gap_25_50",
    "side=NO|spread_bucket=spread_0_1|ttc_bucket=ttc_10m_15m|entry_bucket=entry_50_59|gap_bucket=gap_25_50",
}

def promoted_gate_open():
    return evaluate_promoted_gate(PROMOTED_RULES_PATH, ACTIVE_EDGE_KEYS)


def write_health(logger, cfg, market, kstate, bstate, counters, last_error):
    logger.write("health", {
        "strategy_id": cfg.strategy_id,
        "kalshi_ws_connected": int(bool(kstate and kstate.connected)),
        "btc_ref_ws_connected": int(bool(bstate and bstate.connected)),
        "active_market_ticker": market.ticker if market else "",
        "last_kalshi_msg_age_sec": kstate.age_sec() if kstate else "",
        "last_btc_ref_msg_age_sec": bstate.age_sec() if bstate else "",
        "decision_count": counters.get("decision_count", 0),
        "trade_count": counters.get("trade_count", 0),
        "open_position_count": counters.get("open_position_count", 0),
        "error_count": counters.get("error_count", 0),
        "last_error": last_error or "",
    })

def write_market_state(logger, market, kstate, bstate):
    best = kstate.best()
    if not any(best.get(k) is not None for k in ("yes_best_bid_c", "yes_best_ask_c", "no_best_bid_c", "no_best_ask_c")):
        ticker = getattr(market, "market_ticker", None) or getattr(market, "ticker", None)
        fb = fallback_best_quote(ticker)
        if fb:
            best = fb if kstate else {}
    now = utc_now()
    sec_to_close = None
    if market and market.close_time_utc:
        sec_to_close = (market.close_time_utc - now).total_seconds()
    logger.write("market_state", {
        "market_ticker": market.ticker if market else "",
        "open_time_utc": iso_z(market.open_time_utc) if market else "",
        "close_time_utc": iso_z(market.close_time_utc) if market else "",
        "floor_strike": market.floor_strike if market else "",
        "floor_strike_source": market.floor_strike_source if market else "",
        "btc_ref_price": bstate.price if bstate else "",
        "sec_to_close": sec_to_close,
        "yes_best_bid_c": best.get("yes_best_bid_c", ""),
        "yes_best_ask_c": best.get("yes_best_ask_c", ""),
        "no_best_bid_c": best.get("no_best_bid_c", ""),
        "no_best_ask_c": best.get("no_best_ask_c", ""),
        "yes_bid_plus_no_bid_c": best.get("yes_bid_plus_no_bid_c", ""),
        "ws_seq": best.get("ws_seq", ""),
        "ws_lag_ms": kstate.ws_lag_ms() if kstate else "",
    })

def decision_from_live(cfg, market, kstate, bstate, store):
    if not market or not market.ticker:
        return reject_row("missing_active_market_ticker", cfg, market)
    if not market.close_time_utc:
        return reject_row("missing_close_time", cfg, market)
    if market.floor_strike is None:
        return reject_row("missing_floor_strike", cfg, market)

    try:
        with open(cfg.active_market_state_file) as f:
            active_state = json.load(f)
        bridge_px = active_state.get("live_proxy_median")
        if bridge_px is not None:
            bstate.price = float(bridge_px)
    except Exception:
        pass

    if bstate.price is None:
        return reject_row("missing_btc_ref_price", cfg, market)

    best = kstate.best()
    if not any(best.get(k) is not None for k in ("yes_best_bid_c", "yes_best_ask_c", "no_best_bid_c", "no_best_ask_c")):
        fb = fallback_best_quote(market.ticker)
        if fb:
            best = fb

    now = utc_now()
    direct, reverse = construct_direct_and_reverse_candidates(
        market_ticker=market.ticker,
        now_utc=now,
        close_time_utc=market.close_time_utc,
        btc_ref_price=bstate.price,
        floor_strike=market.floor_strike,
        yes_best_bid_c=best.get("yes_best_bid_c"),
        yes_best_ask_c=best.get("yes_best_ask_c"),
        no_best_bid_c=best.get("no_best_bid_c"),
        no_best_ask_c=best.get("no_best_ask_c"),
        settlement_result=None,
    )

    if store.already_traded(market.ticker):
        return reject_row("already_traded_market", cfg, market)

    gate_ok, gate_reason = promoted_gate_open()
    if not gate_ok:
        return reject_row(gate_reason, cfg, market)

    hour_utc = now.hour

    def valid_base(c, require_gap_25_50=False):
        if c is None:
            return False, "missing_candidate"
        if c.get("sec_to_close") is None or not (600 <= float(c["sec_to_close"]) <= 900):
            return False, "outside_promoted_ttc_10m_15m"
        if c.get("entry_ask_c") is None or c.get("same_side_bid_c") is None:
            return False, "missing_entry_quote"
        if not (50 <= float(c["entry_ask_c"]) <= 59):
            return False, "entry_outside_50_59"
        if c.get("spread_c") is None or float(c["spread_c"]) < -0.0001 or float(c["spread_c"]) > 1:
            return False, "spread_outside_0_1"
        if c.get("yes_best_bid_c") is None or c.get("no_best_bid_c") is None:
            return False, "missing_bid_for_crossed_book_check"
        if float(c["yes_best_bid_c"]) + float(c["no_best_bid_c"]) > 100.0001:
            return False, "crossed_book_bid_sum"
        if require_gap_25_50:
            if c.get("gap_usd") is None or not (25 <= float(c["gap_usd"]) < 50):
                return False, "gap_outside_25_50"
        return True, ""

    # PROMOTED_1_DIRECT_NO_spread_0_1_ttc_10m_15m_entry_50_59
    r1_cut_hours = {3,4,6,9,11,12,14,15,17,20,23}
    ok, reason = valid_base(direct, require_gap_25_50=True)
    if ok and direct.get("side") == "NO" and hour_utc not in r1_cut_hours:
        out = dict(direct)
        out.update({"decision":"TRADE","reason":"promoted_rule_1_direct_no_hour_cut","strategy_id":cfg.strategy_id,"candidate_key":"PROMOTED_1_DIRECT_NO_HOUR_CUT","limit_price_c":direct["entry_ask_c"],"place_orders":int(cfg.place_orders)})
        return out

    # PROMOTED_2_REVERSE_YES_spread_0_1_entry_50_59_gap_25_50
    r2_cut_hours = {6,7,9,11,13,14,16,18,23}
    ok, reason = valid_base(reverse, require_gap_25_50=True)
    if False and ok and reverse.get("side") == "YES" and hour_utc not in r2_cut_hours:
        out = dict(reverse)
        out.update({"decision":"TRADE","reason":"promoted_rule_2_reverse_yes_hour_cut","strategy_id":cfg.strategy_id,"candidate_key":"PROMOTED_2_REVERSE_YES_HOUR_CUT","limit_price_c":reverse["entry_ask_c"],"place_orders":int(cfg.place_orders)})
        return out

    return reject_row("no_promoted_rule_match", cfg, market, {"hour_utc":hour_utc})

def write_order_and_position(logger, cfg, decision, order_result=None):
    order_id = ""
    status = "PAPER_ACCEPTED"
    http_status = ""
    error = ""
    raw_json = ""
    submitted = 0

    if order_result is not None:
        submitted = 1
        order_id = order_result.get("order_id") or ""
        http_status = order_result.get("http_status", "")
        raw_json = json.dumps(order_result.get("body", {}), sort_keys=True)
        status = "ACCEPTED" if int(http_status or 0) in (200, 201) else "REJECTED"
        if status == "REJECTED":
            error = raw_json[:1000]

    logger.write("orders", {
        "strategy_id": cfg.strategy_id,
        "market_ticker": decision.get("market_ticker"),
        "side": decision.get("side"),
        "contracts": decision.get("contracts"),
        "limit_price_c": decision.get("limit_price_c"),
        "entry_ask_c": decision.get("entry_ask_c"),
        "order_mode": "LIVE" if cfg.place_orders else "PAPER",
        "place_orders": int(cfg.place_orders),
        "order_submitted": submitted,
        "order_id": order_id,
        "order_status": status,
        "http_status": http_status,
        "error": error,
        "raw_json": raw_json,
    })

    if order_result is not None:
        try:
            ok_live_position = int(http_status or 0) in (200, 201) and bool(order_id)
        except Exception:
            ok_live_position = False
        if not ok_live_position:
            return

    logger.write("positions", {
        "strategy_id": cfg.strategy_id,
        "market_ticker": decision.get("market_ticker"),
        "side": decision.get("side"),
        "contracts": decision.get("contracts"),
        "avg_entry_price_c": decision.get("entry_ask_c"),
        "fee_c_per_contract": decision.get("fee_c_per_contract"),
        "slippage_stress_c_per_contract": decision.get("slippage_stress_c_per_contract"),
        "status": "OPEN_PAPER" if not cfg.place_orders else "OPEN_LIVE_ACCEPTED",
        "opened_at_utc": decision.get("time_utc"),
        "closed_at_utc": "",
        "settlement_result": "",
        "realized_pnl_c": "",
    })

def run_once(config_path):
    cfg = load_config(config_path)
    os.makedirs(cfg.state_dir, exist_ok=True)
    logger = CsvLogger(cfg.state_dir)
    market = load_market_spec(cfg)
    if not market.ticker:
        logger.write("decisions", reject_row("missing_active_market_ticker", cfg, market))
        print("BLOCKER missing_active_market_ticker")
        return 2
    if not market.close_time_utc:
        logger.write("decisions", reject_row("missing_close_time", cfg, market))
        print("BLOCKER missing_close_time")
        return 2
    if market.floor_strike is None:
        logger.write("decisions", reject_row("missing_floor_strike", cfg, market))
        print("BLOCKER missing_floor_strike")
        return 2
    print(json.dumps({
        "status": "preflight_ok",
        "market_ticker": market.ticker,
        "close_time_utc": iso_z(market.close_time_utc),
        "floor_strike": market.floor_strike,
        "place_orders": cfg.place_orders,
        "state_dir": cfg.state_dir,
    }, indent=2))
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/opt/kalshi-research/btc15_implied_winner_widest/config.env")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    os.makedirs(cfg.state_dir, exist_ok=True)
    lock = acquire_lock(os.path.join(cfg.state_dir, "runner.lock"))
    logger = CsvLogger(cfg.state_dir)
    store = StateStore(os.path.join(cfg.state_dir, "state.json"))

    if args.once:
        return run_once(args.config)

    market = load_market_spec(cfg)
    counters = {"decision_count": 0, "trade_count": 0, "open_position_count": 0, "error_count": 0}
    last_error = ""

    if not market.ticker or not market.close_time_utc or market.floor_strike is None:
        reason = "missing_active_market_dependency"
        if not market.ticker:
            reason = "missing_active_market_ticker"
        elif not market.close_time_utc:
            reason = "missing_close_time"
        elif market.floor_strike is None:
            reason = "missing_floor_strike"
        row = reject_row(reason, cfg, market)
        logger.write("decisions", row)
        write_health(logger, cfg, market, None, None, counters, reason)
        print(f"BLOCKER {reason}")
        return 2

    private_key = None
    if cfg.kalshi_api_key_id and cfg.kalshi_private_key_path:
        private_key = load_private_key(cfg.kalshi_private_key_path)

    if private_key is None:
        logger.write("decisions", reject_row("missing_kalshi_auth", cfg, market))
        print("BLOCKER missing_kalshi_auth")
        return 2

    bstate = BTCRefState()
    kstate = KalshiWSState(market.ticker)
    btc_ws = BTCRefWS(cfg.btc_ref_ws_url, bstate, logger, source=cfg.btc_ref_source, min_log_interval_sec=cfg.btc_tick_log_min_interval_sec)
    kalshi_ws = KalshiOrderbookWS(cfg.kalshi_ws_url, market.ticker, websocket_headers(cfg.kalshi_api_key_id, private_key), kstate)
    btc_ws.start()
    kalshi_ws.start()

    order_client = None
    if cfg.place_orders:
        order_client = KalshiOrderClient(
            cfg.kalshi_api_base,
            cfg.kalshi_api_key_id,
            private_key,
            time_in_force=cfg.live_order_time_in_force,
            post_only=cfg.live_order_post_only,
        )

    last_health = 0.0
    last_market = 0.0
    last_spec_reload = 0.0

    while True:
        now_m = time.monotonic()

        if now_m - last_spec_reload >= 1.0:
            last_spec_reload = now_m
            new_market = load_market_spec(cfg)
            if new_market.ticker and new_market.ticker != market.ticker:
                try:
                    kalshi_ws.stop_flag.set()
                except Exception:
                    pass
                market = new_market
                kstate = KalshiWSState(market.ticker)
                kalshi_ws = KalshiOrderbookWS(
                    cfg.kalshi_ws_url,
                    market.ticker,
                    websocket_headers(cfg.kalshi_api_key_id, private_key),
                    kstate,
                )
                kalshi_ws.start()
                logger.write("health", {
                    "strategy_id": cfg.strategy_id,
                    "active_market_ticker": market.ticker,
                    "last_error": "market_reloaded_from_active_state_file",
                })
            elif new_market.ticker == market.ticker:
                market = new_market
        if now_m - last_health >= cfg.health_every_sec:
            last_health = now_m
            last_error = kstate.last_error or bstate.last_error or last_error
            write_health(logger, cfg, market, kstate, bstate, counters, last_error)

        if now_m - last_market >= cfg.market_state_every_sec:
            last_market = now_m
            write_market_state(logger, market, kstate, bstate)

        decision = decision_from_live(cfg, market, kstate, bstate, store)
        counters["decision_count"] += 1
        logger.write("decisions", decision)

        if decision.get("decision") == "TRADE":
            order_result = None
            if cfg.place_orders:
                try:
                    order_result = order_client.place_buy(
                        decision["market_ticker"],
                        decision["side"],
                        decision["contracts"],
                        decision["limit_price_c"],
                        cfg.strategy_id,
                    )
                except Exception as e:
                    counters["error_count"] += 1
                    last_error = f"order_error:{e}"
                    logger.write("orders", {
                        "strategy_id": cfg.strategy_id,
                        "market_ticker": decision.get("market_ticker"),
                        "side": decision.get("side"),
                        "contracts": decision.get("contracts"),
                        "limit_price_c": decision.get("limit_price_c"),
                        "entry_ask_c": decision.get("entry_ask_c"),
                        "order_mode": "LIVE",
                        "place_orders": 1,
                        "order_submitted": 0,
                        "order_status": "ERROR",
                        "error": last_error,
                    })
                else:
                    write_order_and_position(logger, cfg, decision, order_result=order_result)
                    http_status = int(order_result.get("http_status") or 0)
                    order_id = order_result.get("order_id")
                    if http_status in (200, 201) and order_id:
                        store.mark_traded(decision["market_ticker"], decision)
                        counters["open_position_count"] += 1
                        counters["trade_count"] += 1
            else:
                write_order_and_position(logger, cfg, decision, order_result=None)
                store.mark_traded(decision["market_ticker"], decision)
                counters["open_position_count"] += 1
                counters["trade_count"] += 1

        time.sleep(cfg.loop_sleep_sec)

if __name__ == "__main__":
    raise SystemExit(main())
