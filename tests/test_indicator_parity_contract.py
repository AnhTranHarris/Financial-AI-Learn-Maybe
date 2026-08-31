from __future__ import annotations

import unittest
from pathlib import Path


class IndicatorParityContractTests(unittest.TestCase):
    def test_mql5_probe_is_tester_only_and_read_only(self) -> None:
        text = Path("mt5/DustyIndicatorParity.mq5").read_text(encoding="utf-8")
        self.assertIn("MQL_TESTER", text)
        self.assertIn("iMA(", text)
        self.assertIn("iATR(", text)
        self.assertIn("iRSI(", text)
        self.assertIn("CopyBuffer(", text)
        self.assertNotIn("CTrade", text)
        self.assertNotIn("OrderSend", text)


if __name__ == "__main__":
    unittest.main()
