from .config import load_config
from .kalshi_auth import load_private_key, http_get_json

def _cents_from_dollars(x):
    try:
        v = float(x) * 100.0
    except Exception:
        return None
    if v < 0 or v > 100:
        return None
    return round(v, 4)

def _best_bid(levels):
    vals = []
    for row in levels or []:
        if not row:
            continue
        c = _cents_from_dollars(row[0])
        if c is not None:
            vals.append(c)
    return max(vals) if vals else None

def _market_quotes(m):
    return {
        "yes_best_bid_c": _cents_from_dollars(m.get("yes_bid_dollars")),
        "yes_best_ask_c": _cents_from_dollars(m.get("yes_ask_dollars")),
        "no_best_bid_c": _cents_from_dollars(m.get("no_bid_dollars")),
        "no_best_ask_c": _cents_from_dollars(m.get("no_ask_dollars")),
    }

def fallback_best_quote(ticker, max_age_sec=90):
    if not ticker:
        return {}
    try:
        cfg = load_config("/opt/kalshi-research/btc15_implied_winner_widest/config.env")
        pk = load_private_key(cfg.kalshi_private_key_path)

        status, body = http_get_json(cfg.kalshi_api_base, cfg.kalshi_api_key_id, pk, f"/markets/{ticker}/orderbook")
        if int(status) != 200 or not isinstance(body, dict):
            return {}

        ob = body.get("orderbook_fp") or body.get("orderbook") or {}
        out = {
            "yes_best_bid_c": _best_bid(ob.get("yes_dollars") or ob.get("yes")),
            "no_best_bid_c": _best_bid(ob.get("no_dollars") or ob.get("no")),
        }

        status2, body2 = http_get_json(cfg.kalshi_api_base, cfg.kalshi_api_key_id, pk, f"/markets/{ticker}")
        if int(status2) == 200 and isinstance(body2, dict) and isinstance(body2.get("market"), dict):
            mq = _market_quotes(body2["market"])
            for k, v in mq.items():
                if out.get(k) is None and v is not None:
                    out[k] = v

        if out.get("yes_best_ask_c") is None and out.get("no_best_bid_c") is not None:
            out["yes_best_ask_c"] = round(100.0 - out["no_best_bid_c"], 4)
        if out.get("no_best_ask_c") is None and out.get("yes_best_bid_c") is not None:
            out["no_best_ask_c"] = round(100.0 - out["yes_best_bid_c"], 4)

        if out.get("yes_best_bid_c") is None or out.get("no_best_bid_c") is None:
            return {}

        out["yes_bid_plus_no_bid_c"] = round(out["yes_best_bid_c"] + out["no_best_bid_c"], 4)
        out["quote_source"] = "kalshi_rest_merged"
        return out
    except Exception:
        return {}
