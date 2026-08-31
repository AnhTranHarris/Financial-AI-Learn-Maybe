from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from dusty.markets import AssetClass, InstrumentType, MarketIdentity
from dusty.mt5worker import ReadOnlyMT5Worker


class FakeMT5:
    def __init__(self) -> None:
        self.shutdown_count = 0

    def initialize(self, path: str) -> bool:
        return bool(path)

    def shutdown(self) -> None:
        self.shutdown_count += 1

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, symbol: str):
        if symbol != "EURUSD.a":
            return None
        return SimpleNamespace(
            trade_contract_size=100_000.0,
            trade_tick_size=0.00001,
            trade_tick_value=1.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=50.0,
            bid=1.1000,
            margin_initial=0.0,
            currency_base="EUR",
            currency_profit="USD",
            swap_long=-2.0,
            swap_short=1.0,
            trade_stops_level=10,
            trade_freeze_level=2,
        )

    def account_info(self):
        return SimpleNamespace(currency="USD", leverage=100, server="Broker-Demo")


class MT5SymbolSnapshotTests(unittest.TestCase):
    def test_snapshot_preserves_raw_symbol_and_reads_broker_units(self) -> None:
        module = FakeMT5()
        market = MarketIdentity.of(
            raw_symbol="EURUSD.a",
            economic_underlier="EURUSD",
            asset_class=AssetClass.FX,
            instrument_type=InstrumentType.SPOT,
        )
        worker = ReadOnlyMT5Worker(module)
        snapshot = worker.symbol_snapshot(
            r"C:\\MT5\\terminal64.exe",
            market,
            captured_at=datetime(2026, 8, 31, 9, 30, tzinfo=UTC),
        )
        self.assertFalse(worker.broker_write_authorized)
        self.assertEqual(snapshot.broker, "Broker-Demo")
        self.assertEqual(snapshot.account_currency, "USD")
        self.assertEqual(snapshot.market.raw_symbol, "EURUSD.a")
        self.assertEqual(snapshot.market.base_currency, "EUR")
        self.assertEqual(snapshot.market.quote_currency, "USD")
        self.assertEqual(snapshot.market.economics.volume_min, 0.01)
        self.assertAlmostEqual(snapshot.market.economics.margin_rate, 0.01)
        self.assertEqual(module.shutdown_count, 1)


if __name__ == "__main__":
    unittest.main()
