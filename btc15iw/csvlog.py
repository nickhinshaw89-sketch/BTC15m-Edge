import csv
import os
import threading
from datetime import datetime, timezone

DECISION_FIELDS = [
    "ts_utc","strategy_id","candidate_key","market_ticker","decision","reason",
    "sec_to_close","side","side_logic","implied_side","contracts","btc_ref_price",
    "floor_strike","gap_usd","yes_best_bid_c","yes_best_ask_c","no_best_bid_c",
    "no_best_ask_c","entry_ask_c","same_side_bid_c","spread_c","limit_price_c",
    "fee_c_per_contract","slippage_stress_c_per_contract",
    "expected_win_net_c_per_contract","expected_loss_net_c_per_contract",
    "expected_win_net_c_10_contracts","expected_loss_net_c_10_contracts",
    "place_orders"
]

ORDER_FIELDS = [
    "ts_utc","strategy_id","market_ticker","side","contracts","limit_price_c",
    "entry_ask_c","order_mode","place_orders","order_submitted","order_id",
    "order_status","http_status","error","raw_json"
]

FILL_FIELDS = [
    "ts_utc","strategy_id","market_ticker","side","contracts_requested",
    "contracts_filled","avg_fill_price_c","order_id","fill_source","raw_json"
]

POSITION_FIELDS = [
    "ts_utc","strategy_id","market_ticker","side","contracts","avg_entry_price_c",
    "fee_c_per_contract","slippage_stress_c_per_contract","status","opened_at_utc",
    "closed_at_utc","settlement_result","realized_pnl_c"
]

MARKET_STATE_FIELDS = [
    "ts_utc","market_ticker","open_time_utc","close_time_utc","floor_strike",
    "floor_strike_source","btc_ref_price","sec_to_close","yes_best_bid_c",
    "yes_best_ask_c","no_best_bid_c","no_best_ask_c","yes_bid_plus_no_bid_c",
    "ws_seq","ws_lag_ms"
]

BTC_REF_FIELDS = [
    "ts_utc","source","btc_ref_price","raw_symbol","exchange_ts_utc","receive_lag_ms"
]

HEALTH_FIELDS = [
    "ts_utc","strategy_id","kalshi_ws_connected","btc_ref_ws_connected",
    "active_market_ticker","last_kalshi_msg_age_sec","last_btc_ref_msg_age_sec",
    "decision_count","trade_count","open_position_count","error_count","last_error"
]

REJECT_SUMMARY_FIELDS = ["ts_utc","strategy_id","reason","count"]


class CsvLogger:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.lock = threading.Lock()
        os.makedirs(state_dir, exist_ok=True)
        self.paths = {
            "decisions": os.path.join(state_dir, "btc15_implied_winner_decisions.csv"),
            "orders": os.path.join(state_dir, "btc15_implied_winner_orders.csv"),
            "fills": os.path.join(state_dir, "btc15_implied_winner_fills.csv"),
            "positions": os.path.join(state_dir, "btc15_implied_winner_positions.csv"),
            "market_state": os.path.join(state_dir, "btc15_implied_winner_market_state.csv"),
            "btc_ref_ticks": os.path.join(state_dir, "btc15_implied_winner_btc_ref_ticks.csv"),
            "health": os.path.join(state_dir, "btc15_implied_winner_health.csv"),
            "reject_summary": os.path.join(state_dir, "btc15_implied_winner_reject_summary.csv"),
        }
        self.fields = {
            "decisions": DECISION_FIELDS,
            "orders": ORDER_FIELDS,
            "fills": FILL_FIELDS,
            "positions": POSITION_FIELDS,
            "market_state": MARKET_STATE_FIELDS,
            "btc_ref_ticks": BTC_REF_FIELDS,
            "health": HEALTH_FIELDS,
            "reject_summary": REJECT_SUMMARY_FIELDS,
        }
        for name, fields in self.fields.items():
            self.ensure_header(self.paths[name], fields)

    @staticmethod
    def now_iso():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def ensure_header(self, path, fields):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        with open(path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()

    def write(self, name, row):
        fields = self.fields[name]
        out = {k: row.get(k, "") for k in fields}
        if not out.get("ts_utc"):
            out["ts_utc"] = self.now_iso()
        with self.lock:
            with open(self.paths[name], "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writerow(out)
                f.flush()
                os.fsync(f.fileno())
