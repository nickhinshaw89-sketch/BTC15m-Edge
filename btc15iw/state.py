import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

class StateStore:
    def __init__(self, path):
        self.path = path
        self.data = {
            "traded_markets": [],
            "positions": {},
            "last_market_ticker": None,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            self.data.update(loaded)

    def save(self):
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def already_traded(self, market_ticker):
        return market_ticker in set(self.data.get("traded_markets", []))

    def mark_traded(self, market_ticker, row: Dict[str, Any]):
        traded = list(self.data.get("traded_markets", []))
        if market_ticker not in traded:
            traded.append(market_ticker)
        self.data["traded_markets"] = traded
        self.data.setdefault("positions", {})[market_ticker] = row
        self.save()
