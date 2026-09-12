from __future__ import annotations

from hashlib import sha256
import unittest

from tools.discover_m166_provisional_inputs import discover_in_payload


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M166ProvisionalInputDiscoveryTests(unittest.TestCase):
    def test_requires_explicit_strategy_dataset_and_parameter_identities(self) -> None:
        strategy = fp("strategy")
        payload = {
            "strategy_hash": strategy,
            "dataset_fingerprint": fp("dataset"),
            "variant_fingerprint": fp("variant-not-parameter"),
        }
        self.assertEqual(
            discover_in_payload(payload, expected_strategy=strategy, lane_id="eurusd:m15:test"),
            (),
        )

    def test_accepts_exact_explicit_identity_triple(self) -> None:
        strategy = fp("strategy")
        dataset = fp("dataset")
        parameter = fp("parameter")
        payload = {
            "outer": {
                "lane_id": "EURUSD:M15:TEST",
                "strategy_execution_fingerprint": strategy,
                "dataset_fingerprint": dataset,
                "parameter_fingerprint": parameter,
            }
        }
        rows = discover_in_payload(payload, expected_strategy=strategy, lane_id="eurusd:m15:test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["strategy_fingerprint"], strategy)
        self.assertEqual(rows[0]["dataset_fingerprint"], dataset)
        self.assertEqual(rows[0]["parameter_fingerprint"], parameter)
        self.assertEqual(rows[0]["object_path"], "$.outer")

    def test_rejects_strategy_or_lane_drift(self) -> None:
        strategy = fp("strategy")
        row = {
            "lane_id": "eurusd:m15:other",
            "strategy_fingerprint": strategy,
            "dataset_fingerprint": fp("dataset"),
            "parameter_fingerprint": fp("parameter"),
        }
        self.assertEqual(discover_in_payload(row, expected_strategy=strategy, lane_id="eurusd:m15:test"), ())
        row["lane_id"] = "eurusd:m15:test"
        self.assertEqual(discover_in_payload(row, expected_strategy=fp("different"), lane_id="eurusd:m15:test"), ())

    def test_rejects_non_sha_identity_fields(self) -> None:
        strategy = fp("strategy")
        payload = {
            "strategy_fingerprint": strategy,
            "dataset_fingerprint": "dataset-v1",
            "parameter_fingerprint": fp("parameter"),
        }
        self.assertEqual(discover_in_payload(payload, expected_strategy=strategy, lane_id="eurusd:m15:test"), ())


if __name__ == "__main__":
    unittest.main()
