import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

LATEST = Path("/var/lib/kalshi-reversion/btc_index_proxy/latest.json")
SAMPLES = Path("/var/lib/kalshi-reversion/btc_index_proxy/samples.csv")


def parse_utc(s):
    if s is None or str(s).strip() == "":
        return None
    x = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(x)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt):
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fnum(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(x)
    except Exception:
        return None


def latest_proxy(max_age_sec=10.0):
    if not LATEST.exists():
        return None
    try:
        d = json.loads(LATEST.read_text())
    except Exception:
        return None

    ts = parse_utc(d.get("ts_utc"))
    px = fnum(d.get("proxy_median"))
    fresh = bool(d.get("fresh"))
    venue_count = int(d.get("venue_count") or 0)

    if not ts or px is None or not fresh or venue_count < 3:
        return None

    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > max_age_sec:
        return None

    return {
        "price": px,
        "ts_utc": iso_z(ts),
        "age_sec": age,
        "venue_count": venue_count,
        "max_venue_spread": d.get("max_venue_spread"),
        "source": "btc_index_proxy_median",
    }


def floor_sample_at_or_after(open_dt, max_lag_sec=10.0):
    if not SAMPLES.exists() or not open_dt:
        return None

    best = None
    with SAMPLES.open(newline="") as f:
        for row in csv.DictReader(f):
            ts = parse_utc(row.get("ts_utc"))
            if not ts:
                continue
            lag = (ts - open_dt).total_seconds()
            if lag < 0 or lag > max_lag_sec:
                continue
            px = fnum(row.get("proxy_median"))
            fresh = str(row.get("fresh", "")).strip().lower() == "true"
            venue_count = int(float(row.get("venue_count") or 0))
            if px is None or not fresh or venue_count < 3:
                continue
            cand = {
                "price": px,
                "ts_utc": iso_z(ts),
                "lag_sec": lag,
                "venue_count": venue_count,
                "max_venue_spread": fnum(row.get("max_venue_spread")),
                "source": "btc_index_proxy_median_at_market_open",
            }
            if best is None or lag < best["lag_sec"]:
                best = cand
    return best
