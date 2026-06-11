import base64
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


ORDER_RETRY_ATTEMPTS = 6
ORDER_RETRY_SLEEP_SEC = 0.35


def load_private_key(path):
    raw = Path(path).read_bytes()
    candidates = [raw]

    if b"\\n" in raw and b"\n" not in raw[:80]:
        candidates.append(raw.replace(b"\\n", b"\n"))

    loaders = (
        serialization.load_pem_private_key,
        serialization.load_ssh_private_key,
        serialization.load_der_private_key,
    )

    last_err = None
    for data in candidates:
        for loader in loaders:
            try:
                return loader(data, password=None)
            except Exception as e:
                last_err = e

    raise ValueError("could_not_load_private_key: %s" % last_err)


def _signature(private_key, message):
    sig = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def signed_headers(method, path, key_id, private_key):
    ts_ms = str(int(time.time() * 1000))
    msg = ts_ms + method.upper() + "/trade-api/v2" + path.split("?", 1)[0]
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": _signature(private_key, msg),
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
    }


def websocket_headers(key_id, private_key, path="/trade-api/ws/v2"):
    h = signed_headers("GET", path, key_id, private_key)
    return [f"{k}: {v}" for k, v in h.items()]


def _walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k).lower(), v
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _filled_or_partial(body_text):
    obj = _json_loads(body_text)
    if obj is None:
        return False

    for k, v in _walk(obj):
        if k in {"filled_count", "filled_quantity", "fills_count"}:
            try:
                if int(float(v)) > 0:
                    return True
            except Exception:
                pass
        if k in {"status", "order_status"}:
            s = str(v).lower()
            if "filled" in s or "executed" in s:
                return True

    return False


def _explicit_no_fill(body_text):
    obj = _json_loads(body_text)
    if obj is None:
        return False

    saw_zero_fill = False
    saw_unfilled_status = False

    for k, v in _walk(obj):
        if k in {"filled_count", "filled_quantity", "fills_count"}:
            try:
                if int(float(v)) == 0:
                    saw_zero_fill = True
            except Exception:
                pass
        if k in {"status", "order_status"}:
            s = str(v).lower()
            if any(x in s for x in ("cancel", "unfill", "resting", "open", "accepted")):
                saw_unfilled_status = True

    return saw_zero_fill or saw_unfilled_status


def _fresh_payload(payload, attempt):
    p = dict(payload)
    old = str(p.get("client_order_id") or "btciw")
    suffix = "-r%d-%s" % (attempt, uuid.uuid4().hex[:8])
    base = old[: max(8, 64 - len(suffix))]
    p["client_order_id"] = base + suffix
    return p


def _post_once(base_url, path, key_id, private_key, payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = signed_headers("POST", path, key_id, private_key)
    headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def http_post_json(base_url, path, key_id, private_key, payload):
    is_order_submit = path.rstrip("/").endswith("/portfolio/orders") or "/portfolio/orders" in path
    attempts = ORDER_RETRY_ATTEMPTS if is_order_submit else 1

    last_status = None
    last_text = ""

    for attempt in range(1, attempts + 1):
        use_payload = payload if attempt == 1 else _fresh_payload(payload, attempt)
        status, text = _post_once(base_url, path, key_id, private_key, use_payload)

        last_status = status
        last_text = text

        if not is_order_submit:
            return status, text

        if _filled_or_partial(text):
            return status, text

        retryable_http = status in (408, 409, 425, 429, 500, 502, 503, 504)

        if attempt < attempts and retryable_http:
            time.sleep(ORDER_RETRY_SLEEP_SEC)
            continue

        return status, text

    return last_status, last_text

def http_get_json(base_url, key_id, private_key, path):
    headers = signed_headers("GET", path, key_id, private_key)
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(txt)
            except Exception:
                return r.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt
