from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from dusty.champion_registry import (
    ChampionLifecycleEvent,
    ChampionLifecycleEventType,
    ChampionLifecycleState,
    ChampionRegistryIntegrityError,
    FrozenChampionRecord,
    FrozenChampionRegistry,
    freeze_champion_record,
)
from dusty.forecast_integration_certification import (
    ForecastIntegrationCertification,
    ForecastIntegrationStatus,
)
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus
from dusty.strategy_v3 import FrozenStrategyDeployment


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)
SOURCE_COMMIT = "1cb068e8948e0b143eb4bb94e0b44a2e038fc485"


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M185FrozenChampionRegistryTests(unittest.TestCase):
    def robustness(self, *, status: RobustnessGateStatus = RobustnessGateStatus.SERIOUS_CHALLENGER) -> RobustnessCertification:
        blockers = () if status is RobustnessGateStatus.SERIOUS_CHALLENGER else ("walk_forward",)
        return RobustnessCertification(
            status,
            (
                ("broker_calibration", "calibrated"),
                ("walk_forward", "pass_fraction=0.9"),
                ("parameter_neighborhood", "stable"),
                ("regime_torture", "passed"),
                ("cost_torture", "passed=True"),
                ("historical_forward_decay", "retention=0.75"),
                ("tail_risk", "dd=0.1;cvar=0.08"),
                ("strategy_dependency", "diversified"),
            ),
            blockers,
            tuple(fp(f"robustness-{index}") for index in range(8)),
        )

    def deployment(
        self,
        *,
        generation: str = "generation-1",
        strategy: str = "strategy-1",
        graph: str = "graph-1",
        tools: tuple[str, ...] = ("tool-1", "tool-2"),
    ) -> FrozenStrategyDeployment:
        return FrozenStrategyDeployment(
            fp(strategy),
            fp(graph),
            tuple(fp(value) for value in tools),
            generation,
        )

    def forecast(
        self,
        deployment: FrozenStrategyDeployment,
        *,
        family: str = "breakout",
        status: ForecastIntegrationStatus = ForecastIntegrationStatus.RESEARCH_INTEGRATION_ELIGIBLE,
    ) -> ForecastIntegrationCertification:
        blockers = () if status is ForecastIntegrationStatus.RESEARCH_INTEGRATION_ELIGIBLE else ("blocked",)
        return ForecastIntegrationCertification(
            deployment.strategy_hash,
            fp("evaluation"),
            fp("execution-cost"),
            family,
            fp("forecast-variant"),
            fp("forecast-bucket"),
            status,
            (
                ("strategy_robustness", "serious_challenger"),
                ("adaptive_evidence_weight", "weighted;weight=0.8"),
                ("matched_ablation", "beneficial"),
                ("information_value", "positive"),
                ("strategy_interaction", "beneficial"),
            ),
            blockers,
            tuple(fp(f"m184-evidence-{index}") for index in range(5)),
            fp("m184-policy"),
        )

    def record(
        self,
        *,
        deployment: FrozenStrategyDeployment | None = None,
        robustness: RobustnessCertification | None = None,
        forecast: ForecastIntegrationCertification | None = None,
        parent: str | None = None,
        created_at: datetime = NOW,
        selection: str = "selection-1",
    ) -> FrozenChampionRecord:
        dep = deployment or self.deployment()
        robust = robustness or self.robustness()
        return freeze_champion_record(
            lane_id="EURUSD:M15:breakout",
            strategy_family="breakout",
            deployment=dep,
            source_commit=SOURCE_COMMIT,
            selection_evidence_fingerprint=fp(selection),
            robustness=robust,
            forecast_integration=forecast,
            parent_champion_fingerprint=parent,
            created_at=created_at,
        )

    def registration_evidence(
        self,
        record: FrozenChampionRecord,
        *,
        extra: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        values = [record.selection_evidence_fingerprint, record.robustness_fingerprint]
        if record.forecast_integration_fingerprint is not None:
            values.append(record.forecast_integration_fingerprint)
        values.extend(extra)
        return tuple(values)

    def register(
        self,
        registry: FrozenChampionRegistry,
        record: FrozenChampionRecord,
        *,
        robustness: RobustnessCertification,
        forecast: ForecastIntegrationCertification | None = None,
    ) -> FrozenChampionRecord:
        return registry.register(
            record,
            robustness=robustness,
            forecast_integration=forecast,
            actor_fingerprint=fp("governance-actor"),
            evidence_fingerprints=self.registration_evidence(record),
            reason="external deterministic selection frozen",
        )

    def test_nonforecast_champion_is_content_addressed_active_and_authority_free(self) -> None:
        robust = self.robustness()
        record = self.record(robustness=robust)
        registry = FrozenChampionRegistry()
        try:
            stored = self.register(registry, record, robustness=robust)
            self.assertEqual(stored.fingerprint, record.fingerprint)
            self.assertEqual(registry.get(record.fingerprint), record)
            self.assertEqual(registry.state(record.fingerprint), ChampionLifecycleState.ACTIVE)
            self.assertEqual(registry.active_for_lane(record.lane_id), record)
            self.assertTrue(registry.integrity_check()[0])
            self.assertFalse(record.broker_write_authority)
            self.assertFalse(record.promotion_authority)
            self.assertFalse(record.strategy_mutation_authority)
            self.assertFalse(record.risk_override_authority)
            self.assertFalse(record.guardian_override_authority)
            self.assertFalse(registry.broker_write_authorized)
            self.assertFalse(registry.promotion_authorized)
            self.assertFalse(registry.strategy_mutation_authorized)
        finally:
            registry.close()

    def test_forecast_enabled_champion_requires_exact_m184_identity(self) -> None:
        robust = self.robustness()
        dep = self.deployment()
        forecast = self.forecast(dep)
        record = self.record(deployment=dep, robustness=robust, forecast=forecast)
        registry = FrozenChampionRegistry()
        try:
            self.register(registry, record, robustness=robust, forecast=forecast)
            self.assertEqual(registry.get(record.fingerprint), record)
            with self.assertRaisesRegex(ValueError, "requires M184"):
                registry.register(
                    record,
                    robustness=robust,
                    forecast_integration=None,
                    actor_fingerprint=fp("governance-actor"),
                    evidence_fingerprints=self.registration_evidence(record),
                    reason="missing M184",
                )
        finally:
            registry.close()

    def test_m174_pending_or_rejected_cannot_be_frozen_or_registered(self) -> None:
        for status in (RobustnessGateStatus.PENDING, RobustnessGateStatus.REJECTED):
            with self.subTest(status=status):
                robust = self.robustness(status=status)
                with self.assertRaisesRegex(ValueError, "serious-challenger"):
                    self.record(robustness=robust)

        robust = self.robustness()
        record = self.record(robustness=robust)
        fake = replace(robust, status=RobustnessGateStatus.REJECTED, blockers=("walk_forward",))
        registry = FrozenChampionRegistry()
        try:
            with self.assertRaisesRegex(ValueError, "serious-challenger"):
                self.register(registry, record, robustness=fake)
        finally:
            registry.close()

    def test_m184_status_strategy_and_family_drift_fail_closed(self) -> None:
        robust = self.robustness()
        dep = self.deployment()
        with self.assertRaisesRegex(ValueError, "integration eligibility"):
            self.record(
                deployment=dep,
                robustness=robust,
                forecast=self.forecast(dep, status=ForecastIntegrationStatus.REJECTED),
            )
        wrong_strategy = replace(self.forecast(dep), strategy_fingerprint=fp("other-strategy"))
        with self.assertRaisesRegex(ValueError, "strategy identity"):
            self.record(deployment=dep, robustness=robust, forecast=wrong_strategy)
        wrong_family = self.forecast(dep, family="mean-reversion")
        with self.assertRaisesRegex(ValueError, "strategy family"):
            self.record(deployment=dep, robustness=robust, forecast=wrong_family)

    def test_registration_requires_selection_and_certification_fingerprints(self) -> None:
        robust = self.robustness()
        record = self.record(robustness=robust)
        registry = FrozenChampionRegistry()
        try:
            with self.assertRaisesRegex(ValueError, "missing required"):
                registry.register(
                    record,
                    robustness=robust,
                    forecast_integration=None,
                    actor_fingerprint=fp("governance-actor"),
                    evidence_fingerprints=(record.robustness_fingerprint,),
                    reason="selection evidence omitted",
                )
        finally:
            registry.close()

    def test_same_lane_generation_is_immutable_but_exact_retry_is_idempotent(self) -> None:
        robust = self.robustness()
        record = self.record(robustness=robust)
        registry = FrozenChampionRegistry()
        try:
            first = self.register(registry, record, robustness=robust)
            second = self.register(registry, record, robustness=robust)
            self.assertEqual(first, second)
            drifted = replace(record, strategy_fingerprint=fp("mutated-strategy"))
            with self.assertRaisesRegex(ChampionRegistryIntegrityError, "immutable"):
                self.register(registry, drifted, robustness=robust)
            self.assertEqual(len(registry.lineage(record.lane_id)), 1)
        finally:
            registry.close()

    def test_first_champion_cannot_claim_parent_and_second_requires_parent(self) -> None:
        robust = self.robustness()
        registry = FrozenChampionRegistry()
        try:
            fake_parent = fp("not-present")
            first_with_parent = self.record(robustness=robust, parent=fake_parent)
            with self.assertRaisesRegex(ValueError, "first Champion"):
                self.register(registry, first_with_parent, robustness=robust)

            first = self.record(robustness=robust)
            self.register(registry, first, robustness=robust)
            second = self.record(
                deployment=self.deployment(generation="generation-2", strategy="strategy-2"),
                robustness=robust,
                created_at=NOW + timedelta(minutes=2),
                selection="selection-2",
            )
            with self.assertRaisesRegex(ValueError, "explicit parent"):
                self.register(registry, second, robustness=robust)
        finally:
            registry.close()

    def test_successor_requires_exact_append_only_supersession_and_changed_deployment(self) -> None:
        robust = self.robustness()
        registry = FrozenChampionRegistry()
        try:
            first = self.record(robustness=robust)
            self.register(registry, first, robustness=robust)

            unchanged = self.record(
                deployment=self.deployment(generation="generation-2"),
                robustness=robust,
                parent=first.fingerprint,
                created_at=NOW + timedelta(minutes=2),
                selection="selection-unchanged",
            )
            supersede_unchanged = ChampionLifecycleEvent(
                first.fingerprint,
                ChampionLifecycleEventType.SUPERSEDED,
                fp("governance-actor"),
                (fp("selection-unchanged"),),
                "candidate selected",
                NOW + timedelta(minutes=1),
                unchanged.fingerprint,
            )
            registry.append_lifecycle_event(supersede_unchanged)
            with self.assertRaisesRegex(ValueError, "unchanged deployment"):
                self.register(registry, unchanged, robustness=robust)
        finally:
            registry.close()

    def test_suspended_champion_cannot_reactivate_but_can_be_superseded(self) -> None:
        robust = self.robustness()
        registry = FrozenChampionRegistry()
        try:
            first = self.record(robustness=robust)
            self.register(registry, first, robustness=robust)
            suspended = ChampionLifecycleEvent(
                first.fingerprint,
                ChampionLifecycleEventType.SUSPENDED,
                fp("drift-watch"),
                (fp("drift-evidence"),),
                "drift detected",
                NOW + timedelta(minutes=1),
            )
            registry.append_lifecycle_event(suspended)
            self.assertEqual(registry.state(first.fingerprint), ChampionLifecycleState.SUSPENDED)
            self.assertIsNone(registry.active_for_lane(first.lane_id))

            with self.assertRaisesRegex(ValueError, "registered lifecycle event"):
                registry.append_lifecycle_event(
                    ChampionLifecycleEvent(
                        first.fingerprint,
                        ChampionLifecycleEventType.REGISTERED,
                        fp("governance-actor"),
                        (fp("reactivation-attempt"),),
                        "reactivate",
                        NOW + timedelta(minutes=2),
                    )
                )

            second = self.record(
                deployment=self.deployment(generation="generation-2", strategy="strategy-2"),
                robustness=robust,
                parent=first.fingerprint,
                created_at=NOW + timedelta(minutes=4),
                selection="selection-2",
            )
            superseded = ChampionLifecycleEvent(
                first.fingerprint,
                ChampionLifecycleEventType.SUPERSEDED,
                fp("governance-actor"),
                (fp("selection-2"),),
                "replacement selected after suspension",
                NOW + timedelta(minutes=3),
                second.fingerprint,
            )
            registry.append_lifecycle_event(superseded)
            self.register(registry, second, robustness=robust)
            self.assertEqual(registry.state(first.fingerprint), ChampionLifecycleState.SUPERSEDED)
            self.assertEqual(registry.state(second.fingerprint), ChampionLifecycleState.ACTIVE)
            self.assertEqual(registry.active_for_lane(first.lane_id), second)
            self.assertEqual(tuple(row.fingerprint for row in registry.lineage(first.lane_id)), (first.fingerprint, second.fingerprint))
        finally:
            registry.close()

    def test_retired_or_superseded_champion_is_terminal(self) -> None:
        robust = self.robustness()
        registry = FrozenChampionRegistry()
        try:
            record = self.record(robustness=robust)
            self.register(registry, record, robustness=robust)
            retired = ChampionLifecycleEvent(
                record.fingerprint,
                ChampionLifecycleEventType.RETIRED,
                fp("governance-actor"),
                (fp("retirement-evidence"),),
                "retired",
                NOW + timedelta(minutes=1),
            )
            registry.append_lifecycle_event(retired)
            with self.assertRaisesRegex(ValueError, "illegal Champion lifecycle transition"):
                registry.append_lifecycle_event(
                    ChampionLifecycleEvent(
                        record.fingerprint,
                        ChampionLifecycleEventType.SUSPENDED,
                        fp("drift-watch"),
                        (fp("late-drift"),),
                        "too late",
                        NOW + timedelta(minutes=2),
                    )
                )
        finally:
            registry.close()

    def test_successor_must_be_named_before_registration_and_cannot_predate_supersession(self) -> None:
        robust = self.robustness()
        registry = FrozenChampionRegistry()
        try:
            first = self.record(robustness=robust)
            self.register(registry, first, robustness=robust)
            second = self.record(
                deployment=self.deployment(generation="generation-2", strategy="strategy-2"),
                robustness=robust,
                parent=first.fingerprint,
                created_at=NOW + timedelta(minutes=1),
                selection="selection-2",
            )
            wrong = ChampionLifecycleEvent(
                first.fingerprint,
                ChampionLifecycleEventType.SUPERSEDED,
                fp("governance-actor"),
                (fp("selection-2"),),
                "wrong successor identity",
                NOW + timedelta(minutes=2),
                fp("different-successor"),
            )
            registry.append_lifecycle_event(wrong)
            with self.assertRaisesRegex(ValueError, "does not name this exact successor"):
                self.register(registry, second, robustness=robust)
        finally:
            registry.close()

    def test_lifecycle_event_retry_is_idempotent_and_time_cannot_regress(self) -> None:
        robust = self.robustness()
        registry = FrozenChampionRegistry()
        try:
            record = self.record(robustness=robust)
            self.register(registry, record, robustness=robust)
            event = ChampionLifecycleEvent(
                record.fingerprint,
                ChampionLifecycleEventType.SUSPENDED,
                fp("drift-watch"),
                (fp("drift-evidence"),),
                "suspend",
                NOW + timedelta(minutes=1),
            )
            first = registry.append_lifecycle_event(event)
            second = registry.append_lifecycle_event(event)
            self.assertEqual(first.fingerprint, second.fingerprint)
            with self.assertRaisesRegex(ValueError, "time cannot move backwards"):
                registry.append_lifecycle_event(
                    ChampionLifecycleEvent(
                        record.fingerprint,
                        ChampionLifecycleEventType.RETIRED,
                        fp("governance-actor"),
                        (fp("retire-evidence"),),
                        "backdated retire",
                        NOW,
                    )
                )
        finally:
            registry.close()

    def test_registry_detects_champion_payload_tamper(self) -> None:
        robust = self.robustness()
        with tempfile.TemporaryDirectory() as folder:
            registry = FrozenChampionRegistry(Path(folder) / "champions.sqlite3")
            try:
                record = self.record(robustness=robust)
                self.register(registry, record, robustness=robust)
                registry._db.execute(  # noqa: SLF001 - deliberate corruption test
                    "UPDATE frozen_champions SET payload=? WHERE champion_fingerprint=?",
                    ("{}", record.fingerprint),
                )
                with self.assertRaises(ChampionRegistryIntegrityError):
                    registry.get(record.fingerprint)
                self.assertFalse(registry.integrity_check()[0])
            finally:
                registry.close()

    def test_registry_detects_lifecycle_payload_or_fingerprint_tamper(self) -> None:
        robust = self.robustness()
        with tempfile.TemporaryDirectory() as folder:
            registry = FrozenChampionRegistry(Path(folder) / "champions.sqlite3")
            try:
                record = self.record(robustness=robust)
                self.register(registry, record, robustness=robust)
                registry._db.execute(  # noqa: SLF001 - deliberate corruption test
                    "UPDATE champion_lifecycle_events SET event_fingerprint=? WHERE champion_fingerprint=?",
                    (fp("forged-event-fingerprint"), record.fingerprint),
                )
                self.assertFalse(registry.integrity_check()[0])
            finally:
                registry.close()

    def test_schema_version_and_tool_identity_fail_closed(self) -> None:
        robust = self.robustness()
        record = self.record(robustness=robust)
        with self.assertRaisesRegex(ValueError, "schema version"):
            replace(record, schema_version=2)
        with self.assertRaisesRegex(ValueError, "unique ordered tool"):
            replace(record, tool_fingerprints=(fp("tool"), fp("tool")))


if __name__ == "__main__":
    unittest.main()
