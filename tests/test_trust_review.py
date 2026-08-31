from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.features import FeatureConfig, FeatureVector
from dusty.tester_parity import ExpectedExecutionEnvelope, ExpectedExitKind
from dusty.trust_review import (
    ArtifactFingerprint,
    Capability,
    DataProbeKind,
    LiveDataProbe,
    ProofLevel,
    SoftwareProof,
    build_m75_trust_report,
    qualify_native_indicators,
    qualify_native_tester,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 31, 17, 45, tzinfo=UTC)


class TrustReviewTests(unittest.TestCase):
    def software(self) -> SoftwareProof:
        return SoftwareProof("a" * 40, "ci-333", True, NOW)

    def artifact(self, label: str) -> ArtifactFingerprint:
        return ArtifactFingerprint.from_text(
            label,
            f"payload:{label}",
            observed_at=NOW,
            producer="test-probe",
        )

    def indicator_features(self) -> tuple[FeatureVector, ...]:
        at = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        return (
            FeatureVector.of(
                at,
                {
                    "sma_2": 1.1,
                    "ema_2": 1.11,
                    "atr_2": 0.01,
                    "rsi_2": 55.0,
                },
            ),
        )

    def indicator_csv(self, *, symbol: str = "EURUSD") -> str:
        source = int(datetime(2026, 1, 1, 0, 0, tzinfo=UTC).timestamp())
        available = int(datetime(2026, 1, 1, 0, 15, tzinfo=UTC).timestamp())
        return (
            "terminal_build,symbol,period,source_open_time,available_time,sma,ema,atr,rsi\n"
            f"5000,{symbol},PERIOD_M15,{source},{available},1.1,1.11,0.01,55\n"
        )

    def tester_expected(self) -> tuple[ExpectedExecutionEnvelope, ...]:
        entry = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        exit_at = entry + timedelta(minutes=15)
        return (
            ExpectedExecutionEnvelope(
                strategy_hash="s" * 64,
                trade_id="t1",
                side=TradeSide.LONG,
                volume=0.01,
                entry_signal_at=entry,
                entry_reference_price=1.2,
                exit_not_before=exit_at,
                exit_not_after=exit_at,
                exit_kind=ExpectedExitKind.TIME,
                exit_reference_price=1.21,
                initial_sl=1.19,
                initial_tp=0.0,
            ),
        )

    def tester_csv(self) -> str:
        entry = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        exit_at = entry + timedelta(minutes=15)
        entry_msc = int(entry.timestamp() * 1000)
        exit_msc = int(exit_at.timestamp() * 1000)
        header = (
            "terminal_build,symbol,period,strategy_hash,position_id,deal_id,time_msc,"
            "deal_type,deal_type_name,entry_type,entry_type_name,volume,price,commission,"
            "swap,profit,fee,reason,reason_name,sl,tp,comment\n"
        )
        strategy = "s" * 64
        entry_row = (
            f"5000,EURUSD,PERIOD_M15,{strategy},10,20,{entry_msc},0,buy,0,in,0.01,1.2,"
            "0,0,0,0,3,expert,1.19,0,DDT:t1\n"
        )
        exit_row = (
            f"5000,EURUSD,PERIOD_M15,{strategy},10,21,{exit_msc},1,sell,1,out,0.01,1.21,"
            "0,0,1,0,3,expert,1.19,0,DDT:t1\n"
        )
        return header + entry_row + exit_row

    def probes(self) -> tuple[LiveDataProbe, ...]:
        return tuple(
            LiveDataProbe(kind, source, self.artifact(source), 1)
            for kind, source in (
                (DataProbeKind.MARKET, "mt5_history"),
                (DataProbeKind.MACRO, "bls"),
                (DataProbeKind.EVENT, "bls_calendar"),
                (DataProbeKind.PUBLIC_STRATEGY, "github_known_repo"),
            )
        )

    def test_software_tests_alone_never_claim_native_mt5_proof(self) -> None:
        report = build_m75_trust_report(
            commit_sha="a" * 40,
            software=self.software(),
        )
        self.assertEqual(
            report.for_capability(Capability.EVIDENCE_COGNITION).level,
            ProofLevel.OPERATIONALLY_PROVEN,
        )
        self.assertEqual(
            report.for_capability(Capability.MARKET_FEATURES).level,
            ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED,
        )
        self.assertEqual(
            report.for_capability(Capability.MT5_LABORATORY).level,
            ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED,
        )
        self.assertFalse(report.operationally_trusted)

    def test_stale_ci_commit_cannot_certify_newer_code(self) -> None:
        report = build_m75_trust_report(
            commit_sha="b" * 40,
            software=self.software(),
            data_probes=self.probes(),
        )
        self.assertFalse(report.operationally_trusted)
        self.assertTrue(
            all(
                assessment.level is ProofLevel.FAILED
                and assessment.reasons == ("software_proof_commit_mismatch",)
                for assessment in report.assessments
            )
        )

    def test_native_indicator_proof_requires_matching_environment_metadata(self) -> None:
        config = FeatureConfig(ma_period=2, atr_period=2, rsi_period=2)
        good = qualify_native_indicators(
            self.indicator_features(),
            self.indicator_csv(),
            config=config,
            expected_symbol="EURUSD",
            expected_period="PERIOD_M15",
            observed_at=NOW,
            min_rows=1,
        )
        self.assertTrue(good.passed)
        self.assertEqual(good.environment.terminal_build, 5000)
        self.assertEqual(len(good.input_sha256), 64)

        wrong = qualify_native_indicators(
            self.indicator_features(),
            self.indicator_csv(symbol="GBPUSD"),
            config=config,
            expected_symbol="EURUSD",
            expected_period="PERIOD_M15",
            observed_at=NOW,
            min_rows=1,
        )
        self.assertFalse(wrong.passed)
        self.assertIn("native_symbol_mismatch", wrong.reasons)

    def test_native_tester_proof_binds_deals_to_environment_and_semantics(self) -> None:
        proof = qualify_native_tester(
            self.tester_expected(),
            self.tester_csv(),
            expected_symbol="EURUSD",
            expected_period="PERIOD_M15",
            observed_at=NOW,
            max_entry_delay_seconds=0.0,
            max_entry_price_gap=0.0,
            max_exit_price_gap=0.0,
        )
        self.assertTrue(proof.passed)
        self.assertEqual(proof.parity.matched, 1)
        self.assertEqual(len(proof.input_sha256), 64)

    def test_full_operational_trust_requires_live_data_and_both_native_mt5_artifacts(self) -> None:
        config = FeatureConfig(ma_period=2, atr_period=2, rsi_period=2)
        indicator = qualify_native_indicators(
            self.indicator_features(),
            self.indicator_csv(),
            config=config,
            expected_symbol="EURUSD",
            expected_period="PERIOD_M15",
            observed_at=NOW,
            min_rows=1,
        )
        tester = qualify_native_tester(
            self.tester_expected(),
            self.tester_csv(),
            expected_symbol="EURUSD",
            expected_period="PERIOD_M15",
            observed_at=NOW,
            max_entry_delay_seconds=0.0,
            max_entry_price_gap=0.0,
            max_exit_price_gap=0.0,
        )
        report = build_m75_trust_report(
            commit_sha="a" * 40,
            software=self.software(),
            data_probes=self.probes(),
            indicator_proof=indicator,
            tester_proof=tester,
        )
        self.assertTrue(report.operationally_trusted)
        self.assertTrue(
            all(
                assessment.level is ProofLevel.OPERATIONALLY_PROVEN
                for assessment in report.assessments
            )
        )


if __name__ == "__main__":
    unittest.main()
