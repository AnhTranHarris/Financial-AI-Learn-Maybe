from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.provider_degradation import (
    ProviderDegradationPolicy,
    ProviderFleetAssessment,
    ProviderHealthObservation,
    ProviderObservationOutcome,
    ProviderOperationalStatus,
    assess_provider_degradation,
)


UTC = timezone.utc
START = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)
MODEL = sha256(b"chronos2-pinned-identity").hexdigest()


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def observation(index: int, outcome: ProviderObservationOutcome, *, provider: str = "chronos2", model: str = MODEL):
    return ProviderHealthObservation(
        provider,
        model,
        START + timedelta(seconds=index),
        outcome,
        fp(f"evidence-{index}-{outcome.value}"),
        outcome.value,
    )


class M191ProviderDegradationTests(unittest.TestCase):
    def test_no_health_evidence_is_unavailable_not_healthy_by_default(self) -> None:
        result = assess_provider_degradation((), provider_id="chronos2", model_identity_fingerprint=MODEL)
        self.assertEqual(result.status, ProviderOperationalStatus.UNAVAILABLE)
        self.assertFalse(result.new_evidence_allowed)
        self.assertTrue(result.deterministic_core_operational)

    def test_two_explicit_successes_are_required_for_healthy(self) -> None:
        one = assess_provider_degradation(
            (observation(1, ProviderObservationOutcome.SUCCESS),),
            provider_id="chronos2",
            model_identity_fingerprint=MODEL,
        )
        self.assertEqual(one.status, ProviderOperationalStatus.DEGRADED)
        two = assess_provider_degradation(
            (
                observation(1, ProviderObservationOutcome.SUCCESS),
                observation(2, ProviderObservationOutcome.SUCCESS),
            ),
            provider_id="chronos2",
            model_identity_fingerprint=MODEL,
        )
        self.assertEqual(two.status, ProviderOperationalStatus.HEALTHY)
        self.assertTrue(two.new_evidence_allowed)

    def test_transient_failure_degrades_and_recovery_requires_fresh_successes(self) -> None:
        rows = (
            observation(1, ProviderObservationOutcome.SUCCESS),
            observation(2, ProviderObservationOutcome.SUCCESS),
            observation(3, ProviderObservationOutcome.TRANSIENT_FAILURE),
        )
        degraded = assess_provider_degradation(rows, provider_id="chronos2", model_identity_fingerprint=MODEL)
        self.assertEqual(degraded.status, ProviderOperationalStatus.DEGRADED)
        one_success = assess_provider_degradation(
            rows + (observation(4, ProviderObservationOutcome.SUCCESS),),
            provider_id="chronos2",
            model_identity_fingerprint=MODEL,
        )
        self.assertEqual(one_success.status, ProviderOperationalStatus.DEGRADED)
        recovered = assess_provider_degradation(
            rows
            + (
                observation(4, ProviderObservationOutcome.SUCCESS),
                observation(5, ProviderObservationOutcome.SUCCESS),
            ),
            provider_id="chronos2",
            model_identity_fingerprint=MODEL,
        )
        self.assertEqual(recovered.status, ProviderOperationalStatus.HEALTHY)

    def test_repeated_resource_or_response_failures_become_unavailable(self) -> None:
        for outcome in (ProviderObservationOutcome.RESOURCE_BLOCKED, ProviderObservationOutcome.INVALID_RESPONSE):
            with self.subTest(outcome=outcome):
                rows = tuple(observation(i, outcome) for i in range(1, 5))
                result = assess_provider_degradation(rows, provider_id="chronos2", model_identity_fingerprint=MODEL)
                self.assertEqual(result.status, ProviderOperationalStatus.UNAVAILABLE)
                self.assertEqual(result.consecutive_failures, 4)

    def test_identity_drift_is_sticky_and_cannot_age_out_of_health_window(self) -> None:
        policy = ProviderDegradationPolicy(observation_window=5)
        rows = [observation(1, ProviderObservationOutcome.IDENTITY_DRIFT)]
        rows.extend(observation(i, ProviderObservationOutcome.SUCCESS) for i in range(2, 12))
        result = assess_provider_degradation(
            tuple(rows),
            provider_id="chronos2",
            model_identity_fingerprint=MODEL,
            policy=policy,
        )
        self.assertEqual(result.status, ProviderOperationalStatus.QUARANTINED)
        self.assertFalse(result.new_evidence_allowed)
        self.assertIn("external_revalidation", result.reason)

    def test_wrong_model_identity_or_mixed_provider_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "model identity"):
            assess_provider_degradation(
                (observation(1, ProviderObservationOutcome.SUCCESS, model=fp("wrong-model")),),
                provider_id="chronos2",
                model_identity_fingerprint=MODEL,
            )
        with self.assertRaisesRegex(ValueError, "mix providers"):
            assess_provider_degradation(
                (
                    observation(1, ProviderObservationOutcome.SUCCESS),
                    observation(2, ProviderObservationOutcome.SUCCESS, provider="kronos-small"),
                ),
                provider_id="chronos2",
                model_identity_fingerprint=MODEL,
            )

    def test_duplicate_or_same_timestamp_evidence_is_rejected(self) -> None:
        row = observation(1, ProviderObservationOutcome.SUCCESS)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assess_provider_degradation((row, row), provider_id="chronos2", model_identity_fingerprint=MODEL)
        other = ProviderHealthObservation(
            "chronos2",
            MODEL,
            row.observed_at,
            ProviderObservationOutcome.TRANSIENT_FAILURE,
            fp("other-evidence"),
            "same timestamp",
        )
        with self.assertRaisesRegex(ValueError, "unique timestamps"):
            assess_provider_degradation((row, other), provider_id="chronos2", model_identity_fingerprint=MODEL)

    def test_fleet_can_have_no_optional_providers_without_collapsing_core(self) -> None:
        unavailable = assess_provider_degradation((), provider_id="chronos2", model_identity_fingerprint=MODEL)
        kronos = assess_provider_degradation(
            (), provider_id="kronos-small", model_identity_fingerprint=fp("kronos-model")
        )
        qwen = assess_provider_degradation(
            (), provider_id="qwen-reviewer", model_identity_fingerprint=fp("qwen-model")
        )
        fleet = ProviderFleetAssessment((unavailable, kronos, qwen))
        self.assertTrue(fleet.all_optional_providers_unavailable)
        self.assertTrue(fleet.deterministic_core_operational)
        self.assertEqual(fleet.usable_provider_ids, ())

    def test_assessment_never_controls_provider_process_weight_or_trading(self) -> None:
        result = assess_provider_degradation(
            (
                observation(1, ProviderObservationOutcome.SUCCESS),
                observation(2, ProviderObservationOutcome.SUCCESS),
            ),
            provider_id="chronos2",
            model_identity_fingerprint=MODEL,
        )
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.provider_restart_authority)
        self.assertFalse(result.provider_selection_authority)
        self.assertFalse(result.evidence_weight_override_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
