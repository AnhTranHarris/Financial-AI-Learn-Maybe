import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from dusty.market_clock import MarketClockState
from dusty.production_observability import (
    AlertSeverity,
    FirmHealth,
    OperationalObservation,
    assess_production_health,
)
from dusty.provider_degradation import ProviderOperationalStatus
from dusty.security_boundary import (
    SecurityBoundaryEvidence,
    SecurityBoundaryPolicy,
    certify_security_boundary,
)


def _sha(ch: str) -> str:
    return ch * 64


def _security():
    evidence = SecurityBoundaryEvidence(
        source_commit="a" * 40,
        terminal_executable_path=r"C:\Program Files\Coinexx MT5 Terminal\terminal64.exe",
        terminal_identity_fingerprint=_sha("b"),
        account_identity_fingerprint=_sha("c"),
        credential_fields_persisted=(),
        raw_account_login_persisted=False,
        model_endpoint="http://127.0.0.1:11434",
        broker_write_surfaces=(
            "src/dusty/demo_execution.py",
            "src/dusty/demo_execution_bridge.py",
        ),
        broad_process_termination_used=False,
        shell_execution_used=False,
        state_write_roots=(r"C:\Users\user\AppData\Local\DustyDragon\validation",),
        mql5_dll_imports_present=False,
        mql5_webrequest_present=False,
        generated_mt5_config_contains_credentials=False,
        live_write_surface_present=False,
        source_tree_integrity_ok=True,
    )
    return certify_security_boundary(SecurityBoundaryPolicy(), evidence)


def _observation():
    now = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    return OperationalObservation(
        observed_at=now,
        last_heartbeat_at=now - timedelta(seconds=30),
        terminal_connected=True,
        terminal_identity_ok=True,
        account_identity_ok=True,
        market_state=MarketClockState.OPEN,
        provider_statuses=(ProviderOperationalStatus.HEALTHY,),
        open_positions=0,
        active_orders=0,
        unresolved_reconciliation_count=0,
        recovery_halt_active=False,
        champion_suspended=False,
        portfolio_risk_halt=False,
        state_integrity_ok=True,
        artifact_integrity_ok=True,
        security=_security(),
    )


class M202ProductionObservabilityTests(unittest.TestCase):
    def test_healthy_runtime_is_healthy(self):
        result = assess_production_health(_observation())
        self.assertIs(result.health, FirmHealth.HEALTHY)
        self.assertEqual(result.alerts, ())

    def test_delayed_heartbeat_degrades(self):
        obs = replace(
            _observation(),
            last_heartbeat_at=_observation().observed_at - timedelta(minutes=7),
        )
        result = assess_production_health(obs)
        self.assertIs(result.health, FirmHealth.DEGRADED)
        self.assertTrue(any(row.code == "heartbeat_delayed" for row in result.alerts))

    def test_critical_heartbeat_halts(self):
        obs = replace(
            _observation(),
            last_heartbeat_at=_observation().observed_at - timedelta(minutes=16),
        )
        result = assess_production_health(obs)
        self.assertIs(result.health, FirmHealth.HALTED)

    def test_terminal_disconnect_halts(self):
        result = assess_production_health(replace(_observation(), terminal_connected=False))
        self.assertIs(result.health, FirmHealth.HALTED)

    def test_terminal_or_account_identity_drift_halts(self):
        for kwargs in ({"terminal_identity_ok": False}, {"account_identity_ok": False}):
            result = assess_production_health(replace(_observation(), **kwargs))
            self.assertIs(result.health, FirmHealth.HALTED)

    def test_scheduled_market_closure_is_not_an_alert(self):
        result = assess_production_health(
            replace(_observation(), market_state=MarketClockState.SCHEDULED_CLOSED)
        )
        self.assertIs(result.health, FirmHealth.HEALTHY)

    def test_unexpected_stale_market_halts(self):
        result = assess_production_health(
            replace(_observation(), market_state=MarketClockState.UNEXPECTED_STALE_MARKET)
        )
        self.assertIs(result.health, FirmHealth.HALTED)

    def test_broker_maintenance_degrades_not_panics(self):
        result = assess_production_health(
            replace(_observation(), market_state=MarketClockState.BROKER_MAINTENANCE)
        )
        self.assertIs(result.health, FirmHealth.DEGRADED)

    def test_all_optional_providers_unavailable_only_degrades(self):
        result = assess_production_health(
            replace(
                _observation(),
                provider_statuses=(ProviderOperationalStatus.UNAVAILABLE,),
            )
        )
        self.assertIs(result.health, FirmHealth.DEGRADED)
        self.assertTrue(any(row.code == "optional_providers_unavailable" for row in result.alerts))

    def test_unresolved_reconciliation_halts(self):
        result = assess_production_health(
            replace(_observation(), unresolved_reconciliation_count=1)
        )
        self.assertIs(result.health, FirmHealth.HALTED)

    def test_recovery_and_portfolio_halts_are_critical(self):
        for kwargs in ({"recovery_halt_active": True}, {"portfolio_risk_halt": True}):
            result = assess_production_health(replace(_observation(), **kwargs))
            self.assertIs(result.health, FirmHealth.HALTED)

    def test_champion_suspension_degrades(self):
        result = assess_production_health(replace(_observation(), champion_suspended=True))
        self.assertIs(result.health, FirmHealth.DEGRADED)

    def test_integrity_failure_halts(self):
        for kwargs in ({"state_integrity_ok": False}, {"artifact_integrity_ok": False}):
            result = assess_production_health(replace(_observation(), **kwargs))
            self.assertIs(result.health, FirmHealth.HALTED)

    def test_rejected_security_boundary_halts(self):
        sec = _security()
        bad_evidence = SecurityBoundaryEvidence(
            source_commit="a" * 40,
            terminal_executable_path=r"C:\Program Files\Coinexx MT5 Terminal\terminal64.exe",
            terminal_identity_fingerprint=_sha("b"),
            account_identity_fingerprint=_sha("c"),
            credential_fields_persisted=("password",),
            raw_account_login_persisted=False,
            model_endpoint="http://127.0.0.1:11434",
            broker_write_surfaces=("src/dusty/demo_execution.py",),
            broad_process_termination_used=False,
            shell_execution_used=False,
            state_write_roots=(r"C:\Users\user\AppData\Local\DustyDragon",),
            mql5_dll_imports_present=False,
            mql5_webrequest_present=False,
            generated_mt5_config_contains_credentials=False,
            live_write_surface_present=False,
            source_tree_integrity_ok=True,
        )
        from dusty.security_boundary import certify_security_boundary, SecurityBoundaryPolicy
        bad = certify_security_boundary(SecurityBoundaryPolicy(), bad_evidence)
        self.assertNotEqual(sec.status, bad.status)
        result = assess_production_health(replace(_observation(), security=bad))
        self.assertIs(result.health, FirmHealth.HALTED)

    def test_alert_order_and_fingerprint_are_deterministic(self):
        obs = replace(
            _observation(),
            terminal_connected=False,
            champion_suspended=True,
            provider_statuses=(ProviderOperationalStatus.DEGRADED,),
        )
        left = assess_production_health(obs)
        right = assess_production_health(obs)
        self.assertEqual(left.fingerprint, right.fingerprint)
        severities = [int(row.severity) for row in left.alerts]
        self.assertEqual(severities, sorted(severities, reverse=True))

    def test_snapshot_has_no_operational_authority(self):
        result = assess_production_health(_observation())
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.restart_authority)
        self.assertFalse(result.credential_access_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
