from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dusty.skills_ui import _discovery_service
from dusty.strategy_discovery import (
    DiscoveryMode,
    DiscoveryStatus,
    StrategyDiscoveryConfig,
)
from dusty.strategy_discovery_campaign_service import BatchedStrategyDiscoveryService
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
NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)


def available_catalog(items: list[dict[str, object]]) -> VibeResearchResult:
    text = json.dumps(
        {"status": "ok", "result": {"total": len(items), "items": items}},
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence = VibeResearchEvidence(
        PROTOCOL,
        PROVIDER_ID,
        "list_strategies",
        EXPECTED_VIBE_VERSION,
        H("a"),
        H("b"),
        H("c"),
        text,
    )
    return VibeResearchResult(VibeResearchStatus.AVAILABLE, evidence=evidence)


def item(index: int, *, cross: bool = False) -> dict[str, object]:
    return {
        "id": f"alpha:{index}",
        "nickname": f"Strategy {index}" if not cross else f"Cointegration Pair {index}",
        "columns_required": ["close", "rsi"],
        "theme": [f"theme-{index}"] if not cross else ["cointegration", "pairs trading"],
        "frequency": ["M15"],
        "formula_latex": f"RSI > {40 + index}" if not cross else "zscore(spread)",
    }


class FakeContractor:
    def __init__(self, catalog: VibeResearchResult):
        self.catalog = catalog
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke(self, tool: str, arguments: dict[str, object]) -> VibeResearchResult:
        self.calls.append((tool, dict(arguments)))
        if tool != "list_strategies":
            raise AssertionError("NEW_STRATEGIES integration test must not call web search")
        return self.catalog


class FakeBuilder:
    def __init__(self):
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.total = 0

    def populate(self, proposals, **kwargs):
        rows_in = tuple(proposals)
        if len(rows_in) != 1:
            raise AssertionError("M196.6 must schedule exactly one active Ollama reconstruction")
        self.calls.append((rows_in, dict(kwargs)))
        self.total += 1
        row = rows_in[0]
        population_row = EstatePopulationRow(
            row.proposal_id,
            EstatePopulationStatus.ADDED,
            reconstruction_fingerprint=f"{self.total:064x}"[-64:],
        )
        update = StrategyEstateUpdate(
            Path(kwargs["estate_path"]),
            f"{1000 + self.total:064x}"[-64:],
            1,
            self.total,
        )
        return EstatePopulationResult((population_row,), update)


class M1966StrategyDiscoveryCampaignServiceTests(unittest.TestCase):
    def config(self, root: Path, *, window: int = 6) -> StrategyDiscoveryConfig:
        return StrategyDiscoveryConfig(
            root / "VibeTrading",
            root / "work",
            root / "estate.json",
            root / "state.json",
            root / "reports",
            catalog_limit=100,
            max_reconstructions=window,
        )

    def service(self, root: Path, catalog: VibeResearchResult, builder: FakeBuilder, digest_calls: list[str]):
        return BatchedStrategyDiscoveryService(
            self.config(root),
            contractor_factory=lambda *_: FakeContractor(catalog),
            builder=builder,
            digest_resolver=lambda tag: digest_calls.append(tag) or H("d"),
        )

    def test_thirteen_families_drain_as_six_six_one_with_one_digest_lookup(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            builder = FakeBuilder()
            digest_calls: list[str] = []
            service = self.service(root, available_catalog([item(i) for i in range(13)]), builder, digest_calls)

            result = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)

            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            self.assertEqual(result.proposals_seen, 13)
            self.assertEqual(result.new_single_symbol_candidates, 13)
            self.assertEqual(result.added_to_estate, 13)
            self.assertEqual(digest_calls, ["qwen3:1.7b"])
            self.assertEqual(len(builder.calls), 13)
            self.assertTrue(all(len(rows) == 1 for rows, _ in builder.calls))
            self.assertTrue(all(kwargs["created_at"] == NOW for _, kwargs in builder.calls))
            self.assertTrue(all(len(kwargs["allowed_timeframes"]) == 1 for _, kwargs in builder.calls))

            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            campaign = report["reconstruction_campaign"]
            self.assertEqual(campaign["batch_size_governor"], 6)
            self.assertEqual(campaign["batch_sizes"], [6, 6, 1])
            self.assertEqual(campaign["ready_reconstructions"], 13)
            self.assertFalse(campaign["authority"]["parallel_ollama_calls"])
            population = report["estate_population"]
            self.assertEqual(population["model_calls_scheduled"], 13)
            self.assertEqual(population["batches_completed"], 3)
            self.assertEqual(population["estate_checkpoints"], 13)

    def test_duplicate_families_are_removed_before_builder_calls(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            originals = [item(i) for i in range(6)]
            duplicate_a = dict(originals[0], id="duplicate:a", nickname="Marketing A")
            duplicate_b = dict(originals[1], id="duplicate:b", nickname="Marketing B")
            builder = FakeBuilder()
            digest_calls: list[str] = []
            service = self.service(root, available_catalog([*originals, duplicate_a, duplicate_b]), builder, digest_calls)

            result = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)

            self.assertEqual(result.proposals_seen, 8)
            self.assertEqual(result.new_single_symbol_candidates, 6)
            self.assertEqual(len(builder.calls), 6)
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            campaign = report["reconstruction_campaign"]
            self.assertEqual(campaign["families_after_dedupe"], 6)
            self.assertEqual(campaign["duplicates_removed"], 2)
            self.assertEqual(campaign["batch_sizes"], [6])

    def test_true_cross_symbol_dependency_idea_remains_deferred(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            builder = FakeBuilder()
            digest_calls: list[str] = []
            service = self.service(root, available_catalog([item(0, cross=True)]), builder, digest_calls)

            result = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)

            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            self.assertEqual(result.deferred_cross_symbol_candidates, 1)
            self.assertEqual(result.new_single_symbol_candidates, 0)
            self.assertEqual(builder.calls, [])
            self.assertEqual(digest_calls, [])

    def test_existing_estate_proposal_is_skipped_before_digest_lookup(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = available_catalog([item(0)])
            from dusty.source_intake import proposals_from_vibe
            assert catalog.evidence is not None
            proposal = proposals_from_vibe(catalog.evidence)[0]
            builder = FakeBuilder()
            digest_calls: list[str] = []
            service = self.service(root, catalog, builder, digest_calls)

            with patch(
                "dusty.strategy_reconstruction_campaign.load_strategy_estate",
                return_value=(type("Existing", (), {"proposal_fingerprint": proposal.fingerprint})(),),
            ):
                result = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)

            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            self.assertEqual(result.new_single_symbol_candidates, 0)
            self.assertEqual(builder.calls, [])
            self.assertEqual(digest_calls, [])

    def test_pc_launcher_uses_batched_service(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            service = _discovery_service(
                explicit_library=None,
                estate_path=root / "estate.json",
                no_estate=False,
            )
            self.assertIsInstance(service, BatchedStrategyDiscoveryService)

    def test_reconstruction_window_is_fixed_at_six(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "exactly 6"):
                BatchedStrategyDiscoveryService(
                    self.config(root, window=7),
                    contractor_factory=lambda *_: FakeContractor(available_catalog([])),
                    builder=FakeBuilder(),
                    digest_resolver=lambda _tag: H("d"),
                )

    def test_service_has_no_trading_authority(self) -> None:
        self.assertFalse(BatchedStrategyDiscoveryService.broker_write_authority)
        self.assertFalse(BatchedStrategyDiscoveryService.live_write_authority)
        self.assertFalse(BatchedStrategyDiscoveryService.promotion_authority)
        self.assertFalse(BatchedStrategyDiscoveryService.risk_override_authority)
        self.assertFalse(BatchedStrategyDiscoveryService.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
