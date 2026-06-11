#!/usr/bin/env python3
import csv, json, os, time
from datetime import datetime, timezone
from pathlib import Path
from regime_rules import REGIME_STRATEGIES

BASE = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest")
OUT = BASE / "paper_learning"
MARKET_STATE = BASE / "btc15_implied_winner_market_state.csv"
ORDERS = BASE / "btc15_implied_winner_orders.csv"

OUT.mkdir(parents=True, exist_ok=True)

SIGNALS = OUT / "candidate_signals.csv"
RESULTS = OUT / "settled_results.csv"
LIVE_COMPARE = OUT / "live_vs_paper.csv"
OVERLAP = OUT / "overlap_by_ticker.csv"
STATE = OUT / "state.json"

CONTRACTS = 1
FEE_C = 2.0
SLIP_C = 2.0
DERIVED_RULES = Path("/var/lib/kalshi-learning/reports/derived_candidate_rules.csv")
PROMOTED_RULES = Path("/var/lib/kalshi-learning/reports/promoted_rules.csv.DISABLED")


STRATEGIES = [
    ("LIVE_10S_2M_40_59_S5_G25", 10, 120, 40, 59, 5, 25, "late_implied"),
    ("ORIG_10S_5M_40_59_S5_G25", 10, 300, 40, 59, 5, 25, "late_implied"),
    ("VAR_10S_3M_40_59_S5_G25", 10, 180, 40, 59, 5, 25, "late_implied"),
    ("VAR_10_60S_40_59_S5_G25", 10, 60, 40, 59, 5, 25, "late_implied"),
    ("STRICT_1_2M_40_49_S5_G75", 60, 120, 40, 49, 5, 75, "late_implied"),
    ("STRICT_1_2M_40_49_S5_G100", 60, 120, 40, 49, 5, 100, "late_implied"),
    ("STRICT_10_60S_40_49_S5_G75", 10, 60, 40, 49, 5, 75, "late_implied"),
    ("STRICT_10_60S_40_49_S5_G100", 10, 60, 40, 49, 5, 100, "late_implied"),
    ("PAPER_10_15M_YES_40_49_S2", 600, 900, 40, 49, 2, 0, "early_yes"),
    ("PAPER_10_15M_NO_50_59_S3_5", 600, 900, 50, 59, 5, 0, "early_no"),
]

SIGNAL_FIELDS = [
    "ts_utc","strategy_id","ticker","side","sec_to_close","open_px","btc_px",
    "gap_usd","entry_ask_c","entry_bid_c","spread_c","yes_bid","yes_ask",
    "no_bid","no_ask","source","dedup_key"
]
RESULT_FIELDS = SIGNAL_FIELDS + [
    "settled_ts_utc","winner_side","result_source","gross_c","fee_c","slip_c","net_c","outcome"
]
COMPARE_FIELDS = [
    "ts_utc","ticker","live_side","live_price_c","paper_strategy_ids","matched_live_rule"
]
OVERLAP_FIELDS = [
    "ts_utc","ticker","strategy_count","strategies","sides","min_entry_ask_c","max_gap_usd"
]

def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def ensure_csv(path, fields):
    if not path.exists():
        with path.open("w", newline="") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()

def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            return {}
    return {}

def save_state(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, sort_keys=True))
    tmp.replace(STATE)

def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))

def append_row(path, fields, row):
    ensure_csv(path, fields)
    with path.open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=fields, extrasaction="ignore").writerow(row)

def implied_side(open_px, btc_px):
    if open_px is None or btc_px is None:
        return None
    return "YES" if btc_px >= open_px else "NO"

def side_prices(row, side):
    if side == "YES":
        bid, ask = num(row.get("yes_best_bid_c") or row.get("yes_bid")), num(row.get("yes_best_ask_c") or row.get("yes_ask"))
    else:
        bid, ask = num(row.get("no_best_bid_c") or row.get("no_bid")), num(row.get("no_best_ask_c") or row.get("no_ask"))
    if bid is None or ask is None:
        return None, None, None
    return bid, ask, ask - bid

def qualifies(row, strategy):
    sid, tmin, tmax, amin, amax, smax, gmin, mode = strategy
    ttc = num(row.get("sec_to_close"))
    open_px = num(row.get("floor_strike") or row.get("open_px"))
    btc_px = num(row.get("btc_ref_price") or row.get("btc_px"))
    if ttc is None or open_px is None or btc_px is None:
        return None
    if not (tmin <= ttc <= tmax):
        return None

    if mode == "early_yes":
        side = "YES"
    elif mode == "early_no":
        side = "NO"
    else:
        side = implied_side(open_px, btc_px)

    bid, ask, spread = side_prices(row, side)
    if ask is None or spread is None:
        return None
    gap = abs(btc_px - open_px)

    if not (amin <= ask <= amax):
        return None
    if spread < 0 or spread > smax:
        return None
    if gap < gmin:
        return None

    ticker = row.get("market_ticker") or row.get("ticker","")
    return {
        "ts_utc": row.get("ts_utc",""),
        "strategy_id": sid,
        "ticker": ticker,
        "side": side,
        "sec_to_close": ttc,
        "open_px": open_px,
        "btc_px": btc_px,
        "gap_usd": gap,
        "entry_ask_c": ask,
        "entry_bid_c": bid,
        "spread_c": spread,
        "yes_bid": row.get("yes_best_bid_c") or row.get("yes_bid",""),
        "yes_ask": row.get("yes_best_ask_c") or row.get("yes_ask",""),
        "no_bid": row.get("no_best_bid_c") or row.get("no_bid",""),
        "no_ask": row.get("no_best_ask_c") or row.get("no_ask",""),
        "source": row.get("floor_strike_source") or row.get("source",""),
        "dedup_key": sid + "|" + ticker,
    }

def settle_signal(sig, final_row):
    open_px = num(final_row.get("floor_strike") or sig.get("open_px"))
    final_px = num(final_row.get("btc_ref_price") or final_row.get("btc_px"))
    if open_px is None or final_px is None:
        return None
    winner = "YES" if final_px >= open_px else "NO"
    ask = num(sig.get("entry_ask_c"))
    side = sig.get("side")
    if ask is None:
        return None
    gross = (100.0 - ask) if side == winner else -ask
    net = gross - FEE_C - SLIP_C
    out = dict(sig)
    out.update({
        "settled_ts_utc": utcnow(),
        "winner_side": winner,
        "result_source": "btc_market_state_proxy_pending_kalshi_truth",
        "gross_c": gross,
        "fee_c": FEE_C,
        "slip_c": SLIP_C,
        "net_c": net,
        "outcome": "WIN" if net > 0 else "LOSS",
    })
    return out

def rebuild_overlap():
    rows = read_rows(SIGNALS)
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    ensure_csv(OVERLAP, OVERLAP_FIELDS)
    tmp = OVERLAP.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OVERLAP_FIELDS)
        w.writeheader()
        for ticker, rs in sorted(by_ticker.items()):
            strategies = sorted(set(r["strategy_id"] for r in rs))
            sides = sorted(set(r["side"] for r in rs))
            asks = [num(r.get("entry_ask_c")) for r in rs if num(r.get("entry_ask_c")) is not None]
            gaps = [num(r.get("gap_usd")) for r in rs if num(r.get("gap_usd")) is not None]
            w.writerow({
                "ts_utc": utcnow(),
                "ticker": ticker,
                "strategy_count": len(strategies),
                "strategies": ";".join(strategies),
                "sides": ";".join(sides),
                "min_entry_ask_c": min(asks) if asks else "",
                "max_gap_usd": max(gaps) if gaps else "",
            })
    tmp.replace(OVERLAP)

def rebuild_live_compare():
    signals = read_rows(SIGNALS)
    sigs = {}
    for r in signals:
        ticker = r.get("ticker","")
        if ticker:
            sigs.setdefault(ticker, []).append(r.get("strategy_id",""))

    ensure_csv(LIVE_COMPARE, COMPARE_FIELDS)
    tmp = LIVE_COMPARE.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COMPARE_FIELDS)
        w.writeheader()
        for o in read_rows(ORDERS):
            ticker = o.get("market_ticker") or o.get("ticker","")
            side = (o.get("side") or o.get("outcome_side") or "").upper()
            price = o.get("limit_price_c") or o.get("price_c") or o.get("yes_price_c") or o.get("entry_ask_c") or ""
            ids = sorted(set(sigs.get(ticker, [])))
            w.writerow({
                "ts_utc": o.get("ts_utc",""),
                "ticker": ticker,
                "live_side": side,
                "live_price_c": price,
                "paper_strategy_ids": ";".join(ids),
                "matched_live_rule": "1" if "LIVE_10S_2M_40_59_S5_G25" in ids else "0",
            })
    tmp.replace(LIVE_COMPARE)

SPECIAL_MID120_ID = "EARLY_NO_3_5M_50_59_SP1_MID120_0_5"

def parse_ts_utc(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:
        return None

def side_mid(row, side):
    bid, ask, spread = side_prices(row, side)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0

def build_prev_120(rows):
    by_ticker = {}
    for i, r in enumerate(rows):
        ticker = r.get("market_ticker") or r.get("ticker","")
        t = parse_ts_utc(r.get("ts_utc",""))
        if ticker and t:
            by_ticker.setdefault(ticker, []).append((t, i, r))

    prev = {}
    for ticker, items in by_ticker.items():
        items.sort(key=lambda x: x[0])
        j = 0
        for i, (t, idx, r) in enumerate(items):
            while j + 1 < i and (t - items[j+1][0]).total_seconds() >= 120:
                j += 1
            if i > 0 and (t - items[j][0]).total_seconds() >= 120:
                prev[idx] = items[j][2]
    return prev

def qualifies_regime(row, strategy):
    sid, tmin, tmax, amin, amax, gmin, gmax, smin, smax, side = strategy
    ttc = num(row.get("sec_to_close"))
    open_px = num(row.get("floor_strike") or row.get("open_px"))
    btc_px = num(row.get("btc_ref_price") or row.get("btc_px"))
    if ttc is None or open_px is None or btc_px is None:
        return None
    if not (tmin <= ttc <= tmax):
        return None
    if implied_side(open_px, btc_px) != side:
        return None
    bid, ask, spread = side_prices(row, side)
    if ask is None or bid is None or spread is None:
        return None
    gap = abs(btc_px - open_px)
    if not (amin <= ask <= amax):
        return None
    if not (gmin <= gap < gmax):
        return None
    if not (smin <= spread <= smax):
        return None
    ticker = row.get("market_ticker") or row.get("ticker","")
    return {
        "ts_utc": row.get("ts_utc",""),
        "strategy_id": sid,
        "ticker": ticker,
        "side": side,
        "sec_to_close": ttc,
        "open_px": open_px,
        "btc_px": btc_px,
        "gap_usd": gap,
        "entry_ask_c": ask,
        "entry_bid_c": bid,
        "spread_c": spread,
        "yes_bid": row.get("yes_best_bid_c") or row.get("yes_bid",""),
        "yes_ask": row.get("yes_best_ask_c") or row.get("yes_ask",""),
        "no_bid": row.get("no_best_bid_c") or row.get("no_bid",""),
        "no_ask": row.get("no_best_ask_c") or row.get("no_ask",""),
        "source": "btc15_regime_table_v1_paper",
        "dedup_key": sid + "|" + ticker,
    }

def qualifies_mid120_no(row, prev_row):
    if not prev_row:
        return None

    ttc = num(row.get("sec_to_close"))
    open_px = num(row.get("floor_strike") or row.get("open_px"))
    btc_px = num(row.get("btc_ref_price") or row.get("btc_px"))
    if ttc is None or open_px is None or btc_px is None:
        return None
    if not (180 <= ttc < 300):
        return None

    side = "NO"
    bid, ask, spread = side_prices(row, side)
    if ask is None or bid is None or spread is None:
        return None
    if not (50 <= ask <= 59):
        return None
    if spread < 0 or spread > 1:
        return None

    old_mid = side_mid(prev_row, side)
    new_mid = side_mid(row, side)
    if old_mid is None or new_mid is None:
        return None
    move_120 = new_mid - old_mid
    if not (0 <= move_120 <= 5):
        return None

    ticker = row.get("market_ticker") or row.get("ticker","")
    return {
        "ts_utc": row.get("ts_utc",""),
        "strategy_id": SPECIAL_MID120_ID,
        "ticker": ticker,
        "side": side,
        "sec_to_close": ttc,
        "open_px": open_px,
        "btc_px": btc_px,
        "gap_usd": abs(btc_px - open_px),
        "entry_ask_c": ask,
        "entry_bid_c": bid,
        "spread_c": spread,
        "yes_bid": row.get("yes_best_bid_c") or row.get("yes_bid",""),
        "yes_ask": row.get("yes_best_ask_c") or row.get("yes_ask",""),
        "no_bid": row.get("no_best_bid_c") or row.get("no_bid",""),
        "no_ask": row.get("no_best_ask_c") or row.get("no_ask",""),
        "source": "paper_side_mid_move_120_no_3_5m",
        "dedup_key": SPECIAL_MID120_ID + "|" + ticker,
    }

def bucket_spread_value(x):
    if x <= 1:
        return "spread_0_1"
    if x == 2:
        return "spread_2"
    return "spread_3_plus"

def bucket_ttc_value(x):
    if x < 120:
        return "ttc_under_2m"
    if x < 300:
        return "ttc_2m_5m"
    if x < 600:
        return "ttc_5m_10m"
    return "ttc_10m_15m"

def load_derived_rules():
    return []
    if not DERIVED_RULES.exists() or DERIVED_RULES.stat().st_size == 0:
        return []
    out = []
    with DERIVED_RULES.open(newline="") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "PAPER_TEST_ONLY":
                continue
            if "DERIVED" in (r.get("source_strategy") or ""):
                continue
            if "DERIVED_DERIVED" in (r.get("derived_rule") or ""):
                continue
            out.append(r)
    return out

def qualifies_derived(row, rule):
    if rule.get("status") != "PAPER_TEST_ONLY":
        return None

    side = rule.get("side")
    if side not in ("YES", "NO"):
        return None

    ttc = num(row.get("sec_to_close"))
    open_px = num(row.get("floor_strike") or row.get("open_px"))
    btc_px = num(row.get("btc_ref_price") or row.get("btc_px"))
    if ttc is None or open_px is None or btc_px is None:
        return None

    if bucket_ttc_value(ttc) != rule.get("ttc_bucket"):
        return None

    bid, ask, spread = side_prices(row, side)
    if ask is None or bid is None or spread is None:
        return None
    if ask <= 0 or bid < 0 or spread < 0:
        return None

    if bucket_spread_value(spread) != rule.get("spread_bucket"):
        return None

    ticker = row.get("market_ticker") or row.get("ticker","")
    sid = rule.get("derived_rule") or "DERIVED_UNKNOWN"

    return {
        "ts_utc": row.get("ts_utc",""),
        "strategy_id": sid,
        "ticker": ticker,
        "side": side,
        "sec_to_close": ttc,
        "open_px": open_px,
        "btc_px": btc_px,
        "gap_usd": abs(btc_px - open_px),
        "entry_ask_c": ask,
        "entry_bid_c": bid,
        "spread_c": spread,
        "yes_bid": row.get("yes_best_bid_c") or row.get("yes_bid",""),
        "yes_ask": row.get("yes_best_ask_c") or row.get("yes_ask",""),
        "no_bid": row.get("no_best_bid_c") or row.get("no_bid",""),
        "no_ask": row.get("no_best_ask_c") or row.get("no_ask",""),
        "source": "DISABLED_derived_candidate_rule_paper",
        "dedup_key": sid + "|" + ticker,
    }


def load_promoted_rules():
    if not PROMOTED_RULES.exists() or PROMOTED_RULES.stat().st_size == 0:
        return []
    with PROMOTED_RULES.open(newline="") as f:
        return list(csv.DictReader(f))

def edge_parts(edge_key):
    out = {}
    for part in str(edge_key or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out

def bucket_entry_value(x):
    if x < 40:
        return "entry_under_40"
    if x < 50:
        return "entry_40_49"
    if x < 60:
        return "entry_50_59"
    if x < 70:
        return "entry_60_69"
    return "entry_70_plus"

def bucket_gap_value(x):
    if x < 25:
        return "gap_0_25"
    if x < 50:
        return "gap_25_50"
    if x < 100:
        return "gap_50_100"
    return "gap_100_plus"

def qualifies_promoted(row, rule):
    edge = edge_parts(rule.get("edge_key", ""))
    side = edge.get("side") or rule.get("side")
    if side not in ("YES", "NO"):
        return None

    ttc = num(row.get("sec_to_close"))
    open_px = num(row.get("floor_strike") or row.get("open_px"))
    btc_px = num(row.get("btc_ref_price") or row.get("btc_px"))
    if ttc is None or open_px is None or btc_px is None:
        return None

    bid, ask, spread = side_prices(row, side)
    if ask is None or bid is None or spread is None:
        return None
    if ask <= 0 or bid < 0 or spread < 0:
        return None

    gap = abs(btc_px - open_px)

    if edge.get("spread_bucket") and bucket_spread_value(spread) != edge.get("spread_bucket"):
        return None
    if edge.get("ttc_bucket") and bucket_ttc_value(ttc) != edge.get("ttc_bucket"):
        return None
    if edge.get("entry_bucket") and bucket_entry_value(ask) != edge.get("entry_bucket"):
        return None
    if edge.get("gap_bucket") and bucket_gap_value(gap) != edge.get("gap_bucket"):
        return None

    ticker = row.get("market_ticker") or row.get("ticker", "")
    sid = rule.get("promoted_strategy_id") or "PROMOTED_UNKNOWN"

    return {
        "ts_utc": row.get("ts_utc", ""),
        "strategy_id": sid,
        "ticker": ticker,
        "side": side,
        "sec_to_close": ttc,
        "open_px": open_px,
        "btc_px": btc_px,
        "gap_usd": gap,
        "entry_ask_c": ask,
        "entry_bid_c": bid,
        "spread_c": spread,
        "yes_bid": row.get("yes_best_bid_c") or row.get("yes_bid", ""),
        "yes_ask": row.get("yes_best_ask_c") or row.get("yes_ask", ""),
        "no_bid": row.get("no_best_bid_c") or row.get("no_bid", ""),
        "no_ask": row.get("no_best_ask_c") or row.get("no_ask", ""),
        "source": "promoted_rule_paper",
        "dedup_key": sid + "|" + ticker,
    }

def qualifies_refined_regime(row):
    sid = "REFINED_REGIME_NO_ENTRY50_54_GAP0_25_BTC_ABOVE_OPEN_0_50_SPREAD_NOT_2_3"
    ticker = row.get("market_ticker") or row.get("ticker", "")
    if not ticker:
        return None

    def f(name, default=None):
        try:
            v = row.get(name)
            if v is None or v == "":
                return default
            return float(v)
        except Exception:
            return default

    sec = f("sec_to_close")
    open_px = f("open_px", f("floor_strike"))
    btc_px = f("btc_px", f("btc_ref_price"))
    gap = f("gap_usd")
    yes_bid = f("yes_best_bid_c", f("yes_bid"))
    yes_ask = f("yes_best_ask_c", f("yes_ask"))
    no_bid = f("no_best_bid_c", f("no_bid"))
    no_ask = f("no_best_ask_c", f("no_ask"))

    if None in (sec, open_px, btc_px, gap, no_bid, no_ask):
        return None

    side = "NO"
    entry_ask = no_ask
    entry_bid = no_bid
    spread = entry_ask - entry_bid
    btc_move = btc_px - open_px

    if not (600 <= sec <= 900):
        return None
    if not (50 <= entry_ask < 55):
        return None
    if not (abs(gap) < 25):
        return None
    if not (0 <= btc_move < 50):
        return None
    if spread > 1 and spread <= 3:
        return None

    return {
        "ts_utc": row.get("ts_utc", ""),
        "strategy_id": sid,
        "ticker": ticker,
        "side": side,
        "sec_to_close": sec,
        "open_px": open_px,
        "btc_px": btc_px,
        "gap_usd": gap,
        "entry_ask_c": entry_ask,
        "entry_bid_c": entry_bid,
        "spread_c": spread,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "source": "refined_regime_paper_zero_start",
        "dedup_key": sid + "|" + ticker,
    }


def run_once():
    ensure_csv(SIGNALS, SIGNAL_FIELDS)
    ensure_csv(RESULTS, RESULT_FIELDS)
    st = load_state()
    seen = set(st.get("seen", []))
    settled = set(st.get("settled", []))

    rows = read_rows(MARKET_STATE)
    if not rows:
        print("no market_state rows")
        return

    latest_by_ticker = {}
    for full_row in rows:
        full_ticker = full_row.get("market_ticker") or full_row.get("ticker","")
        if full_ticker:
            latest_by_ticker[full_ticker] = full_row

    new_count = 0
    result_count = 0

    recent_rows = rows[-5000:]
    prev120 = build_prev_120(recent_rows)

    for idx, row in enumerate(recent_rows):
        ticker = row.get("market_ticker") or row.get("ticker","")
        if ticker:
            latest_by_ticker[ticker] = row

        for strat in STRATEGIES:
            sig = qualifies(row, strat)
            if not sig:
                continue
            k = sig["dedup_key"]
            if k in seen:
                continue
            seen.add(k)
            append_row(SIGNALS, SIGNAL_FIELDS, sig)
            new_count += 1

        for strat in REGIME_STRATEGIES:
            sig = qualifies_regime(row, strat)
            if not sig:
                continue
            k = sig["dedup_key"]
            if k in seen:
                continue
            seen.add(k)
            append_row(SIGNALS, SIGNAL_FIELDS, sig)
            new_count += 1

        sig = qualifies_mid120_no(row, prev120.get(idx))
        if sig:
            k = sig["dedup_key"]
            if k not in seen:
                seen.add(k)
                append_row(SIGNALS, SIGNAL_FIELDS, sig)
                new_count += 1
        for rule in load_derived_rules():
            sig = qualifies_derived(row, rule)
            if not sig:
                continue
            k = sig["dedup_key"]
            if k in seen:
                continue
            seen.add(k)
            append_row(SIGNALS, SIGNAL_FIELDS, sig)
            new_count += 1

        for rule in load_promoted_rules():
            sig = qualifies_promoted(row, rule)
            if not sig:
                continue
            k = sig["dedup_key"]
            if k in seen:
                continue
            seen.add(k)
            append_row(SIGNALS, SIGNAL_FIELDS, sig)
            new_count += 1

        # promoted_rule_paper enabled from promoted_rules.csv.DISABLED; display remains Kalshi-truth gated
        for sig in list(csv.DictReader(SIGNALS.open(newline=""))):
            if not sig:
                continue
            k = sig.get("dedup_key")
            if not k or k in settled:
                continue
            ticker = sig.get("ticker")
            if not ticker:
                continue
            final_row = latest_by_ticker.get(ticker)
            if not final_row:
                continue
            ttc = num(final_row.get("sec_to_close"))
            if ttc is None or ttc > 0:
                continue
            res = settle_signal(sig, final_row)
            if res:
                append_row(RESULTS, RESULT_FIELDS, res)
                settled.add(k)
                result_count += 1

    st["seen"] = sorted(seen)
    st["settled"] = sorted(settled)
    st["last_run_utc"] = utcnow()
    save_state(st)
    rebuild_overlap()
    rebuild_live_compare()
    print("new_signals", new_count, "new_results", result_count, "seen", len(seen), "settled", len(settled))

def main():
    while True:
        try:
            run_once()
        except Exception as e:
            print("ERROR", repr(e), flush=True)
        time.sleep(10)

if __name__ == "__main__":
    main()
