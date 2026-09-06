from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dusty.source_intake import proposals_from_vibe
from dusty.strategy_discovery import (
    DiscoveryMode,
    DiscoveryStatus,
    DiscoveryTrigger,
    StrategyDiscoveryConfig,
    StrategyDiscoveryService,
    StrategyDiscoveryState,
    central_slot_utc,
    load_discovery_state,
    next_sunday_slot_utc,
    write_discovery_state,
)
from dusty.strategy_estate import StrategyEstateUpdate
from dusty.strategy_estate_builder import (
    EstatePopulationResult,
    EstatePopulationRow,
    EstatePopulationStatus,
)
from dusty.vibe_research_contract import (
    EXPECTED_VIBE_VERSION,
    PROTOCOL,
    PROVIDER_ID,
    VibeResearchEvidence,
    VibeResearchResult,
    VibeResearchStatus,
)


H = lambda ch: ch * 64
NOW = datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc)


def available(tool: str, payload: dict[str, object], marker: str = "a") -> VibeResearchResult:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    evidence = VibeResearchEvidence(
        PROTOCOL,
        PROVIDER_ID,
        tool,
        EXPECTED_VIBE_VERSION,
        H(marker),
        H("b"),
        H("c"),
        text,
    )
    return VibeResearchResult(VibeResearchStatus.AVAILABLE, evidence=evidence)


def unavailable(error: str = "simulated") -> VibeResearchResult:
    return VibeResearchResult(VibeResearchStatus.UNAVAILABLE, error=error)


def catalog_result(*, cross: bool = False) -> VibeResearchResult:
    item = {
        "id": "alpha:pair" if cross else "alpha:momentum",
        "nickname": "Cointegration Pairs Trading" if cross else "RSI Momentum",
        "columns_required": ["close", "rsi"],
        "theme": ["cointegration", "pairs trading"] if cross else ["momentum"],
        "frequency": ["M15"],
        "formula_latex": "zscore(spread)" if cross else "RSI > 55",
    }
    return available(
        "list_strategies",
        {"status": "ok", "result": {"total": 1, "items": [item]}},
    )


def web_result(marker: str = "d") -> VibeResearchResult:
    return available(
        "web_search",
        {
            "status": "ok",
            "result": {
                "items": [
                    {
                        "title": "Intermarket lead",
                        "url": "https://example.com/intermarket",
                        "snippet": "cross asset research lead",
                    }
                ]
            },
        },
        marker,
    )


class FakeContractor:
    def __init__(self, catalog: VibeResearchResult, web: VibeResearchResult | None = None):
        self.catalog = catalog
        self.web = web or web_result()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke(self, tool: str, arguments: dict[str, object]) -> VibeResearchResult:
        self.calls.append((tool, dict(arguments)))
        return self.catalog if tool == "list_strategies" else self.web


class FakeBuilder:
    def __init__(self):
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def populate(self, proposals, **kwargs):
        rows_in = tuple(proposals)
        self.calls.append((rows_in, dict(kwargs)))
        rows = tuple(
            EstatePopulationRow(
                row.proposal_id,
                EstatePopulationStatus.ADDED,
                reconstruction_fingerprint=H("e"),
            )
            for row in rows_in
        )
        update = StrategyEstateUpdate(
            Path(kwargs["estate_path"]),
            H("f"),
            len(rows),
            len(rows),
        ) if rows else None
        return EstatePopulationResult(rows, update)


class StrategyDiscoveryTests(unittest.TestCase):
    def config(self, root: Path) -> StrategyDiscoveryConfig:
        return StrategyDiscoveryConfig(
            root / "VibeTrading",
            root / "work",
            root / "estate.json",
            root / "state.json",
            root / "reports",
            catalog_limit=100,
            max_reconstructions=6,
        )

    def test_central_schedule_is_dst_aware_without_external_tzdata(self) -> None:
        self.assertEqual(
            central_slot_utc(date(2026, 9, 6)),
            datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            central_slot_utc(date(2026, 12, 6)),
            datetime(2026, 12, 6, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            next_sunday_slot_utc(NOW),
            datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
        )

    def test_schedule_due_after_new_sunday_and_failure_has_cooldown(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            previous = datetime(2026, 8, 30, 13, 10, tzinfo=timezone.utc)
            write_discovery_state(
                config.state_path,
                StrategyDiscoveryState(previous, previous, DiscoveryStatus.COMPLETED, ""),
            )
            service = StrategyDiscoveryService(
                config,
                contractor_factory=lambda *_: FakeContractor(catalog_result()),
                builder=FakeBuilder(),
                digest_resolver=lambda _tag: H("d"),
            )
            self.assertFalse(service.scheduled_due(NOW))
            sunday = datetime(2026, 9, 6, 13, 1, tzinfo=timezone.utc)
            self.assertTrue(service.scheduled_due(sunday))
            failed_at = sunday + timedelta(minutes=4)
            write_discovery_state(
                config.state_path,
                StrategyDiscoveryState(failed_at, previous, DiscoveryStatus.UNAVAILABLE, "report.json"),
            )
            self.assertFalse(service.scheduled_due(failed_at + timedelta(minutes=30)))
            self.assertTrue(service.scheduled_due(failed_at + timedelta(hours=1, minutes=1)))

    def test_both_mode_adds_only_bounded_single_symbol_candidates(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            contractor = FakeContractor(catalog_result())
            builder = FakeBuilder()
            service = StrategyDiscoveryService(
                self.config(root),
                contractor_factory=lambda *_: contractor,
                builder=builder,
                digest_resolver=lambda tag: H("d") if tag == "qwen3:1.7b" else "",
            )
            result = service.discover(DiscoveryMode.BOTH, trigger=DiscoveryTrigger.MANUAL, now=NOW)
            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            self.assertEqual(result.proposals_seen, 1)
            self.assertEqual(result.new_single_symbol_candidates, 1)
            self.assertEqual(result.added_to_estate, 1)
            self.assertEqual(result.cross_symbol_web_leads, 3)
            self.assertTrue(result.restart_required)
            self.assertEqual(len(builder.calls), 1)
            _, kwargs = builder.calls[0]
            self.assertEqual(kwargs["model_digest"], H("d"))
            self.assertEqual(Path(kwargs["estate_path"]), self.config(root).estate_path)
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["authority"]["broker_write"])
            self.assertFalse(report["authority"]["hot_swap_running_snapshot"])
            self.assertIn("untrusted research leads", report["cross_symbol_research"]["policy"])
            state = load_discovery_state(self.config(root).state_path)
            self.assertEqual(state.last_completed_utc, NOW)

    def test_cross_symbol_catalog_candidate_is_deferred_not_compiled(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            builder = FakeBuilder()
            service = StrategyDiscoveryService(
                self.config(root),
                contractor_factory=lambda *_: FakeContractor(catalog_result(cross=True)),
                builder=builder,
                digest_resolver=lambda _tag: (_ for _ in ()).throw(AssertionError("Ollama must not run")),
            )
            result = service.discover(DiscoveryMode.BOTH, now=NOW)
            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            self.assertEqual(result.deferred_cross_symbol_candidates, 1)
            self.assertEqual(result.new_single_symbol_candidates, 0)
            self.assertEqual(builder.calls, [])
            self.assertEqual(result.cross_symbol_web_leads, 3)

    def test_existing_proposal_fingerprint_is_not_reconstructed_again(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = catalog_result()
            assert catalog.evidence is not None
            proposal = proposals_from_vibe(catalog.evidence)[0]
            builder = FakeBuilder()
            service = StrategyDiscoveryService(
                self.config(root),
                contractor_factory=lambda *_: FakeContractor(catalog),
                builder=builder,
                digest_resolver=lambda _tag: (_ for _ in ()).throw(AssertionError("digest lookup must not run")),
            )
            with patch(
                "dusty.strategy_discovery.load_strategy_estate",
                return_value=(SimpleNamespace(proposal_fingerprint=proposal.fingerprint),),
            ):
                result = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)
            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            self.assertEqual(result.new_single_symbol_candidates, 0)
            self.assertEqual(builder.calls, [])

    def test_total_source_failure_is_unavailable_and_does_not_forge_completion(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            contractor = FakeContractor(unavailable("catalog down"), unavailable("web down"))
            service = StrategyDiscoveryService(
                self.config(root),
                contractor_factory=lambda *_: contractor,
                builder=FakeBuilder(),
                digest_resolver=lambda _tag: H("d"),
            )
            result = service.discover(DiscoveryMode.BOTH, now=NOW)
            self.assertEqual(result.status, DiscoveryStatus.UNAVAILABLE)
            self.assertGreaterEqual(len(result.errors), 4)
            self.assertTrue(result.report_path.is_file())
            state = load_discovery_state(self.config(root).state_path)
            self.assertIsNone(state.last_completed_utc)
            self.assertEqual(state.last_attempt_utc, NOW)


if __name__ == "__main__":
    unittest.main()
