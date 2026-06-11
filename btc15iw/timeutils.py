from datetime import datetime, timezone

def utc_now():
    return datetime.now(timezone.utc)

def iso_z(dt):
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_utc(value):
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def ms_to_iso_z(ms):
    if ms is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""
