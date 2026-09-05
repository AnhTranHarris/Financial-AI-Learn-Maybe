from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.mt5lab import MT5TickMode
from dusty.native_mt5_executor import NativeMT5JobPackage, render_native_set


class M161MetaTraderSetContractTests(unittest.TestCase):
    def test_string_inputs_are_plain_and_numeric_inputs_use_fixed_disabled_ranges(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        package = NativeMT5JobPackage(
            manifest_fingerprint="a" * 64,
            execution_fingerprint="b" * 64,
            experiment_id="DD-EXP-M161-SET",
            strategy_fingerprint="c" * 64,
            terminal_path=r"C:\MT5Research\terminal64.exe",
            terminal_data_root=r"C:\MT5ResearchData",
            terminal_binary_sha256="d" * 64,
            expert_relative_path="DustyResearchEA.ex5",
            expert_binary_sha256="e" * 64,
            symbol="EURUSD",
            timeframe="M15",
            window_label="holdout",
            start=start,
            end=start + timedelta(days=1),
            tick_mode=MT5TickMode.REAL_TICKS,
            deposit=10_000.0,
            currency="USD",
            leverage=100,
            execution_mode=0,
            timeout_seconds=60,
            magic=667123456,
            deviation_points=20,
            common_relative_dir="DustyDragon/M161/test-binding",
            set_file_name="dusty_m161_test.set",
            report_relative_path="DustyReports/m161_test.htm",
        )

        lines = render_native_set(package).splitlines()

        self.assertIn(
            r"InpManifestFile=DustyDragon\M161\test-binding\manifest.csv",
            lines,
        )
        self.assertIn(
            r"InpDealsFile=DustyDragon\M161\test-binding\deals.csv",
            lines,
        )
        self.assertIn(f"InpStrategyHash={package.strategy_fingerprint}", lines)
        self.assertIn(
            "InpMagic=667123456||667123456||1||667123456||N",
            lines,
        )
        self.assertIn("InpDeviationPoints=20||20||1||20||N", lines)
        self.assertFalse(any(line.endswith("||Y") for line in lines))


if __name__ == "__main__":
    unittest.main()
