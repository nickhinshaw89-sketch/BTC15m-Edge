import csv, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "/opt/kalshi-research/btc15_implied_winner_widest")

from btc15iw.config import load_config
from btc15iw.kalshi_auth import load_private_key, http_get_json

BASE = Path("/var/lib/kalshi-reversion/btc15_implied_winner_widest/paper_learning")
INP = BASE / "settled_results.csv"
OUT = BASE / "settled_results_with_kalshi.csv"
DAILY = BASE / "daily_strategy_summary.csv"

def norm(v):
    s = str(v or "").strip().upper()
    if s in ("YES", "Y", "TRUE", "1"):
        return "YES"
    if s in ("NO", "N", "FALSE", "0"):
        return "NO"
    return ""

def fnum(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def kalshi_verdict(m):
    for k in ("result", "settlement_value", "winning_result", "winning_outcome", "winner", "outcome", "settlement_result"):
        v = norm(m.get(k))
        if v:
            return v
    return ""

def recompute_truth(row, kalshi_winner):
    side = norm(row.get("side"))
    entry = fnum(row.get("entry_ask_c"))
    fee = fnum(row.get("fee_c"))
    slip = fnum(row.get("slip_c"))

    if not side or not kalshi_winner:
        return "", "", "", ""

    win = side == kalshi_winner
    gross = (100.0 - entry) if win else -entry
    net = gross - fee - slip
    outcome = "WIN" if win else "LOSS"
    return outcome, round(gross, 4), round(net, 4), "kalshi_verdict"

def main():
    if not INP.exists():
        return

    cfg = load_config("/opt/kalshi-research/btc15_implied_winner_widest/config.env")
    pk = load_private_key(cfg.kalshi_private_key_path)

    rows = list(csv.DictReader(INP.open(newline="")))
    if not rows:
        return

    extra = [
        "kalshi_verdict",
        "kalshi_status",
        "kalshi_api_status",
        "verdict_match",
        "truth_winner_side",
        "truth_outcome",
        "truth_gross_c",
        "truth_net_c",
        "truth_used",
    ]
    fields = list(rows[0].keys())
    for x in extra:
        if x not in fields:
            fields.append(x)

    out_rows = []
    cache = {}

    for r in rows:
        ticker = r.get("ticker", "")

        if ticker not in cache:
            status, body = http_get_json(cfg.kalshi_api_base, cfg.kalshi_api_key_id, pk, f"/markets/{ticker}")
            m = body.get("market", {}) if isinstance(body, dict) else {}
            cache[ticker] = (status, m)

        status, m = cache[ticker]
        kv = kalshi_verdict(m)
        pv = norm(r.get("winner_side"))

        truth_outcome, truth_gross, truth_net, truth_used = recompute_truth(r, kv)

        r["kalshi_verdict"] = kv
        r["kalshi_status"] = m.get("status", "")
        r["kalshi_api_status"] = status
        r["verdict_match"] = "1" if kv and pv and kv == pv else "0"
        r["truth_winner_side"] = kv
        r["truth_outcome"] = truth_outcome
        r["truth_gross_c"] = truth_gross
        r["truth_net_c"] = truth_net
        r["truth_used"] = truth_used

        out_rows.append(r)

    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)

    d = defaultdict(lambda: [0, 0, 0, 0.0, 0, 0])
    for r in out_rows:
        day = (r.get("settled_ts_utc") or r.get("ts_utc") or "")[:10]
        sid = r.get("strategy_id", "")
        key = (day, sid)
        outcome = (r.get("truth_outcome") or "").upper()
        truth_net = r.get("truth_net_c")
        if outcome not in {"WIN", "LOSS"} or truth_net in (None, ""):
            continue
        net = fnum(truth_net)

        d[key][0] += 1
        d[key][1] += 1 if outcome == "WIN" else 0
        d[key][2] += 1 if outcome == "LOSS" else 0
        d[key][3] += net
        d[key][4] += 1 if r.get("kalshi_verdict") else 0
        d[key][5] += 1 if r.get("verdict_match") == "1" else 0

    with DAILY.open("w", newline="") as f:
        fields2 = [
            "day","strategy_id","trades","wins","losses","win_rate",
            "truth_net_c","avg_truth_net_c",
            "kalshi_checked","kalshi_match","kalshi_match_rate",
            "source_truth","truth_required"
        ]
        w = csv.DictWriter(f, fieldnames=fields2)
        w.writeheader()
        for (day, sid), (n, wins, losses, net, checked, matched) in sorted(d.items()):
            w.writerow({
                "day": day,
                "strategy_id": sid,
                "trades": n,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / n, 4) if n else "",
                "truth_net_c": round(net, 2),
                "avg_truth_net_c": round(net / n, 2) if n else "",
                "kalshi_checked": checked,
                "kalshi_match": matched,
                "kalshi_match_rate": round(matched / checked, 4) if checked else "",
                "source_truth": "kalshi",
                "truth_required": 1,
            })

if __name__ == "__main__":
    main()
