import uuid
import json
from .kalshi_auth import http_post_json

def dollars_from_cents(c):
    return f"{float(c) / 100.0:.4f}"

class KalshiOrderClient:
    """
    Uses the legacy /portfolio/orders endpoint because it directly supports
    action=buy and side=yes/no. This is used only when PLACE_ORDERS=1.
    """
    def __init__(self, api_base, api_key_id, private_key, time_in_force="immediate_or_cancel", post_only=False):
        self.api_base = api_base
        self.api_key_id = api_key_id
        self.private_key = private_key
        self.time_in_force = time_in_force
        self.post_only = post_only

    def place_buy(self, ticker, side, contracts, limit_price_c, strategy_id):
        side_l = side.lower()
        client_order_id = f"btciw-{ticker[-18:]}-{side_l}-{uuid.uuid4().hex[:8]}"[:64]
        payload = {
            "ticker": ticker,
            "action": "buy",
            "side": side_l,
            "count": int(contracts),
            "type": "limit",
            "client_order_id": client_order_id,
            "time_in_force": self.time_in_force,
        }
        if side_l == "yes":
            payload["yes_price"] = int(round(float(limit_price_c)))
        elif side_l == "no":
            payload["no_price"] = int(round(float(limit_price_c)))
        else:
            raise ValueError(f"bad side: {side}")

        status, body = http_post_json(
            self.api_base,
            "/portfolio/orders",
            self.api_key_id,
            self.private_key,
            payload,
        )
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                pass

        order_id = None
        if isinstance(body, dict):
            order = body.get("order") or body
            order_id = order.get("order_id") or order.get("id") or order.get("client_order_id")
        return {
            "http_status": status,
            "body": body,
            "order_id": order_id,
            "client_order_id": client_order_id,
            "payload": payload,
        }
