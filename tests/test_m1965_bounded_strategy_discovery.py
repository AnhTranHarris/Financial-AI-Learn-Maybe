from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dusty.bounded_strategy_discovery import (
    DEFAULT_RESEARCH_SYMBOLS,
    DISCOVERY_CATALOG_LIMIT,
    DISCOVERY_MAX_RECONSTRUCTIONS,
    OLLAMA_DEFAULT_NUM_CTX,
    OLLAMA_NUM_THREAD_CAP,
    SymbolDiversifyingEstateBuilder,
    bounded_ollama_transport,
    ollama_thread_budget,
    workstation_discovery_config,
)
from dusty.source_intake import (
    EvidenceClass,
    ProposalCompleteness,
    SourceAccess,
    SourceSnapshot,
    StrategyProposal,
)
from dusty.strategy_estate import StrategyEstateUpdate
from dusty.strategy_estate_builder import (
    EstatePopulationResult,
    EstatePopulationRow,
    EstatePopulationStatus,
)


H = lambda ch: ch * 64
NOW = datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)


def proposal(
    name: str,
    *,
    symbols: tuple[str, ...] = (),
    component: str = "momentum",
) -> StrategyProposal:
    return StrategyProposal(
        f"vibe:{name}",
        SourceSnapshot(
            "vibe-trading",
            "http://localhost/vibe-trading/research",
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            H("a"),
            SourceAccess.AUTHENTICATED_TOOL,
            True,
        ),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.CONCEPT_ONLY,
        name,
        symbols=symbols,
        timeframes=("M15",),
        components=(component,),
        unresolved=("entry_logic", "exit_logic", "risk_logic"),
        tags=("source:vibe", "research_only"),
    )


class FakeDelegate:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[StrategyProposal, ...], dict[str, object]]] = []
        self.total = 0

    def populate(self, proposals, **kwargs):
        rows = tuple(proposals)
        self.calls.append((rows, dict(kwargs)))
        self.total += 1
        return EstatePopulationResult(
            (
                EstatePopulationRow(
                    rows[0].proposal_id,
                    EstatePopulationStatus.ADDED,
                    reconstruction_fingerprint=H("e"),
                ),
            ),
            StrategyEstateUpdate(Path(kwargs["estate_path"]), H("f"), 1, self.total),
        )


class BoundedStrategyDiscoveryTests(unittest.TestCase):
    def test_workstation_policy_is_small_and_includes_exercised_symbols(self) -> None:
        with TemporaryDirectory() as temp, patch.dict("os.environ", {"LOCALAPPDATA": temp}, clear=False):
            config = workstation_discovery_config(estate_path=Path(temp) / "estate.json")
        self.assertEqual(config.catalog_limit, DISCOVERY_CATALOG_LIMIT)
        self.assertEqual(config.max_reconstructions, DISCOVERY_MAX_RECONSTRUCTIONS)
        self.assertEqual(DISCOVERY_MAX_RECONSTRUCTIONS, 2)
        for symbol in ("EURUSD", "XAUUSD", "NASUSD"):
            self.assertIn(symbol, config.allowed_symbols)
        self.assertEqual(config.allowed_symbols, DEFAULT_RESEARCH_SYMBOLS)

    def test_exact_symbol_override_is_bounded_and_not_alias_normalized(self) -> None:
        with TemporaryDirectory() as temp, patch.dict(
            "os.environ",
            {"LOCALAPPDATA": temp, "DUSTY_STRATEGY_SYMBOLS": "EURUSD.a,NASUSD,XAUUSD"},
            clear=False,
        ):
            config = workstation_discovery_config(estate_path=Path(temp) / "estate.json")
        self.assertEqual(config.allowed_symbols, ("EURUSD.A", "NASUSD", "XAUUSD"))

    def test_thread_budget_uses_half_visible_cpu_with_cap(self) -> None:
        with patch("dusty.bounded_strategy_discovery.os.cpu_count", return_value=16):
            self.assertEqual(ollama_thread_budget(), OLLAMA_NUM_THREAD_CAP)
        with patch("dusty.bounded_strategy_discovery.os.cpu_count", return_value=4):
            self.assertEqual(ollama_thread_budget(), 2)
        with patch("dusty.bounded_strategy_discovery.os.cpu_count", return_value=2):
            self.assertEqual(ollama_thread_budget(), 1)
        with patch("dusty.bounded_strategy_discovery.os.cpu_count", return_value=None):
            self.assertEqual(ollama_thread_budget(), 1)

    def test_ollama_transport_adds_adaptive_budget_without_mutating_input(self) -> None:
        captured = {}

        def delegate(method, url, payload, timeout):
            captured.update({"method": method, "url": url, "payload": payload, "timeout": timeout})
            return {"done": True}

        original = {"options": {"temperature": 0}, "messages": []}
        with patch("dusty.bounded_strategy_discovery.os.cpu_count", return_value=8):
            result = bounded_ollama_transport(
                "POST",
                "http://127.0.0.1:11434/api/chat",
                original,
                20.0,
                delegate=delegate,
            )
        self.assertEqual(result, {"done": True})
        self.assertNotIn("num_thread", original["options"])
        self.assertNotIn("num_ctx", original["options"])
        self.assertEqual(captured["payload"]["options"]["num_thread"], 4)
        self.assertEqual(captured["payload"]["options"]["num_ctx"], OLLAMA_DEFAULT_NUM_CTX)

    def test_ollama_transport_preserves_reconstructor_explicit_context(self) -> None:
        captured = {}

        def delegate(method, url, payload, timeout):
            captured["payload"] = payload
            return {"done": True}

        original = {"options": {"num_ctx": 4096, "num_predict": 384}}
        with patch("dusty.bounded_strategy_discovery.os.cpu_count", return_value=8):
            bounded_ollama_transport(
                "POST",
                "http://localhost:11434/api/chat",
                original,
                20.0,
                delegate=delegate,
            )
        self.assertEqual(captured["payload"]["options"]["num_ctx"], 4096)
        self.assertEqual(captured["payload"]["options"]["num_predict"], 384)

    def test_generic_proposals_get_distinct_one_symbol_research_lanes(self) -> None:
        delegate = FakeDelegate()
        builder = SymbolDiversifyingEstateBuilder(delegate)
        first = proposal("generic-a", component="momentum")
        second = proposal("generic-b", component="breakout")
        with TemporaryDirectory() as temp:
            result = builder.populate(
                (first, second),
                model_tag="qwen3:1.7b",
                model_digest=H("d"),
                allowed_symbols=("EURUSD", "XAUUSD", "NASUSD"),
                allowed_timeframes=("M15",),
                allowed_features=("rsi",),
                allowed_sessions=("LONDON",),
                estate_path=Path(temp) / "estate.json",
                created_at=NOW,
            )

        self.assertEqual(result.added_count, 2)
        self.assertIsNotNone(result.estate_update)
        assert result.estate_update is not None
        self.assertEqual(result.estate_update.added, 2)
        self.assertEqual(result.estate_update.total, 2)
        self.assertEqual(len(delegate.calls), 2)
        assigned = [call[1]["allowed_symbols"] for call in delegate.calls]
        self.assertTrue(all(len(row) == 1 for row in assigned))
        self.assertNotEqual(assigned[0], assigned[1])
        # Research assignment must not rewrite what the archived source claimed.
        self.assertEqual(first.symbols, ())
        self.assertEqual(second.symbols, ())
        self.assertEqual(delegate.calls[0][0][0].symbols, ())
        self.assertEqual(delegate.calls[1][0][0].symbols, ())

    def test_source_named_symbol_is_preserved_when_it_is_allowed(self) -> None:
        delegate = FakeDelegate()
        builder = SymbolDiversifyingEstateBuilder(delegate)
        named = proposal("named", symbols=("NASUSD",))
        with TemporaryDirectory() as temp:
            builder.populate(
                (named,),
                model_tag="qwen3:1.7b",
                model_digest=H("d"),
                allowed_symbols=("EURUSD", "NASUSD"),
                allowed_timeframes=("M15",),
                allowed_features=("rsi",),
                estate_path=Path(temp) / "estate.json",
                created_at=NOW,
            )
        self.assertEqual(delegate.calls[0][1]["allowed_symbols"], ("NASUSD",))
        self.assertEqual(delegate.calls[0][0][0].symbols, ("NASUSD",))


if __name__ == "__main__":
    unittest.main()
