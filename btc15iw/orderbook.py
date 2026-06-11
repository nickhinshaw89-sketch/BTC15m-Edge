import time
from datetime import datetime, timezone


def fnum(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(x)
    except Exception:
        return None


def cents(x):
    v = fnum(x)
    if v is None:
        return None

    # Kalshi REST orderbook_fp *_dollars levels are dollar prices.
    # Valid contract prices are 0.0000 through 1.0000 dollars.
    # Anything above 1.0 in a *_dollars source is not a usable price level.
    if 0.0 <= v <= 1.0:
        return v * 100.0

    # Some websocket delta shapes may send whole cents.
    # Accept those only if they are inside the valid cents range.
    if 1.0 < v <= 100.0 and abs(v - round(v)) < 1e-9:
        return v

    return None


def now_iso_z():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def best_price(levels):
    vals = []
    if isinstance(levels, dict):
        iterable = levels.items()
    else:
        iterable = levels or []

    for item in iterable:
        px = None
        qty = None

        if isinstance(item, tuple) and len(item) == 2 and not isinstance(item[1], (list, tuple, dict)):
            px, qty = item[0], item[1]
        elif isinstance(item, list) and len(item) >= 2:
            px, qty = item[0], item[1]
        elif isinstance(item, tuple) and len(item) >= 2:
            px, qty = item[0], item[1]
        elif isinstance(item, dict):
            px = item.get("price") or item.get("price_dollars") or item.get("yes_price") or item.get("no_price")
            qty = item.get("quantity") or item.get("count") or item.get("contracts") or item.get("size")
        else:
            continue

        p = cents(px)
        q = fnum(qty)
        if p is not None and (q is None or q > 0):
            vals.append(p)

    return max(vals) if vals else None


class Orderbook:
    def __init__(self, ticker):
        self.ticker = ticker
        self.yes = {}
        self.no = {}
        self.seq = None
        self.updated_monotonic = None
        self.exchange_ts_ms = None
        self.last_msg_type = ""

    def age_sec(self):
        if self.updated_monotonic is None:
            return None
        return time.monotonic() - self.updated_monotonic

    def ws_lag_ms(self):
        if self.exchange_ts_ms is None:
            return None
        try:
            return int(time.time() * 1000) - int(self.exchange_ts_ms)
        except Exception:
            return None

    def _replace_side(self, side, levels):
        book = {}
        if isinstance(levels, dict):
            items = levels.items()
        else:
            items = levels or []

        for item in items:
            if isinstance(item, tuple) and len(item) == 2 and not isinstance(item[1], (list, tuple, dict)):
                px, qty = item[0], item[1]
            elif isinstance(item, list) and len(item) >= 2:
                px, qty = item[0], item[1]
            elif isinstance(item, tuple) and len(item) >= 2:
                px, qty = item[0], item[1]
            elif isinstance(item, dict):
                px = item.get("price") or item.get("price_dollars") or item.get("yes_price") or item.get("no_price")
                qty = item.get("quantity") or item.get("count") or item.get("contracts") or item.get("size")
            else:
                continue

            p = cents(px)
            q = fnum(qty)
            if p is not None and q is not None and q > 0:
                book[round(p, 6)] = q

        if side == "yes":
            self.yes = book
        elif side == "no":
            self.no = book

    def _apply_level_delta(self, side, price, delta=None, quantity=None):
        p = cents(price)
        if p is None:
            return

        book = self.yes if side == "yes" else self.no if side == "no" else None
        if book is None:
            return

        if quantity is not None:
            q = fnum(quantity)
            if q is None or q <= 0:
                book.pop(round(p, 6), None)
            else:
                book[round(p, 6)] = q
            return

        d = fnum(delta)
        if d is None:
            return

        new_q = book.get(round(p, 6), 0.0) + d
        if new_q <= 0:
            book.pop(round(p, 6), None)
        else:
            book[round(p, 6)] = new_q

    def apply_snapshot_msg(self, msg, seq=None):
        fp = msg.get("orderbook_fp") if isinstance(msg, dict) else None
        src = fp if isinstance(fp, dict) else msg

        yes_levels = (
            src.get("yes_dollars")
            or src.get("yes")
            or src.get("yes_orders")
            or src.get("yes_bids")
            or []
        )
        no_levels = (
            src.get("no_dollars")
            or src.get("no")
            or src.get("no_orders")
            or src.get("no_bids")
            or []
        )

        self._replace_side("yes", yes_levels)
        self._replace_side("no", no_levels)

        self.seq = seq if seq is not None else msg.get("seq")
        self.exchange_ts_ms = msg.get("ts_ms") or msg.get("exchange_ts_ms") or msg.get("time_ms")
        self.updated_monotonic = time.monotonic()
        self.last_msg_type = "snapshot"

    def apply_delta_msg(self, msg, seq=None):
        side = (msg.get("side") or msg.get("market_side") or "").lower()
        if side in ("yes_bid", "yes_dollars"):
            side = "yes"
        if side in ("no_bid", "no_dollars"):
            side = "no"

        price = msg.get("price") or msg.get("price_dollars") or msg.get("yes_price") or msg.get("no_price")
        delta = msg.get("delta") or msg.get("quantity_delta") or msg.get("count_delta")
        quantity = msg.get("quantity") or msg.get("count") or msg.get("contracts")

        if side in ("yes", "no") and price is not None:
            self._apply_level_delta(side, price, delta=delta, quantity=quantity)

        self.seq = seq if seq is not None else msg.get("seq")
        self.exchange_ts_ms = msg.get("ts_ms") or msg.get("exchange_ts_ms") or msg.get("time_ms")
        self.updated_monotonic = time.monotonic()
        self.last_msg_type = "delta"

    def best(self):
        yb = best_price(self.yes)
        nb = best_price(self.no)

        ya = (100.0 - nb) if nb is not None else None
        na = (100.0 - yb) if yb is not None else None

        return {
            "yes_best_bid_c": yb,
            "yes_best_ask_c": ya,
            "no_best_bid_c": nb,
            "no_best_ask_c": na,
            "yes_bid_plus_no_bid_c": (yb + nb) if yb is not None and nb is not None else None,
            "ws_seq": self.seq,
            "ws_lag_ms": self.ws_lag_ms(),
            "orderbook_age_sec": self.age_sec(),
            "last_msg_type": self.last_msg_type,
        }
