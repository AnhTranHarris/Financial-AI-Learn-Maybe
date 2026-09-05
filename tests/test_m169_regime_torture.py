from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.regime_torture import (
    RegimeDefinition,
    RegimeSliceResult,
    RegimeTorturePolicy,
    RegimeTortureStatus,
    assess_regime_torture,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


class M169RegimeTortureTests(unittest.TestCase):
    def _defs(self, cutoff: datetime):
        return (
            RegimeDefinition("trend", _fp("trend-def"), cutoff - timedelta(days=30)),
            RegimeDefinition("range", _fp("range-def"), cutoff - timedelta(days=30)),
            RegimeDefinition("high-vol", _fp("high-vol-def"), cutoff - timedelta(days=30)),
            RegimeDefinition("low-vol", _fp("low-vol-def"), cutoff - timedelta(days=30)),
        )

    def test_complete_point_in_time_regime_survival_passes(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        defs = self._defs(cutoff)
        rows = tuple(
            RegimeSliceResult(row.definition_fingerprint, 40, 0.02 - index * 0.002, 0.05 + index * 0.01, True)
            for index, row in enumerate(defs)
        )
        result = assess_regime_torture(defs, rows, evaluation_cutoff=cutoff)
        self.assertEqual(result.status, RegimeTortureStatus.PASSED)
        self.assertEqual(result.pass_fraction, 1.0)
        self.assertEqual(result.worst_net_return, min(row.net_return for row in rows))
        self.assertFalse(result.broker_write_authority)

    def test_hindsight_regime_definition_is_rejected(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        defs = list(self._defs(cutoff))
        defs[0] = RegimeDefinition("trend", _fp("future-trend"), cutoff + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            assess_regime_torture(defs, (), evaluation_cutoff=cutoff)

    def test_missing_or_thin_regime_is_insufficient_not_pass(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        defs = self._defs(cutoff)
        missing = tuple(RegimeSliceResult(row.definition_fingerprint, 40, 0.01, 0.05, True) for row in defs[:-1])
        self.assertEqual(
            assess_regime_torture(defs, missing, evaluation_cutoff=cutoff).status,
            RegimeTortureStatus.INSUFFICIENT,
        )
        thin = tuple(
            RegimeSliceResult(row.definition_fingerprint, 10 if index == 0 else 40, 0.01, 0.05, True)
            for index, row in enumerate(defs)
        )
        self.assertEqual(
            assess_regime_torture(defs, thin, evaluation_cutoff=cutoff).status,
            RegimeTortureStatus.INSUFFICIENT,
        )

    def test_too_many_failed_regimes_fails_and_keeps_worst_case(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        defs = self._defs(cutoff)
        rows = (
            RegimeSliceResult(defs[0].definition_fingerprint, 40, 0.03, 0.04, True),
            RegimeSliceResult(defs[1].definition_fingerprint, 40, -0.08, 0.18, False),
            RegimeSliceResult(defs[2].definition_fingerprint, 40, -0.03, 0.12, False),
            RegimeSliceResult(defs[3].definition_fingerprint, 40, 0.01, 0.06, True),
        )
        result = assess_regime_torture(defs, rows, evaluation_cutoff=cutoff)
        self.assertEqual(result.status, RegimeTortureStatus.FAILED)
        self.assertEqual(result.worst_net_return, -0.08)
        self.assertEqual(result.worst_max_drawdown, 0.18)

    def test_unknown_or_duplicate_result_identity_fails_closed(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        defs = self._defs(cutoff)
        rogue = RegimeSliceResult(_fp("rogue"), 40, 0.0, 0.1, True)
        with self.assertRaises(ValueError):
            assess_regime_torture(defs, (rogue,), evaluation_cutoff=cutoff)
        duplicate = RegimeSliceResult(defs[0].definition_fingerprint, 40, 0.0, 0.1, True)
        with self.assertRaises(ValueError):
            assess_regime_torture(
                defs,
                (duplicate, duplicate),
                evaluation_cutoff=cutoff,
                policy=RegimeTorturePolicy(minimum_regimes=1),
            )


if __name__ == "__main__":
    unittest.main()
