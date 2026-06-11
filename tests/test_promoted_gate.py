import csv
import tempfile
import unittest
from pathlib import Path
from btc15iw.promotion_gate import evaluate_promoted_gate

ACTIVE_EDGE_KEYS = {
    "side=NO|spread_bucket=spread_0_1|entry_bucket=entry_50_59|gap_bucket=gap_25_50"
}

FIELDS = [
    "promoted_strategy_id",
    "edge_key",
    "status",
    "trades",
    "net_c",
    "source_truth",
    "truth_required",
]


def write_rules(path, rows):
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class PromotedGateTruthValidationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.tempdir.name) / "promoted_rules.csv"

    def tearDown(self):
        self.tempdir.cleanup()

    def valid_row(self):
        return {
            "promoted_strategy_id": "BTC_TRUTH_TEST",
            "edge_key": next(iter(ACTIVE_EDGE_KEYS)),
            "status": "ACTIVE",
            "trades": "27",
            "net_c": "1.0",
        }

    def test_header_only_rules_gate_closed(self):
        write_rules(self.rules_path, [])
        self.assertEqual(
            evaluate_promoted_gate(self.rules_path, ACTIVE_EDGE_KEYS),
            (False, "promoted_rules_no_rows"),
        )

    def test_rule_without_truth_metadata_gates_closed(self):
        write_rules(self.rules_path, [self.valid_row()])
        self.assertEqual(
            evaluate_promoted_gate(self.rules_path, ACTIVE_EDGE_KEYS),
            (False, "promoted_rules_not_truth_verified"),
        )

    def test_positive_truth_verified_rule_gates_open(self):
        row = self.valid_row()
        row.update({"source_truth": "kalshi", "truth_required": "1"})
        write_rules(self.rules_path, [row])
        self.assertEqual(
            evaluate_promoted_gate(self.rules_path, ACTIVE_EDGE_KEYS),
            (True, "promoted_rules_active"),
        )


if __name__ == "__main__":
    unittest.main()
