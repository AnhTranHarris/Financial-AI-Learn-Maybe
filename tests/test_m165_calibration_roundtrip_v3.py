from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from tools.execute_m165_calibration_roundtrip_v3 import (
    _entry_order_ticket,
    _parse_time,
    _reconcile_with_grace,
)


class M165CalibrationRoundTripV3Tests(unittest.TestCase):
    def test_extracts_positive_order_ticket_from_child_receipt(self) -> None:
        receipt = {"entry": {"execution": {"order_ticket": 36923813}}}
        self.assertEqual(_entry_order_ticket(receipt), 36923813)
        self.assertEqual(_entry_order_ticket({}), 0)
        self.assertEqual(_entry_order_ticket({"entry": {"execution": {"order_ticket": 0}}}), 0)

    def test_receipt_time_requires_timezone(self) -> None:
        parsed = _parse_time("2026-09-08T22:40:18.139652+00:00")
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)
        with self.assertRaises(ValueError):
            _parse_time("2026-09-08T22:40:18")

    def test_reconciliation_grace_observes_protective_close_without_retry(self) -> None:
        open_payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"position_id": 77}]},
                "history_deals_by_position_77": {"rows": [{"ticket": 101, "position_id": 77, "entry": 0}]},
                "open_position_77": {"rows": [{"ticket": 77}]},
            }
        }
        closed_payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"position_id": 77}]},
                "history_deals_by_position_77": {
                    "rows": [
                        {"ticket": 101, "position_id": 77, "entry": 0},
                        {"ticket": 102, "position_id": 77, "entry": 1},
                    ]
                },
                "open_position_77": {"rows": []},
            }
        }
        states = iter(
            [
                SimpleNamespace(payload=open_payload, fingerprint="a" * 64),
                SimpleNamespace(payload=open_payload, fingerprint="b" * 64),
                SimpleNamespace(payload=closed_payload, fingerprint="c" * 64),
            ]
        )
        sleeps: list[float] = []
        forensic, resolution, attempts = _reconcile_with_grace(
            lambda: next(states),
            attempts=5,
            interval_seconds=0.25,
            sleeper=sleeps.append,
        )
        self.assertEqual(attempts, 3)
        self.assertEqual(forensic.fingerprint, "c" * 64)
        self.assertEqual(resolution.status.value, "completed_protective_close")
        self.assertEqual(sleeps, [0.25, 0.25])
        self.assertFalse(resolution.retry_authority)

    def test_reconciliation_grace_stops_after_bound(self) -> None:
        payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"position_id": 77}]},
                "history_deals_by_position_77": {"rows": [{"ticket": 101, "position_id": 77, "entry": 0}]},
                "open_position_77": {"rows": [{"ticket": 77}]},
            }
        }
        calls = 0
        sleeps: list[float] = []

        def capture() -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(payload=payload, fingerprint=f"{calls:064x}")

        _, resolution, attempts = _reconcile_with_grace(
            capture,
            attempts=3,
            interval_seconds=0.1,
            sleeper=sleeps.append,
        )
        self.assertEqual(attempts, 3)
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, [0.1, 0.1])
        self.assertEqual(resolution.status.value, "position_open")
        self.assertFalse(resolution.retry_authority)

    def test_v3_has_no_direct_broker_send_surface(self) -> None:
        path = Path("tools/execute_m165_calibration_roundtrip_v3.py")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("execute_m165_calibration_roundtrip_v2.py", text)
        self.assertIn("capture_broker_forensics", text)
        self.assertIn("retry\": False", text)
        self.assertIn("RECONCILIATION_ATTEMPTS", text)


if __name__ == "__main__":
    unittest.main()
