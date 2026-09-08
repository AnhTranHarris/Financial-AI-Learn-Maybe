from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from dusty.broker_calibration import BrokerExecutionObservation, TradeSide
from dusty.m165_observation_custody import M165ObservationCustody


BROKER = "a" * 64


def _obs(side: TradeSide, second: int, *, broker: str = BROKER, symbol: str = "EURUSD") -> BrokerExecutionObservation:
    return BrokerExecutionObservation(
        broker_profile_fingerprint=broker,
        symbol=symbol,
        side=side,
        observed_at=datetime(2026, 9, 9, 1, 40, second, tzinfo=timezone.utc),
        point_size=0.00001,
        bid=1.16260,
        ask=1.16262,
        requested_price=1.16261,
        fill_price=1.16261,
        volume_lots=0.01,
        commission=-0.01,
        evidence_fingerprint=("b" if side is TradeSide.BUY else "c") * 64,
    )


class M165ObservationCustodyTests(unittest.TestCase):
    def test_import_is_idempotent_and_recomputes_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "observations.sqlite3"
            with M165ObservationCustody(path) as store:
                first = store.import_rows((_obs(TradeSide.BUY, 19), _obs(TradeSide.SELL, 21)))
                self.assertEqual((first.inserted, first.duplicates, first.total), (2, 0, 2))
                second = store.import_rows((_obs(TradeSide.BUY, 19), _obs(TradeSide.SELL, 21)))
                self.assertEqual((second.inserted, second.duplicates, second.total), (0, 2, 2))
                payload = store.summary_payload()
                self.assertEqual(payload["observation_count"], 2)
                self.assertEqual(payload["distinct_days"], 1)
                self.assertEqual(payload["sides"], ["buy", "sell"])
                self.assertEqual(payload["calibration"]["status"], "insufficient")

    def test_mixed_broker_or_symbol_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "observations.sqlite3"
            with M165ObservationCustody(path) as store:
                store.import_rows((_obs(TradeSide.BUY, 19),))
                with self.assertRaises(ValueError):
                    store.import_rows((_obs(TradeSide.SELL, 21, broker="d" * 64),))
                with self.assertRaises(ValueError):
                    store.import_rows((_obs(TradeSide.SELL, 21, symbol="XAUUSD"),))


if __name__ == "__main__":
    unittest.main()
