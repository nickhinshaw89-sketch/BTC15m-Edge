#!/usr/bin/env python3
"""
BTC15M_IMPLIED_WINNER_WIDEST_V1 rule kernel.

Source-of-truth rule package for:
10s-2m, ask 40-59c, spread <=5c, gap >=$25
fees + 2c slippage
one entry per market
direct side = implied winner
reverse side = opposite implied winner
"""

import math
from datetime import datetime


STRATEGY_ID = "BTC15M_IMPLIED_WINNER_WIDEST_V1"

CONTRACTS = 10

SELECTED_CANDIDATE_KEY = ("10s-2m", "40-59", "5", "25")

TTC_WINDOW_NAME = "10s-2m"
TTC_MIN_SEC = 10.0
TTC_MAX_SEC = 120.0

ASK_WINDOW_NAME = "40-59"
ASK_MIN_C = 40.0
ASK_MAX_C = 59.0

SPREAD_MAX_C = 5.0
GAP_MIN_USD = 25.0

SLIPPAGE_STRESS_C = 2.0

YES = "YES"
NO = "NO"


def parse_time(value):
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fnum(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def cents(value):
    """
    Raw files may store prices as 0.42 or 42. Normalize to cents.
    """
    v = fnum(value)
    if v is None:
        return None
    if -1.5 <= v <= 1.5:
        return v * 100.0
    return v


def normalize_result(value):
    s = str(value or "").strip().lower()
    if s == "yes":
        return YES
    if s == "no":
        return NO
    return None


def opposite_side(side):
    if side == YES:
        return NO
    if side == NO:
        return YES
    raise ValueError(f"bad side: {side}")


def settlement_result_from_raw_row(row):
    return normalize_result(row.get("result"))


def settlement_result_from_final_price(final_btc_price, floor_strike):
    if final_btc_price is None or floor_strike is None:
        return None
    return YES if float(final_btc_price) >= float(floor_strike) else NO


def implied_winner_side(btc_ref_price, floor_strike):
    if btc_ref_price is None or floor_strike is None:
        return None
    return YES if float(btc_ref_price) >= float(floor_strike) else NO


def side_was_implied_winner(side, btc_ref_price, floor_strike):
    winner = implied_winner_side(btc_ref_price, floor_strike)
    if winner is None:
        return None
    return 1 if side == winner else 0


def kalshi_fee_cents_one_contract(ask_c):
    p = ask_c / 100.0
    fee_c = 0.07 * ask_c * (1.0 - p)
    return float(math.ceil(fee_c - 1e-12))


def gross_hold_pnl_c(side, ask_c, settlement_result):
    result = normalize_result(settlement_result)
    if result is None:
        return None
    if side == YES:
        return 100.0 - ask_c if result == YES else -ask_c
    if side == NO:
        return 100.0 - ask_c if result == NO else -ask_c
    raise ValueError(f"bad side: {side}")


def net_hold_pnl_c(side, ask_c, settlement_result):
    gross_c = gross_hold_pnl_c(side, ask_c, settlement_result)
    if gross_c is None:
        return None
    fee_c = kalshi_fee_cents_one_contract(ask_c)
    return gross_c - fee_c - SLIPPAGE_STRESS_C


def expected_win_net_c(ask_c):
    fee_c = kalshi_fee_cents_one_contract(ask_c)
    return (100.0 - ask_c) - fee_c - SLIPPAGE_STRESS_C


def expected_loss_net_c(ask_c):
    fee_c = kalshi_fee_cents_one_contract(ask_c)
    return -ask_c - fee_c - SLIPPAGE_STRESS_C


def seconds_to_close(now_utc, close_time_utc):
    if now_utc is None or close_time_utc is None:
        return None
    return (close_time_utc - now_utc).total_seconds()


def passes_sample_timing_rule(sec_to_close):
    """
    Final selected rule timing:
    10 seconds <= sec_to_close <= 300 seconds.

    The widest fee/slip validator scanned every raw row.
    It did not use the earlier 5-second sampling bin.
    The dedupe rule kept the first qualifying row per market.
    """
    if sec_to_close is None:
        return False
    return TTC_MIN_SEC <= sec_to_close <= TTC_MAX_SEC


def passes_ask_filter(entry_ask_c):
    if entry_ask_c is None:
        return False
    return ASK_MIN_C <= entry_ask_c <= ASK_MAX_C


def passes_spread_filter(entry_ask_c, same_side_bid_c):
    if entry_ask_c is None or same_side_bid_c is None:
        return False
    spread_c = entry_ask_c - same_side_bid_c
    if spread_c < -0.0001:
        return False
    return spread_c <= SPREAD_MAX_C


def passes_gap_filter(btc_ref_price, floor_strike):
    if btc_ref_price is None or floor_strike is None:
        return False
    gap_usd = abs(float(btc_ref_price) - float(floor_strike))
    return gap_usd >= GAP_MIN_USD


def selected_candidate_key():
    return (
        TTC_WINDOW_NAME,
        ASK_WINDOW_NAME,
        str(int(SPREAD_MAX_C)),
        str(int(GAP_MIN_USD)),
    )


def candidate_key_for_trade():
    return selected_candidate_key()


def crossed_book_reject(yes_best_bid_c, no_best_bid_c):
    if yes_best_bid_c is None or no_best_bid_c is None:
        return True
    return yes_best_bid_c + no_best_bid_c > 100.0001


def quote_for_side(side, yes_best_bid_c, yes_best_ask_c, no_best_bid_c, no_best_ask_c):
    if side == YES:
        return yes_best_ask_c, yes_best_bid_c
    if side == NO:
        return no_best_ask_c, no_best_bid_c
    raise ValueError(f"bad side: {side}")


def construct_candidate(
    *,
    market_ticker,
    now_utc,
    close_time_utc,
    btc_ref_price,
    floor_strike,
    yes_best_bid_c,
    yes_best_ask_c,
    no_best_bid_c,
    no_best_ask_c,
    side,
    side_logic,
    settlement_result=None,
):
    sec_to_close = seconds_to_close(now_utc, close_time_utc)
    entry_ask_c, same_side_bid_c = quote_for_side(
        side,
        yes_best_bid_c,
        yes_best_ask_c,
        no_best_bid_c,
        no_best_ask_c,
    )
    spread_c = None
    if entry_ask_c is not None and same_side_bid_c is not None:
        spread_c = entry_ask_c - same_side_bid_c

    gap_usd = None
    if btc_ref_price is not None and floor_strike is not None:
        gap_usd = abs(float(btc_ref_price) - float(floor_strike))

    implied_side = implied_winner_side(btc_ref_price, floor_strike)

    fee_c = None
    expected_win_c = None
    expected_loss_c = None
    if entry_ask_c is not None:
        fee_c = kalshi_fee_cents_one_contract(entry_ask_c)
        expected_win_c = expected_win_net_c(entry_ask_c)
        expected_loss_c = expected_loss_net_c(entry_ask_c)

    gross_pnl_c = None
    net_pnl_c = None
    win_after_fee_slip = None
    if settlement_result is not None and entry_ask_c is not None:
        gross_pnl_c = gross_hold_pnl_c(side, entry_ask_c, settlement_result)
        net_pnl_c = net_hold_pnl_c(side, entry_ask_c, settlement_result)
        if net_pnl_c is not None:
            win_after_fee_slip = 1 if net_pnl_c > 0 else 0

    return {
        "strategy_id": STRATEGY_ID,
        "candidate_key": candidate_key_for_trade(),
        "market_ticker": market_ticker,
        "time_utc": now_utc.isoformat() if now_utc else "",
        "close_time_utc": close_time_utc.isoformat() if close_time_utc else "",
        "sec_to_close": sec_to_close,
        "side": side,
        "side_logic": side_logic,
        "implied_side": implied_side,
        "side_is_implied_winner": 1 if side == implied_side else 0,
        "contracts": CONTRACTS,
        "btc_ref_price": btc_ref_price,
        "floor_strike": floor_strike,
        "gap_usd": gap_usd,
        "yes_best_bid_c": yes_best_bid_c,
        "yes_best_ask_c": yes_best_ask_c,
        "no_best_bid_c": no_best_bid_c,
        "no_best_ask_c": no_best_ask_c,
        "entry_ask_c": entry_ask_c,
        "same_side_bid_c": same_side_bid_c,
        "spread_c": spread_c,
        "fee_c_per_contract": fee_c,
        "slippage_stress_c_per_contract": SLIPPAGE_STRESS_C,
        "expected_win_net_c_per_contract": expected_win_c,
        "expected_loss_net_c_per_contract": expected_loss_c,
        "expected_win_net_c_10_contracts": expected_win_c * CONTRACTS if expected_win_c is not None else None,
        "expected_loss_net_c_10_contracts": expected_loss_c * CONTRACTS if expected_loss_c is not None else None,
        "settlement_result": settlement_result,
        "gross_pnl_c_per_contract": gross_pnl_c,
        "net_pnl_c_per_contract": net_pnl_c,
        "net_pnl_c_10_contracts": net_pnl_c * CONTRACTS if net_pnl_c is not None else None,
        "win_after_fee_slip": win_after_fee_slip,
    }


def construct_direct_and_reverse_candidates(
    *,
    market_ticker,
    now_utc,
    close_time_utc,
    btc_ref_price,
    floor_strike,
    yes_best_bid_c,
    yes_best_ask_c,
    no_best_bid_c,
    no_best_ask_c,
    settlement_result=None,
):
    direct_side = implied_winner_side(btc_ref_price, floor_strike)
    if direct_side is None:
        return None, None
    reverse_side = opposite_side(direct_side)

    direct = construct_candidate(
        market_ticker=market_ticker,
        now_utc=now_utc,
        close_time_utc=close_time_utc,
        btc_ref_price=btc_ref_price,
        floor_strike=floor_strike,
        yes_best_bid_c=yes_best_bid_c,
        yes_best_ask_c=yes_best_ask_c,
        no_best_bid_c=no_best_bid_c,
        no_best_ask_c=no_best_ask_c,
        side=direct_side,
        side_logic="DIRECT_IMPLIED_WINNER",
        settlement_result=settlement_result,
    )

    reverse = construct_candidate(
        market_ticker=market_ticker,
        now_utc=now_utc,
        close_time_utc=close_time_utc,
        btc_ref_price=btc_ref_price,
        floor_strike=floor_strike,
        yes_best_bid_c=yes_best_bid_c,
        yes_best_ask_c=yes_best_ask_c,
        no_best_bid_c=no_best_bid_c,
        no_best_ask_c=no_best_ask_c,
        side=reverse_side,
        side_logic="REVERSE_ANTI_IMPLIED_WINNER",
        settlement_result=settlement_result,
    )

    return direct, reverse


def reject_candidate(reason, **extra):
    out = {
        "decision": "REJECT",
        "reason": reason,
        "strategy_id": STRATEGY_ID,
        "candidate_key": candidate_key_for_trade(),
    }
    out.update(extra)
    return out


def evaluate_selected_direct_candidate(candidate, already_traded_market):
    if candidate is None:
        return reject_candidate("missing_candidate")

    if already_traded_market:
        return reject_candidate(
            "already_traded_market",
            market_ticker=candidate.get("market_ticker"),
        )

    if candidate["side_logic"] != "DIRECT_IMPLIED_WINNER":
        return reject_candidate(
            "not_direct_implied_winner_candidate",
            market_ticker=candidate.get("market_ticker"),
            side_logic=candidate.get("side_logic"),
        )

    if not passes_sample_timing_rule(candidate["sec_to_close"]):
        return reject_candidate(
            "outside_ttc_window",
            market_ticker=candidate["market_ticker"],
            sec_to_close=candidate["sec_to_close"],
            ttc_min_sec=TTC_MIN_SEC,
            ttc_max_sec=TTC_MAX_SEC,
        )

    if candidate["yes_best_bid_c"] is None or candidate["no_best_bid_c"] is None:
        return reject_candidate(
            "missing_bid_for_crossed_book_check",
            market_ticker=candidate["market_ticker"],
        )

    if crossed_book_reject(candidate["yes_best_bid_c"], candidate["no_best_bid_c"]):
        return reject_candidate(
            "crossed_book_bid_sum",
            market_ticker=candidate["market_ticker"],
            yes_best_bid_c=candidate["yes_best_bid_c"],
            no_best_bid_c=candidate["no_best_bid_c"],
            bid_sum_c=candidate["yes_best_bid_c"] + candidate["no_best_bid_c"],
        )

    if candidate["entry_ask_c"] is None or candidate["same_side_bid_c"] is None:
        return reject_candidate(
            "missing_entry_quote",
            market_ticker=candidate["market_ticker"],
            side=candidate["side"],
        )

    if candidate["spread_c"] is None:
        return reject_candidate(
            "missing_spread",
            market_ticker=candidate["market_ticker"],
            side=candidate["side"],
        )

    if candidate["spread_c"] < -0.0001:
        return reject_candidate(
            "negative_spread",
            market_ticker=candidate["market_ticker"],
            side=candidate["side"],
            entry_ask_c=candidate["entry_ask_c"],
            same_side_bid_c=candidate["same_side_bid_c"],
            spread_c=candidate["spread_c"],
        )

    if not passes_ask_filter(candidate["entry_ask_c"]):
        return reject_candidate(
            "ask_outside_40_59",
            market_ticker=candidate["market_ticker"],
            side=candidate["side"],
            entry_ask_c=candidate["entry_ask_c"],
            ask_min_c=ASK_MIN_C,
            ask_max_c=ASK_MAX_C,
        )

    if not passes_spread_filter(candidate["entry_ask_c"], candidate["same_side_bid_c"]):
        return reject_candidate(
            "spread_gt_5c",
            market_ticker=candidate["market_ticker"],
            side=candidate["side"],
            entry_ask_c=candidate["entry_ask_c"],
            same_side_bid_c=candidate["same_side_bid_c"],
            spread_c=candidate["spread_c"],
            spread_max_c=SPREAD_MAX_C,
        )

    if not passes_gap_filter(candidate["btc_ref_price"], candidate["floor_strike"]):
        return reject_candidate(
            "gap_lt_25_usd",
            market_ticker=candidate["market_ticker"],
            side=candidate["side"],
            btc_ref_price=candidate["btc_ref_price"],
            floor_strike=candidate["floor_strike"],
            gap_usd=candidate["gap_usd"],
            gap_min_usd=GAP_MIN_USD,
        )

    limit_buffer_c = float(__import__("os").getenv("LIVE_LIMIT_BUFFER_C", "0") or 0)
    limit_price_c = min(99.0, candidate["entry_ask_c"] + limit_buffer_c)

    out = dict(candidate)
    out.update({
        "decision": "TRADE",
        "reason": "selected_widest_implied_winner_gate_pass",
        "limit_price_c": limit_price_c,
        "candidate_key": candidate_key_for_trade(),
    })
    return out


def candidate_from_raw_backtest_row(row):
    now_utc = parse_time(row.get("time"))
    close_time_utc = parse_time(row.get("close_time"))
    market_ticker = row.get("market_ticker", "")
    btc_ref_price = fnum(row.get("coin_price"))
    floor_strike = fnum(row.get("floor_strike"))
    yes_best_bid_c = cents(row.get("yes_best_bid"))
    no_best_bid_c = cents(row.get("no_best_bid"))
    yes_best_ask_c = cents(row.get("yes_ask_est"))
    no_best_ask_c = cents(row.get("no_ask_est"))
    settlement_result = settlement_result_from_raw_row(row)
    return construct_direct_and_reverse_candidates(
        market_ticker=market_ticker,
        now_utc=now_utc,
        close_time_utc=close_time_utc,
        btc_ref_price=btc_ref_price,
        floor_strike=floor_strike,
        yes_best_bid_c=yes_best_bid_c,
        yes_best_ask_c=yes_best_ask_c,
        no_best_bid_c=no_best_bid_c,
        no_best_ask_c=no_best_ask_c,
        settlement_result=settlement_result,
    )


def dedupe_keep_first_qualifying_by_market(first_trades, trade_decision):
    """
    One entry per market. Keep earliest qualifying row.
    Because sec_to_close counts down, earliest qualifying row has largest
    sec_to_close inside the 10s-2m window.
    """
    if trade_decision.get("decision") != "TRADE":
        return
    market = trade_decision["market_ticker"]
    old = first_trades.get(market)
    if old is None:
        first_trades[market] = trade_decision
        return
    if trade_decision["sec_to_close"] > old["sec_to_close"]:
        first_trades[market] = trade_decision


def evaluate_row_for_selected_rule(row, first_trades):
    direct, reverse = candidate_from_raw_backtest_row(row)
    decision = evaluate_selected_direct_candidate(
        direct,
        already_traded_market=direct["market_ticker"] in first_trades if direct else False,
    )
    dedupe_keep_first_qualifying_by_market(first_trades, decision)
    return {
        "direct_candidate": direct,
        "reverse_candidate": reverse,
        "decision": decision,
    }


def summarize_first_trades(first_trades):
    trades = list(first_trades.values())
    by_date = {}
    by_market = {}
    wins = 0
    losses = 0
    net_c = 0.0
    worst_c = 999.0
    best_c = -999.0

    for trade in trades:
        pnl_c = trade.get("net_pnl_c_per_contract")
        if pnl_c is None:
            continue
        pnl_c = float(pnl_c)
        market = trade["market_ticker"]
        date = str(trade.get("time_utc", ""))[:10]
        if pnl_c > 0:
            wins += 1
        else:
            losses += 1
        net_c += pnl_c
        worst_c = min(worst_c, pnl_c)
        best_c = max(best_c, pnl_c)
        by_market[market] = by_market.get(market, 0.0) + pnl_c
        by_date[date] = by_date.get(date, 0.0) + pnl_c

    best_date_c = max(by_date.values()) if by_date else 0.0
    best_market_c = max(by_market.values()) if by_market else 0.0
    n = wins + losses

    return {
        "strategy_id": STRATEGY_ID,
        "candidate_key": candidate_key_for_trade(),
        "trades": n,
        "markets": len(by_market),
        "dates": len(by_date),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else 0.0,
        "net_c_per_contract_total": net_c,
        "avg_net_c_per_contract": net_c / n if n else 0.0,
        "worst_net_c_per_contract": worst_c if n else None,
        "best_net_c_per_contract": best_c if n else None,
        "net_after_best_date_c": net_c - best_date_c,
        "net_after_best_market_c": net_c - best_market_c,
        "net_c_10_contract_total": net_c * CONTRACTS,
        "avg_net_c_10_contracts": (net_c / n) * CONTRACTS if n else 0.0,
        "worst_net_c_10_contracts": worst_c * CONTRACTS if n else None,
    }
