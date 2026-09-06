from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
from pathlib import Path
import tempfile
import threading
import unittest

import dusty.correlation_concentration_guard as guard_module
from dusty.correlation_concentration_guard import (
    ConcentrationDecision,
    ConcentrationReservationState,
    DependencyEvidence,
    InstrumentFactorEvidence,
    SQLiteCorrelationConcentrationGuard,
)
from dusty.experience import TradeSide
from dusty.order_intent import BrokerPreflight, OrderIntent
from dusty.portfolio_risk_governor import PortfolioCapitalSnapshot, RiskReservationDecision, SQLitePortfolioRiskGovernor
from dusty.strategy_dependency import DependencyStatus, StrategyDependencyMatrix, StrategyDependencyPair


UTC = timezone.utc
NOW = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
SOURCE_COMMIT = "a" * 40
SESSION = sha256(b"m196-session").hexdigest()
ACCOUNT = sha256(b"m196-account").hexdigest()


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M196CorrelationConcentrationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "risk.sqlite3"
        master = SQLitePortfolioRiskGovernor(self.path, source_commit=SOURCE_COMMIT)
        master.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def intent(self, name: str, symbol: str, loss: float, *, side: TradeSide = TradeSide.LONG) -> OrderIntent:
        return OrderIntent(
            strategy_hash=fp("strategy-" + name),
            session_fingerprint=SESSION,
            symbol=symbol,
            side=side,
            volume=0.10,
            reference_price=1.1000,
            stop_price=1.0900 if side is TradeSide.LONG else 1.1100,
            target_price=1.1200 if side is TradeSide.LONG else 1.0800,
            approved_risk_fraction=max(0.000001, min(1.0, loss / 10_000.0)),
            allowed_loss=loss,
            pm_approved=True,
            growth_multiplier=1.0,
            risk_approved=True,
            guardian_approved=True,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            filling_mode=0,
        )

    def snapshot(
        self,
        *,
        equity: float = 10_000.0,
        captured_at: datetime = NOW,
        source_commit: str = SOURCE_COMMIT,
        complete: bool = True,
    ) -> PortfolioCapitalSnapshot:
        reference = max(10_000.0, equity)
        return PortfolioCapitalSnapshot(
            ACCOUNT,
            SESSION,
            source_commit,
            captured_at,
            equity,
            equity,
            reference,
            reference,
            reference,
            0.0,
            max(0.0, equity),
            fp("positions"),
            fp("orders"),
            complete,
        )

    def instrument(
        self,
        symbol: str,
        base: str | None,
        profit: str | None,
        *,
        expires_at: datetime | None = None,
        source_commit: str = SOURCE_COMMIT,
        complete: bool = True,
        additional: tuple[tuple[str, float], ...] = (),
    ) -> InstrumentFactorEvidence:
        expires = expires_at or (NOW + timedelta(hours=1))
        captured = NOW if expires > NOW else NOW - timedelta(hours=2)
        return InstrumentFactorEvidence.from_metaquotes_metadata(
            symbol=symbol,
            source_commit=source_commit,
            captured_at=captured,
            expires_at=expires,
            metadata_fingerprint=fp("metadata-" + symbol),
            evidence_fingerprints=(fp("instrument-" + symbol),),
            currency_base=base,
            currency_profit=profit,
            currency_margin=base,
            additional_directional_factors=additional,
            complete=complete,
        )

    def factors(self, symbol: str) -> InstrumentFactorEvidence:
        mapping = {
            "EURUSD": ("EUR", "USD"),
            "GBPUSD": ("GBP", "USD"),
            "EURJPY": ("EUR", "JPY"),
            "GBPCHF": ("GBP", "CHF"),
            "AUDCAD": ("AUD", "CAD"),
        }
        base, profit = mapping.get(symbol, (None, None))
        return self.instrument(symbol, base, profit)

    def dependency(
        self,
        intents: tuple[OrderIntent, ...],
        *,
        corr_pairs: set[tuple[str, str]] | None = None,
        coloss_pairs: set[tuple[str, str]] | None = None,
        expires_at: datetime | None = None,
        observations: int = 40,
        source_commit: str = SOURCE_COMMIT,
        complete: bool = True,
    ) -> DependencyEvidence:
        corr_pairs = corr_pairs or set()
        coloss_pairs = coloss_pairs or set()
        strategies = tuple(sorted({intent.strategy_hash for intent in intents}))
        pairs: list[StrategyDependencyPair] = []
        for index, left in enumerate(strategies):
            for right in strategies[index + 1 :]:
                key = (left, right)
                pairs.append(
                    StrategyDependencyPair(
                        left,
                        right,
                        0.95 if key in corr_pairs else 0.10,
                        0.90 if key in coloss_pairs else 0.10,
                    )
                )
        matrix = StrategyDependencyMatrix(
            DependencyStatus.DIVERSIFIED,
            strategies,
            observations,
            tuple(pairs),
            max((abs(row.correlation) for row in pairs), default=None),
            max((row.co_loss_fraction for row in pairs), default=None),
            "test evidence",
        )
        expires = expires_at or (NOW + timedelta(hours=1))
        captured = NOW if expires > NOW else NOW - timedelta(hours=2)
        return DependencyEvidence(
            matrix,
            source_commit,
            captured,
            expires,
            (fp("dependency-" + "-".join(strategies)),),
            complete,
        )

    def guard(self) -> SQLiteCorrelationConcentrationGuard:
        return SQLiteCorrelationConcentrationGuard(self.path, source_commit=SOURCE_COMMIT)

    def preflight(self, intent: OrderIntent) -> BrokerPreflight:
        return BrokerPreflight(
            intent,
            True,
            intent.allowed_loss * 0.8,
            25.0,
            1.1001,
            (("symbol", intent.symbol),),
            (),
        )

    def reserve_guard(
        self,
        guard: SQLiteCorrelationConcentrationGuard,
        intent: OrderIntent,
        *,
        dependency: DependencyEvidence | None = None,
        instrument: InstrumentFactorEvidence | None = None,
        snapshot: PortfolioCapitalSnapshot | None = None,
        now: datetime = NOW,
    ):
        return guard.reserve(
            intent,
            snapshot or self.snapshot(),
            instrument or self.factors(intent.symbol),
            dependency,
            now=now,
            evidence_fingerprints=(fp("guard-" + intent.intent_hash),),
        )

    def reserve_master(self, intent: OrderIntent):
        master = SQLitePortfolioRiskGovernor(self.path, source_commit=SOURCE_COMMIT)
        try:
            return master.reserve(
                intent,
                self.preflight(intent),
                self.snapshot(),
                now=NOW,
                evidence_fingerprints=(fp("master-" + intent.intent_hash),),
            )
        finally:
            master.close()

    def test_metaquotes_metadata_supports_broker_suffix_and_direction(self) -> None:
        evidence = self.instrument("EURUSDm", "EUR", "USD")
        self.assertEqual(evidence.exposures_for(TradeSide.LONG), (("CCY:EUR", 1.0), ("CCY:USD", -1.0)))
        self.assertEqual(evidence.exposures_for(TradeSide.SHORT), (("CCY:EUR", -1.0), ("CCY:USD", 1.0)))

    def test_missing_currency_metadata_falls_back_to_exact_symbol(self) -> None:
        self.assertEqual(
            self.instrument("US500.cash", None, None).long_factor_exposures,
            (("SYMBOL:US500.CASH", 1.0),),
        )

    def test_common_usd_net_factor_cap_blocks_stack(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURUSD", 70.0)
            second = self.intent("b", "GBPUSD", 70.0)
            self.assertEqual(self.reserve_guard(guard, first).decision, ConcentrationDecision.APPROVED)
            result = self.reserve_guard(guard, second, dependency=self.dependency((first, second)))
            self.assertEqual(result.decision, ConcentrationDecision.DENIED)
            self.assertIn("factor_heat_ceiling:CCY:USD", result.reasons)
            self.assertAlmostEqual(result.maximum_factor_net_heat, 0.014)
        finally:
            guard.close()

    def test_opposite_direction_offsets_net_but_preserves_gross(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURUSD", 70.0)
            second = self.intent("b", "GBPUSD", 70.0, side=TradeSide.SHORT)
            self.reserve_guard(guard, first)
            result = self.reserve_guard(guard, second, dependency=self.dependency((first, second)))
            self.assertEqual(result.decision, ConcentrationDecision.APPROVED)
            self.assertAlmostEqual(dict(result.factor_net_heat)["CCY:USD"], 0.0)
            self.assertAlmostEqual(dict(result.factor_gross_heat)["CCY:USD"], 0.014)
        finally:
            guard.close()

    def test_m173_high_correlation_creates_single_capped_dependency_bucket(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURJPY", 60.0)
            second = self.intent("b", "GBPCHF", 60.0)
            self.reserve_guard(guard, first)
            pair = tuple(sorted((first.strategy_hash, second.strategy_hash)))
            result = self.reserve_guard(guard, second, dependency=self.dependency((first, second), corr_pairs={pair}))
            self.assertEqual(result.decision, ConcentrationDecision.DENIED)
            self.assertEqual(result.dependency_breaches, (pair,))
            self.assertAlmostEqual(result.maximum_dependency_cluster_heat, 0.012)
            self.assertTrue(any(reason.startswith("dependency_cluster_heat_ceiling:") for reason in result.reasons))
        finally:
            guard.close()

    def test_m173_coloss_breach_is_enforced_from_raw_pairs_not_claimed_status(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURJPY", 60.0)
            second = self.intent("b", "GBPCHF", 60.0)
            self.reserve_guard(guard, first)
            pair = tuple(sorted((first.strategy_hash, second.strategy_hash)))
            evidence = self.dependency((first, second), coloss_pairs={pair})
            self.assertEqual(evidence.matrix.status, DependencyStatus.DIVERSIFIED)
            result = self.reserve_guard(guard, second, dependency=evidence)
            self.assertEqual(result.decision, ConcentrationDecision.DENIED)
            self.assertEqual(result.dependency_breaches, (pair,))
        finally:
            guard.close()

    def test_negative_correlation_uses_m173_absolute_semantics(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURJPY", 60.0)
            second = self.intent("b", "GBPCHF", 60.0)
            self.reserve_guard(guard, first)
            strategies = tuple(sorted((first.strategy_hash, second.strategy_hash)))
            matrix = StrategyDependencyMatrix(
                DependencyStatus.CONCENTRATED,
                strategies,
                40,
                (StrategyDependencyPair(strategies[0], strategies[1], -0.95, 0.0),),
                0.95,
                0.0,
                "negative dependency",
            )
            evidence = DependencyEvidence(matrix, SOURCE_COMMIT, NOW, NOW + timedelta(hours=1), (fp("negative"),))
            result = self.reserve_guard(guard, second, dependency=evidence)
            self.assertEqual(result.decision, ConcentrationDecision.DENIED)
        finally:
            guard.close()

    def test_dependency_evidence_missing_expired_sparse_or_wrong_universe_fails_closed(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURJPY", 40.0)
            second = self.intent("b", "GBPCHF", 40.0)
            third = self.intent("c", "AUDCAD", 40.0)
            self.reserve_guard(guard, first)
            cases = (
                (None, "dependency_evidence_missing"),
                (self.dependency((first, second), expires_at=NOW - timedelta(seconds=1)), "dependency_evidence_expired"),
                (self.dependency((first, second), observations=10), "dependency_evidence_insufficient"),
                (self.dependency((first, third)), "dependency_strategy_universe_mismatch"),
            )
            for evidence, reason in cases:
                with self.subTest(reason=reason):
                    result = self.reserve_guard(guard, second, dependency=evidence)
                    self.assertEqual(result.decision, ConcentrationDecision.DENIED)
                    self.assertIn(reason, result.reasons)
        finally:
            guard.close()

    def test_snapshot_and_instrument_integrity_fail_closed(self) -> None:
        cases = (
            (self.snapshot(captured_at=NOW - timedelta(minutes=2)), self.factors("EURUSD"), "capital_snapshot_stale"),
            (self.snapshot(complete=False), self.factors("EURUSD"), "incomplete_broker_exposure_data"),
            (self.snapshot(source_commit="b" * 40), self.factors("EURUSD"), "capital_snapshot_source_commit_mismatch"),
            (self.snapshot(), self.instrument("EURUSD", "EUR", "USD", expires_at=NOW - timedelta(seconds=1)), "instrument_factor_evidence_expired"),
            (self.snapshot(), self.instrument("EURUSD", "EUR", "USD", complete=False), "instrument_factor_evidence_incomplete"),
            (self.snapshot(), self.instrument("EURUSD", "EUR", "USD", source_commit="b" * 40), "instrument_evidence_source_commit_mismatch"),
        )
        for snapshot, instrument, reason in cases:
            with self.subTest(reason=reason):
                guard = self.guard()
                try:
                    result = self.reserve_guard(guard, self.intent(reason, "EURUSD", 40.0), snapshot=snapshot, instrument=instrument)
                    self.assertEqual(result.decision, ConcentrationDecision.DENIED)
                    self.assertIn(reason, result.reasons)
                finally:
                    guard.close()

    def test_equity_drawdown_revalues_existing_concentration_heat(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURUSD", 50.0)
            second = self.intent("b", "GBPUSD", 50.0)
            self.reserve_guard(guard, first)
            result = self.reserve_guard(
                guard,
                second,
                dependency=self.dependency((first, second)),
                snapshot=self.snapshot(equity=5_000.0),
            )
            self.assertEqual(result.decision, ConcentrationDecision.DENIED)
            self.assertAlmostEqual(result.maximum_factor_net_heat, 0.02)
        finally:
            guard.close()

    def test_direct_m195_bypass_blocks_future_m196_admission(self) -> None:
        bypass = self.intent("bypass", "EURJPY", 40.0)
        self.assertEqual(self.reserve_master(bypass).decision, RiskReservationDecision.APPROVED)
        guard = self.guard()
        try:
            result = self.reserve_guard(guard, self.intent("new", "GBPCHF", 40.0))
            self.assertEqual(result.decision, ConcentrationDecision.DENIED)
            self.assertIn("unmapped_active_master_risk", result.reasons)
        finally:
            guard.close()

    def test_preexisting_m195_exposure_can_be_explicitly_adopted(self) -> None:
        existing = self.intent("existing", "EURJPY", 40.0)
        master = self.reserve_master(existing)
        guard = self.guard()
        try:
            receipt = guard.adopt_active_master(
                existing,
                self.snapshot(),
                self.factors(existing.symbol),
                None,
                now=NOW,
                evidence_fingerprints=(fp("adopt"),),
            )
            self.assertEqual(receipt.state, ConcentrationReservationState.BOUND)
            self.assertEqual(receipt.master_record_fingerprint, master.record_fingerprint)
        finally:
            guard.close()

    def test_full_reserve_bind_release_lifecycle_survives_restart(self) -> None:
        intent = self.intent("life", "EURJPY", 40.0)
        guard = self.guard()
        approved = self.reserve_guard(guard, intent)
        self.assertEqual(approved.decision, ConcentrationDecision.APPROVED)
        guard.close()

        master_result = self.reserve_master(intent)
        reopened = self.guard()
        try:
            bound = reopened.bind_master(
                intent.intent_hash,
                master_result.record_fingerprint or "",
                now=NOW + timedelta(seconds=1),
                evidence_fingerprints=(fp("bind"),),
            )
            self.assertEqual(bound.state, ConcentrationReservationState.BOUND)
            master = SQLitePortfolioRiskGovernor(self.path, source_commit=SOURCE_COMMIT)
            try:
                master.release_pre_send(
                    intent.intent_hash,
                    now=NOW + timedelta(seconds=2),
                    no_send_evidence_fingerprints=(fp("no-send"),),
                )
            finally:
                master.close()
            released = reopened.release_after_master(
                intent.intent_hash,
                now=NOW + timedelta(seconds=3),
                master_release_evidence_fingerprints=(fp("release"),),
            )
            self.assertEqual(released.state, ConcentrationReservationState.RELEASED)
            self.assertEqual(released.master_record_fingerprint, master_result.record_fingerprint)
            self.assertTrue(reopened.integrity_check()[0])
        finally:
            reopened.close()

    def test_bind_requires_exact_active_m195_record(self) -> None:
        intent = self.intent("bind", "EURJPY", 40.0)
        guard = self.guard()
        try:
            self.reserve_guard(guard, intent)
            master = self.reserve_master(intent)
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                guard.bind_master(
                    intent.intent_hash,
                    fp("wrong-master"),
                    now=NOW + timedelta(seconds=1),
                    evidence_fingerprints=(fp("wrong-bind"),),
                )
            receipt = guard.bind_master(
                intent.intent_hash,
                master.record_fingerprint or "",
                now=NOW + timedelta(seconds=1),
                evidence_fingerprints=(fp("right-bind"),),
            )
            self.assertEqual(receipt.state, ConcentrationReservationState.BOUND)
        finally:
            guard.close()

    def test_pre_master_release_cannot_race_past_existing_m195_record(self) -> None:
        intent = self.intent("release", "EURJPY", 40.0)
        guard = self.guard()
        try:
            self.reserve_guard(guard, intent)
            self.reserve_master(intent)
            with self.assertRaisesRegex(ValueError, "M195 record exists"):
                guard.release_pre_master(
                    intent.intent_hash,
                    now=NOW + timedelta(seconds=1),
                    no_master_evidence_fingerprints=(fp("bad-release"),),
                )
        finally:
            guard.close()

    def test_idempotent_release_revalidates_original_master_provenance(self) -> None:
        intent = self.intent("idempotent", "EURJPY", 40.0)
        guard = self.guard()
        try:
            self.reserve_guard(guard, intent)
            guard.release_pre_master(
                intent.intent_hash,
                now=NOW + timedelta(seconds=1),
                no_master_evidence_fingerprints=(fp("pre-master-release"),),
            )
            with self.assertRaisesRegex(ValueError, "required M195 master reservation does not exist"):
                guard.release_after_master(
                    intent.intent_hash,
                    now=NOW + timedelta(seconds=2),
                    master_release_evidence_fingerprints=(fp("false-post-master"),),
                )
        finally:
            guard.close()

    def test_released_m196_intent_cannot_be_resurrected(self) -> None:
        intent = self.intent("released", "EURJPY", 40.0)
        guard = self.guard()
        try:
            self.reserve_guard(guard, intent)
            guard.release_pre_master(
                intent.intent_hash,
                now=NOW + timedelta(seconds=1),
                no_master_evidence_fingerprints=(fp("release"),),
            )
            with self.assertRaisesRegex(ValueError, "cannot be resurrected"):
                self.reserve_guard(guard, intent)
        finally:
            guard.close()

    def test_two_connections_cannot_race_past_common_factor_limit(self) -> None:
        first = self.intent("race-a", "EURUSD", 70.0)
        second = self.intent("race-b", "GBPUSD", 70.0)
        dependency = self.dependency((first, second))
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        decisions: list[ConcentrationDecision] = []
        failures: list[BaseException] = []

        def worker(intent: OrderIntent) -> None:
            guard = self.guard()
            try:
                barrier.wait(timeout=5)
                result = self.reserve_guard(guard, intent, dependency=dependency)
                with lock:
                    decisions.append(result.decision)
            except BaseException as exc:  # pragma: no cover
                with lock:
                    failures.append(exc)
            finally:
                guard.close()

        threads = [threading.Thread(target=worker, args=(intent,)) for intent in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(failures, failures)
        self.assertEqual(sorted(value.value for value in decisions), ["approved", "denied"])

    def test_hhi_and_effective_count_are_diagnostics_only(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("a", "EURJPY", 50.0)
            second = self.intent("b", "AUDCAD", 50.0)
            self.reserve_guard(guard, first)
            result = self.reserve_guard(guard, second, dependency=self.dependency((first, second)))
            self.assertEqual(result.decision, ConcentrationDecision.APPROVED)
            self.assertAlmostEqual(result.risk_hhi, 0.5)
            self.assertAlmostEqual(result.effective_risk_count, 2.0)
            self.assertEqual(result.portfolio_loss_after, 100.0)
        finally:
            guard.close()

    def test_integrity_fingerprint_corruption_blocks_new_risk(self) -> None:
        guard = self.guard()
        try:
            first = self.intent("first", "EURJPY", 40.0)
            self.reserve_guard(guard, first)
            guard._db.execute(
                "UPDATE m196_concentration_records SET reserved_loss=41 WHERE intent_hash=?",
                (first.intent_hash,),
            )
            ok, errors = guard.integrity_check()
            self.assertFalse(ok)
            self.assertTrue(any("record_fingerprint" in error for error in errors))
            with self.assertRaisesRegex(RuntimeError, "integrity failure"):
                self.reserve_guard(guard, self.intent("next", "GBPCHF", 40.0))
        finally:
            guard.close()

    def test_master_validation_is_inside_same_immediate_transaction(self) -> None:
        source = inspect.getsource(SQLiteCorrelationConcentrationGuard._transition)
        self.assertLess(source.index("self._begin()"), source.index("self._validate_master"))

    def test_m196_has_no_execution_llm_optimizer_or_override_authority(self) -> None:
        guard = self.guard()
        try:
            result = self.reserve_guard(guard, self.intent("authority", "EURJPY", 40.0))
            self.assertFalse(result.broker_write_authority)
            self.assertFalse(result.live_write_authority)
            self.assertFalse(result.llm_authority)
            self.assertFalse(result.guardian_override_authority)
            self.assertFalse(result.risk_override_authority)
            self.assertFalse(result.promotion_authority)
            self.assertFalse(guard.broker_write_authorized)
            self.assertFalse(guard.live_write_authorized)
            self.assertFalse(guard.llm_authorized)
            source = inspect.getsource(guard_module)
            for forbidden in ("MetaTrader5", "order_send(", "openai", "ollama", "allocate_portfolio("):
                self.assertNotIn(forbidden, source)
        finally:
            guard.close()


if __name__ == "__main__":
    unittest.main()
