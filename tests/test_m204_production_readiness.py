import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from dusty.long_running_soak import (
    LongRunningSoakCertification,
    LongRunningSoakStatus,
)
from dusty.market_clock import MarketClockState
from dusty.production_observability import OperationalObservation, assess_production_health
from dusty.production_readiness import (
    ProductionReadinessStatus,
    RestrictedProductionEvidence,
    certify_restricted_production,
)
from dusty.provider_degradation import ProviderOperationalStatus
from dusty.release_certification import (
    ReleaseCertificationStatus,
    ReleaseRollbackCertification,
)
from dusty.security_boundary import (
    SecurityBoundaryCertification,
    SecurityBoundaryStatus,
)


def _sha(ch: str) -> str:
    return ch * 64


def _security(status=SecurityBoundaryStatus.CERTIFIED):
    return SecurityBoundaryCertification(status, _sha("a"), _sha("b"), ())


def _soak(status=LongRunningSoakStatus.CERTIFIED):
    return LongRunningSoakCertification(
        status=status,
        policy_fingerprint=_sha("c"),
        graduation_fingerprint=_sha("d"),
        evidence_fingerprint=_sha("e"),
        observed_duration_seconds=3600.0,
        heartbeat_count=60,
        observed_disturbances=("data_gap", "mt5_reconnect"),
        blockers=(),
    )


def _release(status=ReleaseCertificationStatus.CERTIFIED):
    return ReleaseRollbackCertification(status, _sha("f"), _sha("1"), ())


def _health(security=None):
    now = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    observation = OperationalObservation(
        observed_at=now,
        last_heartbeat_at=now - timedelta(seconds=20),
        terminal_connected=True,
        terminal_identity_ok=True,
        account_identity_ok=True,
        market_state=MarketClockState.SCHEDULED_CLOSED,
        provider_statuses=(ProviderOperationalStatus.HEALTHY,),
        open_positions=0,
        active_orders=0,
        unresolved_reconciliation_count=0,
        recovery_halt_active=False,
        champion_suspended=False,
        portfolio_risk_halt=False,
        state_integrity_ok=True,
        artifact_integrity_ok=True,
        security=security or _security(),
    )
    return assess_production_health(observation)


def _evidence():
    commit = "9" * 40
    security = _security()
    return RestrictedProductionEvidence(
        source_commit=commit,
        runtime_source_commit=commit,
        release_source_commit=commit,
        soak=_soak(),
        security=security,
        health=_health(security),
        release=_release(),
        exact_terminal_identity_bound=True,
        exact_account_identity_bound=True,
        exact_broker_profile_bound=True,
        strategy_champion_frozen=True,
        portfolio_risk_constitution_bound=True,
        guardian_bound=True,
        demo_and_live_state_separation_proven=True,
        explicit_live_authorization_absent=True,
    )


class M204ProductionReadinessTests(unittest.TestCase):
    def test_complete_evidence_is_ready_for_explicit_live_authorization(self):
        result = certify_restricted_production(_evidence())
        self.assertIs(result.status, ProductionReadinessStatus.READY_FOR_EXPLICIT_LIVE_AUTHORIZATION)
        self.assertTrue(result.ready_for_human_live_authorization)
        self.assertEqual(result.blockers, ())

    def test_source_identity_drift_rejects(self):
        result = certify_restricted_production(
            replace(_evidence(), runtime_source_commit="8" * 40)
        )
        self.assertIs(result.status, ProductionReadinessStatus.REJECTED)

    def test_pending_soak_blocks_without_manufacturing_rejection(self):
        result = certify_restricted_production(
            replace(_evidence(), soak=_soak(LongRunningSoakStatus.PENDING))
        )
        self.assertIs(result.status, ProductionReadinessStatus.PENDING)

    def test_rejected_soak_rejects(self):
        result = certify_restricted_production(
            replace(_evidence(), soak=_soak(LongRunningSoakStatus.REJECTED))
        )
        self.assertIs(result.status, ProductionReadinessStatus.REJECTED)

    def test_security_pending_blocks_and_rejected_security_rejects(self):
        pending = _security(SecurityBoundaryStatus.PENDING)
        pending_result = certify_restricted_production(
            replace(_evidence(), security=pending, health=_health(pending))
        )
        self.assertIs(pending_result.status, ProductionReadinessStatus.PENDING)

        rejected = _security(SecurityBoundaryStatus.REJECTED)
        rejected_result = certify_restricted_production(
            replace(_evidence(), security=rejected, health=_health(rejected))
        )
        self.assertIs(rejected_result.status, ProductionReadinessStatus.REJECTED)

    def test_degraded_health_blocks_and_halted_health_rejects(self):
        base = _evidence()
        now = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
        obs = OperationalObservation(
            observed_at=now,
            last_heartbeat_at=now - timedelta(seconds=20),
            terminal_connected=True,
            terminal_identity_ok=True,
            account_identity_ok=True,
            market_state=MarketClockState.OPEN,
            provider_statuses=(ProviderOperationalStatus.DEGRADED,),
            open_positions=0,
            active_orders=0,
            unresolved_reconciliation_count=0,
            recovery_halt_active=False,
            champion_suspended=False,
            portfolio_risk_halt=False,
            state_integrity_ok=True,
            artifact_integrity_ok=True,
            security=base.security,
        )
        degraded = assess_production_health(obs)
        result = certify_restricted_production(replace(base, health=degraded))
        self.assertIs(result.status, ProductionReadinessStatus.PENDING)

        halted = assess_production_health(replace(obs, terminal_connected=False))
        result = certify_restricted_production(replace(base, health=halted))
        self.assertIs(result.status, ProductionReadinessStatus.REJECTED)

    def test_release_pending_blocks_and_rejected_release_rejects(self):
        result = certify_restricted_production(
            replace(_evidence(), release=_release(ReleaseCertificationStatus.PENDING))
        )
        self.assertIs(result.status, ProductionReadinessStatus.PENDING)
        result = certify_restricted_production(
            replace(_evidence(), release=_release(ReleaseCertificationStatus.REJECTED))
        )
        self.assertIs(result.status, ProductionReadinessStatus.REJECTED)

    def test_every_required_binding_can_block_readiness(self):
        fields = (
            "exact_terminal_identity_bound",
            "exact_account_identity_bound",
            "exact_broker_profile_bound",
            "strategy_champion_frozen",
            "portfolio_risk_constitution_bound",
            "guardian_bound",
            "demo_and_live_state_separation_proven",
        )
        for field in fields:
            result = certify_restricted_production(
                replace(_evidence(), **{field: False})
            )
            self.assertIs(result.status, ProductionReadinessStatus.PENDING, field)

    def test_preexisting_live_authorization_rejects(self):
        result = certify_restricted_production(
            replace(_evidence(), explicit_live_authorization_absent=False)
        )
        self.assertIs(result.status, ProductionReadinessStatus.REJECTED)
        self.assertIn("live_authorization_already_present", result.blockers)

    def test_m204_never_grants_live_or_broker_authority(self):
        result = certify_restricted_production(_evidence())
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.live_account_authorization)
        self.assertFalse(result.install_authority)
        self.assertFalse(result.credential_access_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprint_is_deterministic(self):
        left = certify_restricted_production(_evidence())
        right = certify_restricted_production(_evidence())
        self.assertEqual(left.fingerprint, right.fingerprint)


if __name__ == "__main__":
    unittest.main()
