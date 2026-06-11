#!/usr/bin/env python3
import json
from datetime import datetime, timezone, timedelta

from .rule_kernel import construct_direct_and_reverse_candidates, evaluate_selected_direct_candidate, candidate_key_for_trade

def main():
    now = datetime.now(timezone.utc)
    close = now + timedelta(seconds=300)
    direct, reverse = construct_direct_and_reverse_candidates(
        market_ticker="TEST",
        now_utc=now,
        close_time_utc=close,
        btc_ref_price=100050.0,
        floor_strike=100000.0,
        yes_best_bid_c=52.0,
        yes_best_ask_c=54.0,
        no_best_bid_c=46.0,
        no_best_ask_c=48.0,
        settlement_result=None,
    )
    decision = evaluate_selected_direct_candidate(direct, already_traded_market=False)
    print(json.dumps({
        "candidate_key": candidate_key_for_trade(),
        "direct_side": direct["side"],
        "decision": decision["decision"],
        "reason": decision["reason"],
        "limit_price_c": decision.get("limit_price_c"),
        "expected_win_net_c_per_contract": decision.get("expected_win_net_c_per_contract"),
        "expected_loss_net_c_per_contract": decision.get("expected_loss_net_c_per_contract"),
    }, indent=2, sort_keys=True))
    return 0 if decision["decision"] == "TRADE" else 1

if __name__ == "__main__":
    raise SystemExit(main())
