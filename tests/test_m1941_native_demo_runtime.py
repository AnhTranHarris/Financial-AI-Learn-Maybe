from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dusty.champion_registry import FrozenChampionRecord
from dusty.demo_session import AccountMode
from dusty.m194_native_demo_journal import SQLiteM194NativeEvidenceJournal
from dusty.m194_native_demo_preflight import (
    NativeDemoPreflightStatus,
    NativeDemoTerminalSnapshot,
    assess_native_demo_preflight,
)
from dusty.m194_native_demo_runtime import (
    account_identity_fingerprint,
    record_native_demo_heartbeat,
    start_native_demo_run,
    terminal_identity_fingerprint,
)


NOW = datetime(2026, 9, 8, 7, 0, tzinfo=timezone.utc)
HEAD = "a" * 40


def fp(seed: str) -> str:
    import hashlib
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class RegistryStub:
    def __init__(self, champion: FrozenChampionRecord | None, *, integrity: bool = True) -> None:
        self.champion = champion
        self.integrity = integrity

    def integrity_check(self):
        return (self.integrity, () if self.integrity else ("bad",))

    def active_for_lane(self, lane_id: str):
        if self.champion is None or self.champion.lane_id != lane_id:
            return None
        return self.champion

    def state(self, champion_fingerprint: str):
        from dusty.champion_registry import ChampionLifecycleState
        if self.champion is None or champion_fingerprint != self.champion.fingerprint:
            raise KeyError(champion_fingerprint)
        return ChampionLifecycleState.ACTIVE


class M1941NativeDemoRuntimeTests(unittest.TestCase):
    def champion(self) -> FrozenChampionRecord:
        return FrozenChampionRecord(
            lane_id="eurusd:m15:breakout",
            generation_id="generation-1",
            strategy_family="breakout",
            strategy_fingerprint=fp("strategy"),
            analysis_graph_fingerprint=fp("graph"),
            tool_fingerprints=(fp("tool-1"), fp("tool-2")),
            source_commit="9" * 40,
            selection_evidence_fingerprint=fp("selection"),
            robustness_fingerprint=fp("robustness"),
            forecast_integration_fingerprint=None,
            parent_champion_fingerprint=None,
            created_at=NOW - timedelta(days=2),
        )

    def snapshot(self, **changes) -> NativeDemoTerminalSnapshot:
        row = NativeDemoTerminalSnapshot(
            terminal_path=r"C:\Program Files\Coinexx MT5 Terminal\terminal64.exe",
            terminal_build="6182",
            connected=True,
            terminal_trade_allowed=True,
            tradeapi_disabled=False,
            server="Coinexx-Demo",
            login=123456,
            account_mode=AccountMode.DEMO,
            account_trade_allowed=True,
            account_expert_allowed=True,
            account_currency="USD",
            leverage=500.0,
            symbol="EURUSD",
            symbol_spec_fingerprint=fp("symbol"),
            captured_at=NOW,
        )
        return replace(row, **changes) if changes else row

    def assessment(self, snapshot: NativeDemoTerminalSnapshot):
        return assess_native_demo_preflight(
            snapshot,
            source_commit=HEAD,
            expected_terminal_path=snapshot.terminal_path,
        )

    def test_start_binds_active_champion_and_ready_preflight(self):
        champion = self.champion()
        snapshot = self.snapshot()
        assessment = self.assessment(snapshot)
        self.assertEqual(assessment.status, NativeDemoPreflightStatus.READY)
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            identity = start_native_demo_run(
                registry=RegistryStub(champion),
                journal=journal,
                lane_id=champion.lane_id,
                run_id="desk-run-001",
                source_commit=HEAD,
                snapshot=snapshot,
                assessment=assessment,
                started_at=NOW,
            )
            self.assertEqual(identity.champion_fingerprint, champion.fingerprint)
            self.assertEqual(identity.terminal_fingerprint, terminal_identity_fingerprint(snapshot))
            self.assertEqual(identity.account_fingerprint, account_identity_fingerprint(snapshot))
            self.assertFalse(identity.broker_write_authority)
            self.assertFalse(identity.live_write_authority)
            summary = journal.summary(identity.run_id)
            self.assertTrue(summary["started"])
            self.assertEqual(summary["heartbeats"], 0)

    def test_blocked_preflight_cannot_start_real_run(self):
        champion = self.champion()
        snapshot = self.snapshot(terminal_trade_allowed=False)
        assessment = self.assessment(snapshot)
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            with self.assertRaisesRegex(PermissionError, "READY preflight"):
                start_native_demo_run(
                    registry=RegistryStub(champion), journal=journal,
                    lane_id=champion.lane_id, run_id="desk-run-001", source_commit=HEAD,
                    snapshot=snapshot, assessment=assessment, started_at=NOW,
                )

    def test_missing_active_champion_cannot_start(self):
        snapshot = self.snapshot()
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            with self.assertRaisesRegex(PermissionError, "ACTIVE M185 Champion"):
                start_native_demo_run(
                    registry=RegistryStub(None), journal=journal,
                    lane_id="eurusd:m15:breakout", run_id="desk-run-001", source_commit=HEAD,
                    snapshot=snapshot, assessment=self.assessment(snapshot), started_at=NOW,
                )

    def test_registry_integrity_failure_blocks_start(self):
        champion = self.champion()
        snapshot = self.snapshot()
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            with self.assertRaisesRegex(RuntimeError, "registry integrity failure"):
                start_native_demo_run(
                    registry=RegistryStub(champion, integrity=False), journal=journal,
                    lane_id=champion.lane_id, run_id="desk-run-001", source_commit=HEAD,
                    snapshot=snapshot, assessment=self.assessment(snapshot), started_at=NOW,
                )

    def test_heartbeat_requires_same_terminal_account_and_symbol(self):
        champion = self.champion()
        snapshot = self.snapshot()
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            identity = start_native_demo_run(
                registry=RegistryStub(champion), journal=journal,
                lane_id=champion.lane_id, run_id="desk-run-001", source_commit=HEAD,
                snapshot=snapshot, assessment=self.assessment(snapshot), started_at=NOW,
            )
            later = replace(snapshot, captured_at=NOW + timedelta(seconds=30))
            record_native_demo_heartbeat(
                journal=journal, identity=identity, snapshot=later,
                assessment=self.assessment(later), observed_at=later.captured_at,
            )
            self.assertEqual(journal.summary(identity.run_id)["heartbeats"], 1)

            drift = replace(later, login=999999, captured_at=NOW + timedelta(seconds=60))
            with self.assertRaisesRegex(PermissionError, "account identity drift"):
                record_native_demo_heartbeat(
                    journal=journal, identity=identity, snapshot=drift,
                    assessment=self.assessment(drift), observed_at=drift.captured_at,
                )

    def test_preflight_source_drift_is_rejected(self):
        champion = self.champion()
        snapshot = self.snapshot()
        assessment = assess_native_demo_preflight(
            snapshot,
            source_commit="c" * 40,
            expected_terminal_path=snapshot.terminal_path,
        )
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            with self.assertRaisesRegex(ValueError, "source commit drift"):
                start_native_demo_run(
                    registry=RegistryStub(champion), journal=journal,
                    lane_id=champion.lane_id, run_id="desk-run-001", source_commit=HEAD,
                    snapshot=snapshot, assessment=assessment, started_at=NOW,
                )


if __name__ == "__main__":
    unittest.main()
