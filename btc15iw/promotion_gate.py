import csv


def truth_verified(row):
    source_truth = (row.get("source_truth") or "").strip().lower()
    truth_required = (row.get("truth_required") or "").strip().lower()
    return source_truth == "kalshi" or truth_required in {"1", "true", "yes"}


def evaluate_promoted_gate(rules_path, active_edge_keys):
    if not rules_path.exists() or rules_path.stat().st_size == 0:
        return False, "promoted_rules_missing_or_empty"
    try:
        with rules_path.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return False, "promoted_rules_unreadable"
    if not rows:
        return False, "promoted_rules_no_rows"

    truth_verified_rows = [row for row in rows if truth_verified(row)]
    if not truth_verified_rows:
        return False, "promoted_rules_not_truth_verified"

    for row in truth_verified_rows:
        status = (row.get("status") or "ACTIVE").strip().upper()
        rule_id = (row.get("promoted_strategy_id") or row.get("rule_id") or "").strip()
        edge_key = (row.get("edge_key") or "").strip()
        try:
            net = float(row.get("net_c") or 0)
            trades = int(float(row.get("trades") or 0))
        except (TypeError, ValueError):
            continue
        if (
            rule_id
            and edge_key in active_edge_keys
            and status not in {"DISABLED", "REJECTED", "CUT"}
            and trades >= 27
            and net > 0
        ):
            return True, "promoted_rules_active"
    return False, "promoted_rules_no_active_positive_rules"
